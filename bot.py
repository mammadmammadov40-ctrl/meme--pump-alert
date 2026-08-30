import os
import time
import threading
import requests

from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# BINANCE SPOT 15M REAL BREAKOUT BOT
#
# FINAL STRATEGY
#
# CMC MARKET CAP >= $200M
# BINANCE SPOT USDT ONLY
# 24H VOLUME >= $10M
#
# 15M ONLY
# 500 CLOSED 15M CANDLES
#
# RESISTANCE = PREVIOUS 100 CLOSED 15M CANDLES
# CONSOLIDATION = PREVIOUS 5 CLOSED 15M CANDLES
#
# BREAKOUT >= +0.5%
# MOMENTUM +1% TO +4%
# VOLUME >= 1.8x
#
# EMA20 > EMA50 > EMA200
# RSI 50-75
# ATR14 ACTIVITY FILTER
#
# CLOSE POSITION >= 75%
# UPPER WICK <= 25%
# SPREAD <= 0.15%
#
# SCORE >= 70
# COOLDOWN = 24 HOURS
#
# TELEGRAM ALERT ONLY
# NO AUTOMATIC ORDER
# ============================================================


# ============================================================
# ENVIRONMENT
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

HISTORY_15M = 500


# ============================================================
# CMC MARKET CAP
# ============================================================

MIN_MARKET_CAP = 200_000_000

CMC_LIMIT = 1000

CMC_REFRESH_SECONDS = 300


# ============================================================
# BINANCE 24H LIQUIDITY
# ============================================================

MIN_24H_QUOTE_VOLUME = 10_000_000


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

MAX_CONSOLIDATION_ATR_MULTIPLE = 1.50


# ============================================================
# MOMENTUM
# ============================================================

MIN_MOMENTUM_PERCENT = 1.00

MAX_MOMENTUM_PERCENT = 4.00


# ============================================================
# VOLUME
# ============================================================

VOLUME_AVERAGE_CANDLES = 20

MIN_VOLUME_RATIO = 1.80


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

MIN_CANDLE_ATR_RATIO = 0.80


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

MIN_SIGNAL_SCORE = 70

STRONG_SIGNAL_SCORE = 80

VERY_STRONG_SIGNAL_SCORE = 90


# ============================================================
# SCAN
# ============================================================

SCAN_INTERVAL = 60

MAX_WORKERS = 8

REQUEST_TIMEOUT = 10


# ============================================================
# COOLDOWN
# ============================================================

SIGNAL_COOLDOWN_SECONDS = 24 * 60 * 60


# ============================================================
# STABLECOINS
# ============================================================

STABLECOINS = {
    "USDT",
    "USDC",
    "BUSD",
    "FDUSD",
    "TUSD",
    "DAI",
    "USDE",
    "USDD",
    "USD1",
    "PYUSD",
    "USDP",
    "GUSD",
    "FRAX",
    "LUSD"
}


# ============================================================
# SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "BinanceSpot15MBreakoutBot/4.0"
})


# ============================================================
# GLOBALS
# ============================================================

spot_symbols = {}

market_caps = {}

last_alert = {}

lock = threading.Lock()

last_cmc_update = 0


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
# BINANCE REQUEST
# ============================================================

def binance_get(path, params=None):

    try:

        response = session.get(
            BINANCE_REST + path,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:

            print(
                "Binance HTTP error:",
                response.status_code,
                path,
                response.text[:300]
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
# CMC REQUEST
# ============================================================

def cmc_get(path, params=None):

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

        response = session.get(
            CMC_REST + path,
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:

            print(
                "CMC HTTP error:",
                response.status_code,
                response.text[:500]
            )

            return None

        return response.json()

    except Exception as e:

        print(
            "CMC request error:",
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

    for item in data.get("symbols", []):

        try:

            symbol = item["symbol"]

            if item.get("status") != "TRADING":
                continue

            if item.get("quoteAsset") != "USDT":
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

            if base in STABLECOINS:
                continue

            if base.endswith(
                ("UP", "DOWN", "BULL", "BEAR")
            ):
                continue

            result[symbol] = item

        except Exception:
            continue

    spot_symbols = result

    print(
        "BINANCE SPOT USDT SYMBOLS:",
        len(spot_symbols)
    )

    return True


# ============================================================
# LOAD CMC MARKET CAPS
# ============================================================

def load_cmc_market_caps():

    global market_caps
    global last_cmc_update

    data = cmc_get(
        "/v1/cryptocurrency/listings/latest",
        {
            "start": 1,
            "limit": CMC_LIMIT,
            "convert": "USD",
            "sort": "market_cap",
            "sort_dir": "desc"
        }
    )

    if not data:

        print(
            "CMC market-cap update failed"
        )

        return False

    new_caps = {}

    for coin in data.get("data", []):

        try:

            symbol = str(
                coin.get(
                    "symbol",
                    ""
                )
            ).upper()

            market_cap = float(
                coin.get(
                    "quote",
                    {}
                ).get(
                    "USD",
                    {}
                ).get(
                    "market_cap",
                    0
                )
            )

            if not symbol:
                continue

            if market_cap < MIN_MARKET_CAP:
                continue

            if (
                symbol not in new_caps
                or market_cap > new_caps[symbol]
            ):

                new_caps[symbol] = market_cap

        except Exception:
            continue

    if not new_caps:

        print(
            "CMC returned no >= $200M coins"
        )

        return False

    market_caps = new_caps

    last_cmc_update = time.time()

    print(
        "CMC MARKET CAP >= $200M:",
        len(market_caps)
    )

    return True


# ============================================================
# CMC REFRESH
# ============================================================

def refresh_cmc_if_needed():

    if (
        time.time()
        - last_cmc_update
        >= CMC_REFRESH_SECONDS
    ):

        return load_cmc_market_caps()

    return bool(market_caps)


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

        try:

            symbol = item.get(
                "symbol"
            )

            if symbol not in spot_symbols:
                continue

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

            if last_price <= 0:
                continue

            if (
                quote_volume
                < MIN_24H_QUOTE_VOLUME
            ):
                continue

            result[symbol] = {
                "quote_volume":
                    quote_volume,

                "price":
                    last_price
            }

        except Exception:
            continue

    return result


# ============================================================
# BOOK TICKERS
# ============================================================

def get_book_tickers():

    data = binance_get(
        "/api/v3/ticker/bookTicker"
    )

    if not data:
        return {}

    result = {}

    for item in data:

        try:

            symbol = item.get(
                "symbol"
            )

            if symbol not in spot_symbols:
                continue

            bid = float(
                item.get(
                    "bidPrice",
                    0
                )
            )

            ask = float(
                item.get(
                    "askPrice",
                    0
                )
            )

            if bid <= 0 or ask <= 0:
                continue

            mid = (
                bid + ask
            ) / 2

            spread = (
                (ask - bid)
                / mid
                * 100
            )

            result[symbol] = {
                "bid": bid,
                "ask": ask,
                "spread": spread
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

        params["endTime"] = end_time

    return binance_get(
        "/api/v3/klines",
        params
    )


# ============================================================
# CLOSED CANDLES
# ============================================================

def only_closed(klines):

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
# LOAD CLOSED HISTORY
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
            continue

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

        previous_count = len(
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
                continue

        if len(collected) == previous_count:
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

    return closed[-required:]


# ============================================================
# CANDLE VALUES
# ============================================================

def candle_values(candle):

    return {
        "open": float(candle[1]),
        "high": float(candle[2]),
        "low": float(candle[3]),
        "close": float(candle[4]),
        "volume": float(candle[5]),
        "quote_volume": float(candle[7])
    }


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
        2.0
        / (period + 1)
    )

    ema = (
        sum(
            values[:period]
        )
        / period
    )

    for value in values[period:]:

        ema = (
            (value - ema)
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

    if len(closes) < period + 1:
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

        gains.append(
            max(change, 0)
        )

        losses.append(
            max(-change, 0)
        )

    avg_gain = (
        sum(gains)
        / period
    )

    avg_loss = (
        sum(losses)
        / period
    )

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
# ATR14
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

        current = candle_values(
            history[i]
        )

        previous = candle_values(
            history[i - 1]
        )

        tr1 = (
            current["high"]
            - current["low"]
        )

        tr2 = abs(
            current["high"]
            - previous["close"]
        )

        tr3 = abs(
            current["low"]
            - previous["close"]
        )

        true_ranges.append(
            max(
                tr1,
                tr2,
                tr3
            )
        )

    if len(true_ranges) < period:
        return None

    atr = (
        sum(
            true_ranges[:period]
        )
        / period
    )

    for tr in true_ranges[period:]:

        atr = (
            (
                atr
                * (period - 1)
            )
            + tr
        ) / period

    return atr


# ============================================================
# 5 CANDLE CONSOLIDATION
# ============================================================

def analyze_consolidation(
    history,
    resistance,
    atr
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

    if len(candles) != (
        CONSOLIDATION_CANDLES
    ):
        return None

    try:

        highs = []
        lows = []
        closes = []

        for candle in candles:

            values = candle_values(
                candle
            )

            highs.append(
                values["high"]
            )

            lows.append(
                values["low"]
            )

            closes.append(
                values["close"]
            )

        highest = max(highs)
        lowest = min(lows)

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

        if atr is None:
            return None

        average_price = (
            sum(closes)
            / len(closes)
        )

        if average_price <= 0:
            return None

        atr_percent = (
            atr
            / average_price
            * 100
        )

        if atr_percent <= 0:
            return None

        if (
            range_percent
            > (
                atr_percent
                * MAX_CONSOLIDATION_ATR_MULTIPLE
            )
        ):
            return None

        # All five candles must stay
        # below resistance.
        for close in closes:

            if close >= resistance:
                return None

        return {
            "range_percent":
                range_percent,

            "atr_percent":
                atr_percent
        }

    except Exception:
        return None


# ============================================================
# STRATEGY
# ============================================================

def analyze_15m(
    symbol,
    history
):

    minimum_required = max(
        EMA_SLOW + 10,
        BREAKOUT_LOOKBACK
        + CONSOLIDATION_CANDLES
        + 5,
        VOLUME_AVERAGE_CANDLES + 10,
        RSI_PERIOD + 10,
        ATR_PERIOD + 10
    )

    if len(history) < minimum_required:
        return None

    try:

        # ----------------------------------------------------
        # Latest CLOSED 15M candle
        # ----------------------------------------------------

        breakout = candle_values(
            history[-1]
        )

        open_price = breakout["open"]
        high = breakout["high"]
        low = breakout["low"]
        close = breakout["close"]
        volume = breakout["volume"]

        if open_price <= 0:
            return None

        # ----------------------------------------------------
        # MOMENTUM
        # ----------------------------------------------------

        momentum = (
            (
                close - open_price
            )
            / open_price
            * 100
        )

        if momentum < MIN_MOMENTUM_PERCENT:
            return None

        if momentum > MAX_MOMENTUM_PERCENT:
            return None

        # ----------------------------------------------------
        # RESISTANCE
        #
        # Previous 100 CLOSED 15M candles
        # ----------------------------------------------------

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
            float(candle[2])
            for candle
            in resistance_history
        )

        if resistance <= 0:
            return None

        # ----------------------------------------------------
        # BREAKOUT
        # ----------------------------------------------------

        breakout_percent = (
            (
                close - resistance
            )
            / resistance
            * 100
        )

        if (
            breakout_percent
            < MIN_BREAKOUT_PERCENT
        ):
            return None

        # ----------------------------------------------------
        # VOLUME
        # ----------------------------------------------------

        volume_reference = (
            resistance_history[
                -VOLUME_AVERAGE_CANDLES:
            ]
        )

        volumes = [
            float(candle[5])
            for candle
            in volume_reference
        ]

        if len(volumes) != (
            VOLUME_AVERAGE_CANDLES
        ):
            return None

        average_volume = (
            sum(volumes)
            / len(volumes)
        )

        if average_volume <= 0:
            return None

        volume_ratio = (
            volume
            / average_volume
        )

        if volume_ratio < MIN_VOLUME_RATIO:
            return None

        # ----------------------------------------------------
        # EMA
        # ----------------------------------------------------

        closes = [
            float(candle[4])
            for candle in history
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
            ema20 > ema50 > ema200
        ):
            return None

        if close <= ema20:
            return None

        # ----------------------------------------------------
        # RSI
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # ATR14
        # ----------------------------------------------------

        atr = calculate_atr(
            history[:-1],
            ATR_PERIOD
        )

        if atr is None:
            return None

        # ----------------------------------------------------
        # Candle range
        # ----------------------------------------------------

        candle_range = (
            high - low
        )

        if candle_range <= 0:
            return None

        if (
            candle_range
            < atr * MIN_CANDLE_ATR_RATIO
        ):
            return None

        # ----------------------------------------------------
        # CLOSE POSITION
        # ----------------------------------------------------

        close_position = (
            (
                close - low
            )
            / candle_range
            * 100
        )

        if (
            close_position
            < MIN_CLOSE_POSITION
        ):
            return None

        # ----------------------------------------------------
        # UPPER WICK
        # ----------------------------------------------------

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

        if (
            upper_wick_percent
            > MAX_UPPER_WICK_PERCENT
        ):
            return None

        # ----------------------------------------------------
        # CONSOLIDATION
        # ----------------------------------------------------

        consolidation = (
            analyze_consolidation(
                history,
                resistance,
                atr
            )
        )

        if not consolidation:
            return None

        # ====================================================
        # SCORE
        # ====================================================

        score = 0

        # Breakout: 25
        if breakout_percent >= 1.0:
            score += 25

        elif breakout_percent >= 0.75:
            score += 23

        else:
            score += 20

        # Volume: 20
        if volume_ratio >= 2.5:
            score += 20

        elif volume_ratio >= 2.0:
            score += 18

        else:
            score += 15

        # Momentum: 15
        if momentum >= 2.0:
            score += 15

        else:
            score += 12

        # EMA: 15
        score += 15

        # Candle: 10
        if (
            close_position >= 85
            and upper_wick_percent <= 15
        ):
            score += 10

        else:
            score += 8

        # RSI: 5
        if 55 <= rsi <= 70:
            score += 5

        else:
            score += 4

        # ATR: 5
        if candle_range >= atr * 1.20:
            score += 5

        else:
            score += 4

        if score < MIN_SIGNAL_SCORE:
            return None

        return {
            "price": close,
            "momentum": momentum,
            "resistance": resistance,
            "breakout": breakout_percent,
            "volume_ratio": volume_ratio,
            "ema20": ema20,
            "ema50": ema50,
            "ema200": ema200,
            "rsi": rsi,
            "atr": atr,
            "close_position": close_position,
            "upper_wick": upper_wick_percent,
            "consolidation_range":
                consolidation[
                    "range_percent"
                ],
            "score": score
        }

    except Exception as e:

        print(
            f"{symbol} strategy error:",
            repr(e)
        )

        return None


# ============================================================
# COOLDOWN
# ============================================================

def is_on_cooldown(symbol):

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


def set_alert_time(symbol):

    with lock:

        last_alert[
            symbol
        ] = time.time()


# ============================================================
# SIGNAL LABEL
# ============================================================

def signal_label(score):

    if score >= VERY_STRONG_SIGNAL_SCORE:
        return "🚀 VERY STRONG BUY"

    if score >= STRONG_SIGNAL_SCORE:
        return "🔥 STRONG BUY"

    return "🟢 BUY ALERT"


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

def build_signal(
    symbol,
    ticker,
    market_cap,
    spread,
    analysis
):

    score = analysis["score"]

    return (
        f"{signal_label(score)}\n"
        "\n"
        f"🪙 {symbol}\n"
        f"💰 Price: "
        f"{analysis['price']:.12g}\n"
        f"💎 Market Cap: "
        f"${market_cap:,.0f}\n"
        "\n"
        "🚀 15M REAL BREAKOUT\n"
        f"🔴 Resistance: "
        f"{analysis['resistance']:.12g}\n"
        f"🚀 Breakout: "
        f"+{analysis['breakout']:.2f}%\n"
        f"⚡ Momentum: "
        f"+{analysis['momentum']:.2f}%\n"
        "\n"
        "📊 VOLUME\n"
        f"💥 Volume: "
        f"{analysis['volume_ratio']:.2f}x\n"
        "\n"
        "📈 EMA TREND\n"
        f"EMA20: "
        f"{analysis['ema20']:.8g}\n"
        f"EMA50: "
        f"{analysis['ema50']:.8g}\n"
        f"EMA200: "
        f"{analysis['ema200']:.8g}\n"
        "\n"
        "📐 INDICATORS\n"
        f"RSI14: "
        f"{analysis['rsi']:.1f}\n"
        f"ATR14: "
        f"{analysis['atr']:.8g}\n"
        "\n"
        "🕯️ CANDLE QUALITY\n"
        f"Close Position: "
        f"{analysis['close_position']:.1f}%\n"
        f"Upper Wick: "
        f"{analysis['upper_wick']:.1f}%\n"
        "\n"
        "🔒 CONSOLIDATION\n"
        f"5 Candle Range: "
        f"{analysis['consolidation_range']:.2f}%\n"
        "\n"
        "💧 LIQUIDITY\n"
        f"24H Volume: "
        f"${ticker['quote_volume']:,.0f}\n"
        f"↔️ Spread: "
        f"{spread:.3f}%\n"
        "\n"
        f"🏆 SCORE: {score}/100\n"
        "\n"
        "📚 FINAL CONDITIONS\n"
        "• Market Cap >= $200M\n"
        "• Binance Spot USDT\n"
        "• 24H Volume >= $10M\n"
        "• 15M CLOSED candle\n"
        "• 500 CLOSED 15M history\n"
        "• Previous 100 candle resistance\n"
        "• Previous 5 candle consolidation\n"
        "• Breakout >= +0.5%\n"
        "• Momentum +1% to +4%\n"
        "• Volume >= 1.8x\n"
        "• EMA20 > EMA50 > EMA200\n"
        "• RSI 50-75\n"
        "• ATR14 activity filter\n"
        "• Close Position >= 75%\n"
        "• Upper Wick <= 25%\n"
        "• Spread <= 0.15%\n"
        "• Score >= 70\n"
        "• Cooldown 24 hours\n"
        "\n"
        "🟢 BINANCE SPOT ONLY\n"
        "❌ SOLANA\n"
        "❌ BINANCE SQUARE\n"
        "\n"
        "⚠️ ALERT ONLY\n"
        "❌ NO AUTOMATIC ORDER"
    )


# ============================================================
# ANALYZE SYMBOL
# ============================================================

def analyze_symbol(
    symbol,
    ticker,
    book
):

    try:

        # ----------------------------------------------------
        # MARKET CAP
        # ----------------------------------------------------

        base_asset = (
            spot_symbols[
                symbol
            ].get(
                "baseAsset",
                ""
            ).upper()
        )

        market_cap = market_caps.get(
            base_asset
        )

        if market_cap is None:
            return None

        if market_cap < MIN_MARKET_CAP:
            return None

        # ----------------------------------------------------
        # COOLDOWN
        # ----------------------------------------------------

        if is_on_cooldown(symbol):
            return None

        # ----------------------------------------------------
        # SPREAD
        # ----------------------------------------------------

        if not book:
            return None

        spread = book.get(
            "spread"
        )

        if spread is None:
            return None

        if spread > MAX_SPREAD_PERCENT:
            return None

        # ----------------------------------------------------
        # 500 CLOSED 15M CANDLES
        # ----------------------------------------------------

        history = load_closed_history(
            symbol,
            INTERVAL,
            HISTORY_15M
        )

        if len(history) != HISTORY_15M:

            print(
                f"15M history incomplete "
                f"{symbol}: "
                f"{len(history)}/"
                f"{HISTORY_15M}"
            )

            return None

        # ----------------------------------------------------
        # STRATEGY
        # ----------------------------------------------------

        analysis = analyze_15m(
            symbol,
            history
        )

        if not analysis:
            return None

        # ----------------------------------------------------
        # SIGNAL
        # ----------------------------------------------------

        return build_signal(
            symbol,
            ticker,
            market_cap,
            spread,
            analysis
        )

    except Exception as e:

        print(
            f"{symbol} ERROR:",
            repr(e)
        )

        return None


# ============================================================
# MAIN SCAN LOOP
# ============================================================

def binance_scan_loop():

    print()
    print("=" * 70)
    print("🚀 BINANCE SPOT 15M BREAKOUT BOT")
    print("=" * 70)
    print()
    print("CMC MARKET CAP     : >= $200M")
    print("BINANCE            : SPOT USDT")
    print("24H VOLUME         : >= $10M")
    print("TIMEFRAME          : 15M")
    print("HISTORY            : 500 CLOSED")
    print("RESISTANCE         : 100 CLOSED CANDLES")
    print("CONSOLIDATION      : 5 CLOSED CANDLES")
    print("BREAKOUT           : >= +0.50%")
    print("MOMENTUM           : +1% -> +4%")
    print("VOLUME             : >= 1.8x")
    print("EMA                : 20 > 50 > 200")
    print("RSI                : 50 -> 75")
    print("ATR                : 14")
    print("CLOSE POSITION     : >= 75%")
    print("UPPER WICK         : <= 25%")
    print("SPREAD             : <= 0.15%")
    print("MIN SCORE          : 70")
    print("COOLDOWN           : 24 HOURS")
    print()
    print("SOLANA             : OFF")
    print("BINANCE SQUARE     : OFF")
    print("AUTO ORDER         : OFF")
    print("=" * 70)
    print()

    while True:

        cycle_start = time.time()

        try:

            # ------------------------------------------------
            # CMC
            # ------------------------------------------------

            if not refresh_cmc_if_needed():

                print(
                    "[CMC] Data unavailable"
                )

                time.sleep(
                    SCAN_INTERVAL
                )

                continue

            # ------------------------------------------------
            # BINANCE 24H
            # ------------------------------------------------

            tickers = get_24h_tickers()

            if not tickers:

                print(
                    "[SCAN] No tickers"
                )

                time.sleep(
                    SCAN_INTERVAL
                )

                continue

            # ------------------------------------------------
            # BOOK
            # ------------------------------------------------

            books = get_book_tickers()

            # ------------------------------------------------
            # CANDIDATES
            # ------------------------------------------------

            candidates = {}

            for symbol, ticker in tickers.items():

                try:

                    base_asset = (
                        spot_symbols[
                            symbol
                        ].get(
                            "baseAsset",
                            ""
                        ).upper()
                    )

                    market_cap = (
                        market_caps.get(
                            base_asset
                        )
                    )

                    if market_cap is None:
                        continue

                    if (
                        market_cap
                        < MIN_MARKET_CAP
                    ):
                        continue

                    candidates[
                        symbol
                    ] = ticker

                except Exception:
                    continue

            print()
            print(
                "[SCAN] Binance volume candidates:",
                len(tickers)
            )

            print(
                "[SCAN] $200M+ market-cap candidates:",
                len(candidates)
            )

            if not candidates:

                time.sleep(
                    SCAN_INTERVAL
                )

                continue

            signals = 0

            # ------------------------------------------------
            # PARALLEL
            # ------------------------------------------------

            with ThreadPoolExecutor(
                max_workers=MAX_WORKERS
            ) as executor:

                futures = {}

                for symbol in candidates:

                    future = executor.submit(
                        analyze_symbol,
                        symbol,
                        candidates[symbol],
                        books.get(symbol)
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

                        message = (
                            future.result()
                        )

                        if not message:
                            continue

                        set_alert_time(
                            symbol
                        )

                        signals += 1

                        print(
                            "[SIGNAL]",
                            symbol
                        )

                        send_telegram(
                            message
                        )

                    except Exception as e:

                        print(
                            f"{symbol} FUTURE ERROR:",
                            repr(e)
                        )

            elapsed = (
                time.time()
                - cycle_start
            )

            print()
            print(
                "[SCAN COMPLETE]",
                f"{elapsed:.1f}s",
                "| Signals:",
                signals
            )

        except Exception as e:

            print(
                "[SCAN LOOP ERROR]",
                repr(e)
            )

        # ----------------------------------------------------
        # NEXT SCAN
        # ----------------------------------------------------

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
# MAIN
# ============================================================

def main():

    print()
    print("=" * 70)
    print("🟢 BINANCE SPOT 15M ALERT BOT")
    print("=" * 70)
    print()
    print("FINAL STRATEGY")
    print()
    print("Market Cap >= $200M")
    print("Binance Spot USDT")
    print("24H Volume >= $10M")
    print("15M ONLY")
    print("500 CLOSED candles")
    print("100 candle resistance")
    print("5 candle consolidation")
    print("Breakout >= 0.5%")
    print("Momentum +1% to +4%")
    print("Volume >= 1.8x")
    print("EMA20 > EMA50 > EMA200")
    print("RSI 50-75")
    print("ATR14")
    print("Close Position >= 75%")
    print("Upper Wick <= 25%")
    print("Spread <= 0.15%")
    print("Score >= 70")
    print("Cooldown 24 hours")
    print()
    print("ALERT ONLY")
    print("NO AUTOMATIC ORDER")
    print()

    # --------------------------------------------------------
    # ENV CHECK
    # --------------------------------------------------------

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

    if not CMC_API_KEY:

        print(
            "ERROR: CMC_API_KEY missing"
        )

        return

    # --------------------------------------------------------
    # BINANCE
    # --------------------------------------------------------

    if not load_spot_symbols():

        print(
            "ERROR: Binance Spot symbols failed"
        )

        return

    # --------------------------------------------------------
    # CMC
    # --------------------------------------------------------

    if not load_cmc_market_caps():

        print(
            "ERROR: CMC market-cap loading failed"
        )

        return

    # --------------------------------------------------------
    # STARTUP TELEGRAM
    # --------------------------------------------------------

    send_telegram(
        "🟢 BINANCE SPOT 15M BOT STARTED\n"
        "\n"
        "💎 Market Cap >= $200M\n"
        "🏦 Binance Spot USDT\n"
        "💧 24H Volume >= $10M\n"
        "⏱ 15M ONLY\n"
        "📚 500 CLOSED candles\n"
        "🔴 Resistance = previous 100 candles\n"
        "🔒 Consolidation = previous 5 candles\n"
        "🚀 Breakout >= +0.5%\n"
        "⚡ Momentum +1% to +4%\n"
        "📊 Volume >= 1.8x\n"
        "📈 EMA20 > EMA50 > EMA200\n"
        "📐 RSI 50-75\n"
        "📏 ATR14 activity filter\n"
        "🕯️ Close Position >= 75%\n"
        "🕯️ Upper Wick <= 25%\n"
        "↔️ Spread <= 0.15%\n"
        "🏆 Score >= 70\n"
        "⏳ Cooldown 24 hours\n"
        "\n"
        "❌ 5M NOT USED\n"
        "❌ Solana OFF\n"
        "❌ Binance Square OFF\n"
        "\n"
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
