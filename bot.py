import os
import time
import requests

from datetime import datetime
from zoneinfo import ZoneInfo


# ============================================================
# CONFIG
# ============================================================

BINANCE_URL = "https://api.binance.com"

INTERVAL = "15m"

CANDLE_COUNT = 100
LEFT_HIGH_CANDLES = 10

MIN_BREAKOUT_PERCENT = 1.0

SCAN_SECONDS = 10
REQUEST_TIMEOUT = 10

AZ_TZ = ZoneInfo("Asia/Baku")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TOP_COINS = 100


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram environment variables missing")
        return

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    try:
        requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message
            },
            timeout=REQUEST_TIMEOUT
        )

    except Exception as e:
        print("Telegram error:", e)


# ============================================================
# TIME
# ============================================================

def local_time(ms):

    return datetime.fromtimestamp(
        ms / 1000,
        tz=AZ_TZ
    ).strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# TOP 100
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

        print("Ticker error:", e)
        return []

    result = []

    for item in data:

        symbol = item.get("symbol", "")

        if not symbol.endswith("USDT"):
            continue

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
            volume = float(item["quoteVolume"])
        except:
            continue

        result.append(
            (symbol, volume)
        )

    result.sort(
        key=lambda x: x[1],
        reverse=True
    )

    return [
        x[0]
        for x in result[:TOP_COINS]
    ]


# ============================================================
# CLOSED 15M CANDLE
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

        print(symbol, "candle error:", e)
        return None

    if not data:
        return None

    now_ms = int(time.time() * 1000)

    # Son bağlanmış şamı tap
    closed = None

    for k in data:

        if int(k[6]) <= now_ms:

            closed = {
                "open_time": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "close_time": int(k[6])
            }

    return closed


# ============================================================
# HIGH VALIDATION
# ============================================================

def is_valid_high(candles, index):

    if index < LEFT_HIGH_CANDLES:
        return False

    current_high = candles[index]["high"]

    left_candles = candles[
        index - LEFT_HIGH_CANDLES:index
    ]

    for candle in left_candles:

        if current_high <= candle["high"]:
            return False

    return True


# ============================================================
# SYMBOL STATE
# ============================================================

class SymbolState:

    def __init__(self):

        # Botun öz topladığı 15m şamlar
        self.candles = []

        # ====================================================
        # INITIAL DIB
        # ====================================================

        self.dib1 = None
        self.dib1_index = None
        self.dib1_time = None

        # ====================================================
        # HIGH 1
        # ====================================================

        self.high1 = None
        self.high1_index = None
        self.high1_time = None

        # ====================================================
        # DIB 2
        # ====================================================

        self.dib2 = None
        self.dib2_index = None
        self.dib2_time = None

        # ====================================================
        # STATE
        #
        # COLLECTING_100
        # FIND_HIGH1
        # WAIT_DIB2
        # WAIT_HIGH1_BREAKOUT
        # ====================================================

        self.state = "COLLECTING_100"

        self.last_candle_time = None


# ============================================================
# STATES
# ============================================================

states = {}


# ============================================================
# CREATE INITIAL DIB 1
# ============================================================

def create_initial_dib(state):

    if len(state.candles) < CANDLE_COUNT:
        return

    # Son 100 yeni bağlanmış şam
    window = state.candles[-CANDLE_COUNT:]

    lowest_index = min(
        range(len(window)),
        key=lambda i: window[i]["low"]
    )

    dib = window[lowest_index]

    state.dib1 = dib["low"]

    state.dib1_index = (
        len(state.candles)
        - CANDLE_COUNT
        + lowest_index
    )

    state.dib1_time = dib["close_time"]

    state.state = "FIND_HIGH1"

    print(
        "DIB 1:",
        state.dib1,
        local_time(state.dib1_time)
    )


# ============================================================
# FIND HIGH 1
# ============================================================

def find_high1(state):

    if state.dib1 is None:
        return

    # DIB 1-dən sonra gələn şamlar
    start = state.dib1_index + 1

    if start >= len(state.candles):
        return

    # Son şama qədər yoxla
    for i in range(
        max(start, LEFT_HIGH_CANDLES),
        len(state.candles)
    ):

        if not is_valid_high(
            state.candles,
            i
        ):
            continue

        high = state.candles[i]

        # HIGH DIB 1-dən sonra olmalıdır
        if i <= state.dib1_index:
            continue

        state.high1 = high["high"]

        state.high1_index = i

        state.high1_time = high["close_time"]

        state.state = "WAIT_DIB2"

        print(
            "HIGH 1:",
            state.high1,
            local_time(state.high1_time)
        )

        return


# ============================================================
# WAIT FOR DIB 1 BREAK
# ============================================================

def check_dib1_break(state, candle_index):

    if state.dib1 is None:
        return False

    candle = state.candles[candle_index]

    # DIB 1 aşağı qırılmalıdır
    if candle["low"] < state.dib1:

        state.state = "WAIT_HIGH1_BREAKOUT"

        # DIB 2 üçün ilk aşağı nöqtə
        state.dib2 = candle["low"]

        state.dib2_index = candle_index

        state.dib2_time = candle["close_time"]

        print(
            "DIB 2 başladı:",
            state.dib2,
            local_time(state.dib2_time)
        )

        return True

    return False


# ============================================================
# UPDATE DIB 2
# ============================================================

def update_dib2(state, candle_index):

    if state.dib2 is None:
        return

    candle = state.candles[candle_index]

    # DIB 2 struktur yaranana qədər daha aşağı low
    # gəlibsə, DIB 2 aşağı salınır.
    if candle["low"] < state.dib2:

        state.dib2 = candle["low"]

        state.dib2_index = candle_index

        state.dib2_time = candle["close_time"]

        print(
            "DIB 2 yeni low:",
            state.dib2,
            local_time(state.dib2_time)
        )


# ============================================================
# CHECK HIGH 1 BREAKOUT
# ============================================================

def check_high1_breakout(state, candle_index):

    if state.high1 is None:
        return None

    candle = state.candles[candle_index]

    close_price = candle["close"]

    breakout_percent = (
        (close_price - state.high1)
        / state.high1
    ) * 100

    # Şam HIGH 1-dən yuxarı bağlanmalıdır
    # və minimum +1% olmalıdır.
    if (
        close_price > state.high1
        and breakout_percent >= MIN_BREAKOUT_PERCENT
    ):

        return {
            "high": state.high1,
            "high_time": state.high1_time,

            "dib1": state.dib1,
            "dib1_time": state.dib1_time,

            "dib2": state.dib2,
            "dib2_time": state.dib2_time,

            "close": close_price,
            "close_time": candle["close_time"],

            "breakout_percent": breakout_percent
        }

    return None


# ============================================================
# RESET AFTER SIGNAL
# ============================================================

def reset_after_signal(state):

    # Siqnaldan sonra köhnə struktur saxlanılmır.
    #
    # Amma bot əvvəlki tarixə qayıtmır.
    # Yeni gələn 15m şamlarla yeni struktur qurur.

    state.candles = []

    state.dib1 = None
    state.dib1_index = None
    state.dib1_time = None

    state.high1 = None
    state.high1_index = None
    state.high1_time = None

    state.dib2 = None
    state.dib2_index = None
    state.dib2_time = None

    state.state = "COLLECTING_100"

    state.last_candle_time = None


# ============================================================
# PROCESS CANDLE
# ============================================================

def process_candle(symbol, candle):

    if symbol not in states:

        states[symbol] = SymbolState()

    state = states[symbol]

    # Eyni şamı ikinci dəfə işlətmə
    if (
        state.last_candle_time is not None
        and candle["close_time"]
        <= state.last_candle_time
    ):
        return None

    state.last_candle_time = candle["close_time"]

    # Yeni bağlanmış şamı əlavə et
    state.candles.append(candle)

    # ========================================================
    # 1. 100 ŞAM TOPLANIR
    # ========================================================

    if state.state == "COLLECTING_100":

        if len(state.candles) >= CANDLE_COUNT:

            create_initial_dib(state)

        return None

    current_index = len(state.candles) - 1

    # ========================================================
    # 2. HIGH 1 AXTAR
    # ========================================================

    if state.state == "FIND_HIGH1":

        find_high1(state)

        return None

    # ========================================================
    # 3. DIB 1 QIRILMASINI GÖZLƏ
    # ========================================================

    if state.state == "WAIT_DIB2":

        # ƏVVƏL breakout yoxlanılır.
        # Çünki DIB qırılmadan HIGH dəyişməz.

        if candle["low"] < state.dib1:

            check_dib1_break(
                state,
                current_index
            )

        return None

    # ========================================================
    # 4. DIB 2 FORMALAŞIR
    # ========================================================

    if state.state == "WAIT_HIGH1_BREAKOUT":

        # DIB 2-ni daha aşağı low-larla yenilə
        update_dib2(
            state,
            current_index
        )

        # ====================================================
        # HIGH 1 HƏLƏ DƏ ƏSAS HƏDƏFDİR
        # ====================================================

        signal = check_high1_breakout(
            state,
            current_index
        )

        if signal:

            return signal

    return None


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

def make_message(symbol, signal):

    return (
        "🚨 15M BREAKOUT SIGNAL 🚨\n\n"

        f"🪙 Coin: {symbol}\n\n"

        "📉 DIB 1\n"
        f"Price: {signal['dib1']:.8f}\n"
        f"Time: {local_time(signal['dib1_time'])}\n\n"

        "📈 HIGH 1\n"
        f"Price: {signal['high']:.8f}\n"
        f"Time: {local_time(signal['high_time'])}\n\n"

        "📉 DIB 2\n"
        f"Price: {signal['dib2']:.8f}\n"
        f"Time: {local_time(signal['dib2_time'])}\n\n"

        "🔥 BREAKOUT\n"
        f"Close: {signal['close']:.8f}\n"
        f"Time: {local_time(signal['close_time'])}\n"
        f"Breakout: +{signal['breakout_percent']:.2f}%\n\n"

        "✅ HIGH 1 broken\n"
        "✅ Breakout >= 1%\n"
        "⚠️ ALERT ONLY — NO AUTO ORDER"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("==============================================")
    print("BINANCE 15M DIB → HIGH → DIB → BREAKOUT BOT")
    print("==============================================")
    print()
    print("Bot started.")
    print("Only NEW 15m candles are collected.")
    print("Previous Binance candles are NOT loaded.")
    print("Cooldown: NONE")
    print()

    # ========================================================
    # IMPORTANT:
    #
    # Bot başladığı anda TOP 100 siyahını müəyyən edir.
    # Amma heç bir köhnə şam yükləmir.
    # ========================================================

    symbols = get_top_symbols()

    print(
        f"Tracking {len(symbols)} symbols."
    )

    while True:

        try:

            # Top 100 siyahısını periodik yenilə
            new_symbols = get_top_symbols()

            if new_symbols:

                symbols = new_symbols

            for symbol in symbols:

                try:

                    candle = get_latest_closed_candle(
                        symbol
                    )

                    if candle is None:
                        continue

                    signal = process_candle(
                        symbol,
                        candle
                    )

                    if signal:

                        print(
                            "\n🚨 SIGNAL",
                            symbol
                        )

                        message = make_message(
                            symbol,
                            signal
                        )

                        send_telegram(
                            message
                        )

                        # Siqnaldan sonra yeni struktur
                        reset_after_signal(
                            states[symbol]
                        )

                except Exception as e:

                    print(
                        symbol,
                        "processing error:",
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
