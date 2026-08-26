import os
import time
import json
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue, Empty
from datetime import datetime, timezone

import requests
import websocket


# ============================================================
# UNIFIED ALERT BOT
# BINANCE 5M MOMENTUM + REAL BREAKOUT
# +
# SOLANA DEXSCREENER MOMENTUM SCANNER
#
# ALERT ONLY
# NO AUTOMATIC ORDER
# ============================================================


# ============================================================
# ENVIRONMENT
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
CMC_API_KEY = os.getenv("CMC_API_KEY", "")


# ============================================================
# GENERAL
# ============================================================

UA = "UnifiedMomentumBreakoutBot/8.0"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA})

TELEGRAM_QUEUE = Queue(maxsize=500)

STOP_EVENT = threading.Event()


# ============================================================
# BINANCE
# ============================================================

BINANCE_REST = "https://api.binance.com"
BINANCE_WS = "wss://stream.binance.com:443"

INTERVAL = "5m"
HISTORY_LIMIT = 1441
AVERAGE_VOLUME_CANDLES = 20
RESISTANCE_LOOKBACK = 1440

MIN_RESISTANCE_AGE = 0
RESISTANCE_TOLERANCE_PERCENT = 0.60
MIN_TEST_DISTANCE_CANDLES = 2
MIN_RESISTANCE_TESTS = 0
MAX_RECENT_TEST_AGE = 1440

MIN_24H_QUOTE_VOLUME = 1_000_000
MAX_SPREAD_PERCENT = 0.20
BOOK_CACHE_MAX_AGE = 10

MAX_CURRENT_5M_PRICE = 8.0
MIN_BUY_PRESSURE = 55.0

MIN_SIGNAL_SCORE = 60
STRONG_SIGNAL_SCORE = 75
VOLUME_MIN_RATIO = 1.2

MIN_BREAKOUT_PERCENT = 1.0
MIN_BREAKOUT_VOLUME_RATIO = 1.5

MIN_CLOSE_POSITION = 70.0
MAX_UPPER_WICK_PERCENT = 30.0

STOP_BELOW_RESISTANCE_PERCENT = 0.50
TP1 = 3.0
TP2 = 5.0
TP3 = 8.0

WS_CHUNK_SIZE = 50
RECONNECT_SECONDS = 5
STATUS_INTERVAL = 60
SIGNAL_COOLDOWN_SECONDS = 30 * 60
SAME_RESISTANCE_TOLERANCE = 0.10

REST_BOOK_TIMEOUT = 5
REST_BOOK_MIN_INTERVAL = 1.0

BINANCE_MAX_WORKERS = 12


# ============================================================
# SOLANA / DEXSCREENER
# ============================================================

DEX_BASE = "https://api.dexscreener.com"

SOLANA_SCAN_INTERVAL = 15

# Latest agreed filters
SOLANA_MIN_AGE_DAYS = 20
SOLANA_MIN_MCAP = 7_000
SOLANA_MAX_MCAP = 100_000

SOLANA_MIN_MOMENTUM = 1.0
SOLANA_MAX_MOMENTUM = 50.0

SOLANA_MIN_LIQUIDITY = 7_000

# 5M volume minimum remains $100
SOLANA_MIN_5M_VOLUME = 100

# IMPORTANT:
# liquidity increasing = NOT mandatory
# buys increasing     = NOT mandatory
SOLANA_REQUIRE_LIQUIDITY_INCREASE = False
SOLANA_REQUIRE_BUYS_INCREASE = False

# Buys > sells IS mandatory
SOLANA_REQUIRE_BUYS_GT_SELLS = True

SOLANA_COOLDOWN = 24 * 60 * 60

SOLANA_MAX_WORKERS = 8

# Search/list pages
DEX_SEARCH_QUERIES = [
    "SOL",
    "USDC",
    "USDT",
    "WIF",
    "BONK",
    "MEME",
    "AI",
    "PEPE",
]

SOLANA_ALERTED = {}
SOLANA_PREVIOUS = {}

# Binance state
BINANCE_SYMBOLS = []
BINANCE_HISTORY = {}
BINANCE_LIVE = {}
BINANCE_BOOKS = {}
BINANCE_LAST_SIGNAL = {}
BINANCE_LAST_BOOK_REST = {}
BINANCE_LAST_STATUS = 0

STATE_LOCK = threading.RLock()


# ============================================================
# HELPERS
# ============================================================

def now_ts():
    return time.time()


def utc_text():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def safe_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def pct_change(old, new):
    if old <= 0:
        return 0.0
    return (new / old - 1.0) * 100.0


# ============================================================
# TELEGRAM
# ============================================================

def telegram_worker():
    while not STOP_EVENT.is_set():
        try:
            msg = TELEGRAM_QUEUE.get(timeout=1)
        except Empty:
            continue

        try:
            if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
                print("Telegram credentials are missing")
                continue

            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": msg,
                "disable_web_page_preview": True,
            }

            r = SESSION.post(url, json=payload, timeout=15)
            if not r.ok:
                print("Telegram error:", r.status_code, r.text[:300])

        except Exception as e:
            print("Telegram worker error:", e)

        finally:
            TELEGRAM_QUEUE.task_done()


def send_alert(text):
    try:
        TELEGRAM_QUEUE.put_nowait(text)
    except Exception:
        print("Telegram queue full")


# ============================================================
# BINANCE REST
# ============================================================

def binance_get(path, params=None, timeout=10):
    url = BINANCE_REST + path
    r = SESSION.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def load_binance_symbols():
    """
    Loads Binance USDT spot symbols.
    If CMC_API_KEY is present, CMC rank 1-2000 is also applied.
    """
    try:
        info = binance_get("/api/v3/exchangeInfo", timeout=20)

        allowed = set()

        if CMC_API_KEY:
            try:
                cmc_url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
                headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}
                params = {
                    "start": 1,
                    "limit": 2000,
                    "convert": "USD",
                }

                cr = SESSION.get(
                    cmc_url,
                    headers=headers,
                    params=params,
                    timeout=20,
                )

                if cr.ok:
                    for item in cr.json().get("data", []):
                        sym = str(item.get("symbol", "")).upper()
                        rank = int(item.get("cmc_rank") or 999999)
                        if 1 <= rank <= 2000:
                            allowed.add(sym)
            except Exception as e:
                print("CMC refresh error:", e)

        symbols = []

        for s in info.get("symbols", []):
            if s.get("status") != "TRADING":
                continue
            if s.get("quoteAsset") != "USDT":
                continue
            if not s.get("isSpotTradingAllowed", False):
                continue

            base = str(s.get("baseAsset", "")).upper()

            if base.endswith(("UP", "DOWN", "BULL", "BEAR")):
                continue

            if allowed and base not in allowed:
                continue

            symbols.append(s["symbol"])

        with STATE_LOCK:
            BINANCE_SYMBOLS[:] = symbols

        print("BINANCE")
        print("Symbols:", len(symbols))

        return symbols

    except Exception as e:
        print("Binance symbol load error:", e)
        return []


def get_klines(symbol, limit=HISTORY_LIMIT):
    return binance_get(
        "/api/v3/klines",
        {
            "symbol": symbol,
            "interval": INTERVAL,
            "limit": limit,
        },
        timeout=15,
    )


def get_24h(symbol):
    return binance_get(
        "/api/v3/ticker/24hr",
        {"symbol": symbol},
        timeout=10,
    )


def get_book(symbol):
    return binance_get(
        "/api/v3/ticker/bookTicker",
        {"symbol": symbol},
        timeout=REST_BOOK_TIMEOUT,
    )


# ============================================================
# BINANCE BOOK
# ============================================================

def update_book(symbol, bid, ask, bid_qty, ask_qty):
    bid = safe_float(bid)
    ask = safe_float(ask)
    bid_qty = safe_float(bid_qty)
    ask_qty = safe_float(ask_qty)

    if bid <= 0 or ask <= 0:
        return

    mid = (bid + ask) / 2.0
    spread = ((ask - bid) / mid) * 100.0 if mid else 999.0

    with STATE_LOCK:
        BINANCE_BOOKS[symbol] = {
            "bid": bid,
            "ask": ask,
            "bid_qty": bid_qty,
            "ask_qty": ask_qty,
            "spread": spread,
            "time": now_ts(),
        }


def get_book_cached(symbol):
    with STATE_LOCK:
        book = BINANCE_BOOKS.get(symbol)

    if book and now_ts() - book["time"] <= BOOK_CACHE_MAX_AGE:
        return book

    try:
        last = BINANCE_LAST_BOOK_REST.get(symbol, 0)
        if now_ts() - last >= REST_BOOK_MIN_INTERVAL:
            BINANCE_LAST_BOOK_REST[symbol] = now_ts()
            data = get_book(symbol)

            update_book(
                symbol,
                data.get("bidPrice"),
                data.get("askPrice"),
                data.get("bidQty"),
                data.get("askQty"),
            )

            with STATE_LOCK:
                return BINANCE_BOOKS.get(symbol)

    except Exception:
        pass

    return book


# ============================================================
# BINANCE WEBSOCKET
# ============================================================

def ws_worker(symbols):
    """Binance WS for live price/spread with chunking and auto-reconnect."""
    if not symbols:
        return

    chunks = [symbols[i:i + WS_CHUNK_SIZE] for i in range(0, len(symbols), WS_CHUNK_SIZE)]

    def run_chunk(chunk):
        streams = []
        for symbol in chunk:
            s = symbol.lower()
            streams.extend([f"{s}@bookTicker", f"{s}@ticker"])
        url = BINANCE_WS + "/stream?streams=" + "/".join(streams)

        while not STOP_EVENT.is_set():
            try:
                def on_message(ws, message):
                    try:
                        data = json.loads(message).get("data", {})
                        event = data.get("e")
                        symbol = data.get("s")
                        if not symbol:
                            return
                        if event == "bookTicker":
                            update_book(symbol, data.get("b"), data.get("a"), data.get("B"), data.get("A"))
                        elif event == "24hrTicker":
                            with STATE_LOCK:
                                BINANCE_LIVE[symbol] = {
                                    "price": safe_float(data.get("c")),
                                    "quote_volume": safe_float(data.get("q")),
                                    "change": safe_float(data.get("P")),
                                    "time": now_ts(),
                                }
                    except Exception as e:
                        print("Binance WS message error:", e)

                def on_error(ws, error):
                    print("Binance WS error:", error)

                def on_close(ws, code, msg):
                    print(f"Binance WS closed: {code} {msg} | reconnecting in {RECONNECT_SECONDS}s")

                ws = websocket.WebSocketApp(url, on_message=on_message, on_error=on_error, on_close=on_close)
                ws.run_forever(ping_interval=20, ping_timeout=10, origin="https://www.binance.com")
            except Exception as e:
                print("Binance WS worker error:", e)
            if not STOP_EVENT.is_set():
                time.sleep(RECONNECT_SECONDS)

    for chunk in chunks:
        threading.Thread(target=run_chunk, args=(chunk,), daemon=True).start()


# ============================================================
# BINANCE ANALYSIS
# ============================================================

def candle_dict(k):
    return {
        "open": safe_float(k[1]),
        "high": safe_float(k[2]),
        "low": safe_float(k[3]),
        "close": safe_float(k[4]),
        "volume": safe_float(k[5]),
        "close_time": int(k[6]),
    }


def prepare_history(symbol, klines):
    candles = [candle_dict(k) for k in klines]

    # Only closed candles
    current_ms = int(time.time() * 1000)
    candles = [c for c, k in zip(candles, klines) if int(k[6]) <= current_ms]

    with STATE_LOCK:
        BINANCE_HISTORY[symbol] = candles

    return candles


def momentum_5m(candles):
    if len(candles) < 2:
        return 0.0

    prev = candles[-2]["close"]
    last = candles[-1]["close"]

    return pct_change(prev, last)


def volume_ratio(candles):
    if len(candles) < AVERAGE_VOLUME_CANDLES + 1:
        return 0.0

    avg = sum(
        c["volume"]
        for c in candles[-AVERAGE_VOLUME_CANDLES-1:-1]
    ) / AVERAGE_VOLUME_CANDLES

    if avg <= 0:
        return 0.0

    return candles[-1]["volume"] / avg


def buy_pressure_estimate(candles):
    """
    Candle-based approximation when trade-side data is not available.
    Close position is used as buying pressure proxy.
    """
    if not candles:
        return 0.0

    c = candles[-1]
    rng = c["high"] - c["low"]

    if rng <= 0:
        return 50.0

    position = (c["close"] - c["low"]) / rng * 100.0
    return position


def find_resistance(candles):
    """Highest high of the previous 1440 CLOSED 5M candles.
    The latest closed candle is excluded because it is the breakout candidate.
    """
    if len(candles) < RESISTANCE_LOOKBACK + 1:
        return None
    data = candles[-(RESISTANCE_LOOKBACK + 1):-1]
    highest = max(data, key=lambda c: c["high"])
    return {"level": highest["high"], "age": len(data)-1-data.index(highest), "tests": 0}


def breakout_data(candles, resistance):
    if not resistance or len(candles) < 2:
        return None

    level = resistance["level"]
    last = candles[-1]

    breakout_pct = pct_change(level, last["close"])

    avg_vol = 0.0
    if len(candles) >= AVERAGE_VOLUME_CANDLES + 1:
        avg_vol = sum(
            c["volume"]
            for c in candles[-AVERAGE_VOLUME_CANDLES-1:-1]
        ) / AVERAGE_VOLUME_CANDLES

    vr = last["volume"] / avg_vol if avg_vol > 0 else 0.0

    rng = last["high"] - last["low"]
    close_position = (
        ((last["close"] - last["low"]) / rng) * 100.0
        if rng > 0 else 0.0
    )

    upper_wick = last["high"] - max(last["open"], last["close"])
    upper_wick_pct = (
        upper_wick / rng * 100.0
        if rng > 0 else 100.0
    )

    valid = (
        breakout_pct >= MIN_BREAKOUT_PERCENT
        and vr >= MIN_BREAKOUT_VOLUME_RATIO
        and close_position >= MIN_CLOSE_POSITION
        and upper_wick_pct <= MAX_UPPER_WICK_PERCENT
    )

    return {
        "breakout_pct": breakout_pct,
        "volume_ratio": vr,
        "close_position": close_position,
        "upper_wick_pct": upper_wick_pct,
        "valid": valid,
    }


def analyze_binance(symbol):
    try:
        klines = get_klines(symbol)
        candles = prepare_history(symbol, klines)

        if len(candles) < 30:
            return None

        ticker = get_24h(symbol)
        qvol = safe_float(ticker.get("quoteVolume"))

        if qvol < MIN_24H_QUOTE_VOLUME:
            return None

        live = BINANCE_LIVE.get(symbol, {})
        price = live.get("price") or candles[-1]["close"]

        momentum = momentum_5m(candles)
        vr = volume_ratio(candles)
        buy_pressure = buy_pressure_estimate(candles)

        if momentum <= 0:
            return None

        if momentum > MAX_CURRENT_5M_PRICE:
            return None

        if vr < VOLUME_MIN_RATIO:
            return None

        if buy_pressure < MIN_BUY_PRESSURE:
            return None

        book = get_book_cached(symbol)

        if not book:
            return None

        spread = book.get("spread", 999)

        if spread > MAX_SPREAD_PERCENT:
            return None

        resistance = find_resistance(candles)
        br = breakout_data(candles, resistance)

        if not br or not br["valid"]:
            return None

        score = 0

        if momentum >= 1:
            score += 15
        if momentum >= 2:
            score += 10
        if vr >= 1.2:
            score += 10
        if vr >= 1.5:
            score += 10
        if buy_pressure >= 55:
            score += 10
        if buy_pressure >= 70:
            score += 5
        if br["breakout_pct"] >= 0.30:
            score += 10
        if br["volume_ratio"] >= 1.5:
            score += 10
        if br["close_position"] >= 70:
            score += 5
        if spread <= 0.10:
            score += 5

        if score < MIN_SIGNAL_SCORE:
            return None

        strength = "🚀 STRONG BUY" if score >= STRONG_SIGNAL_SCORE else "🔥 BUY"

        key = symbol
        now = now_ts()

        with STATE_LOCK:
            last_signal = BINANCE_LAST_SIGNAL.get(key, 0)

        if now - last_signal < SIGNAL_COOLDOWN_SECONDS:
            return None

        with STATE_LOCK:
            BINANCE_LAST_SIGNAL[key] = now

        return (
            f"{strength}\n"
            f"🟡 BINANCE 5M BREAKOUT\n\n"
            f"🪙 {symbol}\n"
            f"💰 Price: {price:g}\n"
            f"📈 5M Momentum: {momentum:+.2f}%\n"
            f"📊 Volume ratio: {vr:.2f}x\n"
            f"🟢 Buy pressure: {buy_pressure:.1f}%\n"
            f"🚀 Breakout: {br['breakout_pct']:+.2f}%\n"
            f"📊 Breakout volume: {br['volume_ratio']:.2f}x\n"
            f"🕯 Close position: {br['close_position']:.1f}%\n"
            f"📐 Spread: {spread:.3f}%\n"
            f"⭐ Score: {score}\n\n"
            f"🛑 Stop: ~{level_stop(resistance['level']):g}\n"
            f"🎯 TP1: +{TP1}%\n"
            f"🎯 TP2: +{TP2}%\n"
            f"🎯 TP3: +{TP3}%\n\n"
            f"⚠️ ALERT ONLY\n"
            f"NO AUTOMATIC ORDER"
        )

    except Exception:
        return None


def level_stop(resistance):
    return resistance * (1.0 - STOP_BELOW_RESISTANCE_PERCENT / 100.0)


def binance_scan_loop():
    global BINANCE_LAST_STATUS

    while not STOP_EVENT.is_set():
        try:
            symbols = list(BINANCE_SYMBOLS)

            checked = 0
            signals = 0

            with ThreadPoolExecutor(max_workers=BINANCE_MAX_WORKERS) as ex:
                futures = {
                    ex.submit(analyze_binance, s): s
                    for s in symbols
                }

                for f in as_completed(futures):
                    checked += 1
                    try:
                        result = f.result()
                        if result:
                            signals += 1
                            send_alert(result)
                    except Exception:
                        pass

            print(
                f"BINANCE STATUS | Symbols: {len(symbols)} "
                f"| Checked: {checked} | Signals: {signals}"
            )

        except Exception as e:
            print("Binance scan error:", e)

        time.sleep(20)


# ============================================================
# SOLANA DEXSCREENER
# ============================================================

def dex_get(path, timeout=15):
    r = SESSION.get(
        DEX_BASE + path,
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def get_solana_pairs():
    """Broad, deduplicated Solana pair discovery via DexScreener search."""
    pairs = {}
    queries = list(dict.fromkeys(DEX_SEARCH_QUERIES + [
        "SOL", "WSOL", "USDC", "USDT", "RAY", "JUP", "BONK", "WIF",
        "POPCAT", "MEW", "PONKE", "BOME", "SAMO", "MYRO", "MOTHER",
        "GOAT", "AI", "MEME", "INU", "CAT", "DOG", "COIN", "TOKEN", "USD"
    ]))
    for q in queries:
        try:
            r = SESSION.get(DEX_BASE + "/latest/dex/search", params={"q": q}, timeout=15)
            if not r.ok:
                continue
            for p in r.json().get("pairs", []):
                if p.get("chainId") != "solana":
                    continue
                address = p.get("pairAddress")
                if address:
                    pairs[address] = p
        except Exception as e:
            print(f"DexScreener query error [{q}]: {e}")
    return list(pairs.values())


def pair_age_days(pair):
    created = pair.get("pairCreatedAt")
    if not created:
        return None

    try:
        created_s = float(created) / 1000.0
        return (time.time() - created_s) / 86400.0
    except Exception:
        return None


def get_5m_stats(pair):
    """
    DexScreener pair object does not provide a direct historical
    5-minute buy/sell time series in the basic pair endpoint.

    We therefore use the current pair's 5m fields:
      priceChange.m5
      volume.m5
      txns.m5.buys
      txns.m5.sells

    Increasing conditions are compared with our local previous scan.
    """
    pc = pair.get("priceChange") or {}
    vol = pair.get("volume") or {}
    txns = pair.get("txns") or {}
    m5_txns = txns.get("m5") or {}

    return {
        "momentum": safe_float(pc.get("m5")),
        "volume": safe_float(vol.get("m5")),
        "buys": int(safe_float(m5_txns.get("buys"))),
        "sells": int(safe_float(m5_txns.get("sells"))),
    }


def solana_analyze(pair):
    try:
        address = pair.get("pairAddress")
        if not address:
            return None

        base = pair.get("baseToken") or {}
        symbol = base.get("symbol") or "UNKNOWN"

        age = pair_age_days(pair)
        if age is None or age <= SOLANA_MIN_AGE_DAYS:
            return None

        mcap = safe_float(
            pair.get("marketCap") or pair.get("fdv")
        )

        if mcap < SOLANA_MIN_MCAP or mcap > SOLANA_MAX_MCAP:
            return None

        liquidity_obj = pair.get("liquidity") or {}
        liquidity = safe_float(liquidity_obj.get("usd"))

        if liquidity < SOLANA_MIN_LIQUIDITY:
            return None

        stats = get_5m_stats(pair)

        momentum = stats["momentum"]
        volume = stats["volume"]
        buys = stats["buys"]
        sells = stats["sells"]

        if momentum < SOLANA_MIN_MOMENTUM:
            return None

        if momentum > SOLANA_MAX_MOMENTUM:
            return None

        if volume < SOLANA_MIN_5M_VOLUME:
            return None

        if SOLANA_REQUIRE_BUYS_GT_SELLS and buys <= sells:
            return None

        previous = SOLANA_PREVIOUS.get(address)

        # Momentum increasing remains mandatory.
        if previous is not None:
            if momentum <= previous["momentum"]:
                return None

            if volume <= previous["volume"]:
                return None

        else:
            # First observation is stored, but does not alert.
            SOLANA_PREVIOUS[address] = {
                "momentum": momentum,
                "volume": volume,
                "liquidity": liquidity,
                "buys": buys,
                "sells": sells,
                "time": now_ts(),
            }
            return None

        # IMPORTANT:
        # Liquidity increase is NOT mandatory.
        # Buys increase is NOT mandatory.
        #
        # We intentionally do not reject the token here based on either.

        now = now_ts()
        last_alert = SOLANA_ALERTED.get(address, 0)

        if now - last_alert < SOLANA_COOLDOWN:
            return None

        SOLANA_ALERTED[address] = now

        SOLANA_PREVIOUS[address] = {
            "momentum": momentum,
            "volume": volume,
            "liquidity": liquidity,
            "buys": buys,
            "sells": sells,
            "time": now,
        }

        url = pair.get("url") or (
            f"https://dexscreener.com/solana/{address}"
        )

        return (
            f"🚀 SOLANA MOMENTUM\n\n"
            f"🪙 {symbol}\n"
            f"⏱ Age: {age:.1f} days\n"
            f"💎 MCap: ${mcap:,.0f}\n"
            f"💧 Liquidity: ${liquidity:,.0f}\n"
            f"📈 5M Momentum: {momentum:+.2f}%\n"
            f"📊 5M Volume: ${volume:,.0f}\n"
            f"🟢 5M Buys: {buys}\n"
            f"🔴 5M Sells: {sells}\n"
            f"⚖️ Buys > Sells: YES\n\n"
            f"🔗 {url}\n\n"
            f"⚠️ ALERT ONLY\n"
            f"NO AUTOMATIC ORDER"
        )

    except Exception:
        return None


def solana_scan_loop():
    while not STOP_EVENT.is_set():
        try:
            pairs = get_solana_pairs()

            passed_age = 0
            passed_mcap = 0
            passed_liq = 0
            passed_momentum = 0
            passed_volume = 0
            passed_buys = 0
            signals = 0

            for pair in pairs:
                age = pair_age_days(pair)

                if age is not None and age > SOLANA_MIN_AGE_DAYS:
                    passed_age += 1

                mcap = safe_float(
                    pair.get("marketCap") or pair.get("fdv")
                )
                if SOLANA_MIN_MCAP <= mcap <= SOLANA_MAX_MCAP:
                    passed_mcap += 1

                liq = safe_float(
                    (pair.get("liquidity") or {}).get("usd")
                )
                if liq >= SOLANA_MIN_LIQUIDITY:
                    passed_liq += 1

                stats = get_5m_stats(pair)

                if (
                    SOLANA_MIN_MOMENTUM
                    <= stats["momentum"]
                    <= SOLANA_MAX_MOMENTUM
                ):
                    passed_momentum += 1

                if stats["volume"] >= SOLANA_MIN_5M_VOLUME:
                    passed_volume += 1

                if stats["buys"] > stats["sells"]:
                    passed_buys += 1

                result = solana_analyze(pair)

                if result:
                    signals += 1
                    send_alert(result)

            print(
                "SOLANA DEXSCREENER | "
                f"Pairs: {len(pairs)} | "
                f"Age passed: {passed_age} | "
                f"MCap passed: {passed_mcap} | "
                f"Momentum passed: {passed_momentum} | "
                f"Volume passed: {passed_volume} | "
                f"Liquidity passed: {passed_liq} | "
                f"Buys passed: {passed_buys} | "
                f"Signals: {signals}"
            )

        except Exception as e:
            print("Solana scan error:", e)

        time.sleep(SOLANA_SCAN_INTERVAL)


# ============================================================
# STARTUP
# ============================================================

def print_config():
    print("=" * 60)
    print("UNIFIED ALERT BOT")
    print("=" * 60)
    print("BINANCE:")
    print("  5M momentum + 1440 closed-candle breakout")
    print(f"  Breakout: +{MIN_BREAKOUT_PERCENT}% above previous {RESISTANCE_LOOKBACK} closed 5M candles")
    print("  Alert only")
    print()
    print("SOLANA DEXSCREENER:")
    print(f"  Age > {SOLANA_MIN_AGE_DAYS} days")
    print(f"  MCap: ${SOLANA_MIN_MCAP:,} - ${SOLANA_MAX_MCAP:,}")
    print(
        f"  5M momentum: +{SOLANA_MIN_MOMENTUM}% "
        f"to +{SOLANA_MAX_MOMENTUM}%"
    )
    print("  Momentum increasing: REQUIRED")
    print("  5M volume increasing: REQUIRED")
    print(f"  Minimum liquidity: ${SOLANA_MIN_LIQUIDITY:,}")
    print("  Liquidity increasing: NOT REQUIRED")
    print("  Buys increasing: NOT REQUIRED")
    print("  Buys > sells: REQUIRED")
    print(f"  Minimum 5M volume: ${SOLANA_MIN_5M_VOLUME}")
    print("  Same token cooldown: 24H")
    print("  ALERT ONLY")
    print("  NO AUTOMATIC ORDER")
    print("=" * 60)


def main():
    print_config()

    threading.Thread(
        target=telegram_worker,
        daemon=True,
    ).start()

    load_binance_symbols()

    # Binance WS
    if BINANCE_SYMBOLS:
        threading.Thread(
            target=ws_worker,
            args=(BINANCE_SYMBOLS,),
            daemon=True,
        ).start()

    # Binance scanner
    threading.Thread(
        target=binance_scan_loop,
        daemon=True,
    ).start()

    # Solana DexScreener scanner
    threading.Thread(
        target=solana_scan_loop,
        daemon=True,
    ).start()

    while not STOP_EVENT.is_set():
        time.sleep(60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        STOP_EVENT.set()
        print("Stopped.")
