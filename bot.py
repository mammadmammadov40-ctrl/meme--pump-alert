import os
import time
import json
import requests
from datetime import datetime, timezone


# ============================================================
# BINANCE SPOT BEAR FVG ALERT BOT
#
# CMC ROBUST CACHE VERSION
#
# ============================================================
#
# STRATEGY
#
# 1) Binance Spot USDT pairs
# 2) CoinMarketCap Market Cap > $300M
# 3) 1H trend:
#       Close > EMA20 > EMA50 > EMA100
# 4) First search 15M Bear FVG
#    If no valid NEW 15M FVG -> search NEW 1H Bear FVG
#
# 5) Bear FVG:
#       Candle 1 Low > Candle 3 High
#       Candle 1 bearish
#       Candle 2 bearish
#       FVG completely inside Candle 2 Open/Close body
#       FVG size >= 50% of Candle 2 body
#
# 6) FVG becomes active AFTER Candle 3 closes
#
# 7) Active FVG:
#
#       Price <= Candle 3 High - 1.7%
#           -> Telegram signal
#
#       Price > Candle 3 High
#           -> FVG CANCEL
#
# 8) After TARGET or CANCEL:
#       Forget old FVG
#       Return to 1H trend
#       Search for NEW FVG
#
# 9) Scan every 60 seconds
#
# 10) CMC cache refresh every 15 minutes
#
# 11) CMC 429 protection:
#       Cooldown + Retry-After
#
# 12) Persistent CMC cache:
#       market_cap_cache.json
#
# IMPORTANT:
#       Old FVGs from before bot startup are NOT activated.
#
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


# ============================================================
# CMC SETTINGS
# ============================================================

# Normal cache lifetime
CMC_CACHE_SECONDS = 15 * 60

# Maximum time to wait for CMC
CMC_TIMEOUT = 10

# If CMC returns 429, don't request again for this period
CMC_BASE_COOLDOWN = 30 * 60

# Maximum cooldown
CMC_MAX_COOLDOWN = 2 * 60 * 60

# Persistent cache file
CMC_CACHE_FILE = "market_cap_cache.json"


# ============================================================
# BINANCE SETTINGS
# ============================================================

SYMBOL_CACHE_SECONDS = 60 * 60

BINANCE_TIMEOUT = 15


# ============================================================
# BOT STATE
# ============================================================

# One active FVG per symbol
active_fvgs = {}


# ------------------------------------------------------------
# Last processed FVG
#
# Key:
#     (symbol, interval)
#
# Value:
#     Candle 3 open time
# ------------------------------------------------------------

last_processed_fvg = {}


# ------------------------------------------------------------
# CMC cache
# ------------------------------------------------------------

market_cap_cache = {}

market_cap_cache_time = 0


# ------------------------------------------------------------
# CMC protection
# ------------------------------------------------------------

cmc_blocked_until = 0

cmc_backoff_level = 0


# ------------------------------------------------------------
# Binance symbol cache
# ------------------------------------------------------------

binance_symbols_cache = []

binance_symbols_cache_time = 0


# ============================================================
# SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "Bear-FVG-Bot/3.0"
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

    return dt.strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


# ============================================================
# LOGGING
# ============================================================

def log(message=""):

    print(
        message,
        flush=True
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if (
        not TELEGRAM_BOT_TOKEN
        or
        not TELEGRAM_CHAT_ID
    ):

        log(
            "[TELEGRAM] Credentials missing."
        )

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

        log(
            f"[TELEGRAM] Error: {e}"
        )

        return False


# ============================================================
# BINANCE REQUEST
# ============================================================

def binance_get(
    endpoint,
    params=None
):

    url = BINANCE_BASE_URL + endpoint

    try:

        response = session.get(
            url,
            params=params,
            timeout=BINANCE_TIMEOUT
        )

        if response.status_code == 429:

            retry_after = response.headers.get(
                "Retry-After",
                "unknown"
            )

            log(
                f"[BINANCE] Rate limit 429 | "
                f"{endpoint} | "
                f"Retry-After={retry_after}"
            )

            return None

        response.raise_for_status()

        return response.json()

    except requests.exceptions.Timeout:

        log(
            f"[BINANCE] Timeout | {endpoint}"
        )

        return None

    except Exception as e:

        log(
            f"[BINANCE] Error | "
            f"{endpoint} | {e}"
        )

        return None


# ============================================================
# BINANCE SPOT USDT SYMBOLS
# ============================================================

def get_binance_spot_symbols():

    global binance_symbols_cache
    global binance_symbols_cache_time

    current_time = time.time()

    # --------------------------------------------------------
    # Cache
    # --------------------------------------------------------

    if (
        binance_symbols_cache
        and
        current_time - binance_symbols_cache_time
        < SYMBOL_CACHE_SECONDS
    ):

        return binance_symbols_cache

    log(
        "[BINANCE] Updating Spot USDT symbols..."
    )

    data = binance_get(
        "/api/v3/exchangeInfo"
    )

    if not data:

        log(
            "[BINANCE] exchangeInfo failed. "
            "Using old symbol cache."
        )

        return binance_symbols_cache

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
            "isSpotTradingAllowed",
            False
        ):
            continue

        symbol = item.get(
            "symbol"
        )

        base_asset = item.get(
            "baseAsset"
        )

        if not symbol or not base_asset:
            continue

        symbols.append({
            "symbol": symbol,
            "base_asset": base_asset
        })

    binance_symbols_cache = symbols

    binance_symbols_cache_time = current_time

    log(
        f"[BINANCE] Spot USDT pairs: "
        f"{len(symbols)}"
    )

    return symbols


# ============================================================
# LOAD CMC CACHE FROM FILE
# ============================================================

def load_cmc_cache():

    global market_cap_cache
    global market_cap_cache_time

    if not os.path.exists(
        CMC_CACHE_FILE
    ):

        log(
            "[CMC] No local cache file found."
        )

        return

    try:

        with open(
            CMC_CACHE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            saved = json.load(f)

        cache = saved.get(
            "market_caps",
            {}
        )

        cache_time = saved.get(
            "timestamp",
            0
        )

        if not isinstance(
            cache,
            dict
        ):

            log(
                "[CMC] Cache file invalid."
            )

            return

        market_cap_cache = {
            str(k): float(v)
            for k, v in cache.items()
        }

        market_cap_cache_time = float(
            cache_time
        )

        log(
            f"[CMC] Local cache loaded: "
            f"{len(market_cap_cache)} coins"
        )

        if market_cap_cache_time:

            age_minutes = (
                time.time()
                - market_cap_cache_time
            ) / 60

            log(
                f"[CMC] Cache age: "
                f"{age_minutes:.1f} minutes"
            )

    except Exception as e:

        log(
            f"[CMC] Failed to load cache: {e}"
        )


# ============================================================
# SAVE CMC CACHE TO FILE
# ============================================================

def save_cmc_cache():

    try:

        payload = {
            "timestamp": market_cap_cache_time,
            "market_caps": market_cap_cache
        }

        temp_file = (
            CMC_CACHE_FILE
            + ".tmp"
        )

        with open(
            temp_file,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                payload,
                f
            )

        os.replace(
            temp_file,
            CMC_CACHE_FILE
        )

        log(
            "[CMC] Cache saved to disk."
        )

    except Exception as e:

        log(
            f"[CMC] Cache save error: {e}"
        )


# ============================================================
# CMC COOLDOWN
# ============================================================

def get_cmc_cooldown_seconds():

    global cmc_backoff_level

    cooldown = (
        CMC_BASE_COOLDOWN
        *
        (2 ** cmc_backoff_level)
    )

    return min(
        cooldown,
        CMC_MAX_COOLDOWN
    )


def block_cmc(seconds):

    global cmc_blocked_until

    cmc_blocked_until = (
        time.time()
        + seconds
    )


def is_cmc_blocked():

    return (
        time.time()
        < cmc_blocked_until
    )


def cmc_remaining_cooldown():

    remaining = (
        cmc_blocked_until
        - time.time()
    )

    return max(
        0,
        remaining
    )


# ============================================================
# COINMARKETCAP
# ============================================================

def get_market_caps():

    global market_cap_cache
    global market_cap_cache_time
    global cmc_backoff_level
    global cmc_blocked_until

    current_time = time.time()

    # ========================================================
    # CMC COOLDOWN
    # ========================================================

    if is_cmc_blocked():

        remaining = (
            cmc_remaining_cooldown()
        )

        log(
            f"[CMC] Cooldown active. "
            f"Remaining: "
            f"{remaining / 60:.1f} min"
        )

        if market_cap_cache:

            log(
                "[CMC] Using previous cache."
            )

        return market_cap_cache

    # ========================================================
    # NORMAL CACHE
    # ========================================================

    if (
        market_cap_cache
        and
        current_time - market_cap_cache_time
        < CMC_CACHE_SECONDS
    ):

        age = (
            current_time
            - market_cap_cache_time
        ) / 60

        log(
            f"[CMC] Using cache "
            f"({age:.1f} min old)."
        )

        return market_cap_cache

    # ========================================================
    # API KEY
    # ========================================================

    if not CMC_API_KEY:

        log(
            "[CMC] ERROR: CMC_API_KEY is missing."
        )

        if market_cap_cache:

            log(
                "[CMC] Using previous cache."
            )

        return market_cap_cache

    # ========================================================
    # REQUEST
    # ========================================================

    log(
        "[CMC] Updating market-cap data..."
    )

    url = (
        "https://pro-api.coinmarketcap.com/"
        "v1/cryptocurrency/listings/latest"
    )

    headers = {
        "X-CMC_PRO_API_KEY": CMC_API_KEY
    }

    params = {
        "start": 1,
        "limit": 1000,
        "convert": "USD"
    }

    request_start = time.time()

    try:

        response = session.get(
            url,
            headers=headers,
            params=params,
            timeout=CMC_TIMEOUT
        )

        elapsed = (
            time.time()
            - request_start
        )

        log(
            f"[CMC] HTTP {response.status_code} "
            f"after {elapsed:.2f}s"
        )

        # ====================================================
        # RATE LIMIT
        # ====================================================

        if response.status_code == 429:

            retry_after = (
                response.headers.get(
                    "Retry-After"
                )
            )

            if retry_after:

                try:

                    retry_seconds = int(
                        float(retry_after)
                    )

                except Exception:

                    retry_seconds = (
                        get_cmc_cooldown_seconds()
                    )

            else:

                retry_seconds = (
                    get_cmc_cooldown_seconds()
                )

            # Don't use a ridiculously small retry
            retry_seconds = max(
                retry_seconds,
                CMC_BASE_COOLDOWN
            )

            retry_seconds = min(
                retry_seconds,
                CMC_MAX_COOLDOWN
            )

            block_cmc(
                retry_seconds
            )

            cmc_backoff_level = min(
                cmc_backoff_level + 1,
                3
            )

            log(
                f"[CMC] RATE LIMIT 429."
            )

            log(
                f"[CMC] CMC blocked for "
                f"{retry_seconds / 60:.1f} minutes."
            )

            if market_cap_cache:

                log(
                    "[CMC] Keeping old cache."
                )

            else:

                log(
                    "[CMC] No old cache available."
                )

            return market_cap_cache

        # ====================================================
        # OTHER HTTP ERRORS
        # ====================================================

        if response.status_code >= 500:

            log(
                f"[CMC] Server error "
                f"{response.status_code}."
            )

            # Short cooldown for server problems
            block_cmc(
                5 * 60
            )

            return market_cap_cache

        response.raise_for_status()

        # ====================================================
        # JSON
        # ====================================================

        data = response.json()

        # ----------------------------------------------------
        # CMC sometimes returns status information
        # ----------------------------------------------------

        status = data.get(
            "status",
            {}
        )

        error_code = status.get(
            "error_code",
            0
        )

        if error_code not in (
            0,
            None
        ):

            error_message = status.get(
                "error_message",
                "Unknown CMC error"
            )

            log(
                f"[CMC] API error "
                f"{error_code}: "
                f"{error_message}"
            )

            return market_cap_cache

        # ====================================================
        # BUILD NEW CACHE
        # ====================================================

        new_cache = {}

        for coin in data.get(
            "data",
            []
        ):

            symbol = coin.get(
                "symbol"
            )

            quote = coin.get(
                "quote",
                {}
            )

            usd = quote.get(
                "USD",
                {}
            )

            market_cap = usd.get(
                "market_cap"
            )

            if not symbol:
                continue

            if market_cap is None:
                continue

            try:

                market_cap = float(
                    market_cap
                )

            except Exception:

                continue

            # ------------------------------------------------
            # If duplicate symbols exist,
            # keep largest market cap.
            # ------------------------------------------------

            if (
                symbol not in new_cache
                or
                market_cap
                >
                new_cache[symbol]
            ):

                new_cache[symbol] = (
                    market_cap
                )

        # ====================================================
        # VALIDATE
        # ====================================================

        if not new_cache:

            log(
                "[CMC] Empty market-cap response."
            )

            log(
                "[CMC] Keeping old cache."
            )

            return market_cap_cache

        # ====================================================
        # SUCCESS
        # ====================================================

        market_cap_cache = new_cache

        market_cap_cache_time = (
            time.time()
        )

        save_cmc_cache()

        # Successful request -> reset backoff
        cmc_backoff_level = 0

        cmc_blocked_until = 0

        log(
            f"[CMC] Updated successfully: "
            f"{len(market_cap_cache)} coins"
        )

        return market_cap_cache

    # ========================================================
    # TIMEOUT
    # ========================================================

    except requests.exceptions.Timeout:

        log(
            f"[CMC] Request timeout "
            f"after {CMC_TIMEOUT}s."
        )

        log(
            "[CMC] Keeping old cache."
        )

        # Prevent repeated timeout requests
        block_cmc(
            5 * 60
        )

        return market_cap_cache

    # ========================================================
    # CONNECTION ERROR
    # ========================================================

    except requests.exceptions.ConnectionError as e:

        log(
            f"[CMC] Connection error: {e}"
        )

        log(
            "[CMC] Keeping old cache."
        )

        block_cmc(
            5 * 60
        )

        return market_cap_cache

    # ========================================================
    # OTHER ERROR
    # ========================================================

    except Exception as e:

        log(
            f"[CMC] Error: {e}"
        )

        log(
            "[CMC] Keeping old cache."
        )

        block_cmc(
            5 * 60
        )

        return market_cap_cache


# ============================================================
# FILTER QUALIFIED COINS
# ============================================================

def get_qualified_symbols():

    log(
        "[FILTER] Getting Binance Spot pairs..."
    )

    binance_symbols = (
        get_binance_spot_symbols()
    )

    if not binance_symbols:

        log(
            "[FILTER] No Binance symbols available."
        )

        return []

    log(
        "[FILTER] Getting CMC market caps..."
    )

    market_caps = (
        get_market_caps()
    )

    if not market_caps:

        log(
            "[FILTER] NO MARKET-CAP DATA."
        )

        log(
            "[FILTER] Signals are BLOCKED "
            "until valid CMC data is available."
        )

        return []

    qualified = []

    for item in binance_symbols:

        symbol = item["symbol"]

        base_asset = item["base_asset"]

        market_cap = market_caps.get(
            base_asset
        )

        if market_cap is None:
            continue

        if market_cap > MIN_MARKET_CAP:

            qualified.append({
                "symbol": symbol,
                "base_asset": base_asset,
                "market_cap": market_cap
            })

    log(
        f"[FILTER] Qualified > $300M: "
        f"{len(qualified)}"
    )

    return qualified


# ============================================================
# KLINES
# ============================================================

def get_klines(
    symbol,
    interval,
    limit=100
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

    return data


# ============================================================
# CLOSED KLINES
# ============================================================

def get_closed_klines(
    symbol,
    interval,
    limit=100
):

    klines = get_klines(
        symbol,
        interval,
        limit
    )

    if len(klines) < 3:

        return []

    current_time_ms = int(
        time.time() * 1000
    )

    closed = []

    for candle in klines:

        close_time = candle[6]

        if close_time <= current_time_ms:

            closed.append(
                candle
            )

    return closed


# ============================================================
# EMA
# ============================================================

def calculate_ema(
    values,
    period
):

    if len(values) < period:

        return None

    multiplier = (
        2 / (period + 1)
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

        log(
            f"[TREND] {symbol}: "
            "not enough closed candles."
        )

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
        or
        ema50 is None
        or
        ema100 is None
    ):

        return False

    trend_ok = (
        current_close > ema20
        and
        ema20 > ema50
        and
        ema50 > ema100
    )

    if trend_ok:

        log(
            f"[TREND] {symbol}: PASS | "
            f"Close={current_close} | "
            f"EMA20={ema20:.8f} | "
            f"EMA50={ema50:.8f} | "
            f"EMA100={ema100:.8f}"
        )

    return trend_ok


# ============================================================
# BEAR FVG DETECTION
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

    last_processed = (
        last_processed_fvg.get(
            (symbol, interval),
            0
        )
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

        c3_open_time = c3[0]

        if (
            c3_open_time
            <= last_processed
        ):

            continue

        # ====================================================
        # CANDLE 1
        # ====================================================

        c1_open = float(c1[1])
        c1_close = float(c1[4])
        c1_low = float(c1[3])

        if c1_close >= c1_open:

            continue

        # ====================================================
        # CANDLE 2
        # ====================================================

        c2_open = float(c2[1])
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

        body_size = (
            body_high
            - body_low
        )

        if body_size <= 0:

            continue

        # ====================================================
        # CANDLE 3
        # ====================================================

        c3_high = float(c3[2])

        # ====================================================
        # BEAR FVG
        # ====================================================

        if c1_low <= c3_high:

            continue

        fvg_low = c3_high

        fvg_high = c1_low

        fvg_size = (
            fvg_high
            - fvg_low
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
        # FVG >= 50% BODY
        # ====================================================

        fvg_ratio = (
            fvg_size
            / body_size
        )

        if fvg_ratio < FVG_MIN_RATIO:

            continue

        # ====================================================
        # TARGET
        # ====================================================

        target_price = (
            c3_high
            *
            (
                1
                - TARGET_DROP_PERCENT / 100
            )
        )

        # ====================================================
        # VALID FVG
        # ====================================================

        log(
            f"[FVG] {symbol} {interval}: "
            f"NEW VALID FVG | "
            f"C3 High={c3_high} | "
            f"FVG={fvg_low}-{fvg_high} | "
            f"Ratio={fvg_ratio * 100:.2f}%"
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
# 15M FIRST
# 1H SECOND
# ============================================================

def search_new_fvg(symbol):

    fvg_15m = find_new_bearish_fvg(
        symbol,
        "15m"
    )

    if fvg_15m:

        log(
            f"[FVG] {symbol}: "
            "15M FVG selected."
        )

        return fvg_15m

    fvg_1h = find_new_bearish_fvg(
        symbol,
        "1h"
    )

    if fvg_1h:

        log(
            f"[FVG] {symbol}: "
            "No new 15M FVG. "
            "1H FVG selected."
        )

        return fvg_1h

    return None


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
# ACTIVATE FVG
# ============================================================

def activate_fvg(fvg):

    symbol = fvg["symbol"]

    interval = fvg["interval"]

    active_fvgs[symbol] = fvg

    last_processed_fvg[
        (symbol, interval)
    ] = fvg["candle3_time"]

    log()

    log(
        "=========================================="
    )

    log(
        "FVG ACTIVATED"
    )

    log(
        "=========================================="
    )

    log(
        f"Symbol        : {symbol}"
    )

    log(
        f"Timeframe     : {interval}"
    )

    log(
        f"Candle 3      : "
        f"{format_time(fvg['candle3_time'])}"
    )

    log(
        f"Candle 3 High : "
        f"{fvg['candle3_high']}"
    )

    log(
        f"FVG           : "
        f"{fvg['fvg_low']} - "
        f"{fvg['fvg_high']}"
    )

    log(
        f"FVG Size      : "
        f"{fvg['fvg_size']}"
    )

    log(
        f"Body Size     : "
        f"{fvg['candle2_body']}"
    )

    log(
        f"FVG Ratio     : "
        f"{fvg['fvg_ratio'] * 100:.2f}%"
    )

    log(
        f"Target        : "
        f"{fvg['target']}"
    )

    log(
        "=========================================="
    )

    log()


# ============================================================
# FINISH FVG
# ============================================================

def finish_fvg(symbol):

    if symbol in active_fvgs:

        del active_fvgs[symbol]

    log(
        f"[FVG] {symbol}: "
        "Finished. "
        "Returning to 1H trend."
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

    price = get_current_price(
        symbol
    )

    if price is None:

        log(
            f"[PRICE] {symbol}: "
            "Could not get current price."
        )

        return

    target = fvg["target"]

    third_high = fvg["candle3_high"]

    # ========================================================
    # TARGET
    # ========================================================

    if price <= target:

        message = (
            "🔴 BEAR FVG SIGNAL\n\n"

            f"Symbol: {symbol}\n"

            f"Timeframe: "
            f"{fvg['interval']}\n\n"

            f"3rd Candle High: "
            f"{third_high}\n"

            f"Target (-1.7%): "
            f"{target}\n"

            f"Current Price: "
            f"{price}\n\n"

            "Bear FVG target reached."
        )

        log()

        log(
            "=========================================="
        )

        log(
            "🎯 TARGET REACHED"
        )

        log(
            message
        )

        log(
            "=========================================="
        )

        send_telegram(
            message
        )

        finish_fvg(
            symbol
        )

        return

    # ========================================================
    # CANCEL
    # ========================================================

    if price > third_high:

        log()

        log(
            "=========================================="
        )

        log(
            f"❌ {symbol}: FVG CANCELLED"
        )

        log(
            f"Current Price : {price}"
        )

        log(
            f"Candle 3 High: {third_high}"
        )

        log(
            "=========================================="
        )

        finish_fvg(
            symbol
        )

        return

    # ========================================================
    # STILL ACTIVE
    # ========================================================

    distance_to_target = (
        (
            third_high
            - price
        )
        /
        third_high
    ) * 100

    log(
        f"[ACTIVE] {symbol} | "
        f"Price={price} | "
        f"C3 High={third_high} | "
        f"Target={target} | "
        f"Drop={distance_to_target:.3f}%"
    )


# ============================================================
# PROCESS ONE COIN
# ============================================================

def process_symbol(item):

    symbol = item["symbol"]

    # ========================================================
    # ACTIVE FVG
    # ========================================================

    if symbol in active_fvgs:

        monitor_active_fvg(
            symbol
        )

        return

    # ========================================================
    # 1H TREND
    # ========================================================

    trend_ok = check_1h_trend(
        symbol
    )

    if not trend_ok:

        return

    # ========================================================
    # SEARCH FVG
    # ========================================================

    fvg = search_new_fvg(
        symbol
    )

    if not fvg:

        return

    # ========================================================
    # ACTIVATE
    # ========================================================

    activate_fvg(
        fvg
    )


# ============================================================
# MARKET SCAN
# ============================================================

def market_scan():

    log()

    log(
        "=========================================="
    )

    log(
        f"MARKET SCAN "
        f"{now_utc().strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )

    log(
        "=========================================="
    )

    qualified = (
        get_qualified_symbols()
    )

    log(
        f"[MARKET] Qualified coins "
        f"(> $300M): {len(qualified)}"
    )

    if not qualified:

        log(
            "[MARKET] No qualified coins."
        )

        return

    for index, item in enumerate(
        qualified,
        start=1
    ):

        symbol = item["symbol"]

        market_cap = item["market_cap"]

        try:

            log(
                f"[{index}/{len(qualified)}] "
                f"{symbol} | "
                f"MC ${market_cap:,.0f}"
            )

            process_symbol(
                item
            )

        except Exception as e:

            log(
                f"[ERROR] {symbol}: "
                f"processing error: {e}"
            )

        time.sleep(
            0.05
        )


# ============================================================
# INITIALIZE FVG BASELINE
# ============================================================

def initialize_fvg_baseline():

    current_ms = int(
        time.time() * 1000
    )

    interval_15m_ms = (
        15 * 60 * 1000
    )

    latest_closed_15m = (
        current_ms
        // interval_15m_ms
    ) * interval_15m_ms - interval_15m_ms

    interval_1h_ms = (
        60 * 60 * 1000
    )

    latest_closed_1h = (
        current_ms
        // interval_1h_ms
    ) * interval_1h_ms - interval_1h_ms

    log(
        "[STARTUP] FVG historical baseline:"
    )

    log(
        f"[STARTUP] Latest closed 15M: "
        f"{format_time(latest_closed_15m)}"
    )

    log(
        f"[STARTUP] Latest closed 1H: "
        f"{format_time(latest_closed_1h)}"
    )

    return (
        latest_closed_15m,
        latest_closed_1h
    )


# ============================================================
# APPLY BASELINE
# ============================================================

def apply_fvg_baseline(
    qualified,
    latest_15m,
    latest_1h
):

    for item in qualified:

        symbol = item["symbol"]

        last_processed_fvg[
            (symbol, "15m")
        ] = latest_15m

        last_processed_fvg[
            (symbol, "1h")
        ] = latest_1h

    log(
        f"[STARTUP] Baseline applied to "
        f"{len(qualified)} qualified coins."
    )


# ============================================================
# STARTUP
# ============================================================

def startup():

    log()

    log(
        "=========================================="
    )

    log(
        "BINANCE SPOT BEAR FVG BOT v3.0"
    )

    log(
        "=========================================="
    )

    log()

    log(
        "Conditions:"
    )

    log()

    log(
        "1. Binance Spot USDT"
    )

    log(
        "2. CMC Market Cap > $300M"
    )

    log(
        "3. 1H: Close > EMA20 > EMA50 > EMA100"
    )

    log(
        "4. Search NEW 15M Bear FVG first"
    )

    log(
        "5. Only if no 15M -> search NEW 1H"
    )

    log(
        "6. Three candles must be CLOSED"
    )

    log(
        "7. Candle 1 must be bearish"
    )

    log(
        "8. Candle 2 must be bearish"
    )

    log(
        "9. Candle 1 Low > Candle 3 High"
    )

    log(
        "10. FVG inside Candle 2 body"
    )

    log(
        "11. FVG >= 50% of Candle 2 body"
    )

    log(
        "12. Target = 1.7% below Candle 3 High"
    )

    log(
        "13. Candle 3 High crossed -> CANCEL"
    )

    log(
        "14. One active FVG per coin"
    )

    log(
        "15. Active FVG blocks new FVG searches"
    )

    log(
        "16. After Target/Cancel -> return to 1H"
    )

    log(
        "17. Scan every 60 seconds"
    )

    log(
        "18. CMC cache refresh every 15 minutes"
    )

    log(
        "19. CMC timeout = 10 seconds"
    )

    log(
        "20. CMC 429 cooldown = 30 minutes+"
    )

    log(
        "21. CMC persistent local cache"
    )

    log(
        "22. Old startup FVGs are ignored"
    )

    log()

    log(
        "No signal cooldown."
    )

    log(
        "No RSI / MACD / Volume filters."
    )

    log(
        "=========================================="
    )

    log()

    send_telegram(
        "🟢 Bear FVG Bot started.\n\n"

        "Binance Spot USDT\n"

        "CMC Market Cap > $300M\n"

        "1H: Close > EMA20 > EMA50 > EMA100\n"

        "15M FVG → 1H fallback\n"

        "Bear FVG >= 50% of Candle 2 body\n"

        "Target: -1.7%\n"

        "Scan: 60 seconds\n"

        "CMC cache: 15 minutes\n"

        "CMC 429 protection: ON"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    startup()

    # --------------------------------------------------------
    # FIRST: Load local CMC cache
    # --------------------------------------------------------

    log(
        "[STARTUP] Loading local CMC cache..."
    )

    load_cmc_cache()

    # --------------------------------------------------------
    # FVG baseline
    # --------------------------------------------------------

    latest_15m, latest_1h = (
        initialize_fvg_baseline()
    )

    # --------------------------------------------------------
    # Initial market data
    # --------------------------------------------------------

    log(
        "[STARTUP] Loading initial market data..."
    )

    qualified = (
        get_qualified_symbols()
    )

    # --------------------------------------------------------
    # Apply baseline
    # --------------------------------------------------------

    apply_fvg_baseline(
        qualified,
        latest_15m,
        latest_1h
    )

    log(
        "[STARTUP] Initialization complete."
    )

    log()

    # --------------------------------------------------------
    # Main loop
    # --------------------------------------------------------

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

            log()

            log(
                "Bot stopped."
            )

            break

        except Exception as e:

            log(
                f"[MAIN ERROR] {e}"
            )

            time.sleep(5)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
