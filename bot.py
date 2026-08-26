
import os
import time
import json
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue, Empty

import requests
import websocket


# ============================================================
# UNIFIED ALERT BOT
# BINANCE SPOT 5M MOMENTUM + 1440 CANDLE REAL BREAKOUT
# +
# SOLANA DEXSCREENER 5M MOMENTUM
#
# ALERT ONLY - NO AUTOMATIC ORDER
# ============================================================


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

session = requests.Session()
session.headers.update({
    "User-Agent": "UnifiedMomentumAlertBot/1.0",
    "Accept": "application/json",
})

telegram_queue = Queue(maxsize=500)


# ============================================================
# AZERBAIJAN TIME
# ============================================================

AZ_TZ = ZoneInfo("Asia/Baku")
TRADING_START_HOUR = 7
TRADING_END_HOUR = 1


def is_trading_time():
    h = datetime.now(AZ_TZ).hour
    return not (1 <= h < 7)


def az_time():
    return datetime.now(AZ_TZ)


# ============================================================
# COMMON
# ============================================================

running = True


def safe_float(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except Exception:
        return default


def percent_change(a, b):
    if a <= 0:
        return 0.0
    return (b - a) / a * 100.0


def round_price(p):
    if p >= 1000:
        return round(p, 2)
    if p >= 1:
        return round(p, 4)
    if p >= 0.01:
        return round(p, 6)
    return round(p, 8)


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_now(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials missing.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        r = session.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            return True
        print("Telegram ERROR:", r.status_code, r.text[:300])
    except Exception as e:
        print("Telegram exception:", e)
    return False


def queue_telegram(message):
    try:
        telegram_queue.put_nowait(message)
    except Exception:
        print("Telegram queue full.")


def telegram_worker():
    while running:
        try:
            msg = telegram_queue.get(timeout=1)
        except Empty:
            continue
        try:
            send_telegram_now(msg)
        finally:
            telegram_queue.task_done()


# ============================================================
# ======================= BINANCE =============================
# ============================================================

BINANCE_REST = "https://api.binance.com"
BINANCE_WS = "wss://stream.binance.com:443"

BINANCE_INTERVAL = "5m"
BINANCE_HISTORY_LIMIT = 1440
BINANCE_AVERAGE_VOLUME_CANDLES = 20

BINANCE_MIN_24H_QUOTE_VOLUME = 1_000_000

BINANCE_MIN_PRICE_CHANGE = 1.0
BINANCE_MIN_BUY_PRESSURE = 55.0

BINANCE_BREAKOUT_LOOKBACK = 1440
BINANCE_MIN_BREAKOUT_PERCENT = 1.0
BINANCE_MIN_BREAKOUT_VOLUME_RATIO = 1.5

BINANCE_MIN_CLOSE_POSITION = 70.0
BINANCE_MAX_UPPER_WICK_PERCENT = 30.0

BINANCE_MIN_SIGNAL_SCORE = 60
BINANCE_STRONG_SIGNAL_SCORE = 75

BINANCE_MAX_SPREAD_PERCENT = 0.20
BINANCE_BOOK_CACHE_MAX_AGE = 10
BINANCE_REST_BOOK_TIMEOUT = 5
BINANCE_REST_BOOK_MIN_INTERVAL = 1.0

BINANCE_SIGNAL_COOLDOWN = 24 * 60 * 60

BINANCE_WS_CHUNK_SIZE = 50
BINANCE_RECONNECT_SECONDS = 5
BINANCE_STATUS_INTERVAL = 60

BINANCE_STOP_PERCENT = 0.50
BINANCE_TP1 = 3.0
BINANCE_TP2 = 5.0
BINANCE_TP3 = 8.0

binance_symbols = []
binance_volume_24h = {}
binance_history = {}
binance_live = {}
binance_books = {}
binance_last_signal = {}
binance_rest_book_last = {}

binance_lock = threading.RLock()
binance_signal_lock = threading.Lock()
binance_ws_connections = []
binance_ws_lock = threading.Lock()

binance_stats = {
    "checked": 0,
    "momentum": 0,
    "volume": 0,
    "buy_pressure": 0,
    "breakout": 0,
    "breakout_volume": 0,
    "candle_quality": 0,
    "spread": 0,
    "signals": 0,
}


def bstat(k):
    with binance_lock:
        if k in binance_stats:
            binance_stats[k] += 1


def load_binance_exchange_info():
    r = session.get(f"{BINANCE_REST}/api/v3/exchangeInfo", timeout=20)
    r.raise_for_status()

    out = []
    for x in r.json().get("symbols", []):
        if x.get("status") != "TRADING":
            continue
        if x.get("quoteAsset") != "USDT":
            continue
        if not x.get("isSpotTradingAllowed", False):
            continue

        base = x.get("baseAsset", "")
        if base.endswith(("UP", "DOWN", "BULL", "BEAR")):
            continue

        out.append(x["symbol"].lower())

    return out


def load_binance_volumes(all_symbols):
    r = session.get(f"{BINANCE_REST}/api/v3/ticker/24hr", timeout=30)
    r.raise_for_status()

    allowed = set(all_symbols)
    out = {}

    for x in r.json():
        s = x.get("symbol", "").lower()
        if s not in allowed:
            continue

        qv = safe_float(x.get("quoteVolume"))
        if qv >= BINANCE_MIN_24H_QUOTE_VOLUME:
            out[s] = qv

    return out


def load_binance_history_one(symbol):
    try:
        r = session.get(
            f"{BINANCE_REST}/api/v3/klines",
            params={
                "symbol": symbol.upper(),
                "interval": BINANCE_INTERVAL,
                "limit": BINANCE_HISTORY_LIMIT,
            },
            timeout=20,
        )
        r.raise_for_status()

        now_ms = int(time.time() * 1000)
        d = deque(maxlen=BINANCE_HISTORY_LIMIT)

        for k in r.json():
            if int(k[6]) >= now_ms:
                continue

            d.append({
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

        return symbol, d
    except Exception as e:
        print("Binance history error", symbol, e)
        return symbol, None


def load_binance_histories():
    with ThreadPoolExecutor(max_workers=15) as ex:
        futures = [ex.submit(load_binance_history_one, s)
                   for s in binance_symbols]

        for f in as_completed(futures):
            s, d = f.result()
            if d:
                with binance_lock:
                    binance_history[s] = d


def binance_resistance(symbol):
    with binance_lock:
        h = list(binance_history.get(symbol, []))

    if len(h) < BINANCE_BREAKOUT_LOOKBACK:
        return None

    candles = h[-BINANCE_BREAKOUT_LOOKBACK:]
    highest = max(candles, key=lambda x: x["high"])

    return {
        "price": highest["high"],
        "open_time": highest["open_time"],
        "candle": highest,
        "age": len(candles) - 1 - candles.index(highest),
    }


def b_momentum_score(x):
    if x >= 5: return 25
    if x >= 4: return 20
    if x >= 3: return 15
    if x >= 2: return 10
    if x >= 1: return 5
    return 0


def b_volume_score(r):
    if r >= 4: return 20
    if r >= 3: return 17
    if r >= 2: return 14
    if r >= 1.5: return 8
    if r >= 1.2: return 4
    return 0


def b_buy_score(p):
    if p >= 65: return 20
    if p >= 60: return 15
    if p >= 55: return 10
    if p >= 50: return 5
    return 0


def b_breakout_score(price, resistance):
    if resistance <= 0:
        return 0, 0.0
    x = (price - resistance) / resistance * 100
    if x >= 2: return 15, x
    if x >= 1.5: return 12, x
    if x >= 1: return 10, x
    return 0, x


def b_breakout_volume_score(current, average):
    if average <= 0:
        return 0, 0.0
    r = current / average
    if r >= 4: return 10, r
    if r >= 3: return 8, r
    if r >= 2: return 6, r
    if r >= 1.5: return 4, r
    return 0, r


def b_candle_quality(c):
    o, h, l, close = c["open"], c["high"], c["low"], c["close"]
    if h <= l or close <= o:
        return 0, 0, 0

    rng = h - l
    cp = (close - l) / rng * 100
    wick = (h - max(o, close)) / rng * 100

    if cp < BINANCE_MIN_CLOSE_POSITION:
        return 0, cp, wick
    if wick > BINANCE_MAX_UPPER_WICK_PERCENT:
        return 0, cp, wick

    return 10, cp, wick


def binance_store_book(symbol, bid, ask):
    if bid <= 0 or ask <= 0:
        return

    with binance_lock:
        binance_books[symbol] = {
            "bid": bid,
            "ask": ask,
            "timestamp": time.time(),
        }


def binance_get_spread(symbol):
    with binance_lock:
        d = binance_books.get(symbol)

    if d and time.time() - d["timestamp"] <= BINANCE_BOOK_CACHE_MAX_AGE:
        mid = (d["bid"] + d["ask"]) / 2
        return (d["ask"] - d["bid"]) / mid * 100

    now = time.time()
    last = binance_rest_book_last.get(symbol, 0)
    if now - last < BINANCE_REST_BOOK_MIN_INTERVAL:
        return None

    binance_rest_book_last[symbol] = now

    try:
        r = session.get(
            f"{BINANCE_REST}/api/v3/ticker/bookTicker",
            params={"symbol": symbol.upper()},
            timeout=BINANCE_REST_BOOK_TIMEOUT,
        )
        if r.status_code != 200:
            return None

        x = r.json()
        bid = safe_float(x.get("bidPrice"))
        ask = safe_float(x.get("askPrice"))
        if bid <= 0 or ask <= 0:
            return None

        binance_store_book(symbol, bid, ask)
        mid = (bid + ask) / 2
        return (ask - bid) / mid * 100
    except Exception:
        return None


def analyze_binance(symbol):
    if not is_trading_time():
        return None

    bstat("checked")

    with binance_lock:
        c = binance_live.get(symbol)
        h = list(binance_history.get(symbol, []))
        qv = binance_volume_24h.get(symbol, 0)

    if not c or len(h) < BINANCE_BREAKOUT_LOOKBACK:
        return None
    if qv < BINANCE_MIN_24H_QUOTE_VOLUME:
        return None

    change = percent_change(c["open"], c["close"])
    if change < BINANCE_MIN_PRICE_CHANGE:
        return None
    bstat("momentum")

    prev = h[-BINANCE_AVERAGE_VOLUME_CANDLES:]
    vols = [x["quote_volume"] for x in prev if x["quote_volume"] > 0]
    if not vols:
        return None

    avg = sum(vols) / len(vols)
    current_vol = c["quote_volume"]
    ratio = current_vol / avg if avg else 0

    if ratio < 1.2:
        return None
    bstat("volume")

    buy = c["taker_buy_quote"] / current_vol * 100 if current_vol > 0 else 0
    if buy < BINANCE_MIN_BUY_PRESSURE:
        return None
    bstat("buy_pressure")

    res = binance_resistance(symbol)
    if not res:
        return None

    bp, breakout = b_breakout_score(c["close"], res["price"])
    if breakout < BINANCE_MIN_BREAKOUT_PERCENT:
        return None
    bstat("breakout")

    bvp, bvr = b_breakout_volume_score(current_vol, avg)
    if bvr < BINANCE_MIN_BREAKOUT_VOLUME_RATIO:
        return None
    bstat("breakout_volume")

    cp, close_pos, wick = b_candle_quality(c)
    if cp <= 0:
        return None
    bstat("candle_quality")

    spread = binance_get_spread(symbol)
    if spread is None or spread > BINANCE_MAX_SPREAD_PERCENT:
        return None
    bstat("spread")

    score = (
        b_momentum_score(change)
        + min(b_volume_score(ratio), 20)
        + b_buy_score(buy)
        + min(bp, 15)
        + min(bvp, 10)
        + cp
    )

    if score < BINANCE_MIN_SIGNAL_SCORE:
        return None

    now = time.time()
    with binance_signal_lock:
        last = binance_last_signal.get(symbol, 0)
        if now - last < BINANCE_SIGNAL_COOLDOWN:
            return None
        binance_last_signal[symbol] = now

    bstat("signals")

    entry = c["close"]
    stop = res["price"] * (1 - BINANCE_STOP_PERCENT / 100)
    tp1 = entry * 1.03
    tp2 = entry * 1.05
    tp3 = entry * 1.08

    return {
        "symbol": symbol.upper(),
        "price": entry,
        "change": change,
        "current_vol": current_vol,
        "avg_vol": avg,
        "ratio": ratio,
        "buy": buy,
        "resistance": res["price"],
        "breakout": breakout,
        "bvr": bvr,
        "spread": spread,
        "close_pos": close_pos,
        "wick": wick,
        "score": score,
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
    }


def format_binance_signal(s):
    status = "🔥 STRONG BUY" if s["score"] >= BINANCE_STRONG_SIGNAL_SCORE else "🟢 BUY"

    return f"""
{status}

<b>🚀 BINANCE SPOT 5M REAL BREAKOUT</b>

🪙 <b>{s["symbol"]}</b>
💰 Price: <b>{round_price(s["price"])}</b>
📈 5M Momentum: <b>+{s["change"]:.2f}%</b>

🏔 1440 Candle High:
<b>{round_price(s["resistance"])}</b>

🚀 Breakout:
<b>+{s["breakout"]:.2f}%</b>

🔥 Breakout Volume:
<b>{s["bvr"]:.2f}×</b>

🟢 Buy Pressure:
<b>{s["buy"]:.1f}%</b>

📊 Current Volume:
<b>${s["current_vol"]:,.0f}</b>

📊 Volume vs Average:
<b>{s["ratio"]:.2f}×</b>

📖 Spread:
<b>{s["spread"]:.3f}%</b>

🏆 Score:
<b>{s["score"]}/100</b>

🎯 Entry: <b>{round_price(s["entry"])}</b>
🛑 Stop: <b>{round_price(s["stop"])}</b>
🎯 TP1: <b>{round_price(s["tp1"])}</b> +3%
🎯 TP2: <b>{round_price(s["tp2"])}</b> +5%
🎯 TP3: <b>{round_price(s["tp3"])}</b> +8%

🕐 Cooldown: <b>24 HOURS</b>

⚠️ <b>ALERT ONLY</b>
No automatic order.
"""


def process_binance_kline(symbol, k):
    if not is_trading_time():
        return

    c = {
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

    with binance_lock:
        binance_live[symbol] = c

    if c["closed"]:
        with binance_lock:
            h = binance_history.get(symbol)
            if h is None:
                h = deque(maxlen=BINANCE_HISTORY_LIMIT)
                binance_history[symbol] = h

            if not h or h[-1]["open_time"] != c["open_time"]:
                h.append(c)

            binance_live.pop(symbol, None)
        return

    s = analyze_binance(symbol)
    if s:
        msg = format_binance_signal(s)
        print(msg)
        queue_telegram(msg)


def make_binance_ws_url(chunk, book=False):
    if book:
        streams = [f"{s}@bookTicker" for s in chunk]
    else:
        streams = [f"{s}@kline_5m" for s in chunk]
    return f"{BINANCE_WS}/stream?streams={'/'.join(streams)}"


def binance_kline_worker(chunk):
    url = make_binance_ws_url(chunk, False)

    while running:
        if not is_trading_time():
            time.sleep(30)
            continue

        try:
            def on_message(ws, message):
                try:
                    d = json.loads(message).get("data", {})
                    if d.get("e") != "kline":
                        return
                    process_binance_kline(d["s"].lower(), d["k"])
                except Exception as e:
                    print("Binance kline message:", e)

            def on_error(ws, error):
                print("Binance KLINE WS ERROR:", error)

            def on_close(ws, code, reason):
                print("Binance KLINE WS CLOSED:", code, reason)

            ws = websocket.WebSocketApp(
                url,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )

            with binance_ws_lock:
                binance_ws_connections.append(ws)

            ws.run_forever(
                ping_interval=20,
                ping_timeout=10,
                origin="https://www.binance.com",
            )

        except Exception as e:
            print("Binance KLINE exception:", e)

        time.sleep(BINANCE_RECONNECT_SECONDS)


def binance_book_worker(chunk):
    url = make_binance_ws_url(chunk, True)

    while running:
        if not is_trading_time():
            time.sleep(30)
            continue

        try:
            def on_message(ws, message):
                try:
                    d = json.loads(message).get("data", {})
                    s = d.get("s")
                    if not s:
                        return
                    s = s.lower()
                    bid = safe_float(d.get("b"))
                    ask = safe_float(d.get("a"))
                    if bid > 0 and ask > 0:
                        binance_store_book(s, bid, ask)
                except Exception as e:
                    print("Binance book message:", e)

            def on_error(ws, error):
                print("Binance BOOK WS ERROR:", error)

            def on_close(ws, code, reason):
                print("Binance BOOK WS CLOSED:", code, reason)

            ws = websocket.WebSocketApp(
                url,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )

            with binance_ws_lock:
                binance_ws_connections.append(ws)

            ws.run_forever(
                ping_interval=20,
                ping_timeout=10,
                origin="https://www.binance.com",
            )

        except Exception as e:
            print("Binance BOOK exception:", e)

        time.sleep(BINANCE_RECONNECT_SECONDS)


def binance_volume_refresh():
    global binance_symbols, binance_volume_24h

    while running:
        time.sleep(30 * 60)
        if not is_trading_time():
            continue

        try:
            all_s = load_binance_exchange_info()
            new_v = load_binance_volumes(all_s)
            with binance_lock:
                binance_volume_24h = new_v
                binance_symbols = list(new_v.keys())
            print("Binance 24H volume refreshed:", len(new_v))
        except Exception as e:
            print("Binance refresh error:", e)


def start_binance():
    global binance_symbols, binance_volume_24h

    print("Loading Binance Spot...")
    all_s = load_binance_exchange_info()
    binance_volume_24h = load_binance_volumes(all_s)
    binance_symbols = list(binance_volume_24h.keys())

    print("Binance final symbols:", len(binance_symbols))

    load_binance_histories()
    print("Binance history ready:", len(binance_history))

    threading.Thread(target=binance_volume_refresh, daemon=True).start()

    chunks = [
        binance_symbols[i:i + BINANCE_WS_CHUNK_SIZE]
        for i in range(0, len(binance_symbols), BINANCE_WS_CHUNK_SIZE)
    ]

    for chunk in chunks:
        threading.Thread(
            target=binance_kline_worker,
            args=(chunk,),
            daemon=True,
        ).start()

        threading.Thread(
            target=binance_book_worker,
            args=(chunk,),
            daemon=True,
        ).start()

        time.sleep(1)


# ============================================================
# ======================== SOLANA =============================
# DEXSCREENER 5M MOMENTUM
# ============================================================

DEX_BASE = "https://api.dexscreener.com"

SOLANA_MIN_AGE_DAYS = 20
SOLANA_MIN_MCAP = 7_000
SOLANA_MAX_MCAP = 100_000

SOLANA_MIN_MOMENTUM = 1.0
SOLANA_MAX_MOMENTUM = 50.0

SOLANA_SCAN_SECONDS = 30
SOLANA_COOLDOWN = 24 * 60 * 60

# Minimum activity safeguards.
SOLANA_MIN_5M_VOLUME = 100
SOLANA_MIN_LIQUIDITY = 7_000

# Require the current snapshot to improve against the previous
# snapshot for the same pair.
SOLANA_REQUIRE_PRICE_INCREASE = True
SOLANA_REQUIRE_VOLUME_INCREASE = True
SOLANA_REQUIRE_LIQUIDITY_INCREASE = True
SOLANA_REQUIRE_BUYS_INCREASE = True

SOLANA_MAX_PAIRS_PER_SCAN = 80

solana_pairs = {}
solana_last_snapshot = {}
solana_last_signal = {}
solana_seen = set()
solana_lock = threading.RLock()

solana_stats = {
    "seen": 0,
    "age": 0,
    "mcap": 0,
    "momentum": 0,
    "volume": 0,
    "liquidity": 0,
    "buys": 0,
    "signals": 0,
}


def sstat(k):
    with solana_lock:
        if k in solana_stats:
            solana_stats[k] += 1


def dex_get(url, params=None):
    try:
        r = session.get(url, params=params, timeout=15)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception as e:
        print("Dexscreener request error:", e)
        return None


def solana_discover_pairs():
    """
    Dexscreener does not expose a public endpoint that continuously lists
    every Solana pair. We therefore use the public search endpoint with
    several Solana-relevant search terms and merge unique pairs.

    This is discovery, not a claim that every Solana token is guaranteed
    to be returned by one scan.
    """
    queries = [
        "SOL", "USDC", "USDT", "WIF", "BONK",
        "JUP", "RAY", "PUMP", "MEME", "AI",
    ]

    found = {}

    for q in queries:
        data = dex_get(
            f"{DEX_BASE}/latest/dex/search",
            params={"q": q},
        )

        if not data:
            continue

        for p in data.get("pairs", []) or []:
            if p.get("chainId") != "solana":
                continue

            pair_addr = p.get("pairAddress")
            if not pair_addr:
                continue

            found[pair_addr] = p

            if len(found) >= SOLANA_MAX_PAIRS_PER_SCAN:
                return list(found.values())

    return list(found.values())


def solana_pair_age_days(pair):
    created = pair.get("pairCreatedAt")
    if not created:
        return None

    try:
        return (time.time() - float(created) / 1000) / 86400
    except Exception:
        return None


def solana_mcap(pair):
    # Dexscreener may provide marketCap; fdv is used only as fallback.
    mcap = safe_float(pair.get("marketCap"))
    if mcap <= 0:
        mcap = safe_float(pair.get("fdv"))
    return mcap


def solana_snapshot(pair):
    tx = (pair.get("txns") or {}).get("m5") or {}
    vol = (pair.get("volume") or {}).get("m5")
    chg = (pair.get("priceChange") or {}).get("m5")
    liq = (pair.get("liquidity") or {}).get("usd")

    buys = safe_float(tx.get("buys"))
    sells = safe_float(tx.get("sells"))

    return {
        "price_change": safe_float(chg),
        "volume": safe_float(vol),
        "liquidity": safe_float(liq),
        "buys": buys,
        "sells": sells,
        "timestamp": time.time(),
    }


def solana_analyze_pair(pair):
    pair_addr = pair.get("pairAddress")
    if not pair_addr:
        return None

    base = pair.get("baseToken") or {}
    symbol = base.get("symbol") or "UNKNOWN"

    age = solana_pair_age_days(pair)
    if age is None or age <= SOLANA_MIN_AGE_DAYS:
        return None
    sstat("age")

    mcap = solana_mcap(pair)
    if not (SOLANA_MIN_MCAP <= mcap <= SOLANA_MAX_MCAP):
        return None
    sstat("mcap")

    snap = solana_snapshot(pair)

    # Never alert on a pair's first observation because there is no
    # previous 5M snapshot to prove that momentum/activity is increasing.
    with solana_lock:
        previous = solana_last_snapshot.get(pair_addr)
        solana_last_snapshot[pair_addr] = snap

    if not previous:
        return None

    momentum = snap["price_change"]

    # Requested ceiling: once the current 5M move is above +50%,
    # do not send a signal.
    if momentum < SOLANA_MIN_MOMENTUM:
        return None

    if momentum > SOLANA_MAX_MOMENTUM:
        return None

    # Momentum itself must be increasing versus the previous snapshot.
    if SOLANA_REQUIRE_PRICE_INCREASE and (
        momentum <= previous["price_change"]
    ):
        return None

    sstat("momentum")

    if snap["volume"] < SOLANA_MIN_5M_VOLUME:
        return None

    if SOLANA_REQUIRE_VOLUME_INCREASE and (
        snap["volume"] <= previous["volume"]
    ):
        return None

    sstat("volume")

    if snap["liquidity"] < SOLANA_MIN_LIQUIDITY:
        return None

    if SOLANA_REQUIRE_LIQUIDITY_INCREASE and (
        snap["liquidity"] <= previous["liquidity"]
    ):
        return None

    sstat("liquidity")

    if SOLANA_REQUIRE_BUYS_INCREASE and (
        snap["buys"] <= previous["buys"]
    ):
        return None

    if snap["buys"] <= snap["sells"]:
        return None

    sstat("buys")

    now = time.time()
    with solana_lock:
        last = solana_last_signal.get(pair_addr, 0)
        if now - last < SOLANA_COOLDOWN:
            return None

        solana_last_signal[pair_addr] = now

    sstat("signals")

    ratio = snap["buys"] / max(snap["sells"], 1)

    return {
        "symbol": symbol,
        "pair": pair_addr,
        "url": pair.get("url", ""),
        "age": age,
        "mcap": mcap,
        "price": safe_float(pair.get("priceUsd")),
        "momentum": momentum,
        "volume": snap["volume"],
        "liquidity": snap["liquidity"],
        "buys": snap["buys"],
        "sells": snap["sells"],
        "buy_sell": ratio,
    }


def format_solana_signal(s):
    return f"""
🚀 <b>SOLANA MEME MOMENTUM</b>

🪙 <b>${s["symbol"]}</b>

📈 5M Momentum:
<b>+{s["momentum"]:.2f}%</b>

💰 Price:
<b>{s["price"]:.12g}</b>

💎 Market Cap:
<b>${s["mcap"]:,.0f}</b>

💧 Liquidity:
<b>${s["liquidity"]:,.0f}</b>

📊 5M Volume:
<b>${s["volume"]:,.0f}</b>

🟢 5M Buys:
<b>{int(s["buys"])}</b>

🔴 5M Sells:
<b>{int(s["sells"])}</b>

🟢 Buy/Sell:
<b>{s["buy_sell"]:.2f}×</b>

📅 Pair Age:
<b>{s["age"]:.1f} days</b>

🔥 <b>Momentum + Volume + Liquidity + Buys are increasing</b>

🕐 Signal cooldown:
<b>24 HOURS</b>

⚠️ <b>ALERT ONLY</b>
No automatic order.
"""


def solana_scanner_worker():
    print("Solana Dexscreener scanner started.")

    while running:
        if not is_trading_time():
            time.sleep(30)
            continue

        try:
            pairs = solana_discover_pairs()

            with solana_lock:
                for p in pairs:
                    solana_pairs[p["pairAddress"]] = p

            sstat("seen")

            # Analyze all currently cached pairs, not just the newly
            # discovered ones, so a pair can be compared across scans.
            with solana_lock:
                cached = list(solana_pairs.values())

            for pair in cached:
                signal = solana_analyze_pair(pair)
                if signal:
                    msg = format_solana_signal(signal)
                    print(msg)
                    queue_telegram(msg)

        except Exception as e:
            print("Solana scanner error:", e)

        time.sleep(SOLANA_SCAN_SECONDS)


# ============================================================
# STATUS
# ============================================================

def status_worker():
    while running:
        time.sleep(60)

        with binance_lock:
            bs = dict(binance_stats)
            bh = len(binance_history)
            bl = len(binance_live)
            bb = len(binance_books)
            bn = len(binance_symbols)

        with solana_lock:
            ss = dict(solana_stats)
            sp = len(solana_pairs)

        print("\n================ STATUS ================")
        print("Azərbaycan vaxtı:", az_time().strftime("%Y-%m-%d %H:%M:%S"))
        print("Trading:", "ACTIVE" if is_trading_time() else "SLEEP")

        print("\nBINANCE")
        print("Symbols:", bn)
        print("Histories:", bh)
        print("Live:", bl)
        print("Books:", bb)
        print("Checked:", bs["checked"])
        print("Momentum:", bs["momentum"])
        print("Volume:", bs["volume"])
        print("Buy pressure:", bs["buy_pressure"])
        print("Breakout:", bs["breakout"])
        print("Breakout volume:", bs["breakout_volume"])
        print("Candle quality:", bs["candle_quality"])
        print("Spread:", bs["spread"])
        print("Signals:", bs["signals"])

        print("\nSOLANA DEXSCREENER")
        print("Cached pairs:", sp)
        print("Seen:", ss["seen"])
        print("Age passed:", ss["age"])
        print("MCap passed:", ss["mcap"])
        print("Momentum passed:", ss["momentum"])
        print("Volume passed:", ss["volume"])
        print("Liquidity passed:", ss["liquidity"])
        print("Buys passed:", ss["buys"])
        print("Signals:", ss["signals"])
        print("========================================\n")

        with binance_lock:
            for k in binance_stats:
                binance_stats[k] = 0

        with solana_lock:
            for k in solana_stats:
                solana_stats[k] = 0


# ============================================================
# SLEEP / WS CLEANUP
# ============================================================

def sleep_manager():
    previous = is_trading_time()

    while running:
        active = is_trading_time()

        if previous and not active:
            print("🌙 01:00 AZ - sleep mode.")

            with binance_lock:
                binance_live.clear()
                binance_books.clear()

        if not previous and active:
            print("🌅 07:00 AZ - active mode.")

        previous = active
        time.sleep(10)


# ============================================================
# MAIN
# ============================================================

def main():
    global running

    print("""
============================================================
                 UNIFIED ALERT BOT
============================================================

🔵 BINANCE
5M Momentum + 1440 Closed Candle Real Breakout

🟣 SOLANA
Dexscreener 5M Momentum Scanner

SOLANA CONDITIONS
-----------------
Token age: >20 days
Market Cap: $7,000 - $100,000
5M momentum: +1% to +50%
Momentum must be increasing
5M volume must be increasing
Liquidity must be increasing
5M buys must be increasing
Buys > sells
Minimum liquidity: $3,000
Minimum 5M volume: $100
Same token cooldown: 24H

⚠️ ALERT ONLY
NO AUTOMATIC ORDER

🌙 Sleep: 01:00 - 07:00 Azerbaijan time
============================================================
""")

    threading.Thread(
        target=telegram_worker,
        daemon=True,
    ).start()

    threading.Thread(
        target=sleep_manager,
        daemon=True,
    ).start()

    threading.Thread(
        target=status_worker,
        daemon=True,
    ).start()

    queue_telegram(
        "🟢 <b>UNIFIED ALERT BOT STARTED</b>\n\n"
        "🔵 Binance Spot 5M Breakout: ACTIVE\n"
        "🟣 Solana Dexscreener 5M Momentum: ACTIVE\n\n"
        "🟣 Solana:\n"
        "• Age >20 days\n"
        "• MCap $7K-$100K\n"
        "• Momentum +1% to +50%\n"
        "• Volume increasing\n"
        "• Liquidity increasing\n"
        "• Buys increasing\n"
        "• 24H cooldown\n\n"
        "⚠️ ALERT ONLY"
    )

    # Binance and Solana run independently.
    threading.Thread(
        target=start_binance,
        daemon=True,
    ).start()

    threading.Thread(
        target=solana_scanner_worker,
        daemon=True,
    ).start()

    while running:
        time.sleep(5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        running = False
        print("Bot stopped.")
