import os
import time
import requests

from datetime import datetime
from zoneinfo import ZoneInfo


# ============================================================
# BINANCE 15M DIB 1 → HIGH 1 → DIB 2 → HIGH 1 BREAKOUT
# ALERT ONLY — NO AUTO ORDER
# ============================================================

BINANCE_URL = "https://api.binance.com"

INTERVAL = "15m"

TOP_COINS = 100

# HIGH üçün solda minimum 10 şam
LEFT_HIGH_CANDLES = 10

# HIGH 1 breakout minimum
MIN_BREAKOUT_PERCENT = 1.0

SCAN_SECONDS = 10
REQUEST_TIMEOUT = 10

AZ_TZ = ZoneInfo("Asia/Baku")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


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

        # Leveraged tokens excluded
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
            (symbol, quote_volume)
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
# GET ONLY NEW CLOSED 15M CANDLE
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

    closed = None

    for k in data:

        close_time = int(k[6])

        if close_time <= now_ms:

            closed = {
                "open_time": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "close_time": close_time
            }

    return closed


# ============================================================
# SYMBOL STATE
# ============================================================

class State:

    def __init__(self):

        # ----------------------------------------------------
        # Bütün şamlar yalnız bot başladıqdan sonra toplanır.
        # ----------------------------------------------------

        self.candles = []

        self.last_candle_time = None

        # ----------------------------------------------------
        # DIB 1 CANDIDATE
        # ----------------------------------------------------

        self.dib1_candidate = None
        self.dib1_candidate_index = None
        self.dib1_candidate_time = None

        self.dib1_rising = False

        # ----------------------------------------------------
        # HIGH 1 CANDIDATE
        # ----------------------------------------------------

        self.high1_candidate = None
        self.high1_candidate_index = None
        self.high1_candidate_time = None

        # ----------------------------------------------------
        # CONFIRMED DIB 1 / HIGH 1
        # ----------------------------------------------------

        self.dib1 = None
        self.dib1_time = None

        self.high1 = None
        self.high1_time = None

        # ----------------------------------------------------
        # DIB 2 CANDIDATE
        # ----------------------------------------------------

        self.dib2_candidate = None
        self.dib2_candidate_index = None
        self.dib2_candidate_time = None

        self.dib2_rising = False

        # ----------------------------------------------------
        # STATE
        # ----------------------------------------------------

        self.state = "SEARCH_DIB1"


states = {}


# ============================================================
# START / RESET DIB 1 SEARCH
# ============================================================

def reset_dib1_search(state):

    state.dib1_candidate = None
    state.dib1_candidate_index = None
    state.dib1_candidate_time = None

    state.dib1_rising = False

    state.high1_candidate = None
    state.high1_candidate_index = None
    state.high1_candidate_time = None

    state.state = "SEARCH_DIB1"


# ============================================================
# SEARCH DIB 1
# ============================================================

def search_dib1(state, index):

    candle = state.candles[index]

    # --------------------------------------------------------
    # İlk aşağı qiyməti yadda saxla
    # --------------------------------------------------------

    if state.dib1_candidate is None:

        state.dib1_candidate = candle["low"]
        state.dib1_candidate_index = index
        state.dib1_candidate_time = candle["close_time"]

        return

    # --------------------------------------------------------
    # Hələ yüksəliş başlamayıbsa və daha aşağı qiymət
    # yaranırsa, DIB namizədi dəyişir.
    # --------------------------------------------------------

    if not state.dib1_rising:

        if candle["low"] < state.dib1_candidate:

            state.dib1_candidate = candle["low"]

            state.dib1_candidate_index = index

            state.dib1_candidate_time = (
                candle["close_time"]
            )

            return

        # ----------------------------------------------------
        # Qiymət DIB-dən yuxarı qalxmağa başlayıb
        # ----------------------------------------------------

        if candle["close"] > state.dib1_candidate:

            state.dib1_rising = True

            # İlk yüksəliş şamının high-ı
            # HIGH namizədi ola bilər.
            if index >= LEFT_HIGH_CANDLES:

                state.high1_candidate = candle["high"]

                state.high1_candidate_index = index

                state.high1_candidate_time = (
                    candle["close_time"]
                )

            state.state = "TRACK_HIGH1"

            return


# ============================================================
# TRACK HIGH 1
# ============================================================

def track_high1(state, index):

    candle = state.candles[index]

    # --------------------------------------------------------
    # Əgər DIB 1 namizədi qırılarsa, əvvəlki DIB və HIGH
    # etibarsızdır. Yeni DIB axtarılır.
    # --------------------------------------------------------

    if candle["low"] < state.dib1_candidate:

        reset_dib1_search(state)

        # Cari şam yeni DIB namizədi ola bilər
        search_dib1(state, index)

        return

    # --------------------------------------------------------
    # HIGH 1 namizədini yüksəlt
    # --------------------------------------------------------

    if (
        state.high1_candidate is None
        and index >= LEFT_HIGH_CANDLES
    ):

        state.high1_candidate = candle["high"]

        state.high1_candidate_index = index

        state.high1_candidate_time = (
            candle["close_time"]
        )

    elif (
        state.high1_candidate is not None
        and candle["high"] > state.high1_candidate
    ):

        # Yüksəliş davam edir.
        # Yeni ən yüksək qiymət HIGH namizədini yeniləyir.

        state.high1_candidate = candle["high"]

        state.high1_candidate_index = index

        state.high1_candidate_time = (
            candle["close_time"]
        )


# ============================================================
# CONFIRM DIB 1 + HIGH 1
# ============================================================

def check_dib1_confirmation(state, index):

    candle = state.candles[index]

    if state.dib1_candidate is None:
        return False

    if state.high1_candidate is None:
        return False

    # --------------------------------------------------------
    # Qiymət əvvəl yadda saxlanmış DIB-i qırmalıdır.
    # --------------------------------------------------------

    if candle["low"] < state.dib1_candidate:

        state.dib1 = state.dib1_candidate
        state.dib1_time = state.dib1_candidate_time

        state.high1 = state.high1_candidate
        state.high1_time = state.high1_candidate_time

        # ----------------------------------------------------
        # DIB 2 axtarışına keçirik
        # ----------------------------------------------------

        state.dib2_candidate = candle["low"]

        state.dib2_candidate_index = index

        state.dib2_candidate_time = (
            candle["close_time"]
        )

        state.dib2_rising = False

        state.state = "SEARCH_DIB2"

        print()
        print("================================")
        print("DIB 1 + HIGH 1 CONFIRMED")
        print("================================")
        print(
            "DIB 1:",
            state.dib1,
            local_time(state.dib1_time)
        )
        print(
            "HIGH 1:",
            state.high1,
            local_time(state.high1_time)
        )

        return True

    return False


# ============================================================
# SEARCH DIB 2
# ============================================================

def search_dib2(state, index):

    candle = state.candles[index]

    # --------------------------------------------------------
    # DIB 1 qırıldıqdan sonra qiymət aşağı gedir.
    # Hələ DIB 2 təsdiqlənmir.
    # --------------------------------------------------------

    if state.dib2_candidate is None:

        state.dib2_candidate = candle["low"]

        state.dib2_candidate_index = index

        state.dib2_candidate_time = (
            candle["close_time"]
        )

        return

    # --------------------------------------------------------
    # Hələ yuxarı dönüş başlamayıbsa,
    # daha aşağı qiyməti izləyirik.
    # --------------------------------------------------------

    if not state.dib2_rising:

        if candle["low"] < state.dib2_candidate:

            state.dib2_candidate = candle["low"]

            state.dib2_candidate_index = index

            state.dib2_candidate_time = (
                candle["close_time"]
            )

            return

        # ----------------------------------------------------
        # Qiymət ən aşağı nöqtədən yuxarı dönməyə başlayıb.
        # ----------------------------------------------------

        if candle["close"] > state.dib2_candidate:

            state.dib2_rising = True

            print(
                "DIB 2 candidate:",
                state.dib2_candidate,
                local_time(
                    state.dib2_candidate_time
                )
            )


# ============================================================
# CHECK HIGH 1 BREAKOUT
# ============================================================

def check_high1_breakout(state, index):

    candle = state.candles[index]

    if state.high1 is None:
        return None

    if state.dib2_candidate is None:
        return None

    # --------------------------------------------------------
    # DIB 2-dən yuxarı dönüş başlamalıdır.
    # --------------------------------------------------------

    if not state.dib2_rising:
        return None

    close_price = candle["close"]

    breakout_percent = (
        (
            close_price - state.high1
        )
        / state.high1
    ) * 100

    # --------------------------------------------------------
    # HIGH 1 qırılmalıdır və bağlanış minimum +1% olmalıdır.
    # --------------------------------------------------------

    if (
        close_price > state.high1
        and breakout_percent >= MIN_BREAKOUT_PERCENT
    ):

        return {
            "dib1": state.dib1,
            "dib1_time": state.dib1_time,

            "high1": state.high1,
            "high1_time": state.high1_time,

            "dib2": state.dib2_candidate,
            "dib2_time": state.dib2_candidate_time,

            "close": close_price,
            "close_time": candle["close_time"],

            "breakout_percent": breakout_percent
        }

    return None


# ============================================================
# PROCESS NEW CLOSED CANDLE
# ============================================================

def process_candle(symbol, candle):

    if symbol not in states:
        states[symbol] = State()

    state = states[symbol]

    # Eyni 15D şamı iki dəfə işlətmə
    if (
        state.last_candle_time is not None
        and candle["close_time"]
        <= state.last_candle_time
    ):
        return None

    state.last_candle_time = candle["close_time"]

    state.candles.append(candle)

    index = len(state.candles) - 1

    # ========================================================
    # DIB 1 AXTAR
    # ========================================================

    if state.state == "SEARCH_DIB1":

        search_dib1(
            state,
            index
        )

        return None

    # ========================================================
    # HIGH 1 İZLƏ
    # ========================================================

    if state.state == "TRACK_HIGH1":

        # Əvvəl DIB qırılması ilə təsdiqi yoxla
        if check_dib1_confirmation(
            state,
            index
        ):

            return None

        # DIB qırılmayıbsa HIGH namizədini izləməyə davam
        track_high1(
            state,
            index
        )

        return None

    # ========================================================
    # DIB 2 AXTAR
    # ========================================================

    if state.state == "SEARCH_DIB2":

        # ----------------------------------------------------
        # Əgər HIGH 1 bu şamda qırılıbsa,
        # DIB 2-nin artıq yuxarı dönüşlə namizəd olması
        # lazımdır.
        # ----------------------------------------------------

        search_dib2(
            state,
            index
        )

        signal = check_high1_breakout(
            state,
            index
        )

        if signal:
            return signal

        return None

    return None


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

def make_signal_message(symbol, signal):

    return (
        "🚨 15M BREAKOUT SIGNAL 🚨\n\n"

        f"🪙 Coin: {symbol}\n\n"

        "📉 DIB 1\n"
        f"Price: {signal['dib1']:.8f}\n"
        f"Time: {local_time(signal['dib1_time'])}\n\n"

        "📈 HIGH 1\n"
        f"Price: {signal['high1']:.8f}\n"
        f"Time: {local_time(signal['high1_time'])}\n\n"

        "📉 DIB 2\n"
        f"Price: {signal['dib2']:.8f}\n"
        f"Time: {local_time(signal['dib2_time'])}\n\n"

        "🔥 BREAKOUT\n"
        f"Close: {signal['close']:.8f}\n"
        f"Time: {local_time(signal['close_time'])}\n"
        f"Breakout: +{signal['breakout_percent']:.2f}%\n\n"

        "✅ HIGH 1 BROKEN\n"
        "✅ Breakout >= +1%\n"
        "⚠️ ALERT ONLY — NO AUTO ORDER"
    )


# ============================================================
# AFTER SIGNAL
# ============================================================

def reset_after_signal(state):

    # Siqnaldan sonra köhnə struktur saxlanılmır.
    # Yeni struktur yeni gələn şamlarla qurulur.

    state.dib1_candidate = None
    state.dib1_candidate_index = None
    state.dib1_candidate_time = None
    state.dib1_rising = False

    state.high1_candidate = None
    state.high1_candidate_index = None
    state.high1_candidate_time = None

    state.dib1 = None
    state.dib1_time = None

    state.high1 = None
    state.high1_time = None

    state.dib2_candidate = None
    state.dib2_candidate_index = None
    state.dib2_candidate_time = None
    state.dib2_rising = False

    state.state = "SEARCH_DIB1"


# ============================================================
# MAIN
# ============================================================

def main():

    print("==============================================")
    print("BINANCE 15M DIB/HIGH SIGNAL BOT")
    print("==============================================")
    print()
    print("Timeframe: 15m")
    print("Top coins: 100")
    print("Cooldown: NONE")
    print("Previous candles: NOT LOADED")
    print("Alert only: YES")
    print()

    # --------------------------------------------------------
    # Burada yalnız TOP 100 siyahısı alınır.
    #
    # KÖHNƏ ŞAMLAR YÜKLƏNMİR.
    # --------------------------------------------------------

    symbols = get_top_symbols()

    print(
        "Tracking:",
        len(symbols),
        "symbols"
    )

    while True:

        try:

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

                        print()
                        print("🚨 SIGNAL:", symbol)

                        message = make_signal_message(
                            symbol,
                            signal
                        )

                        send_telegram(
                            message
                        )

                        # Cooldown yoxdur.
                        # Sadəcə həmin struktur bağlanır
                        # və yeni struktur axtarılır.

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
