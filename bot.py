import os
import time
import json
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue, Empty

import requests
import websocket


# ============================================================
# UNIFIED ALERT BOT
# BINANCE 5M MOMENTUM + REAL BREAKOUT
# +
# SOLANA EARLY QUALITY MEME SCANNER
#
# ALERT ONLY - NO AUTOMATIC BUY
#
# SOLANA DATA:
# GeckoTerminal Public API / Solana new pools
#
# IMPORTANT:
# GeckoTerminal public API is rate limited. This scanner polls
# at a conservative interval and does NOT pretend that GMGN-only
# fields (smart money, sniper %, bundle %) are available unless
# another verified source is connected.
# ============================================================


# ============================================================
# BINANCE
# ============================================================

BINANCE_REST = "https://api.binance.com"
BINANCE_WS = "wss://stream.binance.com:443"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

INTERVAL = "5m"

HISTORY_LIMIT = 200
AVERAGE_VOLUME_CANDLES = 20
RESISTANCE_LOOKBACK = 200

MIN_RESISTANCE_AGE = 3
RESISTANCE_TOLERANCE_PERCENT = 0.60
MIN_TEST_DISTANCE_CANDLES = 2
MIN_RESISTANCE_TESTS = 2
MAX_RECENT_TEST_AGE = 40

MIN_24H_QUOTE_VOLUME = 1_000_000

MAX_SPREAD_PERCENT = 0.20
BOOK_CACHE_MAX_AGE = 10

MAX_CURRENT_5M_PRICE = 8.0
MIN_BUY_PRESSURE = 55.0

MIN_SIGNAL_SCORE = 60
STRONG_SIGNAL_SCORE = 75

VOLUME_MIN_RATIO = 1.2

MIN_BREAKOUT_PERCENT = 0.30
MIN_BREAKOUT_VOLUME_RATIO = 1.5

MIN_CLOSE_POSITION = 70.0
MAX_UPPER_WICK_PERCENT = 30.0

STOP_BELOW_RESISTANCE_PERCENT = 0.50
TP1_PERCENT = 3.0
TP2_PERCENT = 5.0
TP3_PERCENT = 8.0

WS_CHUNK_SIZE = 50
RECONNECT_SECONDS = 5

STATUS_INTERVAL = 60
SIGNAL_COOLDOWN_SECONDS = 30 * 60
SAME_RESISTANCE_TOLERANCE = 0.10

REST_BOOK_TIMEOUT = 5
REST_BOOK_MIN_INTERVAL = 1.0


# ============================================================
# SOLANA EARLY QUALITY
# ============================================================

GECKO_BASE = "https://api.geckoterminal.com/api/v2"
GECKO_NETWORK = "solana"

# Public API is approximately 10 calls/minute.
# 10 seconds = 6 calls/minute.
SOLANA_SCAN_INTERVAL = 10

SOLANA_MAX_PAGES = 2

SOLANA_MIN_AGE_SECONDS = 0
SOLANA_MAX_AGE_SECONDS = 3 * 60

SOLANA_SUPER_EARLY_SECONDS = 30
SOLANA_EARLY_SECONDS = 60

SOLANA_MIN_LIQUIDITY = 5_000.0
SOLANA_MIN_MCAP = 15_000.0
SOLANA_MAX_MCAP = 100_000.0

SOLANA_MIN_1H_VOLUME = 3_000.0

SOLANA_MIN_TXNS = 8
SOLANA_MIN_BUY_SELL_RATIO = 1.8

SOLANA_MIN_SCORE = 85
SOLANA_STRONG_SCORE = 90

SOLANA_ALERT_COOLDOWN = 30 * 60

# We do not hard reject high price change.
SOLANA_MAX_PRICE_CHANGE = None

# Reject obvious low-quality pools.
SOLANA_MAX_SELL_DOMINANCE = 0.70

# Require meaningful activity.
SOLANA_MIN_ACTIVITY_FOR_SCORE = 8

# Test only the first 30 Solana signals.
SOLANA_TEST_LIMIT = 30

SOLANA_TEST_FILE = "solana_test_results.jsonl"

SOLANA_TRACK_SECONDS = [30, 60, 180, 300]

# GeckoTerminal data freshness can be a few seconds and public
# responses are cached. This is an early scanner, not a tick feed.


# ============================================================
# GLOBAL
# ============================================================

session = requests.Session()
session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json",
})

symbols = []
volume_24h = {}

candle_history = {}
live_candles = {}
book_data = {}

last_signal_time = {}
alerted_breakouts = {}

running = True

data_lock = threading.RLock()
signal_lock = threading.Lock()

book_updates = 0
last_book_update = 0
rest_book_last_request = {}

# Central Telegram queue prevents Solana and Binance from blocking
# one another on HTTP requests.
telegram_queue = Queue(maxsize=500)

# Solana state
solana_seen = set()
solana_alerted = {}
solana_tracking = {}
solana_test_count = 0
solana_lock = threading.RLock()

stats_lock = threading.Lock()

stats = {
    "checked": 0,
    "momentum": 0,
    "volume": 0,
    "buy_pressure": 0,
    "resistance": 0,
    "breakout": 0,
    "breakout_volume": 0,
    "candle_quality": 0,
    "spread": 0,
    "spread_missing": 0,
    "spread_rejected": 0,
    "score": 0,
    "signals": 0,
    "telegram_ok": 0,
    "telegram_failed": 0,

    "solana_pools": 0,
    "solana_candidates": 0,
    "solana_signals": 0,
    "solana_rejected": 0,
}


def reset_stats():
    with stats_lock:
        for key in stats:
            stats[key] = 0


def inc_stat(name):
    with stats_lock:
        if name in stats:
            stats[name] += 1


def get_stats():
    with stats_lock:
        return dict(stats)


# ============================================================
# HELPERS
# ============================================================

def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def round_price(price):
    if price >= 1000:
        return round(price, 2)
    if price >= 1:
        return round(price, 4)
    if price >= 0.01:
        return round(price, 6)
    return round(price, 8)


def percent_change(open_price, current_price):
    if open_price <= 0:
        return 0.0
    return ((current_price - open_price) / open_price) * 100.0


def nested_get(obj, *keys, default=None):
    cur = obj
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return cur if cur is not None else default


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_now(message):
    if not TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN yoxdur.")
        inc_stat("telegram_failed")
        return False

    if not TELEGRAM_CHAT_ID:
        print("ERROR: TELEGRAM_CHAT_ID yoxdur.")
        inc_stat("telegram_failed")
        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        response = session.post(
            url,
            json=payload,
            timeout=10,
        )

        if response.status_code == 200:
            inc_stat("telegram_ok")
            return True

        print(
            "Telegram ERROR:",
            response.status_code,
            response.text[:500],
        )
        inc_stat("telegram_failed")

    except Exception as e:
        print("Telegram exception:", e)
        inc_stat("telegram_failed")

    return False


def queue_telegram(message):
    try:
        telegram_queue.put_nowait(message)
    except Exception:
        print("Telegram queue full - message dropped.")


def telegram_worker():
    while running:
        try:
            message = telegram_queue.get(timeout=1)
        except Empty:
            continue

        try:
            send_telegram_now(message)
        finally:
            telegram_queue.task_done()


def telegram_startup_test():
    queue_telegram(
        "🟢 <b>UNIFIED BOT STARTED</b>\n\n"
        "🔵 Binance 5M Momentum + Breakout\n"
        "🟣 Solana Early Quality Scanner\n\n"
        "⚠️ ALERT ONLY\n"
        "No automatic order."
    )


# ============================================================
# BINANCE EXCHANGE INFO
# ============================================================

def load_exchange_info():
    print("Loading Binance exchange info...")

    url = f"{BINANCE_REST}/api/v3/exchangeInfo"

    response = session.get(url, timeout=20)
    response.raise_for_status()

    data = response.json()
    result = []

    for item in data.get("symbols", []):
        if item.get("status") != "TRADING":
            continue

        if item.get("quoteAsset") != "USDT":
            continue

        if not item.get("isSpotTradingAllowed", False):
            continue

        base = item.get("baseAsset", "")

        if (
            base.endswith("UP")
            or base.endswith("DOWN")
            or base.endswith("BULL")
            or base.endswith("BEAR")
        ):
            continue

        result.append(item["symbol"].lower())

    print("USDT Spot symbols:", len(result))
    return result


# ============================================================
# BINANCE 24H VOLUME
# ============================================================

def load_24h_volumes(all_symbols):
    print("Loading Binance 24H ticker data...")

    url = f"{BINANCE_REST}/api/v3/ticker/24hr"

    response = session.get(url, timeout=30)
    response.raise_for_status()

    data = response.json()
    allowed = set(all_symbols)
    result = {}

    for ticker in data:
        symbol = ticker.get("symbol", "").lower()

        if symbol not in allowed:
            continue

        quote_volume = safe_float(ticker.get("quoteVolume"))

        if quote_volume < MIN_24H_QUOTE_VOLUME:
            continue

        result[symbol] = quote_volume

    print("After 24H volume filter:", len(result))
    return result


# ============================================================
# BINANCE HISTORY
# ============================================================

def load_symbol_history(symbol):
    url = f"{BINANCE_REST}/api/v3/klines"

    params = {
        "symbol": symbol.upper(),
        "interval": INTERVAL,
        "limit": HISTORY_LIMIT,
    }

    try:
        response = session.get(url, params=params, timeout=15)
        response.raise_for_status()

        data = response.json()
        candles = deque(maxlen=HISTORY_LIMIT)

        now_ms = int(time.time() * 1000)

        for k in data:
            close_time = int(k[6])

            if close_time >= now_ms:
                continue

            candles.append({
                "open_time": int(k[0]),
                "open": safe_float(k[1]),
                "high": safe_float(k[2]),
                "low": safe_float(k[3]),
                "close": safe_float(k[4]),
                "volume": safe_float(k[5]),
                "quote_volume": safe_float(k[7]),
                "trades": int(k[8]),
                "taker_buy_base": safe_float(k[9]),
                "taker_buy_quote": safe_float(k[10]),
                "closed": True,
            })

        return symbol, candles

    except Exception as e:
        print(f"History error {symbol}: {e}")
        return symbol, None


def load_all_histories():
    print(
        "Loading 5M history for "
        f"{len(symbols)} symbols..."
    )

    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = [
            executor.submit(load_symbol_history, symbol)
            for symbol in symbols
        ]

        completed = 0

        for future in as_completed(futures):
            symbol, candles = future.result()
            completed += 1

            if candles:
                with data_lock:
                    candle_history[symbol] = candles

            if completed % 50 == 0:
                print(
                    "History:",
                    f"{completed}/{len(symbols)}",
                )

    print("History ready:", len(candle_history))


# ============================================================
# BINANCE RESISTANCE
# ============================================================

def find_resistance(symbol):
    with data_lock:
        history = list(candle_history.get(symbol, []))

    if len(history) < 20:
        return None

    candles = history[-RESISTANCE_LOOKBACK:]

    if len(candles) < 20:
        return None

    candidates = []

    for i in range(2, len(candles) - 2):
        high = candles[i]["high"]

        left_highs = [
            candles[j]["high"]
            for j in range(i - 2, i)
        ]

        right_highs = [
            candles[j]["high"]
            for j in range(i + 1, i + 3)
        ]

        if high < max(left_highs):
            continue

        if high < max(right_highs):
            continue

        age = len(candles) - 1 - i

        if age < MIN_RESISTANCE_AGE:
            continue

        candidates.append({
            "price": high,
            "index": i,
            "age": age,
        })

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: x["index"],
        reverse=True,
    )

    best_candidate = None
    best_score = -999999
    current_price = candles[-1]["close"]

    for candidate in candidates:
        resistance = candidate["price"]
        pivot_index = candidate["index"]

        if resistance <= 0:
            continue

        tolerance = (
            resistance
            * RESISTANCE_TOLERANCE_PERCENT
            / 100.0
        )

        tests = []
        last_test_index = None

        for i in range(len(candles)):
            if i == pivot_index:
                continue

            high = candles[i]["high"]

            if abs(high - resistance) > tolerance:
                continue

            if (
                last_test_index is not None
                and i - last_test_index
                < MIN_TEST_DISTANCE_CANDLES
            ):
                continue

            tests.append(i)
            last_test_index = i

        test_count = len(tests) + 1

        if test_count < MIN_RESISTANCE_TESTS:
            continue

        recent_test = False
        recent_test_age = 999999

        for test_index in tests:
            age = len(candles) - 1 - test_index

            if age < recent_test_age:
                recent_test_age = age

            if age <= MAX_RECENT_TEST_AGE:
                recent_test = True

        if not recent_test:
            continue

        distance_percent = (
            abs((current_price - resistance) / resistance)
            * 100.0
        )

        strength = min(test_count * 10, 50)

        candidate_score = 0
        candidate_score += test_count * 100
        candidate_score += max(0, 50 - recent_test_age)
        candidate_score += max(0, 20 - distance_percent)
        candidate_score += candidate["index"] / len(candles)

        if (
            best_candidate is None
            or candidate_score > best_score
        ):
            best_score = candidate_score

            best_candidate = {
                "price": resistance,
                "index": pivot_index,
                "age": candidate["age"],
                "tests": test_count,
                "strength": strength,
                "recent_test_age": recent_test_age,
                "distance_percent": distance_percent,
            }

    return best_candidate


# ============================================================
# BINANCE SCORE
# ============================================================

def momentum_score(price_change):
    if price_change >= 5:
        return 25
    if price_change >= 4:
        return 20
    if price_change >= 3:
        return 15
    if price_change >= 2:
        return 10
    if price_change >= 1:
        return 5
    return 0


def volume_score(ratio):
    if ratio >= 4:
        return 20
    if ratio >= 3:
        return 17
    if ratio >= 2:
        return 14
    if ratio >= 1.5:
        return 8
    if ratio >= 1.2:
        return 4
    return 0


def buy_pressure_score(pressure):
    if pressure >= 65:
        return 20
    if pressure >= 60:
        return 15
    if pressure >= 55:
        return 10
    if pressure >= 50:
        return 5
    return 0


def breakout_score(price, resistance):
    if resistance <= 0:
        return 0, 0.0

    breakout = (
        (price - resistance) / resistance
    ) * 100.0

    if breakout >= 1.0:
        return 15, breakout
    if breakout >= 0.75:
        return 12, breakout
    if breakout >= 0.50:
        return 8, breakout
    if breakout >= 0.30:
        return 5, breakout

    return 0, breakout


def breakout_volume_score(current_volume, average_volume):
    if average_volume <= 0:
        return 0, 0.0

    ratio = current_volume / average_volume

    if ratio >= 4:
        return 10, ratio
    if ratio >= 3:
        return 8, ratio
    if ratio >= 2:
        return 6, ratio
    if ratio >= 1.5:
        return 4, ratio

    return 0, ratio


def candle_quality(candle):
    open_price = candle["open"]
    high = candle["high"]
    low = candle["low"]
    close = candle["close"]

    if high <= low:
        return 0, 0.0, 0.0

    candle_range = high - low

    close_position = (
        (close - low) / candle_range
    ) * 100.0

    upper_wick = high - max(open_price, close)

    upper_wick_percent = (
        upper_wick / candle_range
    ) * 100.0

    if close <= open_price:
        return 0, close_position, upper_wick_percent

    if close_position < MIN_CLOSE_POSITION:
        return 0, close_position, upper_wick_percent

    if upper_wick_percent > MAX_UPPER_WICK_PERCENT:
        return 0, close_position, upper_wick_percent

    return 10, close_position, upper_wick_percent


# ============================================================
# BINANCE BOOK
# ============================================================

def store_book(symbol, bid, ask):
    global book_updates
    global last_book_update

    if bid <= 0 or ask <= 0:
        return

    now = time.time()

    with data_lock:
        book_data[symbol] = {
            "bid": bid,
            "ask": ask,
            "timestamp": now,
        }

        book_updates += 1
        last_book_update = now


def get_spread(symbol):
    with data_lock:
        data = book_data.get(symbol)

    if data:
        age = time.time() - data["timestamp"]

        if age <= BOOK_CACHE_MAX_AGE:
            bid = data["bid"]
            ask = data["ask"]

            if bid > 0 and ask > 0:
                mid = (bid + ask) / 2.0
                return ((ask - bid) / mid) * 100.0

    now = time.time()

    last_req = rest_book_last_request.get(symbol, 0)

    if now - last_req < REST_BOOK_MIN_INTERVAL:
        inc_stat("spread_missing")
        return None

    rest_book_last_request[symbol] = now

    try:
        url = f"{BINANCE_REST}/api/v3/ticker/bookTicker"

        response = session.get(
            url,
            params={"symbol": symbol.upper()},
            timeout=REST_BOOK_TIMEOUT,
        )

        if response.status_code != 200:
            inc_stat("spread_missing")
            return None

        data = response.json()

        bid = safe_float(data.get("bidPrice"))
        ask = safe_float(data.get("askPrice"))

        if bid <= 0 or ask <= 0:
            inc_stat("spread_missing")
            return None

        store_book(symbol, bid, ask)

        mid = (bid + ask) / 2.0
        return ((ask - bid) / mid) * 100.0

    except Exception as e:
        print(f"REST book error {symbol}: {e}")
        inc_stat("spread_missing")
        return None


# ============================================================
# BINANCE ANALYZE
# ============================================================

def analyze_binance_symbol(symbol):
    inc_stat("checked")

    with data_lock:
        candle = live_candles.get(symbol)
        history = list(candle_history.get(symbol, []))
        quote_24h = volume_24h.get(symbol, 0)

    if not candle or len(history) < 20:
        return None

    if quote_24h < MIN_24H_QUOTE_VOLUME:
        return None

    open_price = candle["open"]
    price = candle["close"]

    price_change = percent_change(open_price, price)

    if price_change <= 0:
        return None

    if price_change > MAX_CURRENT_5M_PRICE:
        return None

    momentum_points = momentum_score(price_change)

    if momentum_points <= 0:
        return None

    inc_stat("momentum")

    previous = history[-AVERAGE_VOLUME_CANDLES:]

    volumes = [
        x["quote_volume"]
        for x in previous
        if x["quote_volume"] > 0
    ]

    if not volumes:
        return None

    average_volume = sum(volumes) / len(volumes)
    current_volume = candle["quote_volume"]

    if average_volume <= 0:
        return None

    volume_ratio = current_volume / average_volume

    if volume_ratio < VOLUME_MIN_RATIO:
        return None

    inc_stat("volume")

    volume_points = volume_score(volume_ratio)

    taker_buy_quote = candle["taker_buy_quote"]

    if current_volume <= 0:
        return None

    buy_pressure = (
        taker_buy_quote / current_volume
    ) * 100.0

    if buy_pressure < MIN_BUY_PRESSURE:
        return None

    inc_stat("buy_pressure")

    buy_points = buy_pressure_score(buy_pressure)

    resistance_data = find_resistance(symbol)

    if not resistance_data:
        return None

    inc_stat("resistance")

    resistance = resistance_data["price"]

    if price <= resistance:
        return None

    (
        breakout_points,
        breakout_percent,
    ) = breakout_score(price, resistance)

    if breakout_percent < MIN_BREAKOUT_PERCENT:
        return None

    inc_stat("breakout")

    (
        breakout_volume_points,
        breakout_volume_ratio,
    ) = breakout_volume_score(
        current_volume,
        average_volume,
    )

    if breakout_volume_ratio < MIN_BREAKOUT_VOLUME_RATIO:
        return None

    inc_stat("breakout_volume")

    (
        candle_points,
        close_position,
        upper_wick,
    ) = candle_quality(candle)

    if candle_points <= 0:
        return None

    inc_stat("candle_quality")

    spread = get_spread(symbol)

    if spread is None:
        return None

    if spread > MAX_SPREAD_PERCENT:
        inc_stat("spread_rejected")
        return None

    inc_stat("spread")

    total_score = (
        momentum_points
        + min(volume_points, 20)
        + buy_points
        + min(breakout_points, 15)
        + min(breakout_volume_points, 10)
        + candle_points
    )

    if total_score < MIN_SIGNAL_SCORE:
        return None

    inc_stat("score")

    now = time.time()

    with signal_lock:
        last_time = last_signal_time.get(symbol, 0)

        if now - last_time < SIGNAL_COOLDOWN_SECONDS:
            return None

        previous_resistance = alerted_breakouts.get(symbol)

        if previous_resistance is not None:
            difference = abs(resistance - previous_resistance)

            tolerance = (
                previous_resistance
                * SAME_RESISTANCE_TOLERANCE
                / 100.0
            )

            if difference <= tolerance:
                return None

        last_signal_time[symbol] = now
        alerted_breakouts[symbol] = resistance

    inc_stat("signals")

    status = (
        "🔥 STRONG BUY"
        if total_score >= STRONG_SIGNAL_SCORE
        else "🟢 BUY"
    )

    entry = price

    stop = (
        resistance
        * (
            1
            - STOP_BELOW_RESISTANCE_PERCENT / 100.0
        )
    )

    tp1 = entry * (1 + TP1_PERCENT / 100.0)
    tp2 = entry * (1 + TP2_PERCENT / 100.0)
    tp3 = entry * (1 + TP3_PERCENT / 100.0)

    return {
        "symbol": symbol.upper(),
        "price": price,
        "price_change": price_change,
        "current_volume": current_volume,
        "average_volume": average_volume,
        "volume_ratio": volume_ratio,
        "buy_pressure": buy_pressure,
        "resistance": resistance,
        "resistance_age": resistance_data["age"],
        "resistance_tests": resistance_data["tests"],
        "resistance_strength": resistance_data["strength"],
        "breakout_percent": breakout_percent,
        "breakout_volume_ratio": breakout_volume_ratio,
        "spread": spread,
        "close_position": close_position,
        "upper_wick": upper_wick,
        "momentum_points": momentum_points,
        "volume_points": volume_points,
        "buy_points": buy_points,
        "breakout_points": breakout_points,
        "breakout_volume_points": breakout_volume_points,
        "candle_points": candle_points,
        "score": total_score,
        "status": status,
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
    }


# ============================================================
# BINANCE MESSAGE
# ============================================================

def format_binance_signal(signal):
    symbol = signal["symbol"]

    return f"""
{signal["status"]}

<b>🚀 BINANCE 5M MOMENTUM + EARLY BREAKOUT</b>

🪙 <b>{symbol}</b>

💰 Price:
<b>{round_price(signal["price"])}</b>

📈 5M Momentum:
<b>+{signal["price_change"]:.2f}%</b>

🔥 Current Volume:
<b>${signal["current_volume"]:,.0f}</b>

📊 Volume:
<b>{signal["volume_ratio"]:.2f}× average</b>

🟢 Buy Pressure:
<b>{signal["buy_pressure"]:.1f}%</b>

━━━━━━━━━━━━━━━━━━

💥 Resistance:
<b>{round_price(signal["resistance"])}</b>

🧪 Resistance Tests:
<b>{signal["resistance_tests"]}</b>

⏳ Resistance Age:
<b>{signal["resistance_age"]} candles</b>

💪 Resistance Strength:
<b>{signal["resistance_strength"]}/50</b>

🚀 Breakout:
<b>+{signal["breakout_percent"]:.2f}%</b>

🔥 Breakout Volume:
<b>{signal["breakout_volume_ratio"]:.2f}×</b>

📊 Spread:
<b>{signal["spread"]:.3f}%</b>

🕯 Close Position:
<b>{signal["close_position"]:.1f}%</b>

━━━━━━━━━━━━━━━━━━

🏆 SCORE:
<b>{signal["score"]}/100</b>

🚀 Momentum:
<b>{signal["momentum_points"]}/25</b>

🔥 Volume:
<b>{min(signal["volume_points"],20)}/20</b>

🟢 Buy Pressure:
<b>{signal["buy_points"]}/20</b>

💥 Breakout:
<b>{min(signal["breakout_points"],15)}/15</b>

🔥 Breakout Volume:
<b>{min(signal["breakout_volume_points"],10)}/10</b>

🕯 Candle:
<b>{signal["candle_points"]}/10</b>

━━━━━━━━━━━━━━━━━━

🎯 ENTRY:
<b>{round_price(signal["entry"])}</b>

🛑 STOP LOSS:
<b>{round_price(signal["stop"])}</b>

🎯 TP1:
<b>{round_price(signal["tp1"])}</b> +3%

🎯 TP2:
<b>{round_price(signal["tp2"])}</b> +5%

🎯 TP3:
<b>{round_price(signal["tp3"])}</b> +8%

━━━━━━━━━━━━━━━━━━

💧 24H Volume:
<b>${volume_24h.get(symbol.lower(),0):,.0f}</b>

⚠️ <b>ALERT ONLY</b>
No automatic order.
"""


# ============================================================
# BINANCE KLINE
# ============================================================

def process_kline(symbol, k):
    candle = {
        "open_time": int(k["t"]),
        "open": safe_float(k["o"]),
        "high": safe_float(k["h"]),
        "low": safe_float(k["l"]),
        "close": safe_float(k["c"]),
        "volume": safe_float(k["v"]),
        "quote_volume": safe_float(k["q"]),
        "trades": int(k["n"]),
        "taker_buy_base": safe_float(k["V"]),
        "taker_buy_quote": safe_float(k["Q"]),
        "closed": bool(k["x"]),
    }

    with data_lock:
        live_candles[symbol] = candle

        if candle["closed"]:
            history = candle_history.get(symbol)

            if history is None:
                history = deque(maxlen=HISTORY_LIMIT)
                candle_history[symbol] = history

            if (
                not history
                or history[-1]["open_time"]
                != candle["open_time"]
            ):
                history.append(candle)

            live_candles.pop(symbol, None)
            return

    # Do not run heavy Binance analysis inside the WS callback
    # if another scanner is active. It is still fast enough here,
    # but the Telegram HTTP request is always decoupled.
    signal = analyze_binance_symbol(symbol)

    if signal:
        message = format_binance_signal(signal)

        print("\n" + "=" * 70)
        print(message)
        print("=" * 70)

        queue_telegram(message)


# ============================================================
# BINANCE WEBSOCKET URL
# ============================================================

def make_ws_url(symbol_chunk, stream_type):
    if stream_type == "kline":
        streams = [
            f"{symbol}@kline_5m"
            for symbol in symbol_chunk
        ]
    else:
        streams = [
            f"{symbol}@bookTicker"
            for symbol in symbol_chunk
        ]

    stream_string = "/".join(streams)

    return (
        f"{BINANCE_WS}"
        f"/stream?streams="
        f"{stream_string}"
    )


# ============================================================
# BINANCE KLINE WS
# ============================================================

def kline_websocket_worker(symbol_chunk):
    url = make_ws_url(symbol_chunk, "kline")

    while running:
        try:
            print(
                "KLINE WS connecting "
                f"{len(symbol_chunk)} symbols..."
            )

            def on_open(ws):
                print(
                    "KLINE WS CONNECTED "
                    f"{len(symbol_chunk)} symbols"
                )

            def on_message(ws, message):
                try:
                    data = json.loads(message)
                    payload = data.get("data", data)

                    if payload.get("e") != "kline":
                        return

                    symbol = payload["s"].lower()

                    process_kline(
                        symbol,
                        payload["k"],
                    )

                except Exception as e:
                    print("Kline message error:", e)

            def on_error(ws, error):
                print("KLINE WS ERROR:", error)

            def on_close(ws, code, reason):
                print(
                    "KLINE WS CLOSED:",
                    code,
                    reason,
                )

            ws = websocket.WebSocketApp(
                url,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )

            ws.run_forever(
                ping_interval=20,
                ping_timeout=10,
                origin="https://www.binance.com",
            )

        except Exception as e:
            print("Kline WS exception:", e)

        if running:
            print("Kline WS reconnecting...")
            time.sleep(RECONNECT_SECONDS)


# ============================================================
# BINANCE BOOK WS
# ============================================================

def book_ticker_worker(symbol_chunk):
    url = make_ws_url(symbol_chunk, "book")

    while running:
        try:
            print(
                "BOOK WS connecting "
                f"{len(symbol_chunk)} symbols..."
            )

            def on_open(ws):
                print(
                    "BOOK WS CONNECTED "
                    f"{len(symbol_chunk)} symbols"
                )

            def on_message(ws, message):
                try:
                    data = json.loads(message)
                    payload = data.get("data", data)

                    symbol = payload.get("s")

                    if not symbol:
                        return

                    symbol = symbol.lower()

                    if symbol not in volume_24h:
                        return

                    bid = safe_float(payload.get("b"))
                    ask = safe_float(payload.get("a"))

                    if bid <= 0 or ask <= 0:
                        return

                    store_book(symbol, bid, ask)

                except Exception as e:
                    print("Book message error:", e)

            def on_error(ws, error):
                print("BOOK WS ERROR:", error)

            def on_close(ws, code, reason):
                print(
                    "BOOK WS CLOSED:",
                    code,
                    reason,
                )

            ws = websocket.WebSocketApp(
                url,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )

            ws.run_forever(
                ping_interval=20,
                ping_timeout=10,
                origin="https://www.binance.com",
            )

        except Exception as e:
            print("Book WS exception:", e)

        if running:
            print("Book WS reconnecting...")
            time.sleep(RECONNECT_SECONDS)


# ============================================================
# SOLANA API
# ============================================================

def gecko_get(path, params=None):
    url = GECKO_BASE + path

    try:
        response = session.get(
            url,
            params=params,
            timeout=15,
        )

        if response.status_code == 429:
            print("GeckoTerminal rate limit reached.")
            return None

        if response.status_code != 200:
            print(
                "GeckoTerminal HTTP:",
                response.status_code,
                response.text[:300],
            )
            return None

        return response.json()

    except Exception as e:
        print("GeckoTerminal error:", e)
        return None


def parse_gecko_pool(item):
    if not isinstance(item, dict):
        return None

    attr = item.get("attributes", {})

    if not isinstance(attr, dict):
        return None

    pool_address = item.get("id", "")

    # Gecko ids commonly look like "dex_pooladdress".
    if "_" in pool_address:
        pool_address = pool_address.split("_", 1)[1]

    name = attr.get("name", "UNKNOWN")

    created = attr.get("pool_created_at")

    created_ts = None

    if created:
        try:
            # ISO 8601
            created_ts = (
                time.mktime(
                    time.strptime(
                        created[:19],
                        "%Y-%m-%dT%H:%M:%S",
                    )
                )
            )
        except Exception:
            created_ts = None

    # Prefer explicit market cap; fallback to FDV.
    mcap = safe_float(
        attr.get("market_cap_usd")
    )

    if mcap <= 0:
        mcap = safe_float(
            attr.get("fdv_usd")
        )

    liquidity = safe_float(
        attr.get("reserve_in_usd")
    )

    price_usd = safe_float(
        attr.get("base_token_price_usd")
    )

    volume_h24 = safe_float(
        nested_get(
            attr,
            "volume_usd",
            "h24",
            default=0,
        )
    )

    price_change_m5 = safe_float(
        nested_get(
            attr,
            "price_change_percentage",
            "m5",
            default=0,
        )
    )

    txns_h24 = nested_get(
        attr,
        "transactions",
        "h24",
        default={},
    )

    if not isinstance(txns_h24, dict):
        txns_h24 = {}

    buys = safe_float(txns_h24.get("buys"))
    sells = safe_float(txns_h24.get("sells"))

    txns = buys + sells

    return {
        "pool": pool_address,
        "name": name,
        "created_ts": created_ts,
        "mcap": mcap,
        "liquidity": liquidity,
        "price": price_usd,
        "volume_h24": volume_h24,
        "price_change_m5": price_change_m5,
        "buys": buys,
        "sells": sells,
        "txns": txns,
        "raw": attr,
    }


def solana_age_seconds(pool):
    created_ts = pool.get("created_ts")

    if not created_ts:
        return 999999

    return max(0, time.time() - created_ts)


def solana_buy_sell_ratio(pool):
    buys = pool.get("buys", 0)
    sells = pool.get("sells", 0)

    if sells <= 0:
        if buys > 0:
            return 999.0
        return 0.0

    return buys / sells


# ============================================================
# SOLANA SECURITY / QUALITY
# ============================================================

def solana_quality_score(pool):
    """
    Score available from verified GeckoTerminal pool data only.

    We deliberately do NOT invent:
      - GMGN smart money
      - sniper percentage
      - bundle percentage
      - dev wallet percentage

    Those require a separate verified source.
    """

    age = solana_age_seconds(pool)
    mcap = pool["mcap"]
    liquidity = pool["liquidity"]
    volume = pool["volume_h24"]
    buys = pool["buys"]
    sells = pool["sells"]
    txns = pool["txns"]
    price_change = pool["price_change_m5"]

    # Reject
    if age < SOLANA_MIN_AGE_SECONDS:
        return None, "age"

    if age > SOLANA_MAX_AGE_SECONDS:
        return None, "age"

    if liquidity < SOLANA_MIN_LIQUIDITY:
        return None, "liquidity"

    if mcap < SOLANA_MIN_MCAP:
        return None, "mcap"

    if txns < SOLANA_MIN_TXNS:
        return None, "txns"

    ratio = solana_buy_sell_ratio(pool)

    if ratio < SOLANA_MIN_BUY_SELL_RATIO:
        return None, "buy_sell"

    if sells > 0 and buys + sells > 0:
        sell_share = sells / (buys + sells)

        if sell_share > SOLANA_MAX_SELL_DOMINANCE:
            return None, "sell_dominance"

    if volume < SOLANA_MIN_1H_VOLUME:
        return None, "volume"

    score = 0
    parts = {}

    # 1) EARLY — 15
    if age <= SOLANA_SUPER_EARLY_SECONDS:
        early_points = 15
    elif age <= SOLANA_EARLY_SECONDS:
        early_points = 12
    elif age <= 180:
        early_points = 8
    else:
        early_points = 0

    score += early_points
    parts["early"] = early_points

    # 2) MC — 10
    if SOLANA_MIN_MCAP <= mcap <= SOLANA_MAX_MCAP:
        if mcap <= 50_000:
            mcap_points = 10
        else:
            mcap_points = 8
    elif mcap > SOLANA_MAX_MCAP:
        # No hard rejection here, but lower score.
        mcap_points = 3
    else:
        mcap_points = 0

    score += mcap_points
    parts["mcap"] = mcap_points

    # 3) LIQUIDITY — 10
    if liquidity >= 25_000:
        liquidity_points = 10
    elif liquidity >= 15_000:
        liquidity_points = 8
    elif liquidity >= 10_000:
        liquidity_points = 6
    else:
        liquidity_points = 3

    score += liquidity_points
    parts["liquidity"] = liquidity_points

    # 4) BUY PRESSURE — 15
    total_txns = buys + sells

    if total_txns > 0:
        buy_pressure = buys / total_txns * 100.0
    else:
        buy_pressure = 0.0

    if buy_pressure >= 80:
        buy_points = 15
    elif buy_pressure >= 70:
        buy_points = 13
    elif buy_pressure >= 65:
        buy_points = 11
    elif buy_pressure >= 60:
        buy_points = 8
    elif buy_pressure >= 55:
        buy_points = 5
    else:
        buy_points = 0

    score += buy_points
    parts["buy_pressure"] = buy_points

    # 5) VOLUME — 10
    if volume >= 25_000:
        volume_points = 10
    elif volume >= 10_000:
        volume_points = 8
    elif volume >= 5_000:
        volume_points = 6
    else:
        volume_points = 4

    score += volume_points
    parts["volume"] = volume_points

    # 6) MOMENTUM — 15
    # No maximum price-change rejection.
    if price_change >= 100:
        momentum_points = 15
    elif price_change >= 50:
        momentum_points = 13
    elif price_change >= 25:
        momentum_points = 11
    elif price_change >= 10:
        momentum_points = 9
    elif price_change >= 5:
        momentum_points = 7
    elif price_change > 0:
        momentum_points = 4
    else:
        momentum_points = 0

    score += momentum_points
    parts["momentum"] = momentum_points

    # 7) ACTIVITY / ACCELERATION PROXY — 15
    # Without a second timestamped snapshot, we use transaction
    # density and buy count as a conservative proxy.
    if txns >= 100 and buys >= 70:
        acceleration_points = 15
    elif txns >= 60 and buys >= 40:
        acceleration_points = 13
    elif txns >= 30 and buys >= 20:
        acceleration_points = 10
    elif txns >= 15 and buys >= 10:
        acceleration_points = 7
    else:
        acceleration_points = 3

    score += acceleration_points
    parts["activity"] = acceleration_points

    # 8) SMART MONEY — unavailable in this verified source.
    smart_points = 0
    parts["smart_money"] = smart_points

    # 9) HOLDER STRUCTURE — unavailable in this pool payload.
    holder_points = 0
    parts["holders"] = holder_points

    # 10) DEV — unavailable.
    dev_points = 0
    parts["dev"] = dev_points

    # 11) SECURITY — unavailable as hard contract audit in this
    # endpoint, so do not fabricate a positive score.
    security_points = 0
    parts["security"] = security_points

    return {
        "score": score,
        "parts": parts,
        "buy_pressure": buy_pressure,
        "buy_sell_ratio": ratio,
    }, None


# ============================================================
# SOLANA SIGNAL
# ============================================================

def format_solana_signal(pool, result):
    age = solana_age_seconds(pool)

    if age < 60:
        age_text = f"{age:.0f}s"
    else:
        age_text = f"{age/60:.1f}m"

    score = result["score"]

    if score >= SOLANA_STRONG_SCORE:
        status = "🔥 STRONG EARLY"
    else:
        status = "🟢 EARLY QUALITY"

    parts = result["parts"]

    return f"""
{status}

<b>🟣 SOLANA EARLY QUALITY</b>

🪙 <b>{pool["name"]}</b>

⏱ Age:
<b>{age_text}</b>

💰 Market Cap:
<b>${pool["mcap"]:,.0f}</b>

💧 Liquidity:
<b>${pool["liquidity"]:,.0f}</b>

📊 Volume:
<b>${pool["volume_h24"]:,.0f}</b>

🟢 Buys:
<b>{pool["buys"]:.0f}</b>

🔴 Sells:
<b>{pool["sells"]:.0f}</b>

📈 Buy Pressure:
<b>{result["buy_pressure"]:.1f}%</b>

⚡ Buy/Sell:
<b>{result["buy_sell_ratio"]:.2f}×</b>

🚀 5M Price Change:
<b>{pool["price_change_m5"]:+.2f}%</b>

━━━━━━━━━━━━━━━━━━

🏆 SCORE:
<b>{score}/100</b>

⏱ Early:
<b>{parts["early"]}/15</b>

💰 MC:
<b>{parts["mcap"]}/10</b>

💧 Liquidity:
<b>{parts["liquidity"]}/10</b>

🟢 Buy Pressure:
<b>{parts["buy_pressure"]}/15</b>

🔥 Volume:
<b>{parts["volume"]}/10</b>

🚀 Momentum:
<b>{parts["momentum"]}/15</b>

⚡ Activity:
<b>{parts["activity"]}/15</b>

━━━━━━━━━━━━━━━━━━

ℹ️ Smart Money:
<b>NOT CONNECTED</b>

ℹ️ Dev/Sniper/Bundle:
<b>NOT CONNECTED</b>

⚠️ This is an alert scanner, not a safety guarantee.

⚠️ <b>ALERT ONLY</b>
No automatic order.

📋 Test target:
<b>{solana_test_count + 1}/{SOLANA_TEST_LIMIT}</b>
"""


def save_solana_test(signal):
    try:
        with open(SOLANA_TEST_FILE, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    signal,
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception as e:
        print("Solana test save error:", e)


def create_solana_alert(pool, result):
    global solana_test_count

    pool_id = pool["pool"]
    now = time.time()

    with solana_lock:
        previous = solana_alerted.get(pool_id, 0)

        if now - previous < SOLANA_ALERT_COOLDOWN:
            return

        if solana_test_count >= SOLANA_TEST_LIMIT:
            return

        solana_alerted[pool_id] = now
        solana_test_count += 1

    signal = {
        "timestamp": now,
        "pool": pool["pool"],
        "name": pool["name"],
        "age_seconds": solana_age_seconds(pool),
        "mcap": pool["mcap"],
        "liquidity": pool["liquidity"],
        "volume_h24": pool["volume_h24"],
        "buys": pool["buys"],
        "sells": pool["sells"],
        "buy_pressure": result["buy_pressure"],
        "buy_sell_ratio": result["buy_sell_ratio"],
        "price_change_m5": pool["price_change_m5"],
        "score": result["score"],
        "price": pool["price"],
        "targets": {
            "30": None,
            "60": None,
            "180": None,
            "300": None,
        },
        "max_up": None,
        "max_down": None,
    }

    with solana_lock:
        solana_tracking[pool_id] = {
            "signal": signal,
            "started": now,
        }

    save_solana_test(signal)

    queue_telegram(
        format_solana_signal(pool, result)
    )

    inc_stat("solana_signals")

    print(
        "SOLANA SIGNAL:",
        pool["name"],
        pool["pool"],
        "score=",
        result["score"],
    )


# ============================================================
# SOLANA POLL
# ============================================================

def get_solana_new_pools():
    all_items = []

    for page in range(1, SOLANA_MAX_PAGES + 1):
        data = gecko_get(
            f"/networks/{GECKO_NETWORK}/new_pools",
            params={
                "page": page,
            },
        )

        if not data:
            break

        items = data.get("data", [])

        if not items:
            break

        all_items.extend(items)

    return all_items


def solana_scan_once():
    items = get_solana_new_pools()

    if not items:
        return

    inc_stat("solana_pools")

    for item in items:
        pool = parse_gecko_pool(item)

        if not pool:
            continue

        pool_id = pool["pool"]

        with solana_lock:
            already_seen = pool_id in solana_seen
            solana_seen.add(pool_id)

        if already_seen:
            continue

        inc_stat("solana_candidates")

        result, reason = solana_quality_score(pool)

        if result is None:
            inc_stat("solana_rejected")
            continue

        if result["score"] < SOLANA_MIN_SCORE:
            inc_stat("solana_rejected")
            continue

        create_solana_alert(pool, result)


def solana_worker():
    print("SOLANA scanner started.")

    while running:
        start = time.time()

        try:
            solana_scan_once()
        except Exception as e:
            print("Solana scanner error:", e)

        elapsed = time.time() - start
        sleep_for = max(
            1.0,
            SOLANA_SCAN_INTERVAL - elapsed,
        )

        time.sleep(sleep_for)


# ============================================================
# SOLANA TEST TRACKER
# ============================================================

def solana_tracking_worker():
    while running:
        now = time.time()

        with solana_lock:
            tracked = list(
                solana_tracking.items()
            )

        for pool_id, item in tracked:
            age = now - item["started"]

            # We cannot obtain a reliable per-tick price from the
            # new_pools endpoint alone without additional API calls.
            # Therefore this worker only expires tracking records.
            if age > 600:
                with solana_lock:
                    solana_tracking.pop(pool_id, None)

        time.sleep(10)


# ============================================================
# STATUS
# ============================================================

def status_worker():
    while running:
        time.sleep(STATUS_INTERVAL)

        current = get_stats()

        with data_lock:
            symbol_count = len(symbols)
            history_count = len(candle_history)
            live_count = len(live_candles)
            book_count = len(book_data)
            updates = book_updates
            last_update = last_book_update

        if last_update > 0:
            book_age = time.time() - last_update
        else:
            book_age = -1

        with solana_lock:
            solana_count = len(solana_seen)
            solana_tests = solana_test_count

        print(
            "\n"
            "================ STATUS ================\n"
        )

        print("BINANCE")
        print("Symbols:", symbol_count)
        print("History:", history_count)
        print("Live candles:", live_count)
        print("Book ticker:", book_count)
        print("Book updates:", updates)

        print(
            "Last book update:",
            (
                f"{book_age:.1f}s ago"
                if book_age >= 0
                else "NEVER"
            ),
        )

        print("Checked:", current["checked"])
        print("Momentum passed:", current["momentum"])
        print("Volume passed:", current["volume"])
        print("Buy pressure passed:", current["buy_pressure"])
        print("Resistance found:", current["resistance"])
        print("Breakout passed:", current["breakout"])
        print("Breakout volume:", current["breakout_volume"])
        print("Candle quality:", current["candle_quality"])
        print("Spread passed:", current["spread"])
        print("Spread missing:", current["spread_missing"])
        print("Spread rejected:", current["spread_rejected"])
        print("Score:", current["score"])
        print("Binance signals:", current["signals"])

        print("\nSOLANA")
        print("Pools seen:", solana_count)
        print("Pools fetched:", current["solana_pools"])
        print("Candidates:", current["solana_candidates"])
        print("Rejected:", current["solana_rejected"])
        print("Solana signals:", current["solana_signals"])
        print(
            "30-signal test:",
            f"{solana_tests}/{SOLANA_TEST_LIMIT}",
        )

        print("\nTELEGRAM")
        print("OK:", current["telegram_ok"])
        print("Failed:", current["telegram_failed"])

        print(
            "=========================================\n"
        )

        reset_stats()


# ============================================================
# BINANCE VOLUME REFRESH
# ============================================================

def volume_refresh_worker():
    global volume_24h
    global symbols

    while running:
        time.sleep(30 * 60)

        try:
            all_symbols = load_exchange_info()
            new_volume = load_24h_volumes(all_symbols)

            with data_lock:
                volume_24h = new_volume
                symbols = list(new_volume.keys())

            print(
                "24H volume refreshed:",
                len(new_volume),
            )

        except Exception as e:
            print("Volume refresh error:", e)


# ============================================================
# MAIN
# ============================================================

def main():
    global symbols
    global volume_24h

    print(
        """
============================================================
              UNIFIED ALERT BOT
============================================================

🔵 BINANCE
5M Momentum + Real Breakout
200 candle history
24H volume >= $1M
Minimum score: 60
Strong score: 75
Breakout >= +0.30%
Breakout volume >= 1.5x
Spread <= 0.20%
WebSocket: 443

🟣 SOLANA
Early Quality Meme Scanner
Age: 0-3 minutes
MC target: $15K-$100K
Liquidity: >= $5K
Activity: >= 8 txns
Buy/Sell: >= 1.8x
Volume: >= $3K
Minimum score: 85
Strong score: 90

IMPORTANT:
No hard +15% price filter on Solana.
A +50% token can still signal if activity/quality
is strong.

First test:
30 Solana signals.

ALERT ONLY:
No automatic buy.
============================================================
"""
    )

    threading.Thread(
        target=telegram_worker,
        daemon=True,
    ).start()

    telegram_startup_test()

    # ========================================================
    # BINANCE INITIALIZATION
    # ========================================================

    all_symbols = load_exchange_info()

    volume_24h = load_24h_volumes(all_symbols)

    symbols = list(volume_24h.keys())

    print("Final Binance symbols:", len(symbols))

    if not symbols:
        queue_telegram(
            "🔴 <b>BINANCE BOT ERROR</b>\n\n"
            "Heç bir Binance USDT coin filterdən keçmədi."
        )
    else:
        load_all_histories()

        threading.Thread(
            target=volume_refresh_worker,
            daemon=True,
        ).start()

        chunks = [
            symbols[i:i + WS_CHUNK_SIZE]
            for i in range(
                0,
                len(symbols),
                WS_CHUNK_SIZE,
            )
        ]

        print("WebSocket chunks:", len(chunks))

        for chunk in chunks:
            threading.Thread(
                target=kline_websocket_worker,
                args=(chunk,),
                daemon=True,
            ).start()

            threading.Thread(
                target=book_ticker_worker,
                args=(chunk,),
                daemon=True,
            ).start()

            time.sleep(1)

    # ========================================================
    # SOLANA
    # ========================================================

    threading.Thread(
        target=solana_worker,
        daemon=True,
    ).start()

    threading.Thread(
        target=solana_tracking_worker,
        daemon=True,
    ).start()

    # ========================================================
    # STATUS
    # ========================================================

    threading.Thread(
        target=status_worker,
        daemon=True,
    ).start()

    queue_telegram(
        "🟢 <b>UNIFIED BOT AKTİVDİR</b>\n\n"
        f"🔵 Binance Symbols: {len(symbols)}\n"
        "📡 Binance Kline WS: ACTIVE\n"
        "📖 Binance BookTicker: ACTIVE\n"
        "🟣 Solana Early Scanner: ACTIVE\n"
        f"⏱ Solana scan: {SOLANA_SCAN_INTERVAL}s\n"
        f"🏆 Solana min score: {SOLANA_MIN_SCORE}\n"
        f"🧪 Solana test: 0/{SOLANA_TEST_LIMIT}\n\n"
        "⚠️ ALERT ONLY\n"
        "No automatic order."
    )

    print(
        "\n"
        "==================================================\n"
        "UNIFIED BOT STARTED\n"
        "==================================================\n"
    )

    try:
        while True:
            time.sleep(5)

    except KeyboardInterrupt:
        global running
        running = False
        print("Bot stopped.")


if __name__ == "__main__":
    main()
