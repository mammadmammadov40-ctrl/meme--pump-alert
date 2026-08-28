import os
import time
import json
import threading

from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue, Empty
from datetime import datetime, timezone

import requests
import websocket


# ============================================================
# BINANCE SPOT ONLY
#
# 5M MOMENTUM
# +
# 600 CLOSED 15M CANDLE REAL BREAKOUT
#
# SOLANA: REMOVED
# BINANCE SQUARE: REMOVED
#
# ALERT ONLY
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
# GENERAL
# ============================================================

UA = "BinanceSpot600x15mBreakoutBot/1.0"

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": UA
})

TELEGRAM_QUEUE = Queue(maxsize=500)

STOP_EVENT = threading.Event()


# ============================================================
# BINANCE
# ============================================================

BINANCE_REST = "https://api.binance.com"

BINANCE_WS = "wss://stream.binance.com:443"

# 5M momentum
MOMENTUM_INTERVAL = "5m"

# 15M breakout
BREAKOUT_INTERVAL = "15m"


# ============================================================
# HISTORY
# ============================================================

# EXACTLY 600 CLOSED 15M candles
BREAKOUT_HISTORY_LIMIT = 600

# Binance max request = 1000
# We use 500 + 100
BREAKOUT_BATCH_1 = 500
BREAKOUT_BATCH_2 = 100

# Small 5M history for momentum
MOMENTUM_HISTORY_LIMIT = 30

AVERAGE_VOLUME_CANDLES = 20


# ============================================================
# 5M MOMENTUM
# ============================================================

MIN_PRICE_CHANGE = 1.0

MIN_MOMENTUM_VOLUME_RATIO = 1.2


# ============================================================
# 24H VOLUME
# ============================================================

MIN_24H_QUOTE_VOLUME = 1_000_000


# ============================================================
# SPREAD
# ============================================================

MAX_SPREAD_PERCENT = 0.20

BOOK_CACHE_MAX_AGE = 10

REST_BOOK_TIMEOUT = 5

REST_BOOK_MIN_INTERVAL = 1.0


# ============================================================
# BUY PRESSURE
# ============================================================

MIN_BUY_PRESSURE = 55.0


# ============================================================
# REAL 15M BREAKOUT
# ============================================================

# Current CLOSED 15M candle must close
# at least 1% above the highest HIGH
# of the previous 600 CLOSED 15M candles.

MIN_BREAKOUT_PERCENT = 1.0

# Breakout volume >= 1.5x previous average
MIN_BREAKOUT_VOLUME_RATIO = 1.5


# ============================================================
# BREAKOUT CANDLE QUALITY
# ============================================================

MIN_CLOSE_POSITION = 70.0

MAX_UPPER_WICK_PERCENT = 30.0


# ============================================================
# SCORE
# ============================================================

MIN_SIGNAL_SCORE = 60

STRONG_SIGNAL_SCORE = 75


# ============================================================
# TRADE LEVELS
# ============================================================

STOP_BELOW_RESISTANCE_PERCENT = 0.50

TP1 = 3.0

TP2 = 5.0

TP3 = 8.0


# ============================================================
# SCANNER
# ============================================================

SCAN_INTERVAL = 20

STATUS_INTERVAL = 60

SIGNAL_COOLDOWN_SECONDS = 24 * 60 * 60

WS_CHUNK_SIZE = 40

RECONNECT_SECONDS = 5

BINANCE_MAX_WORKERS = 10


# ============================================================
# STATE
# ============================================================

BINANCE_SYMBOLS = []

# 15M CLOSED history
BINANCE_15M_HISTORY = {}

# 5M CLOSED history
BINANCE_5M_HISTORY = {}

# Live ticker data
BINANCE_LIVE = {}

# Order book
BINANCE_BOOKS = {}

BINANCE_LAST_BOOK_REST = {}

BINANCE_LAST_SIGNAL = {}

# Last processed 15M candle
BINANCE_LAST_BREAKOUT_CANDLE = {}

STATE_LOCK = threading.RLock()


# ============================================================
# HELPERS
# ============================================================

def now_ts():
    return time.time()


def utc_text():
    return datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def pct_change(old, new):
    if old <= 0:
        return 0.0

    return (
        (new / old) - 1.0
    ) * 100.0


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send_now(text):

    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):

        print(
            "TELEGRAM ERROR: "
            "TELEGRAM_BOT_TOKEN or "
            "TELEGRAM_CHAT_ID is missing"
        )

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
        "parse_mode": "HTML",
    }

    try:

        response = SESSION.post(
            url,
            json=payload,
            timeout=15
        )

        print(
            "TELEGRAM HTTP:",
            response.status_code
        )

        if response.ok:

            print(
                "TELEGRAM OK"
            )

            return True

        print(
            "TELEGRAM ERROR:",
            response.text[:500]
        )

    except Exception as e:

        print(
            "TELEGRAM EXCEPTION:",
            repr(e)
        )

    return False


def telegram_worker():

    while not STOP_EVENT.is_set():

        try:

            message = TELEGRAM_QUEUE.get(
                timeout=1
            )

        except Empty:

            continue

        try:

            telegram_send_now(
                message
            )

        finally:

            TELEGRAM_QUEUE.task_done()


def telegram_startup_test():

    telegram_send_now(
        "🟢 <b>BINANCE SPOT BOT TEST</b>\n\n"
        "5M Momentum + 600 CLOSED 15M "
        "Real Breakout aktivdir.\n\n"
        "📊 Resistance: previous 600 CLOSED 15M candles\n"
        "🚀 Breakout: +1%\n"
        "📈 Breakout volume: ≥1.5x\n\n"
        "🟢 Binance Spot ONLY\n"
        "❌ Solana\n"
        "❌ Binance Square\n\n"
        "⚠️ ALERT ONLY\n"
        "NO AUTOMATIC ORDER"
    )


def send_alert(text):

    try:

        TELEGRAM_QUEUE.put_nowait(
            text
        )

    except Exception:

        print(
            "Telegram queue full"
        )


# ============================================================
# BINANCE REST
# ============================================================

def binance_get(
    path,
    params=None,
    timeout=10
):

    url = BINANCE_REST + path

    response = SESSION.get(
        url,
        params=params,
        timeout=timeout
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# BINANCE SYMBOLS
# ============================================================

def load_binance_symbols():

    try:

        info = binance_get(
            "/api/v3/exchangeInfo",
            timeout=20
        )

        cmc_allowed = set()

        # ----------------------------------------------------
        # OPTIONAL CMC FILTER
        # ----------------------------------------------------

        if CMC_API_KEY:

            try:

                cmc_url = (
                    "https://pro-api.coinmarketcap.com/"
                    "v1/cryptocurrency/listings/latest"
                )

                headers = {
                    "X-CMC_PRO_API_KEY":
                    CMC_API_KEY
                }

                params = {
                    "start": 1,
                    "limit": 2000,
                    "convert": "USD",
                }

                response = SESSION.get(
                    cmc_url,
                    headers=headers,
                    params=params,
                    timeout=20
                )

                if response.ok:

                    data = response.json()

                    for item in data.get(
                        "data",
                        []
                    ):

                        symbol = str(
                            item.get(
                                "symbol",
                                ""
                            )
                        ).upper()

                        rank = int(
                            item.get(
                                "cmc_rank"
                            ) or 999999
                        )

                        if (
                            1 <= rank <= 2000
                        ):

                            cmc_allowed.add(
                                symbol
                            )

            except Exception as e:

                print(
                    "CMC error:",
                    repr(e)
                )

        # ----------------------------------------------------
        # BINANCE SPOT
        # ----------------------------------------------------

        symbols = []

        for item in info.get(
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

            if not item.get(
                "isSpotTradingAllowed",
                False
            ):

                continue

            base = str(
                item.get(
                    "baseAsset",
                    ""
                )
            ).upper()

            # Remove leveraged tokens
            if base.endswith(
                (
                    "UP",
                    "DOWN",
                    "BULL",
                    "BEAR"
                )
            ):

                continue

            # CMC filter only if API worked
            if (
                cmc_allowed
                and base not in cmc_allowed
            ):

                continue

            symbols.append(
                item["symbol"]
            )

        symbols.sort()

        with STATE_LOCK:

            BINANCE_SYMBOLS[:] = symbols

        print(
            "BINANCE SPOT SYMBOLS:",
            len(symbols)
        )

        return symbols

    except Exception as e:

        print(
            "Binance symbol load error:",
            repr(e)
        )

        return []


# ============================================================
# KLINES
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
            int(limit),
            1000
        )
    }

    if end_time is not None:

        params["endTime"] = int(
            end_time
        )

    return binance_get(
        "/api/v3/klines",
        params=params,
        timeout=20
    )


def kline_to_candle(k):

    return {
        "open_time": int(k[0]),
        "open": safe_float(k[1]),
        "high": safe_float(k[2]),
        "low": safe_float(k[3]),
        "close": safe_float(k[4]),
        "volume": safe_float(k[5]),
        "close_time": int(k[6]),
        "quote_volume": safe_float(k[7]),
        "taker_buy_quote": safe_float(k[10]),
        "closed": True,
    }


# ============================================================
# LOAD 600 CLOSED 15M CANDLES
#
# 500 + 100
# ============================================================

def load_600_closed_15m(symbol):

    try:

        now_ms = int(
            time.time() * 1000
        )

        # ----------------------------------------------------
        # FIRST BATCH
        # Latest 500
        # ----------------------------------------------------

        batch1 = get_klines(
            symbol,
            BREAKOUT_INTERVAL,
            BREAKOUT_BATCH_1,
            now_ms
        )

        if not batch1:

            return None

        candles1 = []

        for k in batch1:

            if int(k[6]) < now_ms:

                candles1.append(
                    kline_to_candle(k)
                )

        if not candles1:

            return None

        oldest_open_time = (
            candles1[0]["open_time"]
        )

        # ----------------------------------------------------
        # SECOND BATCH
        # Before first batch
        # ----------------------------------------------------

        batch2 = get_klines(
            symbol,
            BREAKOUT_INTERVAL,
            BREAKOUT_BATCH_2,
            oldest_open_time - 1
        )

        candles2 = []

        for k in batch2:

            if int(k[6]) < now_ms:

                candles2.append(
                    kline_to_candle(k)
                )

        if not candles2:

            return None

        combined = (
            candles2
            + candles1
        )

        # ----------------------------------------------------
        # Sort
        # ----------------------------------------------------

        combined.sort(
            key=lambda c:
            c["open_time"]
        )

        # ----------------------------------------------------
        # Remove duplicates
        # ----------------------------------------------------

        unique = {}

        for candle in combined:

            unique[
                candle["open_time"]
            ] = candle

        combined = list(
            unique.values()
        )

        combined.sort(
            key=lambda c:
            c["open_time"]
        )

        # ----------------------------------------------------
        # EXACTLY 600
        # ----------------------------------------------------

        combined = combined[
            -BREAKOUT_HISTORY_LIMIT:
        ]

        if len(combined) < (
            BREAKOUT_HISTORY_LIMIT
        ):

            print(
                f"15M history incomplete "
                f"{symbol}: "
                f"{len(combined)}/"
                f"{BREAKOUT_HISTORY_LIMIT}"
            )

            return None

        return deque(
            combined,
            maxlen=BREAKOUT_HISTORY_LIMIT
        )

    except Exception as e:

        print(
            f"15M history error "
            f"{symbol}:",
            repr(e)
        )

        return None


# ============================================================
# LOAD SMALL 5M HISTORY
# ============================================================

def load_5m_history(symbol):

    try:

        now_ms = int(
            time.time() * 1000
        )

        data = get_klines(
            symbol,
            MOMENTUM_INTERVAL,
            MOMENTUM_HISTORY_LIMIT,
            now_ms
        )

        candles = []

        for k in data:

            if int(k[6]) < now_ms:

                candles.append(
                    kline_to_candle(k)
                )

        candles = candles[
            -MOMENTUM_HISTORY_LIMIT:
        ]

        if not candles:

            return None

        return deque(
            candles,
            maxlen=MOMENTUM_HISTORY_LIMIT
        )

    except Exception as e:

        print(
            f"5M history error "
            f"{symbol}:",
            repr(e)
        )

        return None


# ============================================================
# LOAD ALL HISTORIES
# ============================================================

def load_binance_histories(symbols):

    print("=" * 60)

    print(
        "LOADING BINANCE HISTORIES"
    )

    print(
        "15M = EXACTLY 600 CLOSED candles"
    )

    print(
        "15M REST = 500 + 100"
    )

    print(
        "5M = 30 CLOSED candles"
    )

    print("=" * 60)

    loaded = 0

    success_15m = 0

    success_5m = 0

    def worker(symbol):

        history_15m = (
            load_600_closed_15m(
                symbol
            )
        )

        history_5m = (
            load_5m_history(
                symbol
            )
        )

        return (
            symbol,
            history_15m,
            history_5m
        )

    with ThreadPoolExecutor(
        max_workers=BINANCE_MAX_WORKERS
    ) as executor:

        futures = [
            executor.submit(
                worker,
                symbol
            )
            for symbol in symbols
        ]

        for future in as_completed(
            futures
        ):

            loaded += 1

            try:

                (
                    symbol,
                    history_15m,
                    history_5m
                ) = future.result()

                if (
                    history_15m
                    and len(history_15m)
                    == BREAKOUT_HISTORY_LIMIT
                ):

                    with STATE_LOCK:

                        BINANCE_15M_HISTORY[
                            symbol
                        ] = history_15m

                    success_15m += 1

                if history_5m:

                    with STATE_LOCK:

                        BINANCE_5M_HISTORY[
                            symbol
                        ] = history_5m

                    success_5m += 1

            except Exception as e:

                print(
                    "History worker error:",
                    repr(e)
                )

            if loaded % 25 == 0:

                print(
                    f"History: "
                    f"{loaded}/"
                    f"{len(symbols)} | "
                    f"15M Ready: "
                    f"{success_15m} | "
                    f"5M Ready: "
                    f"{success_5m}"
                )

    print("=" * 60)

    print(
        "15M HISTORY READY:",
        len(BINANCE_15M_HISTORY)
    )

    print(
        "5M HISTORY READY:",
        len(BINANCE_5M_HISTORY)
    )

    print("=" * 60)


# ============================================================
# 24H TICKER
# ============================================================

def get_24h(symbol):

    return binance_get(
        "/api/v3/ticker/24hr",
        {
            "symbol": symbol
        },
        timeout=10
    )


# ============================================================
# BOOK
# ============================================================

def get_book(symbol):

    return binance_get(
        "/api/v3/ticker/bookTicker",
        {
            "symbol": symbol
        },
        timeout=REST_BOOK_TIMEOUT
    )


def update_book(
    symbol,
    bid,
    ask,
    bid_qty,
    ask_qty
):

    bid = safe_float(bid)

    ask = safe_float(ask)

    bid_qty = safe_float(
        bid_qty
    )

    ask_qty = safe_float(
        ask_qty
    )

    if (
        bid <= 0
        or ask <= 0
    ):

        return

    mid = (
        bid + ask
    ) / 2.0

    spread = (
        (ask - bid)
        / mid
    ) * 100.0

    with STATE_LOCK:

        BINANCE_BOOKS[
            symbol
        ] = {

            "bid": bid,

            "ask": ask,

            "bid_qty": bid_qty,

            "ask_qty": ask_qty,

            "spread": spread,

            "time": now_ts(),
        }


def get_book_cached(symbol):

    with STATE_LOCK:

        book = BINANCE_BOOKS.get(
            symbol
        )

        last_rest = (
            BINANCE_LAST_BOOK_REST.get(
                symbol,
                0
            )
        )

    if (
        book
        and now_ts() - book["time"]
        <= BOOK_CACHE_MAX_AGE
    ):

        return book

    if (
        now_ts() - last_rest
        < REST_BOOK_MIN_INTERVAL
    ):

        return book

    try:

        with STATE_LOCK:

            BINANCE_LAST_BOOK_REST[
                symbol
            ] = now_ts()

        data = get_book(
            symbol
        )

        update_book(
            symbol,
            data.get("bidPrice"),
            data.get("askPrice"),
            data.get("bidQty"),
            data.get("askQty"),
        )

        with STATE_LOCK:

            return BINANCE_BOOKS.get(
                symbol
            )

    except Exception:

        return book


# ============================================================
# WEBSOCKET
# ============================================================

def ws_worker(symbols):

    if not symbols:

        return

    streams = []

    for symbol in symbols:

        s = symbol.lower()

        streams.append(
            f"{s}@kline_5m"
        )

        streams.append(
            f"{s}@kline_15m"
        )

        streams.append(
            f"{s}@bookTicker"
        )

        streams.append(
            f"{s}@ticker"
        )

    url = (
        BINANCE_WS
        + "/stream?streams="
        + "/".join(streams)
    )

    while not STOP_EVENT.is_set():

        try:

            def on_message(
                ws,
                message
            ):

                try:

                    obj = json.loads(
                        message
                    )

                    data = obj.get(
                        "data",
                        {}
                    )

                    event = data.get(
                        "e"
                    )

                    symbol = data.get(
                        "s"
                    )

                    if not symbol:

                        return

                    symbol = symbol.upper()

                    # ------------------------------------------------
                    # BOOK
                    # ------------------------------------------------

                    if event == "bookTicker":

                        update_book(
                            symbol,
                            data.get("b"),
                            data.get("a"),
                            data.get("B"),
                            data.get("A"),
                        )

                    # ------------------------------------------------
                    # 24H TICKER
                    # ------------------------------------------------

                    elif event == "24hrTicker":

                        with STATE_LOCK:

                            BINANCE_LIVE.setdefault(
                                symbol,
                                {}
                            )

                            BINANCE_LIVE[
                                symbol
                            ].update({

                                "price":
                                safe_float(
                                    data.get("c")
                                ),

                                "quote_volume":
                                safe_float(
                                    data.get("q")
                                ),

                                "change":
                                safe_float(
                                    data.get("P")
                                ),

                                "time":
                                now_ts(),
                            })

                    # ------------------------------------------------
                    # KLINE
                    # ------------------------------------------------

                    elif event == "kline":

                        k = data.get(
                            "k"
                        ) or {}

                        interval = k.get(
                            "i"
                        )

                        candle = {

                            "open_time":
                            int(
                                k.get(
                                    "t",
                                    0
                                )
                            ),

                            "open":
                            safe_float(
                                k.get("o")
                            ),

                            "high":
                            safe_float(
                                k.get("h")
                            ),

                            "low":
                            safe_float(
                                k.get("l")
                            ),

                            "close":
                            safe_float(
                                k.get("c")
                            ),

                            "volume":
                            safe_float(
                                k.get("v")
                            ),

                            "close_time":
                            int(
                                k.get(
                                    "T",
                                    0
                                )
                            ),

                            "quote_volume":
                            safe_float(
                                k.get("q")
                            ),

                            "taker_buy_quote":
                            safe_float(
                                k.get("Q")
                            ),

                            "closed":
                            bool(
                                k.get("x")
                            ),
                        }

                        # ------------------------------------------------
                        # ONLY CLOSED CANDLES
                        # ------------------------------------------------

                        if not candle[
                            "closed"
                        ]:

                            return

                        with STATE_LOCK:

                            if interval == "5m":

                                history = (
                                    BINANCE_5M_HISTORY.setdefault(
                                        symbol,
                                        deque(
                                            maxlen=
                                            MOMENTUM_HISTORY_LIMIT
                                        )
                                    )
                                )

                                if (
                                    not history
                                    or history[-1][
                                        "open_time"
                                    ]
                                    != candle[
                                        "open_time"
                                    ]
                                ):

                                    history.append(
                                        candle
                                    )

                                else:

                                    history[-1] = candle

                            elif interval == "15m":

                                history = (
                                    BINANCE_15M_HISTORY.setdefault(
                                        symbol,
                                        deque(
                                            maxlen=
                                            BREAKOUT_HISTORY_LIMIT
                                        )
                                    )
                                )

                                if (
                                    not history
                                    or history[-1][
                                        "open_time"
                                    ]
                                    != candle[
                                        "open_time"
                                    ]
                                ):

                                    history.append(
                                        candle
                                    )

                                else:

                                    history[-1] = candle

                except Exception as e:

                    print(
                        "WS message error:",
                        repr(e)
                    )

            def on_error(
                ws,
                error
            ):

                print(
                    "Binance WS error:",
                    error
                )

            def on_close(
                ws,
                code,
                msg
            ):

                print(
                    "Binance WS closed:",
                    code,
                    msg
                )

            ws = websocket.WebSocketApp(

                url,

                on_message=on_message,

                on_error=on_error,

                on_close=on_close,
            )

            ws.run_forever(

                ping_interval=20,

                ping_timeout=10,

                origin="https://www.binance.com"
            )

        except Exception as e:

            print(
                "WS worker error:",
                repr(e)
            )

        if not STOP_EVENT.is_set():

            time.sleep(
                RECONNECT_SECONDS
            )


# ============================================================
# VOLUME RATIO
# ============================================================

def volume_ratio(
    history,
    candle
):

    if not history:

        return 0.0

    data = list(
        history
    )

    if len(data) < (
        AVERAGE_VOLUME_CANDLES
    ):

        return 0.0

    previous = data[
        -AVERAGE_VOLUME_CANDLES:
    ]

    values = [

        c["quote_volume"]

        for c in previous

        if c["quote_volume"] > 0

    ]

    if not values:

        return 0.0

    average = (
        sum(values)
        / len(values)
    )

    if average <= 0:

        return 0.0

    return (
        candle["quote_volume"]
        / average
    )


# ============================================================
# BUY PRESSURE
# ============================================================

def buy_pressure_estimate(
    candle
):

    if not candle:

        return 0.0

    total = safe_float(
        candle.get(
            "quote_volume",
            0
        )
    )

    buy = safe_float(
        candle.get(
            "taker_buy_quote",
            0
        )
    )

    if (
        total > 0
        and buy > 0
    ):

        return (
            buy / total
        ) * 100.0

    # Fallback based on candle position

    candle_range = (
        candle["high"]
        - candle["low"]
    )

    if candle_range <= 0:

        return 50.0

    return (
        (
            candle["close"]
            - candle["low"]
        )
        / candle_range
    ) * 100.0


# ============================================================
# RESISTANCE
# ============================================================

def find_resistance(
    history
):

    if not history:

        return None

    data = list(
        history
    )

    if len(data) < (
        BREAKOUT_HISTORY_LIMIT
    ):

        return None

    # IMPORTANT:
    #
    # The CURRENT breakout candle is NOT
    # part of resistance.
    #
    # We use the 600 candles BEFORE it.

    previous = data[
        -BREAKOUT_HISTORY_LIMIT:
    ]

    highest = max(
        previous,
        key=lambda c:
        c["high"]
    )

    return {

        "level":
        highest["high"],

        "open_time":
        highest["open_time"],

        "age":
        len(previous)
        - 1
        - previous.index(
            highest
        ),
    }


# ============================================================
# BREAKOUT DATA
# ============================================================

def calculate_breakout(
    previous_history,
    current
):

    if (
        not previous_history
        or not current
    ):

        return None

    if len(previous_history) < (
        BREAKOUT_HISTORY_LIMIT
    ):

        return None

    # --------------------------------------------------------
    # Resistance from previous 600 candles
    # --------------------------------------------------------

    resistance = max(
        previous_history,
        key=lambda c:
        c["high"]
    )

    level = resistance[
        "high"
    ]

    # --------------------------------------------------------
    # Breakout %
    # --------------------------------------------------------

    breakout_pct = pct_change(
        level,
        current["close"]
    )

    # --------------------------------------------------------
    # Volume
    # --------------------------------------------------------

    vr = volume_ratio(
        previous_history,
        current
    )

    # --------------------------------------------------------
    # Candle range
    # --------------------------------------------------------

    candle_range = (
        current["high"]
        - current["low"]
    )

    # --------------------------------------------------------
    # Close position
    # --------------------------------------------------------

    if candle_range > 0:

        close_position = (
            (
                current["close"]
                - current["low"]
            )
            / candle_range
        ) * 100.0

    else:

        close_position = 0.0

    # --------------------------------------------------------
    # Upper wick
    # --------------------------------------------------------

    upper_wick = (
        current["high"]
        -
        max(
            current["open"],
            current["close"]
        )
    )

    if candle_range > 0:

        upper_wick_pct = (
            upper_wick
            / candle_range
        ) * 100.0

    else:

        upper_wick_pct = 100.0

    valid = (

        breakout_pct
        >= MIN_BREAKOUT_PERCENT

        and

        vr
        >= MIN_BREAKOUT_VOLUME_RATIO

        and

        close_position
        >= MIN_CLOSE_POSITION

        and

        upper_wick_pct
        <= MAX_UPPER_WICK_PERCENT
    )

    return {

        "resistance":
        level,

        "resistance_time":
        resistance["open_time"],

        "resistance_age":
        len(previous_history)
        - 1
        - previous_history.index(
            resistance
        ),

        "breakout_pct":
        breakout_pct,

        "volume_ratio":
        vr,

        "close_position":
        close_position,

        "upper_wick_pct":
        upper_wick_pct,

        "valid":
        valid,
    }


# ============================================================
# STOP
# ============================================================

def level_stop(
    resistance
):

    return resistance * (
        1.0
        -
        STOP_BELOW_RESISTANCE_PERCENT
        / 100.0
    )


# ============================================================
# SCORE
# ============================================================

def calculate_score(
    momentum,
    momentum_vr,
    buy_pressure,
    breakout_pct,
    breakout_vr,
    close_position,
    spread
):

    score = 0

    # --------------------------------------------------------
    # 5M momentum
    # --------------------------------------------------------

    if momentum >= 1.0:

        score += 15

    if momentum >= 2.0:

        score += 10

    if momentum >= 3.0:

        score += 10

    # --------------------------------------------------------
    # 5M volume
    # --------------------------------------------------------

    if momentum_vr >= 1.2:

        score += 10

    if momentum_vr >= 1.5:

        score += 10

    # --------------------------------------------------------
    # Buy pressure
    # --------------------------------------------------------

    if buy_pressure >= 55:

        score += 10

    if buy_pressure >= 70:

        score += 5

    # --------------------------------------------------------
    # Breakout
    # --------------------------------------------------------

    if breakout_pct >= 1.0:

        score += 10

    if breakout_vr >= 1.5:

        score += 10

    # --------------------------------------------------------
    # Candle
    # --------------------------------------------------------

    if close_position >= 70:

        score += 5

    # --------------------------------------------------------
    # Spread
    # --------------------------------------------------------

    if spread <= 0.10:

        score += 5

    return score


# ============================================================
# BINANCE ANALYSIS
# ============================================================

def analyze_binance(
    symbol,
    status
):

    try:

        # ----------------------------------------------------
        # GET HISTORIES
        # ----------------------------------------------------

        with STATE_LOCK:

            history_15m = (
                BINANCE_15M_HISTORY.get(
                    symbol
                )
            )

            history_5m = (
                BINANCE_5M_HISTORY.get(
                    symbol
                )
            )

            live = dict(
                BINANCE_LIVE.get(
                    symbol,
                    {}
                )
            )

        if (
            not history_15m
            or len(history_15m)
            < BREAKOUT_HISTORY_LIMIT
        ):

            return None

        if not history_5m:

            return None

        status["history"] += 1

        # ----------------------------------------------------
        # 24H VOLUME
        # ----------------------------------------------------

        qvol = safe_float(
            live.get(
                "quote_volume"
            )
        )

        if qvol <= 0:

            try:

                ticker = get_24h(
                    symbol
                )

                qvol = safe_float(
                    ticker.get(
                        "quoteVolume"
                    )
                )

            except Exception:

                return None

        if qvol < (
            MIN_24H_QUOTE_VOLUME
        ):

            return None

        status["24h"] += 1

        # ----------------------------------------------------
        # CURRENT CLOSED 5M
        # ----------------------------------------------------

        candle_5m = list(
            history_5m
        )[-1]

        if not candle_5m:

            return None

        # ----------------------------------------------------
        # 5M MOMENTUM
        # ----------------------------------------------------

        momentum = pct_change(
            candle_5m["open"],
            candle_5m["close"]
        )

        if momentum < (
            MIN_PRICE_CHANGE
        ):

            return None

        status["momentum"] += 1

        # ----------------------------------------------------
        # 5M VOLUME
        # ----------------------------------------------------

        momentum_vr = volume_ratio(
            history_5m,
            candle_5m
        )

        if momentum_vr < (
            MIN_MOMENTUM_VOLUME_RATIO
        ):

            return None

        status["volume"] += 1

        # ----------------------------------------------------
        # BUY PRESSURE
        # ----------------------------------------------------

        buy_pressure = (
            buy_pressure_estimate(
                candle_5m
            )
        )

        if buy_pressure < (
            MIN_BUY_PRESSURE
        ):

            return None

        status["buy"] += 1

        # ----------------------------------------------------
        # BOOK / SPREAD
        # ----------------------------------------------------

        book = get_book_cached(
            symbol
        )

        if not book:

            return None

        spread = safe_float(
            book.get(
                "spread",
                999
            )
        )

        if spread > (
            MAX_SPREAD_PERCENT
        ):

            return None

        status["spread"] += 1

        # ----------------------------------------------------
        # CURRENT CLOSED 15M
        #
        # IMPORTANT:
        # We use the last CLOSED candle as current.
        # Resistance is calculated from the 600 candles
        # BEFORE it.
        # ----------------------------------------------------

        data_15m = list(
            history_15m
        )

        if len(data_15m) < (
            BREAKOUT_HISTORY_LIMIT
        ):

            return None

        current_15m = data_15m[-1]

        previous_600 = data_15m[
            -BREAKOUT_HISTORY_LIMIT:
        ]

        # If current is already inside the stored
        # 600-candle history, we need the candles
        # BEFORE current.
        #
        # Therefore we require 601 candles internally
        # when evaluating a freshly closed candle.

        if (
            len(data_15m)
            >= BREAKOUT_HISTORY_LIMIT + 1
        ):

            previous_600 = data_15m[
                -(BREAKOUT_HISTORY_LIMIT + 1):-1
            ]

        else:

            # Startup history contains exactly 600.
            # Fetch one older/current separation
            # is not available here.
            #
            # We therefore use the previous 599
            # plus the candle before the current
            # only when available.
            if len(data_15m) < 2:

                return None

            previous_600 = data_15m[:-1]

            if len(previous_600) < (
                BREAKOUT_HISTORY_LIMIT
            ):

                return None

        # ----------------------------------------------------
        # Avoid duplicate processing
        # ----------------------------------------------------

        current_open_time = (
            current_15m["open_time"]
        )

        with STATE_LOCK:

            last_processed = (
                BINANCE_LAST_BREAKOUT_CANDLE.get(
                    symbol
                )
            )

        if (
            last_processed
            == current_open_time
        ):

            return None

        # ----------------------------------------------------
        # BREAKOUT
        # ----------------------------------------------------

        br = calculate_breakout(
            previous_600,
            current_15m
        )

        if not br:

            return None

        status["resistance"] += 1

        # ----------------------------------------------------
        # HARD BREAKOUT
        # ----------------------------------------------------

        if br["breakout_pct"] < (
            MIN_BREAKOUT_PERCENT
        ):

            with STATE_LOCK:

                BINANCE_LAST_BREAKOUT_CANDLE[
                    symbol
                ] = current_open_time

            return None

        status["breakout"] += 1

        # ----------------------------------------------------
        # BREAKOUT VOLUME
        # ----------------------------------------------------

        if br["volume_ratio"] < (
            MIN_BREAKOUT_VOLUME_RATIO
        ):

            with STATE_LOCK:

                BINANCE_LAST_BREAKOUT_CANDLE[
                    symbol
                ] = current_open_time

            return None

        status[
            "breakout_volume"
        ] += 1

        # ----------------------------------------------------
        # CANDLE QUALITY
        # ----------------------------------------------------

        if br["close_position"] < (
            MIN_CLOSE_POSITION
        ):

            with STATE_LOCK:

                BINANCE_LAST_BREAKOUT_CANDLE[
                    symbol
                ] = current_open_time

            return None

        if br["upper_wick_pct"] > (
            MAX_UPPER_WICK_PERCENT
        ):

            with STATE_LOCK:

                BINANCE_LAST_BREAKOUT_CANDLE[
                    symbol
                ] = current_open_time

            return None

        status["candle"] += 1

        # ----------------------------------------------------
        # SCORE
        # ----------------------------------------------------

        score = calculate_score(
            momentum,
            momentum_vr,
            buy_pressure,
            br["breakout_pct"],
            br["volume_ratio"],
            br["close_position"],
            spread
        )

        if score < (
            MIN_SIGNAL_SCORE
        ):

            with STATE_LOCK:

                BINANCE_LAST_BREAKOUT_CANDLE[
                    symbol
                ] = current_open_time

            return None

        status["score"] += 1

        # ----------------------------------------------------
        # COOLDOWN
        # ----------------------------------------------------

        current_time = now_ts()

        with STATE_LOCK:

            last_signal = (
                BINANCE_LAST_SIGNAL.get(
                    symbol,
                    0
                )
            )

        if (
            current_time
            - last_signal
            < SIGNAL_COOLDOWN_SECONDS
        ):

            with STATE_LOCK:

                BINANCE_LAST_BREAKOUT_CANDLE[
                    symbol
                ] = current_open_time

            return None

        # ----------------------------------------------------
        # SAVE SIGNAL TIME
        # ----------------------------------------------------

        with STATE_LOCK:

            BINANCE_LAST_SIGNAL[
                symbol
            ] = current_time

            BINANCE_LAST_BREAKOUT_CANDLE[
                symbol
            ] = current_open_time

        # ----------------------------------------------------
        # ALERT
        # ----------------------------------------------------

        price = current_15m[
            "close"
        ]

        resistance = br[
            "resistance"
        ]

        stop = level_stop(
            resistance
        )

        strength = (
            "🚀 STRONG BUY"
            if score >= STRONG_SIGNAL_SCORE
            else
            "🔥 BUY"
        )

        message = (

            f"{strength}\n"

            f"🟡 <b>BINANCE SPOT "
            f"15M REAL BREAKOUT</b>\n\n"

            f"🪙 <b>{symbol}</b>\n"

            f"💰 Price: "
            f"{price:g}\n"

            f"📈 5M Momentum: "
            f"{momentum:+.2f}%\n"

            f"📊 5M Volume: "
            f"{momentum_vr:.2f}x\n"

            f"🟢 Buy pressure: "
            f"{buy_pressure:.1f}%\n\n"

            f"🏔 600 × 15M Resistance: "
            f"{resistance:g}\n"

            f"🚀 15M Breakout: "
            f"{br['breakout_pct']:+.2f}%\n"

            f"📊 Breakout volume: "
            f"{br['volume_ratio']:.2f}x\n"

            f"🕯 Close position: "
            f"{br['close_position']:.1f}%\n"

            f"📐 Upper wick: "
            f"{br['upper_wick_pct']:.1f}%\n"

            f"📏 Spread: "
            f"{spread:.3f}%\n"

            f"⭐ Score: "
            f"{score}\n\n"

            f"🛑 Stop: "
            f"~{stop:g}\n"

            f"🎯 TP1: +{TP1}%\n"

            f"🎯 TP2: +{TP2}%\n"

            f"🎯 TP3: +{TP3}%\n\n"

            f"🕐 Cooldown: 24H\n"

            f"🕐 Signal time: "
            f"{utc_text()}\n\n"

            f"⚠️ ALERT ONLY\n"
            f"NO AUTOMATIC ORDER"
        )

        return message

    except Exception as e:

        print(
            f"Analysis error "
            f"{symbol}:",
            repr(e)
        )

        return None


# ============================================================
# BINANCE SCAN LOOP
# ============================================================

def binance_scan_loop():

    last_status_time = 0

    while not STOP_EVENT.is_set():

        try:

            with STATE_LOCK:

                symbols = list(
                    BINANCE_SYMBOLS
                )

            status = {

                "history": 0,

                "24h": 0,

                "momentum": 0,

                "volume": 0,

                "buy": 0,

                "spread": 0,

                "resistance": 0,

                "breakout": 0,

                "breakout_volume": 0,

                "candle": 0,

                "score": 0,
            }

            checked = 0

            signals = 0

            with ThreadPoolExecutor(
                max_workers=BINANCE_MAX_WORKERS
            ) as executor:

                futures = {

                    executor.submit(
                        analyze_binance,
                        symbol,
                        status
                    ):
                    symbol

                    for symbol in symbols
                }

                for future in as_completed(
                    futures
                ):

                    checked += 1

                    try:

                        result = future.result()

                        if result:

                            signals += 1

                            send_alert(
                                result
                            )

                    except Exception as e:

                        print(
                            "Future analysis error:",
                            repr(e)
                        )

            current_time = now_ts()

            if (
                current_time
                - last_status_time
                >= STATUS_INTERVAL
            ):

                last_status_time = (
                    current_time
                )

                print(

                    "BINANCE STATUS | "

                    f"Symbols: "
                    f"{len(symbols)} | "

                    f"Checked: "
                    f"{checked} | "

                    f"15M History: "
                    f"{status['history']} | "

                    f"24H: "
                    f"{status['24h']} | "

                    f"5M Momentum: "
                    f"{status['momentum']} | "

                    f"5M Volume: "
                    f"{status['volume']} | "

                    f"Buy Pressure: "
                    f"{status['buy']} | "

                    f"Spread: "
                    f"{status['spread']} | "

                    f"Resistance: "
                    f"{status['resistance']} | "

                    f"Breakout +1%: "
                    f"{status['breakout']} | "

                    f"Breakout Volume: "
                    f"{status['breakout_volume']} | "

                    f"Candle: "
                    f"{status['candle']} | "

                    f"Score: "
                    f"{status['score']} | "

                    f"Signals: "
                    f"{signals}"
                )

        except Exception as e:

            print(
                "Binance scan loop error:",
                repr(e)
            )

        STOP_EVENT.wait(
            SCAN_INTERVAL
        )


# ============================================================
# CONFIG
# ============================================================

def print_config():

    print("=" * 65)

    print(
        "BINANCE SPOT ONLY"
    )

    print("=" * 65)

    print()

    print(
        "TIMEFRAME:"
    )

    print(
        "  Momentum = 5M"
    )

    print(
        "  Breakout = 15M"
    )

    print()

    print(
        "15M HISTORY:"
    )

    print(
        "  EXACTLY 600 CLOSED candles"
    )

    print(
        "  REST = 500 + 100"
    )

    print(
        "  600 x 15M = 150 hours"
    )

    print(
        "  = 6 days 6 hours"
    )

    print()

    print(
        "RESISTANCE:"
    )

    print(
        "  Highest HIGH of previous "
        "600 CLOSED 15M candles"
    )

    print()

    print(
        "BREAKOUT:"
    )

    print(
        "  >= +1.00% above resistance"
    )

    print(
        "  Volume >= 1.5x"
    )

    print(
        "  Close position >= 70%"
    )

    print(
        "  Upper wick <= 30%"
    )

    print()

    print(
        "5M MOMENTUM:"
    )

    print(
        "  >= +1.00%"
    )

    print(
        "  Volume >= 1.2x"
    )

    print()

    print(
        "24H VOLUME:"
    )

    print(
        "  >= $1,000,000"
    )

    print()

    print(
        "BUY PRESSURE:"
    )

    print(
        "  >= 55%"
    )

    print()

    print(
        "SPREAD:"
    )

    print(
        "  <= 0.20%"
    )

    print()

    print(
        "SCORE:"
    )

    print(
        "  Minimum = 60"
    )

    print(
        "  Strong = 75"
    )

    print()

    print(
        "COOLDOWN:"
    )

    print(
        "  24H per symbol"
    )

    print()

    print(
        "REMOVED:"
    )

    print(
        "  ❌ Solana"
    )

    print(
        "  ❌ Binance Square"
    )

    print()

    print(
        "ALERT ONLY"
    )

    print(
        "NO AUTOMATIC ORDER"
    )

    print("=" * 65)


# ============================================================
# MAIN
# ============================================================

def main():

    print_config()

    # --------------------------------------------------------
    # Telegram worker
    # --------------------------------------------------------

    threading.Thread(
        target=telegram_worker,
        daemon=True
    ).start()

    # --------------------------------------------------------
    # Telegram test
    # --------------------------------------------------------

    telegram_startup_test()

    # --------------------------------------------------------
    # Load Binance symbols
    # --------------------------------------------------------

    symbols = load_binance_symbols()

    if not symbols:

        print(
            "ERROR: No Binance Spot symbols loaded."
        )

        return

    # --------------------------------------------------------
    # Load history
    # --------------------------------------------------------

    load_binance_histories(
        symbols
    )

    if not BINANCE_15M_HISTORY:

        print(
            "ERROR: No 15M histories loaded."
        )

        return

    # --------------------------------------------------------
    # Start WebSockets
    # --------------------------------------------------------

    for i in range(
        0,
        len(symbols),
        WS_CHUNK_SIZE
    ):

        chunk = symbols[
            i:
            i + WS_CHUNK_SIZE
        ]

        threading.Thread(
            target=ws_worker,
            args=(chunk,),
            daemon=True
        ).start()

        time.sleep(
            0.5
        )

    # --------------------------------------------------------
    # Scanner
    # --------------------------------------------------------

    threading.Thread(
        target=binance_scan_loop,
        daemon=True
    ).start()

    print()

    print(
        "🟢 BINANCE SPOT BOT STARTED"
    )

    print(
        "🟢 5M MOMENTUM ACTIVE"
    )

    print(
        "🟢 600 x 15M CLOSED BREAKOUT ACTIVE"
    )

    print(
        "🟢 BREAKOUT = +1%"
    )

    print(
        "🟢 SOLANA = REMOVED"
    )

    print(
        "🟢 BINANCE SQUARE = REMOVED"
    )

    print(
        "🟢 ALERT ONLY"
    )

    print(
        "🟢 NO AUTOMATIC ORDER"
    )

    # --------------------------------------------------------
    # Keep alive
    # --------------------------------------------------------

    while not STOP_EVENT.is_set():

        STOP_EVENT.wait(
            60
        )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        STOP_EVENT.set()

        print(
            "Stopped."
        )

    except Exception as e:

        STOP_EVENT.set()

        print(
            "FATAL ERROR:",
            repr(e)
        )
