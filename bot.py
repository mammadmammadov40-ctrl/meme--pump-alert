import os
import time
import requests
from datetime import datetime, timezone


# ============================================================
# CONFIG
# ============================================================

BINANCE_BASE_URL = "https://data-api.binance.vision"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

MIN_QUOTE_VOLUME_24H = 20_000_000

FVG_MIN_RATIO = 0.50

TARGET_PERCENT = 1.7

FVG_INTERVALS = ["15m", "1h"]

SCAN_SECONDS = 60


# ============================================================
# BOT START TIME
# ============================================================

BOT_START_TIME = time.time()
BOT_START_MS = int(BOT_START_TIME * 1000)


# ============================================================
# STATE
# ============================================================

active_setups = {}

processed_fvgs = set()


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0"
})


# ============================================================
# BINANCE REQUEST
# ============================================================

def binance_get(endpoint, params=None):

    url = BINANCE_BASE_URL + endpoint

    for attempt in range(3):

        try:

            response = session.get(
                url,
                params=params,
                timeout=20
            )

            if response.status_code in (418, 429):

                wait_time = 2 ** attempt

                print(
                    f"[BINANCE RATE LIMIT] "
                    f"Waiting {wait_time}s..."
                )

                time.sleep(wait_time)

                continue

            if response.status_code >= 500:

                wait_time = 2 ** attempt

                print(
                    f"[BINANCE SERVER ERROR] "
                    f"HTTP {response.status_code}, "
                    f"retry in {wait_time}s"
                )

                time.sleep(wait_time)

                continue

            response.raise_for_status()

            return response.json()

        except Exception as e:

            if attempt < 2:

                wait_time = 2 ** attempt

                print(
                    f"[BINANCE ERROR] "
                    f"{endpoint}: {e} | "
                    f"retry in {wait_time}s"
                )

                time.sleep(wait_time)

            else:

                print(
                    f"[BINANCE ERROR] "
                    f"{endpoint}: {e}"
                )

    return None


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN:

        print("[TELEGRAM ERROR] Bot token missing.")

        return False

    if not TELEGRAM_CHAT_ID:

        print("[TELEGRAM ERROR] Chat ID missing.")

        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }

    try:

        response = session.post(
            url,
            json=payload,
            timeout=15
        )

        response.raise_for_status()

        return True

    except Exception as e:

        print(f"[TELEGRAM ERROR] {e}")

        return False


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

    for item in data.get("symbols", []):

        if (
            item.get("status") == "TRADING"
            and item.get("quoteAsset") == "USDT"
            and item.get("isSpotTradingAllowed") is True
        ):

            symbols.append(item["symbol"])

    return symbols


# ============================================================
# 24H DATA
# ============================================================

def get_24h_data():

    data = binance_get(
        "/api/v3/ticker/24hr"
    )

    if not isinstance(data, list):

        return {}

    result = {}

    for item in data:

        symbol = item.get("symbol")

        if not symbol:
            continue

        try:

            quote_volume = float(
                item["quoteVolume"]
            )

            last_price = float(
                item["lastPrice"]
            )

            result[symbol] = {
                "quote_volume": quote_volume,
                "last_price": last_price
            }

        except (
            KeyError,
            TypeError,
            ValueError
        ):

            continue

    return result


# ============================================================
# KLINES
# ============================================================

def get_klines(symbol, interval, limit=1000):

    data = binance_get(
        "/api/v3/klines",
        {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        }
    )

    if not isinstance(data, list):

        return []

    return data


# ============================================================
# CANDLE CLOSED
# ============================================================

def candle_is_closed(candle):

    now_ms = int(
        time.time() * 1000
    )

    close_time = int(
        candle[6]
    )

    return close_time < now_ms


# ============================================================
# BEARISH FVG DETECTION
# ============================================================

def detect_bearish_fvg(candles):

    if len(candles) < 3:

        return None

    c1 = candles[-3]
    c2 = candles[-2]
    c3 = candles[-1]

    if not candle_is_closed(c3):

        return None

    c1_open = float(c1[1])
    c1_high = float(c1[2])
    c1_low = float(c1[3])
    c1_close = float(c1[4])

    c2_open = float(c2[1])
    c2_high = float(c2[2])
    c2_low = float(c2[3])
    c2_close = float(c2[4])

    c3_open = float(c3[1])
    c3_high = float(c3[2])
    c3_low = float(c3[3])
    c3_close = float(c3[4])

    # C1 bearish
    if not (c1_close < c1_open):

        return None

    # C2 bearish
    if not (c2_close < c2_open):

        return None

    # Bearish FVG
    if not (c1_low > c3_high):

        return None

    # FVG
    fvg_low = c3_high
    fvg_high = c1_low

    fvg_size = (
        fvg_high - fvg_low
    )

    if fvg_size <= 0:

        return None

    # C2 body
    c2_body_low = min(
        c2_open,
        c2_close
    )

    c2_body_high = max(
        c2_open,
        c2_close
    )

    c2_body_size = (
        c2_body_high -
        c2_body_low
    )

    if c2_body_size <= 0:

        return None

    # FVG fully inside C2 body
    if fvg_low < c2_body_low:

        return None

    if fvg_high > c2_body_high:

        return None

    # FVG must be at least 50% of C2 body
    minimum_fvg_size = (
        c2_body_size *
        FVG_MIN_RATIO
    )

    if fvg_size < minimum_fvg_size:

        return None

    c3_open_time = int(
        c3[0]
    )

    c3_close_time = int(
        c3[6]
    )

    return {

        "c1_open": c1_open,
        "c1_high": c1_high,
        "c1_low": c1_low,
        "c1_close": c1_close,

        "c2_open": c2_open,
        "c2_high": c2_high,
        "c2_low": c2_low,
        "c2_close": c2_close,

        "c2_body_low": c2_body_low,
        "c2_body_high": c2_body_high,
        "c2_body_size": c2_body_size,

        "c3_open": c3_open,
        "c3_high": c3_high,
        "c3_low": c3_low,
        "c3_close": c3_close,

        "fvg_low": fvg_low,
        "fvg_high": fvg_high,
        "fvg_size": fvg_size,

        "minimum_fvg_size":
            minimum_fvg_size,

        "c3_open_time":
            c3_open_time,

        "c3_close_time":
            c3_close_time
    }


# ============================================================
# FIND NEW FVG
#
# IMPORTANT:
# We do NOT use "last 6 candles".
#
# We scan historical candles returned by Binance,
# but ONLY FVGs whose C3 candle started AFTER BOT_START_MS
# are considered.
#
# Therefore:
# - FVG before bot startup = ignored
# - FVG after bot startup = eligible
# ============================================================

def find_new_fvg(symbol, interval):

    candles = get_klines(
        symbol,
        interval,
        1000
    )

    if len(candles) < 3:

        return None, "NOT_ENOUGH_CANDLES"

    # Only CLOSED candles
    closed_candles = [
        candle
        for candle in candles
        if candle_is_closed(candle)
    ]

    if len(closed_candles) < 3:

        return None, "NO_CLOSED_CANDLES"

    # --------------------------------------------------------
    # Scan from newest to oldest
    # --------------------------------------------------------

    for i in range(
        len(closed_candles) - 3,
        -1,
        -1
    ):

        three_candles = closed_candles[
            i:i + 3
        ]

        fvg = detect_bearish_fvg(
            three_candles
        )

        if not fvg:

            continue

        # ----------------------------------------------------
        # VERY IMPORTANT:
        # Ignore every FVG that existed before bot started.
        # ----------------------------------------------------

        if (
            fvg["c3_close_time"]
            <= BOT_START_MS
        ):

            continue

        fvg_id = (
            symbol,
            interval,
            fvg["c3_open_time"]
        )

        # Already alerted/processed
        if fvg_id in processed_fvgs:

            continue

        fvg["fvg_id"] = fvg_id

        return fvg, "VALID_FVG"

    return None, "NO_NEW_FVG"


# ============================================================
# CREATE SIGNAL
# ============================================================

def create_signal(
    symbol,
    interval,
    fvg,
    current_price,
    volume
):

    c3_high = fvg["c3_high"]

    target = (
        c3_high *
        (1 - TARGET_PERCENT / 100)
    )

    setup = {

        "symbol":
            symbol,

        "interval":
            interval,

        "c3_high":
            c3_high,

        "fvg_low":
            fvg["fvg_low"],

        "fvg_high":
            fvg["fvg_high"],

        "fvg_size":
            fvg["fvg_size"],

        "c2_open":
            fvg["c2_open"],

        "c2_close":
            fvg["c2_close"],

        "c2_body_size":
            fvg["c2_body_size"],

        "target":
            target,

        "signal_price":
            current_price,

        "c3_open_time":
            fvg["c3_open_time"],

        "c3_close_time":
            fvg["c3_close_time"],

        "fvg_id":
            fvg["fvg_id"]
    }

    message = (

        "🔔 BEARISH FVG SIGNAL\n\n"

        f"Symbol: {symbol}\n"
        f"Interval: {interval}\n\n"

        f"Price: {current_price:.8g}\n"
        f"C3 High: {c3_high:.8g}\n\n"

        f"FVG: "
        f"{fvg['fvg_low']:.8g} - "
        f"{fvg['fvg_high']:.8g}\n"

        f"C2 Body: "
        f"{fvg['c2_body_low']:.8g} - "
        f"{fvg['c2_body_high']:.8g}\n"

        f"C2 Body Size: "
        f"{fvg['c2_body_size']:.8g}\n"

        f"FVG Size: "
        f"{fvg['fvg_size']:.8g}\n"

        f"FVG Minimum: "
        f"{fvg['minimum_fvg_size']:.8g}\n\n"

        f"Target (-{TARGET_PERCENT}%): "
        f"{target:.8g}"
    )

    telegram_ok = send_telegram(
        message
    )

    if not telegram_ok:

        print(
            f"[SIGNAL ERROR] "
            f"{symbol} {interval} "
            f"Telegram failed."
        )

        return False

    active_setups[symbol] = setup

    processed_fvgs.add(
        fvg["fvg_id"]
    )

    print(
        f"[SIGNAL] "
        f"{symbol} {interval} "
        f"FVG="
        f"{fvg['fvg_low']:.8g}-"
        f"{fvg['fvg_high']:.8g} "
        f"Target="
        f"{target:.8g}"
    )

    return True


# ============================================================
# MONITOR ACTIVE SETUPS
# ============================================================

def monitor_active_setups(price_data):

    if not active_setups:

        return

    completed = []

    for symbol, setup in list(
        active_setups.items()
    ):

        ticker = price_data.get(
            symbol
        )

        if not ticker:

            continue

        current_price = (
            ticker["last_price"]
        )

        target = setup["target"]

        c3_high = setup["c3_high"]

        # ----------------------------------------------------
        # TARGET HIT
        # ----------------------------------------------------

        if current_price <= target:

            message = (

                "🎯 TARGET HIT\n\n"

                f"Symbol: {symbol}\n"

                f"Interval: "
                f"{setup['interval']}\n"

                f"Target: "
                f"{target:.8g}\n"

                f"Current Price: "
                f"{current_price:.8g}"
            )

            if send_telegram(message):

                print(
                    f"[TARGET HIT] "
                    f"{symbol} "
                    f"Price="
                    f"{current_price:.8g}"
                )

                completed.append(
                    symbol
                )

            continue

        # ----------------------------------------------------
        # FVG CANCELLED
        # ----------------------------------------------------

        if current_price > c3_high:

            message = (

                "❌ FVG CANCELLED\n\n"

                f"Symbol: {symbol}\n"

                f"Interval: "
                f"{setup['interval']}\n"

                f"C3 High: "
                f"{c3_high:.8g}\n"

                f"Current Price: "
                f"{current_price:.8g}"
            )

            if send_telegram(message):

                print(
                    f"[CANCELLED] "
                    f"{symbol} "
                    f"Price="
                    f"{current_price:.8g}"
                )

                completed.append(
                    symbol
                )

    # --------------------------------------------------------
    # Remove completed setups
    # --------------------------------------------------------

    for symbol in completed:

        active_setups.pop(
            symbol,
            None
        )


# ============================================================
# SCAN
# ============================================================

def scan():

    print()

    print("=" * 75)

    now_text = (
        datetime.now(timezone.utc)
        .strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
    )

    print(
        f"[SCAN] {now_text}"
    )

    # --------------------------------------------------------
    # SYMBOLS
    # --------------------------------------------------------

    symbols = get_spot_usdt_symbols()

    if not symbols:

        print(
            "[ERROR] "
            "No Spot USDT symbols."
        )

        return

    print(
        f"[SYMBOLS] "
        f"{len(symbols)} "
        f"Spot USDT symbols"
    )

    # --------------------------------------------------------
    # 24H DATA
    # --------------------------------------------------------

    ticker_data = get_24h_data()

    if not ticker_data:

        print(
            "[ERROR] "
            "No 24H ticker data."
        )

        return

    # --------------------------------------------------------
    # MONITOR ACTIVE FVGs FIRST
    # --------------------------------------------------------

    monitor_active_setups(
        ticker_data
    )

    volume_count = 0

    fvg_checked_count = 0

    volume_symbols = []

    # --------------------------------------------------------
    # SYMBOL LOOP
    # --------------------------------------------------------

    for symbol in symbols:

        ticker = ticker_data.get(
            symbol
        )

        if not ticker:

            continue

        volume = ticker[
            "quote_volume"
        ]

        current_price = ticker[
            "last_price"
        ]

        # ----------------------------------------------------
        # VOLUME FILTER
        # ----------------------------------------------------

        if volume < MIN_QUOTE_VOLUME_24H:

            continue

        volume_count += 1

        volume_symbols.append(
            symbol
        )

        # ----------------------------------------------------
        # ONE ACTIVE FVG PER SYMBOL
        # ----------------------------------------------------

        if symbol in active_setups:

            continue

        fvg_found = False

        # ----------------------------------------------------
        # FVG 15m / 1h
        # ----------------------------------------------------

        for interval in FVG_INTERVALS:

            fvg_checked_count += 1

            fvg, reason = find_new_fvg(
                symbol,
                interval
            )

            if fvg:

                ratio = (
                    fvg["fvg_size"]
                    /
                    fvg["c2_body_size"]
                    * 100
                )

                print(

                    f"[FVG PASS] "
                    f"{symbol} "
                    f"{interval} | "

                    f"FVG="
                    f"{fvg['fvg_low']:.8g}-"
                    f"{fvg['fvg_high']:.8g} | "

                    f"C2 Body="
                    f"{fvg['c2_body_size']:.8g} | "

                    f"FVG/Body="
                    f"{ratio:.2f}%"
                )

                success = create_signal(

                    symbol=symbol,

                    interval=interval,

                    fvg=fvg,

                    current_price=current_price,

                    volume=volume
                )

                if success:

                    fvg_found = True

                    break

            else:

                print(
                    f"[FVG FAIL] "
                    f"{symbol} "
                    f"{interval} "
                    f"-> {reason}"
                )

        if not fvg_found:

            print(
                f"[NO SIGNAL] "
                f"{symbol} "
                f"-> no valid new FVG"
            )

    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------

    print()

    print(
        "--------------- SCAN SUMMARY ---------------"
    )

    print(
        f"[VOLUME QUALIFIED] "
        f"{volume_count} symbols"
    )

    if volume_symbols:

        print(
            "[VOLUME COINS] "
            +
            ", ".join(
                volume_symbols
            )
        )

    print(
        f"[FVG CHECKED] "
        f"{fvg_checked_count} checks"
    )

    print(
        f"[ACTIVE FVGs] "
        f"{len(active_setups)}"
    )

    print(
        "---------------------------------------------"
    )


# ============================================================
# STARTUP INFO
# ============================================================

def print_startup_info():

    bot_start_text = (
        datetime.fromtimestamp(
            BOT_START_TIME,
            tz=timezone.utc
        )
        .strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
    )

    print()

    print("=" * 75)

    print(
        "BINANCE BEARISH FVG "
        "LIVE ALERT BOT"
    )

    print("=" * 75)

    print(
        f"Bot start: "
        f"{bot_start_text}"
    )

    print(
        f"Volume >= "
        f"${MIN_QUOTE_VOLUME_24H:,.0f}"
    )

    print(
        "FVG intervals: "
        "15m, 1h"
    )

    print(
        "FVG minimum: "
        "50% of C2 Open-Close body"
    )

    print(
        "FVG position: "
        "anywhere inside C2 body"
    )

    print(
        f"Target: "
        f"{TARGET_PERCENT}% "
        f"below C3 High"
    )

    print(
        f"Scan: every "
        f"{SCAN_SECONDS} seconds"
    )

    print(
        "Pre-start FVGs: "
        "IGNORED"
    )

    print(
        "Post-start FVG detection: "
        "ENABLED"
    )

    print(
        "EMA/Trend filter: "
        "DISABLED"
    )

    print(
        "One active FVG per symbol: "
        "ENABLED"
    )

    print("=" * 75)

    print()


# ============================================================
# MAIN
# ============================================================

def main():

    print_startup_info()

    if not TELEGRAM_BOT_TOKEN:

        print(
            "[WARNING] "
            "TELEGRAM_BOT_TOKEN "
            "is missing."
        )

    if not TELEGRAM_CHAT_ID:

        print(
            "[WARNING] "
            "TELEGRAM_CHAT_ID "
            "is missing."
        )

    while True:

        scan_start = time.time()

        try:

            scan()

        except Exception as e:

            print(
                f"[SCAN ERROR] {e}"
            )

        elapsed = (
            time.time() -
            scan_start
        )

        sleep_time = max(
            1,
            SCAN_SECONDS - elapsed
        )

        time.sleep(
            sleep_time
        )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
