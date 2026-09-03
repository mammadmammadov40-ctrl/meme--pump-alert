import os
import time
import requests
from datetime import datetime, timezone


# ============================================================
# BINANCE BEARISH FVG ALERT BOT
# ============================================================
#
# TELEGRAM ONLY:
#   1) FVG fully qualifies -> SIGNAL
#   2) 1.7% target reached -> TARGET HIT
#   3) Target not reached + C3 High broken -> CANCELLED
#
# NO TELEGRAM MESSAGES FOR:
#   - $20M volume passed
#   - Trend waiting
#   - Trend confirmed
#   - Bot started
#   - Telegram test
#
# ============================================================


# ============================================================
# SETTINGS
# ============================================================

BINANCE_BASE_URL = "https://data-api.binance.vision"

MIN_QUOTE_VOLUME_24H = 20_000_000

FVG_MIN_RATIO = 0.50

TARGET_PERCENT = 1.7

SCAN_INTERVAL_SECONDS = 60

FVG_INTERVALS = ["15m", "1h"]

EMA_FAST = 20
EMA_MID = 50
EMA_SLOW = 100

VOLUME_CACHE_SECONDS = 300


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
).strip()


def telegram_config_ok():
    return bool(
        TELEGRAM_BOT_TOKEN
        and TELEGRAM_CHAT_ID
    )


def send_telegram(message):
    """
    Sends Telegram message.

    Only final signal / target / cancellation
    functions should call this.
    """

    if not telegram_config_ok():
        print("[TELEGRAM] Configuration missing.")
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=15
        )

        if response.ok:
            print("[TELEGRAM] Message sent.")
            return True

        print(
            f"[TELEGRAM ERROR] "
            f"{response.status_code}: "
            f"{response.text}"
        )

    except Exception as e:
        print(
            f"[TELEGRAM ERROR] {e}"
        )

    return False


def test_telegram_connection():
    """
    Silent Telegram connection test.

    IMPORTANT:
    This does NOT send a Telegram message.
    """

    if not telegram_config_ok():
        print(
            "[TELEGRAM] Token or Chat ID missing."
        )
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/getMe"
    )

    try:
        response = requests.get(
            url,
            timeout=15
        )

        if response.ok:
            data = response.json()

            if data.get("ok"):
                username = (
                    data.get("result", {})
                    .get("username", "unknown")
                )

                print(
                    f"[TELEGRAM] Connected: "
                    f"@{username}"
                )

                return True

        print(
            f"[TELEGRAM ERROR] "
            f"{response.status_code}: "
            f"{response.text}"
        )

    except Exception as e:
        print(
            f"[TELEGRAM ERROR] {e}"
        )

    return False


# ============================================================
# STATE
# ============================================================

active_fvgs = {}

qualified_symbols = set()

last_processed_fvg = {}

fvg_search_start = {}

volume_cache = {}


# ============================================================
# BINANCE REQUEST
# ============================================================

def binance_get(endpoint, params=None):

    url = BINANCE_BASE_URL + endpoint

    try:
        response = requests.get(
            url,
            params=params,
            timeout=20
        )

        response.raise_for_status()

        return response.json()

    except Exception as e:

        print(
            f"[BINANCE ERROR] "
            f"{endpoint}: {e}"
        )

        return None


# ============================================================
# SYMBOLS
# ============================================================

def get_spot_usdt_symbols():

    data = binance_get(
        "/api/v3/exchangeInfo"
    )

    if not data:
        return []

    symbols = []

    for item in data.get(
        "symbols",
        []
    ):

        if (
            item.get("status") == "TRADING"
            and item.get("quoteAsset") == "USDT"
            and item.get("isSpotTradingAllowed") is True
        ):
            symbols.append(
                item["symbol"]
            )

    return symbols


# ============================================================
# 24H VOLUME
# ============================================================

def get_24h_volume(symbol):

    now = time.time()

    cached = volume_cache.get(symbol)

    if cached:

        cached_time, cached_volume = cached

        if (
            now - cached_time
            < VOLUME_CACHE_SECONDS
        ):
            return cached_volume

    data = binance_get(
        "/api/v3/ticker/24hr",
        {
            "symbol": symbol
        }
    )

    if not data:
        return None

    try:

        volume = float(
            data.get(
                "quoteVolume",
                0
            )
        )

        volume_cache[symbol] = (
            now,
            volume
        )

        return volume

    except Exception:
        return None


def get_qualified_symbols():

    symbols = get_spot_usdt_symbols()

    qualified = []

    for symbol in symbols:

        volume = get_24h_volume(
            symbol
        )

        if (
            volume is not None
            and volume >= MIN_QUOTE_VOLUME_24H
        ):

            qualified.append(symbol)

    return qualified


# ============================================================
# KLINES
# ============================================================

def get_closed_klines(
    symbol,
    interval,
    limit=200
):

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

    now_ms = int(
        time.time() * 1000
    )

    closed = []

    for candle in data:

        close_time = int(
            candle[6]
        )

        # Only closed candles
        if close_time <= now_ms:
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
        return float(
            data["price"]
        )

    except Exception:
        return None


# ============================================================
# EMA
# ============================================================

def calculate_ema(values, period):

    if len(values) < period:
        return None

    multiplier = (
        2 / (period + 1)
    )

    ema = sum(
        values[:period]
    ) / period

    for price in values[period:]:

        ema = (
            (price - ema)
            * multiplier
        ) + ema

    return ema


# ============================================================
# 1H BULLISH TREND
# ============================================================

def bullish_1h_trend(symbol):

    candles = get_closed_klines(
        symbol,
        "1h",
        150
    )

    if len(candles) < EMA_SLOW:
        return False

    closes = [
        float(c[4])
        for c in candles
    ]

    close = closes[-1]

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

    if (
        ema20 is None
        or ema50 is None
        or ema100 is None
    ):
        return False

    result = (
        close > ema20
        and ema20 > ema50
        and ema50 > ema100
    )

    if result:

        print(
            f"[TREND CONFIRMED] "
            f"{symbol} | "
            f"Close={close:.8f} | "
            f"EMA20={ema20:.8f} | "
            f"EMA50={ema50:.8f} | "
            f"EMA100={ema100:.8f}"
        )

    return result


# ============================================================
# FVG INITIALIZATION
# ============================================================

def initialize_symbol_cycle(symbol):

    for interval in FVG_INTERVALS:

        candles = get_closed_klines(
            symbol,
            interval,
            200
        )

        if not candles:
            continue

        latest_candle = candles[-1]

        fvg_search_start[
            (symbol, interval)
        ] = int(
            latest_candle[0]
        )

        last_processed_fvg[
            (symbol, interval)
        ] = int(
            latest_candle[0]
        )


# ============================================================
# FIND NEW BEARISH FVG
# ============================================================

def find_new_bearish_fvg(
    symbol,
    interval
):

    candles = get_closed_klines(
        symbol,
        interval,
        200
    )

    if len(candles) < 3:
        return None

    # Newest Candle 3 first
    for i in range(
        len(candles) - 1,
        1,
        -1
    ):

        c1 = candles[i - 2]
        c2 = candles[i - 1]
        c3 = candles[i]

        c3_open_time = int(
            c3[0]
        )

        # Ignore already processed candle
        last_time = last_processed_fvg.get(
            (symbol, interval),
            0
        )

        if c3_open_time <= last_time:
            continue

        # ----------------------------------------------------
        # Candle values
        # ----------------------------------------------------

        c1_open = float(c1[1])
        c1_high = float(c1[2])
        c1_low = float(c1[3])
        c1_close = float(c1[4])

        c2_open = float(c2[1])
        c2_high = float(c2[2])
        c2_low = float(c2[3])
        c2_close = float(c2[4])

        c3_high = float(c3[2])
        c3_low = float(c3[3])
        c3_close = float(c3[4])

        # ----------------------------------------------------
        # C1 bearish
        # ----------------------------------------------------

        if c1_close >= c1_open:
            continue

        # ----------------------------------------------------
        # C2 bearish
        # ----------------------------------------------------

        if c2_close >= c2_open:
            continue

        # ----------------------------------------------------
        # Bearish FVG
        #
        # C1 Low > C3 High
        # ----------------------------------------------------

        if c1_low <= c3_high:
            continue

        fvg_low = c3_high
        fvg_high = c1_low

        fvg_size = (
            fvg_high - fvg_low
        )

        if fvg_size <= 0:
            continue

        # ----------------------------------------------------
        # Candle 2 body
        # ----------------------------------------------------

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
        # FVG must be fully inside Candle 2 body
        # ----------------------------------------------------

        if fvg_low < body_low:
            continue

        if fvg_high > body_high:
            continue

        # ----------------------------------------------------
        # FVG >= 50% of Candle 2 body
        # ----------------------------------------------------

        fvg_ratio = (
            fvg_size / body_size
        )

        if fvg_ratio < FVG_MIN_RATIO:
            continue

        # ----------------------------------------------------
        # TARGET
        #
        # 1.7% below Candle 3 High
        # ----------------------------------------------------

        target = (
            c3_high
            * (1 - TARGET_PERCENT / 100)
        )

        return {
            "interval": interval,

            "c1_open_time": int(c1[0]),
            "c2_open_time": int(c2[0]),
            "c3_open_time": int(c3[0]),

            "c1_high": c1_high,
            "c1_low": c1_low,

            "c2_high": c2_high,
            "c2_low": c2_low,

            "c3_high": c3_high,
            "c3_low": c3_low,
            "c3_close": c3_close,

            "fvg_low": fvg_low,
            "fvg_high": fvg_high,
            "fvg_size": fvg_size,
            "fvg_ratio": fvg_ratio,

            "target": target,
        }

    return None


def find_bearish_fvg(symbol):

    for interval in FVG_INTERVALS:

        fvg = find_new_bearish_fvg(
            symbol,
            interval
        )

        if fvg is not None:

            return fvg

    return None


# ============================================================
# ACTIVATE FVG
# ============================================================

def activate_fvg(
    symbol,
    fvg
):

    active_fvgs[symbol] = fvg

    interval = fvg["interval"]

    last_processed_fvg[
        (symbol, interval)
    ] = fvg["c3_open_time"]

    volume = get_24h_volume(
        symbol
    )

    if volume is None:
        volume = 0

    signal_time = datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )

    target = fvg["target"]
    c3_high = fvg["c3_high"]

    message = (
        "🔻 <b>BEARISH FVG SIGNAL</b>\n\n"

        f"<b>Symbol:</b> {symbol}\n"
        f"<b>Timeframe:</b> {interval}\n"
        f"<b>24H Volume:</b> "
        f"${volume:,.0f}\n\n"

        f"<b>FVG:</b> "
        f"{fvg['fvg_low']:.8g} → "
        f"{fvg['fvg_high']:.8g}\n"

        f"<b>FVG Ratio:</b> "
        f"{fvg['fvg_ratio'] * 100:.1f}%\n\n"

        f"<b>C3 High / Cancel:</b> "
        f"{c3_high:.8g}\n"

        f"<b>Target -{TARGET_PERCENT}%:</b> "
        f"{target:.8g}\n\n"

        f"⏳ <b>1.7% target is now being tracked.</b>\n"
        f"<i>{signal_time}</i>"
    )

    # ONLY FINAL QUALIFIED SIGNAL
    send_telegram(message)

    print(
        f"[FVG ACTIVE] {symbol} | "
        f"{interval} | "
        f"Target={target:.8g} | "
        f"Cancel={c3_high:.8g}"
    )


# ============================================================
# RESET SYMBOL
# ============================================================

def reset_symbol_cycle(symbol):

    if symbol in active_fvgs:
        del active_fvgs[symbol]

    initialize_symbol_cycle(
        symbol
    )


# ============================================================
# MONITOR ACTIVE FVG
# ============================================================

def monitor_active_fvg(symbol):

    fvg = active_fvgs.get(
        symbol
    )

    if fvg is None:
        return

    price = get_current_price(
        symbol
    )

    if price is None:
        return

    target = fvg["target"]

    c3_high = fvg["c3_high"]

    # ========================================================
    # IMPORTANT:
    #
    # For bearish setup:
    #
    # TARGET = price reaches 1.7% BELOW C3 High
    #
    # CANCEL = price breaks ABOVE C3 High
    #
    # Target is checked FIRST.
    # ========================================================

    if price <= target:

        message = (
            "🎯 <b>BEARISH FVG TARGET HIT</b>\n\n"

            f"<b>Symbol:</b> {symbol}\n"
            f"<b>Timeframe:</b> {fvg['interval']}\n\n"

            f"<b>C3 High:</b> "
            f"{c3_high:.8g}\n"

            f"<b>Target -{TARGET_PERCENT}%:</b> "
            f"{target:.8g}\n"

            f"<b>Current Price:</b> "
            f"{price:.8g}\n\n"

            "✅ <b>1.7% target successfully reached.</b>"
        )

        send_telegram(
            message
        )

        print(
            f"[TARGET HIT] "
            f"{symbol} | "
            f"Price={price:.8g} | "
            f"Target={target:.8g}"
        )

        reset_symbol_cycle(
            symbol
        )

        return

    # ========================================================
    # CANCEL CONDITION
    #
    # 1.7% target was NOT reached
    # AND
    # Candle 3 High is broken
    # ========================================================

    if price > c3_high:

        message = (
            "❌ <b>BEARISH FVG CANCELLED</b>\n\n"

            f"<b>Symbol:</b> {symbol}\n"
            f"<b>Timeframe:</b> {fvg['interval']}\n\n"

            f"<b>C3 High / Cancel Level:</b> "
            f"{c3_high:.8g}\n"

            f"<b>Current Price:</b> "
            f"{price:.8g}\n\n"

            f"❌ <b>1.7% target was not reached.</b>\n"
            f"<b>Candle 3 High was broken.</b>"
        )

        send_telegram(
            message
        )

        print(
            f"[CANCELLED] "
            f"{symbol} | "
            f"Price={price:.8g} | "
            f"C3 High={c3_high:.8g}"
        )

        reset_symbol_cycle(
            symbol
        )

        return


# ============================================================
# PROCESS SYMBOL
# ============================================================

def process_symbol(symbol):

    # --------------------------------------------------------
    # If an active FVG exists, ONLY monitor it.
    # --------------------------------------------------------

    if symbol in active_fvgs:

        monitor_active_fvg(
            symbol
        )

        return

    # --------------------------------------------------------
    # Volume condition
    # --------------------------------------------------------

    volume = get_24h_volume(
        symbol
    )

    if (
        volume is None
        or volume < MIN_QUOTE_VOLUME_24H
    ):
        return

    # Console only
    print(
        f"[VOLUME QUALIFIED] "
        f"{symbol} | "
        f"${volume:,.0f}"
    )

    # --------------------------------------------------------
    # 1H bullish trend
    # --------------------------------------------------------

    if not bullish_1h_trend(
        symbol
    ):

        # NO TELEGRAM MESSAGE
        return

    # --------------------------------------------------------
    # Find Bearish FVG
    # --------------------------------------------------------

    fvg = find_bearish_fvg(
        symbol
    )

    if fvg is None:
        return

    # --------------------------------------------------------
    # ALL CONDITIONS PASSED
    #
    # This is the ONLY place where the initial
    # Telegram signal is sent.
    # --------------------------------------------------------

    activate_fvg(
        symbol,
        fvg
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print(
        "BINANCE BEARISH FVG ALERT BOT"
    )
    print("=" * 70)

    print(
        f"Min 24H Volume: "
        f"${MIN_QUOTE_VOLUME_24H:,.0f}"
    )

    print(
        f"FVG Minimum Ratio: "
        f"{FVG_MIN_RATIO * 100:.0f}%"
    )

    print(
        f"Target: "
        f"{TARGET_PERCENT}%"
    )

    print(
        f"Intervals: "
        f"{FVG_INTERVALS}"
    )

    print(
        f"Scan Interval: "
        f"{SCAN_INTERVAL_SECONDS}s"
    )

    print(
        f"Binance: "
        f"{BINANCE_BASE_URL}"
    )

    print("=" * 70)

    # --------------------------------------------------------
    # Silent Telegram connection test
    # NO MESSAGE SENT
    # --------------------------------------------------------

    test_telegram_connection()

    # --------------------------------------------------------
    # Initialize symbols
    # --------------------------------------------------------

    symbols = get_spot_usdt_symbols()

    if not symbols:

        print(
            "[FATAL] "
            "Could not get Binance symbols."
        )

        return

    print(
        f"[INIT] "
        f"Found {len(symbols)} Spot USDT symbols."
    )

    for symbol in symbols:

        try:

            initialize_symbol_cycle(
                symbol
            )

        except Exception as e:

            print(
                f"[INIT ERROR] "
                f"{symbol}: {e}"
            )

    # --------------------------------------------------------
    # MAIN LOOP
    # --------------------------------------------------------

    while True:

        cycle_start = time.time()

        print(
            "\n"
            + "=" * 70
        )

        print(
            f"[SCAN] "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )

        # ----------------------------------------------------
        # Find current $20M+ symbols
        # ----------------------------------------------------

        try:

            qualified = get_qualified_symbols()

            qualified_symbols.clear()

            qualified_symbols.update(
                qualified
            )

            print(
                f"[SCAN] "
                f"Qualified symbols: "
                f"{len(qualified)}"
            )

        except Exception as e:

            print(
                f"[QUALIFIED ERROR] "
                f"{e}"
            )

            qualified = list(
                qualified_symbols
            )

        # ----------------------------------------------------
        # Process qualified symbols
        # ----------------------------------------------------

        for symbol in qualified:

            try:

                process_symbol(
                    symbol
                )

            except Exception as e:

                print(
                    f"[PROCESS ERROR] "
                    f"{symbol}: {e}"
                )

            # Small delay to reduce API pressure
            time.sleep(0.05)

        # ----------------------------------------------------
        # IMPORTANT:
        #
        # Active FVGs must continue being monitored even if
        # their current volume later falls below $20M.
        # ----------------------------------------------------

        active_symbols = list(
            active_fvgs.keys()
        )

        for symbol in active_symbols:

            if symbol in qualified:
                continue

            try:

                monitor_active_fvg(
                    symbol
                )

            except Exception as e:

                print(
                    f"[ACTIVE MONITOR ERROR] "
                    f"{symbol}: {e}"
                )

            time.sleep(0.05)

        # ----------------------------------------------------
        # Wait until next scan
        # ----------------------------------------------------

        elapsed = (
            time.time()
            - cycle_start
        )

        sleep_time = max(
            1,
            SCAN_INTERVAL_SECONDS
            - elapsed
        )

        print(
            f"[SCAN COMPLETE] "
            f"Active FVGs: "
            f"{len(active_fvgs)} | "
            f"Next scan in "
            f"{sleep_time:.1f}s"
        )

        time.sleep(
            sleep_time
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "\n[BOT] Stopped."
        )

    except Exception as e:

        print(
            f"[FATAL] {e}"
        )

        raise
