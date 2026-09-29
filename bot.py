import os
import time
import requests

from datetime import datetime
from zoneinfo import ZoneInfo


# ============================================================
# BINANCE 15M DIB 1 → HIGH 1 → DIB 2 → BREAKOUT
# 100 CANDLE STRUCTURE
#
# ALERT ONLY — NO AUTO ORDER
# ============================================================

BINANCE_URL = "https://api.binance.com"

INTERVAL = "15m"

TOP_COINS = 100

# Bütün struktur maksimum 100 şam daxilində keçərlidir
MAX_STRUCTURE_CANDLES = 100

# DIB-dən sonra HIGH təsdiqi üçün minimum 10 şam
MIN_HIGH_CANDLES = 10

# HIGH 1 breakout minimum
MIN_BREAKOUT_PERCENT = 1.0

SCAN_SECONDS = 30
REQUEST_TIMEOUT = 10

AZ_TZ = ZoneInfo("Asia/Baku")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# ============================================================
# BOT START TIME
# ============================================================

BOT_START_MS = int(time.time() * 1000)


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram variables missing")
        return

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    try:

        r = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message
            },
            timeout=REQUEST_TIMEOUT
        )

        if not r.ok:
            print("Telegram error:", r.text)

    except Exception as e:

        print("Telegram exception:", e)


# ============================================================
# TIME
# ============================================================

def local_time(timestamp_ms):

    return datetime.fromtimestamp(
        timestamp_ms / 1000,
        tz=AZ_TZ
    ).strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# TOP 100 USDT
# ============================================================

def get_top_symbols():

    try:

        r = requests.get(
            f"{BINANCE_URL}/api/v3/ticker/24hr",
            timeout=REQUEST_TIMEOUT
        )

        r.raise_for_status()

        data = r.json()

    except Exception as e:

        print("Top coins error:", e)
        return []

    coins = []

    for item in data:

        symbol = item.get("symbol", "")

        if not symbol.endswith("USDT"):
            continue

        if symbol.endswith(
            (
                "UPUSDT",
                "DOWNUSDT",
                "BULLUSDT",
                "BEARUSDT"
            )
        ):
            continue

        try:

            quote_volume = float(
                item["quoteVolume"]
            )

        except:

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
        for symbol, volume
        in coins[:TOP_COINS]
    ]


# ============================================================
# LATEST CLOSED 15M CANDLE
# ============================================================

def get_latest_closed_candle(symbol):

    try:

        r = requests.get(
            f"{BINANCE_URL}/api/v3/klines",
            params={
                "symbol": symbol,
                "interval": INTERVAL,
                "limit": 2
            },
            timeout=REQUEST_TIMEOUT
        )

        r.raise_for_status()

        data = r.json()

    except Exception as e:

        print(
            symbol,
            "candle error:",
            e
        )

        return None

    if not data:
        return None

    now_ms = int(
        time.time() * 1000
    )

    closed = None

    for k in data:

        close_time = int(k[6])

        if close_time <= now_ms:

            closed = {

                "open_time":
                    int(k[0]),

                "open":
                    float(k[1]),

                "high":
                    float(k[2]),

                "low":
                    float(k[3]),

                "close":
                    float(k[4]),

                "close_time":
                    close_time
            }

    if closed is None:
        return None

    # ========================================================
    # BOT BAŞLAMAMIŞDAN ƏVVƏLKİ ŞAMLAR İSTİFADƏ OLUNMUR
    # ========================================================

    if closed["close_time"] <= BOT_START_MS:
        return None

    return closed


# ============================================================
# STATE
# ============================================================

class State:

    def __init__(self):

        # ----------------------------------------------------
        # BÜTÜN ŞAMLAR
        # ----------------------------------------------------

        self.candles = []

        self.last_candle_time = None

        # ----------------------------------------------------
        # AKTİV DIB 1
        # ----------------------------------------------------

        self.dib1_candidate = None
        self.dib1_index = None
        self.dib1_time = None

        # DIB 1-dən sonra yaranan ən yüksək qiymət
        self.high1_candidate = None
        self.high1_index = None
        self.high1_time = None

        # ----------------------------------------------------
        # TƏSDİQLƏNMİŞ DIB 1 / HIGH 1
        # ----------------------------------------------------

        self.dib1 = None
        self.dib1_time_confirmed = None

        self.high1 = None
        self.high1_time = None

        # ----------------------------------------------------
        # DIB 2
        # ----------------------------------------------------

        self.dib2 = None
        self.dib2_index = None
        self.dib2_time = None

        # DIB 2-dən sonra yaranan ən yüksək qiymət
        self.high_after_dib2 = None
        self.high_after_dib2_index = None
        self.high_after_dib2_time = None

        # ----------------------------------------------------
        # PHASE
        # ----------------------------------------------------

        self.phase = "SEARCH_DIB1"


states = {}


# ============================================================
# YENİ DIB 1 İLƏ SIFIRDAN BAŞLA
# ============================================================

def start_new_dib1(
    state,
    candle,
    index
):

    # --------------------------------------------------------
    # Bütün köhnə struktur silinir
    # --------------------------------------------------------

    state.dib1_candidate = candle["low"]

    state.dib1_index = index

    state.dib1_time = candle["close_time"]

    state.high1_candidate = None
    state.high1_index = None
    state.high1_time = None

    state.dib1 = None
    state.dib1_time_confirmed = None

    state.high1 = None
    state.high1_time = None

    state.dib2 = None
    state.dib2_index = None
    state.dib2_time = None

    state.high_after_dib2 = None
    state.high_after_dib2_index = None
    state.high_after_dib2_time = None

    state.phase = "SEARCH_DIB1"


# ============================================================
# 100 ŞAM QAYDASI
# ============================================================

def structure_expired(
    state,
    current_index
):

    if state.dib1_index is None:
        return False

    # DIB1 şamı + sonrakı 99 şam = 100 şam
    #
    # current_index - dib1_index == 100
    #
    # olduqda artıq 101-ci şamdır.
    #

    return (
        current_index
        - state.dib1_index
        >= MAX_STRUCTURE_CANDLES
    )


# ============================================================
# HIGH 1 NAMİZƏDİNİ İZLƏ
# ============================================================

def update_high1_candidate(
    state,
    candle,
    index
):

    if state.high1_candidate is None:

        state.high1_candidate = candle["high"]

        state.high1_index = index

        state.high1_time = candle["close_time"]

        return

    if candle["high"] > state.high1_candidate:

        state.high1_candidate = candle["high"]

        state.high1_index = index

        state.high1_time = candle["close_time"]


# ============================================================
# SEARCH DIB 1
# ============================================================

def process_search_dib1(
    state,
    candle,
    index
):

    # --------------------------------------------------------
    # Əgər DIB1 yoxdur
    # --------------------------------------------------------

    if state.dib1_candidate is None:

        start_new_dib1(
            state,
            candle,
            index
        )

        return None

    # --------------------------------------------------------
    # DIB1-dən neçə şam keçib
    # --------------------------------------------------------

    bars_after_dib = (
        index
        - state.dib1_index
    )

    # ========================================================
    # DIB1 AŞAĞI QIRILDI
    # ========================================================

    if candle["low"] < state.dib1_candidate:

        # ----------------------------------------------------
        # 10 ŞAM TAMAMLANIBSA
        #
        # DIB1 + HIGH1 təsdiqlənir
        #
        # Cari aşağı düşmüş şam DIB2 olur.
        # ----------------------------------------------------

        if (
            bars_after_dib
            >= MIN_HIGH_CANDLES
            and state.high1_candidate is not None
        ):

            state.dib1 = (
                state.dib1_candidate
            )

            state.dib1_time_confirmed = (
                state.dib1_time
            )

            state.high1 = (
                state.high1_candidate
            )

            state.high1_time = (
                state.high1_time
            )

            # ------------------------------------------------
            # AŞAĞI QIRAN CƏRİ ŞAM
            # DIB2 KİMİ İZLƏNİR
            # ------------------------------------------------

            state.dib2 = candle["low"]

            state.dib2_index = index

            state.dib2_time = (
                candle["close_time"]
            )

            state.high_after_dib2 = None

            state.high_after_dib2_index = None

            state.high_after_dib2_time = None

            state.phase = "TRACK_DIB2"

            print()
            print(
                "================================"
            )
            print(
                "DIB 1 + HIGH 1 CONFIRMED"
            )
            print(
                "================================"
            )

            print(
                "DIB 1:",
                state.dib1,
                local_time(
                    state.dib1_time
                )
            )

            print(
                "HIGH 1:",
                state.high1,
                local_time(
                    state.high1_time
                )
            )

            print(
                "DIB 2 TRACK:",
                state.dib2,
                local_time(
                    state.dib2_time
                )
            )

            return None

        # ----------------------------------------------------
        # 10 ŞAM TAMAM OLMAYIB
        #
        # DIB1 təsdiqlənmir.
        #
        # Köhnə DIB1 silinir.
        # Cari aşağı qiymət yeni DIB1 olur.
        # ----------------------------------------------------

        start_new_dib1(
            state,
            candle,
            index
        )

        return None

    # ========================================================
    # DIB1 QIRILMAYIB
    #
    # Qiymət düşə bilər.
    # Sonra yenidən qalxa bilər.
    #
    # HIGH1 SİLİNMİR.
    #
    # Hər dəfə daha yüksək zirvə yaranarsa,
    # ən yüksək qiymət HIGH1 namizədi olur.
    # ========================================================

    update_high1_candidate(
        state,
        candle,
        index
    )

    return None


# ============================================================
# TRACK DIB 2
# ============================================================

def process_track_dib2(
    state,
    candle,
    index
):

    # ========================================================
    # 100 ŞAM QAYDASI
    # ========================================================

    if structure_expired(
        state,
        index
    ):

        print(
            "100 CANDLE LIMIT:"
            " STRUCTURE CANCELLED"
        )

        start_new_dib1(
            state,
            candle,
            index
        )

        return None

    if state.dib2 is None:

        state.dib2 = candle["low"]

        state.dib2_index = index

        state.dib2_time = (
            candle["close_time"]
        )

        return None

    # --------------------------------------------------------
    # DIB2-dən neçə şam keçib
    # --------------------------------------------------------

    dib2_bars = (
        index
        - state.dib2_index
    )

    # ========================================================
    # ƏVVƏL HIGH1 BREAKOUT YOXLANIR
    # ========================================================

    if state.high1 is not None:

        close_price = candle["close"]

        breakout_percent = (
            (
                close_price
                - state.high1
            )
            / state.high1
        ) * 100

        # ----------------------------------------------------
        # HIGH1 +1% YUXARI BAĞLANIŞLA QIRILDI
        # ----------------------------------------------------

        if (
            close_price > state.high1
            and
            breakout_percent
            >= MIN_BREAKOUT_PERCENT
        ):

            return {

                "type":
                    "SIGNAL",

                "dib1":
                    state.dib1,

                "dib1_time":
                    state.dib1_time,

                "high1":
                    state.high1,

                "high1_time":
                    state.high1_time,

                "dib2":
                    state.dib2,

                "dib2_time":
                    state.dib2_time,

                "close":
                    close_price,

                "close_time":
                    candle["close_time"],

                "breakout_percent":
                    breakout_percent
            }

    # ========================================================
    # DIB2 AŞAĞI QIRILDI
    # ========================================================

    if candle["low"] < state.dib2:

        # ====================================================
        # VARİANT 1
        #
        # DIB2-dən 10 VƏ YA DAHA ÇOX ŞAM KEÇİB.
        #
        # HIGH1 +1% qırılmayıb.
        #
        # İNDİ:
        #
        # Köhnə DIB2 -> YENİ DIB1
        #
        # DIB2-dən qalxan ən yüksək qiymət -> YENİ HIGH1
        #
        # Cari aşağı düşən qiymət -> YENİ DIB2
        # ====================================================

        if dib2_bars >= MIN_HIGH_CANDLES:

            new_dib1 = state.dib2

            new_dib1_time = (
                state.dib2_time
            )

            # DIB2-dən sonra yaranmış ən yüksək qiymət
            new_high1 = (
                state.high_after_dib2
            )

            new_high1_time = (
                state.high_after_dib2_time
            )

            # Təhlükəsizlik üçün
            # əgər ayrıca high yığılmayıbsa,
            # əvvəlki HIGH1 saxlanılır.
            if new_high1 is None:

                new_high1 = state.high1

                new_high1_time = (
                    state.high1_time
                )

            # ------------------------------------------------
            # KÖHNƏ DIB1 / HIGH1 UNUDULUR
            # ------------------------------------------------

            state.dib1 = new_dib1

            state.dib1_time = (
                new_dib1_time
            )

            state.high1 = new_high1

            state.high1_time = (
                new_high1_time
            )

            # ------------------------------------------------
            # CƏRİ AŞAĞI QIRAN ŞAM
            # YENİ DIB2
            # ------------------------------------------------

            state.dib2 = candle["low"]

            state.dib2_index = index

            state.dib2_time = (
                candle["close_time"]
            )

            state.high_after_dib2 = None

            state.high_after_dib2_index = None

            state.high_after_dib2_time = None

            state.phase = "TRACK_DIB2"

            print()
            print(
                "--------------------------------"
            )

            print(
                "OLD DIB2 -> NEW DIB1"
            )

            print(
                "NEW DIB1:",
                state.dib1,
                local_time(
                    state.dib1_time
                )
            )

            print(
                "NEW HIGH1:",
                state.high1,
                local_time(
                    state.high1_time
                )
            )

            print(
                "NEW DIB2:",
                state.dib2,
                local_time(
                    state.dib2_time
                )
            )

            print(
                "--------------------------------"
            )

            return None

        # ====================================================
        # VARİANT 2
        #
        # DIB2-dən yalnız 1-9 şam keçib.
        #
        # Qiymət DIB2-ni qırdı.
        #
        # BÜTÜN KÖHNƏ STRUKTUR SİLİNİR.
        #
        # Aşağı qıran cari şam yeni DIB1 olur.
        # ====================================================

        start_new_dib1(
            state,
            candle,
            index
        )

        return None

    # ========================================================
    # DIB2 HƏLƏ QIRILMAYIB
    #
    # Qiymət DIB2-dən qalxır.
    #
    # Arada düşə bilər.
    #
    # HIGH1 SİLİNMİR.
    #
    # DIB2-dən yaranan ən yüksək qiymət ayrıca izlənilir.
    # ========================================================

    if state.high_after_dib2 is None:

        state.high_after_dib2 = (
            candle["high"]
        )

        state.high_after_dib2_index = index

        state.high_after_dib2_time = (
            candle["close_time"]
        )

    elif (
        candle["high"]
        > state.high_after_dib2
    ):

        state.high_after_dib2 = (
            candle["high"]
        )

        state.high_after_dib2_index = index

        state.high_after_dib2_time = (
            candle["close_time"]
        )

    return None


# ============================================================
# PROCESS NEW CLOSED CANDLE
# ============================================================

def process_candle(
    symbol,
    candle
):

    if symbol not in states:

        states[symbol] = State()

    state = states[symbol]

    # --------------------------------------------------------
    # Eyni 15m şamı ikinci dəfə işlətmə
    # --------------------------------------------------------

    if (
        state.last_candle_time is not None
        and
        candle["close_time"]
        <= state.last_candle_time
    ):

        return None

    state.last_candle_time = (
        candle["close_time"]
    )

    state.candles.append(candle)

    index = (
        len(state.candles)
        - 1
    )

    # ========================================================
    # SEARCH DIB1
    # ========================================================

    if state.phase == "SEARCH_DIB1":

        # ----------------------------------------------------
        # 101-ci şam gəlibsə:
        #
        # köhnə DIB1 artıq etibarsızdır.
        #
        # Cari canlı şamdan sıfırdan başlayırıq.
        # ----------------------------------------------------

        if structure_expired(
            state,
            index
        ):

            start_new_dib1(
                state,
                candle,
                index
            )

            return None

        return process_search_dib1(
            state,
            candle,
            index
        )

    # ========================================================
    # TRACK DIB2
    # ========================================================

    if state.phase == "TRACK_DIB2":

        return process_track_dib2(
            state,
            candle,
            index
        )

    return None


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

def make_signal_message(
    symbol,
    signal
):

    return (

        "🚨 15M BREAKOUT SIGNAL 🚨\n\n"

        f"🪙 Coin: {symbol}\n\n"

        "📉 DIB 1\n"
        f"Price: {signal['dib1']:.8f}\n"
        f"Time: "
        f"{local_time(signal['dib1_time'])}\n\n"

        "📈 HIGH 1\n"
        f"Price: {signal['high1']:.8f}\n"
        f"Time: "
        f"{local_time(signal['high1_time'])}\n\n"

        "📉 DIB 2\n"
        f"Price: {signal['dib2']:.8f}\n"
        f"Time: "
        f"{local_time(signal['dib2_time'])}\n\n"

        "🔥 BREAKOUT\n"
        f"Close: {signal['close']:.8f}\n"
        f"Time: "
        f"{local_time(signal['close_time'])}\n"
        f"Breakout: "
        f"+{signal['breakout_percent']:.2f}%\n\n"

        "✅ HIGH 1 BROKEN\n"
        "✅ Breakout >= +1%\n"
        "⚠️ ALERT ONLY — NO AUTO ORDER"
    )


# ============================================================
# AFTER SIGNAL
# ============================================================

def reset_after_signal(state):

    state.dib1_candidate = None
    state.dib1_index = None
    state.dib1_time = None

    state.high1_candidate = None
    state.high1_index = None
    state.high1_time = None

    state.dib1 = None
    state.dib1_time_confirmed = None

    state.high1 = None
    state.high1_time = None

    state.dib2 = None
    state.dib2_index = None
    state.dib2_time = None

    state.high_after_dib2 = None
    state.high_after_dib2_index = None
    state.high_after_dib2_time = None

    state.phase = "SEARCH_DIB1"


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=============================================="
    )

    print(
        "BINANCE 15M DIB/HIGH SIGNAL BOT"
    )

    print(
        "=============================================="
    )

    print()

    print(
        "Timeframe: 15m"
    )

    print(
        "Top coins: 100"
    )

    print(
        "Structure window: 100 candles"
    )

    print(
        "Minimum DIB → HIGH candles: 10"
    )

    print(
        "Breakout minimum: +1%"
    )

    print(
        "Cooldown: NONE"
    )

    print(
        "Previous candles: NOT USED"
    )

    print(
        "Alert only: YES"
    )

    print(
        "Bot start:",
        local_time(BOT_START_MS)
    )

    print(
        "Scan interval:",
        SCAN_SECONDS,
        "s"
    )

    print()

    symbols = get_top_symbols()

    print(
        "Tracking:",
        len(symbols),
        "symbols"
    )

    while True:

        try:

            new_symbols = (
                get_top_symbols()
            )

            if new_symbols:

                symbols = new_symbols

            for symbol in symbols:

                try:

                    candle = (
                        get_latest_closed_candle(
                            symbol
                        )
                    )

                    if candle is None:
                        continue

                    signal = (
                        process_candle(
                            symbol,
                            candle
                        )
                    )

                    if signal:

                        print()
                        print(
                            "🚨 SIGNAL:",
                            symbol
                        )

                        message = (
                            make_signal_message(
                                symbol,
                                signal
                            )
                        )

                        send_telegram(
                            message
                        )

                        # Cooldown yoxdur.
                        # Struktur siqnaldan sonra sıfırlanır.

                        reset_after_signal(
                            states[symbol]
                        )

                except Exception as e:

                    print(
                        symbol,
                        "error:",
                        e
                    )

            time.sleep(
                SCAN_SECONDS
            )

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
