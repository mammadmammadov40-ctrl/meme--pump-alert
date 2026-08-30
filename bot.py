import os
import time
import threading
import requests

from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# BINANCE SPOT 15M ALERT BOT
#
# STRATEGY
#
# CMC MARKET CAP >= $200M
# BINANCE USDT SPOT ONLY
# 24H VOLUME >= $10M
#
# 15M MAIN TIMEFRAME
# 500 CLOSED 15M CANDLES
#
# 100 CANDLES = BREAKOUT RESISTANCE
# 5 CANDLES   = PRE-BREAKOUT CONSOLIDATION
#
# BREAKOUT >= +0.5%
# MOMENTUM +1% TO +4%
# VOLUME >= 1.8X
#
# EMA20 > EMA50 > EMA200
# RSI 50-75
# ATR14 ACTIVE FILTER
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
#
# ============================================================


# ============================================================
# ENV
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
).strip()

CMC_API_KEY = (
    os.getenv("CMC_API_KEY", "")
    or os.getenv("CMC_PRO_API_KEY", "")
).strip()


# ============================================================
# URLS
# ============================================================

BINANCE_REST = "https://api.binance.com"

CMC_BASE = "https://pro-api.coinmarketcap.com"


# ============================================================
# SETTINGS
# ============================================================

# ONLY 15M
INTERVAL = "15m"


# ============================================================
# HISTORY
# ============================================================

HISTORY_CANDLES = 500


# ============================================================
# CMC
# ============================================================

MIN_MARKET_CAP = 200_000_000

CMC_LIMIT = 500

CMC_REFRESH_SECONDS = 300


# ============================================================
# BINANCE 24H VOLUME
# ============================================================

MIN_24H_QUOTE_VOLUME = 10_000_000


# ============================================================
# MOMENTUM
# ============================================================

MIN_MOMENTUM_PERCENT = 1.0

MAX_MOMENTUM_PERCENT = 4.0


# ============================================================
# VOLUME
# ============================================================

VOLUME_AVERAGE_CANDLES = 20

MIN_VOLUME_RATIO = 1.80


# ============================================================
# BREAKOUT
# ============================================================

BREAKOUT_LOOKBACK = 100

MIN_BREAKOUT_PERCENT = 0.50


# ============================================================
# CONSOLIDATION
# ============================================================

CONSOLIDATION_CANDLES = 5

MAX_CONSOLIDATION_RANGE_PERCENT = 2.50


# ============================================================
# EMA
# ============================================================

EMA_FAST = 20

EMA_MIDDLE = 50

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

MIN_ATR_PERCENT = 0.20


# ============================================================
# CANDLE QUALITY
# ============================================================

MIN_CLOSE_POSITION = 75.0

MAX_UPPER_WICK_PERCENT = 25.0


# ============================================================
# SPREAD
# ============================================================

MAX_SPREAD_PERCENT = 0.15


# ============================================================
# SCORE
# ============================================================

MIN_SCORE = 70


# ============================================================
# COOLDOWN
# ============================================================

SIGNAL_COOLDOWN_SECONDS = 24 * 60 * 60


# ============================================================
# SCAN
# ============================================================

SCAN_INTERVAL = 60

MAX_WORKERS = 8

REQUEST_TIMEOUT = 10


# ============================================================
# SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "BinanceSpot15MAlertBot/4.0",
    "Accept": "application/json"
})


# ============================================================
# GLOBALS
# ============================================================

spot_symbols = {}

last_alert = {}

cmc_market_caps = {}

cmc_last_update = 0

lock = threading.Lock()


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN:

        print(
            "ERROR: TELEGRAM_BOT_TOKEN missing"
        )

        return False

    if not TELEGRAM_CHAT_ID:

        print(
            "ERROR: TELEGRAM_CHAT_ID missing"
        )

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

        response = session.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code == 200:

            return True

        print(
            "Telegram error:",
            response.status_code,
            response.text[:300]
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

        response = session.get(
            BINANCE_REST + path,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:

            print(
                "Binance HTTP:",
                response.status_code,
                path,
                response.text[:200]
            )

            return None

        return response.json()

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
            "CMC ERROR: API KEY MISSING"
        )

        return None

    headers = {
        "Accept": "application/json",
        "X-CMC_PRO_API_KEY": CMC_API_KEY
    }

    try:

        url = CMC_BASE + path

        response = session.get(
            url,
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        print(
            "CMC HTTP:",
            response.status_code,
            path
        )

        # ----------------------------------------------------
        # HTTP ERROR
        # ----------------------------------------------------

        if response.status_code != 200:

            try:

                body = response.json()

                status = body.get(
                    "status",
                    {}
                )

                print(
                    "CMC ERROR CODE:",
                    status.get(
                        "error_code"
                    )
                )

                print(
                    "CMC ERROR MESSAGE:",
                    status.get(
                        "error_message"
                    )
                )

            except Exception:

                print(
                    "CMC RAW ERROR:",
                    response.text[:500]
                )

            return None

        # ----------------------------------------------------
        # JSON
        # ----------------------------------------------------

        data = response.json()

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

            print(
                "CMC API ERROR:",
                error_code
            )

            print(
                "CMC MESSAGE:",
                status.get(
                    "error_message"
                )
            )

            return None

        return data

    except Exception as e:

        print(
            "CMC REQUEST EXCEPTION:",
            repr(e)
        )

        return None


# ============================================================
# LOAD CMC MARKET CAPS
# ============================================================

def load_cmc_market_caps(
    force=False
):

    global cmc_market_caps

    global cmc_last_update

    now = time.time()

    # --------------------------------------------------------
    # CACHE
    # --------------------------------------------------------

    if (
        not force
        and cmc_market_caps
        and (
            now - cmc_last_update
            < CMC_REFRESH_SECONDS
        )
    ):

        return cmc_market_caps

    print()
    print("=" * 70)
    print("CMC MARKET CAP UPDATE")
    print("=" * 70)

    if not CMC_API_KEY:

        print(
            "CMC ERROR: CMC_API_KEY IS EMPTY"
        )

        return None

    params = {
        "start": 1,
        "limit": CMC_LIMIT,
        "convert": "USD"
    }

    data = None

    # ========================================================
    # PRIMARY ENDPOINT
    # ========================================================

    print(
        "CMC: trying V1 listings endpoint..."
    )

    data = cmc_get(
        "/v1/cryptocurrency/listings/latest",
        params
    )

    # ========================================================
    # FALLBACK
    # ========================================================

    if not data:

        print(
            "CMC V1 failed."
        )

        print(
            "CMC: trying V3 fallback..."
        )

        data = cmc_get(
            "/v3/cryptocurrency/listings/latest",
            params
        )

    # ========================================================
    # COMPLETE FAILURE
    # ========================================================

    if not data:

        print()
        print(
            "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        )

        print(
            "CMC MARKET CAP DATA FAILED"
        )

        print(
            "Check CMC_API_KEY in Railway Variables."
        )

        print(
            "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        )

        return None

    # ========================================================
    # DATA
    # ========================================================

    coins = data.get(
        "data",
        []
    )

    if not isinstance(
        coins,
        list
    ):

        print(
            "CMC ERROR: DATA IS NOT A LIST"
        )

        return None

    if not coins:

        print(
            "CMC ERROR: EMPTY DATA"
        )

        return None

    result = {}

    # ========================================================
    # PARSE
    # ========================================================

    for coin in coins:

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

            usd = None

            # ------------------------------------------------
            # Standard CMC format
            # ------------------------------------------------

            if isinstance(
                quote,
                dict
            ):

                usd = quote.get(
                    "USD"
                )

            # ------------------------------------------------
            # Alternative format
            # ------------------------------------------------

            elif isinstance(
                quote,
                list
            ):

                for item in quote:

                    if not isinstance(
                        item,
                        dict
                    ):
                        continue

                    item_name = str(
                        item.get(
                            "name",
                            ""
                        )
                    ).upper()

                    item_symbol = str(
                        item.get(
                            "symbol",
                            ""
                        )
                    ).upper()

                    if (
                        item_name == "USD"
                        or item_symbol == "USD"
                    ):

                        usd = item

                        break

            if not isinstance(
                usd,
                dict
            ):
                continue

            market_cap = usd.get(
                "market_cap"
            )

            if market_cap is None:
                continue

            market_cap = float(
                market_cap
            )

            if market_cap <= 0:
                continue

            # ------------------------------------------------
            # Duplicate symbol protection
            # ------------------------------------------------

            previous = result.get(
                symbol
            )

            if (
                previous is None
                or market_cap > previous
            ):

                result[
                    symbol
                ] = market_cap

        except Exception:

            continue

    # ========================================================
    # RESULT
    # ========================================================

    if not result:

        print(
            "CMC ERROR: NO USABLE MARKET CAPS"
        )

        return None

    cmc_market_caps = result

    cmc_last_update = now

    qualified = sum(
        1
        for value in result.values()
        if value >= MIN_MARKET_CAP
    )

    print()
    print(
        "CMC COINS LOADED:",
        len(result)
    )

    print(
        "CMC >= $200M:",
        qualified
    )

    print(
        "CMC UPDATE SUCCESS"
    )

    print("=" * 70)
    print()

    return result


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

            symbol = item.get(
                "symbol"
            )

            if not symbol:
                continue

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

            # ------------------------------------------------
            # Remove leveraged tokens
            # ------------------------------------------------

            if base.endswith(
                (
                    "UP",
                    "DOWN",
                    "BULL",
                    "BEAR"
                )
            ):

                continue

            result[
                symbol
            ] = {
                "base_asset": base
            }

        except Exception:

            continue

    spot_symbols = result

    print(
        "BINANCE USDT SPOT SYMBOLS:",
        len(spot_symbols)
    )

    return True


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

            if quote_volume < MIN_24H_QUOTE_VOLUME:

                continue

            if last_price <= 0:

                continue

            result[
                symbol
            ] = {

                "quote_volume":
                    quote_volume,

                "price":
                    last_price,

                "base_asset":
                    spot_symbols[
                        symbol
                    ][
                        "base_asset"
                    ]
            }

        except Exception:

            continue

    return result


# ============================================================
# CMC MARKET CAP FILTER
# ============================================================

def filter_by_market_cap(
    tickers,
    market_caps
):

    if not market_caps:

        return {}

    result = {}

    for symbol, ticker in tickers.items():

        try:

            base = ticker[
                "base_asset"
            ].upper()

            market_cap = market_caps.get(
                base
            )

            if market_cap is None:

                continue

            if market_cap < MIN_MARKET_CAP:

                continue

            item = dict(
                ticker
            )

            item[
                "market_cap"
            ] = market_cap

            result[
                symbol
            ] = item

        except Exception:

            continue

    return result


# ============================================================
# GET 15M KLINES
# ============================================================

def get_klines(
    symbol,
    limit=500
):

    return binance_get(
        "/api/v3/klines",
        {
            "symbol": symbol,
            "interval": INTERVAL,
            "limit": min(
                limit,
                1000
            )
        }
    )


# ============================================================
# CLOSED CANDLES ONLY
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

    result.sort(
        key=lambda x: int(x[0])
    )

    return result


# ============================================================
# LOAD 500 CLOSED 15M CANDLES
# ============================================================

def load_history(
    symbol
):

    data = get_klines(
        symbol,
        HISTORY_CANDLES + 2
    )

    if not data:

        return []

    closed = only_closed(
        data
    )

    if len(closed) < HISTORY_CANDLES:

        return closed

    return closed[
        -HISTORY_CANDLES:
    ]


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
    closes,
    period
):

    if len(closes) < period:

        return None

    multiplier = (
        2.0
        / (period + 1)
    )

    ema = (
        sum(
            closes[
                :period
            ]
        )
        / period
    )

    for price in closes[
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

    if len(closes) <= period:

        return None

    gains = []

    losses = []

    for i in range(
        1,
        period + 1
    ):

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
        sum(gains)
        / period
    )

    avg_loss = (
        sum(losses)
        / period
    )

    if avg_loss == 0:

        return 100.0

    for i in range(
        period + 1,
        len(closes)
    ):

        change = (
            closes[i]
            - closes[i - 1]
        )

        gain = max(
            change,
            0
        )

        loss = max(
            -change,
            0
        )

        avg_gain = (
            (
                avg_gain
                * (period - 1)
            )
            + gain
        ) / period

        avg_loss = (
            (
                avg_loss
                * (period - 1)
            )
            + loss
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
            / (1 + rs)
        )
    )


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    history,
    period=14
):

    if len(history) < period + 1:

        return None

    true_ranges = []

    for i in range(
        1,
        len(history)
    ):

        try:

            high = float(
                history[i][2]
            )

            low = float(
                history[i][3]
            )

            previous_close = float(
                history[i - 1][4]
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
                * (period - 1)
            )
            + tr
        ) / period

    return atr


# ============================================================
# CANDLE VALUES
# ============================================================

def candle_values(
    candle
):

    return {

        "open":
            float(candle[1]),

        "high":
            float(candle[2]),

        "low":
            float(candle[3]),

        "close":
            float(candle[4]),

        "volume":
            float(candle[5]),

        "quote_volume":
            float(candle[7])
    }


# ============================================================
# ANALYZE SYMBOL
# ============================================================

def analyze_symbol(
    symbol,
    ticker
):

    try:

        # ====================================================
        # COOLDOWN
        # ====================================================

        if is_on_cooldown(
            symbol
        ):

            return None


        # ====================================================
        # LOAD 500 CLOSED 15M CANDLES
        # ====================================================

        history = load_history(
            symbol
        )

        if len(history) != HISTORY_CANDLES:

            print(
                f"{symbol}: "
                f"history "
                f"{len(history)}/"
                f"{HISTORY_CANDLES}"
            )

            return None


        # ====================================================
        # LATEST CLOSED 15M CANDLE
        # ====================================================

        breakout = candle_values(
            history[-1]
        )


        # ====================================================
        # PREVIOUS 100 CANDLES
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


        # ====================================================
        # RESISTANCE
        # ====================================================

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
                breakout["close"]
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
        # MOMENTUM
        # ====================================================

        if breakout["open"] <= 0:

            return None

        momentum = (
            (
                breakout["close"]
                - breakout["open"]
            )
            / breakout["open"]
            * 100
        )

        if momentum < MIN_MOMENTUM_PERCENT:

            return None

        if momentum > MAX_MOMENTUM_PERCENT:

            return None


        # ====================================================
        # VOLUME
        # ====================================================

        volume_reference = history[
            -(
                VOLUME_AVERAGE_CANDLES + 1
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

        if not reference_volumes:

            return None

        average_volume = (
            sum(reference_volumes)
            / len(reference_volumes)
        )

        if average_volume <= 0:

            return None

        volume_ratio = (
            breakout["volume"]
            / average_volume
        )

        if (
            volume_ratio
            < MIN_VOLUME_RATIO
        ):

            return None


        # ====================================================
        # 5 CANDLE CONSOLIDATION
        # ====================================================

        consolidation = history[
            -(
                CONSOLIDATION_CANDLES + 1
            ):
            -1
        ]

        if len(
            consolidation
        ) != CONSOLIDATION_CANDLES:

            return None

        consolidation_high = max(
            float(c[2])
            for c in consolidation
        )

        consolidation_low = min(
            float(c[3])
            for c in consolidation
        )

        consolidation_closes = [
            float(c[4])
            for c in consolidation
        ]

        average_consolidation_close = (
            sum(
                consolidation_closes
            )
            / len(
                consolidation_closes
            )
        )

        if (
            average_consolidation_close
            <= 0
        ):

            return None

        consolidation_range = (
            (
                consolidation_high
                - consolidation_low
            )
            / average_consolidation_close
            * 100
        )

        if (
            consolidation_range
            > MAX_CONSOLIDATION_RANGE_PERCENT
        ):

            return None


        # ====================================================
        # CLOSE POSITION
        # ====================================================

        candle_range = (
            breakout["high"]
            - breakout["low"]
        )

        if candle_range <= 0:

            return None

        close_position = (
            (
                breakout["close"]
                - breakout["low"]
            )
            / candle_range
            * 100
        )

        if (
            close_position
            < MIN_CLOSE_POSITION
        ):

            return None


        # ====================================================
        # UPPER WICK
        # ====================================================

        upper_wick = (
            breakout["high"]
            - max(
                breakout["open"],
                breakout["close"]
            )
        )

        upper_wick_percent = (
            upper_wick
            / candle_range
            * 100
        )

        if (
            upper_wick_percent
            > MAX_UPPER_WICK_PERCENT
        ):

            return None


        # ====================================================
        # EMA
        # ====================================================

        closes = [
            float(c[4])
            for c in history
        ]

        ema20 = calculate_ema(
            closes,
            EMA_FAST
        )

        ema50 = calculate_ema(
            closes,
            EMA_MIDDLE
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
        # ATR
        # ====================================================

        atr = calculate_atr(
            history,
            ATR_PERIOD
        )

        if atr is None:

            return None

        atr_percent = (
            atr
            / breakout["close"]
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
        # ====================================================

        score = 0


        # ----------------------------------------------------
        # Breakout
        # ----------------------------------------------------

        if breakout_percent >= 1.0:

            score += 15

        else:

            score += 10


        # ----------------------------------------------------
        # Volume
        # ----------------------------------------------------

        if volume_ratio >= 2.5:

            score += 20

        elif volume_ratio >= 2.0:

            score += 17

        elif volume_ratio >= 1.8:

            score += 15


        # ----------------------------------------------------
        # EMA
        # ----------------------------------------------------

        score += 15


        # ----------------------------------------------------
        # RSI
        # ----------------------------------------------------

        if 55 <= rsi <= 70:

            score += 10

        else:

            score += 8


        # ----------------------------------------------------
        # Momentum
        # ----------------------------------------------------

        if 1.5 <= momentum <= 3.0:

            score += 10

        else:

            score += 8


        # ----------------------------------------------------
        # Consolidation
        # ----------------------------------------------------

        if consolidation_range <= 1.5:

            score += 10

        else:

            score += 7


        # ----------------------------------------------------
        # Candle quality
        # ----------------------------------------------------

        if (
            close_position >= 85
            and upper_wick_percent <= 15
        ):

            score += 10

        else:

            score += 7


        # ----------------------------------------------------
        # ATR
        # ----------------------------------------------------

        if atr_percent >= 0.50:

            score += 5

        else:

            score += 3


        # ----------------------------------------------------
        # Spread
        # ----------------------------------------------------

        if spread <= 0.08:

            score += 5

        else:

            score += 3


        # ====================================================
        # SCORE FILTER
        # ====================================================

        if score < MIN_SCORE:

            return None


        # ====================================================
        # RESULT
        # ====================================================

        return {

            "symbol":
                symbol,

            "price":
                breakout["close"],

            "market_cap":
                ticker[
                    "market_cap"
                ],

            "quote_volume":
                ticker[
                    "quote_volume"
                ],

            "resistance":
                resistance,

            "breakout_percent":
                breakout_percent,

            "momentum":
                momentum,

            "volume_ratio":
                volume_ratio,

            "consolidation_range":
                consolidation_range,

            "ema20":
                ema20,

            "ema50":
                ema50,

            "ema200":
                ema200,

            "rsi":
                rsi,

            "atr":
                atr,

            "atr_percent":
                atr_percent,

            "close_position":
                close_position,

            "upper_wick_percent":
                upper_wick_percent,

            "spread":
                spread,

            "score":
                score
        }

    except Exception as e:

        print(
            f"{symbol} ANALYSIS ERROR:",
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
# BUILD TELEGRAM SIGNAL
# ============================================================

def build_signal(
    result
):

    return (

        "🟢 BINANCE SPOT 15M SIGNAL\n"
        "\n"

        f"🪙 {result['symbol']}\n"

        f"💰 Price: "
        f"{result['price']:.12g}\n"

        "\n"

        "💎 MARKET DATA\n"

        f"💎 Market Cap: "
        f"${result['market_cap']:,.0f}\n"

        f"💧 24H Volume: "
        f"${result['quote_volume']:,.0f}\n"

        f"↔️ Spread: "
        f"{result['spread']:.3f}%\n"

        "\n"

        "🚀 15M BREAKOUT\n"

        f"🔴 Resistance: "
        f"{result['resistance']:.12g}\n"

        f"🚀 Breakout: "
        f"+{result['breakout_percent']:.2f}%\n"

        f"⚡ Momentum: "
        f"+{result['momentum']:.2f}%\n"

        f"📊 Volume: "
        f"{result['volume_ratio']:.2f}x\n"

        "\n"

        "📦 CONSOLIDATION\n"

        f"5 Candle Range: "
        f"{result['consolidation_range']:.2f}%\n"

        "\n"

        "📈 TREND\n"

        f"EMA20: "
        f"{result['ema20']:.12g}\n"

        f"EMA50: "
        f"{result['ema50']:.12g}\n"

        f"EMA200: "
        f"{result['ema200']:.12g}\n"

        "EMA20 > EMA50 > EMA200 ✅\n"

        "\n"

        "📐 INDICATORS\n"

        f"RSI14: "
        f"{result['rsi']:.1f}\n"

        f"ATR14: "
        f"{result['atr_percent']:.2f}%\n"

        "\n"

        "🕯️ CANDLE QUALITY\n"

        f"Close Position: "
        f"{result['close_position']:.1f}%\n"

        f"Upper Wick: "
        f"{result['upper_wick_percent']:.1f}%\n"

        "\n"

        f"🏆 SCORE: "
        f"{result['score']}/100\n"

        "\n"

        "📚 HISTORY: 500 CLOSED 15M CANDLES\n"

        "🔴 Resistance: previous 100 candles\n"

        "📦 Consolidation: previous 5 candles\n"

        "🕯️ Breakout: latest CLOSED 15M candle\n"

        "\n"

        "🟢 Binance USDT Spot ONLY\n"

        "❌ 5M OFF\n"

        "❌ Solana OFF\n"

        "❌ Binance Square OFF\n"

        "\n"

        "⚠️ TELEGRAM ALERT ONLY\n"

        "❌ NO AUTOMATIC ORDER"
    )


# ============================================================
# SCAN LOOP
# ============================================================

def binance_scan_loop():

    print()
    print("=" * 70)
    print("🟢 BINANCE SPOT 15M ALERT BOT")
    print("=" * 70)
    print()

    print(
        "CMC MARKET CAP    : >= $200M"
    )

    print(
        "BINANCE            : USDT SPOT ONLY"
    )

    print(
        "24H VOLUME         : >= $10M"
    )

    print(
        "TIMEFRAME          : 15M ONLY"
    )

    print(
        "HISTORY            : 500 CLOSED"
    )

    print(
        "RESISTANCE         : 100 CANDLES"
    )

    print(
        "CONSOLIDATION      : 5 CANDLES"
    )

    print(
        "BREAKOUT           : >= +0.5%"
    )

    print(
        "MOMENTUM           : +1% TO +4%"
    )

    print(
        "VOLUME             : >= 1.8x"
    )

    print(
        "EMA                : 20 > 50 > 200"
    )

    print(
        "RSI                : 50 - 75"
    )

    print(
        "ATR14              : >= 0.20%"
    )

    print(
        "CLOSE POSITION     : >= 75%"
    )

    print(
        "UPPER WICK         : <= 25%"
    )

    print(
        "SPREAD             : <= 0.15%"
    )

    print(
        "SCORE              : >= 70"
    )

    print(
        "COOLDOWN           : 24 HOURS"
    )

    print(
        "TELEGRAM           : ON"
    )

    print(
        "AUTO ORDER         : OFF"
    )

    print("=" * 70)
    print()


    # ========================================================
    # LOOP
    # ========================================================

    while True:

        cycle_start = time.time()

        try:

            # =================================================
            # CMC
            # =================================================

            market_caps = load_cmc_market_caps()

            if not market_caps:

                print(
                    "SCAN STOPPED:"
                )

                print(
                    "CMC data unavailable."
                )

                time.sleep(
                    SCAN_INTERVAL
                )

                continue


            # =================================================
            # BINANCE 24H
            # =================================================

            tickers = get_24h_tickers()

            if not tickers:

                print(
                    "No Binance symbols passed "
                    "24H volume filter."
                )

                time.sleep(
                    SCAN_INTERVAL
                )

                continue


            print()
            print(
                "[24H FILTER]",
                len(tickers),
                "symbols >= $10M"
            )


            # =================================================
            # CMC FILTER
            # =================================================

            candidates = filter_by_market_cap(
                tickers,
                market_caps
            )

            print(
                "[CMC FILTER]",
                len(candidates),
                "symbols >= $200M"
            )


            if not candidates:

                print(
                    "No candidates."
                )

                time.sleep(
                    SCAN_INTERVAL
                )

                continue


            # =================================================
            # ANALYSIS
            # =================================================

            signals = 0

            with ThreadPoolExecutor(
                max_workers=MAX_WORKERS
            ) as executor:

                futures = {}

                for symbol, ticker in candidates.items():

                    futures[
                        executor.submit(
                            analyze_symbol,
                            symbol,
                            ticker
                        )
                    ] = symbol

                for future in as_completed(
                    futures
                ):

                    symbol = futures[
                        future
                    ]

                    try:

                        result = future.result()

                        if not result:

                            continue


                        # -------------------------------------
                        # Cooldown
                        # -------------------------------------

                        set_alert_time(
                            symbol
                        )


                        # -------------------------------------
                        # Message
                        # -------------------------------------

                        message = build_signal(
                            result
                        )


                        print()
                        print(
                            "=" * 50
                        )

                        print(
                            "[SIGNAL]",
                            symbol
                        )

                        print(
                            "Score:",
                            result["score"]
                        )

                        print(
                            "Market Cap:",
                            f"${result['market_cap']:,.0f}"
                        )

                        print(
                            "Breakout:",
                            f"{result['breakout_percent']:.2f}%"
                        )

                        print(
                            "Momentum:",
                            f"{result['momentum']:.2f}%"
                        )

                        print(
                            "Volume:",
                            f"{result['volume_ratio']:.2f}x"
                        )

                        print(
                            "RSI:",
                            f"{result['rsi']:.1f}"
                        )

                        print(
                            "ATR:",
                            f"{result['atr_percent']:.2f}%"
                        )

                        print(
                            "Spread:",
                            f"{result['spread']:.3f}%"
                        )

                        print(
                            "=" * 50
                        )


                        # -------------------------------------
                        # Telegram
                        # -------------------------------------

                        if send_telegram(
                            message
                        ):

                            print(
                                "[TELEGRAM] SENT",
                                symbol
                            )

                        else:

                            print(
                                "[TELEGRAM] FAILED",
                                symbol
                            )


                        signals += 1

                    except Exception as e:

                        print(
                            f"{symbol} FUTURE ERROR:",
                            repr(e)
                        )


            # =================================================
            # COMPLETE
            # =================================================

            elapsed = (
                time.time()
                - cycle_start
            )

            print()
            print(
                f"[SCAN COMPLETE] "
                f"{elapsed:.1f}s | "
                f"Candidates: "
                f"{len(candidates)} | "
                f"Signals: "
                f"{signals}"
            )

        except Exception as e:

            print(
                "[SCAN LOOP ERROR]",
                repr(e)
            )


        # =====================================================
        # NEXT SCAN
        # =====================================================

        elapsed = (
            time.time()
            - cycle_start
        )

        sleep_time = max(
            1,
            SCAN_INTERVAL - elapsed
        )

        print(
            f"Next scan in "
            f"{sleep_time:.1f}s"
        )

        time.sleep(
            sleep_time
        )


# ============================================================
# CMC TEST
# ============================================================

def test_cmc():

    print()
    print("=" * 70)
    print("CMC CONNECTION TEST")
    print("=" * 70)

    # --------------------------------------------------------
    # API KEY
    # --------------------------------------------------------

    if not CMC_API_KEY:

        print(
            "❌ CMC TEST FAILED"
        )

        print(
            "CMC_API_KEY is missing."
        )

        return False


    print(
        "CMC API KEY: FOUND"
    )


    # --------------------------------------------------------
    # KEY INFO
    #
    # If this endpoint is unavailable for the plan,
    # listings test below will still be performed.
    # --------------------------------------------------------

    key_info = cmc_get(
        "/v1/key/info"
    )

    if key_info:

        print(
            "✅ CMC KEY INFO TEST PASSED"
        )

    else:

        print(
            "⚠️ CMC key-info endpoint "
            "did not return data."
        )

        print(
            "Continuing with listings test..."
        )


    # --------------------------------------------------------
    # MARKET DATA TEST
    # --------------------------------------------------------

    data = load_cmc_market_caps(
        force=True
    )

    if not data:

        print()
        print(
            "❌ CMC TEST FAILED"
        )

        print(
            "CMC market-cap data unavailable."
        )

        print(
            "Check Railway variable:"
        )

        print(
            "CMC_API_KEY"
        )

        return False


    # --------------------------------------------------------
    # SUCCESS
    # --------------------------------------------------------

    print()
    print(
        "✅ CMC TEST PASSED"
    )

    print(
        "Coins loaded:",
        len(data)
    )

    qualified = sum(
        1
        for value in data.values()
        if value >= MIN_MARKET_CAP
    )

    print(
        "Coins >= $200M:",
        qualified
    )

    print("=" * 70)
    print()

    return True


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 70)
    print("🟢 BINANCE SPOT 15M ALERT BOT STARTING")
    print("=" * 70)
    print()

    print(
        "15M ONLY"
    )

    print(
        "NO 5M"
    )

    print(
        "NO SOLANA"
    )

    print(
        "NO BINANCE SQUARE"
    )

    print(
        "NO AUTOMATIC ORDERS"
    )

    print()


    # ========================================================
    # ENV CHECK
    # ========================================================

    if not TELEGRAM_BOT_TOKEN:

        print(
            "ERROR: TELEGRAM_BOT_TOKEN missing"
        )

        return


    if not TELEGRAM_CHAT_ID:

        print(
            "ERROR: TELEGRAM_CHAT_ID missing"
        )

        return


    # ========================================================
    # BINANCE
    # ========================================================

    if not load_spot_symbols():

        print(
            "ERROR: Binance Spot symbols "
            "could not be loaded."
        )

        return


    # ========================================================
    # CMC TEST
    # ========================================================

    if not test_cmc():

        print()
        print(
            "ERROR: CMC market-cap "
            "data unavailable."
        )

        print(
            "Bot will NOT start."
        )

        return


    # ========================================================
    # TELEGRAM STARTUP
    # ========================================================

    send_telegram(

        "🟢 BINANCE SPOT 15M ALERT BOT STARTED\n\n"

        "💎 Market Cap >= $200M\n"

        "💧 24H Volume >= $10M\n"

        "⏱️ 15M ONLY\n"

        "📚 500 Closed 15M Candles\n"

        "🔴 Resistance = Previous 100 Candles\n"

        "📦 Consolidation = Previous 5 Candles\n"

        "🚀 Breakout >= +0.5%\n"

        "⚡ Momentum +1% to +4%\n"

        "📊 Volume >= 1.8x\n"

        "📈 EMA20 > EMA50 > EMA200\n"

        "📐 RSI 50-75\n"

        "📐 ATR14 Active\n"

        "🕯️ Close Position >= 75%\n"

        "🕯️ Upper Wick <= 25%\n"

        "↔️ Spread <= 0.15%\n"

        "🏆 Score >= 70\n"

        "⏳ Cooldown 24H\n\n"

        "🟢 Binance USDT Spot ONLY\n"

        "❌ 5M OFF\n"

        "❌ Solana OFF\n"

        "❌ Binance Square OFF\n\n"

        "⚠️ TELEGRAM ALERT ONLY\n"

        "❌ NO AUTOMATIC ORDER"
    )


    # ========================================================
    # START SCANNER
    # ========================================================

    binance_scan_loop()


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    main()
