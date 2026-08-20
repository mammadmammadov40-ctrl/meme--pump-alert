import os
import time
import json
import threading
from collections import defaultdict, deque

import requests
import websocket


# ============================================================
# FAST MEME PUMP ALERT
# ============================================================
#
# YALNIZ 5 DƏQİQƏLİK PUMP SİSTEMİ
#
# 1-ci bağlanmış şam:
#   Price change       >= +4%
#   Volume             >= $50K
#   Volume acceleration>= 1.5x
#   Buy pressure       >= 60%
#   Body               >= 60%
#   Upper wick         <= 30%
#
# ORDER BOOK:
#   Bid/Ask ratio      >= 1.30
#   Spread             <= 0.20%
#   Bid depth +/-0.5%  >= $50K
#
# 1-ci şam keçməsə:
#   2-ci şam LIVE izlənir.
#
# 1-ci + 2-ci birlikdə:
#   Total price        >= +4%
#   Total volume       >= $50K
#
#   + order book filtrləri
#
# 24 SAAT COOLDOWN
# PRICE LOCK YOXDUR
# BREAKOUT YOXDUR
#
# ============================================================


CMC_API_KEY = os.getenv("CMC_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

BINANCE_REST = "https://api.binance.com"
BINANCE_WS = "wss://stream.binance.com:9443/ws"


# ============================================================
# TIMEFRAME
# ============================================================

PUMP_INTERVAL = "5m"


# ============================================================
# CMC
# ============================================================

CMC_MIN_RANK = 1
CMC_MAX_RANK = 2000


# ============================================================
# MAIN PUMP CONDITIONS
# ============================================================

MIN_PRICE_CHANGE = 4.0
MIN_TOTAL_VOLUME = 50_000.0


# ============================================================
# EXTRA MOMENTUM CONDITIONS
# ============================================================

MIN_VOLUME_ACCELERATION = 1.5
MIN_BUY_PRESSURE = 60.0

MIN_BODY_PERCENT = 60.0
MAX_UPPER_WICK_PERCENT = 30.0


# ============================================================
# ORDER BOOK CONDITIONS
# ============================================================

MIN_BID_ASK_RATIO = 1.30

MAX_SPREAD_PERCENT = 0.20

ORDER_BOOK_DEPTH_PERCENT = 0.50

MIN_BID_DEPTH_USD = 50_000.0


# ============================================================
# COOLDOWN
# ============================================================

COOLDOWN_SECONDS = 24 * 60 * 60


# ============================================================
# WEBSOCKET
# ============================================================

WS_CHUNK_SIZE = 100

RECONNECT_SECONDS = 3

STATUS_INTERVAL = 60

CMC_REFRESH_SECONDS = 1800


# ============================================================
# HISTORY
# ============================================================

MAX_PUMP_CANDLES = 10


# ============================================================
# GLOBAL DATA
# ============================================================

coins = {}

cmc_ranks = {}


pump_history = defaultdict(
    lambda: deque(
        maxlen=MAX_PUMP_CANDLES
    )
)


# ============================================================
# ORDER BOOK DATA
# ============================================================

order_books = {}


# ============================================================
# COOLDOWN DATA
# ============================================================

cooldown_until = {}


# ============================================================
# SIGNAL STATE
# ============================================================

signal_state = {}


# ============================================================
# LOCKS
# ============================================================

data_lock = threading.RLock()

signal_lock = threading.Lock()

orderbook_lock = threading.RLock()


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(text):

    if not TELEGRAM_BOT_TOKEN:
        print(
            "TELEGRAM_BOT_TOKEN MISSING",
            flush=True
        )
        return False

    if not TELEGRAM_CHAT_ID:
        print(
            "TELEGRAM_CHAT_ID MISSING",
            flush=True
        )
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
            response.text[:1000],
            flush=True
        )

        return False

    except Exception as e:

        print(
            "TELEGRAM EXCEPTION:",
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

        print(
            f"CMC HTTP STATUS: "
            f"{response.status_code}",
            flush=True
        )

        if response.status_code != 200:

            print(
                "CMC API ERROR:",
                response.text[:3000],
                flush=True
            )

            return False

        result = response.json()

        status = result.get(
            "status",
            {}
        )

        error_code = status.get(
            "error_code",
            0
        )

        if error_code not in (
            0,
            None,
        ):

            print(
                "CMC STATUS ERROR:",
                json.dumps(
                    status,
                    indent=2,
                    ensure_ascii=False
                ),
                flush=True
            )

            return False

        data = result.get(
            "data",
            []
        )

        new_ranks = {}

        for coin in data:

            symbol = str(
                coin.get(
                    "symbol",
                    ""
                )
            ).upper()

            rank = coin.get(
                "cmc_rank"
            )

            if not symbol:
                continue

            if rank is None:
                continue

            try:
                rank = int(rank)
            except Exception:
                continue

            if not (
                CMC_MIN_RANK
                <= rank
                <= CMC_MAX_RANK
            ):
                continue

            if symbol not in new_ranks:

                new_ranks[
                    symbol
                ] = rank

        if not new_ranks:

            print(
                "CMC RANK MAP 0",
                flush=True
            )

            return False

        with data_lock:

            cmc_ranks.clear()

            cmc_ranks.update(
                new_ranks
            )

        print(
            f"CMC COINS: "
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
# CMC REFRESH
# ============================================================

def cmc_refresh_worker():

    while True:

        time.sleep(
            CMC_REFRESH_SECONDS
        )

        print(
            "CMC: REFRESHING...",
            flush=True
        )

        if load_cmc():

            load_binance_symbols()


# ============================================================
# BINANCE SYMBOLS
# ============================================================

def load_binance_symbols():

    print(
        "BINANCE: LOADING USDT SPOT...",
        flush=True
    )

    url = (
        f"{BINANCE_REST}"
        "/api/v3/exchangeInfo"
    )

    try:

        response = requests.get(
            url,
            timeout=30
        )

        if response.status_code != 200:

            print(
                "BINANCE ERROR:",
                response.text[:2000],
                flush=True
            )

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
                "name": base,
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
                        "active": True
                    }

        print(
            f"BINANCE USDT SPOT: "
            f"{len(result)}",
            flush=True
        )

        return list(
            result.keys()
        )

    except Exception as e:

        print(
            "BINANCE SYMBOL ERROR:",
            repr(e),
            flush=True
        )

        return []


# ============================================================
# CANDLE CONVERSION
# ============================================================

def row_to_candle(row):

    quote_volume = float(
        row[7]
    )

    taker_buy_quote = float(
        row[10]
    )

    return {

        "open_time":
            int(row[0]),

        "open":
            float(row[1]),

        "high":
            float(row[2]),

        "low":
            float(row[3]),

        "close":
            float(row[4]),

        "base_volume":
            float(row[5]),

        "close_time":
            int(row[6]),

        "quote_volume":
            quote_volume,

        "taker_buy_base":
            float(row[9]),

        "taker_buy_quote":
            taker_buy_quote,

        "closed":
            True,
    }


# ============================================================
# LOAD HISTORY
# ============================================================

def load_pump_history(symbols):

    print(
        f"5M BOOTSTRAP START: "
        f"{len(symbols)} coins",
        flush=True
    )

    url = (
        f"{BINANCE_REST}"
        "/api/v3/klines"
    )

    ready = 0

    for index, symbol in enumerate(
        symbols,
        start=1
    ):

        try:

            response = requests.get(
                url,
                params={
                    "symbol": symbol,
                    "interval": PUMP_INTERVAL,
                    "limit": 10,
                },
                timeout=10
            )

            if response.status_code != 200:
                continue

            rows = response.json()

            with data_lock:

                pump_history[
                    symbol
                ].clear()

                for row in rows:

                    pump_history[
                        symbol
                    ].append(
                        row_to_candle(
                            row
                        )
                    )

            if rows:
                ready += 1

            if index % 100 == 0:

                print(
                    f"5M BOOTSTRAP: "
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
                f"{symbol}: "
                f"{repr(e)}",
                flush=True
            )

    print(
        f"5M BOOTSTRAP FINISHED "
        f"HISTORY_READY={ready}",
        flush=True
    )


# ============================================================
# LIVE CANDLE
# ============================================================

def update_live_candle(
    symbol,
    kline
):

    candle = {

        "open_time":
            int(kline["t"]),

        "open":
            float(kline["o"]),

        "high":
            float(kline["h"]),

        "low":
            float(kline["l"]),

        "close":
            float(kline["c"]),

        "base_volume":
            float(kline["v"]),

        "close_time":
            int(kline["T"]),

        "quote_volume":
            float(kline["q"]),

        "taker_buy_base":
            float(kline["V"]),

        "taker_buy_quote":
            float(kline["Q"]),

        "closed":
            bool(kline["x"]),
    }

    with data_lock:

        if symbol not in coins:
            return

        candles = pump_history[
            symbol
        ]

        if (
            candles
            and
            candles[-1]["open_time"]
            ==
            candle["open_time"]
        ):

            candles[-1] = candle

        else:

            candles.append(
                candle
            )


# ============================================================
# PRICE CHANGE
# ============================================================

def price_change(
    start_price,
    end_price
):

    if start_price <= 0:
        return 0.0

    return (
        (
            end_price
            / start_price
        ) - 1.0
    ) * 100.0


# ============================================================
# CANDLE BODY / WICK
# ============================================================

def candle_shape(candle):

    high = candle["high"]

    low = candle["low"]

    open_price = candle["open"]

    close = candle["close"]

    total_range = high - low

    if total_range <= 0:

        return {
            "body": 0.0,
            "upper_wick": 0.0,
        }

    body = abs(
        close - open_price
    )

    body_percent = (
        body
        / total_range
    ) * 100.0

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
        total_range
    ) * 100.0

    return {
        "body":
            body_percent,

        "upper_wick":
            upper_wick_percent,
    }


# ============================================================
# BUY PRESSURE
# ============================================================

def buy_pressure(candle):

    total = candle[
        "quote_volume"
    ]

    buy = candle[
        "taker_buy_quote"
    ]

    if total <= 0:
        return 0.0

    return (
        buy
        / total
    ) * 100.0


# ============================================================
# VOLUME ACCELERATION
# ============================================================

def volume_acceleration(
    previous_candle,
    current_candle
):

    previous_volume = previous_candle[
        "quote_volume"
    ]

    current_volume = current_candle[
        "quote_volume"
    ]

    if previous_volume <= 0:
        return 0.0

    return (
        current_volume
        /
        previous_volume
    )


# ============================================================
# ORDER BOOK UPDATE
# ============================================================

def update_order_book(
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
                float(price),
                float(qty)
            )
            for price, qty in bids
        ]

        parsed_asks = [
            (
                float(price),
                float(qty)
            )
            for price, qty in asks
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
            f"ORDERBOOK ERROR "
            f"{symbol}: "
            f"{repr(e)}",
            flush=True
        )


# ============================================================
# ORDER BOOK ANALYSIS
# ============================================================

def get_orderbook_stats(
    symbol,
    current_price
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

    # --------------------------------------------------------
    # BEST BID / ASK
    # --------------------------------------------------------

    best_bid = max(
        price
        for price, qty in bids
    )

    best_ask = min(
        price
        for price, qty in asks
    )

    if best_bid <= 0:
        return None

    if best_ask <= 0:
        return None

    mid_price = (
        best_bid
        +
        best_ask
    ) / 2.0

    if mid_price <= 0:
        return None

    spread_percent = (
        (
            best_ask
            -
            best_bid
        )
        /
        mid_price
    ) * 100.0

    # --------------------------------------------------------
    # +/- 0.5% DEPTH
    # --------------------------------------------------------

    lower_price = (
        current_price
        *
        (
            1.0
            -
            ORDER_BOOK_DEPTH_PERCENT
            / 100.0
        )
    )

    upper_price = (
        current_price
        *
        (
            1.0
            +
            ORDER_BOOK_DEPTH_PERCENT
            / 100.0
        )
    )

    bid_depth = 0.0

    ask_depth = 0.0

    for price, qty in bids:

        if (
            price
            >= lower_price
            and
            price
            <= current_price
        ):

            bid_depth += (
                price * qty
            )

    for price, qty in asks:

        if (
            price
            >= current_price
            and
            price
            <= upper_price
        ):

            ask_depth += (
                price * qty
            )

    if ask_depth > 0:

        bid_ask_ratio = (
            bid_depth
            /
            ask_depth
        )

    else:

        bid_ask_ratio = 999.0

    return {

        "spread":
            spread_percent,

        "bid_depth":
            bid_depth,

        "ask_depth":
            ask_depth,

        "bid_ask_ratio":
            bid_ask_ratio,

        "age":
            time.time()
            -
            timestamp,
    }


# ============================================================
# ORDER BOOK FILTER
# ============================================================

def orderbook_passes(
    symbol,
    current_price
):

    stats = get_orderbook_stats(
        symbol,
        current_price
    )

    if stats is None:

        return {
            "passed": False,
            "reason":
                "ORDERBOOK_NOT_READY",
            "stats":
                None,
        }

    # --------------------------------------------------------
    # STALE BOOK PROTECTION
    # --------------------------------------------------------

    if stats["age"] > 3.0:

        return {
            "passed": False,
            "reason":
                "ORDERBOOK_STALE",
            "stats":
                stats,
        }

    # --------------------------------------------------------
    # SPREAD
    # --------------------------------------------------------

    if (
        stats["spread"]
        >
        MAX_SPREAD_PERCENT
    ):

        return {
            "passed": False,
            "reason":
                "SPREAD_TOO_WIDE",
            "stats":
                stats,
        }

    # --------------------------------------------------------
    # BID / ASK
    # --------------------------------------------------------

    if (
        stats["bid_ask_ratio"]
        <
        MIN_BID_ASK_RATIO
    ):

        return {
            "passed": False,
            "reason":
                "BID_ASK_WEAK",
            "stats":
                stats,
        }

    # --------------------------------------------------------
    # BID DEPTH
    # --------------------------------------------------------

    if (
        stats["bid_depth"]
        <
        MIN_BID_DEPTH_USD
    ):

        return {
            "passed": False,
            "reason":
                "BID_DEPTH_LOW",
            "stats":
                stats,
        }

    return {
        "passed": True,
        "reason":
            "OK",
        "stats":
            stats,
    }


# ============================================================
# COOLDOWN
# ============================================================

def cooldown_active(
    symbol
):

    now = time.time()

    with data_lock:

        until = cooldown_until.get(
            symbol,
            0
        )

    return now < until


def activate_cooldown(
    symbol
):

    until = (
        time.time()
        +
        COOLDOWN_SECONDS
    )

    with data_lock:

        cooldown_until[
            symbol
        ] = until


# ============================================================
# 1-CANDLE CONDITIONS
# ============================================================

def first_candle_conditions(
    symbol,
    previous_candle,
    candle
):

    percent = price_change(
        candle["open"],
        candle["close"]
    )

    volume = candle[
        "quote_volume"
    ]

    acceleration = (
        volume_acceleration(
            previous_candle,
            candle
        )
    )

    pressure = buy_pressure(
        candle
    )

    shape = candle_shape(
        candle
    )

    body = shape[
        "body"
    ]

    upper_wick = shape[
        "upper_wick"
    ]

    if percent < MIN_PRICE_CHANGE:
        return False, {
            "price": percent,
            "volume": volume,
            "acceleration": acceleration,
            "buy_pressure": pressure,
            "body": body,
            "upper_wick": upper_wick,
        }

    if volume < MIN_TOTAL_VOLUME:
        return False, {
            "price": percent,
            "volume": volume,
            "acceleration": acceleration,
            "buy_pressure": pressure,
            "body": body,
            "upper_wick": upper_wick,
        }

    if (
        acceleration
        <
        MIN_VOLUME_ACCELERATION
    ):
        return False, {
            "price": percent,
            "volume": volume,
            "acceleration": acceleration,
            "buy_pressure": pressure,
            "body": body,
            "upper_wick": upper_wick,
        }

    if (
        pressure
        <
        MIN_BUY_PRESSURE
    ):
        return False, {
            "price": percent,
            "volume": volume,
            "acceleration": acceleration,
            "buy_pressure": pressure,
            "body": body,
            "upper_wick": upper_wick,
        }

    if body < MIN_BODY_PERCENT:
        return False, {
            "price": percent,
            "volume": volume,
            "acceleration": acceleration,
            "buy_pressure": pressure,
            "body": body,
            "upper_wick": upper_wick,
        }

    if (
        upper_wick
        >
        MAX_UPPER_WICK_PERCENT
    ):
        return False, {
            "price": percent,
            "volume": volume,
            "acceleration": acceleration,
            "buy_pressure": pressure,
            "body": body,
            "upper_wick": upper_wick,
        }

    return True, {
        "price": percent,
        "volume": volume,
        "acceleration": acceleration,
        "buy_pressure": pressure,
        "body": body,
        "upper_wick": upper_wick,
    }


# ============================================================
# SIGNAL CHECK
# ============================================================

def check_pump_signal(
    symbol
):

    if cooldown_active(symbol):

        return None

    with data_lock:

        candles = list(
            pump_history.get(
                symbol,
                []
            )
        )

        info = coins.get(
            symbol
        )

    if info is None:
        return None

    if len(candles) < 2:
        return None

    # ========================================================
    # CLOSED CANDLES
    # ========================================================

    closed = [
        c for c in candles
        if c["closed"]
    ]

    if len(closed) < 2:
        return None

    # ========================================================
    # CURRENT / LAST CLOSED
    # ========================================================

    first = closed[-1]

    previous = closed[-2]

    # ========================================================
    # 1-CANDLE FULL SIGNAL
    # ========================================================

    passed, metrics = (
        first_candle_conditions(
            symbol,
            previous,
            first
        )
    )

    if passed:

        current_price = first[
            "close"
        ]

        book_result = (
            orderbook_passes(
                symbol,
                current_price
            )
        )

        if not book_result[
            "passed"
        ]:

            return None

        stats = book_result[
            "stats"
        ]

        return {

            "type":
                "PUMP",

            "symbol":
                symbol,

            "rank":
                info["rank"],

            "signal_price":
                current_price,

            "count":
                1,

            "first_open":
                first["open"],

            "first_close":
                first["close"],

            "first_percent":
                metrics["price"],

            "first_volume":
                metrics["volume"],

            "acceleration":
                metrics["acceleration"],

            "buy_pressure":
                metrics["buy_pressure"],

            "body":
                metrics["body"],

            "upper_wick":
                metrics["upper_wick"],

            "total_percent":
                metrics["price"],

            "total_volume":
                metrics["volume"],

            "spread":
                stats["spread"],

            "bid_ask_ratio":
                stats["bid_ask_ratio"],

            "bid_depth":
                stats["bid_depth"],

            "ask_depth":
                stats["ask_depth"],

            "live":
                False,
        }

    # ========================================================
    # 2-CANDLE FALLBACK
    #
    # FIRST CANDLE DID NOT PASS
    # SECOND CANDLE IS LIVE
    # ========================================================

    if len(candles) < 2:
        return None

    second = candles[-1]

    if second["open_time"] == first[
        "open_time"
    ]:

        return None

    # First must be closed.
    if not first["closed"]:
        return None

    second_percent = price_change(
        second["open"],
        second["close"]
    )

    total_percent = (
        metrics["price"]
        +
        second_percent
    )

    total_volume = (
        first["quote_volume"]
        +
        second["quote_volume"]
    )

    if total_percent < MIN_PRICE_CHANGE:
        return None

    if total_volume < MIN_TOTAL_VOLUME:
        return None

    # --------------------------------------------------------
    # ORDER BOOK AT SIGNAL MOMENT
    # --------------------------------------------------------

    current_price = second[
        "close"
    ]

    book_result = (
        orderbook_passes(
            symbol,
            current_price
        )
    )

    if not book_result[
        "passed"
    ]:

        return None

    stats = book_result[
        "stats"
    ]

    # --------------------------------------------------------
    # SECOND CANDLE BUY PRESSURE
    # --------------------------------------------------------

    second_pressure = buy_pressure(
        second
    )

    # We require the LIVE second candle
    # to have at least 50% taker-buy pressure.
    if second_pressure < 50.0:
        return None

    return {

        "type":
            "PUMP",

        "symbol":
            symbol,

        "rank":
            info["rank"],

        "signal_price":
            current_price,

        "count":
            2,

        "first_open":
            first["open"],

        "first_close":
            first["close"],

        "first_percent":
            metrics["price"],

        "first_volume":
            first["quote_volume"],

        "acceleration":
            metrics["acceleration"],

        "buy_pressure":
            metrics["buy_pressure"],

        "body":
            metrics["body"],

        "upper_wick":
            metrics["upper_wick"],

        "second_open":
            second["open"],

        "second_percent":
            second_percent,

        "second_volume":
            second["quote_volume"],

        "second_buy_pressure":
            second_pressure,

        "total_percent":
            total_percent,

        "total_volume":
            total_volume,

        "spread":
            stats["spread"],

        "bid_ask_ratio":
            stats["bid_ask_ratio"],

        "bid_depth":
            stats["bid_depth"],

        "ask_depth":
            stats["ask_depth"],

        "live":
            not second["closed"],
    }


# ============================================================
# SEND SIGNAL
# ============================================================

def send_pump_signal(
    signal
):

    symbol = signal[
        "symbol"
    ]

    signal_price = signal[
        "signal_price"
    ]

    activate_cooldown(
        symbol
    )

    message = []

    message.append(
        "🚨 FAST PUMP SIGNAL"
    )

    message.append("")

    message.append(
        f"🪙 {symbol}"
    )

    message.append(
        f"🏆 CMC Rank: "
        f"#{signal['rank']}"
    )

    message.append("")

    message.append(
        "🕯️ 1-ci şam"
    )

    message.append(
        f"Open: "
        f"{signal['first_open']:.10g}"
    )

    message.append(
        f"Close: "
        f"{signal['first_close']:.10g}"
    )

    message.append(
        f"📈 Artım: "
        f"{signal['first_percent']:+.2f}%"
    )

    message.append(
        f"💰 Volume: "
        f"${signal['first_volume']:,.0f}"
    )

    message.append(
        f"🚀 Volume accel: "
        f"{signal['acceleration']:.2f}x"
    )

    message.append(
        f"🟢 Buy pressure: "
        f"{signal['buy_pressure']:.1f}%"
    )

    message.append(
        f"🕯️ Body: "
        f"{signal['body']:.1f}%"
    )

    message.append(
        f"↗️ Upper wick: "
        f"{signal['upper_wick']:.1f}%"
    )

    if signal["count"] == 2:

        message.append("")

        message.append(
            "🕯️ 2-ci şam LIVE"
        )

        message.append(
            f"Open: "
            f"{signal['second_open']:.10g}"
        )

        message.append(
            f"Siqnal qiyməti: "
            f"{signal['signal_price']:.10g}"
        )

        message.append(
            f"📈 2-ci şam: "
            f"{signal['second_percent']:+.2f}%"
        )

        message.append(
            f"💰 Volume: "
            f"${signal['second_volume']:,.0f}"
        )

        message.append(
            f"🟢 2-ci Buy pressure: "
            f"{signal['second_buy_pressure']:.1f}%"
        )

    message.append("")

    message.append(
        f"📊 ÜMUMİ: "
        f"{signal['total_percent']:+.2f}%"
    )

    message.append(
        f"💵 ÜMUMİ VOLUME: "
        f"${signal['total_volume']:,.0f}"
    )

    message.append("")

    message.append(
        "📗 ORDER BOOK"
    )

    message.append(
        f"Bid/Ask: "
        f"{signal['bid_ask_ratio']:.2f}x"
    )

    message.append(
        f"↔️ Spread: "
        f"{signal['spread']:.3f}%"
    )

    message.append(
        f"📚 Bid depth ±0.5%: "
        f"${signal['bid_depth']:,.0f}"
    )

    message.append(
        f"📚 Ask depth ±0.5%: "
        f"${signal['ask_depth']:,.0f}"
    )

    message.append("")

    message.append(
        "⏱️ Binance Spot — 5M"
    )

    message.append(
        "🔒 24 saat cooldown aktivdir"
    )

    message.append(
        "🚫 Price lock yoxdur"
    )

    message.append(
        "🚫 Breakout yoxdur"
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
        "=" * 70
        + "\n",
        flush=True
    )

    send_telegram(
        text
    )


# ============================================================
# PROCESS
# ============================================================

def process_pump(
    symbol
):

    if cooldown_active(symbol):
        return

    signal = check_pump_signal(
        symbol
    )

    if signal is None:
        return

    threading.Thread(
        target=send_pump_signal,
        args=(signal,),
        daemon=True
    ).start()


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

        event_type = data.get(
            "e"
        )

        # ====================================================
        # KLINE
        # ====================================================

        if event_type == "kline":

            kline = data.get(
                "k"
            )

            if not kline:
                return

            symbol = str(
                kline.get(
                    "s",
                    ""
                )
            ).upper()

            interval = str(
                kline.get(
                    "i",
                    ""
                )
            )

            if (
                symbol
                and
                interval
                ==
                PUMP_INTERVAL
            ):

                update_live_candle(
                    symbol,
                    kline
                )

                process_pump(
                    symbol
                )

            return

        # ====================================================
        # PARTIAL ORDER BOOK
        # ====================================================

        if (
            "bids" in data
            and
            "asks" in data
        ):

            # Symbol is contained in
            # stream name for combined
            # streams, but raw partial depth
            # payload has no symbol.
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

                if symbol:

                    update_order_book(
                        symbol,
                        data
                    )

            return

    except Exception as e:

        print(
            "WS MESSAGE ERROR:",
            repr(e),
            flush=True
        )


# ============================================================
# WEBSOCKET OPEN
# ============================================================

def websocket_open(
    ws,
    worker_id,
    symbols
):

    print(
        f"WS {worker_id}: "
        f"CONNECTED "
        f"({len(symbols)} coins)",
        flush=True
    )

    streams = []

    for symbol in symbols:

        low = symbol.lower()

        streams.append(
            f"{low}@kline_5m"
        )

        streams.append(
            f"{low}@depth20@100ms"
        )

    print(
        f"WS {worker_id}: "
        f"SUBSCRIBING "
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

    print(
        f"WS {worker_id}: "
        "5M + ORDER BOOK ACTIVE",
        flush=True
    )


# ============================================================
# ERROR
# ============================================================

def websocket_error(
    ws,
    error,
    worker_id
):

    print(
        f"WS {worker_id}: "
        f"ERROR {error}",
        flush=True
    )


# ============================================================
# CLOSE
# ============================================================

def websocket_close(
    ws,
    code,
    message,
    worker_id
):

    print(
        f"WS {worker_id}: "
        f"CLOSED "
        f"code={code} "
        f"message={message}",
        flush=True
    )


# ============================================================
# WEBSOCKET WORKER
# ============================================================

def websocket_worker(
    symbols,
    worker_id
):

    while True:

        try:

            print(
                f"WS {worker_id}: "
                f"CONNECTING "
                f"({len(symbols)} coins)",
                flush=True
            )

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
                f"WS {worker_id}: "
                f"EXCEPTION "
                f"{repr(e)}",
                flush=True
            )

        print(
            f"WS {worker_id}: "
            f"RECONNECTING IN "
            f"{RECONNECT_SECONDS}s",
            flush=True
        )

        time.sleep(
            RECONNECT_SECONDS
        )


# ============================================================
# START WEBSOCKETS
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
        f"WEBSOCKET CONNECTIONS: "
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

        time.sleep(1)


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "\n"
        "========================================\n"
        "       FAST MEME PUMP ALERT\n"
        "========================================",
        flush=True
    )

    print(
        f"CMC RANK: "
        f"{CMC_MIN_RANK}-"
        f"{CMC_MAX_RANK}",
        flush=True
    )

    print(
        f"TIMEFRAME: "
        f"{PUMP_INTERVAL}",
        flush=True
    )

    print(
        f"PRICE: "
        f"+{MIN_PRICE_CHANGE}%",
        flush=True
    )

    print(
        f"VOLUME: "
        f"${MIN_TOTAL_VOLUME:,.0f}",
        flush=True
    )

    print(
        f"VOLUME ACCEL: "
        f"{MIN_VOLUME_ACCELERATION}x",
        flush=True
    )

    print(
        f"BUY PRESSURE: "
        f"{MIN_BUY_PRESSURE}%",
        flush=True
    )

    print(
        f"BODY: "
        f"{MIN_BODY_PERCENT}%",
        flush=True
    )

    print(
        f"UPPER WICK: "
        f"<={MAX_UPPER_WICK_PERCENT}%",
        flush=True
    )

    print(
        f"BID/ASK: "
        f">={MIN_BID_ASK_RATIO}x",
        flush=True
    )

    print(
        f"SPREAD: "
        f"<={MAX_SPREAD_PERCENT}%",
        flush=True
    )

    print(
        f"BID DEPTH: "
        f">=${MIN_BID_DEPTH_USD:,.0f}",
        flush=True
    )

    print(
        "24H COOLDOWN: ACTIVE",
        flush=True
    )

    print(
        "PRICE LOCK: DISABLED",
        flush=True
    )

    print(
        "BREAKOUT: DISABLED",
        flush=True
    )

    print(
        "========================================\n",
        flush=True
    )

    # ========================================================
    # TELEGRAM TEST
    # ========================================================

    send_telegram(
        "✅ FAST MEME PUMP ALERT STARTED\n\n"

        "⏱️ Timeframe: 5M\n"

        "🎯 1-ci şam: ≥4% + ≥$50K\n"

        "🚀 Volume acceleration: ≥1.5x\n"

        "🟢 Buy pressure: ≥60%\n"

        "🕯️ Body: ≥60%\n"

        "↗️ Upper wick: ≤30%\n"

        "📗 Bid/Ask: ≥1.30x\n"

        "↔️ Spread: ≤0.20%\n"

        "📚 Bid depth ±0.5%: ≥$50K\n"

        "⏱️ 24 saat cooldown\n"

        "🚫 Price lock yoxdur\n"

        "🚫 Breakout yoxdur"
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
            "BOT STOPPED: "
            "NO TRACKED COINS",
            flush=True
        )

        return

    # ========================================================
    # HISTORY
    # ========================================================

    load_pump_history(
        symbols
    )

    # ========================================================
    # CMC REFRESH
    # ========================================================

    threading.Thread(
        target=cmc_refresh_worker,
        daemon=True
    ).start()

    # ========================================================
    # WEBSOCKETS
    # ========================================================

    start_websockets(
        symbols
    )

    # ========================================================
    # STATUS
    # ========================================================

    while True:

        with data_lock:

            tracked = len(
                coins
            )

            ready = sum(
                1
                for symbol in coins
                if len(
                    pump_history.get(
                        symbol,
                        []
                    )
                ) >= 2
            )

            cooldown_count = sum(
                1
                for symbol in coins
                if cooldown_active(
                    symbol
                )
            )

            rank_count = len(
                cmc_ranks
            )

        with orderbook_lock:

            orderbook_ready = len(
                order_books
            )

        print(
            f"STATUS | "
            f"CMC={rank_count} | "
            f"TRACKED={tracked} | "
            f"5M_READY={ready} | "
            f"ORDERBOOK_READY={orderbook_ready} | "
            f"24H_COOLDOWN={cooldown_count}",
            flush=True
        )

        time.sleep(
            STATUS_INTERVAL
        )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
