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
# 1) BOT STARTUP:
#    Binance-dan əvvəlki 50 bağlanmış şam götürülür.
#    Bu 50 şam yalnız müqayisə bazasıdır.
#    Tarixi 1-ci dib seçilmir.
#
# 2) DİBSİZ VƏZİYYƏT:
#    Yeni şam əvvəlki 50 şamın hamısından aşağı low edərsə
#    həmin şam yeni 1-ci dib olur.
#
# 3) FIRST LOW:
#    1-ci dibdən sonra maksimum 50 bağlanmış şam ərzində
#    2-ci dib axtarılır.
#
# 4) SECOND LOW:
#    - 2-ci dib qiyməti 1-ci dibdən aşağı olmalıdır.
#    - 1-ci RSI < 30
#    - 2-ci RSI < 30
#    - 2-ci RSI > 1-ci RSI
#
# 5) SECOND LOW ŞƏRTLƏRİ ÖDƏNƏNDƏ:
#    Daha 2 şam gözlənilmir.
#    Sonrakı bağlanan şamların RSI-si izlənilir.
#
# 6) RSI > 30:
#    Son bağlanan şam RSI 30-dan yuxarı bağlanarsa
#    TARGET SIGNAL göndərilir.
#
# 7) RSI 30 OLMADAN QİYMƏT 2-Cİ DİBDƏN AŞAĞI DÜŞƏRSƏ:
#    - 2-ci dib ləğv edilir.
#    - 1-ci dib silinir.
#    - Köhnə 2-ci dib yeni 1-ci dib olur.
#    - Həmin yeni 1-ci dibdən yenidən maksimum 50 şam
#      izlənilir.
#
# 8) SIGNAL GƏLDİKDƏ:
#    Setup tam silinir.
#    Bot yeni 1-ci dib axtarışına keçir.
#
# ============================================================


BINANCE_BASE_URL = "https://api.binance.com"

MIN_QUOTE_VOLUME_24H = 20_000_000

SCAN_SECONDS = 60

RSI_PERIOD = 6

LOOKBACK_CANDLES = 50

RSI_OVERBOUGHT_LEVEL = 30

RSI_TIMEFRAMES = [
    "15m",
    "30m",
    "1h"
]

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
    # COIN ÜZƏRİNƏ BASANDA BİNANCE AÇILSIN
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

    # Son Binance kline hazırda formalaşan şamdır.
    # Son şam çıxarılır.
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

    # --------------------------------------------------------
    # Köhnə tarixçəyə qayıtmaq yoxdur.
    # Yeni rolling 50 şam bazası gələcək şamlardan qurulur.
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
    # BOT BAŞLAYANDA ALINAN ƏVVƏLKİ 50 BAĞLANMIŞ ŞAM
    #
    # YALNIZ MÜQAYİSƏ BAZASIDIR.
    #
    # Bu 50 şamdan 1-ci dib seçilmir.
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

    print(
        f"{state['symbol']} "
        f"{state['interval']} -> "
        f"NEW FIRST LOW: "
        f"{candle['low']} | "
        f"RSI: {rsi_value:.2f}"
    )


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
    # 50 şam tamamlanana qədər baza qurulur.
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
    # Yeni şam əvvəlki 50 şamın hamısından aşağıdır?
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

        # Yeni first low-dan setup başlayır.
        state["comparison_window"] = []

        return

    # --------------------------------------------------------
    # Rolling 50:
    # ən köhnə çıxır, yeni şam daxil olur.
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

    # --------------------------------------------------------
    # 1-ci dib -> 2-ci dib qiymət dəyişimi
    # --------------------------------------------------------

    price_first_to_second = (
        (
            second_low
            - first_low
        )
        / first_low
    ) * 100

    # --------------------------------------------------------
    # 2-ci dib -> RSI 30 üzərinə çıxan qiymət
    #
    # İSTƏDİYİN:
    # RSI-nin faizini yox,
    # QİYMƏTİN neçə faiz qalxdığını göstərmək.
    # --------------------------------------------------------

    price_second_to_signal = (
        (
            signal_price
            - second_low
        )
        / second_low
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

        "🚀 <b>RSI 30 ABOVE</b>\n"

        f"Signal Price: "
        f"{signal_price:.8f}\n"

        f"RSI: "
        f"{signal_rsi:.2f}\n"

        f"Price movement from 2nd low: "
        f"+{price_second_to_signal:.2f}%\n\n"

        "✅ <b>RSI closed above 30.</b>\n\n"

        "🔄 <b>Setup completed.</b>\n"

        "Bot is now looking for a "
        "<b>NEW FIRST LOW</b>."
    )

    send_telegram(
        message,
        state["symbol"]
    )


# ============================================================
# FIRST LOW VAR
# ============================================================

def process_first_low(
    state,
    candle,
    rsi_value
):

    # First low-dan sonra gələn hər yeni şam
    state["bars_since_first"] += 1

    # ========================================================
    # SECOND LOW ARTİQ TAPILIB
    # ========================================================

    if state["candidate_active"]:

        second_low = state[
            "second_low"
        ]

        # ----------------------------------------------------
        # ÇOX VACİB:
        #
        # RSI 30 olmamış qiymət 2-ci dibdən aşağı düşərsə:
        #
        # 2-ci dib -> yeni 1-ci dib
        #
        # Köhnə 1-ci dib tam silinir.
        # ----------------------------------------------------

        if candle["low"] < second_low:

            print(
                f"{state['symbol']} "
                f"{state['interval']} -> "
                f"Price broke SECOND LOW before "
                f"RSI crossed 30."
            )

            print(
                f"{state['symbol']} "
                f"{state['interval']} -> "
                f"OLD FIRST LOW DELETED."
            )

            print(
                f"{state['symbol']} "
                f"{state['interval']} -> "
                f"SECOND LOW becomes NEW FIRST LOW."
            )

            # ------------------------------------------------
            # 2-ci dib yeni 1-ci dib olur.
            # ------------------------------------------------

            state["first_low"] = (
                state["second_low"]
            )

            state["first_rsi"] = (
                state["second_rsi"]
            )

            state["first_time"] = (
                state["second_time"]
            )

            # ------------------------------------------------
            # Yeni first low-dan yeni 50 şamlıq dövr.
            # ------------------------------------------------

            state["bars_since_first"] = 0

            state["candidate_active"] = False

            state["second_low"] = None

            state["second_rsi"] = None

            state["second_time"] = None

            return

        # ----------------------------------------------------
        # ƏSAS YENİ ŞƏRT:
        #
        # Son bağlanmış şam RSI 30-dan yuxarı bağlanıb?
        # ----------------------------------------------------

        if rsi_value > RSI_OVERBOUGHT_LEVEL:

            print(
                f"{state['symbol']} "
                f"{state['interval']} -> "
                f"RSI crossed above 30."
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

            # ------------------------------------------------
            # Signal-dan sonra bütün setup silinir.
            # ------------------------------------------------

            reset_setup(
                state
            )

            return

        # ----------------------------------------------------
        # RSI hələ 30-dan aşağıdır.
        #
        # Nə siqnal var,
        # nə də reset.
        #
        # Növbəti bağlanan şam gözlənilir.
        # ----------------------------------------------------

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
            f"50 candles passed without "
            f"valid second low."
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

        # ----------------------------------------------------
        # ƏVVƏL RSI ŞƏRTLƏRİ
        #
        # HƏM 1-ci, HƏM 2-ci RSI 30-dan aşağı.
        # 2-ci RSI 1-ci RSI-dan yüksək.
        # ----------------------------------------------------

        valid_rsi_divergence = (

            state["first_rsi"]
            < RSI_OVERBOUGHT_LEVEL

            and

            rsi_value
            < RSI_OVERBOUGHT_LEVEL

            and

            rsi_value
            > state["first_rsi"]
        )

        if valid_rsi_divergence:

            # ------------------------------------------------
            # VALID SECOND LOW
            # ------------------------------------------------

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
                f"Now waiting for RSI > 30."
            )

            return

        # ----------------------------------------------------
        # PRICE LOWER LOW VAR,
        # AMMA RSI ŞƏRTİ ÖDƏNMƏYİB.
        #
        # Cari şam yeni 1-ci dib olur.
        # ----------------------------------------------------

        print(
            f"{state['symbol']} "
            f"{state['interval']} -> "
            f"Lower low found, but RSI divergence "
            f"condition failed."
        )

        print(
            f"{state['symbol']} "
            f"{state['interval']} -> "
            f"Current candle becomes NEW FIRST LOW."
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
        f"RSI Period: "
        f"{RSI_PERIOD}"
    )

    print(
        f"RSI level: "
        f"< {RSI_OVERBOUGHT_LEVEL}"
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
