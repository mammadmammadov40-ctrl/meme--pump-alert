import os
import time
import requests

from datetime import datetime
from zoneinfo import ZoneInfo
from collections import deque


# ============================================================
# BINANCE 15M DIB 1 → HIGH 1 → DIB 2 → HIGH 1 BREAKOUT
# ALERT ONLY — NO AUTO ORDER
# ============================================================

BINANCE_URL = "https://api.binance.com"

INTERVAL = "15m"

TOP_COINS = 100

# Struktur üçün son 100 bağlanmış 15M şam
STRUCTURE_CANDLES = 100

# HIGH üçün solda minimum 10 şam
LEFT_HIGH_CANDLES = 10

# HIGH 1 üzərində minimum breakout
MIN_BREAKOUT_PERCENT = 1.0

# Scan intervalı
SCAN_SECONDS = 30

REQUEST_TIMEOUT = 10

AZ_TZ = ZoneInfo("Asia/Baku")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# ============================================================
# BOT START TIME
# ============================================================

BOT_START_TIME_MS = int(time.time() * 1000)


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

        else:
            print("📨 TELEGRAM ALERT SENT")

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
# GET LAST 100 CLOSED CANDLES
# ============================================================

def get_history(symbol):

    try:

        r = requests.get(
            f"{BINANCE_URL}/api/v3/klines",
            params={
                "symbol": symbol,
                "interval": INTERVAL,
                "limit": STRUCTURE_CANDLES + 1
            },
            timeout=REQUEST_TIMEOUT
        )

        r.raise_for_status()

        data = r.json()

    except Exception as e:

        print(
            f"{symbol} history error:",
            e
        )

        return []

    now_ms = int(
        time.time() * 1000
    )

    candles = []

    for k in data:

        close_time = int(k[6])

        # Yalnız bağlanmış şam
        if close_time > now_ms:
            continue

        candles.append(
            {
                "open_time": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "close_time": close_time
            }
        )

    # Son 100 bağlanmış şam
    return candles[-STRUCTURE_CANDLES:]


# ============================================================
# GET LATEST CLOSED CANDLE
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
            f"{symbol} candle error:",
            e
        )

        return None

    now_ms = int(
        time.time() * 1000
    )

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
# STATE
# ============================================================

class State:

    def __init__(self):

        # Son 100 şam
        self.candles = deque(
            maxlen=STRUCTURE_CANDLES
        )

        self.last_candle_time = None

        # Neçə şam işlənib
        self.bars_seen = 0

        # ----------------------------------------------------
        # DIB 1
        # ----------------------------------------------------

        self.dib1_candidate = None
        self.dib1_candidate_time = None

        self.dib1_rising = False

        # ----------------------------------------------------
        # HIGH 1
        # ----------------------------------------------------

        self.high1_candidate = None
        self.high1_candidate_time = None
        self.high1_candidate_bar = None

        # ----------------------------------------------------
        # CONFIRMED DIB 1 / HIGH 1
        # ----------------------------------------------------

        self.dib1 = None
        self.dib1_time = None

        self.high1 = None
        self.high1_time = None

        # ----------------------------------------------------
        # DIB 2
        # ----------------------------------------------------

        self.dib2_candidate = None
        self.dib2_candidate_time = None

        self.dib2_rising = False

        # ----------------------------------------------------
        # STATE
        # ----------------------------------------------------

        self.phase = "SEARCH_DIB1"


states = {}


# ============================================================
# RESET TO DIB 1 SEARCH
# ============================================================

def reset_to_dib1(state):

    state.dib1_candidate = None
    state.dib1_candidate_time = None
    state.dib1_rising = False

    state.high1_candidate = None
    state.high1_candidate_time = None
    state.high1_candidate_bar = None

    state.dib1 = None
    state.dib1_time = None

    state.high1 = None
    state.high1_time = None

    state.dib2_candidate = None
    state.dib2_candidate_time = None
    state.dib2_rising = False

    state.phase = "SEARCH_DIB1"


# ============================================================
# RESET AFTER SIGNAL
# ============================================================

def reset_after_signal(state):

    reset_to_dib1(state)


# ============================================================
# PROCESS SEARCH DIB 1
# ============================================================

def process_search_dib1(
    state,
    candle,
    symbol,
    log_enabled=True
):

    # --------------------------------------------------------
    # İlk LOW
    # --------------------------------------------------------

    if state.dib1_candidate is None:

        state.dib1_candidate = candle["low"]

        state.dib1_candidate_time = (
            candle["close_time"]
        )

        if log_enabled:

            print(
                f"📉 {symbol} | "
                f"DIB candidate: "
                f"{candle['low']:.8f}"
            )

        return

    # --------------------------------------------------------
    # Hələ yüksəliş başlamayıb
    # --------------------------------------------------------

    if not state.dib1_rising:

        # Daha aşağı LOW gəlibsə,
        # əvvəlki DIB namizədi tamamilə silinir.
        if candle["low"] < state.dib1_candidate:

            state.dib1_candidate = candle["low"]

            state.dib1_candidate_time = (
                candle["close_time"]
            )

            if log_enabled:

                print(
                    f"📉 {symbol} | "
                    f"DIB NEW LOW: "
                    f"{candle['low']:.8f}"
                )

            return

        # ----------------------------------------------------
        # Qiymət DIB-dən yuxarı qalxmağa başlayıb
        # ----------------------------------------------------

        if candle["close"] > state.dib1_candidate:

            state.dib1_rising = True

            if log_enabled:

                print(
                    f"📈 {symbol} | "
                    f"RISE FROM DIB: "
                    f"{state.dib1_candidate:.8f}"
                )

            # HIGH namizədi üçün 10 sol şam
            if state.bars_seen >= LEFT_HIGH_CANDLES:

                state.high1_candidate = candle["high"]

                state.high1_candidate_time = (
                    candle["close_time"]
                )

                state.high1_candidate_bar = (
                    state.bars_seen
                )

            state.phase = "TRACK_HIGH1"


# ============================================================
# PROCESS HIGH 1
# ============================================================

def process_track_high1(
    state,
    candle,
    symbol,
    log_enabled=True
):

    # --------------------------------------------------------
    # ÇOX VACİB:
    #
    # Əvvəl DIB-in qırılıb-qırılmadığını yoxlayırıq.
    #
    # Əgər qırılıbsa, bu şam HIGH 1-ə əlavə edilmir.
    # Çünki HIGH 1 DIB-dən sonrakı yüksəlişdə yaranmış
    # ən yüksək qiymət olmalıdır.
    # --------------------------------------------------------

    if candle["low"] < state.dib1_candidate:

        # ----------------------------------------------------
        # DIB 1 + HIGH 1 TƏSDİQ
        # ----------------------------------------------------

        if state.high1_candidate is None:

            # HIGH üçün 10 sol şam şərti ödənməyib
            # Bu struktur keçərli deyil.
            reset_to_dib1(state)

            process_search_dib1(
                state,
                candle,
                symbol,
                log_enabled
            )

            return False

        state.dib1 = state.dib1_candidate

        state.dib1_time = (
            state.dib1_candidate_time
        )

        state.high1 = state.high1_candidate

        state.high1_time = (
            state.high1_candidate_time
        )

        # ----------------------------------------------------
        # DIB 2 başlayır
        #
        # DIB 1-i qıran şamın LOW-u ilk DIB 2 minimumudur.
        # ----------------------------------------------------

        state.dib2_candidate = candle["low"]

        state.dib2_candidate_time = (
            candle["close_time"]
        )

        state.dib2_rising = False

        state.phase = "SEARCH_DIB2"

        if log_enabled:

            print()
            print(
                "=========================================="
            )

            print(
                f"✅ {symbol} | "
                f"DIB 1 + HIGH 1 CONFIRMED"
            )

            print(
                f"DIB 1: "
                f"{state.dib1:.8f} | "
                f"{local_time(state.dib1_time)}"
            )

            print(
                f"HIGH 1: "
                f"{state.high1:.8f} | "
                f"{local_time(state.high1_time)}"
            )

            print(
                f"DIB 2 tracking from: "
                f"{state.dib2_candidate:.8f}"
            )

            print(
                "=========================================="
            )

        return True

    # --------------------------------------------------------
    # DIB qırılmayıbsa HIGH-ı izləyirik
    # --------------------------------------------------------

    if (
        state.bars_seen >= LEFT_HIGH_CANDLES
    ):

        if (
            state.high1_candidate is None
            or candle["high"]
            > state.high1_candidate
        ):

            state.high1_candidate = candle["high"]

            state.high1_candidate_time = (
                candle["close_time"]
            )

            state.high1_candidate_bar = (
                state.bars_seen
            )

            if log_enabled:

                print(
                    f"📈 {symbol} | "
                    f"HIGH 1 NEW HIGH: "
                    f"{candle['high']:.8f}"
                )

    return False


# ============================================================
# PROCESS DIB 2
# ============================================================

def process_dib2(
    state,
    candle,
    symbol,
    log_enabled=True
):

    if state.dib2_candidate is None:

        state.dib2_candidate = candle["low"]

        state.dib2_candidate_time = (
            candle["close_time"]
        )

        return

    # --------------------------------------------------------
    # Hələ yüksəliş başlamayıb
    # --------------------------------------------------------

    if not state.dib2_rising:

        # Daha aşağı LOW gəlibsə
        # əvvəlki DIB 2 namizədi silinir.
        if candle["low"] < state.dib2_candidate:

            state.dib2_candidate = candle["low"]

            state.dib2_candidate_time = (
                candle["close_time"]
            )

            if log_enabled:

                print(
                    f"📉 {symbol} | "
                    f"DIB 2 NEW LOW: "
                    f"{candle['low']:.8f} | "
                    f"{local_time(candle['close_time'])}"
                )

            return

        # ----------------------------------------------------
        # Ən aşağı nöqtədən yuxarı dönüş
        # ----------------------------------------------------

        if candle["close"] > state.dib2_candidate:

            state.dib2_rising = True

            if log_enabled:

                print(
                    f"📈 {symbol} | "
                    f"DIB 2 RISE STARTED | "
                    f"DIB 2: "
                    f"{state.dib2_candidate:.8f}"
                )

    else:

        # ----------------------------------------------------
        # DIB 2-dən sonra yenidən daha aşağı LOW yaranarsa,
        # köhnə DIB 2 ləğv edilir.
        # ----------------------------------------------------

        if candle["low"] < state.dib2_candidate:

            state.dib2_candidate = candle["low"]

            state.dib2_candidate_time = (
                candle["close_time"]
            )

            state.dib2_rising = False

            if log_enabled:

                print(
                    f"📉 {symbol} | "
                    f"DIB 2 RESET → NEW LOW: "
                    f"{candle['low']:.8f}"
                )


# ============================================================
# CHECK BREAKOUT
# ============================================================

def check_breakout(
    state,
    candle,
    symbol,
    log_enabled=True
):

    if state.high1 is None:
        return None

    if state.dib2_candidate is None:
        return None

    # DIB 2-dən yüksəliş başlamalıdır
    if not state.dib2_rising:
        return None

    close_price = candle["close"]

    breakout_percent = (
        (
            close_price
            - state.high1
        )
        / state.high1
    ) * 100

    # --------------------------------------------------------
    # HIGH 1 qırılmalı və CLOSE minimum +1% yuxarı olmalıdır
    # --------------------------------------------------------

    if (
        close_price > state.high1
        and breakout_percent >= MIN_BREAKOUT_PERCENT
    ):

        if log_enabled:

            print()
            print(
                "🚨🚨🚨 BREAKOUT 🚨🚨🚨"
            )

            print(
                f"{symbol} | "
                f"HIGH 1: "
                f"{state.high1:.8f}"
            )

            print(
                f"{symbol} | "
                f"CLOSE: "
                f"{close_price:.8f}"
            )

            print(
                f"{symbol} | "
                f"BREAKOUT: "
                f"+{breakout_percent:.2f}%"
            )

        return {

            "dib1":
                state.dib1,

            "dib1_time":
                state.dib1_time,

            "high1":
                state.high1,

            "high1_time":
                state.high1_time,

            "dib2":
                state.dib2_candidate,

            "dib2_time":
                state.dib2_candidate_time,

            "close":
                close_price,

            "close_time":
                candle["close_time"],

            "breakout_percent":
                breakout_percent
        }

    return None


# ============================================================
# REPLAY HISTORY
# ============================================================
#
# Məqsəd:
#
# Son 100 şam struktur konteksti üçün istifadə olunur.
#
# Amma burada TELEGRAM SIQNALI göndərilmir.
#
# Beləliklə bot keçmişdə baş vermiş breakout-a görə
# indi siqnal göndərmir.
#
# ============================================================

def build_initial_state(
    symbol,
    history
):

    state = State()

    for candle in history:

        state.bars_seen += 1

        state.candles.append(
            candle
        )

        if state.phase == "SEARCH_DIB1":

            process_search_dib1(
                state,
                candle,
                symbol,
                log_enabled=False
            )

        elif state.phase == "TRACK_HIGH1":

            process_track_high1(
                state,
                candle,
                symbol,
                log_enabled=False
            )

        elif state.phase == "SEARCH_DIB2":

            process_dib2(
                state,
                candle,
                symbol,
                log_enabled=False
            )

            # Tarixi breakout yoxlanılmır.
            # Yalnız struktur qurulur.

    if history:

        state.last_candle_time = (
            history[-1]["close_time"]
        )

    return state


# ============================================================
# PROCESS ONE NEW CANDLE
# ============================================================

def process_new_candle(
    symbol,
    candle
):

    if symbol not in states:

        return None

    state = states[symbol]

    # Eyni şamı iki dəfə işlətmə
    if (
        state.last_candle_time is not None
        and candle["close_time"]
        <= state.last_candle_time
    ):

        return None

    state.last_candle_time = (
        candle["close_time"]
    )

    state.bars_seen += 1

    state.candles.append(
        candle
    )

    print(
        f"🕐 NEW 15M | "
        f"{symbol} | "
        f"{local_time(candle['close_time'])} | "
        f"L={candle['low']:.8f} "
        f"H={candle['high']:.8f} "
        f"C={candle['close']:.8f}"
    )

    # ========================================================
    # SEARCH DIB 1
    # ========================================================

    if state.phase == "SEARCH_DIB1":

        process_search_dib1(
            state,
            candle,
            symbol,
            log_enabled=True
        )

        return None

    # ========================================================
    # TRACK HIGH 1
    # ========================================================

    if state.phase == "TRACK_HIGH1":

        process_track_high1(
            state,
            candle,
            symbol,
            log_enabled=True
        )

        return None

    # ========================================================
    # SEARCH DIB 2
    # ========================================================

    if state.phase == "SEARCH_DIB2":

        # Əvvəl DIB 2-ni yenilə
        process_dib2(
            state,
            candle,
            symbol,
            log_enabled=True
        )

        # Sonra breakout yoxla
        signal = check_breakout(
            state,
            candle,
            symbol,
            log_enabled=True
        )

        if signal:

            return signal

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
# INITIALIZE SYMBOL
# ============================================================

def initialize_symbol(symbol):

    history = get_history(
        symbol
    )

    if not history:

        print(
            f"⚠️ {symbol} | "
            f"No history"
        )

        return False

    state = build_initial_state(
        symbol,
        history
    )

    states[symbol] = state

    print(
        f"🧠 {symbol} | "
        f"Loaded {len(history)} candles | "
        f"State: {state.phase}"
    )

    # Əgər tarixdə artıq DIB1/HIGH1 təsdiqlənibsə,
    # bunu yalnız Railway-də göstəririk.
    if state.dib1 is not None:

        print(
            f"   DIB 1: "
            f"{state.dib1:.8f} | "
            f"{local_time(state.dib1_time)}"
        )

        print(
            f"   HIGH 1: "
            f"{state.high1:.8f} | "
            f"{local_time(state.high1_time)}"
        )

    if state.dib2_candidate is not None:

        print(
            f"   DIB 2 candidate: "
            f"{state.dib2_candidate:.8f}"
        )

    return True


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print(
        "================================================"
    )
    print(
        "🚀 BINANCE 15M DIB/HIGH SIGNAL BOT"
    )
    print(
        "================================================"
    )

    print(
        "Bot start:",
        local_time(BOT_START_TIME_MS)
    )

    print(
        "Timeframe:",
        INTERVAL
    )

    print(
        "Top coins:",
        TOP_COINS
    )

    print(
        "Structure candles:",
        STRUCTURE_CANDLES
    )

    print(
        "High left candles:",
        LEFT_HIGH_CANDLES
    )

    print(
        "Breakout minimum:",
        f"{MIN_BREAKOUT_PERCENT}%"
    )

    print(
        "Cooldown: NONE"
    )

    print(
        "Alert only: YES"
    )

    print()
    print(
        "100 candles = STRUCTURE CONTEXT"
    )

    print(
        "Historical breakout = NO ALERT"
    )

    print(
        "Only NEW candle can trigger Telegram"
    )

    print()

    # ========================================================
    # TOP 100
    # ========================================================

    symbols = get_top_symbols()

    print(
        f"📊 Tracking: "
        f"{len(symbols)} symbols"
    )

    # ========================================================
    # INITIALIZE ALL 100
    # ========================================================

    for symbol in symbols:

        try:

            initialize_symbol(
                symbol
            )

        except Exception as e:

            print(
                f"{symbol} initialization error:",
                e
            )

    print()
    print(
        "=============================================="
    )
    print(
        "✅ INITIALIZATION COMPLETE"
    )
    print(
        "=============================================="
    )
    print()

    last_heartbeat = time.time()

    scan_number = 0

    while True:

        try:

            scan_number += 1

            # ------------------------------------------------
            # TOP 100 yenilə
            # ------------------------------------------------

            new_symbols = get_top_symbols()

            if new_symbols:

                symbols = new_symbols

            new_candles = 0

            # ------------------------------------------------
            # TOP 100
            # ------------------------------------------------

            for symbol in symbols:

                try:

                    # ------------------------------------------------
                    # Yeni symbol TOP100-a daxil olubsa
                    # 100 candle context qur.
                    # ------------------------------------------------

                    if symbol not in states:

                        initialize_symbol(
                            symbol
                        )

                        continue

                    candle = (
                        get_latest_closed_candle(
                            symbol
                        )
                    )

                    if candle is None:

                        continue

                    # ------------------------------------------------
                    # Bot başlamazdan əvvəlki şam siqnal yaratmır
                    # ------------------------------------------------

                    if (
                        candle["close_time"]
                        <= BOT_START_TIME_MS
                    ):

                        continue

                    old_time = (
                        states[symbol]
                        .last_candle_time
                    )

                    # ------------------------------------------------
                    # Yalnız yeni 15M şam
                    # ------------------------------------------------

                    if (
                        old_time is not None
                        and candle["close_time"]
                        <= old_time
                    ):

                        continue

                    new_candles += 1

                    signal = process_new_candle(
                        symbol,
                        candle
                    )

                    # ------------------------------------------------
                    # SIGNAL
                    # ------------------------------------------------

                    if signal:

                        print()
                        print(
                            "🚨🚨🚨 SIGNAL:",
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
                        # Bu struktur bağlanır.
                        reset_after_signal(
                            states[symbol]
                        )

                except Exception as e:

                    print(
                        f"❌ {symbol} ERROR:",
                        e
                    )

            # ------------------------------------------------
            # HEARTBEAT
            # ------------------------------------------------

            now = time.time()

            if (
                now - last_heartbeat
                >= 60
            ):

                print(
                    f"💓 HEARTBEAT | "
                    f"Scan #{scan_number} | "
                    f"Symbols: {len(symbols)} | "
                    f"New candles: {new_candles} | "
                    f"Time: "
                    f"{local_time(int(now * 1000))}"
                )

                last_heartbeat = now

            time.sleep(
                SCAN_SECONDS
            )

        except Exception as e:

            print(
                "❌ MAIN LOOP ERROR:",
                e
            )

            time.sleep(10)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
