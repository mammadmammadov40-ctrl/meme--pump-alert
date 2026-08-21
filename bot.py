import os
import time
import json
import threading
from collections import defaultdict, deque

import requests
import websocket


# ============================================================
# 🚀 5M MOMENTUM SCORE BOT
# ============================================================
#
# STRATEGY:
#
# CMC RANK 1-2000
# BINANCE SPOT USDT
#
# LIVE ANALYSIS:
#   5M candle
#   1M candle
#   Order book
#
# SCORE = 100
#
# 1. PRICE MOMENTUM          20
# 2. VOLUME ACCELERATION     25
# 3. BUY PRESSURE            20
# 4. CANDLE STRENGTH         15
# 5. VOLATILITY EXPANSION    10
# 6. LIQUIDITY / SPREAD       10
#
# >= 75  = 🚨 SIGNAL
# >= 85  = 🔥 STRONG SIGNAL
#
# 70-74 = WAIT
#
# RE-SIGNAL:
#   Same wave -> no repeat
#   After signal:
#       price must fall >= 2%
#       then score >= 75 again
#
# CHECK:
#   every 1 second
#
# ============================================================


# ============================================================
# ENV
# ============================================================

CMC_API_KEY = os.getenv("CMC_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


BINANCE_REST = "https://api.binance.com"
BINANCE_WS = "wss://stream.binance.com:9443/ws"


# ============================================================
# CMC
# ============================================================

CMC_MIN_RANK = 1
CMC_MAX_RANK = 2000


# ============================================================
# TIMEFRAMES
# ============================================================

MAIN_INTERVAL = "5m"
MICRO_INTERVAL = "1m"


# ============================================================
# SIGNAL SCORE
# ============================================================

SIGNAL_SCORE = 75
STRONG_SIGNAL_SCORE = 85


# ============================================================
# 1. PRICE MOMENTUM
# ============================================================

MOMENTUM_1M_MIN = 0.7
MOMENTUM_3M_MIN = 1.5


# ============================================================
# 2. VOLUME ACCELERATION
# ============================================================

VOLUME_AVG_CANDLES = 5

VOLUME_ACCEL_1 = 1.5
VOLUME_ACCEL_2 = 2.0

LAST_MINUTE_VOLUME_FACTOR = 1.20


# ============================================================
# 3. BUY PRESSURE
# ============================================================

BUY_PRESSURE_1 = 55.0
BUY_PRESSURE_2 = 60.0
BUY_PRESSURE_3 = 65.0


# ============================================================
# 4. CANDLE STRENGTH
# ============================================================

BODY_1 = 50.0
BODY_2 = 65.0

MAX_UPPER_WICK = 45.0


# ============================================================
# 5. VOLATILITY
# ============================================================

VOLATILITY_FACTOR = 1.30


# ============================================================
# 6. ORDER BOOK
# ============================================================

MAX_SPREAD = 0.10

ORDERBOOK_DEPTH_PERCENT = 0.50

MIN_BID_DEPTH_USD = 25_000.0


# ============================================================
# RE-SIGNAL
# ============================================================

RETRACE_PERCENT = 2.0


# ============================================================
# WEBSOCKET
# ============================================================

WS_CHUNK_SIZE = 100

RECONNECT_SECONDS = 3

CHECK_INTERVAL = 1

ORDERBOOK_STALE_SECONDS = 3


# ============================================================
# HISTORY
# ============================================================

MAX_5M_HISTORY = 20
MAX_1M_HISTORY = 10


# ============================================================
# GLOBALS
# ============================================================

coins = {}

cmc_ranks = {}

five_minute_data = defaultdict(
    lambda: deque(
        maxlen=MAX_5M_HISTORY
    )
)

one_minute_data = defaultdict(
    lambda: deque(
        maxlen=MAX_1M_HISTORY
    )
)

order_books = {}

last_price = {}

signal_state = {}


data_lock = threading.RLock()
orderbook_lock = threading.RLock()
signal_lock = threading.Lock()


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(text):

    if not TELEGRAM_BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN MISSING", flush=True)
        return False

    if not TELEGRAM_CHAT_ID:
        print("TELEGRAM_CHAT_ID MISSING", flush=True)
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    try:

        response = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=10,
        )

        if response.status_code == 200:

            print(
                "TELEGRAM SENT",
                flush=True
            )

            return True

        print(
            "TELEGRAM ERROR:",
            response.status_code,
            response.text[:500],
            flush=True
        )

        return False

    except Exception as e:

        print(
            "TELEGRAM ERROR:",
            repr(e),
            flush=True
        )

        return False


# ============================================================
# CMC
# ============================================================

def load_cmc():

    if not CMC_API_KEY:

        print(
            "CMC_API_KEY MISSING",
            flush=True
        )

        return False

    print(
        "CMC: LOADING TOP 2000...",
        flush=True
    )

    url = (
        "https://pro-api.coinmarketcap.com"
        "/v1/cryptocurrency/listings/latest"
    )

    headers = {
        "X-CMC_PRO_API_KEY": CMC_API_KEY,
        "Accept": "application/json",
    }

    params = {
        "start": 1,
        "limit": 2000,
        "convert": "USD",
    }

    try:

        response = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=30,
        )

        if response.status_code != 200:

            print(
                "CMC ERROR:",
                response.text[:1000],
                flush=True
            )

            return False

        result = response.json()

        new_ranks = {}

        for coin in result.get(
            "data",
            []
        ):

            symbol = str(
                coin.get(
                    "symbol",
                    ""
                )
            ).upper()

            rank = coin.get(
                "cmc_rank"
            )

            if not symbol or rank is None:
                continue

            try:
                rank = int(rank)
            except Exception:
                continue

            if (
                CMC_MIN_RANK
                <= rank
                <= CMC_MAX_RANK
            ):

                if symbol not in new_ranks:

                    new_ranks[
                        symbol
                    ] = rank

        if not new_ranks:

            return False

        with data_lock:

            cmc_ranks.clear()

            cmc_ranks.update(
                new_ranks
            )

        print(
            f"CMC READY: "
            f"{len(new_ranks)}",
            flush=True
        )

        return True

    except Exception as e:

        print(
            "CMC EXCEPTION:",
            repr(e),
            flush=True
        )

        return False


# ============================================================
# BINANCE SYMBOLS
# ============================================================

def load_binance_symbols():

    print(
        "BINANCE: LOADING SPOT...",
        flush=True
    )

    try:

        response = requests.get(
            f"{BINANCE_REST}/api/v3/exchangeInfo",
            timeout=30
        )

        if response.status_code != 200:
            return []

        data = response.json()

        with data_lock:

            ranks = dict(
                cmc_ranks
            )

        result = {}

        for item in data.get(
            "symbols",
            []
        ):

            if item.get(
                "status"
            ) != "TRADING":
                continue

            if item.get(
                "quoteAsset"
            ) != "USDT":
                continue

            if item.get(
                "isSpotTradingAllowed"
            ) is False:
                continue

            symbol = str(
                item.get(
                    "symbol",
                    ""
                )
            ).upper()

            base = str(
                item.get(
                    "baseAsset",
                    ""
                )
            ).upper()

            if not symbol or not base:
                continue

            rank = ranks.get(
                base
            )

            if rank is None:
                continue

            if base.endswith(
                (
                    "UP",
                    "DOWN",
                    "BULL",
                    "BEAR",
                )
            ):
                continue

            result[symbol] = {
                "base": base,
                "rank": rank,
            }

        with data_lock:

            coins.clear()

            coins.update(
                result
            )

            for symbol in result:

                if symbol not in signal_state:

                    signal_state[
                        symbol
                    ] = {
                        "signaled": False,
                        "signal_price": 0.0,
                        "retraced": False,
                    }

        print(
            f"BINANCE SPOT USDT: "
            f"{len(result)}",
            flush=True
        )

        return list(
            result.keys()
        )

    except Exception as e:

        print(
            "BINANCE ERROR:",
            repr(e),
            flush=True
        )

        return []


# ============================================================
# CANDLE
# ============================================================

def make_candle(k):

    return {

        "open_time":
            int(k["t"]),

        "open":
            float(k["o"]),

        "high":
            float(k["h"]),

        "low":
            float(k["l"]),

        "close":
            float(k["c"]),

        "volume":
            float(k["q"]),

        "buy_volume":
            float(k["Q"]),

        "closed":
            bool(k["x"]),

        "timestamp":
            time.time(),
    }


# ============================================================
# UPDATE 5M
# ============================================================

def update_5m(symbol, k):

    candle = make_candle(k)

    with data_lock:

        if symbol not in coins:
            return

        history = five_minute_data[
            symbol
        ]

        if (
            history
            and
            history[-1]["open_time"]
            ==
            candle["open_time"]
        ):

            history[-1] = candle

        else:

            history.append(
                candle
            )

        last_price[
            symbol
        ] = candle["close"]


# ============================================================
# UPDATE 1M
# ============================================================

def update_1m(symbol, k):

    candle = make_candle(k)

    with data_lock:

        if symbol not in coins:
            return

        history = one_minute_data[
            symbol
        ]

        if (
            history
            and
            history[-1]["open_time"]
            ==
            candle["open_time"]
        ):

            history[-1] = candle

        else:

            history.append(
                candle
            )

        last_price[
            symbol
        ] = candle["close"]


# ============================================================
# ORDER BOOK
# ============================================================

def update_orderbook(
    symbol,
    payload
):

    bids = payload.get(
        "bids",
        []
    )

    asks = payload.get(
        "asks",
        []
    )

    if not bids or not asks:
        return

    try:

        parsed_bids = [
            (
                float(p),
                float(q)
            )
            for p, q in bids
        ]

        parsed_asks = [
            (
                float(p),
                float(q)
            )
            for p, q in asks
        ]

        with orderbook_lock:

            order_books[
                symbol
            ] = {

                "bids":
                    parsed_bids,

                "asks":
                    parsed_asks,

                "timestamp":
                    time.time(),
            }

    except Exception as e:

        print(
            "ORDERBOOK ERROR:",
            symbol,
            repr(e),
            flush=True
        )


# ============================================================
# ORDER BOOK ANALYSIS
# ============================================================

def orderbook_stats(
    symbol,
    price
):

    with orderbook_lock:

        book = order_books.get(
            symbol
        )

        if not book:
            return None

        bids = list(
            book["bids"]
        )

        asks = list(
            book["asks"]
        )

        timestamp = book[
            "timestamp"
        ]

    if not bids or not asks:
        return None

    age = (
        time.time()
        -
        timestamp
    )

    if age > ORDERBOOK_STALE_SECONDS:
        return None

    best_bid = max(
        p for p, q in bids
    )

    best_ask = min(
        p for p, q in asks
    )

    if best_bid <= 0 or best_ask <= 0:
        return None

    mid = (
        best_bid
        +
        best_ask
    ) / 2

    spread = (
        (best_ask - best_bid)
        /
        mid
    ) * 100

    lower = price * (
        1 -
        ORDERBOOK_DEPTH_PERCENT / 100
    )

    upper = price * (
        1 +
        ORDERBOOK_DEPTH_PERCENT / 100
    )

    bid_depth = 0.0
    ask_depth = 0.0

    for p, q in bids:

        if (
            lower <= p <= price
        ):

            bid_depth += p * q

    for p, q in asks:

        if (
            price <= p <= upper
        ):

            ask_depth += p * q

    if ask_depth > 0:

        ratio = (
            bid_depth
            /
            ask_depth
        )

    else:

        ratio = 999.0

    return {

        "spread":
            spread,

        "bid_depth":
            bid_depth,

        "ask_depth":
            ask_depth,

        "ratio":
            ratio,

        "age":
            age,
    }


# ============================================================
# PRICE MOMENTUM
# ============================================================

def calculate_momentum(
    symbol,
    price
):

    with data_lock:

        candles = list(
            one_minute_data.get(
                symbol,
                []
            )
        )

    if len(candles) < 3:
        return 0, 0.0, 0.0

    # ------------------------------------
    # 1 MINUTE
    # ------------------------------------

    one_minute_price = candles[-1]["open"]

    if one_minute_price <= 0:
        return 0, 0.0, 0.0

    change_1m = (
        (price / one_minute_price)
        - 1
    ) * 100

    # ------------------------------------
    # 3 MINUTES
    # ------------------------------------

    index = max(
        0,
        len(candles) - 3
    )

    three_minute_price = candles[
        index
    ]["open"]

    if three_minute_price <= 0:
        return 0, 0.0, 0.0

    change_3m = (
        (price / three_minute_price)
        - 1
    ) * 100

    score = 0

    if change_1m >= MOMENTUM_1M_MIN:

        score += 10

    if change_3m >= MOMENTUM_3M_MIN:

        score += 10

    return (
        score,
        change_1m,
        change_3m
    )


# ============================================================
# VOLUME SCORE
# ============================================================

def calculate_volume_score(
    symbol,
    current_5m
):

    with data_lock:

        candles = list(
            five_minute_data.get(
                symbol,
                []
            )
        )

        minutes = list(
            one_minute_data.get(
                symbol,
                []
            )
        )

    if len(candles) < 6:
        return 0, 0.0, 0.0

    previous = candles[-6:-1]

    volumes = [
        c["volume"]
        for c in previous
        if c["volume"] > 0
    ]

    if not volumes:
        return 0, 0.0, 0.0

    avg_volume = (
        sum(volumes)
        /
        len(volumes)
    )

    current_volume = (
        current_5m["volume"]
    )

    if avg_volume <= 0:
        return 0, 0.0, 0.0

    acceleration = (
        current_volume
        /
        avg_volume
    )

    score = 0

    # ------------------------------------
    # 1.5x = 10
    # 2x = additional 8
    # ------------------------------------

    if acceleration >= 1.5:

        score += 10

    if acceleration >= 2.0:

        score += 8

    # ------------------------------------
    # Last minute acceleration
    # ------------------------------------

    if len(minutes) >= 5:

        recent = minutes[-1]["volume"]

        old_minutes = [
            c["volume"]
            for c in minutes[-5:-1]
            if c["volume"] > 0
        ]

        if old_minutes:

            avg_minute = (
                sum(old_minutes)
                /
                len(old_minutes)
            )

            if (
                avg_minute > 0
                and
                recent
                >=
                avg_minute
                *
                LAST_MINUTE_VOLUME_FACTOR
            ):

                score += 7

    return (
        score,
        acceleration,
        current_volume
    )


# ============================================================
# BUY PRESSURE
# ============================================================

def calculate_buy_pressure(
    candle
):

    volume = candle[
        "volume"
    ]

    buy_volume = candle[
        "buy_volume"
    ]

    if volume <= 0:
        return 0.0

    return (
        buy_volume
        /
        volume
    ) * 100


def calculate_buy_score(
    candle
):

    pressure = calculate_buy_pressure(
        candle
    )

    score = 0

    if pressure >= BUY_PRESSURE_1:

        score += 8

    if pressure >= BUY_PRESSURE_2:

        score += 6

    if pressure >= BUY_PRESSURE_3:

        score += 6

    return (
        score,
        pressure
    )


# ============================================================
# CANDLE STRENGTH
# ============================================================

def calculate_candle_score(
    candle
):

    high = candle["high"]
    low = candle["low"]
    open_price = candle["open"]
    close = candle["close"]

    candle_range = (
        high - low
    )

    if candle_range <= 0:
        return 0, 0.0, 0.0

    body = abs(
        close - open_price
    )

    body_percent = (
        body
        /
        candle_range
    ) * 100

    upper_wick = (
        high
        -
        max(
            open_price,
            close
        )
    )

    upper_wick_percent = (
        upper_wick
        /
        candle_range
    ) * 100

    score = 0

    if body_percent >= BODY_1:

        score += 7

    if body_percent >= BODY_2:

        score += 8

    # ------------------------------------
    # Long upper wick penalty
    # ------------------------------------

    if upper_wick_percent > MAX_UPPER_WICK:

        score -= 5

    return (
        max(score, 0),
        body_percent,
        upper_wick_percent
    )


# ============================================================
# VOLATILITY EXPANSION
# ============================================================

def calculate_volatility(
    symbol,
    current
):

    with data_lock:

        candles = list(
            five_minute_data.get(
                symbol,
                []
            )
        )

    if len(candles) < 6:
        return 0, 0.0

    previous_ranges = []

    for candle in candles[-6:-1]:

        if candle["open"] <= 0:
            continue

        movement = (
            abs(
                candle["close"]
                -
                candle["open"]
            )
            /
            candle["open"]
        ) * 100

        previous_ranges.append(
            movement
        )

    if not previous_ranges:
        return 0, 0.0

    current_move = (
        abs(
            current["close"]
            -
            current["open"]
        )
        /
        current["open"]
    ) * 100

    average_move = (
        sum(previous_ranges)
        /
        len(previous_ranges)
    )

    if average_move <= 0:
        return 0, 0.0

    expansion = (
        current_move
        /
        average_move
    )

    score = 0

    if expansion >= VOLATILITY_FACTOR:

        score = 10

    return (
        score,
        expansion
    )


# ============================================================
# LIQUIDITY / SPREAD
# ============================================================

def calculate_liquidity_score(
    stats
):

    if stats is None:
        return 0

    score = 0

    if stats["spread"] <= MAX_SPREAD:

        score += 5

    if (
        stats["bid_depth"]
        >=
        MIN_BID_DEPTH_USD
    ):

        score += 5

    return score


# ============================================================
# TOTAL SCORE
# ============================================================

def calculate_score(
    symbol
):

    with data_lock:

        candles_5m = list(
            five_minute_data.get(
                symbol,
                []
            )
        )

        price = last_price.get(
            symbol
        )

    if price is None:
        return None

    if len(candles_5m) < 6:
        return None

    current_5m = candles_5m[-1]

    # ========================================================
    # PRICE
    # ========================================================

    momentum_score, change_1m, change_3m = (
        calculate_momentum(
            symbol,
            price
        )
    )

    # ========================================================
    # VOLUME
    # ========================================================

    volume_score, volume_accel, current_volume = (
        calculate_volume_score(
            symbol,
            current_5m
        )
    )

    # ========================================================
    # BUY PRESSURE
    # ========================================================

    buy_score, buy_pressure = (
        calculate_buy_score(
            current_5m
        )
    )

    # ========================================================
    # CANDLE
    # ========================================================

    candle_score, body, upper_wick = (
        calculate_candle_score(
            current_5m
        )
    )

    # ========================================================
    # VOLATILITY
    # ========================================================

    volatility_score, volatility_expansion = (
        calculate_volatility(
            symbol,
            current_5m
        )
    )

    # ========================================================
    # ORDER BOOK
    # ========================================================

    book = orderbook_stats(
        symbol,
        price
    )

    liquidity_score = (
        calculate_liquidity_score(
            book
        )
    )

    # ========================================================
    # TOTAL
    # ========================================================

    total_score = (
        momentum_score
        +
        volume_score
        +
        buy_score
        +
        candle_score
        +
        volatility_score
        +
        liquidity_score
    )

    return {

        "score":
            total_score,

        "price":
            price,

        "momentum_score":
            momentum_score,

        "change_1m":
            change_1m,

        "change_3m":
            change_3m,

        "volume_score":
            volume_score,

        "volume_accel":
            volume_accel,

        "current_volume":
            current_volume,

        "buy_score":
            buy_score,

        "buy_pressure":
            buy_pressure,

        "candle_score":
            candle_score,

        "body":
            body,

        "upper_wick":
            upper_wick,

        "volatility_score":
            volatility_score,

        "volatility_expansion":
            volatility_expansion,

        "liquidity_score":
            liquidity_score,

        "book":
            book,
    }


# ============================================================
# SIGNAL STATE
# ============================================================

def can_signal(
    symbol,
    price,
    score
):

    with signal_lock:

        state = signal_state.get(
            symbol
        )

        if state is None:

            state = {
                "signaled": False,
                "signal_price": 0.0,
                "retraced": False,
            }

            signal_state[
                symbol
            ] = state

        # ====================================================
        # NO PREVIOUS SIGNAL
        # ====================================================

        if not state["signaled"]:

            return True

        previous_price = (
            state["signal_price"]
        )

        if previous_price <= 0:
            return False

        # ====================================================
        # PRICE RETRACED 2%
        # ====================================================

        retrace_level = (
            previous_price
            *
            (
                1
                -
                RETRACE_PERCENT / 100
            )
        )

        if price <= retrace_level:

            state[
                "retraced"
            ] = True

        # ====================================================
        # SECOND SIGNAL
        # ====================================================

        if (
            state["retraced"]
            and
            score >= SIGNAL_SCORE
        ):

            return True

        return False


# ============================================================
# ACTIVATE SIGNAL STATE
# ============================================================

def mark_signal(
    symbol,
    price
):

    with signal_lock:

        signal_state[
            symbol
        ] = {

            "signaled":
                True,

            "signal_price":
                price,

            "retraced":
                False,
        }


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def send_signal(
    symbol,
    data
):

    with data_lock:

        info = coins.get(
            symbol
        )

    if not info:
        return

    score = data[
        "score"
    ]

    if score >= STRONG_SIGNAL_SCORE:

        title = (
            "🔥 STRONG MOMENTUM SIGNAL"
        )

    else:

        title = (
            "🚨 MOMENTUM SIGNAL"
        )

    book = data[
        "book"
    ]

    message = []

    message.append(
        title
    )

    message.append("")

    message.append(
        f"🪙 {symbol}"
    )

    message.append(
        f"🏆 CMC Rank: #{info['rank']}"
    )

    message.append(
        f"💰 Price: {data['price']:.10g}"
    )

    message.append("")

    # ========================================================
    # SCORE
    # ========================================================

    message.append(
        f"🎯 SCORE: {score}/100"
    )

    message.append("")

    # ========================================================
    # MOMENTUM
    # ========================================================

    message.append(
        "📈 PRICE MOMENTUM"
    )

    message.append(
        f"1M: {data['change_1m']:+.2f}% "
        f"({data['momentum_score']}/20)"
    )

    message.append(
        f"3M: {data['change_3m']:+.2f}%"
    )

    message.append("")

    # ========================================================
    # VOLUME
    # ========================================================

    message.append(
        "🚀 VOLUME"
    )

    message.append(
        f"Acceleration: "
        f"{data['volume_accel']:.2f}x"
    )

    message.append(
        f"5M Volume: "
        f"${data['current_volume']:,.0f}"
    )

    message.append(
        f"Score: "
        f"{data['volume_score']}/25"
    )

    message.append("")

    # ========================================================
    # BUY PRESSURE
    # ========================================================

    message.append(
        "🟢 BUY PRESSURE"
    )

    message.append(
        f"{data['buy_pressure']:.1f}%"
    )

    message.append(
        f"Score: "
        f"{data['buy_score']}/20"
    )

    message.append("")

    # ========================================================
    # CANDLE
    # ========================================================

    message.append(
        "🕯️ CANDLE"
    )

    message.append(
        f"Body: "
        f"{data['body']:.1f}%"
    )

    message.append(
        f"Upper wick: "
        f"{data['upper_wick']:.1f}%"
    )

    message.append(
        f"Score: "
        f"{data['candle_score']}/15"
    )

    message.append("")

    # ========================================================
    # VOLATILITY
    # ========================================================

    message.append(
        "⚡ VOLATILITY"
    )

    message.append(
        f"Expansion: "
        f"{data['volatility_expansion']:.2f}x"
    )

    message.append(
        f"Score: "
        f"{data['volatility_score']}/10"
    )

    message.append("")

    # ========================================================
    # ORDER BOOK
    # ========================================================

    message.append(
        "📗 ORDER BOOK"
    )

    if book:

        message.append(
            f"Spread: "
            f"{book['spread']:.3f}%"
        )

        message.append(
            f"Bid depth ±0.5%: "
            f"${book['bid_depth']:,.0f}"
        )

        message.append(
            f"Ask depth ±0.5%: "
            f"${book['ask_depth']:,.0f}"
        )

        message.append(
            f"Bid/Ask depth: "
            f"{book['ratio']:.2f}x"
        )

    message.append(
        f"Score: "
        f"{data['liquidity_score']}/10"
    )

    message.append("")

    message.append(
        "⏱️ Binance Spot — 5M"
    )

    message.append(
        "🔄 Yenidən siqnal üçün "
        "əvvəlcə ≥2% retracement lazımdır."
    )

    text = "\n".join(
        message
    )

    print(
        "\n"
        + "=" * 70,
        flush=True
    )

    print(
        text,
        flush=True
    )

    print(
        "=" * 70,
        flush=True
    )

    send_telegram(
        text
    )


# ============================================================
# SIGNAL PROCESSOR
# ============================================================

def process_symbol(
    symbol
):

    data = calculate_score(
        symbol
    )

    if data is None:
        return

    score = data[
        "score"
    ]

    price = data[
        "price"
    ]

    # ========================================================
    # 75+ REQUIRED
    # ========================================================

    if score < SIGNAL_SCORE:

        return

    # ========================================================
    # REPEAT SIGNAL CONTROL
    # ========================================================

    if not can_signal(
        symbol,
        price,
        score
    ):

        return

    # ========================================================
    # MARK BEFORE SEND
    # ========================================================

    mark_signal(
        symbol,
        price
    )

    threading.Thread(
        target=send_signal,
        args=(
            symbol,
            data
        ),
        daemon=True
    ).start()


# ============================================================
# EVERY 1 SECOND SCANNER
# ============================================================

def scanner_worker():

    print(
        "⚡ 1-SECOND MOMENTUM SCANNER ACTIVE",
        flush=True
    )

    while True:

        start = time.time()

        with data_lock:

            symbols = list(
                coins.keys()
            )

        for symbol in symbols:

            try:

                process_symbol(
                    symbol
                )

            except Exception as e:

                print(
                    f"SCAN ERROR "
                    f"{symbol}: "
                    f"{repr(e)}",
                    flush=True
                )

        elapsed = (
            time.time()
            -
            start
        )

        sleep_time = max(
            0.05,
            CHECK_INTERVAL
            -
            elapsed
        )

        time.sleep(
            sleep_time
        )


# ============================================================
# WEBSOCKET MESSAGE
# ============================================================

def websocket_message(
    ws,
    message
):

    try:

        payload = json.loads(
            message
        )

        data = payload.get(
            "data",
            payload
        )

        event = data.get(
            "e"
        )

        # ====================================================
        # KLINE
        # ====================================================

        if event == "kline":

            k = data.get(
                "k"
            )

            if not k:
                return

            symbol = str(
                k.get(
                    "s",
                    ""
                )
            ).upper()

            interval = str(
                k.get(
                    "i",
                    ""
                )
            )

            if interval == MAIN_INTERVAL:

                update_5m(
                    symbol,
                    k
                )

            elif interval == MICRO_INTERVAL:

                update_1m(
                    symbol,
                    k
                )

            return

        # ====================================================
        # DEPTH
        # ====================================================

        if (
            "bids" in data
            and
            "asks" in data
        ):

            stream = payload.get(
                "stream",
                ""
            )

            if stream:

                symbol = (
                    stream
                    .split("@")[0]
                    .upper()
                )

                update_orderbook(
                    symbol,
                    data
                )

    except Exception as e:

        print(
            "WS MESSAGE ERROR:",
            repr(e),
            flush=True
        )


# ============================================================
# WS OPEN
# ============================================================

def websocket_open(
    ws,
    worker_id,
    symbols
):

    print(
        f"WS {worker_id}: "
        f"CONNECTED "
        f"{len(symbols)} coins",
        flush=True
    )

    streams = []

    for symbol in symbols:

        low = symbol.lower()

        # 5M
        streams.append(
            f"{low}@kline_5m"
        )

        # 1M
        streams.append(
            f"{low}@kline_1m"
        )

        # ORDER BOOK
        streams.append(
            f"{low}@depth20@100ms"
        )

    print(
        f"WS {worker_id}: "
        f"{len(streams)} streams",
        flush=True
    )

    ws.send(
        json.dumps(
            {
                "method":
                    "SUBSCRIBE",

                "params":
                    streams,

                "id":
                    worker_id,
            }
        )
    )


# ============================================================
# WS ERROR
# ============================================================

def websocket_error(
    ws,
    error,
    worker_id
):

    print(
        f"WS {worker_id} ERROR:",
        error,
        flush=True
    )


# ============================================================
# WS CLOSE
# ============================================================

def websocket_close(
    ws,
    code,
    message,
    worker_id
):

    print(
        f"WS {worker_id} CLOSED:",
        code,
        message,
        flush=True
    )


# ============================================================
# WS WORKER
# ============================================================

def websocket_worker(
    symbols,
    worker_id
):

    while True:

        try:

            ws = websocket.WebSocketApp(

                BINANCE_WS,

                on_open=lambda ws:
                    websocket_open(
                        ws,
                        worker_id,
                        symbols
                    ),

                on_message=
                    websocket_message,

                on_error=lambda ws, error:
                    websocket_error(
                        ws,
                        error,
                        worker_id
                    ),

                on_close=lambda ws,
                    code,
                    message:
                    websocket_close(
                        ws,
                        code,
                        message,
                        worker_id
                    )
            )

            ws.run_forever(
                ping_interval=20,
                ping_timeout=10
            )

        except Exception as e:

            print(
                f"WS {worker_id} EXCEPTION:",
                repr(e),
                flush=True
            )

        time.sleep(
            RECONNECT_SECONDS
        )


# ============================================================
# START WS
# ============================================================

def start_websockets(
    symbols
):

    chunks = [
        symbols[
            i:i + WS_CHUNK_SIZE
        ]

        for i in range(
            0,
            len(symbols),
            WS_CHUNK_SIZE
        )
    ]

    print(
        f"WEBSOCKET WORKERS: "
        f"{len(chunks)}",
        flush=True
    )

    for worker_id, chunk in enumerate(
        chunks,
        start=1
    ):

        threading.Thread(
            target=websocket_worker,
            args=(
                chunk,
                worker_id
            ),
            daemon=True
        ).start()

        time.sleep(
            0.5
        )


# ============================================================
# BOOTSTRAP HISTORY
# ============================================================

def load_history(
    symbols
):

    print(
        "BOOTSTRAP: LOADING HISTORY...",
        flush=True
    )

    for index, symbol in enumerate(
        symbols,
        start=1
    ):

        try:

            # -----------------------------------------------
            # 5M
            # -----------------------------------------------

            response = requests.get(
                f"{BINANCE_REST}/api/v3/klines",
                params={
                    "symbol":
                        symbol,

                    "interval":
                        "5m",

                    "limit":
                        MAX_5M_HISTORY,
                },
                timeout=10
            )

            if response.status_code == 200:

                rows = response.json()

                with data_lock:

                    five_minute_data[
                        symbol
                    ].clear()

                    for row in rows:

                        k = {

                            "t":
                                row[0],

                            "o":
                                row[1],

                            "h":
                                row[2],

                            "l":
                                row[3],

                            "c":
                                row[4],

                            "q":
                                row[7],

                            "Q":
                                row[10],

                            "x":
                                True,
                        }

                        five_minute_data[
                            symbol
                        ].append(
                            make_candle(k)
                        )

            # -----------------------------------------------
            # 1M
            # -----------------------------------------------

            response = requests.get(
                f"{BINANCE_REST}/api/v3/klines",
                params={
                    "symbol":
                        symbol,

                    "interval":
                        "1m",

                    "limit":
                        MAX_1M_HISTORY,
                },
                timeout=10
            )

            if response.status_code == 200:

                rows = response.json()

                with data_lock:

                    one_minute_data[
                        symbol
                    ].clear()

                    for row in rows:

                        k = {

                            "t":
                                row[0],

                            "o":
                                row[1],

                            "h":
                                row[2],

                            "l":
                                row[3],

                            "c":
                                row[4],

                            "q":
                                row[7],

                            "Q":
                                row[10],

                            "x":
                                True,
                        }

                        one_minute_data[
                            symbol
                        ].append(
                            make_candle(k)
                        )

            if index % 100 == 0:

                print(
                    f"BOOTSTRAP: "
                    f"{index}/"
                    f"{len(symbols)}",
                    flush=True
                )

            time.sleep(
                0.03
            )

        except Exception as e:

            print(
                f"HISTORY ERROR "
                f"{symbol}:",
                repr(e),
                flush=True
            )

    print(
        "BOOTSTRAP FINISHED",
        flush=True
    )


# ============================================================
# STATUS
# ============================================================

def status_worker():

    while True:

        time.sleep(
            60
        )

        with data_lock:

            tracked = len(
                coins
            )

            five_ready = sum(
                1
                for s in coins
                if len(
                    five_minute_data.get(
                        s,
                        []
                    )
                ) >= 6
            )

            one_ready = sum(
                1
                for s in coins
                if len(
                    one_minute_data.get(
                        s,
                        []
                    )
                ) >= 3
            )

        with orderbook_lock:

            book_ready = len(
                order_books
            )

        print(
            "\n"
            f"STATUS | "
            f"CMC={len(cmc_ranks)} | "
            f"TRACKED={tracked} | "
            f"5M_READY={five_ready} | "
            f"1M_READY={one_ready} | "
            f"ORDERBOOK={book_ready}",
            flush=True
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "\n"
        "==================================================\n"
        "       🚀 5M MOMENTUM SCORE BOT\n"
        "==================================================\n"
        f"CMC RANK: 1-2000\n"
        f"BINANCE: SPOT USDT\n"
        f"TIMEFRAME: 5M\n"
        f"MICRO TIMEFRAME: 1M\n"
        f"CHECK: EVERY 1 SECOND\n"
        f"SIGNAL: >= {SIGNAL_SCORE}/100\n"
        f"STRONG: >= {STRONG_SIGNAL_SCORE}/100\n"
        f"RETRACE: >= {RETRACE_PERCENT}%\n"
        "==================================================",
        flush=True
    )

    # ========================================================
    # TELEGRAM TEST
    # ========================================================

    send_telegram(
        "🚀 5M MOMENTUM SCORE BOT STARTED\n\n"
        "CMC #1-2000\n"
        "Binance Spot USDT\n"
        "5M + 1M + Order Book\n"
        "1 saniyəlik analiz\n\n"
        "🎯 Signal: 75/100\n"
        "🔥 Strong: 85/100\n"
        "🔄 Re-signal: 2% retracement"
    )

    # ========================================================
    # CMC
    # ========================================================

    if not load_cmc():

        print(
            "CMC LOAD FAILED",
            flush=True
        )

        return

    # ========================================================
    # BINANCE
    # ========================================================

    symbols = load_binance_symbols()

    if not symbols:

        print(
            "NO SYMBOLS",
            flush=True
        )

        return

    # ========================================================
    # HISTORY
    # ========================================================

    load_history(
        symbols
    )

    # ========================================================
    # SCANNER
    # ========================================================

    threading.Thread(
        target=scanner_worker,
        daemon=True
    ).start()

    # ========================================================
    # STATUS
    # ========================================================

    threading.Thread(
        target=status_worker,
        daemon=True
    ).start()

    # ========================================================
    # WEBSOCKETS
    # ========================================================

    start_websockets(
        symbols
    )

    # ========================================================
    # MAIN LOOP
    # ========================================================

    while True:

        time.sleep(
            3600
        )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
