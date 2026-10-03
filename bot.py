import os
import time
import requests

from datetime import datetime
from zoneinfo import ZoneInfo


# ============================================================
# BINANCE DIB1 SCANNER
# ALERT ONLY — NO AUTOMATIC ORDER
# ============================================================

BINANCE_URL = "https://api.binance.com"
TELEGRAM_URL = "https://api.telegram.org/bot{}/sendMessage"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

AZ_TZ = ZoneInfo("Asia/Baku")

TOP_COINS = 100

TIMEFRAMES = [
    "5m",
    "15m",
    "1h",
]

POLL_SECONDS = 10
TOP_REFRESH_SECONDS = 60

REQUEST_TIMEOUT = 10

# DIB1 strukturunun maksimum ömrü
MAX_STRUCTURE_CANDLES = 100

# DIB1-dən sonra tələb olunan minimum şam sayı
MIN_RISE_CANDLES = 10


# ============================================================
# STATE
# ============================================================

states = {}

last_processed = {}

top_symbols = []
last_top_refresh = 0


# ============================================================
# TIME
# ============================================================

def now_baku():
    return datetime.now(AZ_TZ)


def format_time(ts_ms):
    dt = datetime.fromtimestamp(ts_ms / 1000, AZ_TZ)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM ENV VARIABLES TAPILMADI")
        return

    url = TELEGRAM_URL.format(TELEGRAM_BOT_TOKEN)

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }

    try:
        r = requests.post(
            url,
            json=payload,
            timeout=10
        )

        if r.ok:
            print("Telegram bildirişi göndərildi")
        else:
            print(
                "Telegram error:",
                r.status_code,
                r.text
            )

    except Exception as e:
        print("Telegram exception:", e)


# ============================================================
# BINANCE SYMBOLS
# ============================================================

def get_top_symbols():

    try:

        r = requests.get(
            BINANCE_URL + "/api/v3/ticker/24hr",
            timeout=REQUEST_TIMEOUT
        )

        r.raise_for_status()

        data = r.json()

        items = []

        for x in data:

            symbol = x.get("symbol", "")

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
                volume = float(x["quoteVolume"])
            except:
                continue

            items.append(
                (symbol, volume)
            )

        items.sort(
            key=lambda x: x[1],
            reverse=True
        )

        return [
            x[0]
            for x in items[:TOP_COINS]
        ]

    except Exception as e:

        print(
            "Top coins error:",
            e
        )

        return []


# ============================================================
# CLOSED CANDLE
# ============================================================

def get_latest_closed_candle(symbol, interval):

    try:

        r = requests.get(
            BINANCE_URL + "/api/v3/klines",
            params={
                "symbol": symbol,
                "interval": interval,
                "limit": 2
            },
            timeout=REQUEST_TIMEOUT
        )

        r.raise_for_status()

        data = r.json()

        if len(data) < 2:
            return None

        # Son element formalaşan şamdır.
        # Ondan əvvəlki artıq bağlanmış şamdır.
        k = data[-2]

        return {
            "open_time": k[0],
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "close_time": k[6],
        }

    except Exception as e:

        print(
            f"{symbol} {interval} candle error:",
            e
        )

        return None


# ============================================================
# NEW STATE
# ============================================================

def create_state(candle):

    return {
        # DIB1
        "dib1": candle["low"],
        "dib1_time": candle["close_time"],

        # DIB1-dən sonra neçə bağlanmış şam keçib
        "candles_after_dib1": 0,

        # DIB1-dən sonra yaranan ən yüksək HIGH
        "highest_high": None,
        "highest_high_time": None,

        # 10+ şam + yuxarı qalxma şərti
        "rise_10_confirmed": False,

        # Strukturun ümumi yaşı
        "structure_candles": 1,

        # DIB1 artıq təsdiqlənib?
        "dib1_confirmed": False,

        # Son işlənmiş candle
        "last_close_time": candle["close_time"],
    }


# ============================================================
# DIB1 CONFIRMATION
# ============================================================

def confirm_dib1(
    symbol,
    timeframe,
    state,
    breaking_candle
):

    state["dib1_confirmed"] = True

    message = (
        "🟢 DIB1 TƏSDİQLƏNDİ\n\n"
        f"Coin: {symbol}\n"
        f"Timeframe: {timeframe}\n\n"

        f"DIB1: {state['dib1']}\n"
        f"DIB1 vaxtı: "
        f"{format_time(state['dib1_time'])}\n\n"

        f"HIGH1: {state['highest_high']}\n"
        f"HIGH1 vaxtı: "
        f"{format_time(state['highest_high_time'])}\n\n"

        f"DIB1 qırılan LOW: "
        f"{breaking_candle['low']}\n"
        f"Qırılma vaxtı: "
        f"{format_time(breaking_candle['close_time'])}\n\n"

        f"DIB1-dən sonra şam sayı: "
        f"{state['candles_after_dib1']}\n\n"

        "Şərtlər:\n"
        "✅ 10+ şam\n"
        "✅ DIB1-dən yuxarı HIGH yaranıb\n"
        "✅ DIB1 LOW ilə qırılıb"
    )

    print("\n" + "=" * 60)
    print(message)
    print("=" * 60 + "\n")

    send_telegram(message)


# ============================================================
# PROCESS DIB1
# ============================================================

def process_candle(
    symbol,
    timeframe,
    candle
):

    key = (
        symbol,
        timeframe
    )

    state = states.get(key)

    # --------------------------------------------------------
    # İlk candle
    # --------------------------------------------------------

    if state is None:

        states[key] = create_state(candle)

        print(
            f"{symbol} {timeframe}: "
            f"NEW DIB1 = {candle['low']}"
        )

        return

    # --------------------------------------------------------
    # Eyni candle-i təkrar işləmə
    # --------------------------------------------------------

    if candle["close_time"] <= state["last_close_time"]:
        return

    state["last_close_time"] = candle["close_time"]

    # --------------------------------------------------------
    # Struktur yaşı
    # --------------------------------------------------------

    state["structure_candles"] += 1

    # --------------------------------------------------------
    # 101-ci candle
    # --------------------------------------------------------

    if state["structure_candles"] > MAX_STRUCTURE_CANDLES:

        print(
            f"{symbol} {timeframe}: "
            "100 candle limiti bitdi. "
            "Köhnə struktur silindi."
        )

        # 101-ci candle yeni DIB1 olur
        states[key] = create_state(candle)

        print(
            f"{symbol} {timeframe}: "
            f"NEW DIB1 = {candle['low']}"
        )

        return

    # --------------------------------------------------------
    # ƏGƏR DIB1 ARTİQ TƏSDİQLƏNİBSƏ
    # --------------------------------------------------------

    if state["dib1_confirmed"]:

        return

    # --------------------------------------------------------
    # DIB1-dən sonra yeni candle
    # --------------------------------------------------------

    state["candles_after_dib1"] += 1

    # --------------------------------------------------------
    # ƏN YÜKSƏK HIGH-ı izləyirik
    # --------------------------------------------------------

    if (
        state["highest_high"] is None
        or candle["high"] > state["highest_high"]
    ):

        state["highest_high"] = candle["high"]
        state["highest_high_time"] = candle["close_time"]

        print(
            f"{symbol} {timeframe}: "
            f"NEW HIGH = {candle['high']}"
        )

    # --------------------------------------------------------
    # 10+ ŞAM + YUXARI QALXMA
    # --------------------------------------------------------

    if (
        state["candles_after_dib1"] >= MIN_RISE_CANDLES
        and state["highest_high"] is not None
        and state["highest_high"] > state["dib1"]
    ):

        if not state["rise_10_confirmed"]:

            state["rise_10_confirmed"] = True

            print(
                f"{symbol} {timeframe}: "
                f"10+ RISE CONFIRMED | "
                f"Candles={state['candles_after_dib1']} | "
                f"High={state['highest_high']}"
            )

    # --------------------------------------------------------
    # DIB1 QIRILMASI
    # --------------------------------------------------------

    if candle["low"] < state["dib1"]:

        # ----------------------------------------------------
        # 10+ şam + yuxarı qalxma VAR
        # ----------------------------------------------------

        if state["rise_10_confirmed"]:

            confirm_dib1(
                symbol,
                timeframe,
                state,
                candle
            )

            return

        # ----------------------------------------------------
        # 10+ şərt YOXDUR
        #
        # Köhnə DIB1 silinir.
        # Qıran candle-in LOW-u yeni DIB1 olur.
        # ----------------------------------------------------

        print(
            f"{symbol} {timeframe}: "
            f"DIB1 qırıldı, amma 10+ şərti "
            f"ödənmədi."
        )

        states[key] = create_state(candle)

        print(
            f"{symbol} {timeframe}: "
            f"NEW DIB1 = {candle['low']}"
        )

        return


# ============================================================
# MAIN
# ============================================================

def main():

    global top_symbols
    global last_top_refresh

    print("=" * 60)
    print("BINANCE DIB1 SCANNER STARTED")
    print("ALERT ONLY — NO AUTOMATIC ORDER")
    print("=" * 60)

    # --------------------------------------------------------
    # İlk top 100
    # --------------------------------------------------------

    top_symbols = get_top_symbols()

    last_top_refresh = time.time()

    print(
        f"Top {len(top_symbols)} coins loaded."
    )

    # --------------------------------------------------------
    # ƏSAS LOOP
    # --------------------------------------------------------

    while True:

        try:

            # -----------------------------------------------
            # Top 100 yenilə
            # -----------------------------------------------

            if (
                time.time() - last_top_refresh
                >= TOP_REFRESH_SECONDS
            ):

                new_symbols = get_top_symbols()

                if new_symbols:

                    top_symbols = new_symbols

                    print(
                        f"Top coins refreshed: "
                        f"{len(top_symbols)}"
                    )

                last_top_refresh = time.time()

            # -----------------------------------------------
            # Bütün coinlər / timeframe-lər
            # -----------------------------------------------

            for symbol in top_symbols:

                for timeframe in TIMEFRAMES:

                    candle = get_latest_closed_candle(
                        symbol,
                        timeframe
                    )

                    if candle is None:
                        continue

                    key = (
                        symbol,
                        timeframe
                    )

                    # ---------------------------------------
                    # Eyni candle-i təkrar yoxlama
                    # ---------------------------------------

                    previous = last_processed.get(key)

                    if previous == candle["close_time"]:
                        continue

                    last_processed[key] = candle["close_time"]

                    # ---------------------------------------
                    # DIB1
                    # ---------------------------------------

                    process_candle(
                        symbol,
                        timeframe,
                        candle
                    )

                    time.sleep(0.03)

            time.sleep(POLL_SECONDS)

        except KeyboardInterrupt:

            print(
                "Bot dayandırıldı."
            )

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
