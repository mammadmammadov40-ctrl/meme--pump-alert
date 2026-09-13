import os
import time
import requests
from datetime import datetime, timezone


# ============================================================
# BINANCE RSI BULLISH DIVERGENCE LIVE ALERT BOT
# ============================================================
#
# STRATEGY
#
# 1) STARTUP:
#    Previous 50 CLOSED candles are used only as comparison
#    baseline.
#
# 2) FIRST LOW:
#    A new candle must make a lower low than all previous
#    50 candles.
#
# 3) SECOND LOW:
#    - Price makes lower low than FIRST LOW
#    - FIRST RSI < 30
#    - SECOND RSI < 30
#    - SECOND RSI > FIRST RSI
#
# 4) RSI RECOVERY:
#    When RSI closes above 30, DO NOT immediately signal.
#
# 5) CONFIRMATION:
#    Maximum 3 CLOSED candles are allowed.
#
#    At least 2 of these 3 conditions must pass:
#
#      A) RSI remains above 30
#      B) Price recovery / higher close
#      C) Volume is not weak
#
# 6) SIGNAL:
#    If 2 of 3 confirmation conditions pass:
#
#       BUY SIGNAL
#
# 7) FAILURE:
#    If price goes below SECOND LOW before confirmation:
#
#       SECOND LOW becomes NEW FIRST LOW
#
# 8) ATR:
#    NOT USED.
#
# ============================================================


BINANCE_BASE_URL = "https://api.binance.com"

MIN_QUOTE_VOLUME_24H = 20_000_000

SCAN_SECONDS = 60

RSI_PERIOD = 6

LOOKBACK_CANDLES = 50

RSI_LEVEL = 30

RSI_TIMEFRAMES = [
    "15m",
    "30m",
    "1h"
]

# ------------------------------------------------------------
# CONFIRMATION SETTINGS
# ------------------------------------------------------------

CONFIRMATION_MAX_CANDLES = 3

# Volume çox sərt deyil.
# Cari şamın volume-u əvvəlki 5 şamın orta volume-una
# ən azı 0.70 nisbətində olarsa PASS.
#
# Yəni volume mütləq çox böyük olmalı deyil.
# Sadəcə həddindən artıq zəif olmasın.
#
VOLUME_MIN_RATIO = 0.70

VOLUME_LOOKBACK = 5


TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN"
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID"
)


# ============================================================
# GLOBALS
# ============================================================

session = requests.Session()

states = {}


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(
    message,
    symbol=None
):

    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):
        print(
            "Telegram environment variables are missing."
        )
        return

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}"
        f"/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }

    if symbol:

        binance_url = (
            "https://www.binance.com/en/trade/"
            f"{symbol}_USDT?type=spot"
        )

        payload["reply_markup"] = {
            "inline_keyboard": [
                [
                    {
                        "text": f"🔗 {symbol} — Binance",
                        "url": binance_url
                    }
                ]
            ]
        }

    try:

        response = session.post(
            url,
            json=payload,
            timeout=15
        )

        if response.status_code != 200:

            print(
                "Telegram error:",
                response.text
            )

    except Exception as e:

        print(
            "Telegram exception:",
            e
        )


# ============================================================
# BINANCE SPOT USDT SYMBOLS
# ============================================================

def get_spot_usdt_symbols():

    url = (
        f"{BINANCE_BASE_URL}"
        f"/api/v3/exchangeInfo"
    )

    try:

        response = session.get(
            url,
            timeout=15
        )

        response.raise_for_status()

        data = response.json()

        symbols = []

        for item in data.get(
            "symbols",
            []
        ):

            if item.get("status") != "TRADING":
                continue

            if item.get("quoteAsset") != "USDT":
                continue

            if item.get(
                "isSpotTradingAllowed"
            ) is not True:
                continue

            symbols.append(
                item["symbol"]
            )

        return symbols

    except Exception as e:

        print(
            "Exchange info error:",
            e
        )

        return []


# ============================================================
# 24H VOLUME
# ============================================================

def get_24h_quote_volume(symbol):

    url = (
        f"{BINANCE_BASE_URL}"
        f"/api/v3/ticker/24hr"
    )

    try:

        response = session.get(
            url,
            params={
                "symbol": symbol
            },
            timeout=10
        )

        response.raise_for_status()

        data = response.json()

        return float(
            data.get(
                "quoteVolume",
                0
            )
        )

    except Exception:

        return 0.0


# ============================================================
# BINANCE KLINES
# ============================================================

def get_klines(
    symbol,
    interval,
    limit=200
):

    url = (
        f"{BINANCE_BASE_URL}"
        f"/api/v3/klines"
    )

    try:

        response = session.get(
            url,
            params={
                "symbol": symbol,
                "interval": interval,
                "limit": limit
            },
            timeout=15
        )

        response.raise_for_status()

        data = response.json()

        if not isinstance(
            data,
            list
        ):
            return []

        candles = []

        for k in data:

            candles.append({

                "time": int(k[0]),

                "open": float(k[1]),

                "high": float(k[2]),

                "low": float(k[3]),

                "close": float(k[4]),

                # --------------------------------------------
                # Volume
                # --------------------------------------------

                "volume": float(k[5]),

                # --------------------------------------------
                # Quote volume
                # --------------------------------------------

                "quote_volume": float(k[7]),

                # --------------------------------------------
                # Taker buy base volume
                # --------------------------------------------

                "taker_buy_volume": float(k[9]),

                # --------------------------------------------
                # Taker buy quote volume
                # --------------------------------------------

                "taker_buy_quote_volume": float(k[10])
            })

        return candles

    except Exception as e:

        print(
            f"Kline error "
            f"{symbol} {interval}:",
            e
        )

        return []


# ============================================================
# CLOSED CANDLES ONLY
# ============================================================

def get_closed_candles(
    symbol,
    interval,
    limit=200
):

    candles = get_klines(
        symbol,
        interval,
        limit
    )

    if len(candles) < 2:
        return []

    # Son candle hazırda formalaşan candle-dır.
    # Onu çıxarırıq.

    return candles[:-1]


# ============================================================
# RSI - WILDER RSI
# ============================================================

def calculate_rsi(
    candles,
    period=6
):

    if len(candles) <= period:

        return [
            None
            for _ in candles
        ]

    closes = [
        candle["close"]
        for candle in candles
    ]

    rsi = [
        None
        for _ in closes
    ]

    gains = []
    losses = []

    for i in range(
        1,
        period + 1
    ):

        change = (
            closes[i]
            - closes[i - 1]
        )

        gains.append(
            max(change, 0.0)
        )

        losses.append(
            max(-change, 0.0)
        )

    avg_gain = (
        sum(gains)
        / period
    )

    avg_loss = (
        sum(losses)
        / period
    )

    if avg_loss == 0:

        rsi[period] = 100.0

    else:

        rs = (
            avg_gain
            / avg_loss
        )

        rsi[period] = (
            100.0
            -
            (
                100.0
                /
                (1.0 + rs)
            )
        )

    for i in range(
        period + 1,
        len(closes)
    ):

        change = (
            closes[i]
            - closes[i - 1]
        )

        gain = max(
            change,
            0.0
        )

        loss = max(
            -change,
            0.0
        )

        avg_gain = (
            (
                avg_gain
                * (period - 1)
            )
            + gain
        ) / period

        avg_loss = (
            (
                avg_loss
                * (period - 1)
            )
            + loss
        ) / period

        if avg_loss == 0:

            rsi[i] = 100.0

        else:

            rs = (
                avg_gain
                / avg_loss
            )

            rsi[i] = (
                100.0
                -
                (
                    100.0
                    /
                    (1.0 + rs)
                )
            )

    return rsi


# ============================================================
# NEW STATE
# ============================================================

def new_state():

    return {

        "startup_initialized": False,

        "last_candle_time": None,

        # ----------------------------------------------------
        # 50 candle comparison baseline
        # ----------------------------------------------------

        "comparison_window": [],

        # ----------------------------------------------------
        # FIRST LOW
        # ----------------------------------------------------

        "first_low": None,

        "first_rsi": None,

        "first_time": None,

        "bars_since_first": 0,

        # ----------------------------------------------------
        # SECOND LOW
        # ----------------------------------------------------

        "candidate_active": False,

        "second_low": None,

        "second_rsi": None,

        "second_time": None,

        # ----------------------------------------------------
        # RSI > 30 confirmation stage
        # ----------------------------------------------------

        "confirmation_active": False,

        "confirmation_candles": 0,

        "confirmation_start_time": None,

        "confirmation_start_price": None,

        "confirmation_start_rsi": None,

        # ----------------------------------------------------
        # INFO
        # ----------------------------------------------------

        "symbol": None,

        "interval": None
    }


# ============================================================
# RESET SETUP
# ============================================================

def reset_setup(state):

    state["first_low"] = None

    state["first_rsi"] = None

    state["first_time"] = None

    state["bars_since_first"] = 0

    state["candidate_active"] = False

    state["second_low"] = None

    state["second_rsi"] = None

    state["second_time"] = None

    state["confirmation_active"] = False

    state["confirmation_candles"] = 0

    state["confirmation_start_time"] = None

    state["confirmation_start_price"] = None

    state["confirmation_start_rsi"] = None

    # Yeni 50-lik rolling baza qurulur.

    state["comparison_window"] = []


# ============================================================
# STARTUP
# ============================================================

def startup_initialize(
    state,
    candles,
    rsi_values
):

    if len(candles) < LOOKBACK_CANDLES:

        return False

    state["comparison_window"] = []

    start_index = (
        len(candles)
        - LOOKBACK_CANDLES
    )

    for i in range(
        start_index,
        len(candles)
    ):

        state["comparison_window"].append({

            "time": candles[i]["time"],

            "low": candles[i]["low"],

            "rsi": rsi_values[i]

        })

    state["startup_initialized"] = True

    state["last_candle_time"] = (
        candles[-1]["time"]
    )

    print(
        f"{state['symbol']} "
        f"{state['interval']} -> "
        f"STARTUP: previous 50 closed candles "
        f"loaded."
    )

    return True


# ============================================================
# NEW FIRST LOW
# ============================================================

def make_new_first_low(
    state,
    candle,
    rsi_value
):

    state["first_low"] = (
        candle["low"]
    )

    state["first_rsi"] = (
        rsi_value
    )

    state["first_time"] = (
        candle["time"]
    )

    state["bars_since_first"] = 0

    state["candidate_active"] = False

    state["second_low"] = None

    state["second_rsi"] = None

    state["second_time"] = None

    state["confirmation_active"] = False

    state["confirmation_candles"] = 0

    state["confirmation_start_time"] = None

    state["confirmation_start_price"] = None

    state["confirmation_start_rsi"] = None

    print(
        f"{state['symbol']} "
        f"{state['interval']} -> "
        f"NEW FIRST LOW: "
        f"{candle['low']} | "
        f"RSI: {rsi_value:.2f}"
    )


# ============================================================
# NO FIRST LOW
# ============================================================

def process_no_first_low(
    state,
    candle,
    rsi_value
):

    window = state[
        "comparison_window"
    ]

    if len(window) < LOOKBACK_CANDLES:

        window.append({

            "time": candle["time"],

            "low": candle["low"],

            "rsi": rsi_value

        })

        return

    lowest_previous_50 = min(
        item["low"]
        for item in window
    )

    if candle["low"] < lowest_previous_50:

        make_new_first_low(
            state,
            candle,
            rsi_value
        )

        state["comparison_window"] = []

        return

    window.pop(0)

    window.append({

        "time": candle["time"],

        "low": candle["low"],

        "rsi": rsi_value

    })


# ============================================================
# VOLUME CHECK
# ============================================================

def check_volume_confirmation(
    candles,
    index
):

    # Əvvəlki 5 şam lazımdır.

    if index < VOLUME_LOOKBACK:

        return False, 0.0

    current_volume = (
        candles[index]["volume"]
    )

    previous_volumes = [

        candles[j]["volume"]

        for j in range(
            index - VOLUME_LOOKBACK,
            index
        )
    ]

    if not previous_volumes:

        return False, 0.0

    average_volume = (
        sum(previous_volumes)
        /
        len(previous_volumes)
    )

    if average_volume <= 0:

        return False, 0.0

    ratio = (
        current_volume
        /
        average_volume
    )

    passed = (
        ratio
        >= VOLUME_MIN_RATIO
    )

    return passed, ratio


# ============================================================
# CONFIRMATION
# ============================================================

def process_confirmation(
    state,
    candle,
    rsi_value,
    candles,
    index
):

    state["confirmation_candles"] += 1

    second_low = state[
        "second_low"
    ]

    # ========================================================
    # 1. SECOND LOW PROTECTION
    # ========================================================

    if candle["low"] < second_low:

        print(
            f"{state['symbol']} "
            f"{state['interval']} -> "
            f"CONFIRMATION FAILED."
        )

        print(
            f"Price broke SECOND LOW."
        )

        print(
            f"SECOND LOW becomes NEW FIRST LOW."
        )

        state["first_low"] = (
            state["second_low"]
        )

        state["first_rsi"] = (
            state["second_rsi"]
        )

        state["first_time"] = (
            state["second_time"]
        )

        state["bars_since_first"] = 0

        state["candidate_active"] = False

        state["second_low"] = None

        state["second_rsi"] = None

        state["second_time"] = None

        state["confirmation_active"] = False

        state["confirmation_candles"] = 0

        state["confirmation_start_time"] = None

        state["confirmation_start_price"] = None

        state["confirmation_start_rsi"] = None

        return

    # ========================================================
    # CONDITION A
    # RSI remains above 30
    # ========================================================

    rsi_pass = (
        rsi_value > RSI_LEVEL
    )

    # ========================================================
    # CONDITION B
    # PRICE MOMENTUM / RECOVERY
    #
    # Cari bağlanış confirmation başlanğıcındakı qiymətdən
    # yuxarıdırsa PASS.
    #
    # Əlavə olaraq cari close əvvəlki close-dan aşağı
    # deyilsə PASS.
    # ========================================================

    price_pass = False

    if (
        state["confirmation_start_price"]
        is not None
    ):

        price_above_start = (
            candle["close"]
            >
            state["confirmation_start_price"]
        )

        previous_close = None

        if index > 0:

            previous_close = (
                candles[index - 1]["close"]
            )

        higher_than_previous = (

            previous_close is not None

            and

            candle["close"]
            >=
            previous_close
        )

        if (
            price_above_start
            and
            higher_than_previous
        ):

            price_pass = True

    # ========================================================
    # CONDITION C
    # VOLUME
    # ========================================================

    volume_pass, volume_ratio = (
        check_volume_confirmation(
            candles,
            index
        )
    )

    # ========================================================
    # COUNT
    # ========================================================

    passed_conditions = 0

    if rsi_pass:
        passed_conditions += 1

    if price_pass:
        passed_conditions += 1

    if volume_pass:
        passed_conditions += 1

    print(
        f"{state['symbol']} "
        f"{state['interval']} -> "
        f"CONFIRMATION "
        f"{state['confirmation_candles']}/"
        f"{CONFIRMATION_MAX_CANDLES}"
    )

    print(
        f"RSI: "
        f"{'PASS' if rsi_pass else 'FAIL'} "
        f"({rsi_value:.2f})"
    )

    print(
        f"Price momentum: "
        f"{'PASS' if price_pass else 'FAIL'}"
    )

    print(
        f"Volume: "
        f"{'PASS' if volume_pass else 'FAIL'} "
        f"(ratio: {volume_ratio:.2f})"
    )

    print(
        f"Confirmation score: "
        f"{passed_conditions}/3"
    )

    # ========================================================
    # 2 / 3 PASS
    # ========================================================

    if passed_conditions >= 2:

        print(
            f"{state['symbol']} "
            f"{state['interval']} -> "
            f"CONFIRMATION PASSED."
        )

        print(
            f"{state['symbol']} "
            f"{state['interval']} -> "
            f"SIGNAL."
        )

        send_rsi_signal(
            state,
            candle["close"],
            rsi_value
        )

        reset_setup(
            state
        )

        return

    # ========================================================
    # 3 CANDLE LIMIT
    # ========================================================

    if (
        state["confirmation_candles"]
        >= CONFIRMATION_MAX_CANDLES
    ):

        print(
            f"{state['symbol']} "
            f"{state['interval']} -> "
            f"3 confirmation candles completed."
        )

        print(
            f"{state['symbol']} "
            f"{state['interval']} -> "
            f"Confirmation failed."
        )

        # ----------------------------------------------------
        # Confirmation uğursuz oldu.
        #
        # Amma qiymət hələ 2-ci dibin üstündədirsə,
        # həmin 2-ci dib yenidən FIRST LOW kimi saxlanılır.
        # ----------------------------------------------------

        state["first_low"] = (
            state["second_low"]
        )

        state["first_rsi"] = (
            state["second_rsi"]
        )

        state["first_time"] = (
            state["second_time"]
        )

        state["bars_since_first"] = 0

        state["candidate_active"] = False

        state["second_low"] = None

        state["second_rsi"] = None

        state["second_time"] = None

        state["confirmation_active"] = False

        state["confirmation_candles"] = 0

        state["confirmation_start_time"] = None

        state["confirmation_start_price"] = None

        state["confirmation_start_rsi"] = None

        print(
            f"{state['symbol']} "
            f"{state['interval']} -> "
            f"SECOND LOW retained as NEW FIRST LOW."
        )


# ============================================================
# SEND SIGNAL
# ============================================================

def send_rsi_signal(
    state,
    signal_price,
    signal_rsi
):

    first_time = datetime.fromtimestamp(
        state["first_time"] / 1000,
        tz=timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )

    second_time = datetime.fromtimestamp(
        state["second_time"] / 1000,
        tz=timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )

    first_low = state[
        "first_low"
    ]

    second_low = state[
        "second_low"
    ]

    price_first_to_second = (
        (
            second_low
            - first_low
        )
        /
        first_low
    ) * 100

    price_second_to_signal = (
        (
            signal_price
            - second_low
        )
        /
        second_low
    ) * 100

    message = (

        "🟢 <b>RSI BULLISH DIVERGENCE</b>\n\n"

        f"<b>{state['symbol']}</b> "
        f"— <b>{state['interval']}</b>\n\n"

        "1️⃣ <b>FIRST LOW</b>\n"

        f"Price: "
        f"{first_low:.8f}\n"

        f"RSI: "
        f"{state['first_rsi']:.2f}\n"

        f"Time: "
        f"{first_time}\n\n"

        "2️⃣ <b>SECOND LOW</b>\n"

        f"Price: "
        f"{second_low:.8f}\n"

        f"RSI: "
        f"{state['second_rsi']:.2f}\n"

        f"Time: "
        f"{second_time}\n\n"

        "📉 <b>PRICE</b>\n"

        f"{first_low:.8f}"
        f" → "
        f"{second_low:.8f}\n"

        f"Change: "
        f"{price_first_to_second:.2f}%\n\n"

        "📈 <b>RSI</b>\n"

        f"{state['first_rsi']:.2f}"
        f" → "
        f"{state['second_rsi']:.2f}"
        f" → "
        f"{signal_rsi:.2f}\n\n"

        "🛡 <b>CONFIRMATION PASSED</b>\n"

        "RSI recovery + price/volume confirmation\n\n"

        "🚀 <b>BUY SIGNAL</b>\n"

        f"Signal Price: "
        f"{signal_price:.8f}\n"

        f"RSI: "
        f"{signal_rsi:.2f}\n"

        f"Price movement from 2nd low: "
        f"+{price_second_to_signal:.2f}%\n\n"

        "⚠️ Confirmation does not guarantee "
        "that price cannot fall again."
    )

    send_telegram(
        message,
        state["symbol"]
    )


# ============================================================
# FIRST LOW PROCESS
# ============================================================

def process_first_low(
    state,
    candle,
    rsi_value,
    candles,
    index
):

    state["bars_since_first"] += 1

    # ========================================================
    # CONFIRMATION ACTIVE
    # ========================================================

    if state["confirmation_active"]:

        process_confirmation(
            state,
            candle,
            rsi_value,
            candles,
            index
        )

        return

    # ========================================================
    # SECOND LOW ALREADY VALID
    #
    # RSI 30 keçməyib
    # ========================================================

    if state["candidate_active"]:

        second_low = state[
            "second_low"
        ]

        # ----------------------------------------------------
        # SECOND LOW BREAK
        # ----------------------------------------------------

        if candle["low"] < second_low:

            print(
                f"{state['symbol']} "
                f"{state['interval']} -> "
                f"Price broke SECOND LOW."
            )

            print(
                f"SECOND LOW becomes NEW FIRST LOW."
            )

            state["first_low"] = (
                state["second_low"]
            )

            state["first_rsi"] = (
                state["second_rsi"]
            )

            state["first_time"] = (
                state["second_time"]
            )

            state["bars_since_first"] = 0

            state["candidate_active"] = False

            state["second_low"] = None

            state["second_rsi"] = None

            state["second_time"] = None

            return

        # ----------------------------------------------------
        # RSI > 30
        #
        # Dərhal signal YOX.
        # Confirmation başlayır.
        # ----------------------------------------------------

        if rsi_value > RSI_LEVEL:

            state["confirmation_active"] = True

            state["confirmation_candles"] = 0

            state["confirmation_start_time"] = (
                candle["time"]
            )

            state["confirmation_start_price"] = (
                candle["close"]
            )

            state["confirmation_start_rsi"] = (
                rsi_value
            )

            print(
                f"{state['symbol']} "
                f"{state['interval']} -> "
                f"RSI crossed above 30."
            )

            print(
                f"{state['symbol']} "
                f"{state['interval']} -> "
                f"Confirmation started."
            )

            print(
                f"{state['symbol']} "
                f"{state['interval']} -> "
                f"Waiting maximum "
                f"{CONFIRMATION_MAX_CANDLES} "
                f"closed candles."
            )

            # ------------------------------------------------
            # Vacib:
            # RSI 30-u keçən candle özü confirmation
            # candle kimi hesablanmır.
            #
            # Ondan SONRA gələn candle-lar yoxlanılır.
            # ------------------------------------------------

            return

        # ----------------------------------------------------
        # RSI hələ 30-dan aşağıdır.
        # ----------------------------------------------------

        return

    # ========================================================
    # FIRST LOW-DAN SONRA MAXIMUM 50 ŞAM
    # ========================================================

    if (
        state["bars_since_first"]
        > LOOKBACK_CANDLES
    ):

        print(
            f"{state['symbol']} "
            f"{state['interval']} -> "
            f"50 candles passed."
        )

        reset_setup(
            state
        )

        return

    # ========================================================
    # SECOND LOW SEARCH
    # ========================================================

    if candle["low"] < state["first_low"]:

        valid_rsi_divergence = (

            state["first_rsi"]
            < RSI_LEVEL

            and

            rsi_value
            < RSI_LEVEL

            and

            rsi_value
            >
            state["first_rsi"]
        )

        if valid_rsi_divergence:

            state["candidate_active"] = True

            state["second_low"] = (
                candle["low"]
            )

            state["second_rsi"] = (
                rsi_value
            )

            state["second_time"] = (
                candle["time"]
            )

            print(
                f"{state['symbol']} "
                f"{state['interval']} -> "
                f"VALID SECOND LOW FOUND"
            )

            print(
                f"First Low: "
                f"{state['first_low']}"
            )

            print(
                f"First RSI: "
                f"{state['first_rsi']:.2f}"
            )

            print(
                f"Second Low: "
                f"{state['second_low']}"
            )

            print(
                f"Second RSI: "
                f"{state['second_rsi']:.2f}"
            )

            print(
                f"{state['symbol']} "
                f"{state['interval']} -> "
                f"Waiting for RSI > 30."
            )

            return

        # ----------------------------------------------------
        # Lower low var, divergence yoxdur.
        # Cari candle yeni first low olur.
        # ----------------------------------------------------

        print(
            f"{state['symbol']} "
            f"{state['interval']} -> "
            f"Lower low found, but divergence failed."
        )

        make_new_first_low(
            state,
            candle,
            rsi_value
        )

        return


# ============================================================
# PROCESS SYMBOL + INTERVAL
# ============================================================

def process_symbol_interval(
    symbol,
    interval
):

    key = (
        symbol,
        interval
    )

    if key not in states:

        states[key] = new_state()

    state = states[key]

    state["symbol"] = symbol

    state["interval"] = interval

    candles = get_closed_candles(
        symbol,
        interval,
        limit=200
    )

    if len(candles) < (
        LOOKBACK_CANDLES
        + RSI_PERIOD
        + VOLUME_LOOKBACK
        + 5
    ):

        return

    rsi_values = calculate_rsi(
        candles,
        RSI_PERIOD
    )

    # ========================================================
    # STARTUP
    # ========================================================

    if not state[
        "startup_initialized"
    ]:

        startup_initialize(
            state,
            candles,
            rsi_values
        )

        return

    # ========================================================
    # ONLY NEW CLOSED CANDLES
    # ========================================================

    last_time = (
        state["last_candle_time"]
    )

    new_indices = []

    for i, candle in enumerate(
        candles
    ):

        if last_time is None:
            continue

        if (
            candle["time"]
            >
            last_time
            and
            rsi_values[i]
            is not None
        ):

            new_indices.append(i)

    if not new_indices:

        return

    # ========================================================
    # PROCESS
    # ========================================================

    for i in new_indices:

        candle = candles[i]

        rsi_value = rsi_values[i]

        if rsi_value is None:
            continue

        if state["first_low"] is None:

            process_no_first_low(
                state,
                candle,
                rsi_value
            )

        else:

            process_first_low(
                state,
                candle,
                rsi_value,
                candles,
                i
            )

        state["last_candle_time"] = (
            candle["time"]
        )


# ============================================================
# SCAN
# ============================================================

def scan():

    symbols = get_spot_usdt_symbols()

    if not symbols:

        print(
            "No USDT Spot symbols found."
        )

        return

    print(
        f"Scanning "
        f"{len(symbols)} USDT Spot symbols..."
    )

    for symbol in symbols:

        volume_24h = (
            get_24h_quote_volume(
                symbol
            )
        )

        if volume_24h < (
            MIN_QUOTE_VOLUME_24H
        ):

            continue

        for interval in (
            RSI_TIMEFRAMES
        ):

            try:

                process_symbol_interval(
                    symbol,
                    interval
                )

            except Exception as e:

                print(
                    f"Processing error "
                    f"{symbol} "
                    f"{interval}: "
                    f"{e}"
                )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)

    print(
        "BINANCE RSI BULLISH DIVERGENCE BOT"
    )

    print("=" * 60)

    print(
        f"RSI Period: "
        f"{RSI_PERIOD}"
    )

    print(
        f"RSI Level: "
        f"< {RSI_LEVEL}"
    )

    print(
        f"Timeframes: "
        f"{', '.join(RSI_TIMEFRAMES)}"
    )

    print(
        f"Comparison candles: "
        f"{LOOKBACK_CANDLES}"
    )

    print(
        f"Confirmation candles: "
        f"{CONFIRMATION_MAX_CANDLES}"
    )

    print(
        f"Required confirmation: "
        f"2 / 3"
    )

    print(
        f"Volume minimum ratio: "
        f"{VOLUME_MIN_RATIO}"
    )

    print(
        "ATR: DISABLED"
    )

    print("=" * 60)

    print(
        "STRATEGY:"
    )

    print(
        "First Low -> Second Low -> "
        "RSI > 30 -> Confirmation -> Signal"
    )

    print("=" * 60)

    while True:

        started = time.time()

        try:

            scan()

        except Exception as e:

            print(
                "Main scan error:",
                e
            )

        elapsed = (
            time.time()
            - started
        )

        sleep_time = max(
            1,
            SCAN_SECONDS
            - elapsed
        )

        time.sleep(
            sleep_time
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
