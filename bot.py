import os
import time
import requests
from datetime import datetime, timezone


# ============================================================
# BINANCE DUAL STRATEGY LIVE ALERT BOT
#
# ============================================================
#
# 🔴 STRATEGY 1 — ORIGINAL BEARISH FVG
#
# 5m + 15m + 30m + 1h
# Independent active FVG
#
# 24H TOTAL VOLUME >= 20M USDT
# AND
# 24H BUY VOLUME > 24H SELL VOLUME
#
# ORIGINAL BEARISH FVG CONDITIONS:
#
# 1. C2 MUST BE BEARISH
# 2. C1 LOW > C3 HIGH
# 3. C3 CLOSE < C2 LOW
# 4. FVG >= 0.5%
# 5. FVG MUST BE INSIDE C2 BODY
# 6. FVG / C2 BODY >= 50%
#
# TARGETS:
# 5m  = 1.2%
# 15m = 1.7%
# 30m = 2.2%
# 1h  = 2.7%
#
# TELEGRAM:
# ONLY TARGET HIT
#
#
# ============================================================
#
# 🟢 STRATEGY 2 — NEW BULLISH TREND + BULLISH FVG
#
# 15m + 30m + 1h
# Independent
#
# CONDITIONS:
#
# 1. 24H TOTAL VOLUME >= 20M USDT
# 2. 24H BUY VOLUME > SELL VOLUME
# 3. EMA20 > EMA50
# 4. EMA20 RISING
# 5. CLOSE > EMA20
# 6. BULLISH STRUCTURE BREAK
# 7. STRONG BULLISH DISPLACEMENT
# 8. BODY / RANGE >= 60%
# 9. RANGE >= 1.3x average previous 20 candles
# 10. VOLUME >= 1.5x average previous 20 candles
# 11. BULLISH FVG: C1 HIGH < C3 LOW
# 12. FVG >= 0.5%
# 13. NO C2 BODY CONDITION
# 14. NO 50% FVG/BODY CONDITION
# 15. C3 MUST BE CLOSED
#
# STRUCTURE:
# Previous 10 CLOSED candles' highest HIGH
# must be broken by C3 CLOSE.
#
# TELEGRAM:
# Bullish signal is sent when ALL conditions pass.
#
# ============================================================


BINANCE_BASE_URL = "https://api.binance.com"


# ============================================================
# GENERAL SETTINGS
# ============================================================

MIN_QUOTE_VOLUME_24H = 20_000_000

SCAN_SECONDS = 60


# ============================================================
# 🔴 ORIGINAL BEARISH FVG SETTINGS
# ============================================================

FVG_MIN_PERCENT = 0.5

FVG_MIN_RATIO = 0.50


FVG_TARGETS = {
    "5m": 1.2,
    "15m": 1.7,
    "30m": 2.2,
    "1h": 2.7,
}


FVG_INTERVALS = list(
    FVG_TARGETS.keys()
)


# ============================================================
# 🟢 NEW BULLISH STRATEGY SETTINGS
# ============================================================

BULLISH_INTERVALS = [
    "15m",
    "30m",
    "1h",
]


BULLISH_EMA_FAST = 20

BULLISH_EMA_SLOW = 50


BULLISH_FVG_MIN_PERCENT = 0.5


# Structure break:
# Highest HIGH of previous 10 CLOSED candles
STRUCTURE_LOOKBACK = 10


# Strong displacement:
# Body must be at least 60% of candle range
DISPLACEMENT_MIN_BODY_RATIO = 0.60


# Candle range must be at least 1.3x
# previous 20 candle average range
DISPLACEMENT_RANGE_MULTIPLIER = 1.30


# Volume must be at least 1.5x
# previous 20 candle average volume
DISPLACEMENT_VOLUME_MULTIPLIER = 1.50


# Number of candles used for averages
AVERAGE_LOOKBACK = 20


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN"
)


TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID"
)


# ============================================================
# 🔴 ORIGINAL BEARISH BOT STATE
# ============================================================

BOT_START_MS = int(
    time.time() * 1000
)


active_setups = {}


processed_fvgs = set()


candle_state = {}


# ============================================================
# 🟢 NEW BULLISH BOT STATE
# ============================================================

processed_bullish_signals = set()


# ============================================================
# BINANCE REQUEST
# ============================================================

def binance_get(
    endpoint,
    params=None
):

    url = (
        BINANCE_BASE_URL
        +
        endpoint
    )


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
# TELEGRAM
# ============================================================

def send_telegram(message):

    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):

        print(
            "[TELEGRAM ERROR] "
            "TELEGRAM_BOT_TOKEN "
            "or TELEGRAM_CHAT_ID missing"
        )

        return False


    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )


    payload = {

        "chat_id":
            TELEGRAM_CHAT_ID,

        "text":
            message,

        "parse_mode":
            "HTML",

    }


    try:

        response = requests.post(
            url,
            json=payload,
            timeout=20
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


    for item in data.get(
        "symbols",
        []
    ):

        if (
            item.get("status")
            == "TRADING"

            and

            item.get("quoteAsset")
            == "USDT"

            and

            item.get(
                "isSpotTradingAllowed"
            )
            is True
        ):

            symbols.append(
                item["symbol"]
            )


    return symbols


# ============================================================
# 24H TOTAL VOLUME
# ============================================================

def get_24h_volumes():

    data = binance_get(
        "/api/v3/ticker/24hr"
    )


    if not data:

        return {}


    volumes = {}


    for item in data:

        symbol = item.get(
            "symbol"
        )


        if not symbol:

            continue


        try:

            quote_volume = float(
                item.get(
                    "quoteVolume",
                    0
                )
            )


            volumes[symbol] = (
                quote_volume
            )


        except Exception:

            continue


    return volumes


# ============================================================
# 24H BUY / SELL VOLUME
# ============================================================

def get_24h_buy_sell_volume(symbol):

    data = binance_get(

        "/api/v3/klines",

        {
            "symbol":
                symbol,

            "interval":
                "1h",

            "limit":
                25,
        }

    )


    if not data:

        return None


    now_ms = int(
        time.time() * 1000
    )


    closed_candles = []


    for candle in data:

        try:

            close_time = int(
                candle[6]
            )


            if close_time < now_ms:

                closed_candles.append(
                    candle
                )


        except Exception:

            continue


    if len(closed_candles) < 24:

        return None


    candles_24h = (
        closed_candles[-24:]
    )


    total_volume = 0.0

    buy_volume = 0.0


    for candle in candles_24h:

        try:

            total_quote_volume = float(
                candle[7]
            )


            taker_buy_quote_volume = float(
                candle[10]
            )


            total_volume += (
                total_quote_volume
            )


            buy_volume += (
                taker_buy_quote_volume
            )


        except Exception:

            continue


    if total_volume <= 0:

        return None


    sell_volume = (
        total_volume
        -
        buy_volume
    )


    if sell_volume < 0:

        sell_volume = 0.0


    buy_percent = (
        buy_volume
        /
        total_volume
        *
        100
    )


    sell_percent = (
        sell_volume
        /
        total_volume
        *
        100
    )


    return {

        "total_volume":
            total_volume,

        "buy_volume":
            buy_volume,

        "sell_volume":
            sell_volume,

        "buy_percent":
            buy_percent,

        "sell_percent":
            sell_percent,

    }


# ============================================================
# VOLUME FORMAT
# ============================================================

def format_volume(value):

    if value >= 1_000_000_000:

        return (
            f"{value / 1_000_000_000:.2f}B"
        )


    if value >= 1_000_000:

        return (
            f"{value / 1_000_000:.2f}M"
        )


    if value >= 1_000:

        return (
            f"{value / 1_000:.2f}K"
        )


    return (
        f"{value:.2f}"
    )


# ============================================================
# ============================================================
# 🔴 ORIGINAL BEARISH FVG STRATEGY
# ============================================================
# ============================================================


# ============================================================
# GET LATEST 3 CLOSED CANDLES
# ============================================================

def get_latest_closed_candles(
    symbol,
    interval
):

    data = binance_get(

        "/api/v3/klines",

        {
            "symbol":
                symbol,

            "interval":
                interval,

            "limit":
                4,
        }
    )


    if not data:

        return []


    now_ms = int(
        time.time() * 1000
    )


    closed = []


    for candle in data:

        try:

            close_time = int(
                candle[6]
            )


            if close_time < now_ms:

                closed.append(
                    candle
                )


        except Exception:

            continue


    if len(closed) < 3:

        return []


    return closed[-3:]


# ============================================================
# CANDLE HELPERS
# ============================================================

def candle_open(candle):

    return float(
        candle[1]
    )


def candle_high(candle):

    return float(
        candle[2]
    )


def candle_low(candle):

    return float(
        candle[3]
    )


def candle_close(candle):

    return float(
        candle[4]
    )


def candle_is_bearish(candle):

    return (
        candle_close(candle)
        <
        candle_open(candle)
    )


# ============================================================
# ORIGINAL BEARISH FVG DETECTION
# ============================================================

def detect_bearish_fvg(candles):

    if len(candles) != 3:

        return None


    c1 = candles[0]

    c2 = candles[1]

    c3 = candles[2]


    # --------------------------------------------------------
    # C2 MUST BE BEARISH
    # --------------------------------------------------------

    if not candle_is_bearish(c2):

        return None


    c1_low = candle_low(c1)

    c3_high = candle_high(c3)


    # --------------------------------------------------------
    # BEARISH FVG
    # --------------------------------------------------------

    if c1_low <= c3_high:

        return None


    c2_low = candle_low(c2)

    c3_close = candle_close(c3)


    # --------------------------------------------------------
    # C3 CLOSE BELOW C2 LOW
    # --------------------------------------------------------

    if c3_close >= c2_low:

        return None


    # --------------------------------------------------------
    # FVG
    # --------------------------------------------------------

    fvg_low = c3_high

    fvg_high = c1_low


    fvg_size = (
        fvg_high
        -
        fvg_low
    )


    if fvg_size <= 0:

        return None


    # --------------------------------------------------------
    # FVG >= 0.5%
    # --------------------------------------------------------

    fvg_percent = (
        fvg_size
        /
        c1_low
        *
        100
    )


    if fvg_percent < FVG_MIN_PERCENT:

        return None


    # ========================================================
    # ORIGINAL C2 BODY CONDITION
    # ========================================================

    c2_open = candle_open(c2)

    c2_close = candle_close(c2)


    c2_body_high = max(
        c2_open,
        c2_close
    )


    c2_body_low = min(
        c2_open,
        c2_close
    )


    c2_body_size = (
        c2_body_high
        -
        c2_body_low
    )


    if c2_body_size <= 0:

        return None


    # --------------------------------------------------------
    # ORIGINAL:
    # FVG MUST BE INSIDE C2 BODY
    # --------------------------------------------------------

    if fvg_low < c2_body_low:

        return None


    if fvg_high > c2_body_high:

        return None


    # --------------------------------------------------------
    # ORIGINAL:
    # FVG / C2 BODY >= 50%
    # --------------------------------------------------------

    fvg_ratio = (
        fvg_size
        /
        c2_body_size
    )


    if fvg_ratio < FVG_MIN_RATIO:

        return None


    return {

        "fvg_low":
            fvg_low,

        "fvg_high":
            fvg_high,

        "fvg_size":
            fvg_size,

        "fvg_percent":
            fvg_percent,

        "fvg_ratio":
            fvg_ratio,

        "c1_low":
            c1_low,

        "c2_open":
            c2_open,

        "c2_close":
            c2_close,

        "c2_body_size":
            c2_body_size,

        "c3_open_time":
            int(c3[0]),

        "c1_open_time":
            int(c1[0]),

        "c2_open_time":
            int(c2[0]),

        "c3_close_time":
            int(c3[6]),

        "c3_high":
            c3_high,

        "c3_close":
            candle_close(c3),

    }


# ============================================================
# ORIGINAL REAL-TIME ROLLING FVG
# ============================================================

def update_realtime_fvg(
    symbol,
    interval
):

    candles = (
        get_latest_closed_candles(
            symbol,
            interval
        )
    )


    if len(candles) < 3:

        return None


    latest_candle = candles[-1]


    latest_open_time = int(
        latest_candle[0]
    )


    key = (
        symbol,
        interval
    )


    state = candle_state.get(
        key
    )


    # --------------------------------------------------------
    # FIRST SCAN
    # --------------------------------------------------------

    if state is None:

        candle_state[key] = {

            "last_closed_open_time":
                latest_open_time,

            "window":
                candles[-3:],

        }


        print(
            f"[BEARISH INIT] "
            f"{symbol} {interval}"
        )


        return None


    last_time = int(
        state[
            "last_closed_open_time"
        ]
    )


    # --------------------------------------------------------
    # NO NEW CANDLE
    # --------------------------------------------------------

    if latest_open_time <= last_time:

        return None


    old_window = state[
        "window"
    ]


    new_window = [

        old_window[-2],

        old_window[-1],

        latest_candle,

    ]


    state["window"] = (
        new_window
    )


    state[
        "last_closed_open_time"
    ] = latest_open_time


    print(
        f"[BEARISH NEW CANDLE] "
        f"{symbol} {interval}"
    )


    fvg = detect_bearish_fvg(
        new_window
    )


    if not fvg:

        return None


    fvg_id = (

        symbol,

        interval,

        fvg[
            "c3_open_time"
        ]

    )


    if fvg_id in processed_fvgs:

        return None


    fvg["fvg_id"] = (
        fvg_id
    )


    return fvg


# ============================================================
# ORIGINAL CREATE ACTIVE SETUP
# ============================================================

def create_setup(
    symbol,
    interval,
    fvg,
    volume_data
):

    target_percent = (
        FVG_TARGETS[
            interval
        ]
    )


    c3_high = fvg[
        "c3_high"
    ]


    target = (

        c3_high

        *

        (
            1
            -
            target_percent / 100
        )

    )


    setup = {

        "symbol":
            symbol,

        "interval":
            interval,

        "fvg_id":
            fvg["fvg_id"],

        "fvg_low":
            fvg["fvg_low"],

        "fvg_high":
            fvg["fvg_high"],

        "fvg_size":
            fvg["fvg_size"],

        "fvg_percent":
            fvg["fvg_percent"],

        "fvg_ratio":
            fvg["fvg_ratio"],

        "c1_low":
            fvg["c1_low"],

        "c2_body_size":
            fvg["c2_body_size"],

        "c3_high":
            c3_high,

        "c3_close":
            fvg["c3_close"],

        "target":
            target,

        "target_percent":
            target_percent,

        "volume_24h":
            volume_data["total_volume"],

        "buy_volume":
            volume_data["buy_volume"],

        "sell_volume":
            volume_data["sell_volume"],

        "buy_percent":
            volume_data["buy_percent"],

        "sell_percent":
            volume_data["sell_percent"],

        "created_at":
            int(
                time.time() * 1000
            ),

    }


    return setup


# ============================================================
# ORIGINAL TARGET MESSAGE
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

        {
            "symbol":
                symbol
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
# ORIGINAL MONITOR ACTIVE SETUPS
# ============================================================

def monitor_active_setups():

    if not active_setups:

        return


    for (
        symbol,
        interval
    ), setup in list(
        active_setups.items()
    ):

        try:

            current_price = (
                get_current_price(
                    symbol
                )
            )


            if current_price is None:

                continue


            target = setup[
                "target"
            ]


            c3_high = setup[
                "c3_high"
            ]


            # ------------------------------------------------
            # TARGET HIT
            # ------------------------------------------------

            if current_price <= target:

                print(

                    f"[BEARISH TARGET HIT] "
                    f"{symbol} "
                    f"{interval} "
                    f"Price="
                    f"{current_price}"

                )


                send_telegram(

                    format_target_message(
                        setup
                    )

                )


                active_setups.pop(

                    (
                        symbol,
                        interval
                    ),

                    None

                )


                continue


            # ------------------------------------------------
            # CANCELLED
            # ------------------------------------------------

            if current_price > c3_high:

                print(

                    f"[BEARISH CANCELLED] "
                    f"{symbol} "
                    f"{interval} "
                    f"Price="
                    f"{current_price}"

                )


                active_setups.pop(

                    (
                        symbol,
                        interval
                    ),

                    None

                )


                continue


        except Exception as e:

            print(

                f"[BEARISH MONITOR ERROR] "
                f"{symbol} "
                f"{interval}: "
                f"{e}"

            )


# ============================================================
# ============================================================
# 🟢 NEW BULLISH TREND STRATEGY
# ============================================================
# ============================================================


# ============================================================
# GET CLOSED CANDLES FOR BULLISH STRATEGY
# ============================================================

def get_bullish_closed_candles(
    symbol,
    interval,
    limit=80
):

    data = binance_get(

        "/api/v3/klines",

        {
            "symbol":
                symbol,

            "interval":
                interval,

            "limit":
                limit,
        }

    )


    if not data:

        return []


    now_ms = int(
        time.time() * 1000
    )


    closed = []


    for candle in data:

        try:

            close_time = int(
                candle[6]
            )


            if close_time < now_ms:

                closed.append(
                    candle
                )


        except Exception:

            continue


    return closed


# ============================================================
# EMA
# ============================================================

def calculate_ema(
    values,
    period
):

    if len(values) < period:

        return None


    ema = sum(
        values[:period]
    ) / period


    multiplier = (
        2 /
        (period + 1)
    )


    for value in values[period:]:

        ema = (

            (
                value
                -
                ema
            )

            *
            multiplier

            +
            ema

        )


    return ema


# ============================================================
# BULLISH FVG DETECTION
# ============================================================

def detect_bullish_fvg(
    candles
):

    if len(candles) < 3:

        return None


    c1 = candles[-3]

    c2 = candles[-2]

    c3 = candles[-1]


    c1_high = candle_high(c1)

    c3_low = candle_low(c3)


    # --------------------------------------------------------
    # BULLISH FVG
    #
    # C1 HIGH < C3 LOW
    # --------------------------------------------------------

    if c1_high >= c3_low:

        return None


    fvg_size = (
        c3_low
        -
        c1_high
    )


    if fvg_size <= 0:

        return None


    # --------------------------------------------------------
    # FVG >= 0.5%
    # --------------------------------------------------------

    fvg_percent = (

        fvg_size
        /
        c1_high
        *
        100

    )


    if (
        fvg_percent
        <
        BULLISH_FVG_MIN_PERCENT
    ):

        return None


    return {

        "c1_high":
            c1_high,

        "c2_high":
            candle_high(c2),

        "c2_low":
            candle_low(c2),

        "c3_low":
            c3_low,

        "c3_high":
            candle_high(c3),

        "c3_open":
            candle_open(c3),

        "c3_close":
            candle_close(c3),

        "fvg_size":
            fvg_size,

        "fvg_percent":
            fvg_percent,

        "c1_open_time":
            int(c1[0]),

        "c2_open_time":
            int(c2[0]),

        "c3_open_time":
            int(c3[0]),

        "c3_close_time":
            int(c3[6]),

    }


# ============================================================
# BULLISH DISPLACEMENT
# ============================================================

def check_bullish_displacement(
    candles
):

    if len(candles)
    <
    AVERAGE_LOOKBACK + 1:

        return None


    c3 = candles[-1]


    c3_open = candle_open(c3)

    c3_high = candle_high(c3)

    c3_low = candle_low(c3)

    c3_close = candle_close(c3)


    # --------------------------------------------------------
    # C3 MUST BE BULLISH
    # --------------------------------------------------------

    if c3_close <= c3_open:

        return None


    c3_range = (
        c3_high
        -
        c3_low
    )


    if c3_range <= 0:

        return None


    c3_body = (
        c3_close
        -
        c3_open
    )


    body_ratio = (
        c3_body
        /
        c3_range
    )


    # --------------------------------------------------------
    # BODY >= 60% OF RANGE
    # --------------------------------------------------------

    if (
        body_ratio
        <
        DISPLACEMENT_MIN_BODY_RATIO
    ):

        return None


    previous_candles = candles[
        -(
            AVERAGE_LOOKBACK + 1
        ):
        -1
    ]


    ranges = []


    volumes = []


    for candle in previous_candles:

        high = candle_high(candle)

        low = candle_low(candle)

        quote_volume = float(
            candle[7]
        )


        candle_range = (
            high
            -
            low
        )


        if candle_range > 0:

            ranges.append(
                candle_range
            )


        volumes.append(
            quote_volume
        )


    if not ranges:

        return None


    if not volumes:

        return None


    average_range = (
        sum(ranges)
        /
        len(ranges)
    )


    average_volume = (
        sum(volumes)
        /
        len(volumes)
    )


    # --------------------------------------------------------
    # RANGE >= 1.3x AVERAGE
    # --------------------------------------------------------

    if (
        c3_range
        <
        average_range
        *
        DISPLACEMENT_RANGE_MULTIPLIER
    ):

        return None


    c3_volume = float(
        c3[7]
    )


    # --------------------------------------------------------
    # VOLUME >= 1.5x AVERAGE
    # --------------------------------------------------------

    if (
        c3_volume
        <
        average_volume
        *
        DISPLACEMENT_VOLUME_MULTIPLIER
    ):

        return None


    return {

        "body_ratio":
            body_ratio,

        "range":
            c3_range,

        "average_range":
            average_range,

        "range_multiple":
            (
                c3_range
                /
                average_range
            ),

        "volume":
            c3_volume,

        "average_volume":
            average_volume,

        "volume_multiple":
            (
                c3_volume
                /
                average_volume
            ),

    }


# ============================================================
# BULLISH STRUCTURE BREAK
# ============================================================

def check_bullish_structure_break(
    candles
):

    # Need:
    # previous 10 closed candles
    # + current C3

    required = (
        STRUCTURE_LOOKBACK
        +
        1
    )


    if len(candles) < required:

        return None


    c3 = candles[-1]


    # --------------------------------------------------------
    # IMPORTANT:
    #
    # C3 IS NOT INCLUDED IN STRUCTURE HIGH
    #
    # Previous 10 closed candles are:
    #
    # candles[-11:-1]
    # --------------------------------------------------------

    previous_candles = candles[
        -(
            STRUCTURE_LOOKBACK
            +
            1
        ):
        -1
    ]


    previous_high = max(
        candle_high(candle)
        for candle
        in previous_candles
    )


    c3_close = candle_close(c3)


    # --------------------------------------------------------
    # BULLISH BOS
    # --------------------------------------------------------

    if c3_close <= previous_high:

        return None


    break_percent = (

        (
            c3_close
            -
            previous_high
        )
        /
        previous_high
        *
        100

    )


    return {

        "previous_high":
            previous_high,

        "break_price":
            c3_close,

        "break_percent":
            break_percent,

    }


# ============================================================
# NEW BULLISH STRATEGY
# ============================================================

def detect_bullish_strategy(
    symbol,
    interval
):

    candles = (
        get_bullish_closed_candles(
            symbol,
            interval,
            80
        )
    )


    minimum_needed = (
        max(
            BULLISH_EMA_SLOW,
            STRUCTURE_LOOKBACK,
            AVERAGE_LOOKBACK
        )
        +
        5
    )


    if len(candles) < minimum_needed:

        print(

            f"[BULLISH WAIT] "
            f"{symbol} {interval} "
            f"not enough closed candles"

        )

        return None


    # ========================================================
    # LAST CLOSED CANDLE
    # ========================================================

    c3 = candles[-1]


    c3_open_time = int(
        c3[0]
    )


    # ========================================================
    # EMA20 / EMA50
    # ========================================================

    closes = [

        candle_close(candle)

        for candle
        in candles

    ]


    ema20 = calculate_ema(
        closes,
        BULLISH_EMA_FAST
    )


    ema50 = calculate_ema(
        closes,
        BULLISH_EMA_SLOW
    )


    if (
        ema20 is None
        or
        ema50 is None
    ):

        return None


    # --------------------------------------------------------
    # EMA20 > EMA50
    # --------------------------------------------------------

    if ema20 <= ema50:

        return None


    # ========================================================
    # EMA20 RISING
    #
    # Calculate EMA20 one candle earlier
    # ========================================================

    previous_closes = closes[:-1]


    previous_ema20 = calculate_ema(
        previous_closes,
        BULLISH_EMA_FAST
    )


    if previous_ema20 is None:

        return None


    if ema20 <= previous_ema20:

        return None


    # ========================================================
    # PRICE > EMA20
    # ========================================================

    c3_close = candle_close(c3)


    if c3_close <= ema20:

        return None


    # ========================================================
    # STRUCTURE BREAK
    # ========================================================

    structure = (
        check_bullish_structure_break(
            candles
        )
    )


    if structure is None:

        return None


    # ========================================================
    # BULLISH DISPLACEMENT
    # ========================================================

    displacement = (
        check_bullish_displacement(
            candles
        )
    )


    if displacement is None:

        return None


    # ========================================================
    # BULLISH FVG
    # ========================================================

    fvg = detect_bullish_fvg(
        candles[-3:]
    )


    if fvg is None:

        return None


    # ========================================================
    # ALL CONDITIONS PASSED
    # ========================================================

    signal_id = (

        symbol,

        interval,

        c3_open_time

    )


    if (
        signal_id
        in
        processed_bullish_signals
    ):

        return None


    return {

        "signal_id":
            signal_id,

        "symbol":
            symbol,

        "interval":
            interval,

        "c3_open_time":
            c3_open_time,

        "c3_close":
            c3_close,

        "c3_high":
            candle_high(c3),

        "c3_low":
            candle_low(c3),

        "ema20":
            ema20,

        "ema50":
            ema50,

        "previous_ema20":
            previous_ema20,

        "previous_high":
            structure[
                "previous_high"
            ],

        "break_price":
            structure[
                "break_price"
            ],

        "break_percent":
            structure[
                "break_percent"
            ],

        "body_ratio":
            displacement[
                "body_ratio"
            ],

        "range":
            displacement[
                "range"
            ],

        "average_range":
            displacement[
                "average_range"
            ],

        "range_multiple":
            displacement[
                "range_multiple"
            ],

        "volume":
            displacement[
                "volume"
            ],

        "average_volume":
            displacement[
                "average_volume"
            ],

        "volume_multiple":
            displacement[
                "volume_multiple"
            ],

        "fvg_low":
            fvg["c1_high"],

        "fvg_high":
            fvg["c3_low"],

        "fvg_size":
            fvg["fvg_size"],

        "fvg_percent":
            fvg["fvg_percent"],

    }


# ============================================================
# BULLISH TELEGRAM MESSAGE
# ============================================================

def format_bullish_message(
    signal,
    volume_data
):

    return (

        "🟢 <b>BULLISH TREND SIGNAL</b>\n\n"

        f"<b>{signal['symbol']}</b> "
        f"{signal['interval']}\n\n"

        "━━━━━━━━━━━━━━━━━━\n"

        "<b>📈 TREND</b>\n"

        f"EMA20: "
        f"{signal['ema20']:.8g}\n"

        f"EMA50: "
        f"{signal['ema50']:.8g}\n"

        f"EMA20 > EMA50: "
        f"✅\n"

        f"Price > EMA20: "
        f"✅\n"

        f"EMA20 Rising: "
        f"✅\n\n"

        "━━━━━━━━━━━━━━━━━━\n"

        "<b>🚀 STRUCTURE</b>\n"

        f"Previous High: "
        f"{signal['previous_high']:.8g}\n"

        f"Break Price: "
        f"{signal['break_price']:.8g}\n"

        f"BOS: "
        f"✅ "
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
# ============================================================
# MAIN SCAN
# ============================================================
# ============================================================

def scan():

    print(
        "\n"
        +
        "=" * 75
    )


    print(
        "NEW SCAN"
    )


    print(

        datetime.now(
            timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )

    )


    print(
        "=" * 75
    )


    # ========================================================
    # 🔴 ORIGINAL ACTIVE BEARISH SETUPS
    # ========================================================

    monitor_active_setups()


    # ========================================================
    # GET SYMBOLS
    # ========================================================

    symbols = (
        get_spot_usdt_symbols()
    )


    if not symbols:

        print(
            "[ERROR] "
            "Could not get symbols"
        )

        return


    print(

        f"[INFO] "
        f"Spot USDT symbols: "
        f"{len(symbols)}"

    )


    # ========================================================
    # GET 24H VOLUMES
    # ========================================================

    volumes = (
        get_24h_volumes()
    )


    if not volumes:

        print(

            "[ERROR] "
            "Could not get 24H volumes"

        )

        return


    # ========================================================
    # 20M+ VOLUME FILTER
    # ========================================================

    volume_qualified_symbols = []


    for symbol in symbols:

        volume = volumes.get(
            symbol
        )


        if (

            volume is not None

            and

            volume >=
            MIN_QUOTE_VOLUME_24H

        ):

            volume_qualified_symbols.append(
                symbol
            )


    print(

        f"[INFO] "
        f"20M+ total volume: "
        f"{len(volume_qualified_symbols)}"

    )


    # ========================================================
    # BUY / SELL FILTER
    # ========================================================

    qualified_symbols = []


    for symbol in volume_qualified_symbols:

        try:

            volume_data = (
                get_24h_buy_sell_volume(
                    symbol
                )
            )


            if volume_data is None:

                print(

                    f"[VOLUME ERROR] "
                    f"{symbol}"

                )

                continue


            total_volume = (
                volume_data[
                    "total_volume"
                ]
            )


            buy_volume = (
                volume_data[
                    "buy_volume"
                ]
            )


            sell_volume = (
                volume_data[
                    "sell_volume"
                ]
            )


            buy_percent = (
                volume_data[
                    "buy_percent"
                ]
            )


            sell_percent = (
                volume_data[
                    "sell_percent"
                ]
            )


            if (

                total_volume >=
                MIN_QUOTE_VOLUME_24H

                and

                buy_volume >
                sell_volume

            ):

                qualified_symbols.append(

                    (
                        symbol,
                        volume_data
                    )

                )


                print(

                    f"[QUALIFIED] "
                    f"{symbol} | "
                    f"24H="
                    f"{format_volume(total_volume)} | "
                    f"BUY="
                    f"{format_volume(buy_volume)} "
                    f"({buy_percent:.2f}%) | "
                    f"SELL="
                    f"{format_volume(sell_volume)} "
                    f"({sell_percent:.2f}%)"

                )


            else:

                print(

                    f"[REJECTED] "
                    f"{symbol} | "
                    f"24H="
                    f"{format_volume(total_volume)} | "
                    f"BUY="
                    f"{format_volume(buy_volume)} "
                    f"({buy_percent:.2f}%) | "
                    f"SELL="
                    f"{format_volume(sell_volume)} "
                    f"({sell_percent:.2f}%)"

                )


        except Exception as e:

            print(

                f"[BUY/SELL ERROR] "
                f"{symbol}: "
                f"{e}"

            )


    print(

        f"[INFO] "
        f"Final qualified: "
        f"{len(qualified_symbols)}"

    )


    # ========================================================
    # 🔴 ORIGINAL BEARISH FVG SCAN
    # ========================================================

    print(
        "\n"
        "========== BEARISH FVG SCAN =========="
    )


    for (
        symbol,
        volume_data
    ) in qualified_symbols:

        for interval in FVG_INTERVALS:

            try:

                fvg = (
                    update_realtime_fvg(
                        symbol,
                        interval
                    )
                )


                if not fvg:

                    continue


                processed_fvgs.add(
                    fvg["fvg_id"]
                )


                setup_key = (

                    symbol,
                    interval

                )


                # ------------------------------------------------
                # ONE ACTIVE BEARISH FVG
                # ------------------------------------------------

                if setup_key in active_setups:

                    print(

                        f"[BEARISH IGNORED] "
                        f"{symbol} {interval} "
                        f"already active"

                    )

                    continue


                setup = create_setup(

                    symbol,

                    interval,

                    fvg,

                    volume_data

                )


                active_setups[
                    setup_key
                ] = setup


                print(

                    f"[NEW BEARISH FVG] "
                    f"{symbol} {interval} | "

                    f"FVG="
                    f"{fvg['fvg_low']:.8g}"
                    f"-"
                    f"{fvg['fvg_high']:.8g} | "

                    f"Size="
                    f"{fvg['fvg_percent']:.2f}% | "

                    f"C2 Ratio="
                    f"{fvg['fvg_ratio'] * 100:.2f}% | "

                    f"Target="
                    f"{setup['target']:.8g}"

                )


            except Exception as e:

                print(

                    f"[BEARISH FVG ERROR] "
                    f"{symbol} "
                    f"{interval}: "
                    f"{e}"

                )


    # ========================================================
    # 🟢 NEW BULLISH STRATEGY SCAN
    # ========================================================

    print(
        "\n"
        "========== BULLISH TREND SCAN =========="
    )


    for (
        symbol,
        volume_data
    ) in qualified_symbols:

        for interval in BULLISH_INTERVALS:

            try:

                signal = (
                    detect_bullish_strategy(
                        symbol,
                        interval
                    )
                )


                if signal is None:

                    continue


                signal_id = (
                    signal[
                        "signal_id"
                    ]
                )


                if (
                    signal_id
                    in
                    processed_bullish_signals
                ):

                    continue


                # ------------------------------------------------
                # SAVE FIRST
                # ------------------------------------------------

                processed_bullish_signals.add(
                    signal_id
                )


                print(

                    "\n"
                    "[🟢 BULLISH SIGNAL] "
                    f"{symbol} "
                    f"{interval}"

                )


                print(

                    f"  EMA20="
                    f"{signal['ema20']:.8g} | "
                    f"EMA50="
                    f"{signal['ema50']:.8g}"

                )


                print(

                    f"  BOS="
                    f"{signal['previous_high']:.8g}"
                    f" -> "
                    f"{signal['break_price']:.8g} "
                    f"(+"
                    f"{signal['break_percent']:.2f}"
                    f"%)"

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


                # ------------------------------------------------
                # TELEGRAM
                # ------------------------------------------------

                send_telegram(

                    format_bullish_message(
                        signal,
                        volume_data
                    )

                )


            except Exception as e:

                print(

                    f"[BULLISH ERROR] "
                    f"{symbol} "
                    f"{interval}: "
                    f"{e}"

                )


# ============================================================
# MAIN LOOP
# ============================================================

def main():

    print(
        "=" * 75
    )


    print(
        "BINANCE DUAL STRATEGY LIVE ALERT BOT"
    )


    print(
        "=" * 75
    )


    print(
        "\n🔴 ORIGINAL BEARISH FVG:"
    )


    print(
        "  Timeframes: 5m + 15m + 30m + 1h"
    )


    print(
        "  C2 Bearish: REQUIRED"
    )


    print(
        "  C3 Close < C2 Low: REQUIRED"
    )


    print(
        "  FVG >= 0.5%"
    )


    print(
        "  FVG inside C2 Body: REQUIRED"
    )


    print(
        "  FVG/C2 Body >= 50%: REQUIRED"
    )


    print(
        "  Telegram: TARGET HIT ONLY"
    )


    print(
        "\n🟢 NEW BULLISH TREND:"
    )


    print(
        "  Timeframes: 15m + 30m + 1h"
    )


    print(
        "  EMA20 > EMA50"
    )


    print(
        "  EMA20 Rising"
    )


    print(
        "  Price > EMA20"
    )


    print(
        f"  Structure Break: "
        f"Previous {STRUCTURE_LOOKBACK} "
        f"closed candles HIGH"
    )


    print(
        "  Bullish Displacement"
    )


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


    print(
        "  C2 Body condition: DISABLED"
    )


    print(
        "  50% Ratio condition: DISABLED"
    )


    print(
        "  Telegram: SIGNAL"
    )


    print(
        "\nCOMMON VOLUME FILTER:"
    )


    print(
        "  24H Total >= 20M USDT"
    )


    print(
        "  24H BUY > SELL"
    )


    print(
        f"\nScan: "
        f"{SCAN_SECONDS} seconds"
    )


    print(
        "=" * 75
    )


    while True:

        cycle_start = time.time()


        try:

            scan()


        except Exception as e:

            print(
                f"[MAIN ERROR] {e}"
            )


        elapsed = (

            time.time()
            -
            cycle_start

        )


        sleep_time = max(

            1,

            SCAN_SECONDS
            -
            elapsed

        )


        print(

            f"\n[WAIT] "
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
            "\nBot stopped."
        )


    except Exception as e:

        print(
            f"FATAL ERROR: "
            f"{e}"
        )
