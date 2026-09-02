import os
import time
import requests
from datetime import datetime
from zoneinfo import ZoneInfo


# ============================================================
# BINANCE SPOT FVG ALERT BOT
#
# MARKET CAP > $300M
#
# 1H TREND:
# CLOSE > EMA20 > EMA50 > EMA100
#
# FVG:
# 1) FIRST SEARCH 15M BEARISH FVG
# 2) IF NO 15M FVG -> SEARCH 1H BEARISH FVG
#
# FVG MUST BE INSIDE 2ND CANDLE BODY
# FVG SIZE >= 50% OF 2ND CANDLE BODY
#
# TARGET = 3% BELOW 2ND CANDLE CLOSE
#
# ACTIVE FVG:
# - TARGET HIT -> SIGNAL -> FVG FINISHED
# - PRICE RETURNS TO 2ND CLOSE -> CANCEL FVG
#
# WHILE FVG IS ACTIVE:
# NEW FVGs ARE IGNORED
#
# AFTER FVG FINISHES:
# GO BACK TO 1H TREND
# THEN SEARCH 15M FVG
# IF NONE -> SEARCH 1H FVG
#
# ALERT ONLY
# NO AUTOMATIC ORDERS
# ============================================================


# ============================================================
# API SETTINGS
# ============================================================

BINANCE_URL = "https://api.binance.com"
CMC_URL = "https://pro-api.coinmarketcap.com"

CMC_API_KEY = os.getenv("CMC_API_KEY")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# ============================================================
# GENERAL SETTINGS
# ============================================================

AZ_TZ = ZoneInfo("Asia/Baku")

SCAN_INTERVAL = 60

MIN_MARKET_CAP = 300_000_000

EMA_FAST = 20
EMA_MIDDLE = 50
EMA_SLOW = 100

INTERVAL_1H = "1h"
INTERVAL_15M = "15m"

KLINE_LIMIT_1H = 150
KLINE_LIMIT_15M = 100

FVG_MIN_RATIO = 0.50

DROP_PERCENT = 3.0

# Same coin cannot repeatedly alert immediately
SIGNAL_COOLDOWN = 24 * 60 * 60


# ============================================================
# SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json"
})


# ============================================================
# STATE
# ============================================================

# Only ONE active FVG per coin
active_fvgs = {}

# Last completed/cancelled FVG
# Used to prevent old FVG from being detected again
last_finished_fvg = {}

# Signal cooldown
last_signal_time = {}


# ============================================================
# LOG
# ============================================================

def log(message):

    now = datetime.now(AZ_TZ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    print(
        f"[{now}] {message}",
        flush=True
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):
        log("Telegram credentials missing.")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:

        response = session.post(
            url,
            json=payload,
            timeout=10
        )

        if response.status_code == 200:
            return True

        log(
            f"Telegram error: "
            f"{response.status_code} "
            f"{response.text[:300]}"
        )

    except Exception as e:

        log(
            f"Telegram exception: {e}"
        )

    return False


# ============================================================
# BINANCE EXCHANGE INFO
# ============================================================

def get_binance_symbols():

    url = (
        f"{BINANCE_URL}/api/v3/exchangeInfo"
    )

    try:

        response = session.get(
            url,
            timeout=15
        )

        response.raise_for_status()

        data = response.json()

        symbols = []

        for item in data.get(
            "symbols",
            []
        ):

            if item.get("status") != "TRADING":
                continue

            if item.get("quoteAsset") != "USDT":
                continue

            if not item.get(
                "isSpotTradingAllowed"
            ):
                continue

            symbol = item.get("symbol")
            base_asset = item.get("baseAsset")

            if not symbol or not base_asset:
                continue

            # Leveraged tokens excluded
            if (
                base_asset.endswith("UP")
                or base_asset.endswith("DOWN")
                or base_asset.endswith("BULL")
                or base_asset.endswith("BEAR")
            ):
                continue

            symbols.append({
                "symbol": symbol,
                "base": base_asset
            })

        log(
            f"Binance USDT Spot symbols: "
            f"{len(symbols)}"
        )

        return symbols

    except Exception as e:

        log(
            f"ExchangeInfo error: {e}"
        )

        return []


# ============================================================
# COINMARKETCAP MARKET CAPS
# ============================================================

def get_market_caps():

    if not CMC_API_KEY:

        log(
            "CMC_API_KEY is missing."
        )

        return {}

    url = (
        f"{CMC_URL}/v1/cryptocurrency/"
        f"listings/latest"
    )

    headers = {
        "X-CMC_PRO_API_KEY": CMC_API_KEY,
        "Accept": "application/json"
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

        response.raise_for_status()

        data = response.json()

        result = {}

        for coin in data.get(
            "data",
            []
        ):

            symbol = coin.get("symbol")

            quote = coin.get(
                "quote",
                {}
            ).get(
                "USD",
                {}
            )

            market_cap = quote.get(
                "market_cap"
            )

            if not symbol or not market_cap:
                continue

            # Keep highest market cap if
            # duplicate symbol exists
            if (
                symbol not in result
                or market_cap > result[symbol]
            ):

                result[symbol] = float(
                    market_cap
                )

        log(
            f"CoinMarketCap coins loaded: "
            f"{len(result)}"
        )

        return result

    except Exception as e:

        log(
            f"CoinMarketCap error: {e}"
        )

        return {}


# ============================================================
# BINANCE KLINES
# ============================================================

def get_klines(
    symbol,
    interval,
    limit
):

    url = (
        f"{BINANCE_URL}/api/v3/klines"
    )

    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }

    try:

        response = session.get(
            url,
            params=params,
            timeout=10
        )

        response.raise_for_status()

        return response.json()

    except Exception as e:

        log(
            f"Kline error {symbol} "
            f"{interval}: {e}"
        )

        return []


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
# 1H TREND
# ============================================================

def check_1h_bullish(symbol):

    candles = get_klines(
        symbol,
        INTERVAL_1H,
        KLINE_LIMIT_1H
    )

    if len(candles) < EMA_SLOW + 5:
        return None

    # Ignore currently forming candle
    closed = candles[:-1]

    closes = [
        float(c[4])
        for c in closed
    ]

    if len(closes) < EMA_SLOW:
        return None

    ema20 = calculate_ema(
        closes,
        EMA_FAST
    )

    ema50 = calculate_ema(
        closes,
        EMA_MIDDLE
    )

    ema100 = calculate_ema(
        closes,
        EMA_SLOW
    )

    if (
        ema20 is None
        or ema50 is None
        or ema100 is None
    ):
        return None

    last_close = closes[-1]

    bullish = (
        last_close > ema20
        and ema20 > ema50
        and ema50 > ema100
    )

    return {

        "bullish": bullish,

        "close": last_close,

        "ema20": ema20,

        "ema50": ema50,

        "ema100": ema100
    }


# ============================================================
# BEARISH FVG DETECTION
#
# IMPORTANT:
# We search from newest to oldest.
#
# But ONLY return an FVG that is newer than
# last_finished_fvg for this symbol/timeframe.
#
# This prevents the bot from repeatedly taking
# an old FVG.
# ============================================================

def find_bearish_fvg(
    symbol,
    interval,
    limit
):

    candles = get_klines(
        symbol,
        interval,
        limit
    )

    if len(candles) < 10:
        return None

    # Only closed candles
    closed = candles[:-1]

    # Last completed FVG for this timeframe
    finished_time = last_finished_fvg.get(
        (symbol, interval)
    )

    # Search newest formation first
    for i in range(
        len(closed) - 3,
        -1,
        -1
    ):

        c1 = closed[i]
        c2 = closed[i + 1]
        c3 = closed[i + 2]

        c1_time = int(c1[0])
        c2_time = int(c2[0])
        c3_time = int(c3[0])

        # ----------------------------------------------------
        # Do not use an FVG that was already finished
        # ----------------------------------------------------

        if (
            finished_time is not None
            and c3_time <= finished_time
        ):
            continue

        c1_low = float(c1[3])

        c2_open = float(c2[1])
        c2_close = float(c2[4])

        c3_high = float(c3[2])

        # ----------------------------------------------------
        # BEARISH FVG
        #
        # Candle 1 LOW > Candle 3 HIGH
        # ----------------------------------------------------

        if not (
            c1_low > c3_high
        ):
            continue

        fvg_low = c3_high
        fvg_high = c1_low

        fvg_size = (
            fvg_high - fvg_low
        )

        if fvg_size <= 0:
            continue

        # ----------------------------------------------------
        # 2ND CANDLE BODY
        # ----------------------------------------------------

        body_low = min(
            c2_open,
            c2_close
        )

        body_high = max(
            c2_open,
            c2_close
        )

        body_size = (
            body_high - body_low
        )

        if body_size <= 0:
            continue

        # ----------------------------------------------------
        # FVG MUST BE COMPLETELY INSIDE BODY
        # ----------------------------------------------------

        inside_body = (
            fvg_low >= body_low
            and
            fvg_high <= body_high
        )

        if not inside_body:
            continue

        # ----------------------------------------------------
        # FVG >= 50% OF BODY
        # ----------------------------------------------------

        fvg_ratio = (
            fvg_size / body_size
        )

        if (
            fvg_ratio
            < FVG_MIN_RATIO
        ):
            continue

        # ----------------------------------------------------
        # TARGET = 3% BELOW 2ND CANDLE CLOSE
        # ----------------------------------------------------

        target_price = (
            c2_close
            * (
                1
                - DROP_PERCENT / 100
            )
        )

        return {

            "symbol": symbol,

            "interval": interval,

            "candle1_time": c1_time,

            "candle2_time": c2_time,

            "candle3_time": c3_time,

            "candle1_low": c1_low,

            "candle2_open": c2_open,

            "candle2_close": c2_close,

            "candle3_high": c3_high,

            "fvg_low": fvg_low,

            "fvg_high": fvg_high,

            "fvg_size": fvg_size,

            "body_low": body_low,

            "body_high": body_high,

            "body_size": body_size,

            "fvg_ratio": fvg_ratio,

            "target_price": target_price,

            "created_at": time.time()
        }

    return None


# ============================================================
# FIND FVG
#
# PRIORITY:
# 1. 15M
# 2. If no 15M -> 1H
# ============================================================

def find_best_fvg(symbol):

    # --------------------------------------------------------
    # FIRST: 15M FVG
    # --------------------------------------------------------

    fvg_15m = find_bearish_fvg(
        symbol,
        INTERVAL_15M,
        KLINE_LIMIT_15M
    )

    if fvg_15m:

        return fvg_15m

    # --------------------------------------------------------
    # SECOND: 1H FVG
    # --------------------------------------------------------

    fvg_1h = find_bearish_fvg(
        symbol,
        INTERVAL_1H,
        KLINE_LIMIT_1H
    )

    if fvg_1h:

        return fvg_1h

    return None


# ============================================================
# CURRENT PRICE
# ============================================================

def get_current_price(symbol):

    url = (
        f"{BINANCE_URL}/api/v3/ticker/price"
    )

    params = {
        "symbol": symbol
    }

    try:

        response = session.get(
            url,
            params=params,
            timeout=5
        )

        response.raise_for_status()

        data = response.json()

        return float(
            data["price"]
        )

    except Exception:

        return None


# ============================================================
# SIGNAL COOLDOWN
# ============================================================

def signal_allowed(symbol):

    last = last_signal_time.get(
        symbol
    )

    if last is None:
        return True

    return (
        time.time() - last
        >= SIGNAL_COOLDOWN
    )


# ============================================================
# SEND SIGNAL
# ============================================================

def send_signal(
    fvg,
    market_cap
):

    symbol = fvg["symbol"]

    if not signal_allowed(
        symbol
    ):

        log(
            f"{symbol}: "
            f"signal cooldown active."
        )

        return

    price = get_current_price(
        symbol
    )

    if price is None:
        return

    target = fvg[
        "target_price"
    ]

    timeframe = fvg[
        "interval"
    ]

    message = (

        "🚨 <b>FVG SIGNAL</b>\n\n"

        f"🪙 <b>{symbol}</b>\n"

        f"💰 Market Cap: "
        f"${market_cap:,.0f}\n\n"

        "📊 <b>1H TREND</b>\n"

        f"Close: "
        f"{fvg.get('trend_close', 0):.8f}\n"

        f"EMA20: "
        f"{fvg.get('ema20', 0):.8f}\n"

        f"EMA50: "
        f"{fvg.get('ema50', 0):.8f}\n"

        f"EMA100: "
        f"{fvg.get('ema100', 0):.8f}\n\n"

        f"📉 <b>{timeframe} "
        f"BEARISH FVG</b>\n"

        f"FVG: "
        f"{fvg['fvg_low']:.8f} - "
        f"{fvg['fvg_high']:.8f}\n"

        f"2nd Open: "
        f"{fvg['candle2_open']:.8f}\n"

        f"2nd Close: "
        f"{fvg['candle2_close']:.8f}\n"

        f"FVG/Body: "
        f"{fvg['fvg_ratio'] * 100:.2f}%\n\n"

        f"🎯 <b>3% TARGET:</b> "
        f"{target:.8f}\n"

        f"💵 Current Price: "
        f"{price:.8f}\n\n"

        "⚠️ <b>ALERT ONLY</b>\n"

        "No automatic order."
    )

    if send_telegram(
        message
    ):

        last_signal_time[
            symbol
        ] = time.time()

        log(
            f"🚨 SIGNAL SENT: "
            f"{symbol}"
        )


# ============================================================
# ACTIVATE FVG
# ============================================================

def activate_fvg(
    fvg,
    trend
):

    symbol = fvg[
        "symbol"
    ]

    fvg["trend_close"] = (
        trend["close"]
    )

    fvg["ema20"] = (
        trend["ema20"]
    )

    fvg["ema50"] = (
        trend["ema50"]
    )

    fvg["ema100"] = (
        trend["ema100"]
    )

    # Only ONE active FVG per coin
    active_fvgs[
        symbol
    ] = fvg

    log(
        f"{symbol}: "
        f"NEW {fvg['interval']} FVG | "
        f"FVG="
        f"{fvg['fvg_low']:.8f}-"
        f"{fvg['fvg_high']:.8f} | "
        f"Body="
        f"{fvg['body_size']:.8f} | "
        f"Ratio="
        f"{fvg['fvg_ratio'] * 100:.2f}% | "
        f"Target="
        f"{fvg['target_price']:.8f}"
    )


# ============================================================
# FINISH FVG
# ============================================================

def finish_fvg(
    symbol,
    fvg
):

    # Remember the FVG that has finished
    last_finished_fvg[
        (
            symbol,
            fvg["interval"]
        )
    ] = fvg[
        "candle3_time"
    ]

    # Remove active FVG
    active_fvgs.pop(
        symbol,
        None
    )


# ============================================================
# MONITOR ACTIVE FVG
# ============================================================

def monitor_active_fvg(
    symbol,
    market_cap
):

    fvg = active_fvgs.get(
        symbol
    )

    if not fvg:
        return

    price = get_current_price(
        symbol
    )

    if price is None:
        return

    close2 = fvg[
        "candle2_close"
    ]

    target = fvg[
        "target_price"
    ]

    # --------------------------------------------------------
    # TARGET HIT FIRST
    # --------------------------------------------------------

    if price <= target:

        log(
            f"{symbol}: "
            f"{fvg['interval']} "
            f"3% TARGET HIT "
            f"price={price:.8f}"
        )

        send_signal(
            fvg,
            market_cap
        )

        # FVG is finished
        finish_fvg(
            symbol,
            fvg
        )

        return

    # --------------------------------------------------------
    # CANCEL IF PRICE RETURNS TO 2ND CLOSE
    # --------------------------------------------------------

    if price >= close2:

        log(
            f"{symbol}: "
            f"{fvg['interval']} "
            f"FVG CANCELLED - "
            f"price returned to "
            f"2nd close."
        )

        finish_fvg(
            symbol,
            fvg
        )

        return


# ============================================================
# ANALYZE SYMBOL
# ============================================================

def analyze_symbol(
    symbol_data,
    market_caps
):

    symbol = symbol_data[
        "symbol"
    ]

    base = symbol_data[
        "base"
    ]

    # --------------------------------------------------------
    # MARKET CAP
    # --------------------------------------------------------

    market_cap = market_caps.get(
        base
    )

    if market_cap is None:
        return

    if market_cap <= MIN_MARKET_CAP:
        return

    # --------------------------------------------------------
    # ACTIVE FVG
    #
    # IMPORTANT:
    # If an FVG is active, we DO NOT SEARCH
    # FOR ANOTHER FVG.
    # --------------------------------------------------------

    if symbol in active_fvgs:

        monitor_active_fvg(
            symbol,
            market_cap
        )

        return

    # --------------------------------------------------------
    # 1H TREND
    # --------------------------------------------------------

    trend = check_1h_bullish(
        symbol
    )

    if not trend:
        return

    if not trend["bullish"]:
        return

    # --------------------------------------------------------
    # FVG SEARCH
    #
    # 15M FIRST
    # IF NONE -> 1H
    # --------------------------------------------------------

    fvg = find_best_fvg(
        symbol
    )

    if not fvg:
        return

    # --------------------------------------------------------
    # ACTIVATE
    # --------------------------------------------------------

    activate_fvg(
        fvg,
        trend
    )


# ============================================================
# MAIN SCAN
# ============================================================

def scan():

    log("=" * 60)

    log(
        "Starting market scan..."
    )

    symbols = get_binance_symbols()

    if not symbols:

        log(
            "No Binance symbols."
        )

        return

    market_caps = get_market_caps()

    if not market_caps:

        log(
            "No market cap data."
        )

        return

    eligible = []

    for item in symbols:

        market_cap = market_caps.get(
            item["base"]
        )

        if market_cap is None:
            continue

        if market_cap > MIN_MARKET_CAP:

            eligible.append(
                item
            )

    log(
        f"Market Cap > $300M: "
        f"{len(eligible)} coins"
    )

    for item in eligible:

        try:

            analyze_symbol(
                item,
                market_caps
            )

        except Exception as e:

            log(
                f"{item['symbol']} "
                f"analysis error: {e}"
            )

        # Small pause
        time.sleep(0.05)

    log(
        f"Active FVGs: "
        f"{len(active_fvgs)}"
    )

    log("=" * 60)


# ============================================================
# STARTUP CHECK
# ============================================================

def startup_check():

    if not CMC_API_KEY:

        log(
            "ERROR: CMC_API_KEY "
            "environment variable is missing."
        )

    if not TELEGRAM_BOT_TOKEN:

        log(
            "ERROR: TELEGRAM_BOT_TOKEN "
            "environment variable is missing."
        )

    if not TELEGRAM_CHAT_ID:

        log(
            "ERROR: TELEGRAM_CHAT_ID "
            "environment variable is missing."
        )

    if (
        CMC_API_KEY
        and TELEGRAM_BOT_TOKEN
        and TELEGRAM_CHAT_ID
    ):

        send_telegram(

            "🤖 <b>FVG BOT STARTED</b>\n\n"

            "Market Cap > $300M\n"

            "1H: "
            "Close > EMA20 > EMA50 > EMA100\n"

            "FVG Priority:\n"
            "1️⃣ 15M Bearish FVG\n"
            "2️⃣ If no 15M -> 1H Bearish FVG\n\n"

            "FVG inside 2nd candle body\n"

            "FVG ≥ 50% of body\n"

            "3% target from 2nd candle Close\n\n"

            "Only ONE active FVG per coin\n"

            "⚠️ Alert Only"
        )


# ============================================================
# MAIN LOOP
# ============================================================

def main():

    log(
        "=========================================="
    )

    log(
        "BINANCE FVG ALERT BOT"
    )

    log(
        "15M FVG -> 1H FVG FALLBACK"
    )

    log(
        "ONE ACTIVE FVG PER COIN"
    )

    log(
        "ALERT ONLY - NO AUTOMATIC ORDERS"
    )

    log(
        "=========================================="
    )

    startup_check()

    while True:

        start = time.time()

        try:

            scan()

        except Exception as e:

            log(
                f"MAIN LOOP ERROR: {e}"
            )

        elapsed = (
            time.time() - start
        )

        sleep_time = max(
            1,
            SCAN_INTERVAL - elapsed
        )

        log(
            f"Next scan in "
            f"{sleep_time:.1f}s"
        )

        time.sleep(
            sleep_time
        )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
