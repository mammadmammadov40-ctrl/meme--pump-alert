import os
import time
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from threading import Lock

# ============================================================
# BINANCE DIB1 + HIGH1 + DIB2 + BREAKOUT SCANNER
# TELEGRAM:
#   - 1h: DIB1 CONFIRMED + BREAKOUT
#   - Digər TF: yalnız BREAKOUT
# ============================================================

BINANCE_URL = "https://api.binance.com"
TELEGRAM_URL = "https://api.telegram.org/bot{}/sendMessage"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TOP_COINS = 100
TIMEFRAMES = ["5m", "15m", "30m", "1h"]

# DIB1 CONFIRMED alerti yalnız bu TF-lər üçün Telegram-a gedir
DIB1_ALERT_TIMEFRAMES = {"1h"}

POLL_SECONDS = 10
TOP_REFRESH_SECONDS = 60

ROLLING_CANDLES = 100
MIN_CANDLES_TO_HIGH1 = 10
MIN_CANDLES_TO_BREAKOUT = 10
BREAKOUT_PERCENT = 1.01

FETCH_LIMIT = 250
REQUEST_TIMEOUT = 10
AZ_TZ = ZoneInfo("Asia/Baku")

EXCLUDED_BASES = {"UP", "DOWN", "BULL", "BEAR"}

session = requests.Session()
state_lock = Lock()

states = {}
last_processed = {}
initialized = {}


# ============================================================
# TIME / TELEGRAM
# ============================================================

def now_az():
    return datetime.now(AZ_TZ)


def format_time(ms):
    return datetime.fromtimestamp(ms / 1000, AZ_TZ).strftime("%Y-%m-%d %H:%M:%S")


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram ENV yoxdur.")
        return
    url = TELEGRAM_URL.format(TELEGRAM_BOT_TOKEN)
    try:
        r = session.post(url,
                         json={"chat_id": TELEGRAM_CHAT_ID, "text": message},
                         timeout=REQUEST_TIMEOUT)
        if not r.ok:
            print("Telegram error:", r.text)
    except Exception as e:
        print("Telegram exception:", e)


# ============================================================
# TOP 100
# ============================================================

def get_top_symbols():
    try:
        info = session.get(BINANCE_URL + "/api/v3/exchangeInfo",
                           timeout=REQUEST_TIMEOUT).json()
        valid = {}
        for s in info["symbols"]:
            if s["status"] != "TRADING": continue
            if s["quoteAsset"] != "USDT": continue
            if s["isSpotTradingAllowed"] is not True: continue
            base = s["baseAsset"].upper()
            if any(base.endswith(x) for x in EXCLUDED_BASES): continue
            valid[s["symbol"]] = True

        tickers = session.get(BINANCE_URL + "/api/v3/ticker/24hr",
                              timeout=REQUEST_TIMEOUT).json()
        result = []
        for t in tickers:
            sym = t["symbol"]
            if sym not in valid: continue
            try:
                vol = float(t["quoteVolume"])
            except:
                continue
            result.append((sym, vol))
        result.sort(key=lambda x: x[1], reverse=True)
        return [x[0] for x in result[:TOP_COINS]]
    except Exception as e:
        print("Symbol error:", e)
        return []


# ============================================================
# CLOSED CANDLES
# ============================================================

def get_closed_candles(symbol, interval, limit=FETCH_LIMIT):
    try:
        r = session.get(BINANCE_URL + "/api/v3/klines",
                        params={"symbol": symbol, "interval": interval, "limit": limit},
                        timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        now_ms = int(time.time() * 1000)
        candles = []
        for k in r.json():
            ct = int(k[6])
            if ct > now_ms: continue
            candles.append({
                "open_time": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "close_time": ct,
            })
        candles.sort(key=lambda x: x["close_time"])
        return candles
    except Exception as e:
        print(f"{symbol} {interval} candle error:", e)
        return []


# ============================================================
# CREATE DIB1
# ============================================================

def create_dib1(symbol, timeframe, candle, reason=""):
    states[(symbol, timeframe)] = {
        "phase": "dib1_watch",

        "dib1": candle["low"],
        "dib1_time": candle["close_time"],
        "candles_after_dib1": 0,
        "high1": None,
        "high1_time": None,
        "high1_candle_number": None,

        "dib2": None,
        "dib2_time": None,
        "candles_after_dib2": 0,
        "dib2_break_count": 0,
        "high_since_dib2": None,
        "high_since_dib2_time": None,
        "high_since_dib2_candle_num": None,
    }
    print(f"[{format_time(candle['close_time'])}] {symbol} {timeframe} | "
          f"NEW DIB1 = {candle['low']} | {reason}")


# ============================================================
# CONFIRM DIB1 → KEÇ DIB2 MƏRHƏLƏSİNƏ
# Telegram alert: YALNIZ 1h üçün
# ============================================================

def confirm_dib1_and_start_dib2(symbol, timeframe, state, breaking_candle):

    msg = (
        f"🔔 DIB1 CONFIRMED\n\n"
        f"Coin: {symbol}\nTF: {timeframe}\n\n"
        f"DIB1: {state['dib1']}\n"
        f"DIB1 time: {format_time(state['dib1_time'])}\n\n"
        f"HIGH1: {state['high1']}\n"
        f"HIGH1 time: {format_time(state['high1_time'])}\n\n"
        f"DIB1 → HIGH1: {state['high1_candle_number']} candles\n"
        f"HIGH1 → DIB1 break: "
        f"{state['candles_after_dib1'] - state['high1_candle_number']} candles\n\n"
        f"DIB1 break LOW: {breaking_candle['low']}\n"
        f"Break time: {format_time(breaking_candle['close_time'])}\n\n"
        f"⏳ DIB2 izləməyə başlanılır..."
    )

    # Hər halda terminala yaz
    print("\n" + msg + "\n")

    # YALNIZ seçilmiş timeframe-lər üçün Telegram-a göndər
    if timeframe in DIB1_ALERT_TIMEFRAMES:
        send_telegram(msg)

    # Keç DIB2 mərhələsinə
    state["phase"] = "dib2_watch"
    state["dib2"] = breaking_candle["low"]
    state["dib2_time"] = breaking_candle["close_time"]
    state["candles_after_dib2"] = 0
    state["dib2_break_count"] = 0
    state["high_since_dib2"] = None
    state["high_since_dib2_time"] = None
    state["high_since_dib2_candle_num"] = None


# ============================================================
# BREAKOUT SIGNAL — HƏR TF ÜÇÜN TELEGRAMA GEDİR
# ============================================================

def send_breakout_alert(symbol, timeframe, state, breakout_candle):

    target = state["high1"] * BREAKOUT_PERCENT

    msg = (
        f"🚀 BREAKOUT CONFIRMED\n\n"
        f"Coin: {symbol}\nTF: {timeframe}\n\n"
        f"DIB1: {state['dib1']}\n"
        f"DIB1 time: {format_time(state['dib1_time'])}\n\n"
        f"DIB2: {state['dib2']}\n"
        f"DIB2 time: {format_time(state['dib2_time'])}\n\n"
        f"HIGH1: {state['high1']}\n"
        f"HIGH1 time: {format_time(state['high1_time'])}\n\n"
        f"Breakout target (HIGH1 × 1.01): {target:.8f}\n"
        f"Breakout close: {breakout_candle['close']}\n"
        f"Breakout time: {format_time(breakout_candle['close_time'])}\n\n"
        f"DIB2 → Breakout: {state['candles_after_dib2']} candles"
    )
    print("\n" + msg + "\n")
    send_telegram(msg)


# ============================================================
# DIB2 MƏRHƏLƏSİ EMALI
# ============================================================

def process_dib2_phase(symbol, timeframe, state, candle):

    state["candles_after_dib2"] += 1
    n = state["candles_after_dib2"]

    if (state["high_since_dib2"] is None
        or candle["high"] > state["high_since_dib2"]):
        state["high_since_dib2"] = candle["high"]
        state["high_since_dib2_time"] = candle["close_time"]
        state["high_since_dib2_candle_num"] = n

    # BREAKOUT
    if candle["close"] >= state["high1"] * BREAKOUT_PERCENT:
        send_breakout_alert(symbol, timeframe, state, candle)
        del states[(symbol, timeframe)]
        return

    # DIB2 QIRILMASI
    if candle["low"] < state["dib2"]:

        if n < MIN_CANDLES_TO_BREAKOUT:

            if state["dib2_break_count"] == 0:
                print(f"[{format_time(candle['close_time'])}] "
                      f"{symbol} {timeframe} | "
                      f"DIB2 BROKEN 1st time (<10 candles) → "
                      f"NEW DIB2 = {candle['low']} | "
                      f"old DIB2 = {state['dib2']}")

                state["dib2"] = candle["low"]
                state["dib2_time"] = candle["close_time"]
                state["dib2_break_count"] = 1
                state["candles_after_dib2"] = 0
                state["high_since_dib2"] = None
                state["high_since_dib2_time"] = None
                state["high_since_dib2_candle_num"] = None

            else:
                print(f"[{format_time(candle['close_time'])}] "
                      f"{symbol} {timeframe} | "
                      f"DIB2 BROKEN 2nd time (<10) → "
                      f"STRUCTURE RESET | "
                      f"new DIB1 = {candle['low']}")

                create_dib1(symbol, timeframe, candle,
                            "DIB2 broken 2nd time (<10) → RESET")

        else:

            old_dib2 = state["dib2"]
            old_dib2_time = state["dib2_time"]

            new_high1 = state["high_since_dib2"]
            new_high1_time = state["high_since_dib2_time"]
            new_high1_num = state["high_since_dib2_candle_num"]

            print(f"[{format_time(candle['close_time'])}] "
                  f"{symbol} {timeframe} | "
                  f"DIB2 BROKEN ({n} candles ≥10) → SLIDE DOWN | "
                  f"new DIB1 = {old_dib2} | "
                  f"new HIGH1 = {new_high1} | "
                  f"new DIB2 = {candle['low']}")

            state["dib1"] = old_dib2
            state["dib1_time"] = old_dib2_time
            state["candles_after_dib1"] = n

            state["high1"] = new_high1
            state["high1_time"] = new_high1_time
            state["high1_candle_number"] = new_high1_num

            state["dib2"] = candle["low"]
            state["dib2_time"] = candle["close_time"]
            state["dib2_break_count"] = 0
            state["candles_after_dib2"] = 0
            state["high_since_dib2"] = None
            state["high_since_dib2_time"] = None
            state["high_since_dib2_candle_num"] = None

        return


# ============================================================
# PROCESS CANDLE
# ============================================================

def process_candle(symbol, timeframe, candle, prev_100_min_low=None):

    key = (symbol, timeframe)

    with state_lock:

        if key in states:

            state = states[key]

            if state.get("phase") == "dib2_watch":
                process_dib2_phase(symbol, timeframe, state, candle)
                return

            state["candles_after_dib1"] += 1
            n = state["candles_after_dib1"]

            if n >= ROLLING_CANDLES:
                print(f"[{format_time(candle['close_time'])}] "
                      f"{symbol} {timeframe} | "
                      f"DIB1 LƏĞV OLUNDU (100 limit) | "
                      f"old DIB1={state['dib1']}")
                del states[key]
            else:
                if candle["low"] < state["dib1"]:

                    if (state["high1"] is not None
                        and state["high1_candle_number"] is not None
                        and state["high1_candle_number"] >= MIN_CANDLES_TO_HIGH1):
                        confirm_dib1_and_start_dib2(symbol, timeframe, state, candle)
                        return
                    else:
                        create_dib1(symbol, timeframe, candle,
                                    f"DIB1 broken <10 candles | "
                                    f"old DIB1={state['dib1']}")
                        return

                if state["high1"] is None or candle["high"] > state["high1"]:
                    state["high1"] = candle["high"]
                    state["high1_time"] = candle["close_time"]
                    state["high1_candle_number"] = n
                    print(f"[{format_time(candle['close_time'])}] "
                          f"{symbol} {timeframe} | "
                          f"HIGH1 = {candle['high']} | "
                          f"DIB1 → HIGH1 = {n} candles")
                return

        if prev_100_min_low is None:
            return

        if candle["low"] < prev_100_min_low:
            create_dib1(symbol, timeframe, candle,
                        f"New low vs prev 100: "
                        f"{candle['low']} < {prev_100_min_low}")


# ============================================================
# SCAN SYMBOL
# ============================================================

def scan_symbol(symbol, timeframe):

    key = (symbol, timeframe)

    candles = get_closed_candles(symbol, timeframe, limit=FETCH_LIMIT)
    if not candles:
        return

    if key not in initialized:
        if len(candles) < ROLLING_CANDLES + 1:
            return
        last_processed[key] = candles[-1]["close_time"]
        initialized[key] = True
        print(f"{symbol} {timeframe} | Initialized.")
        return

    prev_ct = last_processed.get(key, 0)
    new_candles = [c for c in candles if c["close_time"] > prev_ct]
    if not new_candles:
        return
    new_candles.sort(key=lambda x: x["close_time"])

    index_map = {c["close_time"]: i for i, c in enumerate(candles)}

    for candle in new_candles:

        i = index_map[candle["close_time"]]

        prev_100_min_low = None
        if i >= ROLLING_CANDLES:
            prev_window = candles[i - ROLLING_CANDLES : i]
            prev_100_min_low = min(c["low"] for c in prev_window)

        process_candle(symbol, timeframe, candle, prev_100_min_low)
        last_processed[key] = candle["close_time"]


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 70)
    print("BINANCE DIB1 + HIGH1 + DIB2 + BREAKOUT SCANNER")
    print("TELEGRAM ALERT:")
    print("  - 1h : DIB1 CONFIRMED + BREAKOUT")
    print("  - Digər TF : yalnız BREAKOUT")
    print("=" * 70)

    symbols = []
    last_top_refresh = 0

    while True:
        try:
            ct = time.time()

            if not symbols or ct - last_top_refresh >= TOP_REFRESH_SECONDS:
                ns = get_top_symbols()
                if ns:
                    symbols = ns
                    last_top_refresh = ct
                    print(f"\nTop {len(symbols)} USDT coins loaded.")

            for symbol in symbols:
                for timeframe in TIMEFRAMES:
                    try:
                        scan_symbol(symbol, timeframe)
                    except Exception as e:
                        print(f"{symbol} {timeframe} scan error:", e)

            print(f"[{now_az().strftime('%Y-%m-%d %H:%M:%S')}] "
                  f"Scan completed. Next in {POLL_SECONDS}s.")

            time.sleep(POLL_SECONDS)

        except KeyboardInterrupt:
            print("Bot stopped.")
            break
        except Exception as e:
            print("MAIN ERROR:", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
