import os
import time
import requests

from datetime import datetime
from zoneinfo import ZoneInfo


# ============================================================
# BINANCE 15M DIB -> HIGH -> DIB2 -> BREAKOUT
# ALERT ONLY / NO AUTOMATIC ORDER
# ============================================================

BINANCE_URL = "https://api.binance.com"

INTERVAL = "15m"

TOP_COINS = 100

# HIGH 1 üçün solda minimum 10 şam
LEFT_HIGH_CANDLES = 10

# DIB 1 maksimum 100 şam daxilində təsdiqlənməlidir
MAX_DIB1_CANDLES = 100

# HIGH 1-dən minimum breakout
MIN_BREAKOUT_PERCENT = 1.0

# DIB2-dən qalxış 10-dan çox şam olarsa rollover
DIB2_LONG_RISE_CANDLES = 10

# 3 və daha az şamlıq uğursuz qalxış
DIB2_SHORT_RISE_CANDLES = 3

# TOP 100 yenilənməsi
TOP_REFRESH_SECONDS = 300

# Scan
SCAN_SECONDS = 10

AZ_TZ = ZoneInfo("Asia/Baku")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

session = requests.Session()


# ============================================================
# STARTUP
# ============================================================

# Bot işə düşdüyü an
START_TIME_MS = int(time.time() * 1000)


# ============================================================
# STATE
# ============================================================

def empty_state():
    return {
        # SEARCH_DIB1
        # TRACK_DIB1
        # TRACK_DIB2
        "mode": "SEARCH_DIB1",

        # -------------------------
        # DIB 1
        # -------------------------
        "dib1": None,
        "dib1_time": None,

        # DIB1-dən sonra neçə yeni şam keçib
        "dib1_candles": 0,

        # -------------------------
        # HIGH 1
        # -------------------------
        "high1": None,
        "high1_time": None,

        # -------------------------
        # DIB 2
        # -------------------------
        "dib2": None,
        "dib2_time": None,

        # DIB2-dən sonra ümumi şam sayı
        "dib2_candles": 0,

        # DIB2-dən yuxarı qalxış şamları
        "dib2_rise_candles": 0,

        # DIB2-dən sonra görülən ən yüksək qiymət
        "dib2_high": None,
        "dib2_high_time": None,

        # Son işlənmiş şam
        "last_close_time": None,

        # Siqnal sayı
        "signals": 0,
    }


states = {}


# ============================================================
# TIME
# ============================================================

def fmt_time(ms):

    if ms is None:
        return "-"

    return datetime.fromtimestamp(
        ms / 1000,
        AZ_TZ
    ).strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN yoxdur")
        return

    if not TELEGRAM_CHAT_ID:
        print("ERROR: TELEGRAM_CHAT_ID yoxdur")
        return

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    try:

        response = session.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message
            },
            timeout=10
        )

        if response.status_code != 200:

            print(
                "Telegram error:",
                response.status_code,
                response.text
            )

    except Exception as e:

        print(
            "Telegram exception:",
            e
        )


# ============================================================
# TOP 100
# ============================================================

def get_top_100():

    try:

        response = session.get(
            f"{BINANCE_URL}/api/v3/ticker/24hr",
            timeout=10
        )

        response.raise_for_status()

        data = response.json()

        coins = []

        for item in data:

            symbol = item.get("symbol", "")

            # USDT
            if not symbol.endswith("USDT"):
                continue

            # Leveraged tokenləri çıxar
            if (
                symbol.endswith("UPUSDT")
                or symbol.endswith("DOWNUSDT")
                or symbol.endswith("BULLUSDT")
                or symbol.endswith("BEARUSDT")
            ):
                continue

            try:
                quote_volume = float(
                    item.get("quoteVolume", 0)
                )
            except Exception:
                continue

            if quote_volume <= 0:
                continue

            coins.append(
                (
                    symbol,
                    quote_volume
                )
            )

        coins.sort(
            key=lambda x: x[1],
            reverse=True
        )

        return [
            symbol
            for symbol, volume in coins[:TOP_COINS]
        ]

    except Exception as e:

        print(
            "TOP 100 error:",
            e
        )

        return []


# ============================================================
# GET LAST CLOSED CANDLE
# ============================================================

def get_latest_closed_candle(symbol):

    try:

        response = session.get(
            f"{BINANCE_URL}/api/v3/klines",
            params={
                "symbol": symbol,
                "interval": INTERVAL,
                "limit": 2
            },
            timeout=10
        )

        response.raise_for_status()

        data = response.json()

        if len(data) < 2:
            return None

        k = data[-2]

        return {
            "open_time": int(k[0]),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "close_time": int(k[6]),
        }

    except Exception as e:

        print(
            f"{symbol}: candle error:",
            e
        )

        return None


# ============================================================
# NEW DIB1
# ============================================================

def make_new_dib1(
    state,
    candle
):

    state["mode"] = "TRACK_DIB1"

    state["dib1"] = candle["low"]
    state["dib1_time"] = candle["close_time"]

    state["dib1_candles"] = 0

    state["high1"] = None
    state["high1_time"] = None

    state["dib2"] = None
    state["dib2_time"] = None

    state["dib2_candles"] = 0
    state["dib2_rise_candles"] = 0

    state["dib2_high"] = None
    state["dib2_high_time"] = None


# ============================================================
# INITIAL DIB1 STRUCTURE
# ============================================================

def process_dib1(
    symbol,
    state,
    candle
):

    low = candle["low"]
    high = candle["high"]

    # ========================================================
    # DIB1 YOXDUR
    # ========================================================

    if state["dib1"] is None:

        make_new_dib1(
            state,
            candle
        )

        return

    # ========================================================
    # DIB1 YAŞI
    # ========================================================

    state["dib1_candles"] += 1

    age = state["dib1_candles"]

    # ========================================================
    # 100 ŞAM QAYDASI
    #
    # 101-ci şama keçibsə və əvvəlki DIB1 təsdiqlənməyibsə,
    # köhnə struktur ləğv edilir.
    # Cari şamdan canlı şəkildə yenidən başlanır.
    # ========================================================

    if age > MAX_DIB1_CANDLES:

        make_new_dib1(
            state,
            candle
        )

        return

    # ========================================================
    # DIB1 QIRILMASI
    # ========================================================

    if low < state["dib1"]:

        # ----------------------------------------------------
        # Əgər 10 şam hələ tamam olmayıbsa:
        #
        # köhnə DIB1 təsdiqlənmir.
        # Qıran yeni aşağı qiymət yeni DIB1 olur.
        # ----------------------------------------------------

        if age < LEFT_HIGH_CANDLES:

            make_new_dib1(
                state,
                candle
            )

            return

        # ----------------------------------------------------
        # 10+ şam var.
        #
        # HIGH1 də yaranıbsa:
        # DIB1 + HIGH1 təsdiqlənir.
        # Qıran candle LOW = DIB2.
        # ----------------------------------------------------

        if state["high1"] is not None:

            confirm_dib1_high1(
                state,
                candle
            )

            return

        # ----------------------------------------------------
        # 10+ şam var, amma keçərli HIGH1 yoxdur.
        #
        # Köhnə DIB1 artıq keçərli deyil.
        # Qıran aşağı qiymət yeni DIB1 olur.
        # ----------------------------------------------------

        make_new_dib1(
            state,
            candle
        )

        return

    # ========================================================
    # HIGH1 İZLƏ
    #
    # HIGH yalnız ən azı 10 şam solda olduqdan sonra
    # qəbul edilir.
    #
    # Ən yüksək HIGH daim yenilənir.
    # ========================================================

    if age >= LEFT_HIGH_CANDLES:

        if (
            state["high1"] is None
            or high > state["high1"]
        ):

            state["high1"] = high
            state["high1_time"] = (
                candle["close_time"]
            )


# ============================================================
# CONFIRM DIB1 + HIGH1
# ============================================================

def confirm_dib1_high1(
    state,
    breaking_candle
):

    state["mode"] = "TRACK_DIB2"

    # DIB1 və HIGH1 artıq təsdiqləndi.
    #
    # DIB1 dəyişmir.
    # HIGH1 dəyişmir.
    #
    # DIB1-i qıran candle LOW = DIB2.

    state["dib2"] = breaking_candle["low"]

    state["dib2_time"] = (
        breaking_candle["close_time"]
    )

    state["dib2_candles"] = 0

    state["dib2_rise_candles"] = 0

    state["dib2_high"] = None
    state["dib2_high_time"] = None


# ============================================================
# DIB2 -> NEW DIB1 ROLLOVER
# ============================================================

def rollover_dib2(
    state,
    breaking_candle
):

    old_dib2 = state["dib2"]
    old_dib2_time = state["dib2_time"]

    old_high = state["dib2_high"]
    old_high_time = state["dib2_high_time"]

    # ========================================================
    # OLD DIB2 -> NEW DIB1
    #
    # Çünki old DIB2 aşağı qırılıb.
    # ========================================================

    state["dib1"] = old_dib2
    state["dib1_time"] = old_dib2_time

    # Bu DIB1 artıq təsdiqlənmiş strukturdan gəldiyi üçün
    # yeni HIGH1 də artıq məlumdur.
    state["dib1_candles"] = (
        state["dib2_candles"]
    )

    state["high1"] = old_high
    state["high1_time"] = old_high_time

    # ========================================================
    # QIRAN YENİ AŞAĞI QİYMƏ -> YENİ DIB2
    # ========================================================

    state["dib2"] = breaking_candle["low"]
    state["dib2_time"] = (
        breaking_candle["close_time"]
    )

    state["dib2_candles"] = 0
    state["dib2_rise_candles"] = 0

    state["dib2_high"] = None
    state["dib2_high_time"] = None

    state["mode"] = "TRACK_DIB2"


# ============================================================
# DIB2 SHORT FAILURE
# ============================================================

def short_dib2_reset(
    state,
    candle
):

    # Köhnə DIB1 / HIGH1 / DIB2 hamısı silinir.
    #
    # DIB2-ni qıran aşağı qiymət
    # tamamilə yeni DIB1 olur.

    make_new_dib1(
        state,
        candle
    )


# ============================================================
# DIB2 PROCESS
# ============================================================

def process_dib2(
    symbol,
    state,
    candle
):

    dib2 = state["dib2"]
    high1 = state["high1"]

    if dib2 is None or high1 is None:

        short_dib2_reset(
            state,
            candle
        )

        return

    # ========================================================
    # 1. BREAKOUT
    #
    # HIGH1-dən minimum +1%
    #
    # YALNIZ CLOSE ilə.
    # Wick kifayət deyil.
    # ========================================================

    breakout_level = (
        high1
        * (1 + MIN_BREAKOUT_PERCENT / 100)
    )

    if candle["close"] >= breakout_level:

        breakout_percent = (
            (
                candle["close"]
                - high1
            )
            / high1
            * 100
        )

        message = (
            "🚨 15M BREAKOUT SIGNAL 🚨\n\n"

            f"🪙 Coin: {symbol}\n\n"

            "📉 DIB 1\n"
            f"Price: {state['dib1']}\n"
            f"Time: {fmt_time(state['dib1_time'])}\n\n"

            "📈 HIGH 1\n"
            f"Price: {state['high1']}\n"
            f"Time: {fmt_time(state['high1_time'])}\n\n"

            "📉 DIB 2\n"
            f"Price: {state['dib2']}\n"
            f"Time: {fmt_time(state['dib2_time'])}\n\n"

            "🔥 BREAKOUT\n"
            f"Close: {candle['close']}\n"
            f"Time: {fmt_time(candle['close_time'])}\n"
            f"Breakout: +{breakout_percent:.2f}%\n\n"

            "✅ HIGH 1 BROKEN\n"
            "✅ Breakout >= +1%\n"
            "⚠️ ALERT ONLY — NO AUTO ORDER"
        )

        send_telegram(message)

        state["signals"] += 1

        # Siqnaldan sonra yeni canlı struktur.
        make_new_dib1(
            state,
            candle
        )

        return

    # ========================================================
    # 2. DIB2 AŞAĞI QIRILIB
    # ========================================================

    if candle["low"] < dib2:

        rise_candles = (
            state["dib2_rise_candles"]
        )

        # ----------------------------------------------------
        # 10-DAN ÇOX ŞAM
        #
        # DIB2 -> yeni DIB1
        # ən yüksək qiymət -> yeni HIGH1
        # qıran qiymət -> yeni DIB2
        # ----------------------------------------------------

        if rise_candles > DIB2_LONG_RISE_CANDLES:

            rollover_dib2(
                state,
                candle
            )

            return

        # ----------------------------------------------------
        # 3 VƏ YA DAHA AZ ŞAM
        #
        # Tam reset.
        # ----------------------------------------------------

        if rise_candles <= DIB2_SHORT_RISE_CANDLES:

            short_dib2_reset(
                state,
                candle
            )

            return

        # ----------------------------------------------------
        # 4-10 şam
        #
        # HIGH1 qırılmayıbsa,
        # köhnə struktur saxlanılmır.
        #
        # Yeni aşağı qiymət yeni DIB1.
        # ----------------------------------------------------

        short_dib2_reset(
            state,
            candle
        )

        return

    # ========================================================
    # 3. DIB2 QIRILMAYIB
    # ========================================================

    state["dib2_candles"] += 1

    # ========================================================
    # DIB2-DƏN QALXAN HƏRƏKƏT
    # ========================================================

    if candle["close"] > dib2:

        state["dib2_rise_candles"] += 1

    # ========================================================
    # DIB2-DƏN SONRA ƏN YÜKSƏK QİYMƏ
    # ========================================================

    if (
        state["dib2_high"] is None
        or candle["high"] > state["dib2_high"]
    ):

        state["dib2_high"] = candle["high"]

        state["dib2_high_time"] = (
            candle["close_time"]
        )


# ============================================================
# PROCESS CANDLE
# ============================================================

def process_candle(
    symbol,
    candle
):

    if symbol not in states:

        states[symbol] = empty_state()

    state = states[symbol]

    # ========================================================
    # STARTUP-DAN ƏVVƏL BAŞLAYAN ŞAMLAR İSTİFADƏ EDİLMİR
    #
    # Məsələn bot 22:13-də başlayıbsa:
    #
    # 22:00 -> 22:15 şamı istifadə edilmir.
    #
    # İlk istifadə edilən tam yeni şam:
    #
    # 22:15 -> 22:30
    # ========================================================

    if candle["open_time"] < START_TIME_MS:

        return False

    # ========================================================
    # DUPLICATE
    # ========================================================

    if (
        state["last_close_time"] is not None
        and candle["close_time"]
        <= state["last_close_time"]
    ):

        return False

    state["last_close_time"] = (
        candle["close_time"]
    )

    # ========================================================
    # STATE MACHINE
    # ========================================================

    if state["mode"] == "SEARCH_DIB1":

        process_dib1(
            symbol,
            state,
            candle
        )

    elif state["mode"] == "TRACK_DIB1":

        process_dib1(
            symbol,
            state,
            candle
        )

    elif state["mode"] == "TRACK_DIB2":

        process_dib2(
            symbol,
            state,
            candle
        )

    return True


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 65)
    print("BINANCE 15M DIB/HIGH BREAKOUT BOT")
    print("ALERT ONLY / NO AUTOMATIC ORDER")
    print("=" * 65)

    print(
        "START:",
        fmt_time(START_TIME_MS)
    )

    print(
        "TIMEFRAME:",
        INTERVAL
    )

    print(
        "TOP:",
        TOP_COINS
    )

    print(
        "HIGH LEFT:",
        LEFT_HIGH_CANDLES
    )

    print(
        "DIB1 MAX:",
        MAX_DIB1_CANDLES
    )

    print(
        "BREAKOUT:",
        f"+{MIN_BREAKOUT_PERCENT}%"
    )

    top_symbols = []

    last_top_refresh = 0

    scan_number = 0

    while True:

        try:

            now = time.time()

            # =================================================
            # TOP 100
            # =================================================

            if (
                not top_symbols
                or now - last_top_refresh
                >= TOP_REFRESH_SECONDS
            ):

                new_symbols = get_top_100()

                if new_symbols:

                    top_symbols = new_symbols

                    last_top_refresh = now

                    print(
                        f"TOP 100 updated | "
                        f"Symbols: {len(top_symbols)}"
                    )

            # =================================================
            # SCAN
            # =================================================

            scan_number += 1

            new_candles = 0

            for symbol in top_symbols:

                candle = get_latest_closed_candle(
                    symbol
                )

                if candle is None:
                    continue

                if process_candle(
                    symbol,
                    candle
                ):

                    new_candles += 1

            print(
                f"[{datetime.now(AZ_TZ).strftime('%Y-%m-%d %H:%M:%S')}] "
                f"HEARTBEAT | "
                f"Scan #{scan_number} | "
                f"Symbols: {len(top_symbols)} | "
                f"New candles: {new_candles}"
            )

            time.sleep(SCAN_SECONDS)

        except KeyboardInterrupt:

            print("BOT STOPPED")
            break

        except Exception as e:

            print(
                "MAIN LOOP ERROR:",
                e
            )

            time.sleep(10)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
