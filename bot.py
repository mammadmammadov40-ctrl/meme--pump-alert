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

MAX_STRUCTURE_CANDLES = 100
MIN_HIGH_CANDLES = 10

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

# ============================================================
# STATE
# ============================================================

states = {}

# Hər symbol/timeframe üçün son işlənmiş candle close time
last_processed = {}

# İlk dəfə scan olunub-olunmadığını saxlayır
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
# BINANCE SYMBOLS
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

            result.append(
                (
                    symbol,
                    volume
                )
            )

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
# GET CLOSED CANDLES
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

            # Yalnız bağlanmış şam
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
# NEW DIB1
# ============================================================

def create_new_dib1(symbol, timeframe, candle):

    state = {

        # DIB1
        "dib1": candle["low"],
        "dib1_time": candle["close_time"],

        # DIB1-dən sonrakı şam sayı
        "candles_after_dib1": 0,

        # HIGH1
        "high1": None,
        "high1_time": None,

        # HIGH1-in DIB1-dən neçə şam sonra
        "high1_candle_number": None,

        # 10+ şam şərti
        "rise_10_confirmed": False,

        # Struktur yaşı
        "structure_candles": 1,

        # DIB1 təsdiqlənib?
        "dib1_confirmed": False,
    }

    states[(symbol, timeframe)] = state

    print(
        f"[{format_time(candle['close_time'])}] "
        f"{symbol} {timeframe} | "
        f"NEW DIB1 = {candle['low']}"
    )


# ============================================================
# RESET DIB1
# ============================================================

def replace_dib1(symbol, timeframe, candle, reason):

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
# DIB1 CONFIRM
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
# PROCESS ONE CLOSED CANDLE
# ============================================================

def process_candle(
    symbol,
    timeframe,
    candle,
    previous_100_candle=None
):

    key = (symbol, timeframe)

    with state_lock:

        # ----------------------------------------------------
        # FIRST SCAN
        #
        # Bot ilk dəfə scan edəndə gördüyü bağlanmış şam
        # birbaşa DIB1 olur.
        # ----------------------------------------------------

        if key not in states:

            create_new_dib1(
                symbol,
                timeframe,
                candle
            )

            return

        state = states[key]

        # Əgər DIB1 artıq təsdiqlənibsə,
        # DIB1 hissəsi bitib.
        if state["dib1_confirmed"]:
            return

        # ----------------------------------------------------
        # STRUCTURE AGE
        # ----------------------------------------------------

        state["structure_candles"] += 1

        # 101-ci şamda struktur tam sıfırlanır
        if state["structure_candles"] > MAX_STRUCTURE_CANDLES:

            replace_dib1(
                symbol,
                timeframe,
                candle,
                "100 candle limit exceeded"
            )

            return

        # ----------------------------------------------------
        # YENİ DIB1 ŞƏRTİ
        # ----------------------------------------------------
        #
        # Yalnız CLOSE müqayisə olunur.
        #
        # Yeni bağlanmış şamın CLOSE-u
        # ondan 100 şam əvvəlki CLOSE-dan aşağıdırsa,
        # bu şam yeni DIB1 olur.
        #
        # 100 şamlıq müqayisə hər yeni şamda
        # bir şam irəli sürüşür.
        #
        # ----------------------------------------------------

        if previous_100_candle is not None:

            if candle["close"] < previous_100_candle["close"]:

                replace_dib1(
                    symbol,
                    timeframe,
                    candle,
                    (
                        f"NEW DIB1 CLOSE CONDITION | "
                        f"NEW CLOSE={candle['close']} < "
                        f"100TH CANDLE CLOSE="
                        f"{previous_100_candle['close']}"
                    )
                )

                return

        # ----------------------------------------------------
        # 1. ƏVVƏL DIB1 BREAK YOXLAYIRIQ
        # ----------------------------------------------------
        #
        # Əgər yeni bağlanan şamın LOW-u DIB1-dən aşağıdırsa:
        #
        #   10+ HIGH1 şərti artıq təsdiqlənibsə
        #       → DIB1 CONFIRMED
        #
        #   təsdiqlənməyibsə
        #       → köhnə DIB1 silinir
        #       → bu şamın LOW-u yeni DIB1 olur
        #
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
        # 2. DIB1 HƏLƏ QALIB
        # ----------------------------------------------------

        state["candles_after_dib1"] += 1

        candle_number = state["candles_after_dib1"]

        # ----------------------------------------------------
        # 3. HIGH1 DİNAMİK OLARAQ İZLƏNİR
        # ----------------------------------------------------
        #
        # Yeni HIGH əvvəlkindən yüksəkdirsə,
        # HIGH1 həmin yeni maksimuma keçir.
        #
        # ----------------------------------------------------

        if (
            state["high1"] is None
            or candle["high"] > state["high1"]
        ):

            state["high1"] = candle["high"]
            state["high1_time"] = candle["close_time"]

            # MƏHZ BU HIGH neçəinci şamda yaranıb
            state["high1_candle_number"] = candle_number

            print(
                f"[{format_time(candle['close_time'])}] "
                f"{symbol} {timeframe} | "
                f"HIGH1 = {candle['high']} | "
                f"DIB1 → HIGH1 = "
                f"{candle_number} candles"
            )

        # ----------------------------------------------------
        # 4. 10+ ŞAM ŞƏRTİ
        # ----------------------------------------------------
        #
        # ÇOX VACİB:
        #
        # 10 şamın keçməsi təkbaşına kifayət deyil.
        #
        # HIGH1-in özü DIB1-dən ən azı 10 şam
        # sonra yaranmalıdır.
        #
        # ----------------------------------------------------

        if (
            state["high1"] is not None
            and state["high1_candle_number"] is not None
            and state["high1_candle_number"] >= MIN_HIGH_CANDLES
            and state["high1"] > state["dib1"]
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
# PROCESS NEW CANDLES
# ============================================================

def scan_symbol(symbol, timeframe):

    key = (symbol, timeframe)

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
    # İLK SCAN
    # --------------------------------------------------------
    #
    # İlk scan zamanı son bağlanmış şam DIB1 olacaq.
    # Ondan əvvəlki şamlar istifadə edilmir.
    #
    # --------------------------------------------------------

    if key not in initialized:

        latest = candles[-1]

        create_new_dib1(
            symbol,
            timeframe,
            latest
        )

        last_processed[key] = latest["close_time"]

        initialized[key] = True

        return

    # --------------------------------------------------------
    # SONRAKI SCANLAR
    # --------------------------------------------------------

    previous_close_time = last_processed.get(
        key,
        0
    )

    new_candles = [
        c for c in candles
        if c["close_time"] > previous_close_time
    ]

    if not new_candles:
        return

    # Köhnədən yeniyə
    new_candles.sort(
        key=lambda x: x["close_time"]
    )

    for candle in new_candles:

        # ----------------------------------------------------
        # Yeni bağlanmış şamın 100 şam əvvəlindəki şamı tapırıq
        # ----------------------------------------------------

        candle_index = candles.index(candle)

        previous_100_candle = None

        if candle_index >= 100:

            previous_100_candle = candles[
                candle_index - 100
            ]

        process_candle(
            symbol,
            timeframe,
            candle,
            previous_100_candle
        )

        last_processed[key] = candle["close_time"]


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("BINANCE DIB1 + HIGH1 SCANNER")
    print("TELEGRAM ALERT ONLY")
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
                or current_time - last_top_refresh
                >= TOP_REFRESH_SECONDS
            ):

                new_symbols = get_top_symbols()

                if new_symbols:

                    symbols = new_symbols

                    last_top_refresh = current_time

                    print(
                        f"\nTop {len(symbols)} USDT coins loaded."
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
                            f"{symbol} {timeframe} scan error:",
                            e
                        )

            print(
                f"[{now_az().strftime('%Y-%m-%d %H:%M:%S')}] "
                f"Scan completed. "
                f"Next scan in {POLL_SECONDS}s."
            )

            time.sleep(POLL_SECONDS)

        except KeyboardInterrupt:

            print("Bot stopped.")
            break

        except Exception as e:

            print("MAIN ERROR:", e)
            time.sleep(5)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
