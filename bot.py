import os
import time
import requests

from datetime import datetime
from zoneinfo import ZoneInfo


# ============================================================
# BINANCE 15M DIB / HIGH / DIB2 / BREAKOUT
#
# ALERT ONLY — NO AUTO ORDER
#
# STRATEGY:
#
# DIB 1
#   ↓
# HIGH 1
#   ↓
# DIB 1 broken
#   ↓
# DIB 2 is ONLY TRACKED
#   ↓
# if DIB 2 goes lower:
#       old DIB1 + HIGH1 are forgotten
#       DIB2 becomes NEW DIB1
#       NEW HIGH1 starts from there
#   ↓
# if price rises and breaks HIGH1:
#       DIB2 becomes CONFIRMED
#       BREAKOUT SIGNAL
#
# MAX STRUCTURE AGE = 100 CLOSED 15M CANDLES
# ============================================================


BINANCE_URL = "https://api.binance.com"

INTERVAL = "15m"

TOP_COINS = 100

# HIGH üçün minimum solda 10 şam
LEFT_HIGH_CANDLES = 10

# HIGH1 breakout minimum
MIN_BREAKOUT_PERCENT = 1.0

# Bir struktur maksimum 100 şam
MAX_STRUCTURE_CANDLES = 100

# Scan
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

        # Leveraged tokens
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

        except Exception:
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
# GET LATEST CLOSED 15M CANDLE
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

    now_ms = int(time.time() * 1000)

    closed = None

    for k in data:

        close_time = int(k[6])

        # Şam bağlanmış olmalıdır
        if close_time <= now_ms:

            closed = {
                "open_time": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "close_time": close_time
            }

    if closed is None:
        return None

    # ========================================================
    # ÇOX VACİB:
    #
    # Bot başlamazdan əvvəl bağlanmış şam istifadə edilmir.
    # ========================================================

    if closed["close_time"] <= BOT_START_MS:
        return None

    return closed


# ============================================================
# STATE
# ============================================================

class State:

    def __init__(self):

        # Bot başladıqdan sonra gələn şamlar
        self.candles = []

        self.last_candle_time = None

        # ====================================================
        # STRUCTURE START
        #
        # DIB1 hansı şamda yaranıbsa,
        # 100 şam limiti həmin nöqtədən hesablanır.
        # ====================================================

        self.structure_start_index = None

        # ====================================================
        # DIB 1 CANDIDATE
        # ====================================================

        self.dib1_candidate = None
        self.dib1_candidate_index = None
        self.dib1_candidate_time = None

        self.dib1_rising = False

        # ====================================================
        # HIGH 1
        # ====================================================

        self.high1_candidate = None
        self.high1_candidate_index = None
        self.high1_candidate_time = None

        # ====================================================
        # CONFIRMED DIB1 / HIGH1
        # ====================================================

        self.dib1 = None
        self.dib1_time = None

        self.high1 = None
        self.high1_time = None

        # ====================================================
        # DIB2
        #
        # DIB2 heç vaxt əvvəlcədən təsdiqlənmir.
        # Yalnız izlənilir.
        # ====================================================

        self.dib2_candidate = None
        self.dib2_candidate_index = None
        self.dib2_candidate_time = None

        self.dib2_rising = False

        # ====================================================
        # STATE
        # ====================================================

        self.state = "SEARCH_DIB1"


states = {}


# ============================================================
# FULL RESET
# ============================================================

def full_reset(state):

    state.structure_start_index = None

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
# RESET STRUCTURE FROM A NEW DIB1
# ============================================================

def start_new_dib1(
    state,
    candle,
    index
):

    # Köhnə HIGH1 tamamilə silinir
    state.high1_candidate = None
    state.high1_candidate_index = None
    state.high1_candidate_time = None

    state.high1 = None
    state.high1_time = None

    # Yeni DIB1
    state.dib1_candidate = candle["low"]

    state.dib1_candidate_index = index

    state.dib1_candidate_time = (
        candle["close_time"]
    )

    state.dib1_rising = False

    # Bu DIB1-dən etibarən 100 şam
    state.structure_start_index = index

    # DIB2 artıq yoxdur
    state.dib2_candidate = None
    state.dib2_candidate_index = None
    state.dib2_candidate_time = None
    state.dib2_rising = False

    state.state = "SEARCH_DIB1"


# ============================================================
# 100 CANDLE CHECK
# ============================================================

def structure_expired(state, index):

    if state.structure_start_index is None:
        return False

    age = (
        index
        - state.structure_start_index
    )

    # DIB1 = 1
    #
    # 100-cü şam keçərlidir.
    # 101-ci şam artıq keçərli deyil.
    #
    # index fərqi:
    # 0 ... 99 = 100 şam
    # 100       = 101-ci şam
    #

    return age >= MAX_STRUCTURE_CANDLES


# ============================================================
# SEARCH DIB1
# ============================================================

def search_dib1(
    state,
    index
):

    candle = state.candles[index]

    # ========================================================
    # İlk aşağı nöqtəni yadda saxla
    # ========================================================

    if state.dib1_candidate is None:

        start_new_dib1(
            state,
            candle,
            index
        )

        return

    # ========================================================
    # Hələ yüksəliş başlamayıb
    #
    # Daha aşağı low gəlirsə DIB namizədi dəyişir.
    # ========================================================

    if not state.dib1_rising:

        if candle["low"] < state.dib1_candidate:

            state.dib1_candidate = candle["low"]

            state.dib1_candidate_index = index

            state.dib1_candidate_time = (
                candle["close_time"]
            )

            # Yeni aşağı nöqtə yeni strukturun başlanğıcıdır
            state.structure_start_index = index

            return

        # ====================================================
        # Qiymət DIB-dən yuxarı qalxmağa başlayır
        # ====================================================

        if candle["close"] > state.dib1_candidate:

            state.dib1_rising = True

            # HIGH yalnız solda 10 şam varsa izlənməyə başlayır
            if index >= LEFT_HIGH_CANDLES:

                state.high1_candidate = candle["high"]

                state.high1_candidate_index = index

                state.high1_candidate_time = (
                    candle["close_time"]
                )

            state.state = "TRACK_HIGH1"

            return


# ============================================================
# TRACK HIGH1
# ============================================================

def track_high1(
    state,
    index
):

    candle = state.candles[index]

    # ========================================================
    # ƏGƏR DIB1 YENİDƏN AŞAĞI QIRILIR
    #
    # Bu, yeni DIB2 prosesinin başlanğıcıdır.
    # ========================================================

    if candle["low"] < state.dib1_candidate:

        # DIB2 artıq izlənir
        state.dib2_candidate = candle["low"]

        state.dib2_candidate_index = index

        state.dib2_candidate_time = (
            candle["close_time"]
        )

        state.dib2_rising = False

        state.state = "SEARCH_DIB2"

        print(
            "DIB 1 broken -> DIB 2 tracking:",
            state.dib2_candidate,
            local_time(
                state.dib2_candidate_time
            )
        )

        return

    # ========================================================
    # HIGH1 ən yüksək qiymətlə yenilənir
    # ========================================================

    if index < LEFT_HIGH_CANDLES:
        return

    if state.high1_candidate is None:

        state.high1_candidate = candle["high"]

        state.high1_candidate_index = index

        state.high1_candidate_time = (
            candle["close_time"]
        )

        return

    if candle["high"] > state.high1_candidate:

        state.high1_candidate = candle["high"]

        state.high1_candidate_index = index

        state.high1_candidate_time = (
            candle["close_time"]
        )

        print(
            "NEW HIGH1:",
            state.high1_candidate,
            local_time(
                state.high1_candidate_time
            )
        )


# ============================================================
# CONFIRM INITIAL DIB1 + HIGH1
# ============================================================

def confirm_dib1_high1(
    state
):

    if state.dib1_candidate is None:
        return False

    if state.high1_candidate is None:
        return False

    state.dib1 = state.dib1_candidate

    state.dib1_time = state.dib1_candidate_time

    state.high1 = state.high1_candidate

    state.high1_time = state.high1_candidate_time

    print()
    print("====================================")
    print("DIB 1 + HIGH 1 CONFIRMED")
    print("====================================")
    print(
        "DIB1:",
        state.dib1,
        local_time(state.dib1_time)
    )
    print(
        "HIGH1:",
        state.high1,
        local_time(state.high1_time)
    )

    return True


# ============================================================
# SEARCH DIB2
# ============================================================

def search_dib2(
    state,
    index
):

    candle = state.candles[index]

    if state.dib2_candidate is None:

        state.dib2_candidate = candle["low"]

        state.dib2_candidate_index = index

        state.dib2_candidate_time = (
            candle["close_time"]
        )

        state.dib2_rising = False

        return None

    # ========================================================
    # ƏN VACİB QAYDA:
    #
    # Qiymət DIB2-dən də aşağı gedərsə:
    #
    # DIB2 köhnə deyil -> YENİ DIB2 olur.
    #
    # Amma burada hələ təsdiq yoxdur.
    # ========================================================

    if candle["low"] < state.dib2_candidate:

        state.dib2_candidate = candle["low"]

        state.dib2_candidate_index = index

        state.dib2_candidate_time = (
            candle["close_time"]
        )

        state.dib2_rising = False

        print(
            "DIB2 NEW LOW:",
            state.dib2_candidate,
            local_time(
                state.dib2_candidate_time
            )
        )

        # ====================================================
        # DİQQƏT:
        #
        # Bu yeni DIB2 əvvəlki DIB1-i aşağı qırdığı üçün
        # köhnə DIB1 və HIGH1 artıq etibarsızdır.
        #
        # Yeni DIB2 -> yeni DIB1 olacaq.
        # ====================================================

        old_dib2 = state.dib2_candidate
        old_time = state.dib2_candidate_time

        # Yeni DIB1
        state.dib1 = old_dib2
        state.dib1_time = old_time

        state.dib1_candidate = old_dib2
        state.dib1_candidate_index = (
            state.dib2_candidate_index
        )
        state.dib1_candidate_time = old_time

        state.dib1_rising = False

        # Köhnə HIGH1 tamamilə silinir
        state.high1 = None
        state.high1_time = None

        state.high1_candidate = None
        state.high1_candidate_index = None
        state.high1_candidate_time = None

        # Artıq bu DIB2 yeni DIB1-dir
        state.dib2_candidate = None
        state.dib2_candidate_index = None
        state.dib2_candidate_time = None
        state.dib2_rising = False

        # 100 şam yeni DIB1-dən başlayır
        state.structure_start_index = index

        state.state = "SEARCH_DIB1"

        print()
        print("====================================")
        print("OLD STRUCTURE FORGOTTEN")
        print("DIB2 -> NEW DIB1")
        print("HIGH1 -> DELETED")
        print("====================================")
        print(
            "NEW DIB1:",
            state.dib1,
            local_time(state.dib1_time)
        )

        return "NEW_DIB1"

    # ========================================================
    # DIB2-dən yuxarı dönüş
    #
    # Hələ təsdiq yoxdur.
    # Sadəcə DIB2 candidate yüksəlişə başlayıb.
    # ========================================================

    if not state.dib2_rising:

        if candle["close"] > state.dib2_candidate:

            state.dib2_rising = True

            print(
                "DIB2 RISING:",
                state.dib2_candidate,
                local_time(
                    state.dib2_candidate_time
                )
            )

    return None


# ============================================================
# UPDATE NEW HIGH1 FROM NEW DIB1
# ============================================================

def update_new_high1(
    state,
    index
):

    candle = state.candles[index]

    # DIB1-dən sonra yuxarı hərəkət
    if not state.dib1_rising:

        if candle["close"] > state.dib1_candidate:

            state.dib1_rising = True

            if index >= LEFT_HIGH_CANDLES:

                state.high1_candidate = candle["high"]

                state.high1_candidate_index = index

                state.high1_candidate_time = (
                    candle["close_time"]
                )

                print(
                    "NEW HIGH1 START:",
                    state.high1_candidate,
                    local_time(
                        state.high1_candidate_time
                    )
                )

        return

    # ========================================================
    # Ən yüksək qiyməti HIGH1 kimi saxla
    # ========================================================

    if index < LEFT_HIGH_CANDLES:
        return

    if state.high1_candidate is None:

        state.high1_candidate = candle["high"]

        state.high1_candidate_index = index

        state.high1_candidate_time = (
            candle["close_time"]
        )

        return

    if candle["high"] > state.high1_candidate:

        state.high1_candidate = candle["high"]

        state.high1_candidate_index = index

        state.high1_candidate_time = (
            candle["close_time"]
        )

        print(
            "NEW HIGH1:",
            state.high1_candidate,
            local_time(
                state.high1_candidate_time
            )
        )


# ============================================================
# CHECK BREAKOUT
# ============================================================

def check_breakout(
    state,
    candle
):

    if state.high1_candidate is None:
        return None

    if state.dib2_candidate is None:
        return None

    # DIB2-dən yuxarı dönüş olmalıdır
    if not state.dib2_rising:
        return None

    close_price = candle["close"]

    breakout_percent = (
        (
            close_price
            - state.high1_candidate
        )
        / state.high1_candidate
    ) * 100

    # ========================================================
    # HIGH1 qırılmalı
    # və close ən az +1% yuxarı olmalıdır.
    # ========================================================

    if (
        close_price > state.high1_candidate
        and breakout_percent >= MIN_BREAKOUT_PERCENT
    ):

        return {
            "dib1": state.dib1,
            "dib1_time": state.dib1_time,

            "high1": state.high1_candidate,
            "high1_time": (
                state.high1_candidate_time
            ),

            # Burada DIB2 artıq təsdiqlənir
            "dib2": state.dib2_candidate,
            "dib2_time": (
                state.dib2_candidate_time
            ),

            "close": close_price,
            "close_time": candle["close_time"],

            "breakout_percent": breakout_percent
        }

    return None


# ============================================================
# PROCESS CANDLE
# ============================================================

def process_candle(
    symbol,
    candle
):

    if symbol not in states:
        states[symbol] = State()

    state = states[symbol]

    # Eyni şamı iki dəfə işlətmə
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
    # 100 ŞAM LIMITI
    # ========================================================

    if structure_expired(
        state,
        index
    ):

        print()
        print(
            symbol,
            "STRUCTURE EXPIRED -> FULL RESET"
        )

        # Cari şam artıq köhnə struktur üçün istifadə edilmir.
        # Bu şamdan sıfırdan yeni struktur başlayır.
        full_reset(state)

        # Cari şam yeni DIB1 üçün başlanğıc olur
        start_new_dib1(
            state,
            candle,
            index
        )

        return None

    # ========================================================
    # SEARCH DIB1
    # ========================================================

    if state.state == "SEARCH_DIB1":

        search_dib1(
            state,
            index
        )

        # Yeni DIB1-dən yuxarı qalxış başlasa
        # HIGH1 izlənəcək.
        if (
            state.dib1_rising
            and state.high1_candidate is not None
        ):

            state.state = "TRACK_HIGH1"

        return None

    # ========================================================
    # TRACK HIGH1
    # ========================================================

    if state.state == "TRACK_HIGH1":

        # DIB1-in aşağı qırılması
        if (
            candle["low"]
            < state.dib1_candidate
        ):

            # Əgər ilk dəfə DIB1 qırılırsa,
            # DIB1 + HIGH1 təsdiqlənir.
            if (
                state.dib1 is None
                and state.high1_candidate is not None
            ):

                confirm_dib1_high1(
                    state
                )

            # Sonra DIB2 izlənməyə başlayır
            state.dib2_candidate = candle["low"]

            state.dib2_candidate_index = index

            state.dib2_candidate_time = (
                candle["close_time"]
            )

            state.dib2_rising = False

            state.state = "SEARCH_DIB2"

            print(
                "DIB2 TRACKING START:",
                state.dib2_candidate,
                local_time(
                    state.dib2_candidate_time
                )
            )

            return None

        # HIGH1 ən yüksək qiymətlə yenilənir
        update_new_high1(
            state,
            index
        )

        return None

    # ========================================================
    # SEARCH DIB2
    # ========================================================

    if state.state == "SEARCH_DIB2":

        result = search_dib2(
            state,
            index
        )

        # DIB2 aşağı qırıldı və yeni DIB1 oldu
        if result == "NEW_DIB1":

            # Cari şam artıq yeni DIB1-dir.
            # Bu şamdan sonrakı şamlarda yüksəliş izlənəcək.
            return None

        # ====================================================
        # DIB2-dən yüksəliş davam edir.
        # Bu yüksəlişin ən yüksək qiyməti HIGH1 olur.
        #
        # Əgər bu yeni struktur üçündürsə,
        # HIGH1 izlənir.
        # ====================================================

        if state.dib1 is not None:

            update_new_high1(
                state,
                index
            )

        # ====================================================
        # HIGH1 breakout
        #
        # DIB2 burada yalnız indi təsdiqlənir.
        # ====================================================

        signal = check_breakout(
            state,
            candle
        )

        if signal:

            return signal

        return None

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
        f"Time: {local_time(signal['dib1_time'])}\n\n"

        "📈 HIGH 1\n"
        f"Price: {signal['high1']:.8f}\n"
        f"Time: {local_time(signal['high1_time'])}\n\n"

        "📉 DIB 2 — CONFIRMED\n"
        f"Price: {signal['dib2']:.8f}\n"
        f"Time: {local_time(signal['dib2_time'])}\n\n"

        "🔥 BREAKOUT\n"
        f"Close: {signal['close']:.8f}\n"
        f"Time: {local_time(signal['close_time'])}\n"
        f"Breakout: +{signal['breakout_percent']:.2f}%\n\n"

        "✅ HIGH 1 BROKEN\n"
        "✅ DIB 2 CONFIRMED\n"
        "✅ Breakout >= +1%\n"
        "⚠️ ALERT ONLY — NO AUTO ORDER"
    )


# ============================================================
# AFTER SIGNAL
# ============================================================

def reset_after_signal(
    state
):

    # ========================================================
    # Siqnaldan sonra köhnə struktur saxlanılmır.
    #
    # Amma candle history silinmir.
    # Çünki bunlar bot başladıqdan sonra gələn şamlardır.
    #
    # Yeni struktur gələcək şamlarla qurulacaq.
    # ========================================================

    state.structure_start_index = None

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

    print()
    print("==============================================")
    print("BINANCE 15M DIB / HIGH SIGNAL BOT")
    print("==============================================")
    print()

    print("Timeframe:", INTERVAL)
    print("Top coins:", TOP_COINS)
    print("High left candles:", LEFT_HIGH_CANDLES)
    print(
        "Breakout minimum:",
        f"{MIN_BREAKOUT_PERCENT}%"
    )
    print(
        "Maximum structure:",
        MAX_STRUCTURE_CANDLES,
        "candles"
    )
    print("Cooldown: NONE")
    print("Previous candles: NOT LOADED")
    print("Alert only: YES")
    print(
        "Bot start:",
        local_time(BOT_START_MS)
    )
    print(
        "Scan interval:",
        SCAN_SECONDS,
        "seconds"
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

                        # Cooldown yoxdur
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
