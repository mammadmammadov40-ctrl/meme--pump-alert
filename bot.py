import os
import time
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from threading import Lock

# ============================================================
# BINANCE DIB1 + HIGH1 SCANNER
# TELEGRAM ALERT ONLY — NO AUTOMATIC ORDER
# ============================================================

BINANCE_URL = "https://api.binance.com"
TELEGRAM_URL = "https://api.telegram.org/bot{}/sendMessage"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TOP_COINS = 100

TIMEFRAMES = [
    "5m",
    "15m",
    "1h",
]

POLL_SECONDS = 10
TOP_REFRESH_SECONDS = 60

# DIB1-dən sonra struktur maksimum 100 şam izlənir
MAX_STRUCTURE_CANDLES = 100

# HIGH1 üçün minimum məsafə
MIN_HIGH_CANDLES = 10

# Hərəkətli period
ROLLING_CANDLES = 100

REQUEST_TIMEOUT = 10

AZ_TZ = ZoneInfo("Asia/Baku")

EXCLUDED_BASES = {
    "UP",
    "DOWN",
    "BULL",
    "BEAR",
}

session = requests.Session()
state_lock = Lock()

states = {}
last_processed = {}
initialized = {}


# ============================================================
# TIME
# ============================================================

def now_az():
    return datetime.now(AZ_TZ)


def format_time(ms):
    dt = datetime.fromtimestamp(ms / 1000, AZ_TZ)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram ENV dəyişənləri yoxdur.")
        return

    url = TELEGRAM_URL.format(TELEGRAM_BOT_TOKEN)

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }

    try:
        r = session.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        if not r.ok:
            print("Telegram error:", r.text)

    except Exception as e:
        print("Telegram exception:", e)


# ============================================================
# TOP 100 COINS
# ============================================================

def get_top_symbols():

    try:

        info = session.get(
            BINANCE_URL + "/api/v3/exchangeInfo",
            timeout=REQUEST_TIMEOUT
        ).json()

        valid_symbols = {}

        for s in info["symbols"]:

            if s["status"] != "TRADING":
                continue

            if s["quoteAsset"] != "USDT":
                continue

            if s["isSpotTradingAllowed"] is not True:
                continue

            base = s["baseAsset"].upper()

            if any(base.endswith(x) for x in EXCLUDED_BASES):
                continue

            valid_symbols[s["symbol"]] = True

        tickers = session.get(
            BINANCE_URL + "/api/v3/ticker/24hr",
            timeout=REQUEST_TIMEOUT
        ).json()

        result = []

        for t in tickers:

            symbol = t["symbol"]

            if symbol not in valid_symbols:
                continue

            try:
                volume = float(t["quoteVolume"])
            except:
                continue

            result.append((symbol, volume))

        result.sort(
            key=lambda x: x[1],
            reverse=True
        )

        return [
            x[0]
            for x in result[:TOP_COINS]
        ]

    except Exception as e:

        print("Symbol error:", e)

        return []


# ============================================================
# CLOSED CANDLES
# ============================================================

def get_closed_candles(symbol, interval, limit=101):

    try:

        r = session.get(
            BINANCE_URL + "/api/v3/klines",
            params={
                "symbol": symbol,
                "interval": interval,
                "limit": limit
            },
            timeout=REQUEST_TIMEOUT
        )

        r.raise_for_status()

        data = r.json()

        now_ms = int(time.time() * 1000)

        candles = []

        for k in data:

            open_time = int(k[0])
            open_price = float(k[1])
            high = float(k[2])
            low = float(k[3])
            close = float(k[4])
            close_time = int(k[6])

            # Yalnız bağlanmış şamlar
            if close_time > now_ms:
                continue

            candles.append({
                "open_time": open_time,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "close_time": close_time,
            })

        return candles

    except Exception as e:

        print(
            f"{symbol} {interval} candle error:",
            e
        )

        return []


# ============================================================
# CREATE DIB1
# ============================================================

def create_new_dib1(symbol, timeframe, candle):

    state = {

        "dib1": candle["low"],

        "dib1_time": candle["close_time"],

        "candles_after_dib1": 0,

        "high1": None,

        "high1_time": None,

        "high1_candle_number": None,

        "rise_10_confirmed": False,

        "structure_candles": 1,

        "dib1_confirmed": False,
    }

    states[(symbol, timeframe)] = state

    print(
        f"[{format_time(candle['close_time'])}] "
        f"{symbol} {timeframe} | "
        f"NEW DIB1 = {candle['low']}"
    )


# ============================================================
# REPLACE DIB1
# ============================================================

def replace_dib1(
    symbol,
    timeframe,
    candle,
    reason
):

    key = (symbol, timeframe)

    states[key] = {

        "dib1": candle["low"],

        "dib1_time": candle["close_time"],

        "candles_after_dib1": 0,

        "high1": None,

        "high1_time": None,

        "high1_candle_number": None,

        "rise_10_confirmed": False,

        "structure_candles": 1,

        "dib1_confirmed": False,
    }

    print(
        f"[{format_time(candle['close_time'])}] "
        f"{symbol} {timeframe} | "
        f"NEW DIB1 = {candle['low']} | "
        f"REASON: {reason}"
    )


# ============================================================
# CONFIRM DIB1
# ============================================================

def confirm_dib1(
    symbol,
    timeframe,
    state,
    breaking_candle
):

    state["dib1_confirmed"] = True

    dib1 = state["dib1"]

    high1 = state["high1"]

    high_number = state["high1_candle_number"]

    break_candles = (
        state["candles_after_dib1"]
        - high_number
    )

    message = (

        f"🔔 DIB1 CONFIRMED\n\n"

        f"Coin: {symbol}\n"

        f"TF: {timeframe}\n\n"

        f"DIB1: {dib1}\n"

        f"DIB1 time: "
        f"{format_time(state['dib1_time'])}\n\n"

        f"HIGH1: {high1}\n"

        f"HIGH1 time: "
        f"{format_time(state['high1_time'])}\n\n"

        f"DIB1 → HIGH1: "
        f"{high_number} candles\n"

        f"HIGH1 → DIB1 break: "
        f"{break_candles} candles\n\n"

        f"DIB1 break LOW: "
        f"{breaking_candle['low']}\n"

        f"Break time: "
        f"{format_time(breaking_candle['close_time'])}\n"
    )

    print("\n" + message + "\n")

    send_telegram(message)


# ============================================================
# PROCESS CANDLE
# ============================================================

def process_candle(
    symbol,
    timeframe,
    candle,
    rolling_100th_candle=None
):

    key = (symbol, timeframe)

    with state_lock:

        # ----------------------------------------------------
        # Əgər əvvəl heç bir DIB1 yoxdursa
        # ----------------------------------------------------

        if key not in states:

            # İlk DIB1-i avtomatik yaratmırıq.
            # 100 şamlıq periodun yaranmasını gözləyirik.

            return

        state = states[key]

        # ----------------------------------------------------
        # Artıq təsdiqlənibsə
        # ----------------------------------------------------

        if state["dib1_confirmed"]:
            return

        # ----------------------------------------------------
        # STRUCTURE CANDLE COUNT
        # ----------------------------------------------------

        state["structure_candles"] += 1

        if (
            state["structure_candles"]
            > MAX_STRUCTURE_CANDLES
        ):

            replace_dib1(
                symbol,
                timeframe,
                candle,
                "100 candle structure limit exceeded"
            )

            return

        # ----------------------------------------------------
        # 1-CI ŞAM vs 100-CÜ ŞAM
        # ----------------------------------------------------
        #
        # Yeni bağlanan candle = 1-ci şam
        #
        # rolling_100th_candle =
        # həmin 100 şamlıq periodun 100-cü şamı
        #
        # CLOSE müqayisəsi:
        #
        # 1-ci CLOSE < 100-cü CLOSE
        #
        # olarsa:
        #
        # 1-ci LOW = DIB1
        # ----------------------------------------------------

        if rolling_100th_candle is not None:

            current_close = candle["close"]

            candle_100_close = (
                rolling_100th_candle["close"]
            )

            if current_close < candle_100_close:

                replace_dib1(
                    symbol,
                    timeframe,
                    candle,
                    (
                        "1-ci ŞAM CLOSE < "
                        "100-cü ŞAM CLOSE | "
                        f"1-ci CLOSE={current_close} | "
                        f"100-cü CLOSE={candle_100_close}"
                    )
                )

                return

        # ----------------------------------------------------
        # DIB1 BREAK
        # ----------------------------------------------------

        if candle["low"] < state["dib1"]:

            if (
                state["rise_10_confirmed"]
                and state["high1"] is not None
            ):

                confirm_dib1(
                    symbol,
                    timeframe,
                    state,
                    candle
                )

                return

            else:

                replace_dib1(
                    symbol,
                    timeframe,
                    candle,
                    "DIB1 broken before valid HIGH1"
                )

                return

        # ----------------------------------------------------
        # DIB1 HƏLƏ QORUNUR
        # ----------------------------------------------------

        state["candles_after_dib1"] += 1

        candle_number = (
            state["candles_after_dib1"]
        )

        # ----------------------------------------------------
        # HIGH1
        # ----------------------------------------------------

        if (
            state["high1"] is None
            or candle["high"] > state["high1"]
        ):

            state["high1"] = candle["high"]

            state["high1_time"] = (
                candle["close_time"]
            )

            state["high1_candle_number"] = (
                candle_number
            )

            print(
                f"[{format_time(candle['close_time'])}] "
                f"{symbol} {timeframe} | "
                f"HIGH1 = {candle['high']} | "
                f"DIB1 → HIGH1 = "
                f"{candle_number} candles"
            )

        # ----------------------------------------------------
        # 10+ CANDLE HIGH1 CONFIRMATION
        # ----------------------------------------------------

        if (
            state["high1"] is not None
            and
            state["high1_candle_number"] is not None
            and
            state["high1_candle_number"]
            >= MIN_HIGH_CANDLES
            and
            state["high1"] > state["dib1"]
        ):

            if not state["rise_10_confirmed"]:

                state["rise_10_confirmed"] = True

                print(
                    f"[{format_time(candle['close_time'])}] "
                    f"{symbol} {timeframe} | "
                    f"10+ HIGH1 CONFIRMED | "
                    f"HIGH1 candle = "
                    f"{state['high1_candle_number']}"
                )


# ============================================================
# SCAN SYMBOL
# ============================================================

def scan_symbol(symbol, timeframe):

    key = (symbol, timeframe)

    # 100 şamlıq period üçün ən azı 100 bağlı şam alırıq
    candles = get_closed_candles(
        symbol,
        timeframe,
        limit=101
    )

    if not candles:
        return

    candles.sort(
        key=lambda x: x["close_time"]
    )

    # --------------------------------------------------------
    # İlk dəfə yüklənəndə:
    # Sadəcə məlumatı yadda saxlayırıq.
    # DIB1 avtomatik yaranmır.
    # --------------------------------------------------------

    if key not in initialized:

        if len(candles) < ROLLING_CANDLES:
            return

        last_processed[key] = (
            candles[-1]["close_time"]
        )

        initialized[key] = True

        print(
            f"{symbol} {timeframe} | "
            f"100 şamlıq hərəkətli period hazırdır."
        )

        return

    previous_close_time = (
        last_processed.get(key, 0)
    )

    new_candles = [

        c for c in candles

        if c["close_time"]
        > previous_close_time

    ]

    if not new_candles:
        return

    new_candles.sort(
        key=lambda x: x["close_time"]
    )

    # --------------------------------------------------------
    # HƏR YENİ BAĞLANAN ŞAM
    # --------------------------------------------------------

    for candle in new_candles:

        # Yeni candle = 1-ci şam
        #
        # 100 şamlıq period:
        #
        # [1-ci, 2-ci, 3-cü, ... 100-cü]
        #
        # Buna görə candles siyahısında
        # cari candle-dan 99 indeks geridə
        # 100-cü şamdır.
        #
        # candle_index - 99

        candle_index = candles.index(candle)

        rolling_100th_candle = None

        if candle_index >= 99:

            rolling_100th_candle = (
                candles[candle_index - 99]
            )

        # Əgər 100 şamlıq period hazırdırsa
        if rolling_100th_candle is not None:

            process_candle(
                symbol,
                timeframe,
                candle,
                rolling_100th_candle
            )

        # ----------------------------------------------------
        # Son işlənmiş şam
        # ----------------------------------------------------

        last_processed[key] = (
            candle["close_time"]
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)

    print(
        "BINANCE DIB1 + HIGH1 SCANNER"
    )

    print(
        "ROLLING 100 CANDLE SYSTEM"
    )

    print(
        "TELEGRAM ALERT ONLY"
    )

    print("=" * 70)

    symbols = []

    last_top_refresh = 0

    while True:

        try:

            current_time = time.time()

            # ------------------------------------------------
            # TOP 100 REFRESH
            # ------------------------------------------------

            if (
                not symbols
                or
                current_time
                - last_top_refresh
                >= TOP_REFRESH_SECONDS
            ):

                new_symbols = get_top_symbols()

                if new_symbols:

                    symbols = new_symbols

                    last_top_refresh = (
                        current_time
                    )

                    print(
                        f"\nTop {len(symbols)} "
                        f"USDT coins loaded."
                    )

            # ------------------------------------------------
            # SCAN
            # ------------------------------------------------

            for symbol in symbols:

                for timeframe in TIMEFRAMES:

                    try:

                        scan_symbol(
                            symbol,
                            timeframe
                        )

                    except Exception as e:

                        print(
                            f"{symbol} "
                            f"{timeframe} "
                            f"scan error:",
                            e
                        )

            print(
                f"[{now_az().strftime('%Y-%m-%d %H:%M:%S')}] "
                f"Scan completed. "
                f"Next scan in "
                f"{POLL_SECONDS}s."
            )

            time.sleep(POLL_SECONDS)

        except KeyboardInterrupt:

            print("Bot stopped.")

            break

        except Exception as e:

            print(
                "MAIN ERROR:",
                e
            )

            time.sleep(5)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
