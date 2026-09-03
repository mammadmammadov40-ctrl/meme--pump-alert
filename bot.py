import os
import time
import requests
from datetime import datetime, timezone


# ============================================================
# BINANCE SPOT BEAR FVG ALERT BOT
#
# STRATEGY
#
# 1) Binance Spot USDT pairs
# 2) CoinMarketCap Market Cap > $300M
# 3) 1H trend:
#       Close > EMA20 > EMA50 > EMA100
# 4) First search 15M Bear FVG
#    If no valid 15M FVG -> search 1H Bear FVG
# 5) Bear FVG:
#       Candle 1 Low > Candle 3 High
#       Candle 1 is bearish
#       Candle 2 is bearish
#       FVG completely inside Candle 2 Open/Close body
#       FVG size >= 50% of Candle 2 body
# 6) FVG becomes active
# 7) Active FVG:
#       Price <= 3rd candle High - 1.7%
#           -> Telegram signal
#
#       Price > 3rd candle High
#           -> FVG CANCEL
#
# 8) After TARGET or CANCEL:
#       Forget old FVG
#       Return to 1H trend
#       Search for a NEW FVG
# 9) Scan every 60 seconds
# 10) CMC refresh every 5 minutes
# ============================================================


# ============================================================
# API SETTINGS
# ============================================================

CMC_API_KEY = os.getenv("CMC_API_KEY")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

BINANCE_BASE_URL = "https://api.binance.com"


# ============================================================
# STRATEGY SETTINGS
# ============================================================

MIN_MARKET_CAP = 300_000_000

FVG_MIN_RATIO = 0.50

TARGET_DROP_PERCENT = 1.7

SCAN_INTERVAL = 60

CMC_CACHE_SECONDS = 5 * 60


# ============================================================
# BOT STATE
# ============================================================

# One active FVG per symbol
active_fvgs = {}

# Last processed FVG formation time.
#
# This is NOT an old-FVG blacklist.
# It only prevents the exact same already-processed
# candle formation from being activated again.
#
# Key:
#     (symbol, interval)
#
# Value:
#     3rd candle open time
last_processed_fvg = {}


# CMC cache
market_cap_cache = {}
market_cap_cache_time = 0


# Binance symbols cache
binance_symbols_cache = []
binance_symbols_cache_time = 0

SYMBOL_CACHE_SECONDS = 60 * 60


# ============================================================
# SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "Bear-FVG-Bot/1.0"
})


# ============================================================
# TIME
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def format_time(timestamp_ms):
    dt = datetime.fromtimestamp(
        timestamp_ms / 1000,
        tz=timezone.utc
    )

    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials missing.")
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
        response = session.post(
            url,
            json=payload,
            timeout=15
        )

        response.raise_for_status()

        return True

    except Exception as e:
        print(f"Telegram error: {e}")
        return False


# ============================================================
# BINANCE REQUEST
# ============================================================

def binance_get(endpoint, params=None):

    url = BINANCE_BASE_URL + endpoint

    try:
        response = session.get(
            url,
            params=params,
            timeout=15
        )

        response.raise_for_status()

        return response.json()

    except Exception as e:
        print(f"Binance error [{endpoint}]: {e}")
        return None


# ============================================================
# BINANCE SPOT USDT SYMBOLS
# ============================================================

def get_binance_spot_symbols():

    global binance_symbols_cache
    global binance_symbols_cache_time

    current_time = time.time()

    if (
        binance_symbols_cache
        and current_time - binance_symbols_cache_time
        < SYMBOL_CACHE_SECONDS
    ):
        return binance_symbols_cache

    data = binance_get("/api/v3/exchangeInfo")

    if not data:
        return binance_symbols_cache

    symbols = []

    for item in data.get("symbols", []):

        if item.get("status") != "TRADING":
            continue

        if item.get("quoteAsset") != "USDT":
            continue

        if not item.get("isSpotTradingAllowed", False):
            continue

        symbol = item.get("symbol")
        base_asset = item.get("baseAsset")

        if not symbol or not base_asset:
            continue

        symbols.append({
            "symbol": symbol,
            "base_asset": base_asset
        })

    binance_symbols_cache = symbols
    binance_symbols_cache_time = current_time

    print(f"Binance Spot USDT pairs: {len(symbols)}")

    return symbols


# ============================================================
# COINMARKETCAP
# ============================================================

def get_market_caps():

    global market_cap_cache
    global market_cap_cache_time

    current_time = time.time()

    # 5 minute cache
    if (
        market_cap_cache
        and current_time - market_cap_cache_time
        < CMC_CACHE_SECONDS
    ):
        return market_cap_cache

    if not CMC_API_KEY:
        print("CMC_API_KEY missing.")
        return market_cap_cache

    url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"

    headers = {
        "X-CMC_PRO_API_KEY": CMC_API_KEY
    }

    params = {
        "start": 1,
        "limit": 5000,
        "convert": "USD"
    }

    try:

        response = session.get(
            url,
            headers=headers,
            params=params,
            timeout=30
        )

        if response.status_code == 429:
            print("CMC rate limit. Keeping old cache.")
            return market_cap_cache

        response.raise_for_status()

        data = response.json()

        new_cache = {}

        for coin in data.get("data", []):

            symbol = coin.get("symbol")
            quote = coin.get("quote", {})
            usd = quote.get("USD", {})

            market_cap = usd.get("market_cap")

            if not symbol or market_cap is None:
                continue

            # If duplicate symbols exist, keep the largest market cap.
            if (
                symbol not in new_cache
                or market_cap > new_cache[symbol]
            ):
                new_cache[symbol] = market_cap

        if new_cache:

            market_cap_cache = new_cache
            market_cap_cache_time = current_time

            print(
                f"CMC updated: "
                f"{len(market_cap_cache)} coins"
            )

        return market_cap_cache

    except Exception as e:

        print(f"CMC error: {e}")

        return market_cap_cache


# ============================================================
# FILTER QUALIFIED COINS
# ============================================================

def get_qualified_symbols():

    binance_symbols = get_binance_spot_symbols()

    if not binance_symbols:
        return []

    market_caps = get_market_caps()

    if not market_caps:
        return []

    qualified = []

    for item in binance_symbols:

        symbol = item["symbol"]
        base_asset = item["base_asset"]

        market_cap = market_caps.get(base_asset)

        if market_cap is None:
            continue

        if market_cap > MIN_MARKET_CAP:

            qualified.append({
                "symbol": symbol,
                "base_asset": base_asset,
                "market_cap": market_cap
            })

    return qualified


# ============================================================
# KLINES
# ============================================================

def get_klines(symbol, interval, limit=100):

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

    return data


# ============================================================
# CLOSED KLINES
# ============================================================

def get_closed_klines(symbol, interval, limit=100):

    klines = get_klines(
        symbol,
        interval,
        limit
    )

    if len(klines) < 4:
        return []

    current_time_ms = int(time.time() * 1000)

    closed = []

    for candle in klines:

        open_time = candle[0]
        close_time = candle[6]

        # Only fully closed candles
        if close_time <= current_time_ms:
            closed.append(candle)

    return closed


# ============================================================
# EMA
# ============================================================

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


# ============================================================
# 1H TREND
#
# Uses ONLY closed 1H candles.
#
# Close > EMA20 > EMA50 > EMA100
# ============================================================

def check_1h_trend(symbol):

    klines = get_closed_klines(
        symbol,
        "1h",
        150
    )

    if len(klines) < 101:
        return False

    closes = [
        float(candle[4])
        for candle in klines
    ]

    current_close = closes[-1]

    ema20 = calculate_ema(
        closes,
        20
    )

    ema50 = calculate_ema(
        closes,
        50
    )

    ema100 = calculate_ema(
        closes,
        100
    )

    if (
        ema20 is None
        or ema50 is None
        or ema100 is None
    ):
        return False

    trend_ok = (
        current_close > ema20
        and ema20 > ema50
        and ema50 > ema100
    )

    return trend_ok


# ============================================================
# BEAR FVG DETECTION
#
# Three CLOSED candles:
#
# Candle 1
# Candle 2
# Candle 3
#
# Candle 1 must be bearish:
#     Close < Open
#
# Candle 2 must be bearish:
#     Close < Open
#
# Bear FVG:
#     Candle 1 Low > Candle 3 High
#
# FVG:
#     Low  = Candle 3 High
#     High = Candle 1 Low
#
# Candle 2 body:
#     Low  = Close
#     High = Open
#
# FVG must be completely inside body.
#
# FVG size >= 50% of Candle 2 body.
#
# Only the NEWEST valid FVG is returned.
# ============================================================

def find_new_bearish_fvg(
    symbol,
    interval
):

    klines = get_closed_klines(
        symbol,
        interval,
        100
    )

    if len(klines) < 3:
        return None

    last_processed = last_processed_fvg.get(
        (symbol, interval),
        0
    )

    # Newest -> oldest
    for i in range(
        len(klines) - 3,
        -1,
        -1
    ):

        c1 = klines[i]
        c2 = klines[i + 1]
        c3 = klines[i + 2]

        # ====================================================
        # Candle timestamps
        # ====================================================

        c3_open_time = c3[0]

        # Do not process an already processed formation.
        if c3_open_time <= last_processed:
            continue

        # ====================================================
        # Candle 1
        # ====================================================

        c1_open = float(c1[1])
        c1_close = float(c1[4])
        c1_low = float(c1[3])

        # Candle 1 must close down
        if c1_close >= c1_open:
            continue

        # ====================================================
        # Candle 2
        # ====================================================

        c2_open = float(c2[1])
        c2_close = float(c2[4])

        # Candle 2 must close down
        if c2_close >= c2_open:
            continue

        # Body = Open to Close ONLY
        body_low = min(
            c2_open,
            c2_close
        )

        body_high = max(
            c2_open,
            c2_close
        )

        body_size = body_high - body_low

        if body_size <= 0:
            continue

        # ====================================================
        # Candle 3
        # ====================================================

        c3_high = float(c3[2])

        # ====================================================
        # Bear FVG
        #
        # Candle 1 Low > Candle 3 High
        # ====================================================

        if c1_low <= c3_high:
            continue

        fvg_low = c3_high
        fvg_high = c1_low

        fvg_size = fvg_high - fvg_low

        if fvg_size <= 0:
            continue

        # ====================================================
        # FVG must be completely inside Candle 2 body
        # ====================================================

        if fvg_low < body_low:
            continue

        if fvg_high > body_high:
            continue

        # ====================================================
        # FVG must be at least 50% of Candle 2 body
        # ====================================================

        fvg_ratio = fvg_size / body_size

        if fvg_ratio < FVG_MIN_RATIO:
            continue

        # ====================================================
        # Target
        #
        # 1.7% below Candle 3 High
        # ====================================================

        target_price = (
            c3_high
            * (1 - TARGET_DROP_PERCENT / 100)
        )

        return {
            "symbol": symbol,
            "interval": interval,

            "candle1_time": c1[0],
            "candle2_time": c2[0],
            "candle3_time": c3[0],

            "candle1_low": c1_low,

            "candle2_open": c2_open,
            "candle2_close": c2_close,
            "candle2_body": body_size,

            "candle3_high": c3_high,

            "fvg_low": fvg_low,
            "fvg_high": fvg_high,
            "fvg_size": fvg_size,
            "fvg_ratio": fvg_ratio,

            "target": target_price,

            "activated_at": time.time()
        }

    return None


# ============================================================
# SEARCH NEW FVG
#
# First 15M.
# If no NEW valid 15M FVG:
#     search 1H.
#
# If both have valid NEW FVGs, use the more recent
# formation. If formation times are equal, 15M wins.
# ============================================================

def search_new_fvg(symbol):

    fvg_15m = find_new_bearish_fvg(
        symbol,
        "15m"
    )

    fvg_1h = find_new_bearish_fvg(
        symbol,
        "1h"
    )

    if fvg_15m and fvg_1h:

        if fvg_15m["candle3_time"] >= fvg_1h["candle3_time"]:
            return fvg_15m

        return fvg_1h

    if fvg_15m:
        return fvg_15m

    if fvg_1h:
        return fvg_1h

    return None


# ============================================================
# GET CURRENT PRICE
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
        return float(data["price"])

    except Exception:
        return None


# ============================================================
# ACTIVATE FVG
# ============================================================

def activate_fvg(fvg):

    symbol = fvg["symbol"]
    interval = fvg["interval"]

    active_fvgs[symbol] = fvg

    # Remember only the formation timestamp.
    # This prevents the exact same FVG from being
    # activated again after TARGET/CANCEL.
    last_processed_fvg[
        (symbol, interval)
    ] = fvg["candle3_time"]

    print()
    print("==========================================")
    print("FVG ACTIVATED")
    print("==========================================")
    print(f"Symbol      : {symbol}")
    print(f"Timeframe   : {interval}")
    print(
        f"Candle 3    : "
        f"{format_time(fvg['candle3_time'])}"
    )
    print(
        f"Candle 3 High: "
        f"{fvg['candle3_high']}"
    )
    print(
        f"FVG         : "
        f"{fvg['fvg_low']} - {fvg['fvg_high']}"
    )
    print(
        f"FVG Size    : "
        f"{fvg['fvg_size']}"
    )
    print(
        f"Body Size   : "
        f"{fvg['candle2_body']}"
    )
    print(
        f"FVG Ratio   : "
        f"{fvg['fvg_ratio'] * 100:.2f}%"
    )
    print(
        f"Target      : "
        f"{fvg['target']}"
    )
    print("==========================================")
    print()


# ============================================================
# FINISH / FORGET ACTIVE FVG
# ============================================================

def finish_fvg(symbol):

    if symbol in active_fvgs:
        del active_fvgs[symbol]

    print(
        f"{symbol}: Active FVG finished. "
        f"Returning to 1H trend."
    )


# ============================================================
# MONITOR ACTIVE FVG
#
# IMPORTANT:
# If active FVG exists:
#     NO NEW FVG SEARCH
#
# Only:
#     TARGET
#     OR
#     CANCEL
# ============================================================

def monitor_active_fvg(symbol):

    fvg = active_fvgs.get(symbol)

    if not fvg:
        return

    price = get_current_price(symbol)

    if price is None:
        return

    target = fvg["target"]
    third_high = fvg["candle3_high"]

    # ========================================================
    # TARGET
    # Price reached 1.7% below Candle 3 High
    # ========================================================

    if price <= target:

        message = (
            "🔴 BEAR FVG SIGNAL\n\n"
            f"Symbol: {symbol}\n"
            f"Timeframe: {fvg['interval']}\n\n"
            f"3rd Candle High: {third_high}\n"
            f"Target (-1.7%): {target}\n"
            f"Current Price: {price}\n\n"
            "Bear FVG target reached."
        )

        print()
        print(message)
        print()

        send_telegram(message)

        # FVG is now finished.
        finish_fvg(symbol)

        return

    # ========================================================
    # CANCEL
    #
    # Price crossed above Candle 3 High
    # ========================================================

    if price > third_high:

        print()
        print(
            f"{symbol}: FVG CANCELLED"
        )
        print(
            f"Price {price} > "
            f"3rd candle High {third_high}"
        )
        print()

        finish_fvg(symbol)

        return


# ============================================================
# PROCESS ONE COIN
# ============================================================

def process_symbol(item):

    symbol = item["symbol"]

    # ========================================================
    # ACTIVE FVG EXISTS
    #
    # DO NOT CHECK TREND
    # DO NOT SEARCH NEW FVG
    #
    # ONLY MONITOR CURRENT FVG
    # ========================================================

    if symbol in active_fvgs:

        monitor_active_fvg(symbol)

        return

    # ========================================================
    # NO ACTIVE FVG
    #
    # Return to 1H trend
    # ========================================================

    trend_ok = check_1h_trend(symbol)

    if not trend_ok:
        return

    # ========================================================
    # 1H trend is valid.
    #
    # Search NEW FVG.
    # ========================================================

    fvg = search_new_fvg(symbol)

    if not fvg:
        return

    # ========================================================
    # Activate only after all FVG conditions passed.
    # ========================================================

    activate_fvg(fvg)


# ============================================================
# MARKET SCAN
# ============================================================

def market_scan():

    print()
    print("==========================================")
    print(
        f"MARKET SCAN "
        f"{now_utc().strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )
    print("==========================================")

    qualified = get_qualified_symbols()

    print(
        f"Qualified coins (> $300M): "
        f"{len(qualified)}"
    )

    if not qualified:
        print("No qualified coins.")
        return

    for index, item in enumerate(
        qualified,
        start=1
    ):

        symbol = item["symbol"]
        market_cap = item["market_cap"]

        try:

            print(
                f"[{index}/{len(qualified)}] "
                f"{symbol} | "
                f"MC ${market_cap:,.0f}"
            )

            process_symbol(item)

        except Exception as e:

            print(
                f"{symbol}: processing error: {e}"
            )

        # Small pause to reduce API pressure
        time.sleep(0.05)


# ============================================================
# STARTUP
# ============================================================

def startup():

    print()
    print("==========================================")
    print("BINANCE SPOT BEAR FVG BOT")
    print("==========================================")
    print()
    print("Conditions:")
    print()
    print("1. Binance Spot USDT")
    print("2. CMC Market Cap > $300M")
    print("3. 1H: Close > EMA20 > EMA50 > EMA100")
    print("4. Search 15M Bear FVG first")
    print("5. If no 15M -> search 1H Bear FVG")
    print("6. Three candles must be CLOSED")
    print("7. Candle 1 must be bearish")
    print("8. Candle 2 must be bearish")
    print("9. Candle 1 Low > Candle 3 High")
    print("10. FVG inside Candle 2 Open/Close body")
    print("11. FVG >= 50% of Candle 2 body")
    print("12. Target = 1.7% below Candle 3 High")
    print("13. Candle 3 High crossed -> CANCEL")
    print("14. One active FVG per coin")
    print("15. Active FVG blocks new FVG searches")
    print("16. After Target/Cancel -> return to 1H trend")
    print("17. Scan every 60 seconds")
    print("18. CMC refresh every 5 minutes")
    print()
    print("No signal cooldown.")
    print("No RSI / MACD / Volume filters.")
    print("==========================================")
    print()

    send_telegram(
        "🟢 Bear FVG Bot started.\n\n"
        "Binance Spot USDT\n"
        "CMC Market Cap > $300M\n"
        "1H Trend: Close > EMA20 > EMA50 > EMA100\n"
        "15M FVG → 1H FVG fallback\n"
        "Bear FVG >= 50% of Candle 2 body\n"
        "Target: -1.7%\n"
        "Scan: 60 seconds\n"
        "CMC: 5 minutes"
    )


# ============================================================
# MAIN LOOP
# ============================================================

def main():

    startup()

    last_scan = 0

    while True:

        try:

            current_time = time.time()

            if (
                current_time - last_scan
                >= SCAN_INTERVAL
            ):

                market_scan()

                last_scan = current_time

            time.sleep(1)

        except KeyboardInterrupt:

            print()
            print("Bot stopped.")
            break

        except Exception as e:

            print(
                f"Main loop error: {e}"
            )

            time.sleep(5)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
