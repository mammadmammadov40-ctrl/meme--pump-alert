import os
import time
import requests
from datetime import datetime, timezone


# ============================================================
# BINANCE 3-STRATEGY LIVE ALERT BOT
#
# FIXED VERSION
#
# 🔴 BEARISH:
#   5m + 15m + 30m + 1h
#   Telegram = TARGET HIT ONLY
#
# 🟢 BULLISH:
#   15m + 30m + 1h
#   Telegram = SIGNAL
#
# FIXES:
# 1. Bearish FVG checks now print exactly which condition fails.
# 2. Bearish target monitoring also checks the latest CLOSED 1m
#    candle low, so a target touched between 60-second scans is
#    much less likely to be missed.
# 3. Bearish setup creation is logged clearly.
# 4. Telegram response/body is logged when sending fails.
# 5. Bearish state is kept independent per symbol/timeframe.
# 6. Existing strategy conditions are preserved.
# ============================================================


BINANCE_BASE_URL = "https://api.binance.com"

MIN_QUOTE_VOLUME_24H = 20_000_000
SCAN_SECONDS = 60

# -------------------- BEARISH --------------------

FVG_MIN_PERCENT = 0.5
FVG_MIN_RATIO = 0.50

FVG_TARGETS = {
    "5m": 1.2,
    "15m": 1.7,
    "30m": 2.2,
    "1h": 2.7,
}

FVG_INTERVALS = list(FVG_TARGETS.keys())

# -------------------- BULLISH --------------------

BULLISH_INTERVALS = ["15m", "30m", "1h"]

BULLISH_EMA_FAST = 20
BULLISH_EMA_SLOW = 50
BULLISH_HISTORY_LIMIT = 200

BULLISH_FVG_MIN_PERCENT = 0.5

STRUCTURE_LOOKBACK = 10

DISPLACEMENT_MIN_BODY_RATIO = 0.60
DISPLACEMENT_RANGE_MULTIPLIER = 1.30
DISPLACEMENT_VOLUME_MULTIPLIER = 1.50

AVERAGE_LOOKBACK = 20

# -------------------- RSI DIVERGENCE --------------------

RSI_INTERVALS = ["15m", "30m", "1h"]
# Screenshot uses RSI(6), so this strategy follows RSI(6).
RSI_PERIOD = 6
RSI_HISTORY_LIMIT = 200
# A swing low is confirmed only after 2 candles to the right.
RSI_PIVOT_LEFT = 2
RSI_PIVOT_RIGHT = 2

# -------------------- TELEGRAM --------------------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# -------------------- STATE --------------------

active_setups = {}
processed_fvgs = set()
candle_state = {}

processed_bullish_signals = set()
bullish_candle_state = {}

processed_rsi_signals = set()
rsi_candle_state = {}


# ============================================================
# BINANCE REQUEST
# ============================================================

def binance_get(endpoint, params=None):
    url = BINANCE_BASE_URL + endpoint

    try:
        response = requests.get(url, params=params, timeout=20)
        response.raise_for_status()
        return response.json()

    except Exception as e:
        print(f"[BINANCE ERROR] {endpoint}: {e}")
        return None


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(
            "[TELEGRAM ERROR] "
            "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing"
        )
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=20
        )

        if not response.ok:
            print(
                f"[TELEGRAM ERROR] HTTP {response.status_code}: "
                f"{response.text}"
            )
            return False

        result = response.json()

        if not result.get("ok"):
            print(f"[TELEGRAM ERROR] {result}")
            return False

        return True

    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")
        return False


# ============================================================
# SYMBOLS
# ============================================================

def get_spot_usdt_symbols():
    data = binance_get("/api/v3/exchangeInfo")

    if not data:
        return []

    symbols = []

    for item in data.get("symbols", []):
        if (
            item.get("status") == "TRADING"
            and item.get("quoteAsset") == "USDT"
            and item.get("isSpotTradingAllowed") is True
        ):
            symbols.append(item["symbol"])

    return symbols


# ============================================================
# 24H TOTAL VOLUME
# ============================================================

def get_24h_volumes():
    data = binance_get("/api/v3/ticker/24hr")

    if not data:
        return {}

    volumes = {}

    for item in data:
        symbol = item.get("symbol")

        if not symbol:
            continue

        try:
            volumes[symbol] = float(item.get("quoteVolume", 0))
        except Exception:
            pass

    return volumes


# ============================================================
# 24H BUY / SELL VOLUME
# ============================================================

def get_24h_buy_sell_volume(symbol):
    data = binance_get(
        "/api/v3/klines",
        {
            "symbol": symbol,
            "interval": "1h",
            "limit": 25,
        }
    )

    if not data:
        return None

    now_ms = int(time.time() * 1000)
    closed = []

    for candle in data:
        try:
            if int(candle[6]) < now_ms:
                closed.append(candle)
        except Exception:
            pass

    if len(closed) < 24:
        return None

    candles_24h = closed[-24:]

    total_volume = 0.0
    buy_volume = 0.0

    for candle in candles_24h:
        try:
            total_volume += float(candle[7])
            buy_volume += float(candle[10])
        except Exception:
            continue

    if total_volume <= 0:
        return None

    sell_volume = max(0.0, total_volume - buy_volume)

    buy_percent = buy_volume / total_volume * 100
    sell_percent = sell_volume / total_volume * 100

    return {
        "total_volume": total_volume,
        "buy_volume": buy_volume,
        "sell_volume": sell_volume,
        "buy_percent": buy_percent,
        "sell_percent": sell_percent,
    }


# ============================================================
# FORMAT VOLUME
# ============================================================

def format_volume(value):
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"

    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"{value / 1_000:.2f}K"

    return f"{value:.2f}"


# ============================================================
# CANDLE HELPERS
# ============================================================

def candle_open(c):
    return float(c[1])


def candle_high(c):
    return float(c[2])


def candle_low(c):
    return float(c[3])


def candle_close(c):
    return float(c[4])


def candle_is_bearish(c):
    return candle_close(c) < candle_open(c)


# ============================================================
# GET LATEST 3 CLOSED CANDLES
# ============================================================

def get_latest_closed_candles(symbol, interval):
    data = binance_get(
        "/api/v3/klines",
        {
            "symbol": symbol,
            "interval": interval,
            "limit": 4,
        }
    )

    if not data:
        return []

    now_ms = int(time.time() * 1000)
    closed = []

    for candle in data:
        try:
            if int(candle[6]) < now_ms:
                closed.append(candle)
        except Exception:
            continue

    if len(closed) < 3:
        return []

    return closed[-3:]


# ============================================================
# 🔴 BEARISH FVG CHECK WITH DEBUG
# ============================================================

def detect_bearish_fvg(candles, debug=False, symbol="", interval=""):
    if len(candles) != 3:
        return None

    c1, c2, c3 = candles

    c1_low = candle_low(c1)
    c2_open = candle_open(c2)
    c2_close = candle_close(c2)
    c2_low = candle_low(c2)
    c3_high = candle_high(c3)
    c3_close = candle_close(c3)

    # --------------------------------------------------------
    # 1. C2 BEARISH
    # --------------------------------------------------------

    cond_c2_bearish = candle_is_bearish(c2)

    # --------------------------------------------------------
    # 2. C1 LOW > C3 HIGH
    # --------------------------------------------------------

    cond_fvg_gap = c1_low > c3_high

    # --------------------------------------------------------
    # 3. C3 CLOSE < C2 LOW
    # --------------------------------------------------------

    cond_c3_close = c3_close < c2_low

    fvg_low = c3_high
    fvg_high = c1_low
    fvg_size = fvg_high - fvg_low

    cond_fvg_positive = fvg_size > 0

    fvg_percent = (
        fvg_size / c1_low * 100
        if c1_low > 0 and fvg_size > 0
        else 0.0
    )

    # --------------------------------------------------------
    # C2 BODY
    # --------------------------------------------------------

    c2_body_high = max(c2_open, c2_close)
    c2_body_low = min(c2_open, c2_close)

    c2_body_size = c2_body_high - c2_body_low

    cond_body_positive = c2_body_size > 0

    # --------------------------------------------------------
    # 5. FVG INSIDE C2 BODY
    # --------------------------------------------------------

    cond_inside_body = (
        cond_body_positive
        and fvg_low >= c2_body_low
        and fvg_high <= c2_body_high
    )

    # --------------------------------------------------------
    # 6. FVG / BODY >= 50%
    # --------------------------------------------------------

    fvg_ratio = (
        fvg_size / c2_body_size
        if c2_body_size > 0 and fvg_size > 0
        else 0.0
    )

    cond_ratio = fvg_ratio >= FVG_MIN_RATIO

    # --------------------------------------------------------
    # 4. FVG >= 0.5%
    # --------------------------------------------------------

    cond_fvg_percent = fvg_percent >= FVG_MIN_PERCENT

    all_passed = all([
        cond_c2_bearish,
        cond_fvg_gap,
        cond_c3_close,
        cond_fvg_positive,
        cond_fvg_percent,
        cond_inside_body,
        cond_ratio,
    ])

    if debug:
        print(
            f"\n[BEARISH CHECK] {symbol} {interval}"
        )

        print(
            f"  C2 bearish: "
            f"{'✅' if cond_c2_bearish else '❌'} "
            f"(O={c2_open:.8g}, C={c2_close:.8g})"
        )

        print(
            f"  C1 LOW > C3 HIGH: "
            f"{'✅' if cond_fvg_gap else '❌'} "
            f"({c1_low:.8g} > {c3_high:.8g})"
        )

        print(
            f"  C3 CLOSE < C2 LOW: "
            f"{'✅' if cond_c3_close else '❌'} "
            f"({c3_close:.8g} < {c2_low:.8g})"
        )

        print(
            f"  FVG >= {FVG_MIN_PERCENT}%: "
            f"{'✅' if cond_fvg_percent else '❌'} "
            f"({fvg_percent:.4f}%)"
        )

        print(
            f"  FVG inside C2 body: "
            f"{'✅' if cond_inside_body else '❌'} "
            f"(FVG {fvg_low:.8g}-{fvg_high:.8g}, "
            f"BODY {c2_body_low:.8g}-{c2_body_high:.8g})"
        )

        print(
            f"  FVG/C2 Body >= "
            f"{FVG_MIN_RATIO * 100:.0f}%: "
            f"{'✅' if cond_ratio else '❌'} "
            f"({fvg_ratio * 100:.2f}%)"
        )

        print(
            f"  RESULT: "
            f"{'✅ ALL CONDITIONS PASSED' if all_passed else '❌ REJECTED'}"
        )

    if not all_passed:
        return None

    return {
        "fvg_low": fvg_low,
        "fvg_high": fvg_high,
        "fvg_size": fvg_size,
        "fvg_percent": fvg_percent,
        "fvg_ratio": fvg_ratio,
        "c1_low": c1_low,
        "c2_open": c2_open,
        "c2_close": c2_close,
        "c2_body_size": c2_body_size,
        "c3_open_time": int(c3[0]),
        "c1_open_time": int(c1[0]),
        "c2_open_time": int(c2[0]),
        "c3_close_time": int(c3[6]),
        "c3_high": c3_high,
        "c3_close": c3_close,
    }


# ============================================================
# 🔴 REAL-TIME ROLLING BEARISH FVG
# ============================================================

def update_realtime_fvg(symbol, interval):
    candles = get_latest_closed_candles(symbol, interval)

    if len(candles) < 3:
        return None

    latest_candle = candles[-1]
    latest_open_time = int(latest_candle[0])

    key = (symbol, interval)

    state = candle_state.get(key)

    # FIRST SCAN:
    # initialize only. Do not alert on old candle.
    if state is None:
        candle_state[key] = {
            "last_closed_open_time": latest_open_time,
            "window": candles[-3:],
        }

        print(
            f"[BEARISH INIT] {symbol} {interval} | "
            f"Waiting for NEW closed candle"
        )

        return None

    last_time = int(state["last_closed_open_time"])

    if latest_open_time <= last_time:
        return None

    # Always rebuild from Binance's latest 3 CLOSED candles.
    # This prevents a stale/incorrect rolling window if Railway
    # temporarily misses one or more scan cycles.
    new_window = candles[-3:]

    state["window"] = new_window
    state["last_closed_open_time"] = latest_open_time

    print(
        f"\n[BEARISH NEW CANDLE] {symbol} {interval} | "
        f"Checking FVG..."
    )

    fvg = detect_bearish_fvg(
        new_window,
        debug=True,
        symbol=symbol,
        interval=interval
    )

    if not fvg:
        return None

    fvg_id = (
        symbol,
        interval,
        fvg["c3_open_time"]
    )

    if fvg_id in processed_fvgs:
        return None

    fvg["fvg_id"] = fvg_id

    return fvg


# ============================================================
# 🔴 CREATE BEARISH SETUP
# ============================================================

def create_setup(symbol, interval, fvg, volume_data):
    target_percent = FVG_TARGETS[interval]

    c3_high = fvg["c3_high"]

    target = c3_high * (
        1 - target_percent / 100
    )

    return {
        "symbol": symbol,
        "interval": interval,
        "fvg_id": fvg["fvg_id"],

        "fvg_low": fvg["fvg_low"],
        "fvg_high": fvg["fvg_high"],
        "fvg_size": fvg["fvg_size"],
        "fvg_percent": fvg["fvg_percent"],
        "fvg_ratio": fvg["fvg_ratio"],

        "c1_low": fvg["c1_low"],
        "c2_body_size": fvg["c2_body_size"],

        "c3_high": c3_high,
        "c3_close": fvg["c3_close"],

        "target": target,
        "target_percent": target_percent,

        "volume_24h": volume_data["total_volume"],
        "buy_volume": volume_data["buy_volume"],
        "sell_volume": volume_data["sell_volume"],
        "buy_percent": volume_data["buy_percent"],
        "sell_percent": volume_data["sell_percent"],

        "created_at": int(time.time() * 1000),
    }


# ============================================================
# 🔴 BEARISH TARGET MESSAGE
# ============================================================

def format_target_message(setup):
    return (
        "🔴 <b>BEARISH FVG TARGET HIT</b>\n\n"

        f"<b>{setup['symbol']}</b> "
        f"{setup['interval']}\n\n"

        f"<b>C3 High:</b> "
        f"{setup['c3_high']:.8g}\n"

        f"<b>Target (-"
        f"{setup['target_percent']}%):</b> "
        f"{setup['target']:.8g}\n\n"

        f"<b>Movement:</b> "
        f"{setup['c3_high']:.8g}"
        f" → "
        f"{setup['target']:.8g}\n\n"

        f"<b>FVG Size:</b> "
        f"{setup['fvg_percent']:.2f}%\n"

        f"<b>FVG / C2 Body:</b> "
        f"{setup['fvg_ratio'] * 100:.2f}%\n\n"

        f"<b>24H Volume:</b> "
        f"{format_volume(setup['volume_24h'])}\n"

        f"<b>Buy:</b> "
        f"{format_volume(setup['buy_volume'])}"
        f" ({setup['buy_percent']:.2f}%)\n"

        f"<b>Sell:</b> "
        f"{format_volume(setup['sell_volume'])}"
        f" ({setup['sell_percent']:.2f}%)\n\n"

        "Original Bearish FVG setup completed."
    )


# ============================================================
# CURRENT PRICE
# ============================================================

def get_current_price(symbol):
    data = binance_get(
        "/api/v3/ticker/price",
        {"symbol": symbol}
    )

    if not data:
        return None

    try:
        return float(data["price"])
    except Exception:
        return None


# ============================================================
# 🔴 LATEST CLOSED 1m CANDLE
#
# Used so a target touched between two 60-second scans is
# not automatically missed.
# ============================================================

def get_latest_closed_1m_candle(symbol):
    data = binance_get(
        "/api/v3/klines",
        {
            "symbol": symbol,
            "interval": "1m",
            "limit": 2,
        }
    )

    if not data:
        return None

    now_ms = int(time.time() * 1000)

    closed = []

    for candle in data:
        try:
            if int(candle[6]) < now_ms:
                closed.append(candle)
        except Exception:
            continue

    if not closed:
        return None

    return closed[-1]


# ============================================================
# 🔴 MONITOR ACTIVE BEARISH SETUPS
# ============================================================

def monitor_active_setups():
    if not active_setups:
        return

    for key, setup in list(active_setups.items()):

        symbol, interval = key

        try:
            current_price = get_current_price(symbol)

            if current_price is None:
                continue

            target = setup["target"]
            c3_high = setup["c3_high"]

            # ------------------------------------------------
            # DIRECT CURRENT PRICE CHECK
            # ------------------------------------------------

            if current_price <= target:
                print(
                    f"[BEARISH TARGET HIT] "
                    f"{symbol} {interval} | "
                    f"Current={current_price:.8g} | "
                    f"Target={target:.8g}"
                )

                success = send_telegram(
                    format_target_message(setup)
                )

                print(
                    "[BEARISH TELEGRAM] "
                    + ("SENT" if success else "FAILED")
                )

                active_setups.pop(key, None)
                continue

            # ------------------------------------------------
            # 1m CLOSED CANDLE CHECK
            #
            # This catches a target touched between normal
            # 60-second ticker checks.
            # ------------------------------------------------

            one_minute = get_latest_closed_1m_candle(symbol)

            if one_minute is not None:
                one_min_low = candle_low(one_minute)

                if one_min_low <= target:
                    print(
                        f"[BEARISH TARGET HIT / 1m LOW] "
                        f"{symbol} {interval} | "
                        f"1m Low={one_min_low:.8g} | "
                        f"Target={target:.8g}"
                    )

                    success = send_telegram(
                        format_target_message(setup)
                    )

                    print(
                        "[BEARISH TELEGRAM] "
                        + ("SENT" if success else "FAILED")
                    )

                    active_setups.pop(key, None)
                    continue

            # ------------------------------------------------
            # CANCELLED
            # ------------------------------------------------

            if current_price > c3_high:
                print(
                    f"[BEARISH CANCELLED] "
                    f"{symbol} {interval} | "
                    f"Current={current_price:.8g} > "
                    f"C3 High={c3_high:.8g}"
                )

                active_setups.pop(key, None)
                continue

        except Exception as e:
            print(
                f"[BEARISH MONITOR ERROR] "
                f"{symbol} {interval}: {e}"
            )


# ============================================================
# 🟢 BULLISH CLOSED CANDLES
# ============================================================

def get_bullish_closed_candles(
    symbol,
    interval,
    limit=BULLISH_HISTORY_LIMIT
):
    data = binance_get(
        "/api/v3/klines",
        {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
    )

    if not data:
        return []

    now_ms = int(time.time() * 1000)
    closed = []

    for candle in data:
        try:
            if int(candle[6]) < now_ms:
                closed.append(candle)
        except Exception:
            continue

    return closed


# ============================================================
# EMA
# ============================================================

def calculate_ema(values, period):
    if len(values) < period:
        return None

    ema = sum(values[:period]) / period

    multiplier = 2.0 / (period + 1)

    for value in values[period:]:
        ema = (
            (value - ema) * multiplier
            + ema
        )

    return ema


# ============================================================
# 🟢 BULLISH FVG
# ============================================================

def detect_bullish_fvg(candles):
    if len(candles) < 3:
        return None

    c1 = candles[-3]
    c2 = candles[-2]
    c3 = candles[-1]

    c1_high = candle_high(c1)
    c3_low = candle_low(c3)

    if c1_high >= c3_low:
        return None

    fvg_size = c3_low - c1_high

    if fvg_size <= 0:
        return None

    fvg_percent = (
        fvg_size / c1_high * 100
    )

    if fvg_percent < BULLISH_FVG_MIN_PERCENT:
        return None

    return {
        "c1_high": c1_high,
        "c2_high": candle_high(c2),
        "c2_low": candle_low(c2),
        "c3_low": c3_low,
        "c3_high": candle_high(c3),
        "c3_open": candle_open(c3),
        "c3_close": candle_close(c3),
        "fvg_size": fvg_size,
        "fvg_percent": fvg_percent,
        "c1_open_time": int(c1[0]),
        "c2_open_time": int(c2[0]),
        "c3_open_time": int(c3[0]),
        "c3_close_time": int(c3[6]),
    }


# ============================================================
# 🟢 BULLISH DISPLACEMENT
# ============================================================

def check_bullish_displacement(candles):
    if len(candles) < AVERAGE_LOOKBACK + 1:
        return None

    c3 = candles[-1]

    c3_open = candle_open(c3)
    c3_high = candle_high(c3)
    c3_low = candle_low(c3)
    c3_close = candle_close(c3)

    if c3_close <= c3_open:
        return None

    c3_range = c3_high - c3_low

    if c3_range <= 0:
        return None

    c3_body = c3_close - c3_open

    body_ratio = c3_body / c3_range

    if body_ratio < DISPLACEMENT_MIN_BODY_RATIO:
        return None

    previous_candles = candles[
        -(AVERAGE_LOOKBACK + 1):-1
    ]

    ranges = []
    volumes = []

    for candle in previous_candles:
        high = candle_high(candle)
        low = candle_low(candle)

        try:
            quote_volume = float(candle[7])
        except Exception:
            continue

        candle_range = high - low

        if candle_range > 0:
            ranges.append(candle_range)

        volumes.append(quote_volume)

    if len(ranges) < AVERAGE_LOOKBACK:
        return None

    if len(volumes) < AVERAGE_LOOKBACK:
        return None

    average_range = sum(ranges) / len(ranges)
    average_volume = sum(volumes) / len(volumes)

    if average_range <= 0 or average_volume <= 0:
        return None

    range_multiple = c3_range / average_range

    if range_multiple < DISPLACEMENT_RANGE_MULTIPLIER:
        return None

    try:
        c3_volume = float(c3[7])
    except Exception:
        return None

    volume_multiple = c3_volume / average_volume

    if volume_multiple < DISPLACEMENT_VOLUME_MULTIPLIER:
        return None

    return {
        "body_ratio": body_ratio,
        "range": c3_range,
        "average_range": average_range,
        "range_multiple": range_multiple,
        "volume": c3_volume,
        "average_volume": average_volume,
        "volume_multiple": volume_multiple,
    }


# ============================================================
# 🟢 BULLISH STRUCTURE BREAK
# ============================================================

def check_bullish_structure_break(candles):
    required = STRUCTURE_LOOKBACK + 1

    if len(candles) < required:
        return None

    c3 = candles[-1]

    previous_candles = candles[
        -(STRUCTURE_LOOKBACK + 1):-1
    ]

    previous_high = max(
        candle_high(c)
        for c in previous_candles
    )

    c3_close = candle_close(c3)

    if c3_close <= previous_high:
        return None

    break_percent = (
        (c3_close - previous_high)
        / previous_high
        * 100
    )

    return {
        "previous_high": previous_high,
        "break_price": c3_close,
        "break_percent": break_percent,
    }


# ============================================================
# 🟢 BULLISH STRATEGY
# ============================================================

def detect_bullish_strategy(symbol, interval):
    candles = get_bullish_closed_candles(
        symbol,
        interval,
        BULLISH_HISTORY_LIMIT
    )

    minimum_needed = max(
        BULLISH_EMA_SLOW + 2,
        STRUCTURE_LOOKBACK + 1,
        AVERAGE_LOOKBACK + 1
    )

    if len(candles) < minimum_needed:
        return None

    c3 = candles[-1]
    c3_open_time = int(c3[0])

    closes = [
        candle_close(c)
        for c in candles
    ]

    ema20 = calculate_ema(
        closes,
        BULLISH_EMA_FAST
    )

    ema50 = calculate_ema(
        closes,
        BULLISH_EMA_SLOW
    )

    if ema20 is None or ema50 is None:
        return None

    if ema20 <= ema50:
        return None

    previous_ema20 = calculate_ema(
        closes[:-1],
        BULLISH_EMA_FAST
    )

    if previous_ema20 is None:
        return None

    if ema20 <= previous_ema20:
        return None

    c3_close = candle_close(c3)

    if c3_close <= ema20:
        return None

    structure = check_bullish_structure_break(candles)

    if structure is None:
        return None

    displacement = check_bullish_displacement(candles)

    if displacement is None:
        return None

    fvg = detect_bullish_fvg(candles[-3:])

    if fvg is None:
        return None

    signal_id = (
        symbol,
        interval,
        c3_open_time
    )

    if signal_id in processed_bullish_signals:
        return None

    return {
        "signal_id": signal_id,
        "symbol": symbol,
        "interval": interval,
        "c3_open_time": c3_open_time,
        "c3_close": c3_close,
        "c3_high": candle_high(c3),
        "c3_low": candle_low(c3),

        "ema20": ema20,
        "ema50": ema50,
        "previous_ema20": previous_ema20,

        "previous_high": structure["previous_high"],
        "break_price": structure["break_price"],
        "break_percent": structure["break_percent"],

        "body_ratio": displacement["body_ratio"],
        "range": displacement["range"],
        "average_range": displacement["average_range"],
        "range_multiple": displacement["range_multiple"],
        "volume": displacement["volume"],
        "average_volume": displacement["average_volume"],
        "volume_multiple": displacement["volume_multiple"],

        "fvg_low": fvg["c1_high"],
        "fvg_high": fvg["c3_low"],
        "fvg_size": fvg["fvg_size"],
        "fvg_percent": fvg["fvg_percent"],
    }


# ============================================================
# 🟢 BULLISH TELEGRAM
# ============================================================

def format_bullish_message(signal, volume_data):
    return (
        "🟢 <b>BULLISH TREND SIGNAL</b>\n\n"

        f"<b>{signal['symbol']}</b> "
        f"{signal['interval']}\n\n"

        "━━━━━━━━━━━━━━━━━━\n"
        "<b>📈 TREND</b>\n"

        f"EMA20: {signal['ema20']:.8g}\n"
        f"EMA50: {signal['ema50']:.8g}\n"
        "EMA20 > EMA50: ✅\n"
        "Price > EMA20: ✅\n"
        "EMA20 Rising: ✅\n\n"

        "━━━━━━━━━━━━━━━━━━\n"
        "<b>🚀 STRUCTURE</b>\n"

        f"Previous High: "
        f"{signal['previous_high']:.8g}\n"

        f"Break Price: "
        f"{signal['break_price']:.8g}\n"

        f"BOS: ✅ "
        f"(+{signal['break_percent']:.2f}%)\n\n"

        "━━━━━━━━━━━━━━━━━━\n"
        "<b>💪 DISPLACEMENT</b>\n"

        f"Body/Range: "
        f"{signal['body_ratio'] * 100:.2f}%\n"

        f"Range: "
        f"{signal['range_multiple']:.2f}x avg\n"

        f"Volume: "
        f"{signal['volume_multiple']:.2f}x avg\n\n"

        "━━━━━━━━━━━━━━━━━━\n"
        "<b>🟢 BULLISH FVG</b>\n"

        f"FVG: "
        f"{signal['fvg_low']:.8g}"
        f" → "
        f"{signal['fvg_high']:.8g}\n"

        f"FVG Size: "
        f"{signal['fvg_percent']:.2f}%\n"

        "C2 Body Filter: ❌\n"
        "50% Ratio Filter: ❌\n\n"

        "━━━━━━━━━━━━━━━━━━\n"

        f"<b>24H Volume:</b> "
        f"{format_volume(volume_data['total_volume'])}\n"

        f"<b>Buy:</b> "
        f"{format_volume(volume_data['buy_volume'])}"
        f" ({volume_data['buy_percent']:.2f}%)\n"

        f"<b>Sell:</b> "
        f"{format_volume(volume_data['sell_volume'])}"
        f" ({volume_data['sell_percent']:.2f}%)\n\n"

        "🟢 <b>ALL BULLISH CONDITIONS PASSED</b>"
    )


# ============================================================
# 🔵 RSI BULLISH DIVERGENCE STRATEGY
# ============================================================


def calculate_rsi(values, period=RSI_PERIOD):
    """Calculate Wilder RSI for a list of closed-candle closes."""
    if len(values) <= period:
        return []

    gains = []
    losses = []

    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # One RSI value per candle. The first 'period' entries do not
    # have enough data to calculate RSI.
    rsi = [None] * period

    if avg_loss == 0:
        rsi.append(100.0)
    else:
        rs = avg_gain / avg_loss
        rsi.append(100.0 - (100.0 / (1.0 + rs)))

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            current_rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            current_rsi = 100.0 - (100.0 / (1.0 + rs))

        rsi.append(current_rsi)

    return rsi


def is_price_pivot_low(candles, index):
    """Confirm a local price swing low using left/right closed candles."""
    left = RSI_PIVOT_LEFT
    right = RSI_PIVOT_RIGHT

    if index < left or index + right >= len(candles):
        return False

    center = candle_low(candles[index])

    left_values = [
        candle_low(candles[i])
        for i in range(index - left, index)
    ]

    right_values = [
        candle_low(candles[i])
        for i in range(index + 1, index + right + 1)
    ]

    return (
        center < min(left_values)
        and center <= min(right_values)
    )


def find_bullish_rsi_divergence(candles):
    """
    Detect the exact pattern shown in the user's chart:

      Price: second confirmed swing low is LOWER than first.
      RSI:   RSI at second swing low is HIGHER than first.

    The second swing low must have RSI_PIVOT_RIGHT closed candles
    after it. This avoids using an unfinished/future pivot.
    """
    minimum_needed = (
        RSI_PERIOD
        + RSI_PIVOT_LEFT
        + RSI_PIVOT_RIGHT
        + 10
    )

    if len(candles) < minimum_needed:
        return None

    closes = [candle_close(c) for c in candles]
    rsi_values = calculate_rsi(closes, RSI_PERIOD)

    if not rsi_values or len(rsi_values) != len(candles):
        return None

    pivots = []

    # Only pivots that are fully confirmed by candles to the right.
    last_confirmable = len(candles) - RSI_PIVOT_RIGHT - 1

    for i in range(RSI_PIVOT_LEFT, last_confirmable + 1):
        if not is_price_pivot_low(candles, i):
            continue

        rsi_value = rsi_values[i]

        if rsi_value is None:
            continue

        pivots.append({
            "index": i,
            "time": int(candles[i][0]),
            "price_low": candle_low(candles[i]),
            "rsi": float(rsi_value),
        })

    if len(pivots) < 2:
        return None

    # Check the newest consecutive pair first.
    for second_pos in range(len(pivots) - 1, 0, -1):
        first = pivots[second_pos - 1]
        second = pivots[second_pos]

        price_lower_low = second["price_low"] < first["price_low"]
        rsi_higher_low = second["rsi"] > first["rsi"]

        if not (price_lower_low and rsi_higher_low):
            continue

        price_change_percent = (
            (second["price_low"] - first["price_low"])
            / first["price_low"]
            * 100
            if first["price_low"] > 0
            else 0.0
        )

        rsi_change = second["rsi"] - first["rsi"]

        return {
            "first_index": first["index"],
            "second_index": second["index"],
            "first_time": first["time"],
            "second_time": second["time"],
            "first_price_low": first["price_low"],
            "second_price_low": second["price_low"],
            "first_rsi": first["rsi"],
            "second_rsi": second["rsi"],
            "price_change_percent": price_change_percent,
            "rsi_change": rsi_change,
            "confirmed_at": int(candles[-1][0]),
        }

    return None


def get_rsi_closed_candles(symbol, interval):
    return get_bullish_closed_candles(
        symbol,
        interval,
        RSI_HISTORY_LIMIT
    )


def format_rsi_divergence_message(signal, total_volume):
    return (
        "🔵 <b>BULLISH RSI DIVERGENCE</b>\n\n"
        f"<b>{signal['symbol']}</b> {signal['interval']}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<b>📉 PRICE</b>\n"
        f"1-ci dib: {signal['first_price_low']:.8g}\n"
        f"2-ci dib: {signal['second_price_low']:.8g}\n"
        f"Qiymət: {signal['price_change_percent']:.2f}%\n"
        "Lower Low: ✅\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<b>📈 RSI(6)</b>\n"
        f"1-ci RSI: {signal['first_rsi']:.2f}\n"
        f"2-ci RSI: {signal['second_rsi']:.2f}\n"
        f"RSI: +{signal['rsi_change']:.2f}\n"
        "Higher Low: ✅\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"<b>24H Volume:</b> {format_volume(total_volume)}\n\n"
        "🔵 <b>BULLISH DIVERGENCE CONFIRMED</b>"
    )


    # ========================================================
    # 🔵 RSI BULLISH DIVERGENCE SCAN
    # ========================================================

    print("\n========== RSI BULLISH DIVERGENCE SCAN ==========")

    # Strategy 3 is independent of the BUY > SELL filter.
    # It uses only the common 24H liquidity requirement.
    rsi_symbols = [
        (symbol, volumes.get(symbol, 0.0))
        for symbol in symbols
        if volumes.get(symbol, 0.0) >= MIN_QUOTE_VOLUME_24H
    ]

    for symbol, total_volume in rsi_symbols:

        for interval in RSI_INTERVALS:

            try:
                candles = get_rsi_closed_candles(
                    symbol,
                    interval
                )

                if not candles:
                    continue

                latest_open_time = int(candles[-1][0])
                state_key = (symbol, interval)
                previous_time = rsi_candle_state.get(state_key)

                # First scan only initializes the candle clock.
                # No old/historical divergence alert after restart.
                if previous_time is None:
                    rsi_candle_state[state_key] = latest_open_time
                    print(
                        f"[RSI INIT] {symbol} {interval} | "
                        "Waiting for NEW closed candle"
                    )
                    continue

                if latest_open_time <= previous_time:
                    continue

                rsi_candle_state[state_key] = latest_open_time

                signal = find_bullish_rsi_divergence(candles)

                if signal is None:
                    continue

                signal_id = (
                    symbol,
                    interval,
                    signal["second_time"],
                )

                if signal_id in processed_rsi_signals:
                    continue

                processed_rsi_signals.add(signal_id)

                full_signal = {
                    "symbol": symbol,
                    "interval": interval,
                    **signal,
                }

                confirmation_time = datetime.fromtimestamp(
                    signal["confirmed_at"] / 1000,
                    tz=timezone.utc
                )

                print(
                    "\n[🔵 RSI BULLISH DIVERGENCE] "
                    f"{symbol} {interval}"
                )
                print(
                    f"  Price: {signal['first_price_low']:.8g}"
                    f" -> {signal['second_price_low']:.8g} "
                    f"({signal['price_change_percent']:.2f}%)"
                )
                print(
                    f"  RSI(6): {signal['first_rsi']:.2f}"
                    f" -> {signal['second_rsi']:.2f} "
                    f"(+{signal['rsi_change']:.2f})"
                )
                print(
                    f"  Confirmed: "
                    f"{confirmation_time.strftime('%Y-%m-%d %H:%M:%S UTC')}"
                )

                success = send_telegram(
                    format_rsi_divergence_message(
                        full_signal,
                        total_volume
                    )
                )

                print(
                    "[RSI TELEGRAM] "
                    + ("SENT" if success else "FAILED")
                )

            except Exception as e:
                print(
                    f"[RSI ERROR] {symbol} {interval}: {e}"
                )


# ============================================================
# MAIN SCAN
# ============================================================

def scan():
    print("\n" + "=" * 75)
    print("NEW SCAN")
    print(
        datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
    )
    print("=" * 75)

    # --------------------------------------------------------
    # FIRST: MONITOR EXISTING BEARISH SETUPS
    # --------------------------------------------------------

    monitor_active_setups()

    # --------------------------------------------------------
    # SYMBOLS
    # --------------------------------------------------------

    symbols = get_spot_usdt_symbols()

    if not symbols:
        print("[ERROR] Could not get symbols")
        return

    print(
        f"[INFO] Spot USDT symbols: {len(symbols)}"
    )

    # --------------------------------------------------------
    # 24H VOLUME
    # --------------------------------------------------------

    volumes = get_24h_volumes()

    if not volumes:
        print("[ERROR] Could not get 24H volumes")
        return

    volume_qualified_symbols = []

    for symbol in symbols:
        volume = volumes.get(symbol)

        if (
            volume is not None
            and volume >= MIN_QUOTE_VOLUME_24H
        ):
            volume_qualified_symbols.append(symbol)

    print(
        f"[INFO] 20M+ total volume: "
        f"{len(volume_qualified_symbols)}"
    )

    # --------------------------------------------------------
    # BUY / SELL FILTER
    # --------------------------------------------------------

    qualified_symbols = []

    for symbol in volume_qualified_symbols:
        try:
            volume_data = get_24h_buy_sell_volume(symbol)

            if volume_data is None:
                print(f"[VOLUME ERROR] {symbol}")
                continue

            total_volume = volume_data["total_volume"]
            buy_volume = volume_data["buy_volume"]
            sell_volume = volume_data["sell_volume"]

            buy_percent = volume_data["buy_percent"]
            sell_percent = volume_data["sell_percent"]

            if (
                total_volume >= MIN_QUOTE_VOLUME_24H
                and buy_volume > sell_volume
            ):
                qualified_symbols.append(
                    (symbol, volume_data)
                )

                print(
                    f"[QUALIFIED] {symbol} | "
                    f"24H={format_volume(total_volume)} | "
                    f"BUY={format_volume(buy_volume)} "
                    f"({buy_percent:.2f}%) | "
                    f"SELL={format_volume(sell_volume)} "
                    f"({sell_percent:.2f}%)"
                )

            else:
                print(
                    f"[REJECTED] {symbol} | "
                    f"24H={format_volume(total_volume)} | "
                    f"BUY={format_volume(buy_volume)} "
                    f"({buy_percent:.2f}%) | "
                    f"SELL={format_volume(sell_volume)} "
                    f"({sell_percent:.2f}%)"
                )

        except Exception as e:
            print(
                f"[BUY/SELL ERROR] {symbol}: {e}"
            )

    print(
        f"[INFO] Final qualified: "
        f"{len(qualified_symbols)}"
    )

    # ========================================================
    # 🔴 BEARISH FVG SCAN
    # ========================================================

    print("\n========== BEARISH FVG SCAN ==========")

    for symbol, volume_data in qualified_symbols:

        for interval in FVG_INTERVALS:

            try:
                fvg = update_realtime_fvg(
                    symbol,
                    interval
                )

                if not fvg:
                    continue

                setup_key = (
                    symbol,
                    interval
                )

                # One active setup per symbol/timeframe
                if setup_key in active_setups:
                    print(
                        f"[BEARISH IGNORED] "
                        f"{symbol} {interval} "
                        f"already active | "
                        f"FVG={fvg['fvg_low']:.8g}-{fvg['fvg_high']:.8g}"
                    )
                    continue

                # Mark it processed only after it is actually accepted
                # as a new active setup.
                if fvg["fvg_id"] in processed_fvgs:
                    continue

                processed_fvgs.add(
                    fvg["fvg_id"]
                )

                setup = create_setup(
                    symbol,
                    interval,
                    fvg,
                    volume_data
                )

                active_setups[setup_key] = setup

                print(
                    "\n"
                    f"[🔥 NEW BEARISH FVG ACTIVE] "
                    f"{symbol} {interval}\n"
                    f"  FVG="
                    f"{fvg['fvg_low']:.8g}"
                    f" - "
                    f"{fvg['fvg_high']:.8g}\n"
                    f"  Size="
                    f"{fvg['fvg_percent']:.2f}%\n"
                    f"  C2 Ratio="
                    f"{fvg['fvg_ratio'] * 100:.2f}%\n"
                    f"  C3 High="
                    f"{fvg['c3_high']:.8g}\n"
                    f"  TARGET="
                    f"{setup['target']:.8g}\n"
                    f"  Telegram="
                    f"TARGET HIT ONLY"
                )

            except Exception as e:
                print(
                    f"[BEARISH FVG ERROR] "
                    f"{symbol} {interval}: {e}"
                )

    print(
        f"[BEARISH ACTIVE SETUPS] "
        f"{len(active_setups)}"
    )

    # ========================================================
    # 🟢 BULLISH STRATEGY
    # ========================================================

    print("\n========== BULLISH TREND SCAN ==========")

    for symbol, volume_data in qualified_symbols:

        for interval in BULLISH_INTERVALS:

            try:
                candles = get_bullish_closed_candles(
                    symbol,
                    interval,
                    BULLISH_HISTORY_LIMIT
                )

                if not candles:
                    continue

                latest_open_time = int(
                    candles[-1][0]
                )

                state_key = (
                    symbol,
                    interval
                )

                previous_time = (
                    bullish_candle_state.get(
                        state_key
                    )
                )

                # FIRST SCAN: initialize only
                if previous_time is None:
                    bullish_candle_state[
                        state_key
                    ] = latest_open_time

                    print(
                        f"[BULLISH INIT] "
                        f"{symbol} {interval} | "
                        f"Waiting for NEW closed candle"
                    )

                    continue

                if latest_open_time <= previous_time:
                    continue

                bullish_candle_state[
                    state_key
                ] = latest_open_time

                print(
                    f"[BULLISH NEW CANDLE] "
                    f"{symbol} {interval}"
                )

                signal = detect_bullish_strategy(
                    symbol,
                    interval
                )

                if signal is None:
                    continue

                signal_id = signal["signal_id"]

                if signal_id in processed_bullish_signals:
                    continue

                processed_bullish_signals.add(
                    signal_id
                )

                print(
                    "\n"
                    f"[🟢 BULLISH SIGNAL] "
                    f"{symbol} {interval}"
                )

                print(
                    f"  EMA20={signal['ema20']:.8g} | "
                    f"EMA50={signal['ema50']:.8g}"
                )

                print(
                    f"  EMA20 Rising="
                    f"{signal['previous_ema20']:.8g}"
                    f" -> "
                    f"{signal['ema20']:.8g}"
                )

                print(
                    f"  BOS="
                    f"{signal['previous_high']:.8g}"
                    f" -> "
                    f"{signal['break_price']:.8g} "
                    f"(+{signal['break_percent']:.2f}%)"
                )

                print(
                    f"  Body="
                    f"{signal['body_ratio'] * 100:.2f}% | "
                    f"Range="
                    f"{signal['range_multiple']:.2f}x | "
                    f"Volume="
                    f"{signal['volume_multiple']:.2f}x"
                )

                print(
                    f"  Bullish FVG="
                    f"{signal['fvg_low']:.8g}"
                    f"-"
                    f"{signal['fvg_high']:.8g} | "
                    f"Size="
                    f"{signal['fvg_percent']:.2f}%"
                )

                success = send_telegram(
                    format_bullish_message(
                        signal,
                        volume_data
                    )
                )

                print(
                    "[BULLISH TELEGRAM] "
                    + ("SENT" if success else "FAILED")
                )

            except Exception as e:
                print(
                    f"[BULLISH ERROR] "
                    f"{symbol} {interval}: {e}"
                )


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 75)
    print("BINANCE 3-STRATEGY LIVE ALERT BOT - FIXED")
    print("=" * 75)

    print("\n🔴 ORIGINAL BEARISH FVG:")
    print("  Timeframes: 5m + 15m + 30m + 1h")
    print("  C2 Bearish: REQUIRED")
    print("  C1 LOW > C3 HIGH: REQUIRED")
    print("  C3 Close < C2 Low: REQUIRED")
    print("  FVG >= 0.5%")
    print("  FVG inside C2 Body: REQUIRED")
    print("  FVG/C2 Body >= 50%")
    print("  Telegram: TARGET HIT ONLY")
    print("  Target monitor: current price + closed 1m LOW")

    print("\n🟢 BULLISH TREND STRATEGY:")
    print("  Timeframes: 15m + 30m + 1h")
    print(f"  EMA{BULLISH_EMA_FAST} > EMA{BULLISH_EMA_SLOW}")
    print("  EMA20 Rising")
    print("  Price > EMA20")
    print(
        f"  Structure Break: Previous "
        f"{STRUCTURE_LOOKBACK} closed candles HIGH"
    )
    print("  Bullish Displacement")
    print(
        f"  Body/Range >= "
        f"{DISPLACEMENT_MIN_BODY_RATIO * 100:.0f}%"
    )
    print(
        f"  Range >= "
        f"{DISPLACEMENT_RANGE_MULTIPLIER:.1f}x average"
    )
    print(
        f"  Volume >= "
        f"{DISPLACEMENT_VOLUME_MULTIPLIER:.1f}x average"
    )
    print(
        f"  Bullish FVG >= "
        f"{BULLISH_FVG_MIN_PERCENT:.1f}%"
    )
    print("  C2 Body condition: DISABLED")
    print("  50% Ratio condition: DISABLED")
    print("  First scan: NO SIGNAL")
    print("  New closed candle: CHECK")
    print("  Telegram: SIGNAL")

    print("\n🔵 RSI BULLISH DIVERGENCE STRATEGY:")
    print("  Timeframes: 15m + 30m + 1h")
    print(f"  RSI: {RSI_PERIOD}")
    print("  Price: Lower Low")
    print("  RSI: Higher Low")
    print(
        f"  Pivot: {RSI_PIVOT_LEFT} left / "
        f"{RSI_PIVOT_RIGHT} right closed candles"
    )
    print("  First scan: NO SIGNAL")
    print("  New closed candle: CHECK")
    print("  Telegram: SIGNAL")
    print("  BUY > SELL filter: DISABLED")

    print("\nCOMMON VOLUME FILTER:")
    print("  24H Total >= 20M USDT")
    print("  24H BUY > SELL")
    print(f"\nScan: {SCAN_SECONDS} seconds")
    print("=" * 75)

    while True:
        cycle_start = time.time()

        try:
            scan()

        except Exception as e:
            print(f"[MAIN ERROR] {e}")

        elapsed = time.time() - cycle_start

        sleep_time = max(
            1,
            SCAN_SECONDS - elapsed
        )

        print(
            f"\n[WAIT] Next scan in "
            f"{sleep_time:.1f}s"
        )

        time.sleep(sleep_time)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        print("\nBot stopped.")

    except Exception as e:
        print(f"FATAL ERROR: {e}")
