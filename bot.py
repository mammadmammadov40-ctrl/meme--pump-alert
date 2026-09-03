import os
import time
import requests
from datetime import datetime, timezone


# =========================================================
# SETTINGS
# =========================================================

BINANCE_BASE_URL = "https://data-api.binance.vision"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

MIN_QUOTE_VOLUME_24H = 20_000_000

FVG_MIN_RATIO = 0.50

TARGET_PERCENT = 1.7

SCAN_INTERVAL_SECONDS = 60

FVG_INTERVALS = ["15m", "1h"]

EMA_FAST = 20
EMA_MID = 50
EMA_SLOW = 100

VOLUME_CACHE_SECONDS = 300


# =========================================================
# STATE
# =========================================================

active_fvgs = {}

qualified_symbols = set()

last_processed_fvg = {}

fvg_search_start = {}

volume_cache = {}

telegram_state = {}


# =========================================================
# TELEGRAM
# =========================================================

def telegram_config_ok():
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def test_telegram_connection():
    """
    Telegram bağlantısını yoxlayır.
    HEÇ BİR mesaj göndərmir.
    """

    if not telegram_config_ok():
        print("[TELEGRAM] ERROR: Telegram variables are missing.")
        return False

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe"

        response = requests.get(
            url,
            timeout=15
        )

        data = response.json()

        if response.ok and data.get("ok"):
            username = data.get("result", {}).get("username", "unknown")
            print(f"[TELEGRAM] Connected: @{username}")
            return True

        print("[TELEGRAM] Connection failed:", data)

    except Exception as e:
        print("[TELEGRAM] Connection error:", e)

    return False


def send_telegram(message):
    """
    Yalnız yekun hadisələr üçün istifadə olunur.
    """

    if not telegram_config_ok():
        print("[TELEGRAM] Config missing.")
        return False

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }

        response = requests.post(
            url,
            data=payload,
            timeout=15
        )

        if response.ok:
            print("[TELEGRAM] Message sent.")
            return True

        print("[TELEGRAM] Send failed:", response.text)

    except Exception as e:
        print("[TELEGRAM] Send error:", e)

    return False


def send_telegram_once(key, message):
    """
    Eyni yekun hadisənin təkrar göndərilməsinin qarşısını alır.
    """

    if telegram_state.get(key):
        return False

    telegram_state[key] = True

    return send_telegram(message)


# =========================================================
# BINANCE REQUEST
# =========================================================

def binance_get(endpoint, params=None):

    try:
        url = BINANCE_BASE_URL + endpoint

        response = requests.get(
            url,
            params=params,
            timeout=20
        )

        if response.status_code != 200:
            print(
                f"[BINANCE] HTTP {response.status_code} "
                f"| {endpoint}"
            )
            return None

        return response.json()

    except Exception as e:
        print(f"[BINANCE] Request error: {e}")
        return None


# =========================================================
# SYMBOLS
# =========================================================

def get_spot_usdt_symbols():

    data = binance_get("/api/v3/exchangeInfo")

    if not data:
        return []

    symbols = []

    for item in data.get("symbols", []):

        if item.get("status") != "TRADING":
            continue

        if item.get("quoteAsset") != "USDT":
            continue

        if item.get("isSpotTradingAllowed") is not True:
            continue

        symbol = item.get("symbol")

        if symbol:
            symbols.append(symbol)

    return symbols


# =========================================================
# 24H VOLUME
# =========================================================

def get_qualified_symbols():

    now = time.time()

    if volume_cache.get("timestamp"):

        if now - volume_cache["timestamp"] < VOLUME_CACHE_SECONDS:
            return volume_cache.get("symbols", [])

    data = binance_get("/api/v3/ticker/24hr")

    if not data:
        return []

    qualified = []

    for item in data:

        symbol = item.get("symbol", "")

        if not symbol.endswith("USDT"):
            continue

        try:
            quote_volume = float(
                item.get("quoteVolume", 0)
            )
        except:
            continue

        if quote_volume >= MIN_QUOTE_VOLUME_24H:

            qualified.append(symbol)

    volume_cache["timestamp"] = now
    volume_cache["symbols"] = qualified

    return qualified


# =========================================================
# KLINES
# =========================================================

def get_closed_klines(symbol, interval, limit=150):

    data = binance_get(
        "/api/v3/klines",
        {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        }
    )

    if not data:
        return []

    # Son şam açıqdırsa onu çıxarırıq.
    # FVG yalnız bağlanmış şamlarla hesablanır.
    now_ms = int(time.time() * 1000)

    closed = []

    for candle in data:

        close_time = int(candle[6])

        if close_time <= now_ms:
            closed.append(candle)

    return closed


def get_current_price(symbol):

    data = binance_get(
        "/api/v3/ticker/price",
        {
            "symbol": symbol
        }
    )

    if not data:
        return None

    try:
        return float(data["price"])
    except:
        return None


# =========================================================
# EMA
# =========================================================

def calculate_ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    ema = sum(values[:period]) / period

    for price in values[period:]:
        ema = (
            (price - ema) * multiplier
            + ema
        )

    return ema


# =========================================================
# BULLISH TREND
# =========================================================

def get_bullish_trend(symbol):

    """
    YENİ TREND MƏNTİQİ

    CURRENT PRICE > EMA20 > EMA50 > EMA100

    EMA-lar 1H intervaldan hesablanır.

    Cari qiymət ayrıca real-time ticker-dən alınır.
    """

    klines = get_closed_klines(
        symbol,
        "1h",
        150
    )

    if len(klines) < EMA_SLOW:
        return False, None

    closes = [
        float(candle[4])
        for candle in klines
    ]

    ema20 = calculate_ema(
        closes,
        EMA_FAST
    )

    ema50 = calculate_ema(
        closes,
        EMA_MID
    )

    ema100 = calculate_ema(
        closes,
        EMA_SLOW
    )

    current_price = get_current_price(symbol)

    if (
        ema20 is None
        or ema50 is None
        or ema100 is None
        or current_price is None
    ):
        return False, None

    bullish = (
        current_price > ema20
        and ema20 > ema50
        and ema50 > ema100
    )

    info = {
        "price": current_price,
        "ema20": ema20,
        "ema50": ema50,
        "ema100": ema100
    }

    return bullish, info


# =========================================================
# INITIALIZE SYMBOL
# =========================================================

def initialize_symbol_cycle(symbol):

    for interval in FVG_INTERVALS:

        klines = get_closed_klines(
            symbol,
            interval,
            100
        )

        if klines:

            latest_close_time = int(
                klines[-1][6]
            )

            fvg_search_start[
                (symbol, interval)
            ] = latest_close_time


# =========================================================
# FIND NEW BEARISH FVG
# =========================================================

def find_new_bearish_fvg(symbol, interval):

    klines = get_closed_klines(
        symbol,
        interval,
        100
    )

    if len(klines) < 10:
        return None

    last_processed = last_processed_fvg.get(
        (symbol, interval),
        0
    )

    # Ən yeni patterndən geriyə baxırıq.
    for i in range(len(klines) - 1, 1, -1):

        c1 = klines[i - 2]
        c2 = klines[i - 1]
        c3 = klines[i]

        c3_close_time = int(c3[6])

        # Artıq işlənmiş FVG
        if c3_close_time <= last_processed:
            continue

        # -------------------------------------------------
        # CANDLE VALUES
        # -------------------------------------------------

        c1_open = float(c1[1])
        c1_high = float(c1[2])
        c1_low = float(c1[3])
        c1_close = float(c1[4])

        c2_open = float(c2[1])
        c2_high = float(c2[2])
        c2_low = float(c2[3])
        c2_close = float(c2[4])

        c3_open = float(c3[1])
        c3_high = float(c3[2])
        c3_low = float(c3[3])
        c3_close = float(c3[4])

        # -------------------------------------------------
        # C1 BEARISH
        # -------------------------------------------------

        c1_bearish = c1_close < c1_open

        # -------------------------------------------------
        # C2 BEARISH
        # -------------------------------------------------

        c2_bearish = c2_close < c2_open

        if not c1_bearish or not c2_bearish:
            continue

        # -------------------------------------------------
        # FVG
        #
        # C1 LOW > C3 HIGH
        # -------------------------------------------------

        if c1_low <= c3_high:
            continue

        fvg_high = c1_low
        fvg_low = c3_high

        fvg_size = fvg_high - fvg_low

        if fvg_size <= 0:
            continue

        # -------------------------------------------------
        # C2 BODY
        # -------------------------------------------------

        c2_body_high = max(
            c2_open,
            c2_close
        )

        c2_body_low = min(
            c2_open,
            c2_close
        )

        c2_body_size = (
            c2_body_high
            - c2_body_low
        )

        if c2_body_size <= 0:
            continue

        # -------------------------------------------------
        # FVG FULLY INSIDE C2 BODY
        # -------------------------------------------------

        if fvg_low < c2_body_low:
            continue

        if fvg_high > c2_body_high:
            continue

        # -------------------------------------------------
        # FVG >= 50% OF C2 BODY
        # -------------------------------------------------

        ratio = fvg_size / c2_body_size

        if ratio < FVG_MIN_RATIO:
            continue

        # -------------------------------------------------
        # TARGET
        # -------------------------------------------------

        target = c3_high * (
            1 - TARGET_PERCENT / 100
        )

        return {
            "symbol": symbol,
            "interval": interval,

            "c1_time": int(c1[0]),
            "c2_time": int(c2[0]),
            "c3_time": int(c3[0]),

            "c1_open": c1_open,
            "c1_high": c1_high,
            "c1_low": c1_low,
            "c1_close": c1_close,

            "c2_open": c2_open,
            "c2_high": c2_high,
            "c2_low": c2_low,
            "c2_close": c2_close,

            "c3_open": c3_open,
            "c3_high": c3_high,
            "c3_low": c3_low,
            "c3_close": c3_close,

            "fvg_high": fvg_high,
            "fvg_low": fvg_low,
            "fvg_size": fvg_size,

            "c2_body_size": c2_body_size,
            "ratio": ratio,

            "target": target,

            "activated_at": time.time()
        }

    return None


# =========================================================
# FIND FVG
# =========================================================

def find_bearish_fvg(symbol):

    for interval in FVG_INTERVALS:

        fvg = find_new_bearish_fvg(
            symbol,
            interval
        )

        if fvg:
            return fvg

    return None


# =========================================================
# ACTIVATE FVG
# =========================================================

def activate_fvg(symbol, fvg):

    active_fvgs[symbol] = fvg

    key = (
        symbol,
        fvg["interval"],
        fvg["c3_time"]
    )

    last_processed_fvg[
        (symbol, fvg["interval"])
    ] = fvg["c3_time"]

    ratio_percent = fvg["ratio"] * 100

    message = (
        "🔻 <b>BEARISH FVG SIGNAL</b>\n\n"

        f"<b>Symbol:</b> {symbol}\n"
        f"<b>Interval:</b> {fvg['interval']}\n\n"

        f"<b>FVG:</b> "
        f"{fvg['fvg_low']:.8f} → "
        f"{fvg['fvg_high']:.8f}\n"

        f"<b>FVG / C2 Body:</b> "
        f"{ratio_percent:.1f}%\n\n"

        f"<b>C3 High:</b> "
        f"{fvg['c3_high']:.8f}\n"

        f"<b>Target:</b> "
        f"{fvg['target']:.8f}\n"

        f"<b>Target:</b> -{TARGET_PERCENT}%\n\n"

        "📉 <b>1.7% target izlənilir.</b>"
    )

    send_telegram_once(
        key,
        message
    )

    print(
        f"[FVG ACTIVE] {symbol} | "
        f"{fvg['interval']} | "
        f"FVG ratio={ratio_percent:.1f}% | "
        f"Target={fvg['target']}"
    )


# =========================================================
# RESET
# =========================================================

def reset_symbol_cycle(symbol):

    active_fvgs.pop(
        symbol,
        None
    )

    # Telegram state-i həmin symbol üçün təmizlə
    keys_to_remove = []

    for key in telegram_state:

        if isinstance(key, tuple):

            if key[0] == symbol:
                keys_to_remove.append(key)

    for key in keys_to_remove:
        telegram_state.pop(key, None)

    initialize_symbol_cycle(symbol)


# =========================================================
# MONITOR ACTIVE FVG
# =========================================================

def monitor_active_fvg(symbol):

    fvg = active_fvgs.get(symbol)

    if not fvg:
        return

    current_price = get_current_price(symbol)

    if current_price is None:
        return

    target = fvg["target"]

    c3_high = fvg["c3_high"]

    # =====================================================
    # 1) TARGET HIT
    # =====================================================

    if current_price <= target:

        key = (
            symbol,
            "TARGET",
            fvg["c3_time"]
        )

        message = (
            "🎯 <b>BEARISH FVG TARGET HIT</b>\n\n"

            f"<b>Symbol:</b> {symbol}\n"
            f"<b>Interval:</b> {fvg['interval']}\n\n"

            f"<b>Target:</b> "
            f"{target:.8f}\n"

            f"<b>Current price:</b> "
            f"{current_price:.8f}\n\n"

            f"✅ <b>-{TARGET_PERCENT}% uğurla yerinə yetirildi.</b>"
        )

        send_telegram_once(
            key,
            message
        )

        print(
            f"[TARGET HIT] {symbol} | "
            f"Price={current_price}"
        )

        reset_symbol_cycle(symbol)

        return

    # =====================================================
    # 2) C3 HIGH BREAK → CANCEL
    # =====================================================

    if current_price > c3_high:

        key = (
            symbol,
            "CANCEL",
            fvg["c3_time"]
        )

        message = (
            "❌ <b>BEARISH FVG CANCELLED</b>\n\n"

            f"<b>Symbol:</b> {symbol}\n"
            f"<b>Interval:</b> {fvg['interval']}\n\n"

            f"<b>C3 High:</b> "
            f"{c3_high:.8f}\n"

            f"<b>Current price:</b> "
            f"{current_price:.8f}\n\n"

            f"⚠️ <b>-{TARGET_PERCENT}% target çatmadan "
            f"C3 High keçildi.</b>"
        )

        send_telegram_once(
            key,
            message
        )

        print(
            f"[CANCELLED] {symbol} | "
            f"Price={current_price} | "
            f"C3 High={c3_high}"
        )

        reset_symbol_cycle(symbol)

        return


# =========================================================
# PROCESS SYMBOL
# =========================================================

def process_symbol(symbol):

    # -----------------------------------------------------
    # Əvvəl aktiv FVG varsa onu izləyirik.
    # -----------------------------------------------------

    if symbol in active_fvgs:

        monitor_active_fvg(symbol)

        return

    # -----------------------------------------------------
    # TREND
    # -----------------------------------------------------

    bullish, trend_info = get_bullish_trend(symbol)

    if not bullish:

        # Telegram YOX.
        # Yalnız Railway log.
        print(
            f"[TREND WAIT] {symbol}"
        )

        return

    # -----------------------------------------------------
    # TREND CONFIRMED
    # -----------------------------------------------------

    print(
        f"[TREND CONFIRMED] {symbol} | "
        f"Price={trend_info['price']:.8f} | "
        f"EMA20={trend_info['ema20']:.8f} | "
        f"EMA50={trend_info['ema50']:.8f} | "
        f"EMA100={trend_info['ema100']:.8f}"
    )

    # -----------------------------------------------------
    # FVG
    # -----------------------------------------------------

    fvg = find_bearish_fvg(symbol)

    if not fvg:
        return

    # -----------------------------------------------------
    # ACTIVATE
    # -----------------------------------------------------

    activate_fvg(
        symbol,
        fvg
    )


# =========================================================
# MAIN
# =========================================================

def main():

    print("=" * 60)
    print("BINANCE BEARISH FVG ALERT BOT")
    print("=" * 60)

    print(
        f"Min 24H Volume: "
        f"${MIN_QUOTE_VOLUME_24H:,}"
    )

    print(
        f"FVG Minimum Ratio: "
        f"{FVG_MIN_RATIO * 100:.0f}%"
    )

    print(
        f"Target: {TARGET_PERCENT}%"
    )

    print(
        f"Intervals: {FVG_INTERVALS}"
    )

    print(
        f"Scan Interval: "
        f"{SCAN_INTERVAL_SECONDS}s"
    )

    print(
        f"Binance: {BINANCE_BASE_URL}"
    )

    print("=" * 60)

    # Telegram connection yoxlanılır.
    # MESAJ GÖNDƏRMİR.
    test_telegram_connection()

    symbols = get_spot_usdt_symbols()

    print(
        f"[INIT] Found {len(symbols)} "
        f"Spot USDT symbols."
    )

    # Hər symbol üçün başlanğıc vəziyyət
    for symbol in symbols:

        initialize_symbol_cycle(symbol)

    while True:

        try:

            now = datetime.now(
                timezone.utc
            ).strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            print()
            print("=" * 60)
            print(
                f"[SCAN] {now} UTC"
            )

            # -------------------------------------------------
            # $20M+ SYMBOLS
            # -------------------------------------------------

            symbols = get_qualified_symbols()

            qualified_symbols.clear()

            qualified_symbols.update(
                symbols
            )

            print(
                f"[SCAN] Qualified symbols: "
                f"{len(symbols)}"
            )

            # -------------------------------------------------
            # PROCESS
            # -------------------------------------------------

            for symbol in symbols:

                try:

                    process_symbol(
                        symbol
                    )

                except Exception as e:

                    print(
                        f"[ERROR] "
                        f"{symbol}: {e}"
                    )

            print("=" * 60)

            time.sleep(
                SCAN_INTERVAL_SECONDS
            )

        except KeyboardInterrupt:

            print(
                "[BOT] Stopped."
            )

            break

        except Exception as e:

            print(
                f"[MAIN ERROR] {e}"
            )

            time.sleep(10)


# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    main()
