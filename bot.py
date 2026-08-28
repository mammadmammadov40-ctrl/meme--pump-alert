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
# SOLANA REMOVED
# BINANCE SQUARE REMOVED
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

TELEGRAM_QUEUE = Queue(
    maxsize=500
)

STOP_EVENT = threading.Event()

STATE_LOCK = threading.RLock()


# ============================================================
# BINANCE
# ============================================================

BINANCE_REST = "https://api.binance.com"

BINANCE_WS = "wss://stream.binance.com:443"


# ============================================================
# TIMEFRAMES
# ============================================================

MOMENTUM_INTERVAL = "5m"

BREAKOUT_INTERVAL = "15m"


# ============================================================
# 15M BREAKOUT HISTORY
# ============================================================

# EXACTLY 600 CLOSED 15M candles
RESISTANCE_LOOKBACK = 600

# Binance maximum REST klines = 1000
KLINE_BATCH_SIZE = 500

KLINE_SECOND_BATCH = 100

HISTORY_LIMIT = 600

# Internal buffer.
# We keep one extra candle so the newest CLOSED candle
# can be tested against the PREVIOUS 600 candles.
BREAKOUT_HISTORY_MAX = 601


# ============================================================
# 5M MOMENTUM HISTORY
# ============================================================

MOMENTUM_HISTORY_LIMIT = 25

AVERAGE_VOLUME_CANDLES = 20


# ============================================================
# MOMENTUM CONDITIONS
# ============================================================

MIN_PRICE_CHANGE = 1.0

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
# 5M VOLUME
# ============================================================

VOLUME_MIN_RATIO = 1.2


# ============================================================
# REAL 15M BREAKOUT
# ============================================================

# Current CLOSED 15M candle must close
# at least +1% above the highest HIGH
# of the previous 600 CLOSED 15M candles.
MIN_BREAKOUT_PERCENT = 1.0

# Breakout candle volume >= 1.5x
MIN_BREAKOUT_VOLUME_RATIO = 1.5


# ============================================================
# BREAKOUT CANDLE QUALITY
# ============================================================

MIN_CLOSE_POSITION = 70.0

MAX_UPPER_WICK_PERCENT = 30.0


# ============================================================
# TRADE LEVELS
# ============================================================

STOP_BELOW_RESISTANCE_PERCENT = 0.50

TP1 = 3.0

TP2 = 5.0

TP3 = 8.0


# ============================================================
# SCORE
# ============================================================

MIN_SIGNAL_SCORE = 60

STRONG_SIGNAL_SCORE = 75


# ============================================================
# WEBSOCKET / SCANNER
# ============================================================

WS_CHUNK_SIZE = 40

RECONNECT_SECONDS = 5

STATUS_INTERVAL = 60

SCAN_INTERVAL = 20

SIGNAL_COOLDOWN_SECONDS = 24 * 60 * 60

BINANCE_MAX_WORKERS = 12


# ============================================================
# STATE
# ============================================================

BINANCE_SYMBOLS = []

# 15M CLOSED history
BINANCE_15M_HISTORY = {}

# 5M CLOSED history
BINANCE_5M_HISTORY = {}

# Live ticker / current candles
BINANCE_LIVE = {}

# Order book
BINANCE_BOOKS = {}

BINANCE_LAST_SIGNAL = {}

BINANCE_LAST_BOOK_REST = {}


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


def safe_float(
    value,
    default=0.0
):
    try:
        return float(value)
    except Exception:
        return default


def pct_change(
    old,
    new
):
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
            "TELEGRAM ERROR BODY:",
            response.text[:1000]
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

            message = (
                TELEGRAM_QUEUE.get(
                    timeout=1
                )
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

        "Binance Spot aktivdir.\n\n"

        "📊 Momentum: 5M\n"

        "🏔 Resistance: previous "
        "600 CLOSED 15M candles\n"

        "🚀 Breakout: +1%\n"

        "📈 Breakout volume: ≥1.5x\n\n"

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
# LOAD BINANCE SPOT SYMBOLS
# ============================================================

def load_binance_symbols():

    try:

        info = binance_get(
            "/api/v3/exchangeInfo",
            timeout=20
        )


        allowed_cmc = set()


        # ----------------------------------------------------
        # CMC FILTER
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

                    for item in response.json().get(
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
                            1
                            <= rank
                            <= 2000
                        ):

                            allowed_cmc.add(
                                symbol
                            )


                    print(
                        "CMC symbols:",
                        len(allowed_cmc)
                    )


                else:

                    print(
                        "CMC HTTP error:",
                        response.status_code
                    )


            except Exception as e:

                print(
                    "CMC error:",
                    repr(e)
                )


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


            # ------------------------------------------------
            # CMC filter
            # ------------------------------------------------

            if (
                allowed_cmc
                and base not in allowed_cmc
            ):

                continue


            symbols.append(
                item["symbol"]
            )


        symbols = sorted(
            set(symbols)
        )


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
        params,
        timeout=20
    )


def kline_to_candle(k):

    return {

        "open_time":
        int(k[0]),

        "open":
        safe_float(k[1]),

        "high":
        safe_float(k[2]),

        "low":
        safe_float(k[3]),

        "close":
        safe_float(k[4]),

        "volume":
        safe_float(k[5]),

        "close_time":
        int(k[6]),

        "quote_volume":
        safe_float(k[7]),

        "taker_buy_quote":
        safe_float(k[10]),

        "closed":
        True,
    }


# ============================================================
# LOAD EXACTLY 600 CLOSED 15M CANDLES
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
        # 500 latest 15M candles
        # ----------------------------------------------------

        batch1 = get_klines(
            symbol,
            BREAKOUT_INTERVAL,
            KLINE_BATCH_SIZE,
            now_ms
        )


        if not batch1:

            return None


        candles1 = []


        for k in batch1:

            close_time = int(
                k[6]
            )


            if close_time < now_ms:

                candles1.append(
                    kline_to_candle(k)
                )


        if not candles1:

            return None


        # ----------------------------------------------------
        # Oldest candle in batch 1
        # ----------------------------------------------------

        oldest_open_time = (
            candles1[0]["open_time"]
        )


        # ----------------------------------------------------
        # SECOND BATCH
        #
        # Request candles BEFORE batch 1.
        # We need 100 immediately previous candles.
        # ----------------------------------------------------

        batch2 = get_klines(
            symbol,
            BREAKOUT_INTERVAL,
            KLINE_BATCH_SIZE,
            oldest_open_time - 1
        )


        candles2 = []


        for k in batch2:

            close_time = int(
                k[6]
            )


            if close_time < now_ms:

                candles2.append(
                    kline_to_candle(k)
                )


        if not candles2:

            return None


        # Latest 100 candles immediately
        # before batch 1.
        candles2 = candles2[
            -KLINE_SECOND_BATCH:
        ]


        combined = (
            candles2
            +
            candles1
        )


        # Sort
        combined.sort(
            key=lambda c:
            c["open_time"]
        )


        # Remove duplicates
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


        # Exactly latest 600 CLOSED candles
        combined = combined[
            -HISTORY_LIMIT:
        ]


        if len(combined) < HISTORY_LIMIT:

            print(
                f"15M HISTORY INCOMPLETE "
                f"{symbol}: "
                f"{len(combined)}/"
                f"{HISTORY_LIMIT}"
            )

            return None


        return deque(
            combined,
            maxlen=BREAKOUT_HISTORY_MAX
        )


    except Exception as e:

        print(
            f"15M history error "
            f"{symbol}: {repr(e)}"
        )

        return None


# ============================================================
# LOAD RECENT 5M HISTORY
# ============================================================

def load_5m_history(symbol):

    try:

        now_ms = int(
            time.time() * 1000
        )


        data = get_klines(
            symbol,
            MOMENTUM_INTERVAL,
            MOMENTUM_HISTORY_LIMIT + 2,
            now_ms
        )


        candles = []


        for k in data:

            close_time = int(
                k[6]
            )


            if close_time < now_ms:

                candles.append(
                    kline_to_candle(k)
                )


        candles = candles[
            -MOMENTUM_HISTORY_LIMIT:
        ]


        if len(candles) < 21:

            return None


        return deque(
            candles,
            maxlen=MOMENTUM_HISTORY_LIMIT
        )


    except Exception as e:

        print(
            f"5M history error "
            f"{symbol}: {repr(e)}"
        )

        return None


# ============================================================
# LOAD ALL HISTORIES
# ============================================================

def load_binance_histories(symbols):

    print("=" * 65)

    print(
        "LOADING BINANCE HISTORIES"
    )

    print(
        "15M BREAKOUT HISTORY: "
        "EXACTLY 600 CLOSED CANDLES"
    )

    print(
        "REST: 500 + 100"
    )

    print(
        "5M MOMENTUM HISTORY: "
        "25 CLOSED CANDLES"
    )

    print("=" * 65)


    loaded = 0

    success_15m = 0

    success_5m = 0


    def load_one(symbol):

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
                load_one,
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
                    >= HISTORY_LIMIT
                ):

                    with STATE_LOCK:

                        BINANCE_15M_HISTORY[
                            symbol
                        ] = history_15m


                    success_15m += 1


                if (
                    history_5m
                    and len(history_5m)
                    >= 21
                ):

                    with STATE_LOCK:

                        BINANCE_5M_HISTORY[
                            symbol
                        ] = history_5m


                    success_5m += 1


            except Exception as e:

                print(
                    "History future error:",
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


    print("=" * 65)

    print(
        "15M HISTORY READY:",
        len(BINANCE_15M_HISTORY)
    )

    print(
        "5M HISTORY READY:",
        len(BINANCE_5M_HISTORY)
    )

    print("=" * 65)


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

    bid = safe_float(
        bid
    )

    ask = safe_float(
        ask
    )

    bid_qty = safe_float(
        bid_qty
    )

    ask_qty = safe_float(
        ask_qty
    )


    if bid <= 0 or ask <= 0:

        return


    mid = (
        bid + ask
    ) / 2.0


    if mid <= 0:

        return


    spread = (
        (ask - bid)
        / mid
    ) * 100.0


    with STATE_LOCK:

        BINANCE_BOOKS[
            symbol
        ] = {

            "bid":
            bid,

            "ask":
            ask,

            "bid_qty":
            bid_qty,

            "ask_qty":
            ask_qty,

            "spread":
            spread,

            "time":
            now_ts(),
        }


def get_book_cached(symbol):

    with STATE_LOCK:

        book = BINANCE_BOOKS.get(
            symbol
        )


    if (
        book
        and
        now_ts()
        - book["time"]
        <= BOOK_CACHE_MAX_AGE
    ):

        return book


    try:

        last = BINANCE_LAST_BOOK_REST.get(
            symbol,
            0
        )


        if (
            now_ts()
            - last
            >= REST_BOOK_MIN_INTERVAL
        ):

            BINANCE_LAST_BOOK_REST[
                symbol
            ] = now_ts()


            data = get_book(
                symbol
            )


            update_book(
                symbol,

                data.get(
                    "bidPrice"
                ),

                data.get(
                    "askPrice"
                ),

                data.get(
                    "bidQty"
                ),

                data.get(
                    "askQty"
                )
            )


            with STATE_LOCK:

                return BINANCE_BOOKS.get(
                    symbol
                )


    except Exception:

        pass


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


        # 5M
        streams.append(
            f"{s}@kline_5m"
        )


        # 15M
        streams.append(
            f"{s}@kline_15m"
        )


        # Book
        streams.append(
            f"{s}@bookTicker"
        )


        # 24H ticker
        streams.append(
            f"{s}@ticker"
        )


    stream_url = (
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


                    # ========================================
                    # BOOK TICKER
                    # ========================================

                    if event == "bookTicker":

                        update_book(

                            symbol,

                            data.get("b"),

                            data.get("a"),

                            data.get("B"),

                            data.get("A")
                        )


                        return


                    # ========================================
                    # 24H TICKER
                    # ========================================

                    if event == "24hrTicker":

                        with STATE_LOCK:

                            live = (
                                BINANCE_LIVE.setdefault(
                                    symbol,
                                    {}
                                )
                            )


                            live.update({

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


                        return


                    # ========================================
                    # KLINE
                    # ========================================

                    if event != "kline":

                        return


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

                        "close_time":
                        int(
                            k.get(
                                "T",
                                0
                            )
                        ),
                    }


                    with STATE_LOCK:

                        live = (
                            BINANCE_LIVE.setdefault(
                                symbol,
                                {}
                            )
                        )


                        # ------------------------------------
                        # Current live candle
                        # ------------------------------------

                        if interval == "5m":

                            live[
                                "5m_candle"
                            ] = candle


                        elif interval == "15m":

                            live[
                                "15m_candle"
                            ] = candle


                        # ------------------------------------
                        # CLOSED 5M
                        # ------------------------------------

                        if (
                            interval == "5m"
                            and
                            candle["closed"]
                        ):

                            history = (
                                BINANCE_5M_HISTORY.get(
                                    symbol
                                )
                            )


                            if history is None:

                                history = deque(
                                    maxlen=
                                    MOMENTUM_HISTORY_LIMIT
                                )


                                BINANCE_5M_HISTORY[
                                    symbol
                                ] = history


                            if (
                                not history
                                or
                                history[-1][
                                    "open_time"
                                ]
                                !=
                                candle[
                                    "open_time"
                                ]
                            ):

                                history.append(
                                    candle
                                )


                            live.pop(
                                "5m_candle",
                                None
                            )


                        # ------------------------------------
                        # CLOSED 15M
                        # ------------------------------------

                        if (
                            interval == "15m"
                            and
                            candle["closed"]
                        ):

                            history = (
                                BINANCE_15M_HISTORY.get(
                                    symbol
                                )
                            )


                            if history is None:

                                history = deque(
                                    maxlen=
                                    BREAKOUT_HISTORY_MAX
                                )


                                BINANCE_15M_HISTORY[
                                    symbol
                                ] = history


                            if (
                                not history
                                or
                                history[-1][
                                    "open_time"
                                ]
                                !=
                                candle[
                                    "open_time"
                                ]
                            ):

                                history.append(
                                    candle
                                )


                            live.pop(
                                "15m_candle",
                                None
                            )


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
                message
            ):

                print(
                    "Binance WS closed:",
                    code,
                    message
                )


            websocket_app = (
                websocket.WebSocketApp(

                    stream_url,

                    on_message=on_message,

                    on_error=on_error,

                    on_close=on_close
                )
            )


            websocket_app.run_forever(

                ping_interval=20,

                ping_timeout=10,

                origin="https://www.binance.com"
            )


        except Exception as e:

            print(
                "Binance WS worker error:",
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
    candle,
    periods=20
):

    if not history or not candle:

        return 0.0


    data = list(
        history
    )


    # IMPORTANT:
    # Average uses candles BEFORE current candle.
    #
    # If history already contains current candle,
    # remove it first.

    if (
        data
        and
        data[-1]["open_time"]
        ==
        candle["open_time"]
    ):

        data = data[:-1]


    if len(data) < periods:

        return 0.0


    previous = data[
        -periods:
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
        /
        len(values)
    )


    if average <= 0:

        return 0.0


    return (
        candle["quote_volume"]
        /
        average
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
        and
        buy > 0
    ):

        return (
            buy
            /
            total
        ) * 100.0


    # Fallback
    candle_range = (
        candle["high"]
        -
        candle["low"]
    )


    if candle_range <= 0:

        return 50.0


    return (
        (
            candle["close"]
            -
            candle["low"]
        )
        /
        candle_range
    ) * 100.0


# ============================================================
# 5M MOMENTUM
# ============================================================

def momentum_5m(
    candle
):

    if not candle:

        return 0.0


    return pct_change(
        candle["open"],
        candle["close"]
    )


# ============================================================
# 600 x 15M RESISTANCE
# ============================================================

def find_resistance(
    history,
    current
):

    if not history or not current:

        return None


    data = list(
        history
    )


    # IMPORTANT:
    #
    # Current breakout candle MUST NOT
    # be part of resistance.
    #
    # We need exactly the previous
    # 600 CLOSED 15M candles.

    if (
        data
        and
        data[-1]["open_time"]
        ==
        current["open_time"]
    ):

        previous = data[:-1]

    else:

        previous = data


    if len(previous) < RESISTANCE_LOOKBACK:

        return None


    previous = previous[
        -RESISTANCE_LOOKBACK:
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
        -
        1
        -
        previous.index(
            highest
        ),

        "candles":
        len(previous),
    }


# ============================================================
# BREAKOUT DATA
# ============================================================

def breakout_data(
    history,
    current,
    resistance
):

    if (
        not history
        or not current
        or not resistance
    ):

        return None


    level = resistance[
        "level"
    ]


    # ----------------------------------------
    # Breakout %
    # ----------------------------------------

    breakout_pct = pct_change(
        level,
        current["close"]
    )


    # ----------------------------------------
    # Volume
    # ----------------------------------------

    vr = volume_ratio(
        history,
        current,
        20
    )


    # ----------------------------------------
    # Candle range
    # ----------------------------------------

    candle_range = (
        current["high"]
        -
        current["low"]
    )


    if candle_range > 0:

        close_position = (

            (
                current["close"]
                -
                current["low"]
            )
            /
            candle_range

        ) * 100.0

    else:

        close_position = 0.0


    # ----------------------------------------
    # Upper wick
    # ----------------------------------------

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
            /
            candle_range

        ) * 100.0

    else:

        upper_wick_pct = 100.0


    # ----------------------------------------
    # Hard validation
    # ----------------------------------------

    valid = (

        breakout_pct
        >=
        MIN_BREAKOUT_PERCENT

        and

        vr
        >=
        MIN_BREAKOUT_VOLUME_RATIO

        and

        close_position
        >=
        MIN_CLOSE_POSITION

        and

        upper_wick_pct
        <=
        MAX_UPPER_WICK_PERCENT
    )


    return {

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

    return (

        resistance
        *
        (
            1.0
            -
            STOP_BELOW_RESISTANCE_PERCENT
            /
            100.0
        )
    )


# ============================================================
# ANALYZE BINANCE
# ============================================================

def analyze_binance(
    symbol,
    status
):

    try:

        # ================================================
        # LOAD STATE
        # ================================================

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
            or
            len(history_15m)
            <
            RESISTANCE_LOOKBACK
        ):

            return None


        if (
            not history_5m
            or
            len(history_5m)
            < 21
        ):

            return None


        status["history"] += 1


        # ================================================
        # 24H VOLUME
        # ================================================

        qvol = safe_float(
            live.get(
                "quote_volume"
            )
        )


        if qvol <= 0:

            try:

                ticker = binance_get(

                    "/api/v3/ticker/24hr",

                    {
                        "symbol":
                        symbol
                    },

                    timeout=10
                )


                qvol = safe_float(
                    ticker.get(
                        "quoteVolume"
                    )
                )


            except Exception:

                return None


        if (
            qvol
            <
            MIN_24H_QUOTE_VOLUME
        ):

            return None


        status["24h"] += 1


        # ================================================
        # CURRENT CLOSED 5M CANDLE
        # ================================================

        candle_5m = None


        live_5m = live.get(
            "5m_candle"
        )


        # We only use CLOSED 5M.
        if (
            live_5m
            and
            live_5m.get(
                "closed"
            )
        ):

            candle_5m = live_5m

        else:

            candle_5m = list(
                history_5m
            )[-1]


        if not candle_5m:

            return None


        # ================================================
        # 5M MOMENTUM
        # ================================================

        momentum = momentum_5m(
            candle_5m
        )


        if (
            momentum
            <
            MIN_PRICE_CHANGE
        ):

            return None


        status["momentum"] += 1


        # ================================================
        # 5M VOLUME
        # ================================================

        momentum_vr = volume_ratio(

            history_5m,

            candle_5m,

            AVERAGE_VOLUME_CANDLES
        )


        if (
            momentum_vr
            <
            VOLUME_MIN_RATIO
        ):

            return None


        status["volume"] += 1


        # ================================================
        # BUY PRESSURE
        # ================================================

        buy_pressure = (
            buy_pressure_estimate(
                candle_5m
            )
        )


        if (
            buy_pressure
            <
            MIN_BUY_PRESSURE
        ):

            return None


        status["buy"] += 1


        # ================================================
        # SPREAD
        # ================================================

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


        if (
            spread
            >
            MAX_SPREAD_PERCENT
        ):

            return None


        status["spread"] += 1


        # ================================================
        # CURRENT CLOSED 15M CANDLE
        # ================================================

        candle_15m = None


        live_15m = live.get(
            "15m_candle"
        )


        if (
            live_15m
            and
            live_15m.get(
                "closed"
            )
        ):

            candle_15m = live_15m

        else:

            candle_15m = list(
                history_15m
            )[-1]


        if not candle_15m:

            return None


        # ================================================
        # RESISTANCE
        #
        # Previous 600 CLOSED 15M candles.
        # Current candle excluded.
        # ================================================

        resistance = find_resistance(

            history_15m,

            candle_15m
        )


        if not resistance:

            return None


        status["resistance"] += 1


        # ================================================
        # BREAKOUT
        # ================================================

        br = breakout_data(

            history_15m,

            candle_15m,

            resistance
        )


        if not br:

            return None


        # ================================================
        # HARD +1% BREAKOUT
        # ================================================

        if (
            br["breakout_pct"]
            <
            MIN_BREAKOUT_PERCENT
        ):

            return None


        status["breakout"] += 1


        # ================================================
        # BREAKOUT VOLUME
        # ================================================

        if (
            br["volume_ratio"]
            <
            MIN_BREAKOUT_VOLUME_RATIO
        ):

            return None


        status[
            "breakout_volume"
        ] += 1


        # ================================================
        # CANDLE QUALITY
        # ================================================

        if (
            br["close_position"]
            <
            MIN_CLOSE_POSITION
        ):

            return None


        if (
            br["upper_wick_pct"]
            >
            MAX_UPPER_WICK_PERCENT
        ):

            return None


        status["candle"] += 1


        # ================================================
        # SCORE
        # ================================================

        score = 0


        # --------------------------------
        # 5M MOMENTUM
        # --------------------------------

        if momentum >= 1.0:

            score += 15


        if momentum >= 2.0:

            score += 10


        if momentum >= 3.0:

            score += 10


        # --------------------------------
        # 5M VOLUME
        # --------------------------------

        if momentum_vr >= 1.2:

            score += 10


        if momentum_vr >= 1.5:

            score += 10


        # --------------------------------
        # BUY PRESSURE
        # --------------------------------

        if buy_pressure >= 55:

            score += 10


        if buy_pressure >= 70:

            score += 5


        # --------------------------------
        # 15M BREAKOUT
        # --------------------------------

        if br["breakout_pct"] >= 1.0:

            score += 10


        if br["volume_ratio"] >= 1.5:

            score += 10


        # --------------------------------
        # CANDLE
        # --------------------------------

        if br["close_position"] >= 70:

            score += 5


        # --------------------------------
        # SPREAD
        # --------------------------------

        if spread <= 0.10:

            score += 5


        # ================================================
        # MIN SCORE
        # ================================================

        if score < MIN_SIGNAL_SCORE:

            return None


        status["score"] += 1


        # ================================================
        # COOLDOWN
        # ================================================

        now = now_ts()


        with STATE_LOCK:

            last_signal = (
                BINANCE_LAST_SIGNAL.get(
                    symbol,
                    0
                )
            )


        if (
            now
            -
            last_signal
            <
            SIGNAL_COOLDOWN_SECONDS
        ):

            return None


        # ================================================
        # PRICE / LEVELS
        # ================================================

        price = candle_15m[
            "close"
        ]


        resistance_level = (
            resistance[
                "level"
            ]
        )


        stop = level_stop(
            resistance_level
        )


        # ================================================
        # STRENGTH
        # ================================================

        if score >= STRONG_SIGNAL_SCORE:

            strength = "🚀 STRONG BUY"

        else:

            strength = "🔥 BUY"


        # ================================================
        # SAVE COOLDOWN
        # ================================================

        with STATE_LOCK:

            BINANCE_LAST_SIGNAL[
                symbol
            ] = now


        # ================================================
        # ALERT
        # ================================================

        return (

            f"{strength}\n"

            f"🟡 <b>BINANCE SPOT "
            f"5M + 15M REAL BREAKOUT</b>\n\n"

            f"🪙 <b>{symbol}</b>\n"

            f"💰 Price: "
            f"{price:g}\n"

            f"📈 5M Momentum: "
            f"{momentum:+.2f}%\n"

            f"📊 5M Volume: "
            f"{momentum_vr:.2f}x\n"

            f"🟢 Buy pressure: "
            f"{buy_pressure:.1f}%\n\n"

            f"🏔 <b>600 × 15M "
            f"Resistance</b>\n"

            f"Resistance: "
            f"{resistance_level:g}\n"

            f"🚀 Breakout: "
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

            f"⏰ Signal: "
            f"{utc_text()}\n\n"

            f"⚠️ ALERT ONLY\n"

            f"NO AUTOMATIC ORDER"
        )


# ============================================================
# BINANCE SCAN LOOP
# ============================================================

def binance_scan_loop():

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
                            "Analysis error:",
                            repr(e)
                        )


            print(

                "BINANCE STATUS | "

                f"Symbols: "
                f"{len(symbols)} | "

                f"Checked: "
                f"{checked} | "

                f"15M History: "
                f"{status['history']} | "

                f"24H Volume: "
                f"{status['24h']} | "

                f"5M Momentum: "
                f"{status['momentum']} | "

                f"5M Volume: "
                f"{status['volume']} | "

                f"Buy Pressure: "
                f"{status['buy']} | "

                f"Spread: "
                f"{status['spread']} | "

                f"600x15M Resistance: "
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
                "Binance scan error:",
                repr(e)
            )


        time.sleep(
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
        "MOMENTUM TIMEFRAME:"
    )

    print(
        "  5M CLOSED CANDLE"
    )

    print()

    print(
        "BREAKOUT TIMEFRAME:"
    )

    print(
        "  15M CLOSED CANDLE"
    )

    print()

    print(
        "RESISTANCE:"
    )

    print(
        "  EXACTLY 600 PREVIOUS "
        "CLOSED 15M CANDLES"
    )

    print(
        "  REST = 500 + 100"
    )

    print()

    print(
        "BREAKOUT:"
    )

    print(
        "  >= +1.00% above resistance"
    )

    print(
        "  Breakout volume >= 1.5x"
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

    print()

    print(
        "5M VOLUME:"
    )

    print(
        "  >= 1.20x previous 20 candles"
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
        "  24 HOURS / SYMBOL"
    )

    print()

    print(
        "REMOVED:"
    )

    print(
        "  ❌ Solana Meme Scanner"
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
    # Telegram startup test
    # --------------------------------------------------------

    telegram_startup_test()


    # --------------------------------------------------------
    # Binance Spot symbols
    # --------------------------------------------------------

    symbols = load_binance_symbols()


    if not symbols:

        print(
            "ERROR: No Binance Spot symbols loaded."
        )

        return


    # --------------------------------------------------------
    # Load 15M + 5M history
    # --------------------------------------------------------

    load_binance_histories(
        symbols
    )


    # --------------------------------------------------------
    # Start WebSocket workers
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
        "🟢 SOLANA: REMOVED"
    )

    print(
        "🟢 BINANCE SQUARE: REMOVED"
    )

    print(
        "🟢 5M MOMENTUM: ACTIVE"
    )

    print(
        "🟢 15M RESISTANCE: 600 CLOSED CANDLES"
    )

    print(
        "🟢 HISTORY: 500 + 100"
    )

    print(
        "🟢 BREAKOUT: +1%"
    )

    print(
        "🟢 ALERT ONLY"
    )


    # --------------------------------------------------------
    # Keep alive
    # --------------------------------------------------------

    while not STOP_EVENT.is_set():

        time.sleep(
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
