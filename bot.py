import os
import time
import requests
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# BINANCE 5M HIGH / DIB BREAKOUT ALERT BOT
# ============================================================
#
# YALNIZ ALERT
# AVTOMATIK ORDER YOXDUR
#
# STRATEGIYA:
#
# 1. Binance Spot USDT cütləri
# 2. 24h quote volume-a görə TOP 100
# 3. 5 dəqiqəlik timeframe
# 4. Son 100 BAĞLANMIŞ şam
#
# HIGH QAYDASI:
# - High-ın solunda ən azı 10 şam olmalıdır.
# - High > əvvəlki 10 şamın bütün High qiymətləri
# - Sağda 10 şam gözlənilmir.
#
# DIB QAYDASI:
# - Aktiv DIB-dən aşağı yeni qiymət gəlsə,
#   yeni DIB yaranır.
# - Aradakı kiçik təpələr yeni High sayılmır,
#   əgər yeni DIB yaranmayıbsa.
#
# HIGH QIRILMASI:
# - Qiymət target High-ı keçməlidir.
# - 5M şam target High-ın ÜSTÜNDƏ BAĞLANMALIDIR.
# - Bundan sonra Telegram siqnalı göndərilir.
#
# ============================================================


# =========================
# ENV
# =========================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# =========================
# SETTINGS
# =========================

BINANCE_URL = "https://api.binance.com"

INTERVAL = "5m"

TOP_COINS = 100

CANDLE_LIMIT = 100

LEFT_HIGH_CANDLES = 10

SCAN_SECONDS = 15

REQUEST_TIMEOUT = 10

MAX_WORKERS = 10

# Eyni breakout-un təkrar göndərilməsinin qarşısını alır
SIGNAL_COOLDOWN_SECONDS = 24 * 60 * 60


# =========================
# SESSION
# =========================

session = requests.Session()

session.headers.update({
    "User-Agent": "Mozilla/5.0"
})


# =========================
# MEMORY
# =========================

last_signal = {}

# Hər symbol üçün son işlənmiş candle open time
last_processed_candle = {}

# İlk scan zamanı köhnə breakout siqnallarını
# göndərməmək üçün istifadə olunur
initialized_symbols = set()


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram ENV dəyişənləri yoxdur.")
        print(message)
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }

    try:
        r = session.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        if r.ok:
            return True

        print(
            "Telegram error:",
            r.status_code,
            r.text[:300]
        )

    except Exception as e:
        print("Telegram exception:", e)

    return False


# ============================================================
# BINANCE REQUEST
# ============================================================

def binance_get(path, params=None):

    url = BINANCE_URL + path

    r = session.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT
    )

    r.raise_for_status()

    return r.json()


# ============================================================
# TOP 100 USDT COINS
# ============================================================

def get_top_100_symbols():

    try:

        tickers = binance_get(
            "/api/v3/ticker/24hr"
        )

        symbols = []

        for x in tickers:

            symbol = x.get("symbol", "")

            # Yalnız USDT
            if not symbol.endswith("USDT"):
                continue

            # 24h quote volume
            try:
                quote_volume = float(
                    x.get("quoteVolume", 0)
                )
            except:
                quote_volume = 0

            if quote_volume <= 0:
                continue

            symbols.append(
                (
                    symbol,
                    quote_volume
                )
            )

        # Həcmə görə sıralama
        symbols.sort(
            key=lambda x: x[1],
            reverse=True
        )

        return [
            x[0]
            for x in symbols[:TOP_COINS]
        ]

    except Exception as e:

        print(
            "Top symbols error:",
            e
        )

        return []


# ============================================================
# GET 100 CLOSED 5M CANDLES
# ============================================================

def get_closed_candles(symbol):

    try:

        data = binance_get(
            "/api/v3/klines",
            {
                "symbol": symbol,
                "interval": INTERVAL,
                "limit": CANDLE_LIMIT + 1
            }
        )

        now_ms = int(
            time.time() * 1000
        )

        candles = []

        for c in data:

            open_time = int(c[0])
            close_time = int(c[6])

            # Hələ bağlanmamış şamı at
            if close_time >= now_ms:
                continue

            candles.append({
                "open_time": open_time,
                "close_time": close_time,

                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),

                "volume": float(c[5])
            })

        # Son 100 bağlanmış şam
        candles = candles[-CANDLE_LIMIT:]

        return candles

    except Exception as e:

        print(
            f"{symbol}: candle error:",
            e
        )

        return []


# ============================================================
# HIGH VALIDATION
# ============================================================

def is_valid_high(candles, index):

    """
    High yalnız əvvəlində ən azı 10 şam olduqda keçərlidir.

    Məsələn:

    əvvəlki 10 şamın High-ları:
    100
    101
    99
    102
    ...

    yeni High = 105

    105 hamısından yüksəkdirsə:
    VALID HIGH
    """

    if index < LEFT_HIGH_CANDLES:
        return False

    current_high = candles[index]["high"]

    previous_highs = [
        candles[j]["high"]
        for j in range(
            index - LEFT_HIGH_CANDLES,
            index
        )
    ]

    return current_high > max(previous_highs)


# ============================================================
# STRATEGY
# ============================================================

def analyze_symbol(symbol, candles):

    if len(candles) < LEFT_HIGH_CANDLES + 2:
        return None

    #
    # STATE
    #
    # target_high:
    # Hazırda qırılması gözlənilən High
    #
    # active_dib:
    # Hazırkı aktiv DIB
    #

    target_high = None
    target_high_index = None

    active_dib = None
    active_dib_index = None

    #
    # Əvvəlcə keçmişdə formalaşmış struktur qurulur.
    #

    started = False

    for i in range(
        LEFT_HIGH_CANDLES,
        len(candles)
    ):

        candle = candles[i]

        # ==================================================
        # 1. Hələ struktur başlamayıbsa,
        #    ilk valid High axtar
        # ==================================================

        if not started:

            if is_valid_high(candles, i):

                target_high = candle["high"]
                target_high_index = i

                started = True

            continue

        # ==================================================
        # 2. TARGET HIGH ARTİQ VAR
        # ==================================================

        # --------------------------------------------------
        # Əgər yeni DIB yaranırsa
        # --------------------------------------------------

        if active_dib is None:

            # İlk DIB:
            #
            # target High-dan sonra ən aşağı low
            #
            if i > target_high_index:

                active_dib = candle["low"]
                active_dib_index = i

        else:

            #
            # Cari aktiv DIB-dən aşağı düşdüsə:
            # yeni DIB yaranır
            #

            if candle["low"] < active_dib:

                active_dib = candle["low"]
                active_dib_index = i

        # ==================================================
        # 3. BREAKOUT
        # ==================================================

        if target_high is not None:

            #
            # Şam həm High-ı keçməlidir,
            # həm də üstündə bağlanmalıdır.
            #

            if (
                candle["high"] > target_high
                and
                candle["close"] > target_high
            ):

                return {
                    "type": "BREAKOUT",

                    "symbol": symbol,

                    "target_high": target_high,

                    "breakout_price": candle["close"],

                    "dib": active_dib,

                    "candle_index": i,

                    "candle": candle
                }

        # ==================================================
        # 4. YENİ VALID HIGH
        # ==================================================
        #
        # Çox vacib:
        #
        # DIB-dən sonra qiymət qalxır.
        #
        # Əgər yeni zirvə:
        # - əvvəlki 10 şamdan yüksəkdirsə
        #
        # həmin zirvə yeni High kimi qəbul edilir.
        #
        # Amma bu High əvvəlki target High-ı
        # qıra bilməyibsə, yeni target olur.
        #
        # ==================================================

        if i > active_dib_index:

            if is_valid_high(candles, i):

                new_high = candle["high"]

                #
                # Əgər bu High əvvəlki target High-dan
                # aşağıdırsa, əvvəlki High qırılmayıb.
                #
                # Buna görə yeni High target ola bilər.
                #

                if new_high < target_high:

                    target_high = new_high
                    target_high_index = i

                    #
                    # Bu yeni High-dan sonra yaranacaq
                    # yeni DIB üçün gözləməyə davam edilir.
                    #

                elif new_high == target_high:

                    # Eyni səviyyədirsə dəyişmir
                    pass

        # ==================================================
        # 5. Əgər yeni High formalaşdısa,
        #    aktiv DIB həmin High-dan əvvəlki DIB olaraq qalır.
        #
        # Yeni DIB yalnız əvvəlki DIB aşağı qırıldıqda yaranır.
        # ==================================================

    return None


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def make_signal_message(signal):

    symbol = signal["symbol"]

    high_price = signal["target_high"]

    close_price = signal["breakout_price"]

    dib_price = signal["dib"]

    candle = signal["candle"]

    breakout_percent = (
        (close_price - high_price)
        / high_price
        * 100
    )

    utc_time = datetime.fromtimestamp(
        candle["close_time"] / 1000,
        tz=timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )

    message = (
        "🚨 5M BREAKOUT SIGNAL 🚨\n\n"
        f"🪙 {symbol}\n"
        f"📈 High: {high_price:g}\n"
        f"💰 Close: {close_price:g}\n"
        f"📉 Active DIB: {dib_price:g}\n\n"
        f"🔥 Breakout: +{breakout_percent:.2f}%\n"
        f"⏱ Candle: {utc_time}\n\n"
        "⚠️ ALERT ONLY — NO AUTO ORDER"
    )

    return message


# ============================================================
# PROCESS SYMBOL
# ============================================================

def process_symbol(symbol):

    candles = get_closed_candles(symbol)

    if len(candles) < LEFT_HIGH_CANDLES + 2:
        return None

    latest_candle_time = candles[-1]["open_time"]

    #
    # Eyni bağlanmış şamı təkrar analiz etmə
    #

    if (
        symbol in last_processed_candle
        and
        last_processed_candle[symbol]
        == latest_candle_time
    ):
        return None

    last_processed_candle[symbol] = latest_candle_time

    signal = analyze_symbol(
        symbol,
        candles
    )

    if signal is None:
        return None

    #
    # İlk scan zamanı köhnə siqnal göndərmə.
    #
    # Bot artıq işləyərkən yaranan yeni breakout
    # göndəriləcək.
    #

    if symbol not in initialized_symbols:

        initialized_symbols.add(symbol)

        return None

    initialized_symbols.add(symbol)

    #
    # 24 saat cooldown
    #

    now = time.time()

    previous_signal = last_signal.get(symbol)

    if previous_signal is not None:

        if now - previous_signal < SIGNAL_COOLDOWN_SECONDS:
            return None

    last_signal[symbol] = now

    return signal


# ============================================================
# MAIN SCAN
# ============================================================

def scan():

    symbols = get_top_100_symbols()

    if not symbols:
        print("Top 100 tapılmadı.")
        return

    print(
        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
        f"Scanning {len(symbols)} symbols..."
    )

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                process_symbol,
                symbol
            ): symbol
            for symbol in symbols
        }

        for future in as_completed(futures):

            symbol = futures[future]

            try:

                signal = future.result()

                if signal:

                    message = make_signal_message(
                        signal
                    )

                    print("\n" + message + "\n")

                    send_telegram(
                        message
                    )

            except Exception as e:

                print(
                    f"{symbol}: processing error:",
                    e
                )


# ============================================================
# MAIN LOOP
# ============================================================

def main():

    print("=" * 60)
    print("BINANCE 5M HIGH / DIB BREAKOUT ALERT BOT")
    print("=" * 60)
    print("ALERT ONLY")
    print("NO AUTOMATIC ORDER")
    print(
        f"Top coins: {TOP_COINS}"
    )
    print(
        f"Candles: {CANDLE_LIMIT}"
    )
    print(
        f"High left candles: {LEFT_HIGH_CANDLES}"
    )
    print("=" * 60)

    while True:

        try:

            scan()

        except Exception as e:

            print(
                "MAIN ERROR:",
                e
            )

        time.sleep(
            SCAN_SECONDS
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
