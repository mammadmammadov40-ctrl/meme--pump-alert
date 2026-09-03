import os
import time
import requests
from datetime import datetime, timezone


# =========================================================
# CONFIG
# =========================================================

CMC_API_KEY = os.getenv("CMC_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

BINANCE_BASE_URL = "https://api.binance.com"

MIN_MARKET_CAP = 300_000_000

# FVG must be at least 50% of 2nd candle body
FVG_MIN_RATIO = 0.50

# Target = 3% below 3rd candle HIGH
DROP_PERCENT = 3.0

# Main market scan
SCAN_INTERVAL = 60

# CoinMarketCap market-cap cache
# CMC is refreshed only once every 15 minutes
CMC_CACHE_SECONDS = 15 * 60

# Telegram signal cooldown
SIGNAL_COOLDOWN = 24 * 60 * 60


# =========================================================
# GLOBAL STATE
# =========================================================

active_fvgs = {}

last_finished_fvg = {}

last_signal_time = {}

market_cap_cache = {}

market_cap_cache_time = 0


# =========================================================
# LOG
# =========================================================

def log(message):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}", flush=True)


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("Telegram credentials missing.")
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
        response = requests.post(
            url,
            json=payload,
            timeout=15
        )

        response.raise_for_status()

        return True

    except Exception as e:
        log(f"Telegram error: {e}")
        return False


# =========================================================
# BINANCE
# =========================================================

def get_binance_usdt_symbols():

    url = f"{BINANCE_BASE_URL}/api/v3/exchangeInfo"

    try:

        response = requests.get(
            url,
            timeout=20
        )

        response.raise_for_status()

        data = response.json()

        symbols = []

        for item in data.get("symbols", []):

            if (
                item.get("status") == "TRADING"
                and item.get("quoteAsset") == "USDT"
                and item.get("isSpotTradingAllowed") is True
            ):
                symbols.append(item["symbol"])

        log(
            f"Binance USDT Spot symbols: "
            f"{len(symbols)}"
        )

        return symbols

    except Exception as e:

        log(f"Binance symbols error: {e}")

        return []


# =========================================================
# COINMARKETCAP
# =========================================================

def fetch_market_caps_from_cmc():

    global market_cap_cache
    global market_cap_cache_time

    if not CMC_API_KEY:
        log("CMC_API_KEY is missing.")
        return market_cap_cache

    url = (
        "https://pro-api.coinmarketcap.com/"
        "v1/cryptocurrency/listings/latest"
    )

    headers = {
        "Accept": "application/json",
        "X-CMC_PRO_API_KEY": CMC_API_KEY
    }

    params = {
        "start": 1,
        "limit": 5000,
        "convert": "USD"
    }

    max_retries = 4

    for attempt in range(max_retries):

        try:

            response = requests.get(
                url,
                headers=headers,
                params=params,
                timeout=30
            )

            # -------------------------------------------------
            # 429 RATE LIMIT
            # -------------------------------------------------

            if response.status_code == 429:

                wait_seconds = min(
                    60,
                    5 * (2 ** attempt)
                )

                log(
                    "CoinMarketCap 429 "
                    f"rate limit. "
                    f"Retry in {wait_seconds}s..."
                )

                time.sleep(wait_seconds)

                continue

            response.raise_for_status()

            result = response.json()

            data = result.get("data", [])

            if not data:

                log("CoinMarketCap returned no data.")

                return market_cap_cache

            new_cache = {}

            for coin in data:

                symbol = coin.get("symbol")

                quote = (
                    coin.get("quote", {})
                    .get("USD", {})
                )

                market_cap = quote.get("market_cap")

                if not symbol or market_cap is None:
                    continue

                # If duplicate symbols exist,
                # keep the highest market cap.
                if (
                    symbol not in new_cache
                    or market_cap > new_cache[symbol]
                ):
                    new_cache[symbol] = market_cap

            market_cap_cache = new_cache
            market_cap_cache_time = time.time()

            log(
                f"CoinMarketCap coins loaded: "
                f"{len(market_cap_cache)}"
            )

            return market_cap_cache

        except requests.exceptions.RequestException as e:

            log(
                f"CoinMarketCap request error: {e}"
            )

            if attempt < max_retries - 1:

                wait_seconds = min(
                    30,
                    2 ** attempt
                )

                log(
                    f"Retry in {wait_seconds}s..."
                )

                time.sleep(wait_seconds)

        except Exception as e:

            log(
                f"CoinMarketCap error: {e}"
            )

            break

    # ---------------------------------------------------------
    # IMPORTANT:
    # If CMC fails, KEEP OLD CACHE
    # ---------------------------------------------------------

    if market_cap_cache:

        log(
            "CMC unavailable. "
            "Using previous market-cap cache."
        )

    else:

        log(
            "CMC unavailable and "
            "no market-cap cache exists."
        )

    return market_cap_cache


def get_market_caps():

    global market_cap_cache_time

    now = time.time()

    # ---------------------------------------------------------
    # USE CACHE
    # ---------------------------------------------------------

    if (
        market_cap_cache
        and
        (now - market_cap_cache_time)
        < CMC_CACHE_SECONDS
    ):

        age = int(
            now - market_cap_cache_time
        )

        log(
            f"Using CMC cache "
            f"(age={age}s)"
        )

        return market_cap_cache

    # ---------------------------------------------------------
    # REFRESH CMC
    # ---------------------------------------------------------

    log(
        "Refreshing CoinMarketCap data..."
    )

    return fetch_market_caps_from_cmc()


# =========================================================
# BINANCE KLINES
# =========================================================

def get_klines(symbol, interval, limit=100):

    url = f"{BINANCE_BASE_URL}/api/v3/klines"

    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }

    try:

        response = requests.get(
            url,
            params=params,
            timeout=20
        )

        response.raise_for_status()

        return response.json()

    except Exception as e:

        log(
            f"{symbol}: "
            f"{interval} klines error: {e}"
        )

        return []


# =========================================================
# EMA
# =========================================================

def calculate_ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    ema = sum(
        values[:period]
    ) / period

    for price in values[period:]:

        ema = (
            (price - ema)
            * multiplier
        ) + ema

    return ema


# =========================================================
# 1H TREND
# =========================================================

def check_1h_trend(symbol):

    klines = get_klines(
        symbol,
        "1h",
        150
    )

    if len(klines) < 105:
        return False

    # Ignore current forming candle
    closed_klines = klines[:-1]

    closes = [
        float(k[4])
        for k in closed_klines
    ]

    if len(closes) < 100:
        return False

    close = closes[-1]

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

    return (
        close > ema20
        and
        ema20 > ema50
        and
        ema50 > ema100
    )


# =========================================================
# BEARISH FVG
# =========================================================

def find_bearish_fvg(
    symbol,
    interval
):

    klines = get_klines(
        symbol,
        interval,
        100
    )

    if len(klines) < 10:
        return None

    # Ignore current forming candle
    closed_klines = klines[:-1]

    # Search newest -> oldest
    for i in range(
        len(closed_klines) - 3,
        -1,
        -1
    ):

        c1 = closed_klines[i]
        c2 = closed_klines[i + 1]
        c3 = closed_klines[i + 2]

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

        # -----------------------------------------------------
        # BEARISH FVG
        # 1st candle LOW > 3rd candle HIGH
        # -----------------------------------------------------

        if not (
            c1_low > c3_high
        ):
            continue

        fvg_low = c3_high
        fvg_high = c1_low

        fvg_size = (
            fvg_high
            - fvg_low
        )

        # -----------------------------------------------------
        # 2ND CANDLE BODY
        # -----------------------------------------------------

        body_high = max(
            c2_open,
            c2_close
        )

        body_low = min(
            c2_open,
            c2_close
        )

        body_size = (
            body_high
            - body_low
        )

        if body_size <= 0:
            continue

        # -----------------------------------------------------
        # FVG MUST BE FULLY INSIDE 2ND CANDLE BODY
        # -----------------------------------------------------

        if not (
            fvg_low >= body_low
            and
            fvg_high <= body_high
        ):
            continue

        # -----------------------------------------------------
        # FVG >= 50% OF 2ND CANDLE BODY
        # -----------------------------------------------------

        ratio = (
            fvg_size
            / body_size
        )

        if ratio < FVG_MIN_RATIO:
            continue

        # -----------------------------------------------------
        # CHECK IF THIS FVG WAS ALREADY FINISHED
        # -----------------------------------------------------

        candle_time = c2[0]

        previous_finished = (
            last_finished_fvg.get(symbol)
        )

        if (
            previous_finished is not None
            and
            candle_time <= previous_finished
        ):
            continue

        # -----------------------------------------------------
        # TARGET
        #
        # 3rd candle HIGH - 3%
        # -----------------------------------------------------

        target_price = (
            c3_high
            *
            (
                1
                -
                DROP_PERCENT / 100
            )
        )

        return {
            "symbol": symbol,
            "interval": interval,

            "candle1_time": c1[0],
            "candle2_time": c2[0],
            "candle3_time": c3[0],

            "candle2_open": c2_open,
            "candle2_close": c2_close,

            "c3_high": c3_high,

            "fvg_low": fvg_low,
            "fvg_high": fvg_high,

            "ratio": ratio,

            "target": target_price
        }

    return None


# =========================================================
# FINISH FVG
# =========================================================

def finish_fvg(
    symbol,
    fvg
):

    active_fvgs.pop(
        symbol,
        None
    )

    last_finished_fvg[symbol] = (
        fvg["candle2_time"]
    )


# =========================================================
# SIGNAL
# =========================================================

def send_signal(
    fvg,
    market_cap
):

    symbol = fvg["symbol"]

    now = time.time()

    previous_signal = (
        last_signal_time.get(symbol, 0)
    )

    if (
        now - previous_signal
        < SIGNAL_COOLDOWN
    ):
        log(
            f"{symbol}: "
            "Signal cooldown active."
        )
        return

    last_signal_time[symbol] = now

    market_cap_text = (
        f"${market_cap:,.0f}"
        if market_cap
        else "N/A"
    )

    message = (
        "🚨 BEARISH FVG TARGET HIT 🚨\n\n"

        f"Symbol: {symbol}\n"
        f"Market Cap: {market_cap_text}\n\n"

        f"1H Trend: "
        "CLOSE > EMA20 > EMA50 > EMA100\n\n"

        f"FVG Timeframe: "
        f"{fvg['interval']}\n"

        f"FVG Range: "
        f"{fvg['fvg_low']:.8f} - "
        f"{fvg['fvg_high']:.8f}\n\n"

        f"2nd Candle Open: "
        f"{fvg['candle2_open']:.8f}\n"

        f"2nd Candle Close: "
        f"{fvg['candle2_close']:.8f}\n\n"

        f"FVG Ratio: "
        f"{fvg['ratio'] * 100:.2f}%\n\n"

        f"3rd Candle High: "
        f"{fvg['c3_high']:.8f}\n"

        f"Target: "
        f"{fvg['target']:.8f}\n"
    )

    send_telegram(message)


# =========================================================
# ACTIVE FVG MONITOR
# =========================================================

def monitor_active_fvg(
    symbol,
    market_cap
):

    fvg = active_fvgs.get(symbol)

    if not fvg:
        return

    url = (
        f"{BINANCE_BASE_URL}/api/v3/ticker/price"
    )

    try:

        response = requests.get(
            url,
            params={
                "symbol": symbol
            },
            timeout=10
        )

        response.raise_for_status()

        data = response.json()

        price = float(
            data["price"]
        )

    except Exception as e:

        log(
            f"{symbol}: price error: {e}"
        )

        return

    target = fvg["target"]

    c3_high = fvg["c3_high"]

    # ---------------------------------------------------------
    # TARGET FIRST
    # ---------------------------------------------------------

    if price <= target:

        log(
            f"{symbol}: "
            f"{fvg['interval']} "
            f"FVG TARGET HIT - "
            f"price={price:.8f}, "
            f"target={target:.8f}"
        )

        send_signal(
            fvg,
            market_cap
        )

        finish_fvg(
            symbol,
            fvg
        )

        return

    # ---------------------------------------------------------
    # CANCEL
    #
    # If price crosses ABOVE 3rd candle HIGH
    # before target is hit.
    # ---------------------------------------------------------

    if price > c3_high:

        log(
            f"{symbol}: "
            f"{fvg['interval']} "
            f"FVG CANCELLED - "
            f"price crossed 3rd candle high "
            f"(price={price:.8f}, "
            f"3rd_high={c3_high:.8f})"
        )

        finish_fvg(
            symbol,
            fvg
        )

        return


# =========================================================
# FIND NEW FVG
# =========================================================

def search_new_fvg(
    symbol
):

    # ---------------------------------------------------------
    # FIRST: 15M
    # ---------------------------------------------------------

    fvg = find_bearish_fvg(
        symbol,
        "15m"
    )

    if fvg:

        return fvg

    # ---------------------------------------------------------
    # FALLBACK: 1H
    # ---------------------------------------------------------

    fvg = find_bearish_fvg(
        symbol,
        "1h"
    )

    return fvg


# =========================================================
# MAIN MARKET SCAN
# =========================================================

def market_scan():

    log(
        "Starting market scan..."
    )

    # ---------------------------------------------------------
    # GET BINANCE SYMBOLS
    # ---------------------------------------------------------

    symbols = (
        get_binance_usdt_symbols()
    )

    if not symbols:

        log(
            "No Binance symbols."
        )

        return

    # ---------------------------------------------------------
    # GET / CACHE MARKET CAPS
    # ---------------------------------------------------------

    market_caps = get_market_caps()

    if not market_caps:

        log(
            "No market cap data."
        )

        return

    # ---------------------------------------------------------
    # FILTER MARKET CAP
    # ---------------------------------------------------------

    qualified_symbols = []

    for symbol in symbols:

        base_asset = (
            symbol[:-4]
            if symbol.endswith("USDT")
            else None
        )

        if not base_asset:
            continue

        market_cap = (
            market_caps.get(
                base_asset
            )
        )

        if (
            market_cap is not None
            and
            market_cap > MIN_MARKET_CAP
        ):

            qualified_symbols.append(
                symbol
            )

    log(
        f"Market Cap > $300M: "
        f"{len(qualified_symbols)} coins"
    )

    # ---------------------------------------------------------
    # PROCESS COINS
    # ---------------------------------------------------------

    for symbol in qualified_symbols:

        try:

            # -------------------------------------------------
            # ACTIVE FVG
            # -------------------------------------------------

            if symbol in active_fvgs:

                monitor_active_fvg(
                    symbol,
                    market_caps.get(
                        symbol[:-4]
                    )
                )

                continue

            # -------------------------------------------------
            # 1H TREND
            # -------------------------------------------------

            if not check_1h_trend(
                symbol
            ):

                continue

            # -------------------------------------------------
            # FIND FVG
            # -------------------------------------------------

            fvg = search_new_fvg(
                symbol
            )

            if not fvg:
                continue

            # -------------------------------------------------
            # ACTIVATE FVG
            # -------------------------------------------------

            active_fvgs[symbol] = fvg

            log(
                f"{symbol}: "
                f"NEW {fvg['interval']} FVG | "
                f"FVG="
                f"{fvg['fvg_low']:.8f}"
                f"-"
                f"{fvg['fvg_high']:.8f} | "
                f"ratio="
                f"{fvg['ratio'] * 100:.2f}% | "
                f"3rd High="
                f"{fvg['c3_high']:.8f} | "
                f"Target="
                f"{fvg['target']:.8f}"
            )

        except Exception as e:

            log(
                f"{symbol}: "
                f"processing error: {e}"
            )


# =========================================================
# STARTUP
# =========================================================

def startup_message():

    message = (
        "🤖 Binance FVG Alert Bot Started\n\n"

        "Market Cap: > $300M\n"

        "1H Trend:\n"
        "CLOSE > EMA20 > EMA50 > EMA100\n\n"

        "FVG:\n"
        "15M first, 1H fallback\n"
        "FVG >= 50% of 2nd candle body\n"
        "FVG fully inside 2nd candle body\n\n"

        "Target:\n"
        "3% below 3rd candle HIGH\n\n"

        "Cancel:\n"
        "Price crosses above 3rd candle HIGH\n\n"

        "Scan interval: 60 seconds\n"
        "CMC cache: 15 minutes"
    )

    send_telegram(
        message
    )


# =========================================================
# MAIN LOOP
# =========================================================

def main():

    log(
        "Bot starting..."
    )

    log(
        "Scan interval: "
        f"{SCAN_INTERVAL}s"
    )

    log(
        "CMC cache interval: "
        f"{CMC_CACHE_SECONDS}s"
    )

    startup_message()

    while True:

        scan_start = time.time()

        try:

            market_scan()

        except Exception as e:

            log(
                f"Main scan error: {e}"
            )

        elapsed = (
            time.time()
            - scan_start
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


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
