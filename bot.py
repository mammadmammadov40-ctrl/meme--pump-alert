import os
import time
import requests
from datetime import datetime, timezone


# ============================================================
# CONFIG
# ============================================================

# Binance public market-data API
BINANCE_BASE_URL = "https://data-api.binance.vision"

# ============================================================
# TELEGRAM SETTINGS
# Railway Variables-dan oxunur
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
).strip()


# ============================================================
# STRATEGY SETTINGS
# ============================================================

MIN_QUOTE_VOLUME_24H = 20_000_000

FVG_MIN_RATIO = 0.50

TARGET_PERCENT = 1.7

SCAN_INTERVAL_SECONDS = 60

FVG_INTERVALS = ["15m", "1h"]

EMA_FAST = 20
EMA_MID = 50
EMA_SLOW = 100

VOLUME_CACHE_SECONDS = 300


# ============================================================
# STATE
# ============================================================

active_fvgs = {}

qualified_symbols = set()

last_processed_fvg = {}

fvg_search_start = {}

volume_cache = {
    "timestamp": 0,
    "symbols": []
}

telegram_state = {}


# ============================================================
# BINANCE REQUEST
# ============================================================

def binance_get(endpoint, params=None):

    url = BINANCE_BASE_URL + endpoint

    try:

        response = requests.get(
            url,
            params=params,
            timeout=15
        )

        response.raise_for_status()

        return response.json()

    except Exception as e:

        print(
            f"[BINANCE ERROR] {endpoint}: {e}"
        )

        return None


# ============================================================
# TELEGRAM CONFIG CHECK
# ============================================================

def telegram_config_ok():

    if not TELEGRAM_BOT_TOKEN:

        print(
            "[TELEGRAM ERROR] "
            "TELEGRAM_BOT_TOKEN is empty."
        )

        return False

    if not TELEGRAM_CHAT_ID:

        print(
            "[TELEGRAM ERROR] "
            "TELEGRAM_CHAT_ID is empty."
        )

        return False

    return True


# ============================================================
# TELEGRAM REQUEST
# ============================================================

def send_telegram(message):

    if not telegram_config_ok():

        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }

    try:

        print("[TELEGRAM] Sending message...")

        response = requests.post(
            url,
            data=payload,
            timeout=15
        )

        print(
            f"[TELEGRAM STATUS] "
            f"{response.status_code}"
        )

        print(
            f"[TELEGRAM RESPONSE] "
            f"{response.text}"
        )

        if response.status_code == 200:

            try:

                data = response.json()

            except Exception:

                data = {}

            if data.get("ok") is True:

                print(
                    "[TELEGRAM SUCCESS] "
                    "Message sent successfully."
                )

                return True

            print(
                "[TELEGRAM FAILED] "
                f"{data}"
            )

            return False

        print(
            f"[TELEGRAM ERROR] "
            f"HTTP {response.status_code}"
        )

        return False

    except requests.exceptions.Timeout:

        print(
            "[TELEGRAM ERROR] "
            "Request timed out."
        )

        return False

    except requests.exceptions.RequestException as e:

        print(
            "[TELEGRAM REQUEST ERROR] "
            f"{e}"
        )

        return False

    except Exception as e:

        print(
            "[TELEGRAM UNKNOWN ERROR] "
            f"{e}"
        )

        return False


# ============================================================
# TELEGRAM ONCE
# ============================================================

def send_telegram_once(key, message):

    if telegram_state.get(key):

        return

    success = send_telegram(message)

    if success:

        telegram_state[key] = True


# ============================================================
# TELEGRAM CONNECTION TEST
# ============================================================

def test_telegram():

    print("=" * 70)
    print("[TELEGRAM TEST] Starting...")
    print("=" * 70)

    if not telegram_config_ok():

        print(
            "[TELEGRAM TEST] FAILED ❌"
        )

        return False

    result = send_telegram(
        "🧪 TELEGRAM TEST\n\n"
        "Binance Bearish FVG Bot Telegram connection is working. ✅"
    )

    if result:

        print(
            "[TELEGRAM TEST] SUCCESS ✅"
        )

    else:

        print(
            "[TELEGRAM TEST] FAILED ❌"
        )

    print("=" * 70)

    return result


# ============================================================
# SPOT USDT SYMBOLS
# ============================================================

def get_spot_usdt_symbols():

    data = binance_get(
        "/api/v3/exchangeInfo"
    )

    if not data:

        return []

    symbols = []

    for item in data.get("symbols", []):

        if (
            item.get("status") == "TRADING"
            and item.get("quoteAsset") == "USDT"
            and item.get("isSpotTradingAllowed") is True
        ):

            symbols.append(
                item["symbol"]
            )

    return symbols


# ============================================================
# $20M VOLUME FILTER
# ============================================================

def get_qualified_symbols():

    global volume_cache

    now = time.time()

    if (
        now - volume_cache["timestamp"]
        < VOLUME_CACHE_SECONDS
    ):

        return volume_cache["symbols"]

    data = binance_get(
        "/api/v3/ticker/24hr"
    )

    if not data:

        return volume_cache["symbols"]

    qualified = []

    for item in data:

        symbol = item.get(
            "symbol",
            ""
        )

        if not symbol.endswith("USDT"):

            continue

        try:

            quote_volume = float(
                item.get(
                    "quoteVolume",
                    0
                )
            )

        except Exception:

            continue

        if quote_volume >= MIN_QUOTE_VOLUME_24H:

            qualified.append(
                symbol
            )

    volume_cache["timestamp"] = now
    volume_cache["symbols"] = qualified

    return qualified


# ============================================================
# CLOSED KLINES
# ============================================================

def get_closed_klines(
    symbol,
    interval,
    limit=150
):

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

    now_ms = int(
        time.time() * 1000
    )

    closed = []

    for candle in data:

        close_time = int(
            candle[6]
        )

        if close_time < now_ms:

            closed.append(
                candle
            )

    return closed


# ============================================================
# CURRENT PRICE
# ============================================================

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

        return float(
            data["price"]
        )

    except Exception:

        return None


# ============================================================
# EMA
# ============================================================

def calculate_ema(
    values,
    period
):

    if len(values) < period:

        return None

    multiplier = 2 / (
        period + 1
    )

    ema = sum(
        values[:period]
    ) / period

    for price in values[period:]:

        ema = (
            (price - ema)
            * multiplier
            + ema
        )

    return ema


# ============================================================
# GET CURRENT 24H VOLUME
# ============================================================

def get_24h_volume(symbol):

    data = binance_get(
        "/api/v3/ticker/24hr",
        {
            "symbol": symbol
        }
    )

    if not data:

        return None

    try:

        return float(
            data["quoteVolume"]
        )

    except Exception:

        return None


# ============================================================
# 1H BULLISH TREND
# ============================================================

def get_bullish_trend_data(symbol):

    candles = get_closed_klines(
        symbol,
        "1h",
        150
    )

    if len(candles) < EMA_SLOW:

        return None

    closes = [
        float(candle[4])
        for candle in candles
    ]

    current_close = closes[-1]

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

    if None in (
        ema20,
        ema50,
        ema100
    ):

        return None

    bullish = (
        current_close > ema20
        and ema20 > ema50
        and ema50 > ema100
    )

    return {
        "bullish": bullish,
        "close": current_close,
        "ema20": ema20,
        "ema50": ema50,
        "ema100": ema100
    }


# ============================================================
# NEW SYMBOL / NEW CYCLE BASELINE
# ============================================================

def initialize_symbol_cycle(symbol):

    for interval in FVG_INTERVALS:

        candles = get_closed_klines(
            symbol,
            interval,
            5
        )

        if not candles:

            continue

        latest_open_time = int(
            candles[-1][0]
        )

        fvg_search_start[
            (symbol, interval)
        ] = latest_open_time

        last_processed_fvg[
            (symbol, interval)
        ] = latest_open_time


# ============================================================
# FIND NEW BEARISH FVG
# ============================================================

def find_new_bearish_fvg(
    symbol,
    interval
):

    candles = get_closed_klines(
        symbol,
        interval,
        100
    )

    if len(candles) < 3:

        return None

    last_processed = last_processed_fvg.get(
        (symbol, interval),
        0
    )

    search_start = fvg_search_start.get(
        (symbol, interval),
        0
    )

    for i in range(
        len(candles) - 1,
        1,
        -1
    ):

        c1 = candles[i - 2]
        c2 = candles[i - 1]
        c3 = candles[i]

        c1_open_time = int(c1[0])
        c2_open_time = int(c2[0])
        c3_open_time = int(c3[0])

        if c3_open_time <= last_processed:

            continue

        if c3_open_time <= search_start:

            continue

        # ====================================================
        # CANDLE 1
        # ====================================================

        c1_open = float(c1[1])
        c1_high = float(c1[2])
        c1_low = float(c1[3])
        c1_close = float(c1[4])

        if c1_close >= c1_open:

            continue

        # ====================================================
        # CANDLE 2
        # ====================================================

        c2_open = float(c2[1])
        c2_high = float(c2[2])
        c2_low = float(c2[3])
        c2_close = float(c2[4])

        if c2_close >= c2_open:

            continue

        body_low = min(
            c2_open,
            c2_close
        )

        body_high = max(
            c2_open,
            c2_close
        )

        body_size = abs(
            c2_open - c2_close
        )

        if body_size <= 0:

            continue

        # ====================================================
        # CANDLE 3
        # ====================================================

        c3_open = float(c3[1])
        c3_high = float(c3[2])
        c3_low = float(c3[3])
        c3_close = float(c3[4])

        # ====================================================
        # BEARISH FVG
        # ====================================================

        if c1_low <= c3_high:

            continue

        fvg_low = c3_high
        fvg_high = c1_low

        fvg_size = (
            fvg_high - fvg_low
        )

        if fvg_size <= 0:

            continue

        # ====================================================
        # FVG INSIDE CANDLE 2 BODY
        # ====================================================

        if fvg_low < body_low:

            continue

        if fvg_high > body_high:

            continue

        # ====================================================
        # FVG >= 50% OF CANDLE 2 BODY
        # ====================================================

        fvg_ratio = (
            fvg_size / body_size
        )

        if fvg_ratio < FVG_MIN_RATIO:

            continue

        # ====================================================
        # TARGET
        # ====================================================

        target = (
            c3_high
            * (1 - TARGET_PERCENT / 100)
        )

        return {
            "symbol": symbol,
            "interval": interval,

            "c1_open_time": c1_open_time,
            "c2_open_time": c2_open_time,
            "c3_open_time": c3_open_time,

            "c1_low": c1_low,

            "c2_open": c2_open,
            "c2_close": c2_close,

            "c3_high": c3_high,

            "body_low": body_low,
            "body_high": body_high,
            "body_size": body_size,

            "fvg_low": fvg_low,
            "fvg_high": fvg_high,
            "fvg_size": fvg_size,
            "fvg_ratio": fvg_ratio,

            "target": target
        }

    return None


# ============================================================
# FIND FVG
# 15M FIRST -> 1H SECOND
# ============================================================

def find_bearish_fvg(symbol):

    for interval in FVG_INTERVALS:

        fvg = find_new_bearish_fvg(
            symbol,
            interval
        )

        if fvg:

            return fvg

    return None


# ============================================================
# ACTIVATE FVG + TELEGRAM
# ============================================================

def activate_fvg(
    symbol,
    fvg,
    volume,
    trend
):

    active_fvgs[symbol] = fvg

    key = (
        symbol,
        fvg["interval"]
    )

    last_processed_fvg[key] = (
        fvg["c3_open_time"]
    )

    ratio_percent = (
        fvg["fvg_ratio"] * 100
    )

    message = (
        "🔻 BEARISH FVG FOUND & ACTIVE\n\n"

        f"Symbol: {symbol}\n"
        f"Timeframe: {fvg['interval']}\n\n"

        f"24H Volume: "
        f"${volume:,.0f} ✅\n\n"

        "1H TREND\n"
        f"Close: {trend['close']:.8f}\n"
        f"EMA20: {trend['ema20']:.8f}\n"
        f"EMA50: {trend['ema50']:.8f}\n"
        f"EMA100: {trend['ema100']:.8f}\n"
        "Bullish: ✅\n\n"

        "FVG CONDITIONS\n"
        "Candle 1 Bearish: ✅\n"
        "Candle 2 Bearish: ✅\n"
        "C1 Low > C3 High: ✅\n"
        "FVG inside C2 Body: ✅\n"
        f"FVG / C2 Body: "
        f"{ratio_percent:.2f}% ✅\n"
        "Candle 3 Closed: ✅\n\n"

        f"C2 Open: {fvg['c2_open']:.8f}\n"
        f"C2 Close: {fvg['c2_close']:.8f}\n\n"

        f"FVG Low: {fvg['fvg_low']:.8f}\n"
        f"FVG High: {fvg['fvg_high']:.8f}\n\n"

        f"Candle 3 High: "
        f"{fvg['c3_high']:.8f}\n"

        f"Target: "
        f"{fvg['target']:.8f}\n\n"

        "STATUS: ACTIVE 🔴"
    )

    success = send_telegram(
        message
    )

    if success:

        print(
            f"[FVG ACTIVE] {symbol} | "
            f"{fvg['interval']} | "
            f"Telegram sent"
        )

    else:

        print(
            f"[FVG ACTIVE] {symbol} | "
            f"{fvg['interval']} | "
            f"Telegram FAILED"
        )


# ============================================================
# RESET
# ============================================================

def reset_symbol_cycle(
    symbol,
    reason
):

    active_fvgs.pop(
        symbol,
        None
    )

    keys_to_delete = []

    for key in list(telegram_state.keys()):

        if (
            isinstance(key, tuple)
            and key[0] == symbol
        ):

            keys_to_delete.append(
                key
            )

    for key in keys_to_delete:

        telegram_state.pop(
            key,
            None
        )

    initialize_symbol_cycle(
        symbol
    )

    print(
        f"[RESET] {symbol} | "
        f"Reason={reason}"
    )


# ============================================================
# MONITOR ACTIVE FVG
# ============================================================

def monitor_active_fvg(symbol):

    fvg = active_fvgs.get(
        symbol
    )

    if not fvg:

        return

    current_price = get_current_price(
        symbol
    )

    if current_price is None:

        return

    target = fvg["target"]

    c3_high = fvg["c3_high"]

    print(
        f"[MONITOR] {symbol} | "
        f"Price={current_price:.8f} | "
        f"Target={target:.8f} | "
        f"C3 High={c3_high:.8f}"
    )

    # ========================================================
    # TARGET HIT
    # ========================================================

    if current_price <= target:

        message = (
            "🎯 BEARISH FVG TARGET HIT\n\n"

            f"Symbol: {symbol}\n"
            f"Timeframe: {fvg['interval']}\n\n"

            f"FVG Low: "
            f"{fvg['fvg_low']:.8f}\n"

            f"FVG High: "
            f"{fvg['fvg_high']:.8f}\n"

            f"Candle 3 High: "
            f"{c3_high:.8f}\n"

            f"Target: "
            f"{target:.8f}\n"

            f"Current Price: "
            f"{current_price:.8f}\n\n"

            f"Target: -{TARGET_PERCENT}%\n\n"

            "FVG STATUS: FINISHED ✅\n"
            "RESET: STARTING AGAIN"
        )

        send_telegram(
            message
        )

        reset_symbol_cycle(
            symbol,
            "TARGET HIT"
        )

        return

    # ========================================================
    # CANDLE 3 HIGH BROKEN
    # ========================================================

    if current_price > c3_high:

        message = (
            "❌ BEARISH FVG CANCELLED\n\n"

            f"Symbol: {symbol}\n"
            f"Timeframe: {fvg['interval']}\n\n"

            f"Current Price: "
            f"{current_price:.8f}\n"

            f"Candle 3 High: "
            f"{c3_high:.8f}\n\n"

            "Reason:\n"
            "Price broke above Candle 3 High.\n\n"

            "FVG STATUS: CANCELLED ❌\n"
            "RESET: STARTING AGAIN"
        )

        send_telegram(
            message
        )

        reset_symbol_cycle(
            symbol,
            "CANDLE 3 HIGH BROKEN"
        )

        return


# ============================================================
# PROCESS SYMBOL
# ============================================================

def process_symbol(
    symbol,
    volume
):

    # ========================================================
    # STEP 1
    # $20M QUALIFICATION
    # ========================================================

    if symbol not in qualified_symbols:

        qualified_symbols.add(
            symbol
        )

        initialize_symbol_cycle(
            symbol
        )

        message = (
            "💰 $20M VOLUME FILTER PASSED\n\n"

            f"Symbol: {symbol}\n"
            f"24H Quote Volume: "
            f"${volume:,.0f} ✅\n\n"

            "NEXT STEP:\n"
            "Checking 1H bullish trend..."
        )

        send_telegram(
            message
        )

        print(
            f"[NEW $20M COIN] "
            f"{symbol}"
        )

    # ========================================================
    # STEP 2
    # ACTIVE FVG?
    # ========================================================

    if symbol in active_fvgs:

        monitor_active_fvg(
            symbol
        )

        return

    # ========================================================
    # STEP 3
    # 1H BULLISH TREND
    # ========================================================

    trend = get_bullish_trend_data(
        symbol
    )

    if not trend:

        return

    trend_key = (
        symbol,
        "TREND"
    )

    if not trend["bullish"]:

        if telegram_state.get(
            trend_key
        ) != "NOT_BULLISH":

            send_telegram(
                "⏳ 1H BULLISH TREND NOT CONFIRMED\n\n"
                f"Symbol: {symbol}\n\n"
                f"Close: {trend['close']:.8f}\n"
                f"EMA20: {trend['ema20']:.8f}\n"
                f"EMA50: {trend['ema50']:.8f}\n"
                f"EMA100: {trend['ema100']:.8f}\n\n"
                "Condition:\n"
                "Close > EMA20 > EMA50 > EMA100\n\n"
                "Status: WAITING FOR BULLISH TREND ⏳"
            )

            telegram_state[
                trend_key
            ] = "NOT_BULLISH"

        return

    # ========================================================
    # BULLISH TREND CONFIRMED
    # ========================================================

    if telegram_state.get(
        trend_key
    ) != "BULLISH":

        send_telegram(
            "📈 1H BULLISH TREND CONFIRMED\n\n"
            f"Symbol: {symbol}\n\n"
            f"Close: {trend['close']:.8f}\n"
            f"EMA20: {trend['ema20']:.8f}\n"
            f"EMA50: {trend['ema50']:.8f}\n"
            f"EMA100: {trend['ema100']:.8f}\n\n"
            "Close > EMA20 > EMA50 > EMA100 ✅\n\n"
            "NEXT STEP:\n"
            "Searching for NEW Bearish FVG..."
        )

        telegram_state[
            trend_key
        ] = "BULLISH"

    # ========================================================
    # STEP 4
    # SEARCH BEARISH FVG
    # ========================================================

    fvg = find_bearish_fvg(
        symbol
    )

    if not fvg:

        return

    # ========================================================
    # STEP 5
    # FVG FOUND
    # ========================================================

    activate_fvg(
        symbol,
        fvg,
        volume,
        trend
    )


# ============================================================
# CLEANUP
# ============================================================

def cleanup_symbols(
    current_qualified
):

    current_set = set(
        current_qualified
    )

    removed = (
        qualified_symbols
        - current_set
    )

    for symbol in removed:

        print(
            f"[REMOVED < $20M] "
            f"{symbol}"
        )

        if symbol in active_fvgs:

            continue

        qualified_symbols.discard(
            symbol
        )

        for interval in FVG_INTERVALS:

            fvg_search_start.pop(
                (symbol, interval),
                None
            )

            last_processed_fvg.pop(
                (symbol, interval),
                None
            )

        keys_to_delete = []

        for key in list(telegram_state.keys()):

            if (
                isinstance(key, tuple)
                and key[0] == symbol
            ):

                keys_to_delete.append(
                    key
                )

        for key in keys_to_delete:

            telegram_state.pop(
                key,
                None
            )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("BINANCE SPOT BEARISH FVG ALERT BOT")
    print("=" * 70)

    print(
        f"Minimum 24H Volume: "
        f"${MIN_QUOTE_VOLUME_24H:,.0f}"
    )

    print(
        f"FVG Minimum Ratio: "
        f"{FVG_MIN_RATIO * 100:.0f}%"
    )

    print(
        f"Target: "
        f"{TARGET_PERCENT}% below Candle 3 High"
    )

    print(
        "FVG Priority: 15M -> 1H"
    )

    print("=" * 70)

    # ========================================================
    # TELEGRAM TEST
    # ========================================================

    telegram_ok = test_telegram()

    if not telegram_ok:

        print(
            "\n"
            "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
            "TELEGRAM CONNECTION FAILED\n"
            "Check Railway Variables:\n"
            "TELEGRAM_BOT_TOKEN\n"
            "TELEGRAM_CHAT_ID\n"
            "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
        )

    # ========================================================
    # START MESSAGE
    # ========================================================

    if telegram_ok:

        send_telegram(
            "🤖 BEARISH FVG BOT STARTED\n\n"
            f"Volume Filter: ${MIN_QUOTE_VOLUME_24H:,.0f}+\n"
            "1H Trend: Close > EMA20 > EMA50 > EMA100\n"
            "FVG: Bearish only\n"
            "FVG Priority: 15M -> 1H\n"
            "FVG inside Candle 2 Body: YES\n"
            "Minimum FVG / C2 Body: 50%\n"
            f"Target: -{TARGET_PERCENT}% from Candle 3 High\n\n"
            "Status: RUNNING 🟢"
        )

    # ========================================================
    # MAIN LOOP
    # ========================================================

    while True:

        try:

            qualified = (
                get_qualified_symbols()
            )

            if not qualified:

                print(
                    "[NO $20M+ SYMBOLS]"
                )

                time.sleep(
                    SCAN_INTERVAL_SECONDS
                )

                continue

            cleanup_symbols(
                qualified
            )

            print(
                "\n"
                + "=" * 70
            )

            print(
                f"[SCAN] "
                f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )

            print(
                f"Qualified symbols: "
                f"{len(qualified)}"
            )

            print(
                "=" * 70
            )

            for symbol in qualified:

                try:

                    volume = get_24h_volume(
                        symbol
                    )

                    if volume is None:

                        continue

                    if volume < MIN_QUOTE_VOLUME_24H:

                        continue

                    process_symbol(
                        symbol,
                        volume
                    )

                except Exception as e:

                    print(
                        f"[SYMBOL ERROR] "
                        f"{symbol}: {e}"
                    )

                time.sleep(
                    0.05
                )

        except Exception as e:

            print(
                f"[MAIN LOOP ERROR] {e}"
            )

        time.sleep(
            SCAN_INTERVAL_SECONDS
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
