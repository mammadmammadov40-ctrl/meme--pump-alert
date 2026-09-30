import os
import time
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# BINANCE 15M DIB -> HIGH -> DIB2 -> +1% BREAKOUT
# ============================================================
#
# ALERT ONLY
# NO AUTOMATIC ORDER
#
# ƏSAS MƏNTİQ:
#
# DIB 1
#   ↓
# DIB 1-dən sonra bütün yüksəliş izlənir
#   ↓
# görülən ƏN YÜKSƏK qiymət = HIGH 1
#   ↓
# qiymət DIB 1-i aşağı qırır
#   ↓
# DIB 1 + HIGH 1 təsdiqlənir
#   ↓
# DIB 2 izlənir
#   ↓
# HIGH 1 +1% üstündə CLOSE
#   ↓
# TELEGRAM SIGNAL
#
# ============================================================
# VACİB:
#
# 10 şam HIGH-ı seçmək üçün limit DEYİL.
#
# 10 şam = HIGH 1-in solunda minimum 10 şam olması qaydasıdır.
#
# Məsələn:
#
# DIB1 = 0.060
#
# 0.063
# 0.066
# 0.070
# 0.080
# ↓
# 0.073
# 0.069
# ↑
# 0.078
# 0.086
# 0.097
# 0.100   <-- HIGH 1
# ↓
# 0.059   <-- DIB1 qırıldı
#
# HIGH1 = 0.100
#
# 0.080 seçilmir.
# 0.068 seçilmir.
# İlk high seçilmir.
#
# Bütün yüksəliş boyunca ən yüksək qiymət izlənir.
#
# ============================================================


# -------------------------
# CONFIG
# -------------------------

BINANCE_URL = "https://api.binance.com"

INTERVAL = "15m"

TOP_COINS = 100

SCAN_SECONDS = 10

TOP100_UPDATE_SECONDS = 60

LEFT_HIGH_CANDLES = 10

MAX_DIB1_CANDLES = 100

MIN_BREAKOUT_PERCENT = 1.0

# DIB2 qalxışında:
# 10 və ya daha az şam -> yeni DIB1
# 10-dan çox şam -> rollover
DIB2_LONG_RISE_CANDLES = 10

AZ_TZ = ZoneInfo("Asia/Baku")


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# ============================================================
# SESSION
# ============================================================

session = requests.Session()

START_TIME_MS = int(time.time() * 1000)


# ============================================================
# STATE
# ============================================================

SEARCH_DIB1 = "SEARCH_DIB1"
TRACK_DIB2 = "TRACK_DIB2"


states = {}


# ============================================================
# TIME
# ============================================================

def now_baku():
    return datetime.now(AZ_TZ)


def format_time(ms):
    return datetime.fromtimestamp(
        ms / 1000,
        tz=AZ_TZ
    ).strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram ENV dəyişənləri yoxdur.")
        return

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    try:

        response = session.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message
            },
            timeout=10
        )

        if not response.ok:
            print(
                "Telegram error:",
                response.status_code,
                response.text
            )

    except Exception as e:
        print("Telegram error:", e)


# ============================================================
# TOP 100
# ============================================================

def get_top_100():

    try:

        response = session.get(
            BINANCE_URL + "/api/v3/ticker/24hr",
            timeout=10
        )

        response.raise_for_status()

        data = response.json()

        coins = []

        for item in data:

            symbol = item.get("symbol", "")

            if not symbol.endswith("USDT"):
                continue

            if any(
                symbol.endswith(x)
                for x in ["UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT"]
            ):
                continue

            try:
                volume = float(item.get("quoteVolume", 0))
            except:
                continue

            coins.append(
                (
                    symbol,
                    volume
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

        print("TOP 100 error:", e)

        return []


# ============================================================
# LATEST CLOSED 15M CANDLE
# ============================================================

def get_latest_closed_candle(symbol):

    try:

        response = session.get(
            BINANCE_URL + "/api/v3/klines",
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

        # Binance:
        # data[-1] = current/open candle
        # data[-2] = last CLOSED candle

        k = data[-2]

        return {
            "open_time": int(k[0]),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "close_time": int(k[6])
        }

    except Exception as e:

        print(symbol, "candle error:", e)

        return None


# ============================================================
# INITIAL STATE
# ============================================================

def new_search_state():

    return {
        "phase": SEARCH_DIB1,

        # DIB1
        "dib1": None,
        "dib1_time": None,
        "dib1_index": None,

        # HIGH1
        "high1": None,
        "high1_time": None,
        "high1_index": None,

        # Last processed candle
        "last_close_time": None,

        # Candle counter since bot started
        "index": -1
    }


# ============================================================
# RESET TO NEW DIB1
# ============================================================

def start_new_dib1(state, candle, index):

    state["phase"] = SEARCH_DIB1

    state["dib1"] = candle["low"]
    state["dib1_time"] = candle["close_time"]
    state["dib1_index"] = index

    # ÇOX VACİB:
    #
    # Yeni DIB1 yarananda köhnə HIGH silinir.
    #
    # Yeni DIB1-dən sonrakı şamlarla
    # HIGH yenidən izlənəcək.
    #
    state["high1"] = None
    state["high1_time"] = None
    state["high1_index"] = None


# ============================================================
# UPDATE HIGH1
# ============================================================

def update_high1(state, candle, index):

    high = candle["high"]

    if state["high1"] is None:

        state["high1"] = high
        state["high1_time"] = candle["close_time"]
        state["high1_index"] = index

        return

    # Yalnız DAHA YÜKSƏK high gələndə dəyişir.
    #
    # Əgər qiymət düşsə:
    #
    # HIGH1 SİLİNMİR.
    #
    # Sonra yenidən qalxıb daha yüksək qiymət
    # yaradarsa HIGH1 yenilənir.

    if high > state["high1"]:

        state["high1"] = high
        state["high1_time"] = candle["close_time"]
        state["high1_index"] = index


# ============================================================
# HIGH1 VALIDATION
# ============================================================

def valid_high1(state):

    if state["high1"] is None:
        return False

    if state["high1_index"] is None:
        return False

    # HIGH1-in solunda minimum 10 şam.
    #
    # Bu şərt HIGH1-i ilk 10 şamda dondurmur.
    #
    # Sadəcə HIGH1-in özü üçün minimum 10 sol şam
    # tələb edir.

    if state["high1_index"] < LEFT_HIGH_CANDLES:
        return False

    return True


# ============================================================
# INITIAL DIB1 LOGIC
# ============================================================

def process_search_dib1(symbol, state, candle, index):

    dib1 = state["dib1"]

    # --------------------------------------------------------
    # 1. ƏGƏR DIB1 HƏLƏ YOXDUR
    # --------------------------------------------------------

    if dib1 is None:

        start_new_dib1(
            state,
            candle,
            index
        )

        return None


    # --------------------------------------------------------
    # 2. DIB1 YAŞI
    # --------------------------------------------------------

    dib1_age = index - state["dib1_index"]


    # --------------------------------------------------------
    # 3. 101-ci şam
    #
    # DIB1 100 şam daxilində təsdiqlənməlidir.
    #
    # 101-ci şama çıxırsa:
    #
    # köhnə struktur silinir
    # yeni canlı DIB1 cari candle low olur.
    # --------------------------------------------------------

    if dib1_age > MAX_DIB1_CANDLES:

        print(
            f"{symbol}: DIB1 100 şamı keçdi -> RESET"
        )

        start_new_dib1(
            state,
            candle,
            index
        )

        return None


    # --------------------------------------------------------
    # 4. ƏVVƏL HIGH-I YENİLƏ
    #
    # Bu çox vacibdir.
    #
    # DIB1-i qıran candle özü daha yüksək HIGH
    # yaradıbsa, həmin HIGH də nəzərə alınır.
    # --------------------------------------------------------

    update_high1(
        state,
        candle,
        index
    )


    # --------------------------------------------------------
    # 5. DIB1 AŞAĞI QIRILDI?
    # --------------------------------------------------------

    if candle["low"] < dib1:

        # --------------------------------------------
        # Əgər 10 şam tamam olmayıbsa:
        #
        # Köhnə DIB1 təsdiqlənmir.
        #
        # Qıran aşağı qiymət yeni DIB1 olur.
        # --------------------------------------------

        if dib1_age < LEFT_HIGH_CANDLES:

            print(
                f"{symbol}: DIB1 erkən qırıldı -> "
                f"yeni DIB1 = {candle['low']}"
            )

            start_new_dib1(
                state,
                candle,
                index
            )

            return None


        # --------------------------------------------
        # HIGH1 mütləq 10 sol şamlı olmalıdır.
        # --------------------------------------------

        if not valid_high1(state):

            print(
                f"{symbol}: DIB1 qırıldı, "
                f"amma HIGH1 10 sol şam şərtini ödəmir."
            )

            # Qıran qiymət yeni DIB1 olur.
            start_new_dib1(
                state,
                candle,
                index
            )

            return None


        # --------------------------------------------
        # BURADA DIB1 + HIGH1 TƏSDİQLƏNİR
        # --------------------------------------------

        print(
            f"{symbol}: DIB1 CONFIRMED | "
            f"DIB1={dib1} | "
            f"HIGH1={state['high1']}"
        )


        # --------------------------------------------
        # DIB2:
        #
        # DIB1-i qıran candle-in low-u
        # ilk DIB2 olur.
        # --------------------------------------------

        state["phase"] = TRACK_DIB2

        state["dib2"] = candle["low"]
        state["dib2_time"] = candle["close_time"]
        state["dib2_index"] = index

        # DIB2-dən sonrakı qalxış üçün
        # HIGH1 ayrıca izlənmir;
        # breakout əsas HIGH1-ə görədir.

        state["dib2_high"] = None
        state["dib2_high_time"] = None
        state["dib2_high_index"] = None

        return None


    # --------------------------------------------------------
    # 6. DIB1 QIRILMAYIBSA
    #
    # Heç nə sıfırlanmır.
    #
    # HIGH1 ən yüksək qiyməti saxlamağa davam edir.
    # --------------------------------------------------------

    return None


# ============================================================
# DIB2 HIGH TRACK
# ============================================================

def update_dib2_high(state, candle, index):

    high = candle["high"]

    if state["dib2_high"] is None:

        state["dib2_high"] = high
        state["dib2_high_time"] = candle["close_time"]
        state["dib2_high_index"] = index

    elif high > state["dib2_high"]:

        state["dib2_high"] = high
        state["dib2_high_time"] = candle["close_time"]
        state["dib2_high_index"] = index


# ============================================================
# BREAKOUT
# ============================================================

def breakout_percent(close, high1):

    return (
        (close - high1)
        / high1
        * 100
    )


# ============================================================
# SEND BREAKOUT SIGNAL
# ============================================================

def send_breakout_signal(
    symbol,
    state,
    candle,
    percentage
):

    message = f"""
🚨 15M BREAKOUT SIGNAL 🚨

🪙 Coin: {symbol}

📉 DIB 1
Price: {state['dib1']:.12g}
Time: {format_time(state['dib1_time'])}

📈 HIGH 1
Price: {state['high1']:.12g}
Time: {format_time(state['high1_time'])}

📉 DIB 2
Price: {state['dib2']:.12g}
Time: {format_time(state['dib2_time'])}

🔥 BREAKOUT
Close: {candle['close']:.12g}
Time: {format_time(candle['close_time'])}
Breakout: +{percentage:.2f}%

✅ HIGH 1 BROKEN
✅ Breakout >= +1%
⚠️ ALERT ONLY — NO AUTO ORDER
""".strip()

    send_telegram(message)

    print(message)


# ============================================================
# DIB2 LOGIC
# ============================================================

def process_dib2(symbol, state, candle, index):

    dib2 = state["dib2"]
    high1 = state["high1"]


    # --------------------------------------------------------
    # 1. ƏVVƏL BREAKOUT YOXLA
    #
    # Close HIGH1-dən minimum +1% yuxarıdırsa
    # siqnal gəlir.
    #
    # Wick kifayət deyil.
    # YALNIZ CLOSE.
    # --------------------------------------------------------

    if high1 is not None:

        percentage = breakout_percent(
            candle["close"],
            high1
        )

        if (
            candle["close"] > high1
            and percentage >= MIN_BREAKOUT_PERCENT
        ):

            send_breakout_signal(
                symbol,
                state,
                candle,
                percentage
            )

            # Siqnaldan sonra köhnə struktur bitir.
            #
            # Cooldown yoxdur.
            #
            # Növbəti yeni candle ilə yeni DIB1
            # axtarılacaq.

            states[symbol] = new_search_state()

            return


    # --------------------------------------------------------
    # 2. DIB2 YENİ AŞAĞI LOW EDİRSƏ
    # --------------------------------------------------------

    if candle["low"] < dib2:

        dib2_age = index - state["dib2_index"]


        # ====================================================
        # DIB2 10 VƏ YA DAHA AZ ŞAMDAN SONRA QIRILDI
        # ====================================================
        #
        # Bu halda köhnə struktur tam silinir.
        #
        # QIRAN LOW = YENİ DIB1
        #
        # HIGH1 yenidən sıfırdan izlənəcək.
        #
        # ====================================================

        if dib2_age <= DIB2_LONG_RISE_CANDLES:

            print(
                f"{symbol}: DIB2 qırıldı "
                f"({dib2_age} şam) -> "
                f"FRESH NEW DIB1"
            )

            start_new_dib1(
                state,
                candle,
                index
            )

            return


        # ====================================================
        # DIB2 10-DAN ÇOX ŞAM SONRA QIRILDI
        # ====================================================
        #
        # Burada struktur TAM sıfırlanmır.
        #
        # KÖHNƏ DIB2
        #       ↓
        # YENİ DIB1
        #
        # DIB2-dən qalxan qiymətin ƏN YÜKSƏK nöqtəsi
        #       ↓
        # YENİ HIGH1
        #
        # DIB2-ni qıran yeni aşağı qiymət
        #       ↓
        # YENİ DIB2
        #
        # ====================================================

        old_dib2 = state["dib2"]
        old_dib2_time = state["dib2_time"]

        old_high = state["dib2_high"]
        old_high_time = state["dib2_high_time"]
        old_high_index = state["dib2_high_index"]


        # Əgər DIB2-dən sonra high qeyd olunmayıbsa,
        # köhnə DIB2-ni yeni DIB1 edib
        # cari candle-dan davam edirik.

        if old_high is None:

            print(
                f"{symbol}: DIB2 >10 şam, "
                f"amma HIGH yoxdur -> yeni DIB1"
            )

            start_new_dib1(
                state,
                candle,
                index
            )

            return


        # ----------------------------------------------------
        # YENİ ROLLOVER STRUCTURE
        # ----------------------------------------------------

        state["phase"] = TRACK_DIB2

        # Köhnə DIB2 -> yeni DIB1
        state["dib1"] = old_dib2
        state["dib1_time"] = old_dib2_time

        # Yeni HIGH1 = köhnə DIB2-dən sonra
        # görülən ən yüksək qiymət
        state["high1"] = old_high
        state["high1_time"] = old_high_time
        state["high1_index"] = old_high_index

        # DIB2-ni qıran yeni qiymət -> yeni DIB2
        state["dib2"] = candle["low"]
        state["dib2_time"] = candle["close_time"]
        state["dib2_index"] = index

        # Yeni DIB2-dən yeni high izləməsi
        state["dib2_high"] = None
        state["dib2_high_time"] = None
        state["dib2_high_index"] = None

        print(
            f"{symbol}: DIB2 ROLLOVER | "
            f"NEW DIB1={state['dib1']} | "
            f"NEW HIGH1={state['high1']} | "
            f"NEW DIB2={state['dib2']}"
        )

        return


    # --------------------------------------------------------
    # 3. DIB2 QIRILMAYIBSA
    #
    # Bu candle DIB2-dən yuxarıdadır.
    #
    # DIB2-dən sonrakı ən yüksək qiyməti izləyirik.
    # --------------------------------------------------------

    if candle["high"] > dib2:

        update_dib2_high(
            state,
            candle,
            index
        )


# ============================================================
# PROCESS CANDLE
# ============================================================

def process_candle(symbol, candle):

    # --------------------------------------------------------
    # STARTUP FILTER
    #
    # Bot başlamazdan əvvəl bağlanmış candle istifadə olunmur.
    # --------------------------------------------------------

    if candle["close_time"] <= START_TIME_MS:

        return


    if symbol not in states:

        states[symbol] = new_search_state()


    state = states[symbol]


    # --------------------------------------------------------
    # Eyni candle ikinci dəfə işlənməsin
    # --------------------------------------------------------

    if (
        state["last_close_time"] is not None
        and candle["close_time"] <= state["last_close_time"]
    ):

        return


    # --------------------------------------------------------
    # Index artır
    # --------------------------------------------------------

    state["index"] += 1

    index = state["index"]

    state["last_close_time"] = candle["close_time"]


    # --------------------------------------------------------
    # FIRST CANDLE
    #
    # İlk yeni candle = ilk DIB1 namizədi.
    # --------------------------------------------------------

    if state["dib1"] is None:

        start_new_dib1(
            state,
            candle,
            index
        )

        print(
            f"{symbol}: NEW DIB1 = "
            f"{candle['low']}"
        )

        return


    # --------------------------------------------------------
    # PHASE
    # --------------------------------------------------------

    if state["phase"] == SEARCH_DIB1:

        process_search_dib1(
            symbol,
            state,
            candle,
            index
        )

    elif state["phase"] == TRACK_DIB2:

        process_dib2(
            symbol,
            state,
            candle,
            index
        )


# ============================================================
# FETCH CANDLES IN PARALLEL
# ============================================================

def fetch_symbol_candle(symbol):

    return (
        symbol,
        get_latest_closed_candle(symbol)
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)
    print("15M DIB -> HIGH -> DIB2 BREAKOUT BOT")
    print("ALERT ONLY — NO AUTO ORDER")
    print("=" * 60)

    print(
        "BOT START:",
        now_baku().strftime("%Y-%m-%d %H:%M:%S")
    )

    print(
        "START_TIME_MS:",
        START_TIME_MS
    )

    top100 = []

    last_top_update = 0

    scan_number = 0

    while True:

        try:

            current_time = time.time()


            # ------------------------------------------------
            # TOP 100 UPDATE
            # ------------------------------------------------

            if (
                not top100
                or current_time - last_top_update
                >= TOP100_UPDATE_SECONDS
            ):

                new_top100 = get_top_100()

                if new_top100:

                    top100 = new_top100

                    last_top_update = current_time

                    print(
                        f"[{now_baku()}] "
                        f"TOP 100 updated | "
                        f"Symbols: {len(top100)}"
                    )


            if not top100:

                time.sleep(5)

                continue


            # ------------------------------------------------
            # FETCH CANDLES
            # ------------------------------------------------

            scan_number += 1

            results = []


            with ThreadPoolExecutor(
                max_workers=20
            ) as executor:

                futures = [
                    executor.submit(
                        fetch_symbol_candle,
                        symbol
                    )
                    for symbol in top100
                ]

                for future in as_completed(futures):

                    try:

                        symbol, candle = future.result()

                        if candle is not None:

                            results.append(
                                (
                                    symbol,
                                    candle
                                )
                            )

                    except Exception as e:

                        print(
                            "Worker error:",
                            e
                        )


            # ------------------------------------------------
            # PROCESS
            # ------------------------------------------------

            new_candles = 0

            for symbol, candle in results:

                before = states.get(symbol, {}).get(
                    "last_close_time"
                )

                process_candle(
                    symbol,
                    candle
                )

                after = states.get(symbol, {}).get(
                    "last_close_time"
                )

                if (
                    after is not None
                    and after != before
                ):

                    new_candles += 1


            print(
                f"[{now_baku().strftime('%Y-%m-%d %H:%M:%S')}] "
                f"HEARTBEAT | "
                f"Scan #{scan_number} | "
                f"Symbols: {len(top100)} | "
                f"New candles: {new_candles}"
            )


            time.sleep(
                SCAN_SECONDS
            )


        except KeyboardInterrupt:

            print("Bot stopped.")

            break


        except Exception as e:

            print(
                "MAIN LOOP ERROR:",
                e
            )

            time.sleep(5)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
