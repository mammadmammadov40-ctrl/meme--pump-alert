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

# Binance-i hər 30 saniyədə yoxla
SCAN_SECONDS = 30

# Railway heartbeat hər 60 saniyə
HEARTBEAT_SECONDS = 60

REQUEST_TIMEOUT = 10

AZ_TZ = ZoneInfo("Asia/Baku")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# ============================================================
# BOT START TIME
# ============================================================
#
# ÇOX VACİB:
#
# Bot başladıqdan ƏVVƏL bağlanmış şamlar siqnal üçün istifadə
# olunmayacaq.
#
# Məsələn bot 23:42-də başlayıbsa:
#
# 23:30 şamı istifadə olunmur.
# 23:45 bağlanandan sonra ilk dəfə istifadə olunur.
#
# ============================================================

BOT_START_TIME_MS = int(time.time() * 1000)


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:

        print("⚠️ Telegram variables missing")

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

            print(
                "❌ Telegram error:",
                r.text
            )

        else:

            print("📨 Telegram alert sent")

    except Exception as e:

        print(
            "❌ Telegram exception:",
            e
        )


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

        print(
            "❌ Top coins error:",
            e
        )

        return []

    coins = []

    for item in data:

        symbol = item.get(
            "symbol",
            ""
        )

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
            f"{symbol} candle error:",
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

    return closed


# ============================================================
# SYMBOL STATE
# ============================================================

class State:

    def __init__(self):

        # ----------------------------------------------------
        # Yalnız bot başladıqdan sonra gələn şamlar
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
# RESET DIB 1 SEARCH
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

def search_dib1(state, index, symbol):

    candle = state.candles[index]

    # --------------------------------------------------------
    # İlk LOW
    # --------------------------------------------------------

    if state.dib1_candidate is None:

        state.dib1_candidate = candle["low"]

        state.dib1_candidate_index = index

        state.dib1_candidate_time = (
            candle["close_time"]
        )

        print(
            f"📉 {symbol} | "
            f"DIB 1 candidate: "
            f"{candle['low']:.8f} | "
            f"{local_time(candle['close_time'])}"
        )

        return

    # --------------------------------------------------------
    # Hələ yüksəliş başlamayıb
    # --------------------------------------------------------

    if not state.dib1_rising:

        # Daha aşağı LOW yaranıb
        if candle["low"] < state.dib1_candidate:

            state.dib1_candidate = candle["low"]

            state.dib1_candidate_index = index

            state.dib1_candidate_time = (
                candle["close_time"]
            )

            print(
                f"📉 {symbol} | "
                f"DIB 1 NEW LOW: "
                f"{candle['low']:.8f} | "
                f"{local_time(candle['close_time'])}"
            )

            return

        # ----------------------------------------------------
        # Qiymət DIB-dən yuxarı qalxmağa başladı
        # ----------------------------------------------------

        if candle["close"] > state.dib1_candidate:

            state.dib1_rising = True

            print(
                f"📈 {symbol} | "
                f"RISE FROM DIB 1: "
                f"{state.dib1_candidate:.8f}"
            )

            # HIGH üçün minimum 10 şam solda
            if index >= LEFT_HIGH_CANDLES:

                state.high1_candidate = candle["high"]

                state.high1_candidate_index = index

                state.high1_candidate_time = (
                    candle["close_time"]
                )

            state.state = "TRACK_HIGH1"


# ============================================================
# TRACK HIGH 1
# ============================================================

def track_high1(state, index, symbol):

    candle = state.candles[index]

    # --------------------------------------------------------
    # DIB 1 hələ təsdiqlənməyib.
    #
    # Əgər saved DIB aşağı qırılırsa:
    # əvvəlki DIB/HIGH strukturu silinir.
    # --------------------------------------------------------

    if candle["low"] < state.dib1_candidate:

        print(
            f"🔄 {symbol} | "
            f"DIB candidate broken: "
            f"{candle['low']:.8f}"
        )

        reset_dib1_search(state)

        # Cari şam yeni DIB kimi izlənə bilər
        search_dib1(
            state,
            index,
            symbol
        )

        return

    # --------------------------------------------------------
    # HIGH 1
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

        print(
            f"📈 {symbol} | "
            f"HIGH 1 candidate: "
            f"{candle['high']:.8f} | "
            f"{local_time(candle['close_time'])}"
        )

    elif (
        state.high1_candidate is not None
        and candle["high"] > state.high1_candidate
    ):

        old_high = state.high1_candidate

        state.high1_candidate = candle["high"]

        state.high1_candidate_index = index

        state.high1_candidate_time = (
            candle["close_time"]
        )

        print(
            f"📈 {symbol} | "
            f"HIGH 1 NEW HIGH: "
            f"{old_high:.8f} → "
            f"{state.high1_candidate:.8f}"
        )


# ============================================================
# CONFIRM DIB 1 + HIGH 1
# ============================================================

def check_dib1_confirmation(
    state,
    index,
    symbol
):

    candle = state.candles[index]

    if state.dib1_candidate is None:

        return False

    if state.high1_candidate is None:

        return False

    # --------------------------------------------------------
    # Saved DIB aşağı qırıldı.
    #
    # Eyni anda:
    #
    # DIB 1 = saved LOW
    # HIGH 1 = saved HIGH
    # --------------------------------------------------------

    if candle["low"] < state.dib1_candidate:

        state.dib1 = state.dib1_candidate

        state.dib1_time = (
            state.dib1_candidate_time
        )

        state.high1 = state.high1_candidate

        state.high1_time = (
            state.high1_candidate_time
        )

        # ----------------------------------------------------
        # DIB 2 üçün yeni aşağı hərəkət başlayır
        # ----------------------------------------------------

        state.dib2_candidate = candle["low"]

        state.dib2_candidate_index = index

        state.dib2_candidate_time = (
            candle["close_time"]
        )

        state.dib2_rising = False

        state.state = "SEARCH_DIB2"

        print()
        print("========================================")
        print(f"✅ {symbol} | DIB 1 + HIGH 1 CONFIRMED")
        print("========================================")

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

        print(
            "DIB 2 tracking starts:",
            state.dib2_candidate,
            local_time(state.dib2_candidate_time)
        )

        print("========================================")

        return True

    return False


# ============================================================
# SEARCH DIB 2
# ============================================================

def search_dib2(state, index, symbol):

    candle = state.candles[index]

    # --------------------------------------------------------
    # Daha aşağı LOW yaranırsa DIB 2 candidate dəyişir.
    # --------------------------------------------------------

    if state.dib2_candidate is None:

        state.dib2_candidate = candle["low"]

        state.dib2_candidate_index = index

        state.dib2_candidate_time = (
            candle["close_time"]
        )

        return

    # --------------------------------------------------------
    # Hələ yuxarı dönüş yoxdur
    # --------------------------------------------------------

    if not state.dib2_rising:

        # Yeni daha aşağı LOW
        if candle["low"] < state.dib2_candidate:

            state.dib2_candidate = candle["low"]

            state.dib2_candidate_index = index

            state.dib2_candidate_time = (
                candle["close_time"]
            )

            print(
                f"📉 {symbol} | "
                f"DIB 2 NEW LOW: "
                f"{candle['low']:.8f} | "
                f"{local_time(candle['close_time'])}"
            )

            return

        # ----------------------------------------------------
        # Qiymət ən aşağı nöqtədən yuxarı dönür
        # ----------------------------------------------------

        if candle["close"] > state.dib2_candidate:

            state.dib2_rising = True

            print(
                f"📈 {symbol} | "
                f"DIB 2 RISE STARTED | "
                f"DIB 2: "
                f"{state.dib2_candidate:.8f} | "
                f"{local_time(state.dib2_candidate_time)}"
            )


# ============================================================
# CHECK HIGH 1 BREAKOUT
# ============================================================

def check_high1_breakout(
    state,
    index,
    symbol
):

    candle = state.candles[index]

    if state.high1 is None:

        return None

    if state.dib2_candidate is None:

        return None

    # --------------------------------------------------------
    # DIB 2-dən yuxarı dönüş başlamalıdır
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
    # HIGH 1 qırılmalı
    # və CLOSE minimum +1% yuxarı olmalıdır
    # --------------------------------------------------------

    if (
        close_price > state.high1
        and breakout_percent >= MIN_BREAKOUT_PERCENT
    ):

        print()
        print("========================================")
        print(f"🚨 {symbol} | BREAKOUT DETECTED")
        print("========================================")

        print(
            "HIGH 1:",
            state.high1
        )

        print(
            "Close:",
            close_price
        )

        print(
            "Breakout:",
            f"+{breakout_percent:.2f}%"
        )

        print("========================================")

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
    # Eyni şamı iki dəfə işlətmə
    # --------------------------------------------------------

    if (
        state.last_candle_time is not None
        and candle["close_time"]
        <= state.last_candle_time
    ):

        return None, False

    state.last_candle_time = (
        candle["close_time"]
    )

    state.candles.append(candle)

    index = len(
        state.candles
    ) - 1

    # --------------------------------------------------------
    # YENİ ŞAM
    # --------------------------------------------------------

    print(
        f"🕐 NEW 15M | "
        f"{symbol} | "
        f"{local_time(candle['close_time'])} | "
        f"O={candle['open']:.8f} "
        f"H={candle['high']:.8f} "
        f"L={candle['low']:.8f} "
        f"C={candle['close']:.8f}"
    )

    # ========================================================
    # DIB 1
    # ========================================================

    if state.state == "SEARCH_DIB1":

        search_dib1(
            state,
            index,
            symbol
        )

        return None, True

    # ========================================================
    # HIGH 1
    # ========================================================

    if state.state == "TRACK_HIGH1":

        # DIB qırılıbsa əvvəlcə DIB/HIGH təsdiqlə
        if check_dib1_confirmation(
            state,
            index,
            symbol
        ):

            return None, True

        # DIB qırılmayıbsa HIGH-ı izləməyə davam
        track_high1(
            state,
            index,
            symbol
        )

        return None, True

    # ========================================================
    # DIB 2
    # ========================================================

    if state.state == "SEARCH_DIB2":

        search_dib2(
            state,
            index,
            symbol
        )

        signal = check_high1_breakout(
            state,
            index,
            symbol
        )

        if signal:

            return signal, True

        return None, True

    return None, True


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
# RESET AFTER SIGNAL
# ============================================================

def reset_after_signal(state):

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
    print("🚀 BINANCE 15M DIB/HIGH SIGNAL BOT")
    print("==============================================")
    print()
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
        "Cooldown: NONE"
    )
    print(
        "Previous candles: NOT LOADED"
    )
    print(
        "Alert only: YES"
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
        "Scan interval:",
        f"{SCAN_SECONDS}s"
    )
    print()
    print(
        "⚠️ Only candles CLOSED AFTER BOT START "
        "will be processed."
    )
    print()

    # --------------------------------------------------------
    # İlk TOP 100
    # --------------------------------------------------------

    symbols = get_top_symbols()

    print(
        "📊 Tracking:",
        len(symbols),
        "symbols"
    )

    print()

    last_heartbeat = time.time()

    total_scans = 0

    total_new_candles = 0

    while True:

        try:

            total_scans += 1

            # ------------------------------------------------
            # TOP 100 yenilə
            # ------------------------------------------------

            new_symbols = get_top_symbols()

            if new_symbols:

                symbols = new_symbols

            # ------------------------------------------------
            # SCAN BAŞLADI
            # ------------------------------------------------

            now = time.time()

            if (
                now - last_heartbeat
                >= HEARTBEAT_SECONDS
            ):

                print(
                    f"💓 HEARTBEAT | "
                    f"Scan #{total_scans} | "
                    f"Symbols: {len(symbols)} | "
                    f"New candles: {total_new_candles} | "
                    f"Time: "
                    f"{local_time(int(now * 1000))}"
                )

                last_heartbeat = now

            # ------------------------------------------------
            # Bütün TOP 100
            # ------------------------------------------------

            new_this_scan = 0

            for symbol in symbols:

                try:

                    candle = (
                        get_latest_closed_candle(
                            symbol
                        )
                    )

                    if candle is None:

                        continue

                    # ------------------------------------------------
                    # ÇOX VACİB:
                    #
                    # Bot başlamazdan ƏVVƏL bağlanmış şamı keç.
                    # ------------------------------------------------

                    if (
                        candle["close_time"]
                        <= BOT_START_TIME_MS
                    ):

                        continue

                    signal, is_new = (
                        process_candle(
                            symbol,
                            candle
                        )
                    )

                    if is_new:

                        new_this_scan += 1

                        total_new_candles += 1

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
                        # Struktur bağlanır.
                        reset_after_signal(
                            states[symbol]
                        )

                except Exception as e:

                    print(
                        f"❌ {symbol} ERROR:",
                        e
                    )

            # ------------------------------------------------
            # SCAN NƏTİCƏSİ
            # ------------------------------------------------

            if new_this_scan > 0:

                print(
                    f"🔄 SCAN COMPLETE | "
                    f"Checked: {len(symbols)} | "
                    f"New candles: {new_this_scan} | "
                    f"Time: "
                    f"{local_time(int(time.time() * 1000))}"
                )

            # ------------------------------------------------
            # Növbəti scan
            # ------------------------------------------------

            time.sleep(
                SCAN_SECONDS
            )

        except Exception as e:

            print()
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
