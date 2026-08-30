import os
import time
import threading
import requests
import math

from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# BINANCE SPOT 15M HIGH QUALITY BREAKOUT ALERT BOT
#
# CMC MARKET CAP >= $200M
# BINANCE USDT SPOT ONLY
#
# 15M ONLY
# 500 CLOSED 15M CANDLES
#
# 100 CANDLE RESISTANCE
# 5 CANDLE CONSOLIDATION
#
# BREAKOUT >= 0.5%
# MOMENTUM +1% TO +4%
# VOLUME >= 1.8x
#
# EMA 20 > 50 > 200
# RSI 50 - 75
# ATR14 ACTIVITY FILTER
#
# CLOSE POSITION >= 75%
# UPPER WICK <= 25%
# SPREAD <= 0.15%
#
# SCORE >= 70
# 24H COOLDOWN
#
# TELEGRAM ALERT ONLY
# NO AUTOMATIC ORDER
# ============================================================


# ============================================================
# ENV
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
)

CMC_API_KEY = os.getenv(
    "CMC_API_KEY",
    ""
)


# ============================================================
# API
# ============================================================

BINANCE_REST = "https://api.binance.com"
CMC_REST = "https://pro-api.coinmarketcap.com"


# ============================================================
# TIMEFRAME
# ============================================================

INTERVAL = "15m"


# ============================================================
# HISTORY
# ============================================================

HISTORY_CANDLES = 500


# ============================================================
# CMC
# ============================================================

MIN_MARKET_CAP = 200_000_000


# ============================================================
# BINANCE 24H VOLUME
# ============================================================

MIN_24H_QUOTE_VOLUME = 10_000_000


# ============================================================
# BREAKOUT
# ============================================================

BREAKOUT_LOOKBACK = 100

MIN_BREAKOUT_PERCENT = 0.50

BREAKOUT_VOLUME_AVERAGE = 20

MIN_BREAKOUT_VOLUME_RATIO = 1.80


# ============================================================
# CONSOLIDATION
# ============================================================

CONSOLIDATION_CANDLES = 5

# Previous 5 candles must remain relatively tight.
# 2% maximum total range.
MAX_CONSOLIDATION_RANGE_PERCENT = 2.0


# ============================================================
# MOMENTUM
# ============================================================

MIN_MOMENTUM_PERCENT = 1.0
MAX_MOMENTUM_PERCENT = 4.0


# ============================================================
# EMA
# ============================================================

EMA_FAST = 20
EMA_MID = 50
EMA_SLOW = 200


# ============================================================
# RSI
# ============================================================

RSI_PERIOD = 14

MIN_RSI = 50.0
MAX_RSI = 75.0


# ============================================================
# ATR
# ============================================================

ATR_PERIOD = 14

# ATR must be at least this percentage of price.
# Prevents extremely dead markets.
MIN_ATR_PERCENT = 0.20


# ============================================================
# CANDLE QUALITY
# ============================================================

MIN_CLOSE_POSITION = 75.0

MAX_UPPER_WICK = 25.0


# ============================================================
# SPREAD
# ============================================================

MAX_SPREAD_PERCENT = 0.15


# ============================================================
# SCORE
# ============================================================

MIN_SCORE = 70


# ============================================================
# SCAN
# ============================================================

SCAN_INTERVAL = 60

MAX_WORKERS = 8

REQUEST_TIMEOUT = 10


# ============================================================
# CMC CACHE
# ============================================================

CMC_REFRESH_SECONDS = 10 * 60

cmc_cache = {}

cmc_cache_time = 0

cmc_lock = threading.Lock()


# ============================================================
# COOLDOWN
# ============================================================

SIGNAL_COOLDOWN_SECONDS = 24 * 60 * 60


# ============================================================
# SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "BinanceSpot15MBreakoutBot/3.0",
    "Accept": "application/json"
})


# ============================================================
# GLOBALS
# ============================================================

spot_symbols = {}

last_alert = {}

lock = threading.Lock()


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN missing")
        return False

    if not TELEGRAM_CHAT_ID:
        print("ERROR: TELEGRAM_CHAT_ID missing")
        return False

    url = (
        "https://api.telegram.org/bot"
        + TELEGRAM_BOT_TOKEN
        + "/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": True
    }

    try:

        r = session.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        if r.status_code == 200:
            return True

        print(
            "Telegram error:",
            r.status_code,
            r.text[:500]
        )

    except Exception as e:

        print(
            "Telegram exception:",
            repr(e)
        )

    return False


# ============================================================
# BINANCE GET
# ============================================================

def binance_get(
    path,
    params=None
):

    try:

        r = session.get(
            BINANCE_REST + path,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        if r.status_code != 200:

            print(
                "Binance HTTP error:",
                r.status_code,
                path,
                r.text[:300]
            )

            return None

        return r.json()

    except Exception as e:

        print(
            "Binance request error:",
            path,
            repr(e)
        )

        return None


# ============================================================
# CMC GET
# ============================================================

def cmc_get(
    path,
    params=None
):

    if not CMC_API_KEY:

        print(
            "ERROR: CMC_API_KEY missing"
        )

        return None

    headers = {
        "Accept": "application/json",
        "X-CMC_PRO_API_KEY": CMC_API_KEY
    }

    try:

        r = session.get(
            CMC_REST + path,
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT
        )

        if r.status_code != 200:

            print(
                "CMC HTTP ERROR:",
                r.status_code,
                r.text[:1000]
            )

            return None

        data = r.json()

        status = data.get(
            "status",
            {}
        )

        error_code = status.get(
            "error_code",
            0
        )

        error_message = status.get(
            "error_message",
            ""
        )

        if error_code not in (0, None):

            print(
                "CMC API ERROR:",
                error_code,
                error_message
            )

            return None

        return data

    except Exception as e:

        print(
            "CMC REQUEST ERROR:",
            repr(e)
        )

        return None


# ============================================================
# LOAD BINANCE SPOT SYMBOLS
# ============================================================

def load_spot_symbols():

    global spot_symbols

    data = binance_get(
        "/api/v3/exchangeInfo"
    )

    if not data:
        return False

    result = {}

    for item in data.get(
        "symbols",
        []
    ):

        try:

            symbol = item["symbol"]

            if item.get(
                "status"
            ) != "TRADING":
                continue

            if item.get(
                "quoteAsset"
            ) != "USDT":
                continue

            if not item.get(
                "isSpotTradingAllowed",
                False
            ):
                continue

            base = item.get(
                "baseAsset",
                ""
            ).upper()

            if base.endswith(
                (
                    "UP",
                    "DOWN",
                    "BULL",
                    "BEAR"
                )
            ):
                continue

            result[symbol] = {
                "base": base,
                "quote": "USDT"
            }

        except Exception:
            continue

    spot_symbols = result

    print(
        "BINANCE SPOT USDT SYMBOLS:",
        len(spot_symbols)
    )

    return True


# ============================================================
# CMC MARKET CAP
#
# IMPORTANT FIX
#
# CMC official modern endpoint:
#
# /v3/cryptocurrency/listings/latest
#
# We request market_cap_min directly.
#
# If CMC returns no data with the server-side filter,
# we retry without that filter and apply >= $200M locally.
# ============================================================

def load_cmc_market_caps(
    force=False
):

    global cmc_cache
    global cmc_cache_time

    now = time.time()

    with cmc_lock:

        if (
            not force
            and cmc_cache
            and now - cmc_cache_time
            < CMC_REFRESH_SECONDS
        ):

            return dict(cmc_cache)

    print()
    print(
        "========== CMC MARKET CAP LOAD =========="
    )

    if not CMC_API_KEY:

        print(
            "ERROR: CMC_API_KEY is not configured."
        )

        return {}

    # --------------------------------------------------------
    # FIRST TRY
    #
    # Server-side market cap filter
    # --------------------------------------------------------

    params = {
        "start": 1,
        "limit": 1000,
        "convert": "USD",
        "market_cap_min": MIN_MARKET_CAP,
        "sort": "market_cap",
        "sort_dir": "desc"
    }

    data = cmc_get(
        "/v3/cryptocurrency/listings/latest",
        params
    )

    records = []

    if data:

        records = data.get(
            "data",
            []
        )

    print(
        "CMC filtered records:",
        len(records)
    )

    # --------------------------------------------------------
    # FALLBACK
    #
    # If CMC returned no records, retry without
    # market_cap_min.
    #
    # Then apply the filter locally.
    # --------------------------------------------------------

    if not records:

        print(
            "CMC filtered request returned no data."
        )

        print(
            "Retrying CMC without market_cap_min..."
        )

        fallback_params = {
            "start": 1,
            "limit": 1000,
            "convert": "USD",
            "sort": "market_cap",
            "sort_dir": "desc"
        }

        fallback = cmc_get(
            "/v3/cryptocurrency/listings/latest",
            fallback_params
        )

        if fallback:

            records = fallback.get(
                "data",
                []
            )

            print(
                "CMC fallback records:",
                len(records)
            )

    if not records:

        print(
            "ERROR: CMC returned no cryptocurrency records."
        )

        print(
            "Check CMC_API_KEY and CMC API plan."
        )

        return {}

    # --------------------------------------------------------
    # BUILD MARKET CAP MAP
    #
    # Symbol -> highest valid market cap
    #
    # We keep the highest value if CMC contains
    # duplicate symbols.
    # --------------------------------------------------------

    result = {}

    for coin in records:

        try:

            symbol = str(
                coin.get(
                    "symbol",
                    ""
                )
            ).upper().strip()

            if not symbol:
                continue

            quote = coin.get(
                "quote",
                {}
            )

            usd = quote.get(
                "USD",
                {}
            )

            market_cap = float(
                usd.get(
                    "market_cap",
                    0
                ) or 0
            )

            if market_cap < MIN_MARKET_CAP:
                continue

            old = result.get(
                symbol
            )

            if (
                old is None
                or market_cap > old
            ):

                result[symbol] = market_cap

        except Exception:
            continue

    with cmc_lock:

        cmc_cache = result

        cmc_cache_time = time.time()

    print(
        "CMC coins >= $200M:",
        len(result)
    )

    if result:

        print(
            "CMC market-cap data loaded successfully."
        )

    else:

        print(
            "ERROR: No CMC coins passed $200M."
        )

    print(
        "=========================================="
    )
    print()

    return dict(result)


# ============================================================
# 24H TICKERS
# ============================================================

def get_24h_tickers():

    data = binance_get(
        "/api/v3/ticker/24hr"
    )

    if not data:
        return {}

    result = {}

    for item in data:

        symbol = item.get(
            "symbol"
        )

        if symbol not in spot_symbols:
            continue

        try:

            quote_volume = float(
                item.get(
                    "quoteVolume",
                    0
                )
            )

            last_price = float(
                item.get(
                    "lastPrice",
                    0
                )
            )

            if (
                quote_volume
                < MIN_24H_QUOTE_VOLUME
            ):
                continue

            if last_price <= 0:
                continue

            result[symbol] = {
                "quote_volume": quote_volume,
                "price": last_price
            }

        except Exception:
            continue

    return result


# ============================================================
# GET KLINES
# ============================================================

def get_klines(
    symbol,
    interval,
    limit=1000,
    end_time=None
):

    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": min(
            limit,
            1000
        )
    }

    if end_time is not None:

        params[
            "endTime"
        ] = end_time

    return binance_get(
        "/api/v3/klines",
        params
    )


# ============================================================
# CLOSED CANDLES
# ============================================================

def only_closed(
    klines
):

    if not klines:
        return []

    now_ms = int(
        time.time() * 1000
    )

    result = []

    for candle in klines:

        try:

            close_time = int(
                candle[6]
            )

            if close_time <= now_ms:

                result.append(
                    candle
                )

        except Exception:
            continue

    return result


# ============================================================
# LOAD EXACT CLOSED HISTORY
# ============================================================

def load_closed_history(
    symbol,
    interval,
    required
):

    collected = {}

    first_limit = min(
        1000,
        required + 2
    )

    data = get_klines(
        symbol,
        interval,
        first_limit
    )

    if not data:
        return []

    for candle in data:

        try:

            open_time = int(
                candle[0]
            )

            collected[
                open_time
            ] = candle

        except Exception:
            pass

    while True:

        closed = only_closed(
            list(
                collected.values()
            )
        )

        if len(closed) >= required:
            break

        if not collected:
            break

        oldest_open_time = min(
            collected.keys()
        )

        data = get_klines(
            symbol,
            interval,
            1000,
            oldest_open_time - 1
        )

        if not data:
            break

        before = len(
            collected
        )

        for candle in data:

            try:

                open_time = int(
                    candle[0]
                )

                collected[
                    open_time
                ] = candle

            except Exception:
                pass

        if len(collected) == before:
            break

        if len(collected) > (
            required + 2500
        ):
            break

    closed = only_closed(
        list(
            collected.values()
        )
    )

    closed.sort(
        key=lambda x: int(x[0])
    )

    if len(closed) >= required:

        return closed[
            -required:
        ]

    return closed


# ============================================================
# SPREAD
# ============================================================

def get_spread(
    symbol
):

    data = binance_get(
        "/api/v3/ticker/bookTicker",
        {
            "symbol": symbol
        }
    )

    if not data:
        return None

    try:

        bid = float(
            data["bidPrice"]
        )

        ask = float(
            data["askPrice"]
        )

        if bid <= 0 or ask <= 0:
            return None

        mid = (
            bid + ask
        ) / 2

        spread = (
            (ask - bid)
            / mid
            * 100
        )

        return spread

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

    multiplier = (
        2
        / (
            period + 1
        )
    )

    ema = (
        sum(
            values[
                :period
            ]
        )
        / period
    )

    for price in values[
        period:
    ]:

        ema = (
            (
                price - ema
            )
            * multiplier
            + ema
        )

    return ema


# ============================================================
# RSI
# ============================================================

def calculate_rsi(
    closes,
    period=14
):

    if len(closes) < (
        period + 1
    ):
        return None

    gains = []
    losses = []

    for i in range(1, len(closes)):

        change = (
            closes[i]
            - closes[i - 1]
        )

        if change > 0:

            gains.append(
                change
            )

            losses.append(
                0
            )

        else:

            gains.append(
                0
            )

            losses.append(
                abs(change)
            )

    avg_gain = (
        sum(
            gains[
                :period
            ]
        )
        / period
    )

    avg_loss = (
        sum(
            losses[
                :period
            ]
        )
        / period
    )

    for i in range(
        period,
        len(gains)
    ):

        avg_gain = (
            (
                avg_gain
                * (
                    period - 1
                )
            )
            + gains[i]
        ) / period

        avg_loss = (
            (
                avg_loss
                * (
                    period - 1
                )
            )
            + losses[i]
        ) / period

    if avg_loss == 0:

        return 100.0

    rs = (
        avg_gain
        / avg_loss
    )

    return (
        100
        - (
            100
            / (
                1 + rs
            )
        )
    )


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    candles,
    period=14
):

    if len(candles) < (
        period + 1
    ):
        return None

    true_ranges = []

    for i in range(
        1,
        len(candles)
    ):

        try:

            high = float(
                candles[i][2]
            )

            low = float(
                candles[i][3]
            )

            previous_close = float(
                candles[i - 1][4]
            )

            tr = max(
                high - low,
                abs(
                    high
                    - previous_close
                ),
                abs(
                    low
                    - previous_close
                )
            )

            true_ranges.append(
                tr
            )

        except Exception:
            continue

    if len(true_ranges) < period:
        return None

    atr = (
        sum(
            true_ranges[
                :period
            ]
        )
        / period
    )

    for tr in true_ranges[
        period:
    ]:

        atr = (
            (
                atr
                * (
                    period - 1
                )
            )
            + tr
        ) / period

    return atr


# ============================================================
# CANDLE QUALITY
# ============================================================

def candle_quality(
    candle
):

    try:

        high = float(
            candle[2]
        )

        low = float(
            candle[3]
        )

        close = float(
            candle[4]
        )

        open_price = float(
            candle[1]
        )

    except Exception:
        return None

    candle_range = (
        high - low
    )

    if candle_range <= 0:
        return None

    close_position = (
        (
            close - low
        )
        / candle_range
        * 100
    )

    body_top = max(
        open_price,
        close
    )

    upper_wick = (
        high - body_top
    )

    upper_wick_percent = (
        upper_wick
        / candle_range
        * 100
    )

    return {
        "close_position":
            close_position,
        "upper_wick":
            upper_wick_percent
    }


# ============================================================
# 5 CANDLE CONSOLIDATION
# ============================================================

def check_consolidation(
    history
):

    if len(history) < (
        CONSOLIDATION_CANDLES + 1
    ):
        return None

    candles = history[
        -(
            CONSOLIDATION_CANDLES + 1
        ):
        -1
    ]

    highs = []
    lows = []

    for candle in candles:

        try:

            highs.append(
                float(candle[2])
            )

            lows.append(
                float(candle[3])
            )

        except Exception:
            return None

    highest = max(
        highs
    )

    lowest = min(
        lows
    )

    if lowest <= 0:
        return None

    range_percent = (
        (
            highest - lowest
        )
        / lowest
        * 100
    )

    if (
        range_percent
        > MAX_CONSOLIDATION_RANGE_PERCENT
    ):
        return None

    return {
        "range_percent":
            range_percent
    }


# ============================================================
# MAIN ANALYSIS
# ============================================================

def analyze_symbol(
    symbol,
    ticker,
    market_cap
):

    try:

        # ----------------------------------------------------
        # COOLDOWN
        # ----------------------------------------------------

        if is_on_cooldown(
            symbol
        ):
            return None

        # ----------------------------------------------------
        # 500 CLOSED 15M CANDLES
        # ----------------------------------------------------

        history = load_closed_history(
            symbol,
            INTERVAL,
            HISTORY_CANDLES
        )

        if len(history) != HISTORY_CANDLES:

            return None

        # ----------------------------------------------------
        # Latest CLOSED candle
        # ----------------------------------------------------

        current = history[-1]

        try:

            open_price = float(
                current[1]
            )

            high = float(
                current[2]
            )

            low = float(
                current[3]
            )

            close = float(
                current[4]
            )

            volume = float(
                current[5]
            )

            quote_volume = float(
                current[7]
            )

        except Exception:

            return None

        if open_price <= 0:
            return None

        # ====================================================
        # MOMENTUM
        # ====================================================

        momentum = (
            (
                close
                - open_price
            )
            / open_price
            * 100
        )

        if momentum < MIN_MOMENTUM_PERCENT:
            return None

        if momentum > MAX_MOMENTUM_PERCENT:
            return None

        # ====================================================
        # 100 CANDLE RESISTANCE
        #
        # Previous 100 CLOSED candles
        # excluding current breakout candle
        # ====================================================

        resistance_history = history[
            -(
                BREAKOUT_LOOKBACK + 1
            ):
            -1
        ]

        if len(
            resistance_history
        ) != BREAKOUT_LOOKBACK:

            return None

        resistance = max(
            float(c[2])
            for c in resistance_history
        )

        if resistance <= 0:
            return None

        # ====================================================
        # BREAKOUT
        # ====================================================

        breakout_percent = (
            (
                close
                - resistance
            )
            / resistance
            * 100
        )

        if (
            breakout_percent
            < MIN_BREAKOUT_PERCENT
        ):
            return None

        # ====================================================
        # 5 CANDLE CONSOLIDATION
        # ====================================================

        consolidation = (
            check_consolidation(
                history
            )
        )

        if not consolidation:
            return None

        # ====================================================
        # VOLUME
        #
        # Current breakout candle vs previous 20
        # ====================================================

        volume_reference = history[
            -(
                BREAKOUT_VOLUME_AVERAGE + 1
            ):
            -1
        ]

        reference_volumes = []

        for candle in volume_reference:

            try:

                reference_volumes.append(
                    float(candle[5])
                )

            except Exception:
                pass

        if len(
            reference_volumes
        ) != BREAKOUT_VOLUME_AVERAGE:

            return None

        average_volume = (
            sum(
                reference_volumes
            )
            / len(
                reference_volumes
            )
        )

        if average_volume <= 0:
            return None

        volume_ratio = (
            volume
            / average_volume
        )

        if (
            volume_ratio
            < MIN_BREAKOUT_VOLUME_RATIO
        ):
            return None

        # ====================================================
        # CLOSE POSITION / UPPER WICK
        # ====================================================

        quality = candle_quality(
            current
        )

        if not quality:
            return None

        close_position = (
            quality[
                "close_position"
            ]
        )

        upper_wick = (
            quality[
                "upper_wick"
            ]
        )

        if (
            close_position
            < MIN_CLOSE_POSITION
        ):
            return None

        if (
            upper_wick
            > MAX_UPPER_WICK
        ):
            return None

        # ====================================================
        # EMA
        # ====================================================

        closes = []

        for candle in history:

            try:

                closes.append(
                    float(candle[4])
                )

            except Exception:
                return None

        ema20 = calculate_ema(
            closes,
            EMA_FAST
        )

        ema50 = calculate_ema(
            closes,
            EMA_MID
        )

        ema200 = calculate_ema(
            closes,
            EMA_SLOW
        )

        if (
            ema20 is None
            or ema50 is None
            or ema200 is None
        ):
            return None

        if not (
            ema20
            > ema50
            > ema200
        ):
            return None

        # ====================================================
        # RSI
        # ====================================================

        rsi = calculate_rsi(
            closes,
            RSI_PERIOD
        )

        if rsi is None:
            return None

        if rsi < MIN_RSI:
            return None

        if rsi > MAX_RSI:
            return None

        # ====================================================
        # ATR14
        # ====================================================

        atr = calculate_atr(
            history,
            ATR_PERIOD
        )

        if atr is None:
            return None

        atr_percent = (
            atr
            / close
            * 100
        )

        if (
            atr_percent
            < MIN_ATR_PERCENT
        ):
            return None

        # ====================================================
        # SPREAD
        # ====================================================

        spread = get_spread(
            symbol
        )

        if spread is None:
            return None

        if (
            spread
            > MAX_SPREAD_PERCENT
        ):
            return None

        # ====================================================
        # SCORE
        #
        # Maximum = 100
        #
        # Momentum       15
        # Breakout       20
        # Volume         20
        # EMA            15
        # RSI            10
        # Consolidation  5
        # Candle quality 10
        # Spread          5
        # ====================================================

        score = 0

        # Momentum
        if momentum >= 2.0:
            score += 15
        else:
            score += 10

        # Breakout
        if breakout_percent >= 1.0:
            score += 20
        else:
            score += 15

        # Volume
        if volume_ratio >= 2.5:
            score += 20
        else:
            score += 15

        # EMA
        score += 15

        # RSI
        if 55 <= rsi <= 70:
            score += 10
        else:
            score += 7

        # Consolidation
        if (
            consolidation[
                "range_percent"
            ] <= 1.0
        ):
            score += 5
        else:
            score += 3

        # Candle quality
        if (
            close_position >= 85
            and upper_wick <= 15
        ):
            score += 10
        else:
            score += 7

        # Spread
        if spread <= 0.08:
            score += 5
        else:
            score += 3

        if score < MIN_SCORE:
            return None

        # ====================================================
        # SIGNAL
        # ====================================================

        return {
            "symbol": symbol,
            "market_cap": market_cap,
            "price": close,
            "momentum": momentum,
            "resistance": resistance,
            "breakout_percent":
                breakout_percent,
            "volume_ratio":
                volume_ratio,
            "consolidation":
                consolidation[
                    "range_percent"
                ],
            "ema20": ema20,
            "ema50": ema50,
            "ema200": ema200,
            "rsi": rsi,
            "atr": atr,
            "atr_percent": atr_percent,
            "close_position":
                close_position,
            "upper_wick":
                upper_wick,
            "spread": spread,
            "score": score,
            "quote_volume":
                ticker[
                    "quote_volume"
                ]
        }

    except Exception as e:

        print(
            f"{symbol} ERROR:",
            repr(e)
        )

        return None


# ============================================================
# COOLDOWN
# ============================================================

def is_on_cooldown(
    symbol
):

    now = time.time()

    with lock:

        previous = last_alert.get(
            symbol
        )

        if previous is None:
            return False

        return (
            now - previous
            < SIGNAL_COOLDOWN_SECONDS
        )


def set_alert_time(
    symbol
):

    with lock:

        last_alert[
            symbol
        ] = time.time()


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

def build_signal(
    signal
):

    return (
        "🟢 BINANCE SPOT 15M BREAKOUT\n"
        "\n"
        f"🪙 {signal['symbol']}\n"
        f"💰 Price: {signal['price']:.12g}\n"
        f"💎 Market Cap: "
        f"${signal['market_cap']:,.0f}\n"
        "\n"
        "⚡ 15M MOMENTUM\n"
        f"📈 Momentum: "
        f"+{signal['momentum']:.2f}%\n"
        "\n"
        "🚀 REAL BREAKOUT\n"
        f"🔴 Resistance: "
        f"{signal['resistance']:.12g}\n"
        f"🚀 Breakout: "
        f"+{signal['breakout_percent']:.2f}%\n"
        "\n"
        "📊 VOLUME\n"
        f"🔥 Volume: "
        f"{signal['volume_ratio']:.2f}x\n"
        f"💧 24H Volume: "
        f"${signal['quote_volume']:,.0f}\n"
        "\n"
        "📐 TREND\n"
        f"EMA20: {signal['ema20']:.12g}\n"
        f"EMA50: {signal['ema50']:.12g}\n"
        f"EMA200: {signal['ema200']:.12g}\n"
        "🟢 EMA20 > EMA50 > EMA200\n"
        "\n"
        "📊 RSI / ATR\n"
        f"RSI14: {signal['rsi']:.1f}\n"
        f"ATR14: {signal['atr_percent']:.2f}%\n"
        "\n"
        "🕯️ CANDLE QUALITY\n"
        f"Close Position: "
        f"{signal['close_position']:.1f}%\n"
        f"Upper Wick: "
        f"{signal['upper_wick']:.1f}%\n"
        "\n"
        "📦 CONSOLIDATION\n"
        f"5 Candle Range: "
        f"{signal['consolidation']:.2f}%\n"
        "\n"
        f"↔️ Spread: "
        f"{signal['spread']:.3f}%\n"
        f"🏆 SCORE: "
        f"{signal['score']}/100\n"
        "\n"
        "📚 500 CLOSED 15M CANDLES\n"
        "📚 Resistance = previous 100 candles\n"
        "📦 Consolidation = previous 5 candles\n"
        "\n"
        "🟢 Binance Spot USDT ONLY\n"
        "💎 CMC Market Cap >= $200M\n"
        "❌ Solana OFF\n"
        "❌ Binance Square OFF\n"
        "\n"
        "⚠️ TELEGRAM ALERT ONLY\n"
        "❌ NO AUTOMATIC ORDER"
    )


# ============================================================
# MAIN SCAN LOOP
# ============================================================

def binance_scan_loop():

    print()
    print(
        "=" * 65
    )
    print(
        "🟢 BINANCE SPOT 15M ALERT BOT"
    )
    print(
        "=" * 65
    )
    print(
        "CMC MARKET CAP      : >= $200M"
    )
    print(
        "BINANCE             : USDT SPOT ONLY"
    )
    print(
        "24H VOLUME          : >= $10M"
    )
    print(
        "TIMEFRAME           : 15M"
    )
    print(
        "HISTORY             : 500 CLOSED"
    )
    print(
        "RESISTANCE          : 100 CLOSED"
    )
    print(
        "CONSOLIDATION       : 5 CANDLES"
    )
    print(
        "BREAKOUT            : >= +0.5%"
    )
    print(
        "MOMENTUM            : +1% TO +4%"
    )
    print(
        "VOLUME              : >= 1.8x"
    )
    print(
        "EMA                 : 20 > 50 > 200"
    )
    print(
        "RSI                 : 50 - 75"
    )
    print(
        "ATR14               : >= 0.20%"
    )
    print(
        "CLOSE POSITION      : >= 75%"
    )
    print(
        "UPPER WICK          : <= 25%"
    )
    print(
        "SPREAD              : <= 0.15%"
    )
    print(
        "SCORE               : >= 70"
    )
    print(
        "COOLDOWN            : 24 HOURS"
    )
    print(
        "AUTO ORDER          : OFF"
    )
    print(
        "=" * 65
    )
    print()

    while True:

        cycle_start = time.time()

        try:

            # =================================================
            # CMC
            # =================================================

            market_caps = (
                load_cmc_market_caps()
            )

            if not market_caps:

                print(
                    "[SCAN] CMC market-cap data unavailable."
                )

                time.sleep(
                    SCAN_INTERVAL
                )

                continue

            # =================================================
            # BINANCE 24H
            # =================================================

            tickers = (
                get_24h_tickers()
            )

            if not tickers:

                print(
                    "[SCAN] No Binance symbols passed 24H volume."
                )

                time.sleep(
                    SCAN_INTERVAL
                )

                continue

            # =================================================
            # CMC + BINANCE INTERSECTION
            # =================================================

            candidates = {}

            for symbol, ticker in tickers.items():

                info = spot_symbols.get(
                    symbol
                )

                if not info:
                    continue

                base = info.get(
                    "base",
                    ""
                ).upper()

                market_cap = market_caps.get(
                    base
                )

                if market_cap is None:
                    continue

                if market_cap < MIN_MARKET_CAP:
                    continue

                candidates[
                    symbol
                ] = {
                    "ticker": ticker,
                    "market_cap":
                        market_cap
                }

            print()
            print(
                "[SCAN]"
            )

            print(
                "Binance 24H >= $10M:",
                len(tickers)
            )

            print(
                "CMC >= $200M:",
                len(market_caps)
            )

            print(
                "Final candidates:",
                len(candidates)
            )

            if not candidates:

                elapsed = (
                    time.time()
                    - cycle_start
                )

                print(
                    f"[SCAN COMPLETE] "
                    f"{elapsed:.1f}s | "
                    "No candidates"
                )

                time.sleep(
                    max(
                        1,
                        SCAN_INTERVAL
                        - elapsed
                    )
                )

                continue

            # =================================================
            # PARALLEL ANALYSIS
            # =================================================

            signals = 0

            with ThreadPoolExecutor(
                max_workers=MAX_WORKERS
            ) as executor:

                futures = {}

                for symbol, data in candidates.items():

                    future = executor.submit(
                        analyze_symbol,
                        symbol,
                        data["ticker"],
                        data["market_cap"]
                    )

                    futures[
                        future
                    ] = symbol

                for future in as_completed(
                    futures
                ):

                    symbol = futures[
                        future
                    ]

                    try:

                        signal = (
                            future.result()
                        )

                        if not signal:
                            continue

                        message = (
                            build_signal(
                                signal
                            )
                        )

                        set_alert_time(
                            symbol
                        )

                        signals += 1

                        print(
                            f"[SIGNAL] "
                            f"{symbol} | "
                            f"Score "
                            f"{signal['score']}"
                        )

                        send_telegram(
                            message
                        )

                    except Exception as e:

                        print(
                            f"{symbol} "
                            f"FUTURE ERROR:",
                            repr(e)
                        )

            elapsed = (
                time.time()
                - cycle_start
            )

            print(
                f"[SCAN COMPLETE] "
                f"{elapsed:.1f}s | "
                f"Signals: {signals}"
            )

        except Exception as e:

            print(
                "[SCAN LOOP ERROR]",
                repr(e)
            )

        elapsed = (
            time.time()
            - cycle_start
        )

        sleep_time = max(
            1,
            SCAN_INTERVAL
            - elapsed
        )

        print(
            f"Next scan in "
            f"{sleep_time:.1f}s"
        )

        time.sleep(
            sleep_time
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print(
        "=" * 65
    )
    print(
        "🟢 BINANCE SPOT 15M ALERT BOT STARTING"
    )
    print(
        "=" * 65
    )
    print()

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    if not TELEGRAM_BOT_TOKEN:

        print(
            "ERROR: TELEGRAM_BOT_TOKEN missing."
        )

        return

    if not TELEGRAM_CHAT_ID:

        print(
            "ERROR: TELEGRAM_CHAT_ID missing."
        )

        return

    # --------------------------------------------------------
    # CMC
    # --------------------------------------------------------

    if not CMC_API_KEY:

        print(
            "ERROR: CMC_API_KEY missing."
        )

        return

    # --------------------------------------------------------
    # BINANCE
    # --------------------------------------------------------

    if not load_spot_symbols():

        print(
            "ERROR: Binance Spot symbols "
            "could not be loaded."
        )

        return

    # --------------------------------------------------------
    # TEST CMC BEFORE STARTING
    # --------------------------------------------------------

    market_caps = (
        load_cmc_market_caps(
            force=True
        )
    )

    if not market_caps:

        print()
        print(
            "================================================"
        )
        print(
            "CMC TEST FAILED"
        )
        print(
            "Bot will NOT start."
        )
        print(
            "Check CMC_API_KEY in Railway Variables."
        )
        print(
            "================================================"
        )

        return

    # --------------------------------------------------------
    # TELEGRAM STARTUP
    # --------------------------------------------------------

    send_telegram(
        "🟢 BINANCE SPOT 15M ALERT BOT STARTED\n\n"
        "💎 CMC Market Cap >= $200M\n"
        "💧 24H Volume >= $10M\n"
        "📊 15M Timeframe\n"
        "📚 500 CLOSED 15M candles\n"
        "🔴 Previous 100 candle resistance\n"
        "📦 5 candle consolidation\n"
        "🚀 Breakout >= +0.5%\n"
        "⚡ Momentum +1% to +4%\n"
        "🔥 Volume >= 1.8x\n"
        "📈 EMA20 > EMA50 > EMA200\n"
        "📊 RSI 50-75\n"
        "📐 ATR14 activity filter\n"
        "🕯️ Close Position >= 75%\n"
        "🕯️ Upper Wick <= 25%\n"
        "↔️ Spread <= 0.15%\n"
        "🏆 Score >= 70\n"
        "⏱️ Cooldown 24 hours\n\n"
        "🟢 Binance Spot USDT ONLY\n"
        "⚠️ ALERT ONLY\n"
        "❌ NO AUTOMATIC ORDER"
    )

    # --------------------------------------------------------
    # START
    # --------------------------------------------------------

    binance_scan_loop()


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    main()
