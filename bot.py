import os
import time
import requests
from datetime import datetime, timezone


# ============================================================
# BINANCE BEARISH FVG LIVE ALERT BOT
# REAL-TIME ROLLING 3-CANDLE VERSION
# 5m + 15m + 30m + 1h INDEPENDENT ACTIVE FVG
# EACH TIMEFRAME HAS ITS OWN TARGET
#
# TELEGRAM:
# ONLY TARGET HIT
# NO FVG CREATION MESSAGE
# NO CANCEL MESSAGE
# ============================================================


BINANCE_BASE_URL = "https://api.binance.com"


# ============================================================
# SETTINGS
# ============================================================

MIN_QUOTE_VOLUME_24H = 20_000_000

FVG_MIN_RATIO = 0.50


# Each timeframe has its own target
FVG_TARGETS = {
    "5m": 0.7,
    "15m": 1.2,
    "30m": 1.7,
    "1h": 2.4,
}


FVG_INTERVALS = list(
    FVG_TARGETS.keys()
)


SCAN_SECONDS = 60


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
# BOT STATE
# ============================================================

BOT_START_MS = int(
    time.time() * 1000
)


active_setups = {}

processed_fvgs = set()

candle_state = {}


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
# 24H VOLUME
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
# KLINES
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
# BEARISH FVG DETECTION
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
    # BEARISH FVG CONDITION
    # --------------------------------------------------------

    if c1_low <= c3_high:

        return None


    c2_low = candle_low(c2)

    c3_close = candle_close(c3)


    # --------------------------------------------------------
    # C3 CLOSE MUST BE BELOW C2 LOW
    # --------------------------------------------------------

    if c3_close >= c2_low:

        return None


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
    # C2 BODY
    # --------------------------------------------------------

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
    # FVG MUST BE INSIDE C2 BODY
    # --------------------------------------------------------

    if fvg_low < c2_body_low:

        return None


    if fvg_high > c2_body_high:

        return None


    # --------------------------------------------------------
    # FVG RATIO
    # --------------------------------------------------------

    fvg_ratio = (
        fvg_size
        /
        c2_body_size
    )


    # Minimum FVG ratio = 50%
    if fvg_ratio < FVG_MIN_RATIO:

        return None


    return {

        "fvg_low":
            fvg_low,

        "fvg_high":
            fvg_high,

        "fvg_size":
            fvg_size,

        "fvg_ratio":
            fvg_ratio,

        "c1_open_time":
            int(c1[0]),

        "c2_open_time":
            int(c2[0]),

        "c3_open_time":
            int(c3[0]),

        "c3_close_time":
            int(c3[6]),

        "c3_high":
            c3_high,

        "c3_close":
            candle_close(c3),

    }


# ============================================================
# REAL-TIME ROLLING FVG
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
            f"[INIT] "
            f"{symbol} {interval} "
            f"rolling window initialized"
        )


        return None


    last_time = int(
        state[
            "last_closed_open_time"
        ]
    )


    # --------------------------------------------------------
    # NO NEW CLOSED CANDLE
    # --------------------------------------------------------

    if latest_open_time <= last_time:

        return None


    old_window = state[
        "window"
    ]


    # --------------------------------------------------------
    # ROLLING 3-CANDLE WINDOW
    # --------------------------------------------------------

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
        f"[NEW CANDLE] "
        f"{symbol} {interval} "
        f"C3 closed"
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
# CREATE ACTIVE SETUP
# ============================================================

def create_setup(
    symbol,
    interval,
    fvg,
    volume_24h
):

    # Target specifically for timeframe
    target_percent = (
        FVG_TARGETS[
            interval
        ]
    )


    # C3 HIGH IS THE STARTING PRICE
    c3_high = fvg[
        "c3_high"
    ]


    # Target is calculated downward
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

        "fvg_ratio":
            fvg["fvg_ratio"],

        "c3_high":
            c3_high,

        "c3_close":
            fvg["c3_close"],

        "target":
            target,

        "target_percent":
            target_percent,

        "volume_24h":
            volume_24h,

        "created_at":
            int(
                time.time() * 1000
            ),

    }


    return setup


# ============================================================
# TARGET MESSAGE
# ============================================================

def format_target_message(setup):

    return (

        "🟢 <b>TARGET HIT</b>\n\n"

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

        "FVG setup completed.\n"
        "Bot is now looking for a NEW FVG."

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
# MONITOR ACTIVE SETUPS
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


            # ==================================================
            # TARGET HIT
            # ==================================================

            if current_price <= target:

                print(

                    f"[TARGET HIT] "
                    f"{symbol} "
                    f"{interval} "
                    f"C3 High="
                    f"{c3_high} "
                    f"Target="
                    f"{target} "
                    f"Price="
                    f"{current_price}"

                )


                # ------------------------------------------------
                # TELEGRAM ONLY HERE
                # ------------------------------------------------

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


            # ==================================================
            # CANCELLED
            # ==================================================

            if current_price > c3_high:

                print(

                    f"[CANCELLED] "
                    f"{symbol} "
                    f"{interval} "
                    f"C3 High="
                    f"{c3_high} "
                    f"Price="
                    f"{current_price}"

                )


                # ------------------------------------------------
                # NO TELEGRAM ON CANCEL
                # ------------------------------------------------

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

                f"[MONITOR ERROR] "
                f"{symbol} "
                f"{interval}: "
                f"{e}"

            )


# ============================================================
# SCAN
# ============================================================

def scan():

    print(
        "\n"
        +
        "=" * 70
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
        "=" * 70
    )


    # --------------------------------------------------------
    # FIRST MONITOR EXISTING ACTIVE SETUPS
    # --------------------------------------------------------

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
    # GET VOLUMES
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
    # VOLUME FILTER
    # ========================================================

    qualified_symbols = []


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

            qualified_symbols.append(
                symbol
            )


    print(

        f"[INFO] "
        f"Volume qualified: "
        f"{len(qualified_symbols)}"

    )


    # ========================================================
    # FVG SCAN
    # ========================================================

    for symbol in qualified_symbols:

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


                # ------------------------------------------------
                # EACH TIMEFRAME IS INDEPENDENT
                # ------------------------------------------------

                setup_key = (

                    symbol,

                    interval

                )


                # ------------------------------------------------
                # ONE ACTIVE FVG PER TIMEFRAME
                # ------------------------------------------------

                if setup_key in active_setups:

                    print(

                        f"[IGNORED] "
                        f"{symbol} {interval} "
                        f"new FVG found but "
                        f"this timeframe already has "
                        f"an active setup"

                    )

                    continue


                volume = volumes.get(
                    symbol,
                    0
                )


                setup = create_setup(

                    symbol,

                    interval,

                    fvg,

                    volume

                )


                active_setups[
                    setup_key
                ] = setup


                print(

                    f"[NEW FVG] "
                    f"{symbol} {interval} "
                    f"FVG="
                    f"{fvg['fvg_low']:.8g}"
                    f"-"
                    f"{fvg['fvg_high']:.8g} "
                    f"ratio="
                    f"{fvg['fvg_ratio'] * 100:.2f}% "
                    f"C3 High="
                    f"{setup['c3_high']:.8g} "
                    f"target="
                    f"{setup['target']:.8g} "
                    f"(-"
                    f"{setup['target_percent']}"
                    f"%)"

                )


                # =================================================
                # NO TELEGRAM MESSAGE HERE
                # =================================================
                #
                # FVG CREATED = NO TELEGRAM
                #
                # Telegram will ONLY be sent inside
                # monitor_active_setups() when TARGET HIT.
                #
                # =================================================


            except Exception as e:

                print(

                    f"[FVG ERROR] "
                    f"{symbol} "
                    f"{interval}: "
                    f"{e}"

                )


# ============================================================
# MAIN LOOP
# ============================================================

def main():

    print(
        "=" * 70
    )


    print(
        "BINANCE BEARISH FVG LIVE ALERT BOT"
    )


    print(
        "REAL-TIME ROLLING 3-CANDLE VERSION"
    )


    print(
        "5m + 15m + 30m + 1h INDEPENDENT ACTIVE FVG"
    )


    print(
        "TELEGRAM ALERT ONLY WHEN TARGET IS HIT"
    )


    print(
        "=" * 70
    )


    print(

        f"Min 24H Volume: "
        f"${MIN_QUOTE_VOLUME_24H:,.0f}"

    )


    print(

        f"FVG Minimum Ratio: "
        f"{FVG_MIN_RATIO * 100:.0f}%"

    )


    print(
        "Targets:"
    )


    for interval in FVG_INTERVALS:

        print(

            f"  {interval}: "
            f"{FVG_TARGETS[interval]}%"

        )


    print(

        f"Scan: "
        f"{SCAN_SECONDS} seconds"

    )


    print(
        "C1: GREEN or RED"
    )


    print(
        "C2: MUST BE RED"
    )


    print(
        "C3: GREEN or RED"
    )


    print(
        "C3 CLOSE: MUST BE BELOW C2 LOW"
    )


    print(
        "Historical FVG scan: DISABLED"
    )


    print(
        "EMA / Trend filter: DISABLED"
    )


    print(
        "5m + 15m + 30m + 1h: INDEPENDENT"
    )


    print(
        "One active FVG PER TIMEFRAME"
    )


    print(
        "Telegram: TARGET HIT ONLY"
    )


    print(
        "Telegram shows: C3 High -> Target"
    )


    print(
        "=" * 70
    )


    # ========================================================
    # MAIN LOOP
    # ========================================================

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

            SCAN_SECONDS - elapsed

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
