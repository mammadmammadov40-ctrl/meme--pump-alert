import os
import time
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# BINANCE MULTI-TIMEFRAME
#
# 5M + 15M + 1H
#
# DIB1 -> HIGH1 -> DIB2 -> +1% BREAKOUT
#
# ALERT ONLY
# NO AUTOMATIC ORDER
# ============================================================
#
# HƏR TIMEFRAME MÜSTƏQİL İŞLƏYİR:
#
# 5m  -> ayrıca state
# 15m -> ayrıca state
# 1h  -> ayrıca state
#
# ============================================================


# ============================================================
# CONFIG
# ============================================================

BINANCE_URL = "https://api.binance.com"

# 3 timeframe
TIMEFRAMES = ["5m", "15m", "1h"]

TOP_COINS = 100

SCAN_SECONDS = 10

TOP100_UPDATE_SECONDS = 60

# HIGH1-in solunda minimum 10 şam
LEFT_HIGH_CANDLES = 10

# DIB1 maksimum 100 şam yaşayır
MAX_DIB1_CANDLES = 100

# Breakout minimum +1%
MIN_BREAKOUT_PERCENT = 1.0

# DIB2:
#
# <= 10 şam -> fresh reset
# > 10 şam -> rollover
DIB2_LONG_RISE_CANDLES = 10


AZ_TZ = ZoneInfo("Asia/Baku")


TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN"
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID"
)


# ============================================================
# SESSION
# ============================================================

session = requests.Session()

# Botun başladığı vaxt
START_TIME_MS = int(time.time() * 1000)


# ============================================================
# PHASES
# ============================================================

SEARCH_DIB1 = "SEARCH_DIB1"

TRACK_DIB2 = "TRACK_DIB2"


# ============================================================
# STATE
# ============================================================
#
# states[symbol][timeframe]
#
# Məsələn:
#
# states["BTCUSDT"]["5m"]
# states["BTCUSDT"]["15m"]
# states["BTCUSDT"]["1h"]
#
# Hər timeframe tam müstəqildir.
# ============================================================

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
    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):

        print(
            "Telegram ENV dəyişənləri yoxdur."
        )

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

        print(
            "Telegram error:",
            e
        )


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

            symbol = item.get(
                "symbol",
                ""
            )


            if not symbol.endswith(
                "USDT"
            ):

                continue


            # Leveraged tokenləri çıxar
            if any(
                symbol.endswith(x)
                for x in [
                    "UPUSDT",
                    "DOWNUSDT",
                    "BULLUSDT",
                    "BEARUSDT"
                ]
            ):

                continue


            try:

                volume = float(
                    item.get(
                        "quoteVolume",
                        0
                    )
                )

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
            for symbol, volume
            in coins[:TOP_COINS]
        ]


    except Exception as e:

        print(
            "TOP 100 error:",
            e
        )

        return []


# ============================================================
# LATEST CLOSED CANDLE
# ============================================================

def get_latest_closed_candle(
    symbol,
    timeframe
):

    try:

        response = session.get(
            BINANCE_URL + "/api/v3/klines",
            params={
                "symbol": symbol,
                "interval": timeframe,
                "limit": 2
            },
            timeout=10
        )


        response.raise_for_status()

        data = response.json()


        if len(data) < 2:

            return None


        # Son candle hələ açıq ola bilər.
        #
        # data[-2] = son BAĞLANMIŞ candle

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

        print(
            symbol,
            timeframe,
            "candle error:",
            e
        )

        return None


# ============================================================
# INITIAL STATE
# ============================================================

def new_search_state():

    return {

        "phase": SEARCH_DIB1,


        # -------------------------
        # DIB1
        # -------------------------

        "dib1": None,

        "dib1_time": None,

        "dib1_index": None,


        # -------------------------
        # HIGH1
        # -------------------------

        "high1": None,

        "high1_time": None,

        "high1_index": None,


        # -------------------------
        # DIB2
        # -------------------------

        "dib2": None,

        "dib2_time": None,

        "dib2_index": None,


        # DIB2-dən sonra görülən
        # ən yüksək qiymət

        "dib2_high": None,

        "dib2_high_time": None,

        "dib2_high_index": None,


        # -------------------------
        # LAST CANDLE
        # -------------------------

        "last_close_time": None,


        # -------------------------
        # POST-START CANDLE INDEX
        # -------------------------

        "index": -1
    }


# ============================================================
# GET STATE
# ============================================================

def get_state(symbol, timeframe):

    if symbol not in states:

        states[symbol] = {}


    if timeframe not in states[symbol]:

        states[symbol][timeframe] = (
            new_search_state()
        )


    return states[symbol][timeframe]


# ============================================================
# START NEW DIB1
# ============================================================

def start_new_dib1(
    state,
    candle,
    index
):

    state["phase"] = SEARCH_DIB1


    # -------------------------
    # NEW DIB1
    # -------------------------

    state["dib1"] = candle["low"]

    state["dib1_time"] = candle["close_time"]

    state["dib1_index"] = index


    # -------------------------
    # OLD HIGH1 SILINIR
    # -------------------------

    state["high1"] = None

    state["high1_time"] = None

    state["high1_index"] = None


    # -------------------------
    # DIB2 SILINIR
    # -------------------------

    state["dib2"] = None

    state["dib2_time"] = None

    state["dib2_index"] = None

    state["dib2_high"] = None

    state["dib2_high_time"] = None

    state["dib2_high_index"] = None


# ============================================================
# UPDATE HIGH1
# ============================================================

def update_high1(
    state,
    candle,
    index
):

    high = candle["high"]


    if state["high1"] is None:

        state["high1"] = high

        state["high1_time"] = (
            candle["close_time"]
        )

        state["high1_index"] = index

        return


    # Yalnız daha yüksək high gələndə
    # HIGH1 dəyişir.

    if high > state["high1"]:

        state["high1"] = high

        state["high1_time"] = (
            candle["close_time"]
        )

        state["high1_index"] = index


# ============================================================
# VALID HIGH1
# ============================================================

def valid_high1(state):

    if state["high1"] is None:

        return False


    if state["high1_index"] is None:

        return False


    # HIGH1-in solunda minimum 10 candle

    if (
        state["high1_index"]
        < LEFT_HIGH_CANDLES
    ):

        return False


    return True


# ============================================================
# PROCESS SEARCH DIB1
# ============================================================

def process_search_dib1(
    symbol,
    timeframe,
    state,
    candle,
    index
):

    dib1 = state["dib1"]


    # ========================================================
    # DIB1 YOXDURSA
    # ========================================================

    if dib1 is None:

        start_new_dib1(
            state,
            candle,
            index
        )

        return


    # ========================================================
    # DIB1 AGE
    # ========================================================

    dib1_age = (
        index
        - state["dib1_index"]
    )


    # ========================================================
    # 100 CANDLE LIMIT
    # ========================================================
    #
    # DIB1 100 candle daxilində
    # təsdiqlənməlidir.
    #
    # 101-ci candle gəlibsə:
    #
    # köhnə DIB1 silinir
    # cari candle LOW = yeni DIB1
    #
    # ========================================================

    if dib1_age > MAX_DIB1_CANDLES:

        print(
            f"{symbol} {timeframe}: "
            f"DIB1 100 candle keçdi -> RESET"
        )


        start_new_dib1(
            state,
            candle,
            index
        )

        return


    # ========================================================
    # ƏVVƏL HIGH1 YENİLƏNİR
    # ========================================================
    #
    # ÇOX VACİB:
    #
    # DIB1-i qıran candle özü
    # daha yüksək high yaradıbsa,
    # o high da nəzərə alınır.
    #
    # ========================================================

    update_high1(
        state,
        candle,
        index
    )


    # ========================================================
    # DIB1 AŞAĞI QIRILDI?
    # ========================================================

    if candle["low"] < dib1:


        # ====================================================
        # 10 CANDLE TAMAM OLMAYIB
        # ====================================================

        if dib1_age < LEFT_HIGH_CANDLES:

            print(
                f"{symbol} {timeframe}: "
                f"DIB1 erkən qırıldı -> "
                f"NEW DIB1={candle['low']}"
            )


            start_new_dib1(
                state,
                candle,
                index
            )

            return


        # ====================================================
        # HIGH1 10 SOL CANDLE ŞƏRTİ
        # ====================================================

        if not valid_high1(state):

            print(
                f"{symbol} {timeframe}: "
                f"DIB1 qırıldı, HIGH1 "
                f"10 sol candle şərtini ödəmir"
            )


            start_new_dib1(
                state,
                candle,
                index
            )

            return


        # ====================================================
        # DIB1 + HIGH1 TƏSDİQLƏNDİ
        # ====================================================

        print(
            f"{symbol} {timeframe}: "
            f"DIB1 CONFIRMED | "
            f"DIB1={dib1} | "
            f"HIGH1={state['high1']}"
        )


        # ====================================================
        # DIB2 BAŞLAYIR
        # ====================================================

        state["phase"] = TRACK_DIB2


        state["dib2"] = candle["low"]

        state["dib2_time"] = (
            candle["close_time"]
        )

        state["dib2_index"] = index


        state["dib2_high"] = None

        state["dib2_high_time"] = None

        state["dib2_high_index"] = None


        return


# ============================================================
# UPDATE DIB2 HIGH
# ============================================================

def update_dib2_high(
    state,
    candle,
    index
):

    high = candle["high"]


    if state["dib2_high"] is None:

        state["dib2_high"] = high

        state["dib2_high_time"] = (
            candle["close_time"]
        )

        state["dib2_high_index"] = index

        return


    if high > state["dib2_high"]:

        state["dib2_high"] = high

        state["dib2_high_time"] = (
            candle["close_time"]
        )

        state["dib2_high_index"] = index


# ============================================================
# BREAKOUT PERCENT
# ============================================================

def breakout_percent(
    close,
    high1
):

    return (
        (close - high1)
        / high1
        * 100
    )


# ============================================================
# SEND BREAKOUT
# ============================================================

def send_breakout_signal(
    symbol,
    timeframe,
    state,
    candle,
    percentage
):

    message = f"""
🚨 {timeframe.upper()} BREAKOUT SIGNAL 🚨

🪙 Coin: {symbol}

⏱ Timeframe: {timeframe}

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
✅ CLOSE >= HIGH1 +1%

⚠️ ALERT ONLY
⚠️ NO AUTO ORDER
""".strip()


    send_telegram(message)

    print(
        message
    )


# ============================================================
# PROCESS DIB2
# ============================================================

def process_dib2(
    symbol,
    timeframe,
    state,
    candle,
    index
):

    dib2 = state["dib2"]

    high1 = state["high1"]


    # ========================================================
    # 1. ƏVVƏL BREAKOUT
    # ========================================================
    #
    # Əgər eyni candle həm DIB2-ni aşağı qırıb,
    # həm də HIGH1 +1% üstündə bağlanıbsa,
    # BREAKOUT prioritetdir.
    #
    # ========================================================

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
                timeframe,
                state,
                candle,
                percentage
            )


            # Siqnaldan sonra
            # həmin timeframe sıfırlanır.

            states[symbol][timeframe] = (
                new_search_state()
            )

            return


    # ========================================================
    # 2. DIB2 AŞAĞI QIRILDI?
    # ========================================================

    if candle["low"] < dib2:

        dib2_age = (
            index
            - state["dib2_index"]
        )


        # ====================================================
        # <= 10 CANDLE
        # ====================================================
        #
        # FRESH RESET
        #
        # QIRAN LOW = YENİ DIB1
        #
        # Köhnə HIGH1 silinir.
        #
        # ====================================================

        if (
            dib2_age
            <= DIB2_LONG_RISE_CANDLES
        ):

            print(
                f"{symbol} {timeframe}: "
                f"DIB2 qırıldı "
                f"({dib2_age} candle) -> "
                f"FRESH NEW DIB1"
            )


            start_new_dib1(
                state,
                candle,
                index
            )

            return


        # ====================================================
        # > 10 CANDLE
        # ====================================================
        #
        # ROLLOVER
        #
        # OLD DIB2 -> NEW DIB1
        #
        # DIB2-dən qalxan bütün hərəkətin
        # ƏN YÜKSƏK qiyməti -> NEW HIGH1
        #
        # QIRAN LOW -> NEW DIB2
        #
        # ====================================================

        old_dib2 = state["dib2"]

        old_dib2_time = (
            state["dib2_time"]
        )


        old_high = (
            state["dib2_high"]
        )

        old_high_time = (
            state["dib2_high_time"]
        )

        old_high_index = (
            state["dib2_high_index"]
        )


        # Əgər high yaranmayıbsa,
        # fresh structure.

        if old_high is None:

            print(
                f"{symbol} {timeframe}: "
                f"DIB2 >10 candle, "
                f"HIGH yoxdur -> NEW DIB1"
            )


            start_new_dib1(
                state,
                candle,
                index
            )

            return


        # ====================================================
        # ROLLOVER
        # ====================================================

        state["phase"] = TRACK_DIB2


        # ----------------------------------------------------
        # OLD DIB2 -> NEW DIB1
        # ----------------------------------------------------

        state["dib1"] = old_dib2

        state["dib1_time"] = old_dib2_time

        # Rollover zamanı DIB1-in yaşı
        # ayrıca saxlanılır.
        #
        # Yeni structure artıq təsdiqlənmiş
        # DIB1/HIGH1 əsasında davam edir.

        state["dib1_index"] = (
            state["dib2_index"]
        )


        # ----------------------------------------------------
        # OLD DIB2 RISE MAX HIGH -> NEW HIGH1
        # ----------------------------------------------------

        state["high1"] = old_high

        state["high1_time"] = old_high_time

        state["high1_index"] = old_high_index


        # ----------------------------------------------------
        # BREAKING LOW -> NEW DIB2
        # ----------------------------------------------------

        state["dib2"] = candle["low"]

        state["dib2_time"] = (
            candle["close_time"]
        )

        state["dib2_index"] = index


        # Yeni DIB2-dən sonrakı high
        # yenidən ayrıca izlənəcək.

        state["dib2_high"] = None

        state["dib2_high_time"] = None

        state["dib2_high_index"] = None


        print(
            f"{symbol} {timeframe}: "
            f"DIB2 ROLLOVER | "
            f"NEW DIB1={state['dib1']} | "
            f"NEW HIGH1={state['high1']} | "
            f"NEW DIB2={state['dib2']}"
        )


        return


    # ========================================================
    # 3. DIB2 QIRILMAYIB
    # ========================================================
    #
    # DIB2-dən sonrakı yüksəlişin maksimum high-ını
    # izləyirik.
    #
    # ========================================================

    update_dib2_high(
        state,
        candle,
        index
    )


# ============================================================
# PROCESS CANDLE
# ============================================================

def process_candle(
    symbol,
    timeframe,
    candle
):

    # ========================================================
    # STARTUP FILTER
    # ========================================================
    #
    # Bot başlamazdan əvvəl bağlanmış candle
    # istifadə edilmir.
    #
    # ========================================================

    if (
        candle["close_time"]
        <= START_TIME_MS
    ):

        return False


    state = get_state(
        symbol,
        timeframe
    )


    # ========================================================
    # EYNİ CANDLE İKİ DƏFƏ İŞLƏNMƏSİN
    # ========================================================

    if (
        state["last_close_time"]
        is not None
        and candle["close_time"]
        <= state["last_close_time"]
    ):

        return False


    # ========================================================
    # INDEX
    # ========================================================

    state["index"] += 1

    index = state["index"]


    state["last_close_time"] = (
        candle["close_time"]
    )


    # ========================================================
    # İLK CANDLE
    # ========================================================

    if state["dib1"] is None:

        start_new_dib1(
            state,
            candle,
            index
        )


        print(
            f"{symbol} {timeframe}: "
            f"NEW DIB1 = {candle['low']}"
        )


        return True


    # ========================================================
    # SEARCH DIB1
    # ========================================================

    if (
        state["phase"]
        == SEARCH_DIB1
    ):

        process_search_dib1(
            symbol,
            timeframe,
            state,
            candle,
            index
        )


    # ========================================================
    # TRACK DIB2
    # ========================================================

    elif (
        state["phase"]
        == TRACK_DIB2
    ):

        process_dib2(
            symbol,
            timeframe,
            state,
            candle,
            index
        )


    return True


# ============================================================
# FETCH ONE SYMBOL + TIMEFRAME
# ============================================================

def fetch_symbol_timeframe(
    symbol,
    timeframe
):

    candle = get_latest_closed_candle(
        symbol,
        timeframe
    )


    return (
        symbol,
        timeframe,
        candle
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)

    print(
        "BINANCE MULTI-TIMEFRAME "
        "DIB -> HIGH -> DIB2 BREAKOUT BOT"
    )

    print(
        "TIMEFRAMES:",
        ", ".join(TIMEFRAMES)
    )

    print(
        "ALERT ONLY — NO AUTO ORDER"
    )

    print("=" * 70)


    print(
        "BOT START:",
        now_baku().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
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


            # =================================================
            # TOP 100 UPDATE
            # =================================================

            if (
                not top100
                or (
                    current_time
                    - last_top_update
                    >= TOP100_UPDATE_SECONDS
                )
            ):

                new_top100 = get_top_100()


                if new_top100:

                    top100 = new_top100

                    last_top_update = (
                        current_time
                    )


                    print(
                        f"[{now_baku()}] "
                        f"TOP 100 updated | "
                        f"Symbols: {len(top100)}"
                    )


            if not top100:

                time.sleep(5)

                continue


            # =================================================
            # FETCH
            # =================================================
            #
            # 100 coin x 3 timeframe
            #
            # = maksimum 300 request
            #
            # ThreadPool ilə paralel gedir.
            #
            # =================================================

            scan_number += 1


            results = []


            jobs = []

            for symbol in top100:

                for timeframe in TIMEFRAMES:

                    jobs.append(
                        (
                            symbol,
                            timeframe
                        )
                    )


            with ThreadPoolExecutor(
                max_workers=30
            ) as executor:


                futures = [

                    executor.submit(
                        fetch_symbol_timeframe,
                        symbol,
                        timeframe
                    )

                    for symbol, timeframe
                    in jobs

                ]


                for future in as_completed(
                    futures
                ):

                    try:

                        (
                            symbol,
                            timeframe,
                            candle
                        ) = future.result()


                        if candle is not None:

                            results.append(
                                (
                                    symbol,
                                    timeframe,
                                    candle
                                )
                            )


                    except Exception as e:

                        print(
                            "Worker error:",
                            e
                        )


            # =================================================
            # PROCESS
            # =================================================

            new_candles = 0


            for (
                symbol,
                timeframe,
                candle
            ) in results:


                state = get_state(
                    symbol,
                    timeframe
                )


                before = (
                    state["last_close_time"]
                )


                process_candle(
                    symbol,
                    timeframe,
                    candle
                )


                after = (
                    state["last_close_time"]
                )


                if (
                    after is not None
                    and after != before
                ):

                    new_candles += 1


            # =================================================
            # HEARTBEAT
            # =================================================

            print(
                f"["
                f"{now_baku().strftime('%Y-%m-%d %H:%M:%S')}"
                f"] "
                f"HEARTBEAT | "
                f"Scan #{scan_number} | "
                f"Symbols: {len(top100)} | "
                f"Timeframes: "
                f"{','.join(TIMEFRAMES)} | "
                f"New candles: {new_candles}"
            )


            time.sleep(
                SCAN_SECONDS
            )


        except KeyboardInterrupt:

            print(
                "Bot stopped."
            )

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
