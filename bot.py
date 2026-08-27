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
HISTORY_LIMIT = 1440
AVERAGE_VOLUME_CANDLES = 20
RESISTANCE_LOOKBACK = 1440

# Resistance = highest high of previous 1440 CLOSED 5M candles.
# Current live candle is never included in resistance.

MIN_24H_QUOTE_VOLUME = 1_000_000
MAX_SPREAD_PERCENT = 0.20
BOOK_CACHE_MAX_AGE = 10

MAX_CURRENT_5M_PRICE = None
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
SIGNAL_COOLDOWN_SECONDS = 24 * 60 * 60
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
BINANCE_STATUS = {}

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

def telegram_send_now(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM ERROR: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
        "parse_mode": "HTML",
    }

    try:
        r = SESSION.post(url, json=payload, timeout=15)
        print(f"TELEGRAM HTTP: {r.status_code}")
        if r.ok:
            print("TELEGRAM OK")
            return True
        print("TELEGRAM ERROR BODY:", r.text[:1000])
    except Exception as e:
        print("TELEGRAM EXCEPTION:", repr(e))
    return False


def telegram_worker():
    while not STOP_EVENT.is_set():
        try:
            msg = TELEGRAM_QUEUE.get(timeout=1)
        except Empty:
            continue
        try:
            telegram_send_now(msg)
        finally:
            TELEGRAM_QUEUE.task_done()


def telegram_startup_test():
    telegram_send_now(
        "🟢 <b>UNIFIED ALERT BOT TEST</b>\n\n"
        "Binance + Solana DexScreener aktivdir.\n"
        "Telegram bağlantısı işləyir.\n\n"
        "⚠️ ALERT ONLY\n"
        "No automatic order."
    )


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
    """
    Stable Binance combined WebSocket.
    Each worker handles a small chunk and reconnects automatically.
    Streams:
      - 5M kline
      - bookTicker
      - 24hrTicker
    """
    if not symbols:
        return

    streams = []
    for symbol in symbols:
        s = symbol.lower()
        streams.append(f"{s}@kline_5m")
        streams.append(f"{s}@bookTicker")
        streams.append(f"{s}@ticker")

    url = BINANCE_WS + "/stream?streams=" + "/".join(streams)

    while not STOP_EVENT.is_set():
        try:
            def on_message(ws, message):
                try:
                    obj = json.loads(message)
                    data = obj.get("data", {})
                    event = data.get("e")
                    symbol = data.get("s")
                    if not symbol:
                        return

                    symbol = symbol.upper()

                    if event == "bookTicker":
                        update_book(
                            symbol,
                            data.get("b"),
                            data.get("a"),
                            data.get("B"),
                            data.get("A"),
                        )

                    elif event == "24hrTicker":
                        with STATE_LOCK:
                            BINANCE_LIVE.setdefault(symbol, {})
                            BINANCE_LIVE[symbol].update({
                                "price": safe_float(data.get("c")),
                                "quote_volume": safe_float(data.get("q")),
                                "change": safe_float(data.get("P")),
                                "time": now_ts(),
                            })

                    elif event == "kline":
                        k = data.get("k") or {}
                        candle = {
                            "open_time": int(k.get("t", 0)),
                            "open": safe_float(k.get("o")),
                            "high": safe_float(k.get("h")),
                            "low": safe_float(k.get("l")),
                            "close": safe_float(k.get("c")),
                            "volume": safe_float(k.get("v")),
                            "quote_volume": safe_float(k.get("q")),
                            "taker_buy_quote": safe_float(k.get("Q")),
                            "closed": bool(k.get("x")),
                            "close_time": int(k.get("T", 0)),
                        }

                        with STATE_LOCK:
                            BINANCE_LIVE.setdefault(symbol, {})
                            BINANCE_LIVE[symbol]["candle"] = candle

                            if candle["closed"]:
                                hist = BINANCE_HISTORY.get(symbol)
                                if hist is None:
                                    hist = deque(maxlen=HISTORY_LIMIT)
                                    BINANCE_HISTORY[symbol] = hist

                                if not hist or hist[-1]["open_time"] != candle["open_time"]:
                                    hist.append(candle)

                                BINANCE_LIVE[symbol].pop("candle", None)

                except Exception:
                    pass

            def on_error(ws, error):
                print("Binance WS error:", error)

            def on_close(ws, code, msg):
                print("Binance WS closed:", code, msg)
                print(f"Binance WS reconnecting in {RECONNECT_SECONDS}s...")

            ws = websocket.WebSocketApp(
                url,
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
            print("Binance WS worker error:", e)

        if not STOP_EVENT.is_set():
            time.sleep(RECONNECT_SECONDS)


def load_binance_histories(symbols):
    """
    Load 1440 CLOSED 5M candles at startup.
    """
    print(f"Loading {HISTORY_LIMIT} closed 5M candles for {len(symbols)} symbols...")

    def one(symbol):
        try:
            klines = get_klines(symbol, HISTORY_LIMIT)
            candles = []
            now_ms = int(time.time() * 1000)

            for k in klines:
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
                    "taker_buy_quote": safe_float(k[10]),
                    "closed": True,
                    "close_time": close_time,
                })

            return symbol, deque(candles[-HISTORY_LIMIT:], maxlen=HISTORY_LIMIT)
        except Exception as e:
            print(f"History error {symbol}: {e}")
            return symbol, None

    loaded = 0
    with ThreadPoolExecutor(max_workers=12) as ex:
        futures = [ex.submit(one, s) for s in symbols]
        for f in as_completed(futures):
            symbol, candles = f.result()
            loaded += 1
            if candles and len(candles) >= RESISTANCE_LOOKBACK:
                with STATE_LOCK:
                    BINANCE_HISTORY[symbol] = candles
            if loaded % 50 == 0:
                print(f"History: {loaded}/{len(symbols)}")

    print("1440-candle histories ready:", len(BINANCE_HISTORY))


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


def momentum_5m(candle):
    if not candle:
        return 0.0
    return pct_change(candle["open"], candle["close"])


def volume_ratio(history, candle):
    if len(history) < AVERAGE_VOLUME_CANDLES:
        return 0.0

    vals = [
        c["quote_volume"]
        for c in list(history)[-AVERAGE_VOLUME_CANDLES:]
        if c["quote_volume"] > 0
    ]
    if not vals:
        return 0.0

    avg = sum(vals) / len(vals)
    return candle["quote_volume"] / avg if avg > 0 else 0.0


def buy_pressure_estimate(candle):
    if not candle:
        return 0.0

    # Binance kline provides taker-buy quote volume.
    total = candle["quote_volume"]
    buy = candle.get("taker_buy_quote", 0.0)

    if total > 0 and buy > 0:
        return (buy / total) * 100.0

    rng = candle["high"] - candle["low"]
    if rng <= 0:
        return 50.0

    return ((candle["close"] - candle["low"]) / rng) * 100.0


def find_resistance(history):
    """
    Highest HIGH of the previous 1440 CLOSED 5M candles.
    The current live candle is excluded.
    """
    if len(history) < RESISTANCE_LOOKBACK:
        return None

    data = list(history)[-RESISTANCE_LOOKBACK:]
    highest = max(data, key=lambda c: c["high"])

    return {
        "level": highest["high"],
        "age": len(data) - 1 - data.index(highest),
        "tests": None,
    }


def breakout_data(history, current, resistance):
    if not resistance or not current or len(history) < RESISTANCE_LOOKBACK:
        return None

    level = resistance["level"]
    breakout_pct = pct_change(level, current["close"])

    vr = volume_ratio(history, current)

    rng = current["high"] - current["low"]
    close_position = (
        ((current["close"] - current["low"]) / rng) * 100.0
        if rng > 0 else 0.0
    )

    upper_wick = current["high"] - max(current["open"], current["close"])
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


def analyze_binance(symbol, status):
    try:
        with STATE_LOCK:
            history = BINANCE_HISTORY.get(symbol)
            live = dict(BINANCE_LIVE.get(symbol, {}))

        if not history or len(history) < RESISTANCE_LOOKBACK:
            return None

        status["history"] += 1

        qvol = safe_float(live.get("quote_volume"))
        if qvol <= 0:
            ticker = get_24h(symbol)
            qvol = safe_float(ticker.get("quoteVolume"))

        if qvol < MIN_24H_QUOTE_VOLUME:
            return None
        status["24h"] += 1

        candle = live.get("candle")
        if not candle:
            # Use latest closed candle only if no live kline exists.
            candle = list(history)[-1]

        momentum = momentum_5m(candle)

        if momentum < MIN_PRICE_CHANGE:
            return None
        status["momentum"] += 1

        if MAX_CURRENT_5M_PRICE is not None and momentum > MAX_CURRENT_5M_PRICE:
            return None

        vr = volume_ratio(history, candle)
        if vr < VOLUME_MIN_RATIO:
            return None
        status["volume"] += 1

        buy_pressure = buy_pressure_estimate(candle)
        if buy_pressure < MIN_BUY_PRESSURE:
            return None
        status["buy"] += 1

        book = get_book_cached(symbol)
        if not book:
            return None

        spread = book.get("spread", 999)
        if spread > MAX_SPREAD_PERCENT:
            return None
        status["spread"] += 1

        resistance = find_resistance(history)
        if not resistance:
            return None
        status["resistance"] += 1

        br = breakout_data(history, candle, resistance)

        # HARD REQUIREMENT: +1% above the highest high
        # of the previous 1440 CLOSED 5M candles.
        if br["breakout_pct"] < MIN_BREAKOUT_PERCENT:
            return None
        status["breakout"] += 1

        if br["volume_ratio"] < MIN_BREAKOUT_VOLUME_RATIO:
            return None
        status["breakout_volume"] += 1

        if br["close_position"] < MIN_CLOSE_POSITION:
            return None
        if br["upper_wick_pct"] > MAX_UPPER_WICK_PERCENT:
            return None
        status["candle"] += 1

        score = 0
        if momentum >= 1:
            score += 15
        if momentum >= 2:
            score += 10
        if momentum >= 3:
            score += 10
        if vr >= 1.2:
            score += 10
        if vr >= 1.5:
            score += 10
        if buy_pressure >= 55:
            score += 10
        if buy_pressure >= 70:
            score += 5
        if br["breakout_pct"] >= 1.0:
            score += 10
        if br["volume_ratio"] >= 1.5:
            score += 10
        if br["close_position"] >= 70:
            score += 5
        if spread <= 0.10:
            score += 5

        if score < MIN_SIGNAL_SCORE:
            return None
        status["score"] += 1

        key = symbol
        now = now_ts()

        with STATE_LOCK:
            last_signal = BINANCE_LAST_SIGNAL.get(key, 0)

        if now - last_signal < SIGNAL_COOLDOWN_SECONDS:
            return None

        with STATE_LOCK:
            BINANCE_LAST_SIGNAL[key] = now

        price = candle["close"]
        strength = "🚀 STRONG BUY" if score >= STRONG_SIGNAL_SCORE else "🔥 BUY"

        return (
            f"{strength}\n"
            f"🟡 BINANCE 5M REAL BREAKOUT\n\n"
            f"🪙 {symbol}\n"
            f"💰 Price: {price:g}\n"
            f"📈 5M Momentum: {momentum:+.2f}%\n"
            f"📊 Volume ratio: {vr:.2f}x\n"
            f"🟢 Buy pressure: {buy_pressure:.1f}%\n"
            f"🏔 1440 Candle High: {resistance['level']:g}\n"
            f"🚀 Breakout: {br['breakout_pct']:+.2f}%\n"
            f"📊 Breakout volume: {br['volume_ratio']:.2f}x\n"
            f"🕯 Close position: {br['close_position']:.1f}%\n"
            f"📐 Spread: {spread:.3f}%\n"
            f"⭐ Score: {score}\n\n"
            f"🛑 Stop: ~{level_stop(resistance['level']):g}\n"
            f"🎯 TP1: +{TP1}%\n"
            f"🎯 TP2: +{TP2}%\n"
            f"🎯 TP3: +{TP3}%\n\n"
            f"🕐 Cooldown: 24H\n"
            f"⚠️ ALERT ONLY\n"
            f"NO AUTOMATIC ORDER"
        )

    except Exception:
        return None


def level_stop(resistance):
    return resistance * (1.0 - STOP_BELOW_RESISTANCE_PERCENT / 100.0)


def binance_scan_loop():
    while not STOP_EVENT.is_set():
        try:
            symbols = list(BINANCE_SYMBOLS)

            status = {
                "history": 0,
                "24h": 0,
                "momentum": 0,
                "volume": 0,
                "buy": 0,
                "spread": 0,
                "resistance": 0,
                "breakout": 0,
                "breakout_volume": 0,
                "candle": 0,
                "score": 0,
            }

            checked = 0
            signals = 0

            with ThreadPoolExecutor(max_workers=BINANCE_MAX_WORKERS) as ex:
                futures = {
                    ex.submit(analyze_binance, s, status): s
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
                "BINANCE STATUS | "
                f"Symbols: {len(symbols)} | "
                f"Checked: {checked} | "
                f"1440 History: {status['history']} | "
                f"24H Volume: {status['24h']} | "
                f"Momentum: {status['momentum']} | "
                f"Volume: {status['volume']} | "
                f"Buy Pressure: {status['buy']} | "
                f"Spread: {status['spread']} | "
                f"1440 Resistance: {status['resistance']} | "
                f"Breakout +1%: {status['breakout']} | "
                f"Breakout Volume: {status['breakout_volume']} | "
                f"Candle: {status['candle']} | "
                f"Score: {status['score']} | "
                f"Signals: {signals}"
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
    pairs = {}

    for q in DEX_SEARCH_QUERIES:
        try:
            r = SESSION.get(
                DEX_BASE + "/latest/dex/search",
                params={"q": q},
                timeout=15,
            )

            if not r.ok:
                print(f"DexScreener HTTP {r.status_code} for q={q}: {r.text[:200]}")
                continue

            data = r.json()
            for p in data.get("pairs", []):
                if p.get("chainId") != "solana":
                    continue
                address = p.get("pairAddress")
                if address:
                    pairs[address] = p

        except Exception as e:
            print(f"DexScreener error q={q}: {repr(e)}")

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


def solana_analyze(pair, status):
    try:
        address = pair.get("pairAddress")
        if not address:
            return None

        base = pair.get("baseToken") or {}
        symbol = base.get("symbol") or "UNKNOWN"

        age = pair_age_days(pair)
        if age is None or age <= SOLANA_MIN_AGE_DAYS:
            return None
        status["age"] += 1

        mcap = safe_float(pair.get("marketCap") or pair.get("fdv"))
        if mcap < SOLANA_MIN_MCAP or mcap > SOLANA_MAX_MCAP:
            return None
        status["mcap"] += 1

        liquidity = safe_float((pair.get("liquidity") or {}).get("usd"))
        if liquidity < SOLANA_MIN_LIQUIDITY:
            return None
        status["liquidity"] += 1

        stats = get_5m_stats(pair)
        momentum = stats["momentum"]
        volume = stats["volume"]
        buys = stats["buys"]
        sells = stats["sells"]

        if momentum < SOLANA_MIN_MOMENTUM or momentum > SOLANA_MAX_MOMENTUM:
            return None
        status["momentum"] += 1

        if volume < SOLANA_MIN_5M_VOLUME:
            return None
        status["volume"] += 1

        if SOLANA_REQUIRE_BUYS_GT_SELLS and buys <= sells:
            return None
        status["buys"] += 1

        previous = SOLANA_PREVIOUS.get(address)

        if previous is None:
            SOLANA_PREVIOUS[address] = {
                "momentum": momentum,
                "volume": volume,
                "liquidity": liquidity,
                "buys": buys,
                "sells": sells,
                "time": now_ts(),
            }
            return None

        if momentum <= previous["momentum"]:
            return None
        status["momentum_increase"] += 1

        if volume <= previous["volume"]:
            return None
        status["volume_increase"] += 1

        # Liquidity increase and buys increase are intentionally NOT required.
        now = now_ts()
        last_alert = SOLANA_ALERTED.get(address, 0)

        if now - last_alert < SOLANA_COOLDOWN:
            SOLANA_PREVIOUS[address] = {
                "momentum": momentum,
                "volume": volume,
                "liquidity": liquidity,
                "buys": buys,
                "sells": sells,
                "time": now,
            }
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

        status["signals"] += 1

        url = pair.get("url") or f"https://dexscreener.com/solana/{address}"

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
            f"⚖️ Buys > Sells: YES\n"
            f"📈 Momentum increasing: YES\n"
            f"📊 Volume increasing: YES\n"
            f"🕐 Cooldown: 24H\n\n"
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

            status = {
                "age": 0,
                "mcap": 0,
                "liquidity": 0,
                "momentum": 0,
                "volume": 0,
                "buys": 0,
                "momentum_increase": 0,
                "volume_increase": 0,
                "signals": 0,
            }

            for pair in pairs:
                result = solana_analyze(pair, status)
                if result:
                    send_alert(result)

            print(
                "SOLANA DEXSCREENER | "
                f"Pairs: {len(pairs)} | "
                f"Age: {status['age']} | "
                f"MCap: {status['mcap']} | "
                f"Liquidity: {status['liquidity']} | "
                f"Momentum: {status['momentum']} | "
                f"Volume: {status['volume']} | "
                f"Buys>Sells: {status['buys']} | "
                f"Momentum Increase: {status['momentum_increase']} | "
                f"Volume Increase: {status['volume_increase']} | "
                f"Signals: {status['signals']}"
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
    print("  5M momentum + 1440-candle real breakout")
    print("  1440 CLOSED 5M candles")
    print("  Breakout: >= +1% above 1440 highest high")
    print("  Breakout volume: >= 1.5x")
    print("  Buy pressure: >= 55%")
    print("  24H volume: >= $1M")
    print("  Spread: <= 0.20%")
    print("  Same coin cooldown: 24H")
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

    # Immediate Telegram connectivity test.
    telegram_startup_test()

    load_binance_symbols()

    # Load 1440 CLOSED 5M candles before scanning.
    if BINANCE_SYMBOLS:
        load_binance_histories(BINANCE_SYMBOLS)

        # Split WebSocket streams into small chunks for stability.
        for i in range(0, len(BINANCE_SYMBOLS), WS_CHUNK_SIZE):
            chunk = BINANCE_SYMBOLS[i:i + WS_CHUNK_SIZE]
            threading.Thread(
                target=ws_worker,
                args=(chunk,),
                daemon=True,
            ).start()
            time.sleep(0.5)

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
