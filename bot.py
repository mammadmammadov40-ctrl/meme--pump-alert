import os
import time
import requests
from datetime import datetime, timezone


# ============================================================
# BINANCE BEARISH FVG LIVE ALERT BOT
# ============================================================

BINANCE_BASE_URL = "https://data-api.binance.vision"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()


# ============================================================
# SETTINGS
# ============================================================

MIN_QUOTE_VOLUME_24H = 20_000_000

FVG_MIN_RATIO = 0.50
TARGET_PERCENT = 1.7

FVG_INTERVALS = ["15m", "1h"]

EMA_FAST = 20
EMA_MID = 50
EMA_SLOW = 100

EMA_CANDLES = 120

SCAN_SECONDS = 60

# FVG üçün yalnız son bir neçə candle lazımdır.
# 120 candle FVG üçün istifadə olunmur.
FVG_LOOKBACK = 6


# ============================================================
# BOT START TIME
# ============================================================

BOT_START_TIME = time.time()
BOT_START_MS = int(BOT_START_TIME * 1000)


# ============================================================
# ACTIVE SETUPS
# ============================================================
#
# Hər symbol üçün eyni anda maksimum 1 aktiv FVG.
#
# {
#     "BTCUSDT": {
#         "symbol": "BTCUSDT",
#         "interval": "15m",
#         "c3_time": ...,
#         "c3_high": ...,
#         "fvg_low": ...,
#         "fvg_high": ...,
#         "target": ...
#     }
# }
#

active_setups = {}


# Eyni FVG-ni təkrar signal etməmək üçün
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

    try:
        response = session.get(
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
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN:
        print("[TELEGRAM] Bot token is missing.")
        return False

    if not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Chat ID is missing.")
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
        print(
            f"[TELEGRAM ERROR] {e}"
        )

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
            symbols.append(
                item["symbol"]
            )

    return symbols


# ============================================================
# 24H VOLUME + CURRENT PRICE
# ============================================================

def get_24h_data():
    """
    Bir request ilə bütün Binance Spot ticker-lərini alır.

    quoteVolume:
        24H USDT volume

    lastPrice:
        cari canlı qiymət
    """

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

def get_klines(symbol, interval, limit):
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
# EMA
# ============================================================

def calculate_ema(values, period):
    if len(values) < period:
        return None

    # Sadə başlanğıc EMA
    ema = sum(
        values[:period]
    ) / period

    multiplier = 2 / (
        period + 1
    )

    for value in values[period:]:
        ema = (
            (value - ema)
            * multiplier
            + ema
        )

    return ema


# ============================================================
# LIVE 1H TREND
# ============================================================

def check_live_trend(symbol, current_price):
    """
    120 ədəd 1H candle götürür.

    Əgər son 1H candle hələ açıqdırsa,
    onun Close qiyməti əvəzinə CURRENT PRICE
    istifadə olunur.

    Sonra:

        Current Price > EMA20 > EMA50 > EMA100

    """

    candles = get_klines(
        symbol,
        "1h",
        EMA_CANDLES
    )

    if len(candles) < EMA_CANDLES:
        return False, None

    closes = []

    now_ms = int(
        time.time() * 1000
    )

    for candle in candles:

        close = float(
            candle[4]
        )

        close_time = int(
            candle[6]
        )

        # Son candle hələ açıqdırsa
        # canlı qiyməti onun close-u kimi istifadə et.
        if close_time >= now_ms:
            close = current_price

        closes.append(close)

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
        return False, None

    trend_ok = (
        current_price > ema20
        and ema20 > ema50
        and ema50 > ema100
    )

    return trend_ok, {
        "price": current_price,
        "ema20": ema20,
        "ema50": ema50,
        "ema100": ema100
    }


# ============================================================
# CANDLE CLOSED CHECK
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
    """
    candles:
        yalnız son bir neçə candle.

    Pattern:

        C1 bearish
        C2 bearish
        C1 Low > C3 High

    FVG:

        C3 High -> C1 Low

    FVG tamamilə C2-nin
    Open-Close BODY-si daxilində olmalıdır.

    FVG body-nin:
        aşağı hissəsində,
        ortasında,
        yuxarı hissəsində
    ola bilər.

    FVG ölçüsü >= C2 body-nin 50%-i.

    C3 mütləq bağlı olmalıdır.
    """

    if len(candles) < 3:
        return None

    # Son 3 candle
    c1 = candles[-3]
    c2 = candles[-2]
    c3 = candles[-1]

    # --------------------------------------------------------
    # C3 mütləq bağlı olmalıdır
    # --------------------------------------------------------

    if not candle_is_closed(c3):
        return None

    # --------------------------------------------------------
    # Candle values
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # C1 bearish
    # --------------------------------------------------------

    if not (
        c1_close < c1_open
    ):
        return None

    # --------------------------------------------------------
    # C2 bearish
    # --------------------------------------------------------

    if not (
        c2_close < c2_open
    ):
        return None

    # --------------------------------------------------------
    # FVG condition
    #
    # C1 Low > C3 High
    # --------------------------------------------------------

    if not (
        c1_low > c3_high
    ):
        return None

    # --------------------------------------------------------
    # FVG boundaries
    # --------------------------------------------------------

    fvg_low = c3_high
    fvg_high = c1_low

    fvg_size = (
        fvg_high - fvg_low
    )

    if fvg_size <= 0:
        return None

    # --------------------------------------------------------
    # C2 BODY
    #
    # YALNIZ Open və Close istifadə olunur.
    #
    # Wick / High / Low burada istifadə olunmur.
    # --------------------------------------------------------

    c2_body_low = min(
        c2_open,
        c2_close
    )

    c2_body_high = max(
        c2_open,
        c2_close
    )

    c2_body_size = (
        c2_body_high
        - c2_body_low
    )

    if c2_body_size <= 0:
        return None

    # --------------------------------------------------------
    # FVG C2 BODY-nin TAM DAXİLİNDƏ olmalıdır
    #
    # FVG aşağıda, ortada və ya yuxarıda ola bilər.
    # --------------------------------------------------------

    if fvg_low < c2_body_low:
        return None

    if fvg_high > c2_body_high:
        return None

    # --------------------------------------------------------
    # FVG >= 50% of C2 BODY
    # --------------------------------------------------------

    minimum_fvg_size = (
        c2_body_size
        * FVG_MIN_RATIO
    )

    if fvg_size < minimum_fvg_size:
        return None

    # --------------------------------------------------------
    # C3 close time
    # --------------------------------------------------------

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

        "minimum_fvg_size": minimum_fvg_size,

        "c3_open_time": c3_open_time,
        "c3_close_time": c3_close_time
    }


# ============================================================
# RECENT FVG SEARCH
# ============================================================

def find_new_fvg(symbol, interval):
    """
    Yalnız son bir neçə candle yoxlanılır.

    Tarixi 100/120 candle FVG üçün istifadə olunmur.

    C3 bot startından əvvəl bağlanıbsa:
        IGNORE
    """

    candles = get_klines(
        symbol,
        interval,
        FVG_LOOKBACK
    )

    if len(candles) < 3:
        return None

    # Son 3 closed candle üzərində yoxla.
    fvg = detect_bearish_fvg(
        candles
    )

    if not fvg:
        return None

    # --------------------------------------------------------
    # BOT START FILTER
    # --------------------------------------------------------
    #
    # C3 bot başladıqdan sonra bağlanmalıdır.
    #

    if (
        fvg["c3_close_time"]
        < BOT_START_MS
    ):
        return None

    # Eyni FVG-ni ikinci dəfə signal etmə.
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
# CREATE SIGNAL
# ============================================================

def create_signal(
    symbol,
    interval,
    fvg,
    current_price,
    trend_data,
    volume
):
    c3_high = fvg["c3_high"]

    target = (
        c3_high
        * (1 - TARGET_PERCENT / 100)
    )

    setup = {
        "symbol": symbol,
        "interval": interval,

        "c3_high": c3_high,

        "fvg_low": fvg["fvg_low"],
        "fvg_high": fvg["fvg_high"],
        "fvg_size": fvg["fvg_size"],

        "c2_open": fvg["c2_open"],
        "c2_close": fvg["c2_close"],
        "c2_body_size": fvg["c2_body_size"],

        "target": target,

        "signal_price": current_price,

        "c3_open_time": fvg["c3_open_time"],
        "c3_close_time": fvg["c3_close_time"],

        "fvg_id": fvg["fvg_id"]
    }

    active_setups[symbol] = setup

    processed_fvgs.add(
        fvg["fvg_id"]
    )

    message = (
        "🔔 BEARISH FVG SIGNAL\n\n"
        f"Symbol: {symbol}\n"
        f"Interval: {interval}\n\n"

        f"Price: {current_price:.8g}\n"
        f"C3 High: {c3_high:.8g}\n\n"

        f"FVG: {fvg['fvg_low']:.8g}"
        f" - {fvg['fvg_high']:.8g}\n"

        f"C2 Body: "
        f"{fvg['c2_body_low']:.8g}"
        f" - {fvg['c2_body_high']:.8g}\n"

        f"C2 Body Size: "
        f"{fvg['c2_body_size']:.8g}\n"

        f"FVG Size: "
        f"{fvg['fvg_size']:.8g}\n"

        f"FVG Min (50%): "
        f"{fvg['minimum_fvg_size']:.8g}\n\n"

        f"Target (-{TARGET_PERCENT}%): "
        f"{target:.8g}"
    )

    send_telegram(message)

    print(
        f"[SIGNAL] "
        f"{symbol} {interval} "
        f"FVG={fvg['fvg_low']:.8g}-"
        f"{fvg['fvg_high']:.8g} "
        f"Target={target:.8g}"
    )


# ============================================================
# MONITOR ACTIVE SETUPS
# ============================================================

def monitor_active_setups(price_data):
    """
    Aktiv FVG artıq yaranıbsa,
    onun target/cancel vəziyyəti
    Volume və Trend-dən asılı deyil.

    Target:
        Current Price <= Target

    Cancel:
        Current Price > C3 High
    """

    if not active_setups:
        return

    completed = []

    for symbol, setup in list(
        active_setups.items()
    ):

        item = price_data.get(symbol)

        if not item:
            continue

        current_price = item["last_price"]

        target = setup["target"]
        c3_high = setup["c3_high"]

        # ----------------------------------------------------
        # TARGET
        # ----------------------------------------------------

        if current_price <= target:

            message = (
                "🎯 TARGET HIT\n\n"
                f"Symbol: {symbol}\n"
                f"Interval: {setup['interval']}\n"
                f"Target: {target:.8g}\n"
                f"Current Price: {current_price:.8g}"
            )

            send_telegram(message)

            print(
                f"[TARGET HIT] "
                f"{symbol} "
                f"Price={current_price:.8g}"
            )

            completed.append(
                symbol
            )

            continue

        # ----------------------------------------------------
        # CANCEL
        # ----------------------------------------------------

        if current_price > c3_high:

            message = (
                "❌ FVG CANCELLED\n\n"
                f"Symbol: {symbol}\n"
                f"Interval: {setup['interval']}\n"
                f"C3 High: {c3_high:.8g}\n"
                f"Current Price: {current_price:.8g}"
            )

            send_telegram(message)

            print(
                f"[CANCELLED] "
                f"{symbol} "
                f"Price={current_price:.8g}"
            )

            completed.append(
                symbol
            )

    for symbol in completed:
        active_setups.pop(
            symbol,
            None
        )


# ============================================================
# SCAN
# ============================================================

def scan():
    print(
        "\n"
        + "=" * 70
    )

    now_text = datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )

    print(
        f"[SCAN] {now_text}"
    )

    # --------------------------------------------------------
    # GET SYMBOLS
    # --------------------------------------------------------

    symbols = get_spot_usdt_symbols()

    if not symbols:
        print(
            "[SCAN] No Spot USDT symbols."
        )
        return

    print(
        f"[SYMBOLS] "
        f"{len(symbols)} Spot USDT symbols"
    )

    # --------------------------------------------------------
    # GET ALL 24H VOLUME + CURRENT PRICE
    # --------------------------------------------------------

    ticker_data = get_24h_data()

    if not ticker_data:
        print(
            "[SCAN] Could not get 24H ticker data."
        )
        return

    # --------------------------------------------------------
    # ACTIVE SETUPS
    #
    # Bunlar yeni signal filtrlərindən asılı deyil.
    # Hər scan monitor olunur.
    # --------------------------------------------------------

    monitor_active_setups(
        ticker_data
    )

    # --------------------------------------------------------
    # NEW SIGNAL PIPELINE
    #
    # 1. VOLUME
    # 2. TREND
    # 3. FVG
    #
    # ƏLAVƏ trend_confirmed_since YOXDUR.
    # --------------------------------------------------------

    for symbol in symbols:

        # ----------------------------------------------------
        # Bir symbol üçün artıq aktiv FVG varsa,
        # yeni FVG açma.
        # ----------------------------------------------------

        if symbol in active_setups:
            continue

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

        # ====================================================
        # 1. VOLUME
        # ====================================================

        if volume < MIN_QUOTE_VOLUME_24H:
            continue

        # ====================================================
        # 2. LIVE TREND
        # ====================================================

        trend_ok, trend_data = (
            check_live_trend(
                symbol,
                current_price
            )
        )

        if not trend_ok:
            continue

        # ====================================================
        # 3. FVG
        # ====================================================

        for interval in FVG_INTERVALS:

            # Bir interval-də FVG tapılıbsa,
            # digər intervalə keçməyə ehtiyac yoxdur.
            if symbol in active_setups:
                break

            fvg = find_new_fvg(
                symbol,
                interval
            )

            if not fvg:
                continue

            # =================================================
            # FINAL SIGNAL
            # =================================================

            create_signal(
                symbol=symbol,
                interval=interval,
                fvg=fvg,
                current_price=current_price,
                trend_data=trend_data,
                volume=volume
            )

            break


# ============================================================
# STARTUP INFO
# ============================================================

def print_startup_info():
    bot_start_text = datetime.fromtimestamp(
        BOT_START_TIME,
        tz=timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )

    print()
    print("=" * 70)
    print("BINANCE BEARISH FVG BOT")
    print("=" * 70)

    print(
        f"Bot start: {bot_start_text}"
    )

    print(
        f"Volume >= "
        f"${MIN_QUOTE_VOLUME_24H:,.0f}"
    )

    print(
        "Trend: "
        "Current Price > EMA20 > EMA50 > EMA100"
    )

    print(
        f"EMA candles: {EMA_CANDLES}"
    )

    print(
        f"FVG intervals: "
        f"{', '.join(FVG_INTERVALS)}"
    )

    print(
        f"FVG minimum: "
        f"{FVG_MIN_RATIO * 100:.0f}% "
        "of C2 Open-Close body"
    )

    print(
        "FVG position: "
        "anywhere inside C2 body"
    )

    print(
        f"Target: "
        f"{TARGET_PERCENT}% below C3 High"
    )

    print(
        f"Scan: every {SCAN_SECONDS} seconds"
    )

    print(
        "Trend-confirmation timestamp: DISABLED"
    )

    print(
        "Historical FVG protection: ENABLED"
    )

    print("=" * 70)
    print()


# ============================================================
# MAIN LOOP
# ============================================================

def main():

    print_startup_info()

    if not TELEGRAM_BOT_TOKEN:
        print(
            "[WARNING] "
            "TELEGRAM_BOT_TOKEN is not set."
        )

    if not TELEGRAM_CHAT_ID:
        print(
            "[WARNING] "
            "TELEGRAM_CHAT_ID is not set."
        )

    while True:

        start_time = time.time()

        try:
            scan()

        except Exception as e:

            print(
                f"[SCAN ERROR] {e}"
            )

        elapsed = (
            time.time()
            - start_time
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
