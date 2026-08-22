import os
import time
import json
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import websocket


# ============================================================
# 5M MOMENTUM + REAL BREAKOUT BOT
# BINANCE SPOT
# TELEGRAM ALERT
# ============================================================

BINANCE_REST = "https://api.binance.com"
BINANCE_WS = "wss://api.binance.com:443/ws"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

INTERVAL = "5m"


# ============================================================
# HISTORY
# ============================================================

# 500 əvəzinə 200
HISTORY_LIMIT = 200

AVERAGE_VOLUME_CANDLES = 20
RESISTANCE_LOOKBACK = 200


# ============================================================
# RESISTANCE
# ============================================================

MIN_RESISTANCE_AGE = 5

RESISTANCE_TOLERANCE_PERCENT = 0.40

MIN_TEST_DISTANCE_CANDLES = 2

MIN_RESISTANCE_TESTS = 2

MAX_RECENT_TEST_AGE = 30


# ============================================================
# 24H VOLUME
# ============================================================

MIN_24H_QUOTE_VOLUME = 1_000_000


# ============================================================
# SPREAD
# ============================================================

MAX_SPREAD_PERCENT = 0.20

BOOK_CACHE_MAX_AGE = 10


# ============================================================
# 5M MOMENTUM
# ============================================================

MAX_CURRENT_5M_PRICE = 10.0

MIN_BUY_PRESSURE = 55.0


# ============================================================
# SCORE
# ============================================================

MIN_SIGNAL_SCORE = 70

STRONG_SIGNAL_SCORE = 80


# ============================================================
# VOLUME
# ============================================================

VOLUME_MIN_RATIO = 1.2


# ============================================================
# BREAKOUT
# ============================================================

# Resistance ən azı 0.50% qırılmalıdır
MIN_BREAKOUT_PERCENT = 0.50

# Çox gec siqnal gəlməsin.
# Resistance-dən 3%-dən çox yuxarı qalxmış coin qəbul edilmir.
MAX_BREAKOUT_PERCENT = 3.00

MIN_BREAKOUT_VOLUME_RATIO = 2.0


# ============================================================
# CANDLE QUALITY
# ============================================================

MIN_CLOSE_POSITION = 70.0

MAX_UPPER_WICK_PERCENT = 30.0


# ============================================================
# TRADE LEVELS
# ============================================================

STOP_BELOW_RESISTANCE_PERCENT = 0.50

TP1_PERCENT = 3.0
TP2_PERCENT = 5.0
TP3_PERCENT = 8.0


# ============================================================
# WEBSOCKET
# ============================================================

WS_CHUNK_SIZE = 100

RECONNECT_SECONDS = 5


# ============================================================
# STATUS / COOLDOWN
# ============================================================

STATUS_INTERVAL = 60

SIGNAL_COOLDOWN_SECONDS = 30 * 60

SAME_RESISTANCE_TOLERANCE = 0.10


# ============================================================
# REST BOOK
# ============================================================

REST_BOOK_TIMEOUT = 5

REST_BOOK_MIN_INTERVAL = 1.0


# ============================================================
# GLOBAL
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "FiveMinuteMomentumBreakoutBot/7.0"
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


# ============================================================
# DEBUG
# ============================================================

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

    return (
        (current_price - open_price)
        / open_price
    ) * 100.0


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN:

        print("ERROR: TELEGRAM_BOT_TOKEN yoxdur.")

        inc_stat("telegram_failed")

        return False

    if not TELEGRAM_CHAT_ID:

        print("ERROR: TELEGRAM_CHAT_ID yoxdur.")

        inc_stat("telegram_failed")

        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {

        "chat_id": TELEGRAM_CHAT_ID,

        "text": message,

        "parse_mode": "HTML",

        "disable_web_page_preview": True
    }

    try:

        response = session.post(
            url,
            json=payload,
            timeout=10
        )

        if response.status_code == 200:

            print("Telegram message sent.")

            inc_stat("telegram_ok")

            return True

        print(
            "Telegram ERROR:",
            response.status_code,
            response.text[:500]
        )

        inc_stat("telegram_failed")

    except Exception as e:

        print(
            "Telegram exception:",
            e
        )

        inc_stat("telegram_failed")

    return False


def telegram_startup_test():

    print("Testing Telegram...")

    message = (
        "🟢 <b>BOT STARTED</b>\n\n"
        "5M Momentum + Real Breakout Bot aktivdir.\n\n"
        "📡 Kline WebSocket hazırlanır.\n"
        "📖 BookTicker WebSocket hazırlanır.\n"
        "🛟 REST spread fallback aktivdir.\n"
        "📊 Debug aktivdir.\n"
        "📚 History: 200 candles.\n"
        "💥 Breakout: +0.50% → +3.00%.\n\n"
        "Telegram bağlantısı test olunur ✅"
    )

    if send_telegram(message):

        print("TELEGRAM TEST: OK")

    else:

        print("TELEGRAM TEST: FAILED")


# ============================================================
# EXCHANGE INFO
# ============================================================

def load_exchange_info():

    print("Loading Binance exchange info...")

    url = f"{BINANCE_REST}/api/v3/exchangeInfo"

    response = session.get(
        url,
        timeout=20
    )

    response.raise_for_status()

    data = response.json()

    result = []

    for item in data.get("symbols", []):

        if item.get("status") != "TRADING":

            continue

        if item.get("quoteAsset") != "USDT":

            continue

        if not item.get(
            "isSpotTradingAllowed",
            False
        ):

            continue

        base = item.get(
            "baseAsset",
            ""
        )

        if (
            base.endswith("UP")
            or base.endswith("DOWN")
            or base.endswith("BULL")
            or base.endswith("BEAR")
        ):

            continue

        result.append(
            item["symbol"].lower()
        )

    print(
        "USDT Spot symbols:",
        len(result)
    )

    return result


# ============================================================
# 24H VOLUME
# ============================================================

def load_24h_volumes(all_symbols):

    print("Loading 24H ticker data...")

    url = (
        f"{BINANCE_REST}/api/v3/ticker/24hr"
    )

    response = session.get(
        url,
        timeout=30
    )

    response.raise_for_status()

    data = response.json()

    allowed = set(all_symbols)

    result = {}

    for ticker in data:

        symbol = ticker.get(
            "symbol",
            ""
        ).lower()

        if symbol not in allowed:

            continue

        quote_volume = safe_float(
            ticker.get("quoteVolume")
        )

        if (
            quote_volume
            < MIN_24H_QUOTE_VOLUME
        ):

            continue

        result[symbol] = quote_volume

    print(
        "After 24H volume filter:",
        len(result)
    )

    return result


# ============================================================
# HISTORY
# ============================================================

def load_symbol_history(symbol):

    url = (
        f"{BINANCE_REST}/api/v3/klines"
    )

    params = {

        "symbol": symbol.upper(),

        "interval": INTERVAL,

        "limit": HISTORY_LIMIT
    }

    try:

        response = session.get(
            url,
            params=params,
            timeout=15
        )

        response.raise_for_status()

        data = response.json()

        candles = deque(
            maxlen=HISTORY_LIMIT
        )

        now_ms = int(
            time.time() * 1000
        )

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

                "taker_buy_base":
                    safe_float(k[9]),

                "taker_buy_quote":
                    safe_float(k[10]),

                "closed": True
            })

        return symbol, candles

    except Exception as e:

        print(
            f"History error {symbol}: {e}"
        )

        return symbol, None


def load_all_histories():

    print(
        f"Loading 5M history for "
        f"{len(symbols)} symbols..."
    )

    with ThreadPoolExecutor(
        max_workers=15
    ) as executor:

        futures = [

            executor.submit(
                load_symbol_history,
                symbol
            )

            for symbol in symbols
        ]

        completed = 0

        for future in as_completed(
            futures
        ):

            symbol, candles = (
                future.result()
            )

            completed += 1

            if candles:

                with data_lock:

                    candle_history[
                        symbol
                    ] = candles

            if completed % 50 == 0:

                print(
                    f"History: "
                    f"{completed}/"
                    f"{len(symbols)}"
                )

    print(
        "History ready:",
        len(candle_history)
    )


# ============================================================
# RESISTANCE
# ============================================================

def find_resistance(symbol):

    with data_lock:

        history = list(
            candle_history.get(
                symbol,
                []
            )
        )

    if len(history) < 20:

        return None

    candles = history[
        -RESISTANCE_LOOKBACK:
    ]

    if len(candles) < 20:

        return None

    candidates = []

    # --------------------------------------------------------
    # LOCAL HIGH PIVOTS
    # --------------------------------------------------------

    for i in range(
        2,
        len(candles) - 2
    ):

        high = candles[i]["high"]

        left_highs = [

            candles[j]["high"]

            for j in range(
                i - 2,
                i
            )
        ]

        right_highs = [

            candles[j]["high"]

            for j in range(
                i + 1,
                i + 3
            )
        ]

        if high < max(left_highs):

            continue

        if high < max(right_highs):

            continue

        age = (
            len(candles)
            - 1
            - i
        )

        if age < MIN_RESISTANCE_AGE:

            continue

        candidates.append({

            "price": high,

            "index": i,

            "age": age
        })

    if not candidates:

        return None

    candidates.sort(
        key=lambda x: x["index"],
        reverse=True
    )

    best_candidate = None

    best_score = -999999

    current_price = candles[-1]["close"]

    # --------------------------------------------------------
    # CHECK PIVOTS
    # --------------------------------------------------------

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

        for i in range(
            len(candles)
        ):

            if i == pivot_index:

                continue

            high = candles[i]["high"]

            if abs(
                high - resistance
            ) > tolerance:

                continue

            if (
                last_test_index is not None
                and
                i - last_test_index
                < MIN_TEST_DISTANCE_CANDLES
            ):

                continue

            tests.append(i)

            last_test_index = i

        test_count = len(tests) + 1

        if (
            test_count
            < MIN_RESISTANCE_TESTS
        ):

            continue

        recent_test = False

        recent_test_age = 999999

        for test_index in tests:

            age = (
                len(candles)
                - 1
                - test_index
            )

            if age < recent_test_age:

                recent_test_age = age

            if (
                age
                <= MAX_RECENT_TEST_AGE
            ):

                recent_test = True

        if not recent_test:

            continue

        distance_percent = abs(
            (
                current_price
                - resistance
            )
            / resistance
        ) * 100.0

        strength = min(
            test_count * 10,
            50
        )

        candidate_score = 0

        candidate_score += (
            test_count * 100
        )

        candidate_score += max(
            0,
            50 - recent_test_age
        )

        candidate_score += max(
            0,
            20 - distance_percent
        )

        candidate_score += (
            candidate["index"]
            / len(candles)
        )

        if (
            best_candidate is None
            or
            candidate_score
            > best_score
        ):

            best_score = candidate_score

            best_candidate = {

                "price": resistance,

                "index": pivot_index,

                "age": candidate["age"],

                "tests": test_count,

                "strength": strength,

                "recent_test_age":
                    recent_test_age,

                "distance_percent":
                    distance_percent
            }

    return best_candidate


# ============================================================
# SCORE FUNCTIONS
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


def breakout_score(
    price,
    resistance
):

    if resistance <= 0:

        return 0, 0.0

    breakout = (
        (price - resistance)
        / resistance
    ) * 100.0

    if breakout >= 1.0:

        return 15, breakout

    if breakout >= 0.75:

        return 12, breakout

    if breakout >= 0.50:

        return 8, breakout

    return 0, breakout


def breakout_volume_score(
    current_volume,
    average_volume
):

    if average_volume <= 0:

        return 0, 0.0

    ratio = (
        current_volume
        / average_volume
    )

    if ratio >= 4:

        return 10, ratio

    if ratio >= 3:

        return 8, ratio

    if ratio >= 2:

        return 6, ratio

    if ratio >= 1.5:

        return 3, ratio

    return 0, ratio


def candle_quality(candle):

    open_price = candle["open"]

    high = candle["high"]

    low = candle["low"]

    close = candle["close"]

    if high <= low:

        return 0, 0.0, 0.0

    candle_range = (
        high - low
    )

    close_position = (
        (close - low)
        / candle_range
    ) * 100.0

    upper_wick = (
        high
        - max(
            open_price,
            close
        )
    )

    upper_wick_percent = (
        upper_wick
        / candle_range
    ) * 100.0

    if close <= open_price:

        return (
            0,
            close_position,
            upper_wick_percent
        )

    if (
        close_position
        < MIN_CLOSE_POSITION
    ):

        return (
            0,
            close_position,
            upper_wick_percent
        )

    if (
        upper_wick_percent
        > MAX_UPPER_WICK_PERCENT
    ):

        return (
            0,
            close_position,
            upper_wick_percent
        )

    return (
        10,
        close_position,
        upper_wick_percent
    )


# ============================================================
# BOOK TICKER
# ============================================================

def store_book(
    symbol,
    bid,
    ask
):

    global book_updates
    global last_book_update

    if bid <= 0 or ask <= 0:

        return

    now = time.time()

    with data_lock:

        book_data[symbol] = {

            "bid": bid,

            "ask": ask,

            "timestamp": now
        }

        book_updates += 1

        last_book_update = now


def get_spread(symbol):

    with data_lock:

        data = book_data.get(
            symbol
        )

    if data:

        age = (
            time.time()
            - data["timestamp"]
        )

        if (
            age
            <= BOOK_CACHE_MAX_AGE
        ):

            bid = data["bid"]

            ask = data["ask"]

            if bid > 0 and ask > 0:

                mid = (
                    bid + ask
                ) / 2.0

                return (
                    (ask - bid)
                    / mid
                ) * 100.0

    # --------------------------------------------------------
    # REST FALLBACK
    # --------------------------------------------------------

    now = time.time()

    last_req = (
        rest_book_last_request
        .get(symbol, 0)
    )

    if (
        now - last_req
        < REST_BOOK_MIN_INTERVAL
    ):

        inc_stat(
            "spread_missing"
        )

        return None

    rest_book_last_request[
        symbol
    ] = now

    try:

        url = (
            f"{BINANCE_REST}"
            f"/api/v3/ticker/bookTicker"
        )

        response = session.get(
            url,
            params={
                "symbol":
                    symbol.upper()
            },
            timeout=REST_BOOK_TIMEOUT
        )

        if response.status_code != 200:

            inc_stat(
                "spread_missing"
            )

            return None

        data = response.json()

        bid = safe_float(
            data.get("bidPrice")
        )

        ask = safe_float(
            data.get("askPrice")
        )

        if bid <= 0 or ask <= 0:

            inc_stat(
                "spread_missing"
            )

            return None

        store_book(
            symbol,
            bid,
            ask
        )

        mid = (
            bid + ask
        ) / 2.0

        return (
            (ask - bid)
            / mid
        ) * 100.0

    except Exception as e:

        print(
            f"REST book error "
            f"{symbol}: {e}"
        )

        inc_stat(
            "spread_missing"
        )

        return None


# ============================================================
# ANALYZE
# ============================================================

def analyze_symbol(symbol):

    inc_stat("checked")

    with data_lock:

        candle = live_candles.get(
            symbol
        )

        history = list(
            candle_history.get(
                symbol,
                []
            )
        )

        quote_24h = volume_24h.get(
            symbol,
            0
        )

    if (
        not candle
        or len(history) < 20
    ):

        return None

    if (
        quote_24h
        < MIN_24H_QUOTE_VOLUME
    ):

        return None

    # ========================================================
    # PRICE / MOMENTUM
    # ========================================================

    open_price = candle["open"]

    price = candle["close"]

    price_change = percent_change(
        open_price,
        price
    )

    if price_change <= 0:

        return None

    if (
        price_change
        > MAX_CURRENT_5M_PRICE
    ):

        return None

    momentum_points = momentum_score(
        price_change
    )

    if momentum_points <= 0:

        return None

    inc_stat("momentum")

    # ========================================================
    # VOLUME
    # ========================================================

    previous = history[
        -AVERAGE_VOLUME_CANDLES:
    ]

    volumes = [

        x["quote_volume"]

        for x in previous

        if x["quote_volume"] > 0
    ]

    if not volumes:

        return None

    average_volume = (
        sum(volumes)
        / len(volumes)
    )

    current_volume = (
        candle["quote_volume"]
    )

    if average_volume <= 0:

        return None

    volume_ratio = (
        current_volume
        / average_volume
    )

    if (
        volume_ratio
        < VOLUME_MIN_RATIO
    ):

        return None

    inc_stat("volume")

    volume_points = volume_score(
        volume_ratio
    )

    # ========================================================
    # BUY PRESSURE
    # ========================================================

    taker_buy_quote = (
        candle["taker_buy_quote"]
    )

    if current_volume <= 0:

        return None

    buy_pressure = (
        taker_buy_quote
        / current_volume
    ) * 100.0

    if (
        buy_pressure
        < MIN_BUY_PRESSURE
    ):

        return None

    inc_stat("buy_pressure")

    buy_points = buy_pressure_score(
        buy_pressure
    )

    # ========================================================
    # RESISTANCE
    # ========================================================

    resistance_data = find_resistance(
        symbol
    )

    if not resistance_data:

        return None

    inc_stat("resistance")

    resistance = (
        resistance_data["price"]
    )

    resistance_age = (
        resistance_data["age"]
    )

    resistance_tests = (
        resistance_data["tests"]
    )

    resistance_strength = (
        resistance_data["strength"]
    )

    # ========================================================
    # BREAKOUT
    # ========================================================

    if price <= resistance:

        return None

    breakout_points, breakout_percent = (
        breakout_score(
            price,
            resistance
        )
    )

    # Minimum breakout
    if (
        breakout_percent
        < MIN_BREAKOUT_PERCENT
    ):

        return None

    # ========================================================
    # IMPORTANT:
    # ÇOX GEC BREAKOUTLARI QƏBUL ETMƏ
    #
    # Məsələn:
    # Resistance = 0.0503
    # Price      = 0.0541
    # Breakout   = +7.55%
    #
    # Belə siqnal artıq gecdir və rədd edilir.
    # ========================================================

    if (
        breakout_percent
        > MAX_BREAKOUT_PERCENT
    ):

        return None

    inc_stat("breakout")

    # ========================================================
    # BREAKOUT VOLUME
    # ========================================================

    (
        breakout_volume_points,
        breakout_volume_ratio
    ) = breakout_volume_score(
        current_volume,
        average_volume
    )

    if (
        breakout_volume_ratio
        < MIN_BREAKOUT_VOLUME_RATIO
    ):

        return None

    inc_stat("breakout_volume")

    # ========================================================
    # CANDLE QUALITY
    # ========================================================

    (
        candle_points,
        close_position,
        upper_wick
    ) = candle_quality(
        candle
    )

    if candle_points <= 0:

        return None

    inc_stat("candle_quality")

    # ========================================================
    # SPREAD
    # ========================================================

    spread = get_spread(
        symbol
    )

    if spread is None:

        return None

    if (
        spread
        > MAX_SPREAD_PERCENT
    ):

        inc_stat(
            "spread_rejected"
        )

        return None

    inc_stat("spread")

    # ========================================================
    # TOTAL SCORE
    # ========================================================

    total_score = (

        momentum_points

        + min(
            volume_points,
            20
        )

        + buy_points

        + min(
            breakout_points,
            15
        )

        + min(
            breakout_volume_points,
            10
        )

        + candle_points
    )

    if (
        total_score
        < MIN_SIGNAL_SCORE
    ):

        return None

    inc_stat("score")

    # ========================================================
    # COOLDOWN
    # ========================================================

    now = time.time()

    with signal_lock:

        last_time = (
            last_signal_time.get(
                symbol,
                0
            )
        )

        if (
            now - last_time
            < SIGNAL_COOLDOWN_SECONDS
        ):

            return None

        previous_resistance = (
            alerted_breakouts.get(
                symbol
            )
        )

        if (
            previous_resistance
            is not None
        ):

            difference = abs(
                resistance
                - previous_resistance
            )

            tolerance = (
                previous_resistance
                * SAME_RESISTANCE_TOLERANCE
                / 100.0
            )

            if (
                difference
                <= tolerance
            ):

                return None

        last_signal_time[
            symbol
        ] = now

        alerted_breakouts[
            symbol
        ] = resistance

    inc_stat("signals")

    # ========================================================
    # LEVELS
    # ========================================================

    status = (
        "🔥 STRONG BUY"
        if total_score
        >= STRONG_SIGNAL_SCORE
        else
        "🟢 BUY"
    )

    entry = price

    stop = (
        resistance
        * (
            1
            - STOP_BELOW_RESISTANCE_PERCENT
            / 100.0
        )
    )

    tp1 = (
        entry
        * (
            1
            + TP1_PERCENT
            / 100.0
        )
    )

    tp2 = (
        entry
        * (
            1
            + TP2_PERCENT
            / 100.0
        )
    )

    tp3 = (
        entry
        * (
            1
            + TP3_PERCENT
            / 100.0
        )
    )

    return {

        "symbol":
            symbol.upper(),

        "price":
            price,

        "price_change":
            price_change,

        "current_volume":
            current_volume,

        "average_volume":
            average_volume,

        "volume_ratio":
            volume_ratio,

        "buy_pressure":
            buy_pressure,

        "resistance":
            resistance,

        "resistance_age":
            resistance_age,

        "resistance_tests":
            resistance_tests,

        "resistance_strength":
            resistance_strength,

        "breakout_percent":
            breakout_percent,

        "breakout_volume_ratio":
            breakout_volume_ratio,

        "spread":
            spread,

        "close_position":
            close_position,

        "upper_wick":
            upper_wick,

        "momentum_points":
            momentum_points,

        "volume_points":
            volume_points,

        "buy_points":
            buy_points,

        "breakout_points":
            breakout_points,

        "breakout_volume_points":
            breakout_volume_points,

        "candle_points":
            candle_points,

        "score":
            total_score,

        "status":
            status,

        "entry":
            entry,

        "stop":
            stop,

        "tp1":
            tp1,

        "tp2":
            tp2,

        "tp3":
            tp3
    }


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def format_signal(signal):

    symbol = signal["symbol"]

    return f"""
{signal["status"]}

<b>🚀 5M MOMENTUM + REAL BREAKOUT</b>

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
<b>{min(signal["volume_points"], 20)}/20</b>

🟢 Buy Pressure:
<b>{signal["buy_points"]}/20</b>

💥 Breakout:
<b>{min(signal["breakout_points"], 15)}/15</b>

🔥 Breakout Volume:
<b>{min(signal["breakout_volume_points"], 10)}/10</b>

🕯 Candle:
<b>{signal["candle_points"]}/10</b>

━━━━━━━━━━━━━━━━━━

🎯 ENTRY:
<b>{round_price(signal["entry"])}</b>

🛑 STOP LOSS:
<b>{round_price(signal["stop"])}</b>

🎯 TP1:
<b>{round_price(signal["tp1"])}</b>
+3%

🎯 TP2:
<b>{round_price(signal["tp2"])}</b>
+5%

🎯 TP3:
<b>{round_price(signal["tp3"])}</b>
+8%

━━━━━━━━━━━━━━━━━━

💧 24H Volume:
<b>${volume_24h.get(symbol.lower(), 0):,.0f}</b>

⚠️ <b>ALERT ONLY</b>
No automatic order.
"""


# ============================================================
# KLINE
# ============================================================

def process_kline(
    symbol,
    k
):

    candle = {

        "open_time":
            int(k["t"]),

        "open":
            safe_float(k["o"]),

        "high":
            safe_float(k["h"]),

        "low":
            safe_float(k["l"]),

        "close":
            safe_float(k["c"]),

        "volume":
            safe_float(k["v"]),

        "quote_volume":
            safe_float(k["q"]),

        "trades":
            int(k["n"]),

        "taker_buy_base":
            safe_float(k["V"]),

        "taker_buy_quote":
            safe_float(k["Q"]),

        "closed":
            bool(k["x"])
    }

    with data_lock:

        live_candles[
            symbol
        ] = candle

        if candle["closed"]:

            history = (
                candle_history.get(
                    symbol
                )
            )

            if history is None:

                history = deque(
                    maxlen=HISTORY_LIMIT
                )

                candle_history[
                    symbol
                ] = history

            if (
                not history
                or
                history[-1]["open_time"]
                != candle["open_time"]
            ):

                history.append(
                    candle
                )

            live_candles.pop(
                symbol,
                None
            )

            return

    # ========================================================
    # ANALYZE LIVE 5M CANDLE
    # ========================================================

    signal = analyze_symbol(
        symbol
    )

    if signal:

        message = format_signal(
            signal
        )

        print(
            "\n"
            + "=" * 70
            + "\n"
            + message
            + "\n"
            + "=" * 70
        )

        send_telegram(
            message
        )


# ============================================================
# BOOK TICKER WORKER
# ============================================================

def book_ticker_worker(
    symbol_chunk
):

    streams = [

        f"{symbol}@bookTicker"

        for symbol in symbol_chunk
    ]

    stream_string = "/".join(
        streams
    )

    url = (
        f"{BINANCE_WS}"
        f"/stream?streams="
        f"{stream_string}"
    )

    while running:

        try:

            print(
                f"BOOK WS connecting "
                f"{len(symbol_chunk)} "
                f"symbols..."
            )

            def on_open(ws):

                print(
                    f"BOOK WS CONNECTED "
                    f"{len(symbol_chunk)} "
                    f"symbols"
                )

            def on_message(
                ws,
                message
            ):

                try:

                    data = json.loads(
                        message
                    )

                    payload = data.get(
                        "data",
                        data
                    )

                    symbol = payload.get(
                        "s"
                    )

                    if not symbol:

                        return

                    symbol = (
                        symbol.lower()
                    )

                    if (
                        symbol
                        not in volume_24h
                    ):

                        return

                    bid = safe_float(
                        payload.get("b")
                    )

                    ask = safe_float(
                        payload.get("a")
                    )

                    if (
                        bid <= 0
                        or ask <= 0
                    ):

                        return

                    store_book(
                        symbol,
                        bid,
                        ask
                    )

                except Exception as e:

                    print(
                        "Book message error:",
                        e
                    )

            def on_error(
                ws,
                error
            ):

                print(
                    "BOOK WS error:",
                    error
                )

            def on_close(
                ws,
                code,
                reason
            ):

                print(
                    "BOOK WS closed:",
                    code,
                    reason
                )

            ws = websocket.WebSocketApp(

                url,

                on_open=on_open,

                on_message=on_message,

                on_error=on_error,

                on_close=on_close
            )

            ws.run_forever(

                ping_interval=20,

                ping_timeout=10
            )

        except Exception as e:

            print(
                "Book WS exception:",
                e
            )

        if running:

            time.sleep(
                RECONNECT_SECONDS
            )


# ============================================================
# KLINE WEBSOCKET
# ============================================================

def kline_websocket_worker(
    symbol_chunk
):

    streams = [

        f"{symbol}@kline_5m"

        for symbol in symbol_chunk
    ]

    stream_string = "/".join(
        streams
    )

    url = (
        f"{BINANCE_WS}"
        f"/stream?streams="
        f"{stream_string}"
    )

    while running:

        try:

            print(
                f"KLINE WS connecting "
                f"{len(symbol_chunk)} "
                f"symbols..."
            )

            def on_open(ws):

                print(
                    f"KLINE WS CONNECTED "
                    f"{len(symbol_chunk)} "
                    f"symbols"
                )

            def on_message(
                ws,
                message
            ):

                try:

                    data = json.loads(
                        message
                    )

                    payload = data.get(
                        "data",
                        data
                    )

                    if (
                        payload.get("e")
                        != "kline"
                    ):

                        return

                    symbol = (
                        payload["s"]
                        .lower()
                    )

                    k = payload["k"]

                    process_kline(
                        symbol,
                        k
                    )

                except Exception as e:

                    print(
                        "Kline message error:",
                        e
                    )

            def on_error(
                ws,
                error
            ):

                print(
                    "Kline WS error:",
                    error
                )

            def on_close(
                ws,
                code,
                reason
            ):

                print(
                    "Kline WS closed:",
                    code,
                    reason
                )

            ws = websocket.WebSocketApp(

                url,

                on_open=on_open,

                on_message=on_message,

                on_error=on_error,

                on_close=on_close
            )

            ws.run_forever(

                ping_interval=20,

                ping_timeout=10
            )

        except Exception as e:

            print(
                "Kline WS exception:",
                e
            )

        if running:

            time.sleep(
                RECONNECT_SECONDS
            )


# ============================================================
# STATUS
# ============================================================

def status_worker():

    while running:

        time.sleep(
            STATUS_INTERVAL
        )

        current = get_stats()

        with data_lock:

            symbol_count = len(
                symbols
            )

            history_count = len(
                candle_history
            )

            live_count = len(
                live_candles
            )

            book_count = len(
                book_data
            )

            updates = (
                book_updates
            )

            last_update = (
                last_book_update
            )

        if last_update > 0:

            book_age = (
                time.time()
                - last_update
            )

        else:

            book_age = -1

        print(
            "\n"
            "================ STATUS ================\n"
        )

        print(
            "Symbols:",
            symbol_count
        )

        print(
            "History:",
            history_count
        )

        print(
            "Live candles:",
            live_count
        )

        print(
            "Book ticker:",
            book_count
        )

        print(
            "Book updates:",
            updates
        )

        if book_age >= 0:

            print(
                "Last book update:",
                f"{book_age:.1f}s ago"
            )

        else:

            print(
                "Last book update: NEVER"
            )

        print(
            "\n----------- DEBUG 5M ---------------"
        )

        print(
            "Checked:",
            current["checked"]
        )

        print(
            "Momentum passed:",
            current["momentum"]
        )

        print(
            "Volume passed:",
            current["volume"]
        )

        print(
            "Buy pressure passed:",
            current["buy_pressure"]
        )

        print(
            "Resistance found:",
            current["resistance"]
        )

        print(
            "Breakout passed:",
            current["breakout"]
        )

        print(
            "Breakout volume passed:",
            current["breakout_volume"]
        )

        print(
            "Candle quality passed:",
            current["candle_quality"]
        )

        print(
            "Spread passed:",
            current["spread"]
        )

        print(
            "Spread missing:",
            current["spread_missing"]
        )

        print(
            "Spread rejected:",
            current["spread_rejected"]
        )

        print(
            "Score >= 70:",
            current["score"]
        )

        print(
            "Telegram OK:",
            current["telegram_ok"]
        )

        print(
            "Telegram failed:",
            current["telegram_failed"]
        )

        print(
            "SIGNALS:",
            current["signals"]
        )

        print(
            "--------------------------------------"
        )

        print(
            "=========================================\n"
        )

        reset_stats()


# ============================================================
# VOLUME REFRESH
# ============================================================

def volume_refresh_worker():

    global volume_24h

    while running:

        time.sleep(
            30 * 60
        )

        try:

            all_symbols = (
                load_exchange_info()
            )

            new_volume = (
                load_24h_volumes(
                    all_symbols
                )
            )

            with data_lock:

                volume_24h = (
                    new_volume
                )

            print(
                "24H volume refreshed:",
                len(new_volume)
            )

        except Exception as e:

            print(
                "Volume refresh error:",
                e
            )


# ============================================================
# MAIN
# ============================================================

def main():

    global symbols
    global volume_24h

    print(
        """
============================================================
       5M MOMENTUM + REAL BREAKOUT BOT v7
============================================================

TIMEFRAME:
5M

SYMBOL:
BINANCE SPOT USDT

24H VOLUME:
>= $1,000,000

HISTORY:
200 candles

RESISTANCE:
200 candles
Minimum age: 5 candles
Minimum tests: 2
Test distance: 2 candles
Tolerance: 0.40%
Recent test: 30 candles

SCORE:
Momentum           25
Volume             20
Buy Pressure       20
Breakout           15
Breakout Volume    10
Candle Quality     10
----------------------
TOTAL              100

MIN SIGNAL:
70

STRONG:
80

BREAKOUT:
>= +0.50%
<= +3.00%

BREAKOUT VOLUME:
>= 2x average

SPREAD:
<= 0.20%

BOOK:
Per-symbol @bookTicker

REST FALLBACK:
ACTIVE

TELEGRAM:
ACTIVE

============================================================
"""
    )

    # ========================================================
    # TELEGRAM
    # ========================================================

    telegram_startup_test()

    # ========================================================
    # SYMBOLS
    # ========================================================

    all_symbols = (
        load_exchange_info()
    )

    # ========================================================
    # 24H VOLUME
    # ========================================================

    volume_24h = (
        load_24h_volumes(
            all_symbols
        )
    )

    symbols = list(
        volume_24h.keys()
    )

    print(
        "Final symbols:",
        len(symbols)
    )

    if not symbols:

        print(
            "No symbols passed filters."
        )

        send_telegram(

            "🔴 <b>BOT ERROR</b>\n\n"

            "Heç bir Binance USDT "
            "coin filterdən keçmədi."
        )

        return

    # ========================================================
    # HISTORY
    # ========================================================

    load_all_histories()

    # ========================================================
    # STATUS
    # ========================================================

    threading.Thread(

        target=status_worker,

        daemon=True

    ).start()

    # ========================================================
    # VOLUME REFRESH
    # ========================================================

    threading.Thread(

        target=volume_refresh_worker,

        daemon=True

    ).start()

    # ========================================================
    # WEBSOCKET CHUNKS
    # ========================================================

    chunks = [

        symbols[i:i + WS_CHUNK_SIZE]

        for i in range(
            0,
            len(symbols),
            WS_CHUNK_SIZE
        )
    ]

    print(
        "WebSocket chunks:",
        len(chunks)
    )

    # ========================================================
    # START WEBSOCKETS
    # ========================================================

    for chunk in chunks:

        threading.Thread(

            target=kline_websocket_worker,

            args=(chunk,),

            daemon=True

        ).start()

        threading.Thread(

            target=book_ticker_worker,

            args=(chunk,),

            daemon=True

        ).start()

        time.sleep(0.5)

    # ========================================================
    # BOT STARTED
    # ========================================================

    print(
        "\n"
        "==================================================\n"
        "BOT STARTED\n"
        "==================================================\n"
    )

    send_telegram(

        "🟢 <b>5M BOT AKTİVDİR</b>\n\n"

        f"📊 Symbols: {len(symbols)}\n"

        "📚 History: 200 candles\n"

        "📡 Kline WebSocket: ACTIVE\n"

        "📖 Per-symbol BookTicker: ACTIVE\n"

        "🛟 REST Spread Fallback: ACTIVE\n"

        "🔎 Resistance Detection: ACTIVE\n"

        "💥 Breakout: +0.50% → +3.00%\n"

        "📊 Debug: ACTIVE\n\n"

        "Siqnallar gözlənilir..."
    )

    try:

        while True:

            time.sleep(5)

    except KeyboardInterrupt:

        running = False

        print(
            "Bot stopped."
        )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
