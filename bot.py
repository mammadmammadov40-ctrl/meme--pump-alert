import os
import time
import requests
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo


# ============================================================
# DIB 1 TEST BOT
# ============================================================
# ONLY SIGNAL / NO AUTOMATIC ORDER
#
# Məntiq:
#
# DIB1 yaranır
#      ↓
# DIB1-dən SONRA 10+ şamlıq yüksəliş
#      ↓
# Bu yüksəliş zamanı ən yüksək HIGH izlənir
#      ↓
# Qiymət geri qayıdır və DIB1-i qırır
#      ↓
# DIB1 TƏSDİQLƏNİR
#      ↓
# həmin anda ən yüksək HIGH = gələcək HIGH1
#
# Hazırkı versiyada yalnız DIB1 test edilir.
# HIGH1 breakout / DIB2 hələ əlavə olunmur.
# ============================================================


# ============================================================
# SETTINGS
# ============================================================

BINANCE_URL = "https://api.binance.com"

TIMEFRAMES = ["5m", "15m", "1h"]

TOP_COINS = 100

# DIB1-dən sonra tələb olunan minimum şam sayı
MIN_RISE_CANDLES = 10

# Bir strukturun maksimum ömrü
MAX_STRUCTURE_CANDLES = 100

# Neçə saniyədən bir yoxlama
POLL_SECONDS = 10

# Top 100 neçə saniyədən bir yenilənsin
TOP_REFRESH_SECONDS = 60

# Baku vaxtı
AZ_TZ = ZoneInfo("Asia/Baku")


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
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
            timeout=10
        )
    except Exception as e:
        print("Telegram error:", e)


# ============================================================
# STATE
# ============================================================

@dataclass
class DibState:

    # Aktiv DIB1
    dib1: float | None = None
    dib1_time: int | None = None

    # DIB1-dən sonra neçə bağlanmış şam keçib
    candles_after_dib1: int = 0

    # DIB1-dən sonra görülən ən yüksək HIGH
    highest_high: float | None = None
    highest_high_time: int | None = None

    # 10+ şam şərti keçilibmi?
    rise_10_confirmed: bool = False

    # Strukturun neçə şamlıq olduğunu göstərir
    structure_candles: int = 0

    # Son işlənmiş şamın close time-ı
    last_candle_time: int | None = None

    # DIB1 artıq təsdiqlənibmi?
    dib1_confirmed: bool = False


states = {}


# ============================================================
# TIME
# ============================================================

def now_baku():
    return datetime.now(AZ_TZ).strftime("%Y-%m-%d %H:%M:%S")


def candle_time(ms):
    return datetime.fromtimestamp(
        ms / 1000,
        tz=AZ_TZ
    ).strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# BINANCE
# ============================================================

def get_top_100_symbols():

    try:

        response = requests.get(
            f"{BINANCE_URL}/api/v3/ticker/24hr",
            timeout=10
        )

        response.raise_for_status()

        data = response.json()

        symbols = []

        for item in data:

            symbol = item.get("symbol", "")

            # Yalnız USDT
            if not symbol.endswith("USDT"):
                continue

            # Spot üçün istifadə etdiyimiz normal USDT cütləri
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
                volume = float(
                    item.get("quoteVolume", 0)
                )
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
            symbol
            for symbol, volume
            in symbols[:TOP_COINS]
        ]

    except Exception as e:

        print(
            f"[{now_baku()}] "
            f"Top 100 error: {e}"
        )

        return []


# ============================================================
# GET LAST CLOSED CANDLE
# ============================================================

def get_last_closed_candle(symbol, interval):

    try:

        response = requests.get(
            f"{BINANCE_URL}/api/v3/klines",
            params={
                "symbol": symbol,
                "interval": interval,
                "limit": 2
            },
            timeout=10
        )

        response.raise_for_status()

        data = response.json()

        if len(data) < 2:
            return None

        # Sonuncu şam açıq ola bilər.
        # Ona görə ikinci sonuncu bağlanmış şamı götürürük.
        k = data[-2]

        return {
            "open_time": int(k[0]),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "close_time": int(k[6])
        }

    except Exception as e:

        print(
            f"{symbol} {interval} candle error: {e}"
        )

        return None


# ============================================================
# RESET STRUCTURE
# ============================================================

def reset_structure(
    state,
    new_dib1,
    new_dib1_time,
    first_candle=True
):

    state.dib1 = new_dib1
    state.dib1_time = new_dib1_time

    state.candles_after_dib1 = 0

    state.highest_high = None
    state.highest_high_time = None

    state.rise_10_confirmed = False

    state.dib1_confirmed = False

    if first_candle:
        state.structure_candles = 1
    else:
        state.structure_candles = 1


# ============================================================
# PROCESS DIB1
# ============================================================

def process_candle(
    symbol,
    timeframe,
    candle
):

    key = (symbol, timeframe)

    if key not in states:
        states[key] = DibState()

    state = states[key]

    close_time = candle["close_time"]
    low = candle["low"]
    high = candle["high"]

    # --------------------------------------------------------
    # Eyni bağlanmış şamı ikinci dəfə işlətmə
    # --------------------------------------------------------

    if state.last_candle_time == close_time:
        return

    state.last_candle_time = close_time

    # ========================================================
    # 1. DIB1 YOXDUR
    # ========================================================

    if state.dib1 is None:

        reset_structure(
            state,
            new_dib1=low,
            new_dib1_time=close_time
        )

        print(
            f"[{now_baku()}] "
            f"{symbol} {timeframe} | "
            f"NEW DIB1 = {low}"
        )

        return

    # ========================================================
    # 2. STRUKTURUN 101-Cİ ŞAMI
    # ========================================================

    # Cari şam əvvəlcə strukturun növbəti şamıdır.
    next_structure_candle = (
        state.structure_candles + 1
    )

    if next_structure_candle > MAX_STRUCTURE_CANDLES:

        print(
            f"[{now_baku()}] "
            f"{symbol} {timeframe} | "
            f"STRUCTURE EXPIRED | "
            f"NEW DIB1 = {low}"
        )

        reset_structure(
            state,
            new_dib1=low,
            new_dib1_time=close_time
        )

        return

    state.structure_candles = next_structure_candle

    # ========================================================
    # 3. DIB1 HƏLƏ TƏSDİQLƏNMƏYİB
    # ========================================================

    if not state.dib1_confirmed:

        # ----------------------------------------------------
        # A) Əvvəlcə DIB1 aşağı qırılıb-qırılmadığını yoxlayırıq
        # ----------------------------------------------------

        if low < state.dib1:

            # -----------------------------------------------
            # 10+ şam şərti artıq tamamlanıbsa:
            #
            # DIB1 təsdiqlənir.
            #
            # Həmin vaxta qədər görülən ən yüksək HIGH
            # gələcək HIGH1 olur.
            # -----------------------------------------------

            if state.rise_10_confirmed:

                state.dib1_confirmed = True

                print(
                    f"\n"
                    f"[{now_baku()}] "
                    f"====================================\n"
                    f"{symbol} {timeframe} | "
                    f"DIB1 CONFIRMED\n"
                    f"DIB1: {state.dib1}\n"
                    f"DIB1 TIME: "
                    f"{candle_time(state.dib1_time)}\n"
                    f"HIGH1 CANDIDATE: "
                    f"{state.highest_high}\n"
                    f"HIGH1 TIME: "
                    f"{candle_time(state.highest_high_time)}\n"
                    f"10+ CANDLES: YES\n"
                    f"DIB1 BREAK LOW: {low}\n"
                    f"====================================\n"
                )

                # Hələlik yalnız DIB1 test olunur.
                # HIGH1/DIB2 məntiqi sonrakı mərhələdə əlavə ediləcək.

                return

            # -----------------------------------------------
            # 10+ şam tamamlanmayıbsa:
            #
            # Köhnə DIB1 silinir.
            # Qıran şamın LOW-u yeni DIB1 olur.
            # -----------------------------------------------

            old_dib = state.dib1

            reset_structure(
                state,
                new_dib1=low,
                new_dib1_time=close_time
            )

            print(
                f"[{now_baku()}] "
                f"{symbol} {timeframe} | "
                f"DIB1 RESET\n"
                f"OLD DIB1: {old_dib}\n"
                f"NEW DIB1: {low}\n"
                f"Reason: 10+ candles not completed"
            )

            return

        # ----------------------------------------------------
        # B) DIB1 qırılmayıbsa
        # ----------------------------------------------------

        # Bu şam DIB1-dən sonrakı şamdır.
        state.candles_after_dib1 += 1

        # ----------------------------------------------------
        # Ən yüksək HIGH daim izlənir
        # ----------------------------------------------------

        if (
            state.highest_high is None
            or high > state.highest_high
        ):

            state.highest_high = high
            state.highest_high_time = close_time

        # ----------------------------------------------------
        # 10+ şam şərti
        # ----------------------------------------------------

        if (
            state.candles_after_dib1
            >= MIN_RISE_CANDLES
        ):

            if not state.rise_10_confirmed:

                state.rise_10_confirmed = True

                print(
                    f"[{now_baku()}] "
                    f"{symbol} {timeframe} | "
                    f"10+ CANDLES CONFIRMED | "
                    f"DIB1={state.dib1} | "
                    f"HIGHEST={state.highest_high}"
                )

        return

    # ========================================================
    # 4. DIB1 ARTİQ TƏSDİQLƏNİB
    # ========================================================

    # Bu mərhələ hələlik yalnız DIB1 kodunda
    # gözləmə vəziyyətidir.
    #
    # Sonrakı mərhələdə burada:
    #
    # DIB2
    # HIGH1 breakout
    # +1% breakout
    #
    # məntiqi əlavə ediləcək.

    return


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 60)
    print("DIB1 TEST BOT STARTED")
    print("=" * 60)
    print("Timeframes:", TIMEFRAMES)
    print("Top coins:", TOP_COINS)
    print("Minimum rise candles:", MIN_RISE_CANDLES)
    print("Max structure candles:", MAX_STRUCTURE_CANDLES)
    print("Historical candles are NOT used for signal formation.")
    print("=" * 60)
    print()

    # --------------------------------------------------------
    # Vacib:
    #
    # Bot başlayanda heç bir əvvəlki candle DIB1 yaratmaq üçün
    # istifadə olunmur.
    #
    # İlk görülən BAĞLANMIŞ şamdan başlayırıq.
    # --------------------------------------------------------

    start_time_ms = int(time.time() * 1000)

    print(
        f"[{now_baku()}] "
        f"START TIME = {candle_time(start_time_ms)}"
    )

    top_symbols = []
    last_top_refresh = 0

    while True:

        try:

            # =================================================
            # TOP 100 REFRESH
            # =================================================

            current_time = time.time()

            if (
                not top_symbols
                or current_time - last_top_refresh
                >= TOP_REFRESH_SECONDS
            ):

                new_symbols = get_top_100_symbols()

                if new_symbols:

                    top_symbols = new_symbols

                    last_top_refresh = current_time

                    print(
                        f"[{now_baku()}] "
                        f"Top 100 refreshed: "
                        f"{len(top_symbols)} symbols"
                    )

            # =================================================
            # SCAN
            # =================================================

            for symbol in top_symbols:

                for timeframe in TIMEFRAMES:

                    candle = get_last_closed_candle(
                        symbol,
                        timeframe
                    )

                    if candle is None:
                        continue

                    # ------------------------------------------------
                    # Bot başlamazdan əvvəl bağlanmış candle-ları
                    # heç vaxt istifadə etmirik.
                    # ------------------------------------------------

                    if candle["close_time"] < start_time_ms:
                        continue

                    process_candle(
                        symbol,
                        timeframe,
                        candle
                    )

            time.sleep(POLL_SECONDS)

        except KeyboardInterrupt:

            print("Bot stopped.")
            break

        except Exception as e:

            print(
                f"[{now_baku()}] "
                f"MAIN ERROR: {e}"
            )

            time.sleep(5)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
