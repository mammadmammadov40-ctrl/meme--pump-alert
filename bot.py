import time
import requests
from datetime import datetime, timezone

# ============================================================
# CONFIG
# ============================================================

BINANCE_BASE_URL = "https://api.binance.com"

TELEGRAM_BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID = "YOUR_TELEGRAM_CHAT_ID"

# 24H minimum quote volume
MIN_QUOTE_VOLUME_24H = 20_000_000

# Bearish FVG minimum size
FVG_MIN_RATIO = 0.50

# Target: Candle 3 High - 1.7%
TARGET_PERCENT = 1.7

# Scan interval
SCAN_INTERVAL_SECONDS = 60

# 15M first, then 1H
FVG_INTERVALS = ["15m", "1h"]

# EMA settings
EMA_FAST = 20
EMA_MID = 50
EMA_SLOW = 100


# ============================================================
# SESSION STATE
# ============================================================

# Active FVG:
# {
#     "symbol": ...,
#     "interval": ...,
#     "c1_open_time": ...,
#     "c2_open_time": ...,
#     "c3_open_time": ...,
#     "c1_low": ...,
#     "c2_open": ...,
#     "c2_close": ...,
#     "c3_high": ...,
#     "fvg_low": ...,
#     "fvg_high": ...,
#     "target": ...
# }
active_fvgs = {}

# Last processed Candle 3 for each symbol/timeframe.
# This prevents re-using an old FVG.
last_processed_fvg = {}

# Search baseline for each symbol/timeframe.
# Only FVGs formed after this point are considered NEW.
fvg_search_start = {}

# Symbols that have already passed the $20M qualification.
qualified_symbols = set()

# Cache for 24H volume
volume_cache = {
    "timestamp": 0,
    "symbols": []
}

VOLUME_CACHE_SECONDS = 300


# ============================================================
# BINANCE HELPERS
# ============================================================

def binance_get(endpoint, params=None):
    url = BINANCE_BASE_URL + endpoint

    try:
        response = requests.get(
            url,
            params=params,
            timeout=15
        )
        response.raise_for_status()
        return response.json()

    except Exception as e:
        print(f"[BINANCE ERROR] {endpoint}: {e}")
        return None


# ============================================================
# GET ALL SPOT USDT SYMBOLS
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
# $20M VOLUME FILTER
# ============================================================

def get_qualified_symbols():
    global volume_cache

    now = time.time()

    if now - volume_cache["timestamp"] < VOLUME_CACHE_SECONDS:
        return volume_cache["symbols"]

    data = binance_get("/api/v3/ticker/24hr")

    if not data:
        return volume_cache["symbols"]

    qualified = []

    for item in data:

        symbol = item.get("symbol", "")

        if not symbol.endswith("USDT"):
            continue

        try:
            quote_volume = float(item.get("quoteVolume", 0))
        except:
            continue

        if quote_volume >= MIN_QUOTE_VOLUME_24H:
            qualified.append(symbol)

    volume_cache["timestamp"] = now
    volume_cache["symbols"] = qualified

    return qualified


# ============================================================
# CLOSED KLINES
# ============================================================

def get_closed_klines(symbol, interval, limit=150):

    data = binance_get(
        "/api/v3/klines",
        {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        }
    )

    if not data:
        return []

    now_ms = int(time.time() * 1000)

    closed = []

    for candle in data:

        close_time = int(candle[6])

        # Candle must be completely closed
        if close_time < now_ms:
            closed.append(candle)

    return closed


# ============================================================
# CURRENT PRICE
# ============================================================

def get_current_price(symbol):

    data = binance_get(
        "/api/v3/ticker/price",
        {
            "symbol": symbol
        }
    )

    if not data:
        return None

    try:
        return float(data["price"])
    except:
        return None


# ============================================================
# EMA
# ============================================================

def calculate_ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    ema = sum(values[:period]) / period

    for price in values[period:]:
        ema = (price - ema) * multiplier + ema

    return ema


# ============================================================
# 1H BULLISH TREND
# ============================================================

def is_bullish_1h_trend(symbol):

    candles = get_closed_klines(
        symbol,
        "1h",
        150
    )

    if len(candles) < EMA_SLOW:
        return False

    closes = [
        float(candle[4])
        for candle in candles
    ]

    current_close = closes[-1]

    ema20 = calculate_ema(
        closes,
        EMA_FAST
    )

    ema50 = calculate_ema(
        closes,
        EMA_MID
    )

    ema100 = calculate_ema(
        closes,
        EMA_SLOW
    )

    if None in (ema20, ema50, ema100):
        return False

    bullish = (
        current_close > ema20
        and ema20 > ema50
        and ema50 > ema100
    )

    print(
        f"[TREND] {symbol} | "
        f"Close={current_close:.8f} | "
        f"EMA20={ema20:.8f} | "
        f"EMA50={ema50:.8f} | "
        f"EMA100={ema100:.8f} | "
        f"BULLISH={bullish}"
    )

    return bullish


# ============================================================
# FVG BASELINE
# ============================================================

def get_latest_closed_candle_open_time(symbol, interval):

    candles = get_closed_klines(
        symbol,
        interval,
        5
    )

    if not candles:
        return None

    return int(candles[-1][0])


def initialize_symbol_cycle(symbol):

    """
    Called when a symbol newly enters the $20M filter
    or when the previous FVG cycle has finished.

    Important:
    Old FVGs are ignored.

    The bot waits for a NEW FVG after this point.
    """

    for interval in FVG_INTERVALS:

        latest_open = get_latest_closed_candle_open_time(
            symbol,
            interval
        )

        if latest_open is not None:

            fvg_search_start[
                (symbol, interval)
            ] = latest_open

            last_processed_fvg[
                (symbol, interval)
            ] = latest_open


# ============================================================
# BEARISH FVG DETECTION
# ============================================================

def find_new_bearish_fvg(symbol, interval):

    candles = get_closed_klines(
        symbol,
        interval,
        100
    )

    if len(candles) < 3:
        return None

    last_processed = last_processed_fvg.get(
        (symbol, interval),
        0
    )

    search_start = fvg_search_start.get(
        (symbol, interval),
        0
    )

    # --------------------------------------------------------
    # Search from newest 3-candle formation backwards
    # --------------------------------------------------------

    for i in range(len(candles) - 1, 1, -1):

        c1 = candles[i - 2]
        c2 = candles[i - 1]
        c3 = candles[i]

        c1_open_time = int(c1[0])
        c2_open_time = int(c2[0])
        c3_open_time = int(c3[0])

        # Only NEW FVGs are allowed
        if c3_open_time <= last_processed:
            continue

        if c3_open_time <= search_start:
            continue

        # ----------------------------------------------------
        # Candle 1
        # ----------------------------------------------------

        c1_open = float(c1[1])
        c1_high = float(c1[2])
        c1_low = float(c1[3])
        c1_close = float(c1[4])

        # Candle 1 must be bearish
        if c1_close >= c1_open:
            continue

        # ----------------------------------------------------
        # Candle 2
        # ----------------------------------------------------

        c2_open = float(c2[1])
        c2_high = float(c2[2])
        c2_low = float(c2[3])
        c2_close = float(c2[4])

        # Candle 2 must be bearish
        if c2_close >= c2_open:
            continue

        # Candle 2 BODY only
        body_low = min(
            c2_open,
            c2_close
        )

        body_high = max(
            c2_open,
            c2_close
        )

        body_size = abs(
            c2_open - c2_close
        )

        if body_size <= 0:
            continue

        # ----------------------------------------------------
        # Candle 3
        # ----------------------------------------------------

        c3_open = float(c3[1])
        c3_high = float(c3[2])
        c3_low = float(c3[3])
        c3_close = float(c3[4])

        # ----------------------------------------------------
        # Bearish FVG
        #
        # C1 Low > C3 High
        # ----------------------------------------------------

        if c1_low <= c3_high:
            continue

        fvg_low = c3_high
        fvg_high = c1_low

        fvg_size = fvg_high - fvg_low

        if fvg_size <= 0:
            continue

        # ----------------------------------------------------
        # FVG MUST BE COMPLETELY INSIDE CANDLE 2 BODY
        #
        # It may be anywhere inside the body:
        # upper part / middle / lower part.
        # ----------------------------------------------------

        if fvg_low < body_low:
            continue

        if fvg_high > body_high:
            continue

        # ----------------------------------------------------
        # FVG SIZE MUST BE AT LEAST 50% OF CANDLE 2 BODY
        #
        # This is NOT "above the upper half".
        #
        # The FVG can be anywhere inside the Open-Close body.
        # Its SIZE must simply be >= 50% of the body.
        # ----------------------------------------------------

        fvg_ratio = fvg_size / body_size

        if fvg_ratio < FVG_MIN_RATIO:
            continue

        # ----------------------------------------------------
        # VALID NEW BEARISH FVG
        # ----------------------------------------------------

        return {
            "symbol": symbol,
            "interval": interval,

            "c1_open_time": c1_open_time,
            "c2_open_time": c2_open_time,
            "c3_open_time": c3_open_time,

            "c1_low": c1_low,

            "c2_open": c2_open,
            "c2_close": c2_close,

            "c3_high": c3_high,

            "fvg_low": fvg_low,
            "fvg_high": fvg_high,

            "fvg_size": fvg_size,
            "c2_body_size": body_size,
            "fvg_ratio": fvg_ratio,

            "target": c3_high * (
                1 - TARGET_PERCENT / 100
            )
        }

    return None


# ============================================================
# FIND BEARISH FVG
#
# 15M FIRST
# THEN 1H
# ============================================================

def find_bearish_fvg(symbol):

    for interval in FVG_INTERVALS:

        fvg = find_new_bearish_fvg(
            symbol,
            interval
        )

        if fvg:

            print(
                f"[FVG FOUND] {symbol} | "
                f"{interval} | "
                f"FVG={fvg['fvg_low']:.8f} - "
                f"{fvg['fvg_high']:.8f} | "
                f"Ratio={fvg['fvg_ratio'] * 100:.2f}%"
            )

            return fvg

    return None


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }

    try:

        requests.post(
            url,
            data=payload,
            timeout=10
        )

    except Exception as e:

        print(
            f"[TELEGRAM ERROR] {e}"
        )


# ============================================================
# ACTIVATE FVG
# ============================================================

def activate_fvg(symbol, fvg):

    active_fvgs[symbol] = fvg

    key = (
        symbol,
        fvg["interval"]
    )

    # Mark Candle 3 as processed immediately.
    # This prevents the same FVG from being selected again.
    last_processed_fvg[key] = (
        fvg["c3_open_time"]
    )

    print(
        f"[FVG ACTIVE] {symbol} | "
        f"TF={fvg['interval']} | "
        f"FVG={fvg['fvg_low']:.8f} - "
        f"{fvg['fvg_high']:.8f} | "
        f"Target={fvg['target']:.8f}"
    )


# ============================================================
# RESET AFTER TARGET / CANCEL
# ============================================================

def reset_symbol_cycle(symbol):

    active_fvgs.pop(
        symbol,
        None
    )

    # Start completely from a fresh cycle.
    #
    # Old FVGs are ignored.
    # The next process begins again with:
    #
    # $20M -> 1H trend -> NEW FVG
    #

    initialize_symbol_cycle(
        symbol
    )

    print(
        f"[RESET] {symbol} -> "
        f"1H trend check from fresh cycle"
    )


# ============================================================
# MONITOR ACTIVE FVG
# ============================================================

def monitor_active_fvg(symbol):

    fvg = active_fvgs.get(symbol)

    if not fvg:
        return False

    current_price = get_current_price(
        symbol
    )

    if current_price is None:
        return True

    target = fvg["target"]
    c3_high = fvg["c3_high"]

    print(
        f"[MONITOR] {symbol} | "
        f"Price={current_price:.8f} | "
        f"Target={target:.8f} | "
        f"C3 High={c3_high:.8f}"
    )

    # --------------------------------------------------------
    # TARGET HIT
    # --------------------------------------------------------

    if current_price <= target:

        message = (
            "🔻 BEARISH FVG TARGET\n\n"
            f"Symbol: {symbol}\n"
            f"Timeframe: {fvg['interval']}\n"
            f"FVG: {fvg['fvg_low']:.8f} - "
            f"{fvg['fvg_high']:.8f}\n"
            f"Candle 3 High: {c3_high:.8f}\n"
            f"Target: {target:.8f}\n"
            f"Current: {current_price:.8f}\n"
            f"Target: -{TARGET_PERCENT}%"
        )

        send_telegram(message)

        print(
            f"[TARGET HIT] {symbol}"
        )

        reset_symbol_cycle(
            symbol
        )

        return True

    # --------------------------------------------------------
    # CANDLE 3 HIGH BROKEN
    # --------------------------------------------------------

    if current_price > c3_high:

        print(
            f"[FVG CANCELLED] {symbol} | "
            f"Price broke Candle 3 High"
        )

        reset_symbol_cycle(
            symbol
        )

        return True

    # --------------------------------------------------------
    # FVG STILL ACTIVE
    # --------------------------------------------------------

    return True


# ============================================================
# PROCESS ONE SYMBOL
# ============================================================

def process_symbol(symbol):

    # ========================================================
    # STEP 1
    # $20M QUALIFICATION
    # ========================================================

    if symbol not in qualified_symbols:

        print(
            f"[NEW $20M COIN] {symbol}"
        )

        qualified_symbols.add(
            symbol
        )

        # Ignore old historical FVGs.
        # Start waiting for a NEW FVG.
        initialize_symbol_cycle(
            symbol
        )

    # ========================================================
    # STEP 2
    # ACTIVE FVG?
    #
    # If yes, do NOT search for another FVG.
    # ========================================================

    if symbol in active_fvgs:

        monitor_active_fvg(
            symbol
        )

        return

    # ========================================================
    # STEP 3
    # 1H BULLISH TREND
    #
    # IMPORTANT:
    # FVG SEARCH DOES NOT HAPPEN BEFORE THIS.
    # ========================================================

    bullish = is_bullish_1h_trend(
        symbol
    )

    if not bullish:

        print(
            f"[WAIT TREND] {symbol} | "
            f"1H bullish trend not confirmed"
        )

        return

    # ========================================================
    # STEP 4
    # ONLY AFTER BULLISH TREND:
    # SEARCH FOR BEARISH FVG
    # ========================================================

    fvg = find_bearish_fvg(
        symbol
    )

    if not fvg:

        print(
            f"[NO NEW BEARISH FVG] {symbol}"
        )

        return

    # ========================================================
    # STEP 5
    # ACTIVATE FVG
    # ========================================================

    activate_fvg(
        symbol,
        fvg
    )


# ============================================================
# REMOVE SYMBOLS THAT NO LONGER QUALIFY
# ============================================================

def cleanup_symbols(current_qualified):

    current_set = set(
        current_qualified
    )

    old_symbols = (
        qualified_symbols
        - current_set
    )

    for symbol in old_symbols:

        print(
            f"[REMOVED < $20M] {symbol}"
        )

        qualified_symbols.discard(
            symbol
        )

        # If there is no active FVG,
        # clear its cycle state.
        if symbol not in active_fvgs:

            for interval in FVG_INTERVALS:

                fvg_search_start.pop(
                    (symbol, interval),
                    None
                )

                last_processed_fvg.pop(
                    (symbol, interval),
                    None
                )


# ============================================================
# MAIN LOOP
# ============================================================

def main():

    print("=" * 70)
    print("BINANCE SPOT BEARISH FVG ALERT BOT")
    print("=" * 70)

    print(
        f"Minimum 24H Volume: "
        f"${MIN_QUOTE_VOLUME_24H:,.0f}"
    )

    print(
        f"FVG Minimum Body Ratio: "
        f"{FVG_MIN_RATIO * 100:.0f}%"
    )

    print(
        f"Target: "
        f"{TARGET_PERCENT}% below Candle 3 High"
    )

    print(
        "FVG Priority: 15M -> 1H"
    )

    print("=" * 70)

    while True:

        try:

            # =================================================
            # GET CURRENT $20M+ COINS
            # =================================================

            qualified = get_qualified_symbols()

            if not qualified:

                print(
                    "[NO QUALIFIED SYMBOLS]"
                )

                time.sleep(
                    SCAN_INTERVAL_SECONDS
                )

                continue

            cleanup_symbols(
                qualified
            )

            print(
                f"\n[SCAN] "
                f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} | "
                f"Qualified={len(qualified)}"
            )

            # =================================================
            # PROCESS SYMBOLS
            # =================================================

            for symbol in qualified:

                try:

                    process_symbol(
                        symbol
                    )

                except Exception as e:

                    print(
                        f"[SYMBOL ERROR] "
                        f"{symbol}: {e}"
                    )

                time.sleep(0.05)

        except Exception as e:

            print(
                f"[MAIN LOOP ERROR] {e}"
            )

        time.sleep(
            SCAN_INTERVAL_SECONDS
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
