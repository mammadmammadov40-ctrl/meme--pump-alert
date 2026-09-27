import os
import time
import requests

from datetime import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# CONFIG
# ============================================================

BINANCE_URL = "https://api.binance.com"
INTERVAL = "5m"

TOP_COINS = 100
CANDLE_LIMIT = 100

LEFT_HIGH_CANDLES = 10

MIN_BREAKOUT_PERCENT = 1.0

SCAN_SECONDS = 15
REQUEST_TIMEOUT = 10
MAX_WORKERS = 10

SIGNAL_COOLDOWN_SECONDS = 24 * 60 * 60

AZ_TZ = ZoneInfo("Asia/Baku")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram env variables missing")
        return

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }

    try:
        requests.post(
            url,
            data=data,
            timeout=REQUEST_TIMEOUT
        )
    except Exception as e:
        print("Telegram error:", e)


# ============================================================
# TIME
# ============================================================

def format_time(ms):
    dt = datetime.fromtimestamp(
        ms / 1000,
        tz=AZ_TZ
    )

    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# BINANCE
# ============================================================

def get_top_symbols():
    url = f"{BINANCE_URL}/api/v3/ticker/24hr"

    try:
        r = requests.get(
            url,
            timeout=REQUEST_TIMEOUT
        )

        r.raise_for_status()

        data = r.json()

    except Exception as e:
        print("Ticker error:", e)
        return []

    symbols = []

    for x in data:

        symbol = x.get("symbol", "")

        if not symbol.endswith("USDT"):
            continue

        # Spot only
        if any(
            symbol.endswith(x)
            for x in ["UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT"]
        ):
            continue

        try:
            volume = float(x.get("quoteVolume", 0))
        except:
            continue

        symbols.append(
            (symbol, volume)
        )

    symbols.sort(
        key=lambda x: x[1],
        reverse=True
    )

    return [
        x[0]
        for x in symbols[:TOP_COINS]
    ]


# ============================================================
# CANDLES
# ============================================================

def get_closed_candles(symbol):

    url = f"{BINANCE_URL}/api/v3/klines"

    params = {
        "symbol": symbol,
        "interval": INTERVAL,
        "limit": CANDLE_LIMIT + 1
    }

    try:

        r = requests.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        r.raise_for_status()

        data = r.json()

    except Exception as e:
        print(f"{symbol} candle error:", e)
        return []

    candles = []

    now_ms = int(time.time() * 1000)

    for k in data:

        open_time = int(k[0])
        close_time = int(k[6])

        # Açıq şamı götürmə
        if close_time > now_ms:
            continue

        candles.append({
            "open_time": open_time,
            "close_time": close_time,
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4])
        })

    return candles[-CANDLE_LIMIT:]


# ============================================================
# HIGH
# ============================================================

def is_valid_high(candles, index):

    if index < LEFT_HIGH_CANDLES:
        return False

    current_high = candles[index]["high"]

    left = candles[
        index - LEFT_HIGH_CANDLES:index
    ]

    for c in left:
        if current_high <= c["high"]:
            return False

    return True


# ============================================================
# FIND HIGHEST VALID HIGH
# ============================================================

def find_initial_high(candles):

    candidates = []

    for i in range(
        LEFT_HIGH_CANDLES,
        len(candles)
    ):

        if is_valid_high(candles, i):

            candidates.append(i)

    if not candidates:
        return None

    # Son formalaşmış valid High
    return candidates[-1]


# ============================================================
# STATE
# ============================================================

class SymbolState:

    def __init__(self):

        self.initialized = False

        # Hazırkı əsas HIGH
        self.target_high = None
        self.target_high_index = None

        # Hazırkı aktiv DIB
        self.active_dib = None
        self.active_dib_index = None

        # DIB-dən sonra yaranan rally-nin ən yüksək nöqtəsi
        self.rally_peak = None
        self.rally_peak_index = None

        # DIB-dən sonra valid High gözlənilir
        self.waiting_for_valid_high = False

        # Son siqnal vaxtı
        self.last_signal_time = 0

        # Son işlənmiş şam
        self.last_candle_time = None


states = {}


# ============================================================
# STATE RESET
# ============================================================

def reset_state():

    return SymbolState()


# ============================================================
# BUILD INITIAL STRUCTURE
# ============================================================

def initialize_structure(symbol, candles):

    state = SymbolState()

    if len(candles) < LEFT_HIGH_CANDLES + 2:
        return state

    high_index = find_initial_high(candles)

    if high_index is None:
        return state

    high = candles[high_index]

    state.target_high = high["high"]
    state.target_high_index = high_index

    # --------------------------------------------------------
    # İlk HIGH-dan sonra ilk DIB
    #
    # İlk HIGH-dan sonra qiymətin etdiyi minimum low götürülür.
    # Sonrakı rally həmin DIB-dən başlayır.
    # --------------------------------------------------------

    after_high = candles[
        high_index + 1:
    ]

    if not after_high:
        return state

    min_index = min(
        range(len(after_high)),
        key=lambda i: after_high[i]["low"]
    )

    dib_index = (
        high_index +
        1 +
        min_index
    )

    state.active_dib = candles[dib_index]["low"]
    state.active_dib_index = dib_index

    state.initialized = True

    state.last_candle_time = candles[-1]["close_time"]

    return state


# ============================================================
# PROCESS SYMBOL
# ============================================================

def process_symbol(symbol):

    candles = get_closed_candles(symbol)

    if len(candles) < LEFT_HIGH_CANDLES + 5:
        return None

    if symbol not in states:

        state = initialize_structure(
            symbol,
            candles
        )

        states[symbol] = state

        # İlk quruluşda köhnə breakout-u siqnal etmə
        return None

    state = states[symbol]

    # --------------------------------------------------------
    # Yalnız yeni bağlanmış şamlarla işləyirik
    # --------------------------------------------------------

    new_candles = []

    for c in candles:

        if (
            state.last_candle_time is None
            or c["close_time"] > state.last_candle_time
        ):
            new_candles.append(c)

    if not new_candles:
        return None

    signal = None

    for candle in new_candles:

        current_close = candle["close"]
        current_high = candle["high"]
        current_low = candle["low"]

        # ====================================================
        # 1. TARGET HIGH YOXDURSA
        # ====================================================

        if state.target_high is None:

            # Hazırkı rally peak-i saxla
            if (
                state.rally_peak is None
                or current_high > state.rally_peak
            ):

                state.rally_peak = current_high

                state.rally_peak_index = (
                    candles.index(candle)
                    if candle in candles
                    else None
                )

            # Valid High olub-olmadığını yoxla
            try:
                idx = candles.index(candle)
            except ValueError:
                idx = None

            if (
                idx is not None
                and idx >= LEFT_HIGH_CANDLES
                and is_valid_high(candles, idx)
            ):

                state.target_high = current_high
                state.target_high_index = idx

                state.waiting_for_valid_high = False

                state.rally_peak = current_high
                state.rally_peak_index = idx

            state.last_candle_time = candle["close_time"]

            continue

        # ====================================================
        # 2. BREAKOUT YOXLAMASI
        # ====================================================

        breakout_percent = (
            (current_close - state.target_high)
            / state.target_high
        ) * 100

        if (
            current_close > state.target_high
            and breakout_percent >= MIN_BREAKOUT_PERCENT
        ):

            now = time.time()

            if (
                now - state.last_signal_time
                >= SIGNAL_COOLDOWN_SECONDS
            ):

                high_candle = candles[
                    state.target_high_index
                ]

                dib_candle = candles[
                    state.active_dib_index
                ]

                signal = {
                    "symbol": symbol,

                    "high": state.target_high,
                    "high_time": high_candle["close_time"],

                    "dib": state.active_dib,
                    "dib_time": dib_candle["close_time"],

                    "breakout_close": current_close,
                    "breakout_time": candle["close_time"],

                    "breakout_percent": breakout_percent
                }

                state.last_signal_time = now

            # Breakout olduqdan sonra yeni struktur
            # axtarmaq üçün state-i sıfırla.
            state.target_high = None
            state.target_high_index = None

            state.active_dib = None
            state.active_dib_index = None

            state.rally_peak = None
            state.rally_peak_index = None

            state.waiting_for_valid_high = True

            state.last_candle_time = candle["close_time"]

            if signal:
                return signal

            continue

        # ====================================================
        # 3. RALLY PEAK-I YENİLƏ
        # ====================================================

        if (
            state.rally_peak is None
            or current_high > state.rally_peak
        ):

            state.rally_peak = current_high

            try:
                state.rally_peak_index = candles.index(candle)
            except:
                state.rally_peak_index = None

        # ====================================================
        # 4. AKTİV DIB QIRILIB?
        # ====================================================

        if (
            state.active_dib is not None
            and current_low < state.active_dib
        ):

            # ------------------------------------------------
            # Yeni DIB
            # ------------------------------------------------

            new_dib = current_low

            try:
                new_dib_index = candles.index(candle)
            except:
                new_dib_index = None

            old_rally_peak = state.rally_peak
            old_rally_peak_index = (
                state.rally_peak_index
            )

            # ------------------------------------------------
            # Əvvəlki DIB-dən gələn rally peak-i
            # valid High-dır?
            # ------------------------------------------------

            valid_peak = False

            if (
                old_rally_peak_index is not None
                and old_rally_peak_index >= LEFT_HIGH_CANDLES
            ):

                valid_peak = is_valid_high(
                    candles,
                    old_rally_peak_index
                )

            # ------------------------------------------------
            # Əgər peak 10 sol şam şərtinə uyğundursa
            # yeni HIGH target olur.
            #
            # Əks halda həmin peak TAMAMİLƏ İGNOR olunur.
            # ------------------------------------------------

            if valid_peak:

                state.target_high = old_rally_peak
                state.target_high_index = (
                    old_rally_peak_index
                )

                state.waiting_for_valid_high = False

            else:

                state.target_high = None
                state.target_high_index = None

                state.waiting_for_valid_high = True

            # Yeni DIB aktiv olur
            state.active_dib = new_dib
            state.active_dib_index = new_dib_index

            # Yeni rally başlayır
            state.rally_peak = None
            state.rally_peak_index = None

            state.last_candle_time = candle["close_time"]

            continue

        # ====================================================
        # 5. ƏGƏR TARGET HIGH YOXDURSA
        #    YENİ VALID HIGH AXTAR
        # ====================================================

        if (
            state.target_high is None
            and state.active_dib is not None
        ):

            try:
                idx = candles.index(candle)
            except:
                idx = None

            if (
                idx is not None
                and idx >= LEFT_HIGH_CANDLES
                and is_valid_high(candles, idx)
            ):

                # Yeni valid High
                state.target_high = current_high
                state.target_high_index = idx

                state.waiting_for_valid_high = False

                state.rally_peak = current_high
                state.rally_peak_index = idx

        state.last_candle_time = candle["close_time"]

    return signal


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

def make_signal_message(signal):

    breakout = signal["breakout_percent"]

    return (
        "🚨 5M BREAKOUT SIGNAL 🚨\n\n"

        f"🪙 Coin: {signal['symbol']}\n\n"

        "📈 HIGH\n"
        f"Price: {signal['high']:.8f}\n"
        f"Time: {format_time(signal['high_time'])}\n\n"

        "📉 DIB\n"
        f"Price: {signal['dib']:.8f}\n"
        f"Time: {format_time(signal['dib_time'])}\n\n"

        "🔥 BREAKOUT\n"
        f"Close: {signal['breakout_close']:.8f}\n"
        f"Time: {format_time(signal['breakout_time'])}\n"
        f"Breakout: +{breakout:.2f}%\n\n"

        "✅ Breakout >= 1%\n"
        "⚠️ ALERT ONLY — NO AUTO ORDER"
    )


# ============================================================
# MAIN LOOP
# ============================================================

def main():

    print("==========================================")
    print("BINANCE 5M HIGH / DIB BREAKOUT BOT")
    print("ALERT ONLY — NO AUTO ORDER")
    print("==========================================")

    while True:

        try:

            symbols = get_top_symbols()

            if not symbols:

                print("No symbols found")
                time.sleep(SCAN_SECONDS)
                continue

            print(
                f"\nScanning {len(symbols)} symbols..."
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

                            print(
                                f"🚨 SIGNAL: {symbol} "
                                f"+{signal['breakout_percent']:.2f}%"
                            )

                            message = (
                                make_signal_message(
                                    signal
                                )
                            )

                            send_telegram(
                                message
                            )

                    except Exception as e:

                        print(
                            f"{symbol} processing error:",
                            e
                        )

            print(
                f"Next scan in {SCAN_SECONDS}s..."
            )

            time.sleep(SCAN_SECONDS)

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
