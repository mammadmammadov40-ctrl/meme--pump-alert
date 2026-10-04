import os
import time
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from threading import Lock

# ============================================================
# BINANCE DIB1 + HIGH1 SCANNER
# ROLLING 100 CANDLE SYSTEM
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
    "30m",
    "1h",
]

POLL_SECONDS = 10
TOP_REFRESH_SECONDS = 60

# Rolling period — 100 şam
ROLLING_CANDLES = 100

# DIB1-dən HIGH1-ə minimum şam sayı
MIN_CANDLES_TO_HIGH1 = 10

# Neçə bağlı şam çəkilsin
FETCH_LIMIT = 250

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

# (symbol, timeframe) -> state dict
states = {}

# (symbol, timeframe) -> son işlənmiş close_time
last_processed = {}

# (symbol, timeframe) -> initialized flag
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

def get_closed_candles(symbol, interval, limit=FETCH_LIMIT):

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

            close_time = int(k[6])

            # Yalnız bağlanmış şamlar
            if close_time > now_ms:
                continue

            candles.append({
                "open_time": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "close_time": close_time,
            })

        candles.sort(key=lambda x: x["close_time"])

        return candles

    except Exception as e:

        print(
            f"{symbol} {interval} candle error:",
            e
        )

        return []


# ============================================================
# CREATE NEW DIB1
# ============================================================

def create_dib1(symbol, timeframe, candle, reason=""):

    key = (symbol, timeframe)

    states[key] = {

        # DIB1
        "dib1": candle["low"],
        "dib1_time": candle["close_time"],

        # DIB1-dən sonra neçə şam keçib
        "candles_after_dib1": 0,

        # HIGH1
        "high1": None,
        "high1_time": None,
        "high1_candle_number": None,
    }

    print(
        f"[{format_time(candle['close_time'])}] "
        f"{symbol} {timeframe} | "
        f"NEW DIB1 = {candle['low']} | "
        f"REASON: {reason}"
    )


# ============================================================
# CONFIRM DIB1  → TELEGRAM ALERT
# ============================================================

def confirm_dib1(symbol, timeframe, state, breaking_candle):

    dib1 = state["dib1"]
    high1 = state["high1"]
    high1_number = state["high1_candle_number"]

    # HIGH1 → DIB1 break arasındakı şam sayı
    break_candles = (
        state["candles_after_dib1"]
        - high1_number
    )

    message = (

        f"🔔 DIB1 CONFIRMED\n\n"

        f"Coin: {symbol}\n"
        f"TF: {timeframe}\n\n"

        f"DIB1: {dib1}\n"
        f"DIB1 time: {format_time(state['dib1_time'])}\n\n"

        f"HIGH1: {high1}\n"
        f"HIGH1 time: {format_time(state['high1_time'])}\n\n"

        f"DIB1 → HIGH1: {high1_number} candles\n"
        f"HIGH1 → DIB1 break: {break_candles} candles\n\n"

        f"DIB1 break LOW: {breaking_candle['low']}\n"
        f"Break time: {format_time(breaking_candle['close_time'])}\n"
    )

    print("\n" + message + "\n")

    send_telegram(message)


# ============================================================
# PROCESS CANDLE
# prev_100_min_low = əvvəlki 100 şamın ən aşağı low-u
# ============================================================

def process_candle(
    symbol,
    timeframe,
    candle,
    prev_100_min_low=None
):

    key = (symbol, timeframe)

    with state_lock:

        # ----------------------------------------------------
        # DIB1 MÖVCUDDURSA → idarə et
        # ----------------------------------------------------

        if key in states:

            state = states[key]

            # DIB1-dən sonra keçən şam sayı
            state["candles_after_dib1"] += 1
            candle_number = state["candles_after_dib1"]

            # ------------------------------------------------
            # 100 ŞAM LİMİTİ → LƏĞV
            # ------------------------------------------------
            # DIB1 izlənilir, təsdiqlənməyib və 100 şamlıq
            # pəncərədən çıxır → silinir, yenidən son şamdan
            # rolling müqayisəsi başlayır

            if candle_number >= ROLLING_CANDLES:

                print(
                    f"[{format_time(candle['close_time'])}] "
                    f"{symbol} {timeframe} | "
                    f"DIB1 LƏĞV OLUNDU "
                    f"(100 şamlıq pəncərədən çıxdı) | "
                    f"old DIB1={state['dib1']}"
                )

                del states[key]

                # Aşağı düşür → yeni DIB1 axtarışı

            else:

                # --------------------------------------------
                # DIB1 BREAK YOXLAMASI
                # --------------------------------------------

                if candle["low"] < state["dib1"]:

                    # 10+ şam keçib və HIGH1 mövcuddur → CONFIRM
                    if (
                        state["high1"] is not None
                        and state["high1_candle_number"] is not None
                        and state["high1_candle_number"] >= MIN_CANDLES_TO_HIGH1
                    ):

                        confirm_dib1(
                            symbol,
                            timeframe,
                            state,
                            candle
                        )

                        del states[key]
                        return

                    else:

                        # 10+ keçməyib və ya HIGH1 yoxdur → yeni DIB1
                        create_dib1(
                            symbol,
                            timeframe,
                            candle,
                            (
                                "DIB1 broken before 10+ candles / "
                                "no valid HIGH1 | "
                                f"old DIB1={state['dib1']}"
                            )
                        )

                        return

                # --------------------------------------------
                # DIB1 HƏLƏ QORUNUR → HIGH1 İZLƏ
                # --------------------------------------------

                if (
                    state["high1"] is None
                    or candle["high"] > state["high1"]
                ):

                    state["high1"] = candle["high"]
                    state["high1_time"] = candle["close_time"]
                    state["high1_candle_number"] = candle_number

                    print(
                        f"[{format_time(candle['close_time'])}] "
                        f"{symbol} {timeframe} | "
                        f"HIGH1 = {candle['high']} | "
                        f"DIB1 → HIGH1 = {candle_number} candles"
                    )

                return

        # ----------------------------------------------------
        # DIB1 YOXDUR → YENİ DIB1 YOXLAMASI
        # Şərt: cari şam.low < ƏVVƏLKİ 100 şamın ƏN AŞAĞI low-u
        # ----------------------------------------------------

        if prev_100_min_low is None:
            return  # 100 şam hələ tamamlanmayıb

        if candle["low"] < prev_100_min_low:

            create_dib1(
                symbol,
                timeframe,
                candle,
                (
                    "New low vs prev 100 candles | "
                    f"cari LOW={candle['low']} < "
                    f"prev 100 min LOW={prev_100_min_low}"
                )
            )


# ============================================================
# SCAN SYMBOL
# ============================================================

def scan_symbol(symbol, timeframe):

    key = (symbol, timeframe)

    candles = get_closed_candles(
        symbol,
        timeframe,
        limit=FETCH_LIMIT
    )

    if not candles:
        return

    # --------------------------------------------------------
    # İlk dəfə → yalnız son bağlanmış şamı qeyd et
    # --------------------------------------------------------

    if key not in initialized:

        if len(candles) < ROLLING_CANDLES + 1:
            return

        last_processed[key] = candles[-1]["close_time"]
        initialized[key] = True

        print(
            f"{symbol} {timeframe} | "
            f"Rolling {ROLLING_CANDLES} period hazırdır."
        )

        return

    previous_close_time = last_processed.get(key, 0)

    new_candles = [
        c for c in candles
        if c["close_time"] > previous_close_time
    ]

    if not new_candles:
        return

    new_candles.sort(key=lambda x: x["close_time"])

    # İndeks xəritəsi (sürətli axtarış üçün)
    index_map = {
        c["close_time"]: i
        for i, c in enumerate(candles)
    }

    # --------------------------------------------------------
    # HƏR YENİ BAĞLANAN ŞAM
    # --------------------------------------------------------

    for candle in new_candles:

        i = index_map[candle["close_time"]]

        # Əvvəlki 100 şamın ən aşağı low-u
        prev_100_min_low = None

        if i >= ROLLING_CANDLES:

            prev_window = candles[
                i - ROLLING_CANDLES : i
            ]

            prev_100_min_low = min(
                c["low"] for c in prev_window
            )

        process_candle(
            symbol,
            timeframe,
            candle,
            prev_100_min_low
        )

        last_processed[key] = candle["close_time"]


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("BINANCE DIB1 + HIGH1 SCANNER")
    print("ROLLING 100 CANDLE SYSTEM")
    print("PREV-100 MIN LOW DIB1 CONDITION")
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
                or current_time - last_top_refresh >= TOP_REFRESH_SECONDS
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
                        scan_symbol(symbol, timeframe)

                    except Exception as e:
                        print(
                            f"{symbol} {timeframe} scan error:",
                            e
                        )

            print(
                f"[{now_az().strftime('%Y-%m-%d %H:%M:%S')}] "
                f"Scan completed. Next scan in {POLL_SECONDS}s."
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
