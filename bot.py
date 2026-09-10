import os
import time
import requests
from datetime import datetime, timezone


# ============================================================
# BINANCE RSI BULLISH DIVERGENCE LIVE ALERT BOT
# ============================================================

BINANCE_BASE_URL = "https://api.binance.com"

MIN_QUOTE_VOLUME_24H = 20_000_000

SCAN_SECONDS = 60

RSI_PERIOD = 6

LOOKBACK_CANDLES = 50

RSI_TIMEFRAMES = [
    "15m",
    "30m",
    "1h"
]

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# ============================================================
# GLOBALS
# ============================================================

session = requests.Session()

states = {}


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram environment variables are missing.")
        return

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
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
                "close": float(k[4])
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

    # Binance-da son kline hazırda formalaşan şamdır.
    # Ona görə son şam çıxarılır.
    return candles[:-1]


# ============================================================
# RSI - WILDER RSI
# ============================================================

def calculate_rsi(
    candles,
    period=6
):

    if len(candles) <= period:
        return [None] * len(candles)

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
        # DİBSİZ VƏZİYYƏTDƏ 50-LİK MÜQAYİSƏ BAZASI
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

        "confirmation_count": 0,

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

    state["confirmation_count"] = 0

    # --------------------------------------------------------
    # ÇOX VACİB:
    #
    # Köhnə Binance şamlarına qayıtmaq yoxdur.
    #
    # Yeni müqayisə bazası gələcək yeni şamlardan qurulur.
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # BOT BAŞLAYANDA ALINAN 50 ŞAM
    #
    # YALNIZ MÜQAYİSƏ BAZASIDIR.
    #
    # Bu şamların heç birindən tarixi 1-ci dib seçilmir.
    # --------------------------------------------------------

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

    # Son bağlanmış şam artıq işlənmiş hesab olunur.
    state["last_candle_time"] = (
        candles[-1]["time"]
    )

    print(
        f"{state['symbol']} "
        f"{state['interval']} -> "
        f"STARTUP: previous 50 closed candles "
        f"loaded as comparison baseline."
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

    state["confirmation_count"] = 0


# ============================================================
# DİBSİZ VƏZİYYƏT
# ============================================================

def process_no_first_low(
    state,
    candle,
    rsi_value
):

    window = state[
        "comparison_window"
    ]

    # --------------------------------------------------------
    # Əgər yeni reset olunubsa və baza hələ 50 şam deyil:
    #
    # Yeni şamlar toplanır.
    # Köhnə Binance tarixçəsi istifadə edilmir.
    # --------------------------------------------------------

    if len(window) < LOOKBACK_CANDLES:

        window.append({
            "time": candle["time"],
            "low": candle["low"],
            "rsi": rsi_value
        })

        print(
            f"{state['symbol']} "
            f"{state['interval']} -> "
            f"Building new 50-candle comparison "
            f"window: {len(window)}/50"
        )

        return

    # --------------------------------------------------------
    # YENİ ŞAM ƏVVƏLKİ 50 ŞAMIN HAMISINDAN AŞAĞIDIR?
    # --------------------------------------------------------

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

        print(
            f"{state['symbol']} "
            f"{state['interval']} -> "
            f"FIRST LOW FOUND: "
            f"{candle['low']} | "
            f"RSI: {rsi_value:.2f}"
        )

        # ----------------------------------------------------
        # Artıq bu rolling baza lazım deyil.
        # First low-dan yeni setup başlayır.
        # ----------------------------------------------------

        state["comparison_window"] = []

        return

    # --------------------------------------------------------
    # DİB YOXDUR:
    #
    # Rolling 50 davam edir.
    # Ən köhnə şam çıxır,
    # yeni şam daxil olur.
    # --------------------------------------------------------

    window.pop(0)

    window.append({
        "time": candle["time"],
        "low": candle["low"],
        "rsi": rsi_value
    })


# ============================================================
# SEND RSI SIGNAL
# ============================================================

def send_rsi_signal(state):

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

    message = (
        "🟢 <b>RSI BULLISH DIVERGENCE</b>\n\n"

        f"<b>{state['symbol']}</b> "
        f"— <b>{state['interval']}</b>\n\n"

        f"1️⃣ <b>First Low:</b> "
        f"{state['first_low']:.8f}\n"

        f"RSI: "
        f"{state['first_rsi']:.2f}\n"

        f"Time: "
        f"{first_time}\n\n"

        f"2️⃣ <b>Second Low:</b> "
        f"{state['second_low']:.8f}\n"

        f"RSI: "
        f"{state['second_rsi']:.2f}\n"

        f"Time: "
        f"{second_time}\n\n"

        "📉 <b>Price:</b> Lower Low\n"
        "📈 <b>RSI:</b> Higher Low\n\n"

        "✅ 2 confirmation candles "
        "closed above the second low.\n\n"

        "🔄 <b>Setup completed.</b>\n"
        "Bot is now looking for a "
        "<b>NEW first low</b>."
    )

    send_telegram(message)


# ============================================================
# FIRST LOW VAR
# ============================================================

def process_first_low(
    state,
    candle,
    rsi_value
):

    # First low-dan sonra gələn yeni şam
    state["bars_since_first"] += 1

    # ========================================================
    # SECOND LOW CONFIRMATION
    # ========================================================

    if state["candidate_active"]:

        second_low = state[
            "second_low"
        ]

        # ----------------------------------------------------
        # TƏSDİQ ZAMANI DAHA AŞAĞI LOW GƏLDİ
        #
        # Köhnə first low silinir.
        # Yeni aşağı şam yeni first low olur.
        # ----------------------------------------------------

        if candle["low"] < second_low:

            print(
                f"{state['symbol']} "
                f"{state['interval']} -> "
                f"Lower low during confirmation. "
                f"New first low."
            )

            make_new_first_low(
                state,
                candle,
                rsi_value
            )

            return

        # ----------------------------------------------------
        # CLOSE SECOND LOW-DAN YUXARIDADIR?
        # ----------------------------------------------------

        if candle["close"] > second_low:

            state["confirmation_count"] += 1

            print(
                f"{state['symbol']} "
                f"{state['interval']} -> "
                f"Confirmation "
                f"{state['confirmation_count']}/2"
            )

            # ------------------------------------------------
            # 2 TƏSDİQ ŞAMI TAMAMLANDI
            # ------------------------------------------------

            if state[
                "confirmation_count"
            ] >= 2:

                send_rsi_signal(
                    state
                )

                # Bütün setup silinir.
                #
                # Köhnə tarixçəyə qayıdılmır.
                reset_setup(
                    state
                )

                return

        else:

            # ------------------------------------------------
            # CLOSE ikinci dibdən yuxarı bağlanmadı.
            #
            # Amma LOW ikinci dibdən aşağı da deyil.
            #
            # Candidate uğursuz olur.
            # İkinci dib yeni first low kimi qəbul edilir.
            # ------------------------------------------------

            print(
                f"{state['symbol']} "
                f"{state['interval']} -> "
                f"Second-low confirmation failed. "
                f"Second low becomes new first low."
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

            state["confirmation_count"] = 0

        return

    # ========================================================
    # FIRST LOW-DAN SONRA MAXIMUM 50 ŞAM
    # ========================================================

    if state[
        "bars_since_first"
    ] > LOOKBACK_CANDLES:

        print(
            f"{state['symbol']} "
            f"{state['interval']} -> "
            f"50 candles passed. "
            f"First low deleted."
        )

        reset_setup(
            state
        )

        return

    # ========================================================
    # SECOND LOW AXTARIŞI
    # ========================================================

    if candle["low"] < state["first_low"]:

        # ----------------------------------------------------
        # RSI HIGHER LOW
        # ----------------------------------------------------

        if rsi_value > state["first_rsi"]:

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

            state["confirmation_count"] = 0

            print(
                f"{state['symbol']} "
                f"{state['interval']} -> "
                f"POTENTIAL SECOND LOW: "
                f"{candle['low']} | "
                f"RSI: {rsi_value:.2f}"
            )

            return

        # ----------------------------------------------------
        # PRICE LOWER LOW VAR
        # RSI HIGHER LOW YOXDUR
        #
        # Köhnə first low silinir.
        # Cari şam yeni first low olur.
        # ----------------------------------------------------

        else:

            print(
                f"{state['symbol']} "
                f"{state['interval']} -> "
                f"Lower low found, but RSI "
                f"condition failed. "
                f"Current candle becomes new first low."
            )

            make_new_first_low(
                state,
                candle,
                rsi_value
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
        + 5
    ):
        return

    rsi_values = calculate_rsi(
        candles,
        RSI_PERIOD
    )

    # ========================================================
    # FIRST STARTUP
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
            > last_time
            and rsi_values[i]
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

        if rsi_value is None:
            continue

        # ----------------------------------------------------
        # DİBSİZ
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
                rsi_value
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

    print("=" * 60)

    print(
        "BINANCE RSI BULLISH DIVERGENCE BOT"
    )

    print("=" * 60)

    print(
        f"RSI Period: {RSI_PERIOD}"
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
        f"Minimum 24H volume: "
        f"{MIN_QUOTE_VOLUME_24H:,} USDT"
    )

    print("=" * 60)

    print(
        "STARTUP MODE:"
    )

    print(
        "Previous 50 closed candles = "
        "comparison baseline only."
    )

    print(
        "No historical first low is selected."
    )

    print(
        "After reset, old historical candles "
        "are not reused."
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
