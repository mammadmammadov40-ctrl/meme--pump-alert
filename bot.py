import os
import time
import requests
from datetime import datetime, timezone


# ============================================================
# BINANCE RSI BULLISH DIVERGENCE + MOMENTUM CONFIRMATION BOT
# ============================================================
#
# STRATEGY
#
# 1) STARTUP:
#    Previous 50 CLOSED candles are loaded only as a
#    comparison baseline.
#
# 2) FIRST LOW:
#    A new candle becomes FIRST LOW if its low is below
#    all previous 50 closed candles.
#
# 3) SECOND LOW:
#    Price must make a lower low than FIRST LOW.
#
#    RSI conditions:
#       - First RSI < 30
#       - Second RSI < 30
#       - Second RSI > First RSI
#
# 4) IMPORTANT CHANGE:
#
#    RSI > 30 NO LONGER MEANS IMMEDIATE BUY.
#
#    RSI > 30 starts a CONFIRMATION PHASE.
#
# 5) CONFIRMATION PHASE:
#
#    After RSI closes above 30, the bot analyzes the next
#    3 CLOSED candles.
#
#    It checks:
#
#       - RSI remains above 30
#       - Price remains above SECOND LOW
#       - Taker BUY pressure
#       - BUY/SELL percentage
#       - Volume strength
#       - Price momentum
#       - ATR recovery from SECOND LOW
#       - Candle closing strength
#       - Sudden selling pressure
#
# 6) BUY PRESSURE:
#
#    Binance kline data:
#
#       Total Quote Volume
#       Taker Buy Quote Volume
#
#    Estimated Sell Quote Volume:
#
#       Sell = Total - Taker Buy
#
#    Buy percentage:
#
#       Buy / Total * 100
#
# 7) ATR:
#
#    Confirmation requires price to move at least
#    0.5 ATR above SECOND LOW.
#
# 8) SELLING PRESSURE PROTECTION:
#
#    If a large-volume candle appears with strong selling
#    pressure, confirmation is rejected.
#
# 9) RSI FAILURE:
#
#    If RSI goes back below 30 during confirmation,
#    confirmation is cancelled.
#
# 10) SECOND LOW BREAK:
#
#    If price breaks SECOND LOW:
#
#       - Current setup is cancelled.
#       - SECOND LOW becomes NEW FIRST LOW.
#
# 11) SIGNAL:
#
#    BUY signal is sent only after confirmation passes.
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


# ============================================================
# CONFIRMATION SETTINGS
# ============================================================

# RSI 30-dan sonra neçə bağlanmış şam analiz ediləcək
CONFIRMATION_CANDLES = 3


# 2-ci dibdən minimum recovery:
#
# 0.5 ATR = qiymət 2-ci dibdən ən azı yarım ATR
# yuxarı qalxmalıdır.
#
ATR_PERIOD = 14

ATR_RECOVERY_MULTIPLIER = 0.50


# ------------------------------------------------------------
# BUY PRESSURE
# ------------------------------------------------------------

# Hər confirmation şamında minimum BUY %
#
# Məsələn:
# 60% buy / 40% sell -> keçir
# 52% buy / 48% sell -> keçmir
#
MIN_BUY_PERCENT = 55.0


# Son 3 şamın orta BUY faizi
MIN_AVG_BUY_PERCENT = 57.0


# Son 3 şamın ən az neçə dənəsində BUY > SELL olmalıdır
MIN_BUY_DOMINANCE_CANDLES = 2


# ------------------------------------------------------------
# MOMENTUM
# ------------------------------------------------------------

# Son 3 şamın maksimum close qiyməti 2-ci dibdən
# ən azı bu qədər ATR uzaqda olmalıdır.
MIN_PRICE_RECOVERY_ATR = 0.50


# Son confirmation şamının close-u əvvəlki close-dan
# ən azı bu qədər yaxşı olmalıdır.
#
# 0.0 = sadəcə yuxarı bağlanması kifayətdir.
MIN_LAST_CLOSE_PROGRESS_ATR = 0.05


# ------------------------------------------------------------
# VOLUME
# ------------------------------------------------------------

# Son confirmation şamı əvvəlki 20 şamın orta
# həcminin bundan az hissəsidirsə, momentum zəif hesab edilir.
MIN_VOLUME_VS_AVG20 = 0.80


# Böyük satış şamını müəyyən etmək üçün:
#
# Əgər volume >= AVG20 * 1.5
# və SELL >= 60%
# olarsa -> güclü satış təzyiqi
#
SELL_VOLUME_SPIKE_MULTIPLIER = 1.50

STRONG_SELL_PERCENT = 60.0


# ------------------------------------------------------------
# CANDLE QUALITY
# ------------------------------------------------------------

# Son confirmation şamında close-un candle range daxilində
# minimum yerləşmə faizi.
#
# 0.60 = close range-in yuxarı 60%-lik hissəsində olmalıdır.
#
MIN_CLOSE_LOCATION = 0.60


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

    # --------------------------------------------------------
    # COIN ÜZƏRİNƏ BASANDA BINANCE AÇILSIN
    # --------------------------------------------------------

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

            total_quote_volume = float(k[7])

            taker_buy_quote_volume = float(k[10])

            taker_sell_quote_volume = (
                total_quote_volume
                - taker_buy_quote_volume
            )

            if total_quote_volume > 0:

                buy_percent = (
                    taker_buy_quote_volume
                    / total_quote_volume
                ) * 100

                sell_percent = (
                    taker_sell_quote_volume
                    / total_quote_volume
                ) * 100

            else:

                buy_percent = 0.0

                sell_percent = 0.0

            candles.append({

                "time": int(k[0]),

                "open": float(k[1]),

                "high": float(k[2]),

                "low": float(k[3]),

                "close": float(k[4]),

                "volume": float(k[5]),

                "quote_volume": total_quote_volume,

                "buy_quote_volume":
                    taker_buy_quote_volume,

                "sell_quote_volume":
                    taker_sell_quote_volume,

                "buy_percent":
                    buy_percent,

                "sell_percent":
                    sell_percent
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

    # Son kline hələ formalaşır.
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
            - (
                100.0
                / (1.0 + rs)
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
                - (
                    100.0
                    / (1.0 + rs)
                )
            )

    return rsi


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    candles,
    period=14
):

    if len(candles) <= period:

        return [
            None
            for _ in candles
        ]

    atr = [
        None
        for _ in candles
    ]

    true_ranges = []

    for i in range(
        len(candles)
    ):

        high = candles[i]["high"]

        low = candles[i]["low"]

        if i == 0:

            tr = high - low

        else:

            previous_close = (
                candles[i - 1]["close"]
            )

            tr = max(
                high - low,
                abs(
                    high
                    - previous_close
                ),
                abs(
                    low
                    - previous_close
                )
            )

        true_ranges.append(tr)

    initial_atr = (
        sum(
            true_ranges[
                1:period + 1
            ]
        )
        / period
    )

    atr[period] = initial_atr

    previous_atr = initial_atr

    for i in range(
        period + 1,
        len(candles)
    ):

        current_atr = (
            (
                previous_atr
                * (period - 1)
            )
            + true_ranges[i]
        ) / period

        atr[i] = current_atr

        previous_atr = current_atr

    return atr


# ============================================================
# NEW STATE
# ============================================================

def new_state():

    return {

        # ----------------------------------------------------
        # STARTUP
        # ----------------------------------------------------

        "startup_initialized": False,

        "last_candle_time": None,

        # ----------------------------------------------------
        # 50-CANDLE COMPARISON WINDOW
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
        # RSI RECLAIM / CONFIRMATION
        # ----------------------------------------------------

        "confirmation_active": False,

        "confirmation_candles": [],

        "reclaim_time": None,

        "reclaim_price": None,

        "reclaim_rsi": None,

        "reclaim_atr": None,

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

    state["confirmation_candles"] = []

    state["reclaim_time"] = None

    state["reclaim_price"] = None

    state["reclaim_rsi"] = None

    state["reclaim_atr"] = None

    # Köhnə tarixçə yenidən istifadə edilmir.

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

            "time":
                candles[i]["time"],

            "low":
                candles[i]["low"],

            "rsi":
                rsi_values[i]

        })

    state["startup_initialized"] = True

    state["last_candle_time"] = (
        candles[-1]["time"]
    )

    print(
        f"{state['symbol']} "
        f"{state['interval']} -> "
        f"STARTUP: previous 50 closed "
        f"candles loaded."
    )

    print(
        f"{state['symbol']} "
        f"{state['interval']} -> "
        f"No historical first low selected."
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

    state["confirmation_candles"] = []

    state["reclaim_time"] = None

    state["reclaim_price"] = None

    state["reclaim_rsi"] = None

    state["reclaim_atr"] = None

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

            "time":
                candle["time"],

            "low":
                candle["low"],

            "rsi":
                rsi_value

        })

        print(
            f"{state['symbol']} "
            f"{state['interval']} -> "
            f"Building comparison window: "
            f"{len(window)}/50"
        )

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

        "time":
            candle["time"],

        "low":
            candle["low"],

        "rsi":
            rsi_value

    })


# ============================================================
# CANDLE ANALYSIS HELPERS
# ============================================================

def candle_close_location(candle):

    candle_range = (
        candle["high"]
        - candle["low"]
    )

    if candle_range <= 0:

        return 0.0

    return (
        (
            candle["close"]
            - candle["low"]
        )
        / candle_range
    )


def is_strong_selling_candle(
    candle,
    average_volume
):

    if average_volume <= 0:

        return False

    volume_spike = (
        candle["volume"]
        >= (
            average_volume
            * SELL_VOLUME_SPIKE_MULTIPLIER
        )
    )

    strong_sell = (
        candle["sell_percent"]
        >= STRONG_SELL_PERCENT
    )

    return (
        volume_spike
        and strong_sell
    )


def get_average_volume(
    candles,
    count=20
):

    if not candles:

        return 0.0

    selected = candles[-count:]

    if not selected:

        return 0.0

    return (
        sum(
            candle["volume"]
            for candle in selected
        )
        / len(selected)
    )


# ============================================================
# SEND RSI SIGNAL
# ============================================================

def send_rsi_signal(
    state,
    signal_price,
    signal_rsi,
    confirmation_data
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

    reclaim_time = datetime.fromtimestamp(
        state["reclaim_time"] / 1000,
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
        / first_low
    ) * 100

    price_second_to_signal = (
        (
            signal_price
            - second_low
        )
        / second_low
    ) * 100

    atr_value = (
        state["reclaim_atr"]
    )

    if atr_value and atr_value > 0:

        atr_recovery = (
            signal_price
            - second_low
        ) / atr_value

    else:

        atr_recovery = 0.0

    avg_buy = confirmation_data[
        "avg_buy_percent"
    ]

    buy_dominance = confirmation_data[
        "buy_dominance"
    ]

    volume_ratio = confirmation_data[
        "volume_ratio"
    ]

    momentum_percent = (
        confirmation_data[
            "momentum_percent"
        ]
    )

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

        f"{first_low:.8f} "
        f"→ "
        f"{second_low:.8f}\n"

        f"Change: "
        f"{price_first_to_second:.2f}%\n\n"

        "📈 <b>RSI</b>\n"

        f"{state['first_rsi']:.2f} "
        f"→ "
        f"{state['second_rsi']:.2f} "
        f"→ "
        f"{signal_rsi:.2f}\n\n"

        "🔓 <b>RSI 30 RECLAIM</b>\n"

        f"Price: "
        f"{state['reclaim_price']:.8f}\n"

        f"RSI: "
        f"{state['reclaim_rsi']:.2f}\n"

        f"Time: "
        f"{reclaim_time}\n\n"

        "📊 <b>MOMENTUM CONFIRMATION</b>\n"

        f"Avg Buy Pressure: "
        f"{avg_buy:.1f}%\n"

        f"BUY Dominance: "
        f"{buy_dominance}/"
        f"{CONFIRMATION_CANDLES} candles\n"

        f"Volume / Avg20: "
        f"{volume_ratio:.2f}x\n"

        f"Price Momentum: "
        f"+{momentum_percent:.2f}%\n"

        f"ATR Recovery: "
        f"{atr_recovery:.2f} ATR\n\n"

        "🟢 <b>BUY CONFIRMED</b>\n"

        f"Signal Price: "
        f"{signal_price:.8f}\n"

        f"RSI: "
        f"{signal_rsi:.2f}\n"

        f"From 2nd Low: "
        f"+{price_second_to_signal:.2f}%\n\n"

        "🛡️ <b>CONFIRMATION PASSED</b>\n"

        "Buy pressure + momentum + volume "
        "were sufficient.\n\n"

        "🔄 <b>Setup completed.</b>\n"

        "Bot is now looking for a "
        "<b>NEW FIRST LOW</b>."
    )

    send_telegram(
        message,
        state["symbol"]
    )


# ============================================================
# CONFIRMATION ANALYSIS
# ============================================================

def analyze_confirmation(
    state,
    candles,
    rsi_values,
    atr_values,
    current_index
):

    confirmation = state[
        "confirmation_candles"
    ]

    if len(confirmation) < CONFIRMATION_CANDLES:

        return False, None

    second_low = state[
        "second_low"
    ]

    # --------------------------------------------------------
    # RSI CHECK
    # --------------------------------------------------------

    for item in confirmation:

        if item["rsi"] <= RSI_LEVEL:

            print(
                f"{state['symbol']} "
                f"{state['interval']} -> "
                f"CONFIRMATION FAILED: "
                f"RSI returned below/equal 30."
            )

            return False, None

    # --------------------------------------------------------
    # SECOND LOW PROTECTION
    # --------------------------------------------------------

    for item in confirmation:

        if item["low"] < second_low:

            print(
                f"{state['symbol']} "
                f"{state['interval']} -> "
                f"CONFIRMATION FAILED: "
                f"Second low broken."
            )

            return False, None

    # --------------------------------------------------------
    # BUY PRESSURE
    # --------------------------------------------------------

    buy_percentages = [
        item["buy_percent"]
        for item in confirmation
    ]

    avg_buy_percent = (
        sum(buy_percentages)
        / len(buy_percentages)
    )

    buy_dominance = sum(
        1
        for item in confirmation
        if item["buy_percent"]
        > item["sell_percent"]
    )

    each_buy_ok = all(
        item["buy_percent"]
        >= MIN_BUY_PERCENT
        for item in confirmation
    )

    avg_buy_ok = (
        avg_buy_percent
        >= MIN_AVG_BUY_PERCENT
    )

    dominance_ok = (
        buy_dominance
        >= MIN_BUY_DOMINANCE_CANDLES
    )

    if not each_buy_ok:

        print(
            f"{state['symbol']} "
            f"{state['interval']} -> "
            f"CONFIRMATION FAILED: "
            f"BUY pressure too weak."
        )

        return False, None

    if not avg_buy_ok:

        print(
            f"{state['symbol']} "
            f"{state['interval']} -> "
            f"CONFIRMATION FAILED: "
            f"Average BUY pressure "
            f"{avg_buy_percent:.1f}%."
        )

        return False, None

    if not dominance_ok:

        print(
            f"{state['symbol']} "
            f"{state['interval']} -> "
            f"CONFIRMATION FAILED: "
            f"BUY dominance insufficient."
        )

        return False, None

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    confirmation_candles_data = []

    for item in confirmation:

        confirmation_candles_data.append(
            item["candle"]
        )

    # Cari confirmation şamından əvvəlki
    # 20 şamın həcmini hesablamaq.
    #
    # current_index daxilində əvvəlki 20 bağlanmış şam.
    # Son confirmation şamını orta hesablamaya daxil etmirik.
    # --------------------------------------------------------

    start = max(
        0,
        current_index - 20
    )

    previous_volume_candles = (
        candles[start:current_index]
    )

    average_volume = get_average_volume(
        previous_volume_candles,
        20
    )

    latest_candle = (
        confirmation[-1]["candle"]
    )

    if average_volume > 0:

        volume_ratio = (
            latest_candle["volume"]
            / average_volume
        )

    else:

        volume_ratio = 0.0

    if volume_ratio < MIN_VOLUME_VS_AVG20:

        print(
            f"{state['symbol']} "
            f"{state['interval']} -> "
            f"CONFIRMATION FAILED: "
            f"Volume too weak."
        )

        return False, None

    # --------------------------------------------------------
    # STRONG SELLING SPIKE
    # --------------------------------------------------------

    for item in confirmation:

        candle = item["candle"]

        if is_strong_selling_candle(
            candle,
            average_volume
        ):

            print(
                f"{state['symbol']} "
                f"{state['interval']} -> "
                f"CONFIRMATION FAILED: "
                f"Strong selling volume spike."
            )

            print(
                f"SELL: "
                f"{candle['sell_percent']:.1f}%"
            )

            print(
                f"Volume ratio: "
                f"{candle['volume'] / average_volume:.2f}x"
            )

            return False, None

    # --------------------------------------------------------
    # ATR RECOVERY
    # --------------------------------------------------------

    atr_value = state[
        "reclaim_atr"
    ]

    if (
        atr_value is None
        or atr_value <= 0
    ):

        print(
            f"{state['symbol']} "
            f"{state['interval']} -> "
            f"CONFIRMATION FAILED: "
            f"ATR unavailable."
        )

        return False, None

    maximum_close = max(
        item["candle"]["close"]
        for item in confirmation
    )

    recovery_atr = (
        maximum_close
        - second_low
    ) / atr_value

    if recovery_atr < MIN_PRICE_RECOVERY_ATR:

        print(
            f"{state['symbol']} "
            f"{state['interval']} -> "
            f"CONFIRMATION FAILED: "
            f"Price recovery only "
            f"{recovery_atr:.2f} ATR."
        )

        return False, None

    # --------------------------------------------------------
    # PRICE MOMENTUM
    # --------------------------------------------------------

    first_confirmation_close = (
        confirmation[0]["candle"]["close"]
    )

    last_confirmation_close = (
        confirmation[-1]["candle"]["close"]
    )

    momentum_percent = (
        (
            last_confirmation_close
            - first_confirmation_close
        )
        / first_confirmation_close
    ) * 100

    minimum_progress = (
        atr_value
        * MIN_LAST_CLOSE_PROGRESS_ATR
    )

    price_progress = (
        last_confirmation_close
        - first_confirmation_close
    )

    if price_progress < minimum_progress:

        print(
            f"{state['symbol']} "
            f"{state['interval']} -> "
            f"CONFIRMATION FAILED: "
            f"Momentum too weak."
        )

        return False, None

    # --------------------------------------------------------
    # LAST CANDLE QUALITY
    # --------------------------------------------------------

    latest_close_location = (
        candle_close_location(
            latest_candle
        )
    )

    if (
        latest_close_location
        < MIN_CLOSE_LOCATION
    ):

        print(
            f"{state['symbol']} "
            f"{state['interval']} -> "
            f"CONFIRMATION FAILED: "
            f"Latest candle closed too low "
            f"inside its range."
        )

        return False, None

    # --------------------------------------------------------
    # ALL PASSED
    # --------------------------------------------------------

    confirmation_data = {

        "avg_buy_percent":
            avg_buy_percent,

        "buy_dominance":
            buy_dominance,

        "volume_ratio":
            volume_ratio,

        "momentum_percent":
            momentum_percent
    }

    print(
        f"{state['symbol']} "
        f"{state['interval']} -> "
        f"ALL CONFIRMATION CONDITIONS PASSED."
    )

    return True, confirmation_data


# ============================================================
# PROCESS FIRST LOW
# ============================================================

def process_first_low(
    state,
    candle,
    rsi_value,
    atr_value,
    candles,
    current_index
):

    state["bars_since_first"] += 1

    # ========================================================
    # CONFIRMATION PHASE
    # ========================================================

    if state["confirmation_active"]:

        second_low = state[
            "second_low"
        ]

        # ----------------------------------------------------
        # 2-ci dibin altına düşmə
        # ----------------------------------------------------

        if candle["low"] < second_low:

            print(
                f"{state['symbol']} "
                f"{state['interval']} -> "
                f"CONFIRMATION CANCELLED."
            )

            print(
                f"Price broke SECOND LOW: "
                f"{second_low}"
            )

            # 2-ci dib yeni 1-ci dib olur.

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

            state["confirmation_candles"] = []

            state["reclaim_time"] = None

            state["reclaim_price"] = None

            state["reclaim_rsi"] = None

            state["reclaim_atr"] = None

            return

        # ----------------------------------------------------
        # RSI yenidən 30 və ya aşağı
        # ----------------------------------------------------

        if rsi_value <= RSI_LEVEL:

            print(
                f"{state['symbol']} "
                f"{state['interval']} -> "
                f"RSI returned to "
                f"{rsi_value:.2f}."
            )

            print(
                f"{state['symbol']} "
                f"{state['interval']} -> "
                f"CONFIRMATION CANCELLED."
            )

            # Burada 2-ci dib tamamilə silinmir.
            #
            # Çünki qiymət 2-ci dibdən aşağı düşməyib.
            #
            # Sadəcə bu RSI reclaim uğursuz oldu.
            #
            # Yenidən 30 üzərinə çıxmasını gözləyirik.

            state["confirmation_active"] = False

            state["confirmation_candles"] = []

            state["reclaim_time"] = None

            state["reclaim_price"] = None

            state["reclaim_rsi"] = None

            state["reclaim_atr"] = None

            return

        # ----------------------------------------------------
        # Confirmation candle əlavə et
        # ----------------------------------------------------

        state["confirmation_candles"].append({

            "candle": candle,

            "rsi": rsi_value,

            "buy_percent":
                candle["buy_percent"],

            "sell_percent":
                candle["sell_percent"]
        })

        confirmation_count = len(
            state["confirmation_candles"]
        )

        print(
            f"{state['symbol']} "
            f"{state['interval']} -> "
            f"CONFIRMATION CANDLE "
            f"{confirmation_count}/"
            f"{CONFIRMATION_CANDLES}"
        )

        print(
            f"RSI: {rsi_value:.2f}"
        )

        print(
            f"BUY: "
            f"{candle['buy_percent']:.1f}%"
        )

        print(
            f"SELL: "
            f"{candle['sell_percent']:.1f}%"
        )

        # ----------------------------------------------------
        # 3 şam tamamlandıqda analiz
        # ----------------------------------------------------

        if (
            confirmation_count
            >= CONFIRMATION_CANDLES
        ):

            passed, confirmation_data = (
                analyze_confirmation(
                    state,
                    candles,
                    rsi_values=None,
                    atr_values=None,
                    current_index=current_index
                )
            )

            if passed:

                send_rsi_signal(
                    state,
                    candle["close"],
                    rsi_value,
                    confirmation_data
                )

                reset_setup(
                    state
                )

                return

            # ------------------------------------------------
            # Confirmation uğursuz oldu.
            #
            # Setup tam silinmir.
            #
            # RSI 30 yenidən keçərsə yeni confirmation
            # başlaya bilər.
            # ------------------------------------------------

            state["confirmation_active"] = False

            state["confirmation_candles"] = []

            state["reclaim_time"] = None

            state["reclaim_price"] = None

            state["reclaim_rsi"] = None

            state["reclaim_atr"] = None

            return

        return

    # ========================================================
    # MAXIMUM 50 ŞAM
    # ========================================================

    if state[
        "bars_since_first"
    ] > LOOKBACK_CANDLES:

        print(
            f"{state['symbol']} "
            f"{state['interval']} -> "
            f"50 candles passed."
        )

        print(
            f"{state['symbol']} "
            f"{state['interval']} -> "
            f"FIRST LOW deleted."
        )

        reset_setup(
            state
        )

        return

    # ========================================================
    # SECOND LOW AXTARIŞI
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
            > state["first_rsi"]
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
                f"Now waiting for RSI > 30."
            )

            return

        # ----------------------------------------------------
        # LOWER LOW VAR,
        # RSI DIVERGENCE YOXDUR
        # ----------------------------------------------------

        print(
            f"{state['symbol']} "
            f"{state['interval']} -> "
            f"Lower low found but "
            f"RSI divergence failed."
        )

        print(
            f"Current candle becomes "
            f"NEW FIRST LOW."
        )

        make_new_first_low(
            state,
            candle,
            rsi_value
        )

        return

    # ========================================================
    # SECOND LOW TAPILIB
    # AMMA RSI HƏLƏ 30-DAN AŞAĞIDIR
    #
    # RSI 30-a qalxanda confirmation başlayacaq.
    # ========================================================

    if (
        state["candidate_active"]
        and rsi_value > RSI_LEVEL
    ):

        state["confirmation_active"] = True

        state["confirmation_candles"] = []

        state["reclaim_time"] = (
            candle["time"]
        )

        state["reclaim_price"] = (
            candle["close"]
        )

        state["reclaim_rsi"] = (
            rsi_value
        )

        state["reclaim_atr"] = (
            atr_value
        )

        # RSI 30-u keçən ilk şam confirmation
        # şamlarından biri kimi daxil edilir.

        state["confirmation_candles"].append({

            "candle": candle,

            "rsi": rsi_value,

            "buy_percent":
                candle["buy_percent"],

            "sell_percent":
                candle["sell_percent"]
        })

        print(
            f"{state['symbol']} "
            f"{state['interval']} -> "
            f"RSI ABOVE 30."
        )

        print(
            f"Price: "
            f"{candle['close']}"
        )

        print(
            f"RSI: "
            f"{rsi_value:.2f}"
        )

        print(
            f"BUY: "
            f"{candle['buy_percent']:.1f}%"
        )

        print(
            f"SELL: "
            f"{candle['sell_percent']:.1f}%"
        )

        print(
            f"Confirmation started: "
            f"1/{CONFIRMATION_CANDLES}"
        )

        return


# ============================================================
# PROCESS SYMBOL + TIMEFRAME
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
        + ATR_PERIOD
        + 25
    ):

        return

    rsi_values = calculate_rsi(
        candles,
        RSI_PERIOD
    )

    atr_values = calculate_atr(
        candles,
        ATR_PERIOD
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
    # NEW CLOSED CANDLES
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
            > last_time
            and rsi_values[i]
            is not None
            and atr_values[i]
            is not None
        ):

            new_indices.append(i)

    if not new_indices:

        return

    # ========================================================
    # PROCESS NEW CANDLES
    # ========================================================

    for i in new_indices:

        candle = candles[i]

        rsi_value = rsi_values[i]

        atr_value = atr_values[i]

        if rsi_value is None:

            continue

        if atr_value is None:

            continue

        # ----------------------------------------------------
        # NO FIRST LOW
        # ----------------------------------------------------

        if state["first_low"] is None:

            process_no_first_low(
                state,
                candle,
                rsi_value
            )

        # ----------------------------------------------------
        # FIRST LOW VAR
        # ----------------------------------------------------

        else:

            process_first_low(
                state,
                candle,
                rsi_value,
                atr_value,
                candles,
                i
            )

        # ----------------------------------------------------
        # LAST PROCESSED CANDLE
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # 24H VOLUME
        # ----------------------------------------------------

        volume_24h = (
            get_24h_quote_volume(
                symbol
            )
        )

        if volume_24h < (
            MIN_QUOTE_VOLUME_24H
        ):

            continue

        # ----------------------------------------------------
        # TIMEFRAMES
        # ----------------------------------------------------

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

    print("=" * 70)

    print(
        "BINANCE RSI BULLISH DIVERGENCE "
        "+ MOMENTUM CONFIRMATION BOT"
    )

    print("=" * 70)

    print(
        f"RSI Period: "
        f"{RSI_PERIOD}"
    )

    print(
        f"RSI level: "
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
        f"{CONFIRMATION_CANDLES}"
    )

    print(
        f"Minimum BUY pressure: "
        f"{MIN_BUY_PERCENT:.1f}%"
    )

    print(
        f"Minimum average BUY pressure: "
        f"{MIN_AVG_BUY_PERCENT:.1f}%"
    )

    print(
        f"Minimum ATR recovery: "
        f"{ATR_RECOVERY_MULTIPLIER:.2f} ATR"
    )

    print(
        f"Minimum volume / Avg20: "
        f"{MIN_VOLUME_VS_AVG20:.2f}x"
    )

    print(
        f"Minimum 24H volume: "
        f"{MIN_QUOTE_VOLUME_24H:,} USDT"
    )

    print("=" * 70)

    print(
        "STARTUP MODE:"
    )

    print(
        "Previous 50 closed candles = "
        "comparison baseline only."
    )

    print(
        "No historical first low selected."
    )

    print("=" * 70)

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
            SCAN_SECONDS - elapsed
        )

        time.sleep(
            sleep_time
        )


# ============================================================
# START BOT
# ============================================================

if __name__ == "__main__":

    main()
