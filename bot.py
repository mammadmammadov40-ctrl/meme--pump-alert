import os
import time
import requests
from datetime import datetime, timezone


# ============================================================
# BINANCE RSI BULLISH DIVERGENCE LIVE ALERT BOT
# ============================================================
#
# TIMEFRAMES:
# 15m + 30m + 1h
#
# RSI:
# RSI PERIOD = 6
#
# ============================================================
# ƏSAS MƏNTİQ
# ============================================================
#
# 1) BOT BAŞLAYANDA:
#
# Binance-dan əvvəlki 50 BAĞLANMIŞ şam alınır.
#
# Bu 50 şam yalnız MÜQAYİSƏ BAZASIDIR.
#
# !!! Bu 50 şamın içindən 1-ci dib seçilmir !!!
#
# Bot yeni bağlanmış şam gözləyir.
#
# Yeni şamın LOW qiyməti əvvəlki 50 şamın
# hamısından aşağıdırsa:
#
#       -> 1-ci DİB yaranır.
#
#
# 2) DİBSİZ VƏZİYYƏT:
#
# 1-ci dib yoxdursa, yeni gələn hər şam əvvəlki
# 50 şamla müqayisə edilir.
#
# 50-lik pəncərə davamlı sürüşür:
#
# Şam 51 gəlir -> Şam 1 çıxır
# Şam 52 gəlir -> Şam 2 çıxır
# və s.
#
# Bu proses yeni 1-ci dib tapılana qədər davam edir.
#
#
# 3) ÇOX VACİB:
#
# 1-ci dib tapıldıqdan sonra əvvəlki 50 şam artıq
# yeni setup üçün nəzərə alınmır.
#
# 1-ci dibdən sonra maksimum 50 YENİ BAĞLANMIŞ ŞAM
# ərzində 2-ci dib axtarılır.
#
#
# 4) 2-Cİ DİB:
#
# Qiymət:
#
#       2-ci LOW < 1-ci LOW
#
# RSI:
#
#       2-ci RSI > 1-ci RSI
#
# olmalıdır.
#
# Bu halda 2-ci dib POTENSİAL DİB kimi saxlanılır.
#
# Dərhal Telegram göndərilmir.
#
#
# 5) TƏSDİQ:
#
# 2-ci dibdən sonra gələn NÖVBƏTİ 2 bağlanmış şam:
#
#       CLOSE > 2-ci dibin LOW
#
# olmalıdır.
#
# Həmçinin bu 2 şam ərzində heç birinin LOW qiyməti
# 2-ci dibdən aşağı düşməməlidir.
#
# 2 şam uğurla təsdiqləsə:
#
#       -> RSI BULLISH DIVERGENCE
#       -> Telegram mesajı
#       -> bütün setup silinir
#       -> yenidən DİBSİZ vəziyyət
#
#
# 6) ƏGƏR 2-Cİ DİBİN RSI ŞƏRTİ ÖDƏNMƏSƏ:
#
# Qiymət 1-ci dibdən aşağı düşür, amma:
#
#       current RSI <= first RSI
#
# olarsa:
#
#       -> köhnə 1-ci dib silinir
#       -> həmin şam yeni 1-ci dib olur
#       -> yeni 50 şamlıq müddət başlayır
#
#
# 7) ƏGƏR TƏSDİQ ZAMANI DAHA AŞAĞI DİB GƏLSƏ:
#
#       current LOW < second LOW
#
# olarsa:
#
#       -> köhnə 1-ci dib silinir
#       -> həmin aşağı şam yeni 1-ci dib olur
#       -> yeni 50 şamlıq müddət başlayır
#
#
# 8) 1-Cİ DİBDƏN SONRA 50 ŞAM ƏRZİNDƏ
#    2-Cİ DİB TAPILMAZSA:
#
#       -> 1-ci dib silinir
#       -> DİBSİZ vəziyyətə qayıdır
#
# !!! Bundan sonra köhnə 50 şama geri qayıdılmır !!!
#
# Yalnız YENİ bağlanmış şamlar izlənilir.
#
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
# STATE
# ============================================================

states = {}


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()


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
            print("Telegram error:", response.text)

    except Exception as e:
        print("Telegram exception:", e)


# ============================================================
# BINANCE SPOT USDT SYMBOLS
# ============================================================

def get_spot_usdt_symbols():

    url = f"{BINANCE_BASE_URL}/api/v3/exchangeInfo"

    try:
        response = session.get(
            url,
            timeout=15
        )

        data = response.json()

        symbols = []

        for item in data.get("symbols", []):

            if item.get("status") != "TRADING":
                continue

            if item.get("quoteAsset") != "USDT":
                continue

            if item.get("isSpotTradingAllowed") is not True:
                continue

            symbols.append(item["symbol"])

        return symbols

    except Exception as e:

        print("Exchange info error:", e)

        return []


# ============================================================
# 24H VOLUME
# ============================================================

def get_24h_quote_volume(symbol):

    url = f"{BINANCE_BASE_URL}/api/v3/ticker/24hr"

    try:

        response = session.get(
            url,
            params={"symbol": symbol},
            timeout=10
        )

        data = response.json()

        return float(data.get("quoteVolume", 0))

    except Exception:

        return 0.0


# ============================================================
# KLINES
# ============================================================

def get_klines(symbol, interval, limit=200):

    url = f"{BINANCE_BASE_URL}/api/v3/klines"

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

        data = response.json()

        if not isinstance(data, list):
            return []

        candles = []

        for k in data:

            candles.append({
                "time": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
            })

        return candles

    except Exception as e:

        print(
            f"Kline error {symbol} {interval}:",
            e
        )

        return []


# ============================================================
# ONLY CLOSED CANDLES
# ============================================================

def get_closed_candles(symbol, interval, limit=200):

    candles = get_klines(
        symbol,
        interval,
        limit
    )

    if len(candles) < 2:
        return []

    now_ms = int(
        datetime.now(timezone.utc).timestamp() * 1000
    )

    closed = []

    for candle in candles:

        # Binance candle open time
        # A candle is considered closed if its next candle
        # has already started.
        #
        # Since we fetch enough candles, the final candle
        # is normally the currently forming candle.

        if candle["time"] < now_ms:
            closed.append(candle)

    # Safer approach:
    # remove the last candle because it may still be forming
    if len(closed) > 1:
        closed = closed[:-1]

    return closed


# ============================================================
# RSI - WILDER
# ============================================================

def calculate_rsi(candles, period=6):

    if len(candles) <= period:
        return [None] * len(candles)

    closes = [
        candle["close"]
        for candle in candles
    ]

    rsi = [None] * len(closes)

    gains = []
    losses = []

    for i in range(1, period + 1):

        change = closes[i] - closes[i - 1]

        if change > 0:
            gains.append(change)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(change))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        rsi[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi[period] = 100 - (
            100 / (1 + rs)
        )

    for i in range(period + 1, len(closes)):

        change = closes[i] - closes[i - 1]

        gain = max(change, 0)
        loss = max(-change, 0)

        avg_gain = (
            (avg_gain * (period - 1)) + gain
        ) / period

        avg_loss = (
            (avg_loss * (period - 1)) + loss
        ) / period

        if avg_loss == 0:
            rsi[i] = 100.0
        else:
            rs = avg_gain / avg_loss

            rsi[i] = 100 - (
                100 / (1 + rs)
            )

    return rsi


# ============================================================
# STATE CREATION
# ============================================================

def new_state():

    return {
        # ----------------------------------------------------
        # DİBSİZ VƏZİYYƏTDƏKİ 50-LİK ROLLING BAZA
        # ----------------------------------------------------
        #
        # Bu yalnız botun başlanğıcındakı 50 şamdan
        # başlayır.
        #
        # Sonradan setup reset olunanda bu baza yenidən
        # tarixdən götürülmür.
        #
        "initial_window": [],

        # ----------------------------------------------------
        # FIRST LOW
        # ----------------------------------------------------

        "first_low": None,
        "first_rsi": None,
        "first_time": None,

        # First low-dan sonra neçə YENİ şam keçib
        "bars_since_first": 0,

        # ----------------------------------------------------
        # SECOND LOW CANDIDATE
        # ----------------------------------------------------

        "candidate_active": False,

        "second_low": None,
        "second_rsi": None,
        "second_time": None,

        "confirmation_count": 0,

        # ----------------------------------------------------
        # LAST PROCESSED CLOSED CANDLE
        # ----------------------------------------------------

        "last_candle_time": None,

        # Botun başlanğıcda ilkin 50 şamı qurduğunu göstərir
        "startup_initialized": False,
    }


# ============================================================
# RESET FIRST + SECOND LOW
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


# ============================================================
# STARTUP INITIALIZATION
# ============================================================

def initialize_startup_state(
    state,
    candles,
    rsi_values
):

    if len(candles) < LOOKBACK_CANDLES:
        return False

    # --------------------------------------------------------
    # ÇOX VACİB:
    #
    # Buradakı 50 şam yalnız MÜQAYİSƏ BAZASIDIR.
    #
    # Onlardan heç biri 1-ci dib seçilmir.
    # --------------------------------------------------------

    state["initial_window"] = [
        {
            "time": candles[i]["time"],
            "low": candles[i]["low"],
            "rsi": rsi_values[i]
        }
        for i in range(
            len(candles) - LOOKBACK_CANDLES,
            len(candles)
        )
    ]

    state["startup_initialized"] = True

    # Son mövcud bağlanmış şamı yadda saxla.
    # Bu şam yenidən işlənməyəcək.
    state["last_candle_time"] = candles[-1]["time"]

    print(
        "STARTUP: Initial 50 closed candles loaded "
        "as comparison baseline. "
        "No historical first low selected."
    )

    return True


# ============================================================
# FIRST LOW DETECTION — DİBSİZ
# ============================================================

def process_no_first_low(
    state,
    candle,
    rsi_value
):

    window = state["initial_window"]

    if len(window) < LOOKBACK_CANDLES:
        return

    current_low = candle["low"]

    previous_50_lowest = min(
        item["low"]
        for item in window
    )

    # --------------------------------------------------------
    # YENİ ŞAM ƏVVƏLKİ 50 ŞAMIN HAMISINDAN AŞAĞIDIRSA
    # --------------------------------------------------------

    if current_low < previous_50_lowest:

        state["first_low"] = current_low
        state["first_rsi"] = rsi_value
        state["first_time"] = candle["time"]

        state["bars_since_first"] = 0

        state["candidate_active"] = False

        state["second_low"] = None
        state["second_rsi"] = None
        state["second_time"] = None

        state["confirmation_count"] = 0

        print(
            f"FIRST LOW FOUND | "
            f"LOW={current_low} | "
            f"RSI={rsi_value:.2f}"
        )

        # Artıq ilkin 50-lik baza lazım deyil.
        # Yeni setup yalnız first low-dan başlayır.
        state["initial_window"] = []

        return

    # --------------------------------------------------------
    # 1-ci dib tapılmayıb.
    #
    # 50-lik rolling pəncərəni sürüşdür.
    # --------------------------------------------------------

    window.pop(0)

    window.append({
        "time": candle["time"],
        "low": candle["low"],
        "rsi": rsi_value
    })


# ============================================================
# FIRST LOW EXISTS
# ============================================================

def process_first_low(
    state,
    candle,
    rsi_value
):

    first_low = state["first_low"]
    first_rsi = state["first_rsi"]

    current_low = candle["low"]

    # --------------------------------------------------------
    # FIRST LOW-DAN SONRA YENİ ŞAM
    # --------------------------------------------------------

    state["bars_since_first"] += 1

    # --------------------------------------------------------
    # ƏVVƏL AKTİV 2-Cİ DİBİN TƏSDİQİNİ YOXLAYIRIQ
    # --------------------------------------------------------

    if state["candidate_active"]:

        second_low = state["second_low"]

        # ----------------------------------------------------
        # TƏSDİQ ZAMANI DAHA AŞAĞI LOW GƏLDİ
        #
        # Köhnə first low silinir.
        # Bu yeni aşağı low yeni first low olur.
        # ----------------------------------------------------

        if current_low < second_low:

            print(
                "SECOND LOW INVALIDATED BY LOWER LOW -> "
                "NEW FIRST LOW"
            )

            state["first_low"] = current_low
            state["first_rsi"] = rsi_value
            state["first_time"] = candle["time"]

            state["bars_since_first"] = 0

            state["candidate_active"] = False

            state["second_low"] = None
            state["second_rsi"] = None
            state["second_time"] = None

            state["confirmation_count"] = 0

            return

        # ----------------------------------------------------
        # 2-Cİ DİBİN CLOSE TƏSDİQİ
        # ----------------------------------------------------

        if candle["close"] > second_low:

            state["confirmation_count"] += 1

            print(
                f"SECOND LOW CONFIRMATION "
                f"{state['confirmation_count']}/2"
            )

            # ------------------------------------------------
            # 2 ŞAM UĞURLU TƏSDİQ
            # ------------------------------------------------

            if state["confirmation_count"] >= 2:

                send_rsi_signal(
                    candle=candle,
                    state=state
                )

                # Signal-dan sonra hər şey silinir.
                reset_setup(state)

                # ÇOX VACİB:
                #
                # Köhnə 50 şama qayıtmaq yoxdur.
                #
                # Növbəti yeni şamlarla yenidən dipsiz
                # vəziyyətdə davam ediləcək.
                #
                # Yeni first low yalnız gələcəkdə yeni şamın
                # əvvəlki 50 YENİ şamdan aşağı olması ilə
                # yaranacaq.

                return

        else:

            # ------------------------------------------------
            # CLOSE 2-ci dibin üstündə bağlanmadı.
            #
            # Aşağı LOW da gəlməyibsə, candidate uğursuzdur.
            #
            # 2-ci dib yeni first low kimi saxlanılır.
            # ------------------------------------------------

            print(
                "SECOND LOW CONFIRMATION FAILED"
            )

            state["first_low"] = state["second_low"]
            state["first_rsi"] = state["second_rsi"]
            state["first_time"] = state["second_time"]

            state["bars_since_first"] = 0

            state["candidate_active"] = False

            state["second_low"] = None
            state["second_rsi"] = None
            state["second_time"] = None

            state["confirmation_count"] = 0

            return

        return

    # --------------------------------------------------------
    # MAXIMUM 50 YENİ ŞAM
    # --------------------------------------------------------

    if state["bars_since_first"] > LOOKBACK_CANDLES:

        print(
            "50 CANDLES PASSED -> "
            "FIRST LOW DELETED"
        )

        reset_setup(state)

        # Burada tarixi şamlara qayıtmırıq.
        # Yalnız gələcək yeni şamlar izlənəcək.

        return

    # --------------------------------------------------------
    # 2-Cİ DİBİN QİYMƏT ŞƏRTİ
    # --------------------------------------------------------

    if current_low < first_low:

        # ----------------------------------------------------
        # RSI HIGHER LOW
        # ----------------------------------------------------

        if rsi_value > first_rsi:

            # POTENSİAL 2-Cİ DİB
            state["candidate_active"] = True

            state["second_low"] = current_low
            state["second_rsi"] = rsi_value
            state["second_time"] = candle["time"]

            state["confirmation_count"] = 0

            print(
                f"POTENTIAL SECOND LOW | "
                f"LOW={current_low} | "
                f"RSI={rsi_value:.2f} | "
                f"FIRST RSI={first_rsi:.2f}"
            )

            return

        # ----------------------------------------------------
        # PRICE LOWER LOW VAR
        # AMMA RSI HIGHER LOW YOXDUR
        #
        # KÖHNƏ FIRST LOW SİLİNİR.
        # CURRENT CANDLE YENİ FIRST LOW OLUR.
        # ----------------------------------------------------

        else:

            print(
                "SECOND LOW RSI CONDITION FAILED -> "
                "CURRENT CANDLE BECOMES NEW FIRST LOW"
            )

            state["first_low"] = current_low
            state["first_rsi"] = rsi_value
            state["first_time"] = candle["time"]

            state["bars_since_first"] = 0

            return


# ============================================================
# RSI SIGNAL
# ============================================================

def send_rsi_signal(candle, state):

    first_low = state["first_low"]
    first_rsi = state["first_rsi"]

    second_low = state["second_low"]
    second_rsi = state["second_rsi"]

    first_time = state["first_time"]
    second_time = state["second_time"]

    first_time_str = datetime.fromtimestamp(
        first_time / 1000,
        tz=timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )

    second_time_str = datetime.fromtimestamp(
        second_time / 1000,
        tz=timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )

    # symbol və interval aşağıda xaricdən əlavə olunur.
    # Bu funksiya scan zamanı dəyişdiriləcək.
    symbol = state.get("symbol", "UNKNOWN")
    interval = state.get("interval", "UNKNOWN")

    message = (
        "🟢 <b>RSI BULLISH DIVERGENCE</b>\n\n"
        f"<b>{symbol}</b> — <b>{interval}</b>\n\n"
        f"1️⃣ <b>First Low:</b> {first_low:.8f}\n"
        f"RSI: {first_rsi:.2f}\n"
        f"Time: {first_time_str}\n\n"
        f"2️⃣ <b>Second Low:</b> {second_low:.8f}\n"
        f"RSI: {second_rsi:.2f}\n"
        f"Time: {second_time_str}\n\n"
        "📈 <b>Price:</b> Lower Low\n"
        "📈 <b>RSI:</b> Higher Low\n\n"
        "✅ 2 confirmation candles closed above "
        "the second low.\n\n"
        "🔄 Setup completed. "
        "Bot is now looking for a NEW first low."
    )

    send_telegram(message)


# ============================================================
# PROCESS NEW CLOSED CANDLES
# ============================================================

def process_symbol_interval(
    symbol,
    interval
):

    key = (symbol, interval)

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
        LOOKBACK_CANDLES + RSI_PERIOD + 5
    ):
        return

    rsi_values = calculate_rsi(
        candles,
        RSI_PERIOD
    )

    # ========================================================
    # BOT STARTUP
    # ========================================================

    if not state["startup_initialized"]:

        initialize_startup_state(
            state,
            candles,
            rsi_values
        )

        return

    # ========================================================
    # ONLY NEW CLOSED CANDLES
    # ========================================================

    new_indices = []

    last_time = state["last_candle_time"]

    for i, candle in enumerate(candles):

        if last_time is None:
            continue

        if candle["time"] > last_time:

            if rsi_values[i] is not None:
                new_indices.append(i)

    if not new_indices:
        return

    # ========================================================
    # PROCESS EACH NEW CLOSED CANDLE
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
        # SON İŞLƏNƏN ŞAM
        # ----------------------------------------------------

        state["last_candle_time"] = candle["time"]


# ============================================================
# MAIN SCAN
# ============================================================

def scan():

    symbols = get_spot_usdt_symbols()

    if not symbols:

        print("No symbols found.")
        return

    print(
        f"Scanning {len(symbols)} USDT Spot symbols..."
    )

    for symbol in symbols:

        # ----------------------------------------------------
        # 24H VOLUME FILTER
        # ----------------------------------------------------

        volume_24h = get_24h_quote_volume(
            symbol
        )

        if volume_24h < MIN_QUOTE_VOLUME_24H:
            continue

        # ----------------------------------------------------
        # RSI TIMEFRAMES
        # ----------------------------------------------------

        for interval in RSI_TIMEFRAMES:

            try:

                process_symbol_interval(
                    symbol,
                    interval
                )

            except Exception as e:

                print(
                    f"Processing error "
                    f"{symbol} {interval}:",
                    e
                )


# ============================================================
# MAIN LOOP
# ============================================================

def main():

    print("=" * 60)
    print("BINANCE RSI BULLISH DIVERGENCE BOT")
    print("=" * 60)

    print(
        f"RSI Period: {RSI_PERIOD}"
    )

    print(
        f"Initial comparison candles: "
        f"{LOOKBACK_CANDLES}"
    )

    print(
        "IMPORTANT:"
    )

    print(
        "At startup, previous 50 closed candles "
        "are ONLY the comparison baseline."
    )

    print(
        "No historical first low is selected."
    )

    print(
        "After reset, old historical candles "
        "are NOT reused."
    )

    print("=" * 60)

    while True:

        start = time.time()

        try:

            scan()

        except Exception as e:

            print(
                "Main scan error:",
                e
            )

        elapsed = time.time() - start

        sleep_time = max(
            1,
            SCAN_SECONDS - elapsed
        )

        time.sleep(sleep_time)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()

Bu versiyada əsas fərq dəqiq olaraq budur: başlanğıcdakı 50 şam bir dəfəlik ilkin müqayisə bazasıdır. Sonrakı resetlərdə bot həmin köhnə 50 şama qayıtmır; yalnız bundan sonra gələn yeni şamlarla işləyir.

Bir də qeyd edim: kodda "2-ci dib" tapıldıqdan sonra 2 yeni bağlanmış şamın close-u 2-ci dibin LOW-undan yuxarı olmalıdır və həmin müddətdə daha aşağı LOW gəlməməlidir.
