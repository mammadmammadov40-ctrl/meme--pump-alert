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
# PREVIOUS 1440 CLOSED 5M CANDLES REAL BREAKOUT
#
# RESISTANCE = HIGHEST HIGH OF PREVIOUS 1440 CLOSED CANDLES
#
# CURRENT CLOSED CANDLE MUST CLOSE >= +1% ABOVE RESISTANCE
#
# ALERT ONLY
# NO AUTOMATIC ORDER
# ============================================================


# ============================================================
# ENVIRONMENT
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
CMC_API_KEY = os.getenv("CMC_API_KEY", "")


# ============================================================
# GENERAL
# ============================================================

UA = "BinanceSpot1440BreakoutBot/11.0"

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

INTERVAL = "5m"

# ------------------------------------------------------------
# IMPORTANT
#
# We need:
#
# 1440 PREVIOUS CLOSED candles
# +
# 1 CURRENT CLOSED candle
#
# TOTAL = 1441 CLOSED candles
#
# Binance REST maximum = 1000
#
# Therefore:
#
# 1000 + 441 = 1441
# ------------------------------------------------------------

HISTORY_LIMIT = 1441

RESISTANCE_LOOKBACK = 1440

KLINE_BATCH_SIZE = 1000
KLINE_SECOND_BATCH = 441

AVERAGE_VOLUME_CANDLES = 20


# ============================================================
# BINANCE CONDITIONS
# ============================================================

# Current closed 5M candle minimum momentum
MIN_PRICE_CHANGE = 1.0

# 24H quote volume
MIN_24H_QUOTE_VOLUME = 1_000_000


# ============================================================
# SPREAD
# ============================================================

MAX_SPREAD_PERCENT = 0.20

BOOK_CACHE_MAX_AGE = 10


# ============================================================
# BUY PRESSURE
# ============================================================

MIN_BUY_PRESSURE = 55.0


# ============================================================
# VOLUME
# ============================================================

VOLUME_MIN_RATIO = 1.2


# ============================================================
# REAL BREAKOUT
# ============================================================

# Current CLOSED candle must close at least +1%
# above highest HIGH of previous 1440 CLOSED candles.

MIN_BREAKOUT_PERCENT = 1.0

# Breakout candle volume >= 1.5x previous average
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

WS_CHUNK_SIZE = 50

RECONNECT_SECONDS = 5

STATUS_INTERVAL = 60

SIGNAL_COOLDOWN_SECONDS = 24 * 60 * 60

REST_BOOK_TIMEOUT = 5

REST_BOOK_MIN_INTERVAL = 1.0

BINANCE_MAX_WORKERS = 12


# ============================================================
# STATE
# ============================================================

BINANCE_SYMBOLS = []

BINANCE_HISTORY = {}

BINANCE_LIVE = {}

BINANCE_BOOKS = {}

BINANCE_LAST_SIGNAL = {}

BINANCE_LAST_BOOK_REST = {}

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


def safe_float(v, default=0.0):

    try:
        return float(v)

    except Exception:
        return default


def pct_change(old, new):

    if old <= 0:
        return 0.0

    return (
        (new / old - 1.0)
        * 100.0
    )


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
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )


    payload = {

        "chat_id":
        TELEGRAM_CHAT_ID,

        "text":
        text,

        "disable_web_page_preview":
        True,

        "parse_mode":
        "HTML",
    }


    try:

        r = SESSION.post(
            url,
            json=payload,
            timeout=15
        )


        print(
            f"TELEGRAM HTTP: {r.status_code}"
        )


        if r.ok:

            print(
                "TELEGRAM OK"
            )

            return True


        print(
            "TELEGRAM ERROR BODY:",
            r.text[:1000]
        )


    except Exception as e:

        print(
            "TELEGRAM EXCEPTION:",
            repr(e)
        )


    return False


# ============================================================
# TELEGRAM WORKER
# ============================================================

def telegram_worker():

    while not STOP_EVENT.is_set():

        try:

            msg = TELEGRAM_QUEUE.get(
                timeout=1
            )

        except Empty:

            continue


        try:

            telegram_send_now(
                msg
            )

        finally:

            TELEGRAM_QUEUE.task_done()


# ============================================================
# STARTUP TEST
# ============================================================

def telegram_startup_test():

    telegram_send_now(

        "🟢 <b>BINANCE SPOT BOT TEST</b>\n\n"

        "Binance Spot 5M Momentum + "
        "1440 CLOSED candle Real Breakout aktivdir.\n\n"

        "📊 Resistance: "
        "previous 1440 CLOSED 5M candles\n"

        "🚀 Breakout: >= +1%\n"

        "📈 Breakout volume: >= 1.5x\n"

        "🕯 Breakout candle: CLOSED\n\n"

        "⚠️ ALERT ONLY\n"
        "NO AUTOMATIC ORDER"
    )


# ============================================================
# SEND ALERT
# ============================================================

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


    r = SESSION.get(
        url,
        params=params,
        timeout=timeout
    )


    r.raise_for_status()


    return r.json()


# ============================================================
# BINANCE SYMBOLS
# ============================================================

def load_binance_symbols():

    try:

        info = binance_get(
            "/api/v3/exchangeInfo",
            timeout=20
        )


        allowed = set()


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

                    "start":
                    1,

                    "limit":
                    2000,

                    "convert":
                    "USD",
                }


                cr = SESSION.get(

                    cmc_url,

                    headers=headers,

                    params=params,

                    timeout=20
                )


                if cr.ok:

                    for item in cr.json().get(
                        "data",
                        []
                    ):

                        sym = str(
                            item.get(
                                "symbol",
                                ""
                            )
                        ).upper()


                        rank = int(
                            item.get(
                                "cmc_rank"
                            )
                            or 999999
                        )


                        if 1 <= rank <= 2000:

                            allowed.add(
                                sym
                            )


            except Exception as e:

                print(
                    "CMC error:",
                    e
                )


        symbols = []


        for s in info.get(
            "symbols",
            []
        ):

            # ------------------------------------------------
            # SPOT ONLY
            # ------------------------------------------------

            if s.get(
                "status"
            ) != "TRADING":

                continue


            if s.get(
                "quoteAsset"
            ) != "USDT":

                continue


            if not s.get(
                "isSpotTradingAllowed",
                False
            ):

                continue


            base = str(
                s.get(
                    "baseAsset",
                    ""
                )
            ).upper()


            # ------------------------------------------------
            # REMOVE LEVERAGED TOKENS
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
            # CMC TOP 2000 FILTER
            # ------------------------------------------------

            if (
                allowed
                and base not in allowed
            ):

                continue


            symbols.append(
                s["symbol"]
            )


        with STATE_LOCK:

            BINANCE_SYMBOLS[:] = symbols


        print(
            "BINANCE SPOT USDT SYMBOLS:",
            len(symbols)
        )


        return symbols


    except Exception as e:

        print(
            "Binance symbol load error:",
            e
        )

        return []


# ============================================================
# BINANCE KLINES
# ============================================================

def get_klines(
    symbol,
    limit=1000,
    end_time=None
):

    params = {

        "symbol":
        symbol,

        "interval":
        INTERVAL,

        "limit":
        min(
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


# ============================================================
# KLINE CONVERSION
# ============================================================

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
# LOAD 1441 CLOSED CANDLES
#
# 1000 + 441
#
# 1440 previous candles
# +
# 1 current/latest closed candle
# ============================================================

def load_1441_closed_klines(symbol):

    try:

        now_ms = int(
            time.time() * 1000
        )


        # ----------------------------------------------------
        # Find the end of the LAST CLOSED 5M candle.
        #
        # This prevents the current unfinished candle
        # from consuming one of the 1000 slots.
        # ----------------------------------------------------

        interval_ms = 5 * 60 * 1000

        current_open = (
            now_ms
            // interval_ms
        ) * interval_ms


        last_closed_end = (
            current_open - 1
        )


        # ----------------------------------------------------
        # BATCH 1
        #
        # Exactly latest 1000 CLOSED candles
        # ----------------------------------------------------

        batch1 = get_klines(

            symbol,

            KLINE_BATCH_SIZE,

            last_closed_end
        )


        if not batch1:

            return None


        candles1 = []


        for k in batch1:

            close_time = int(
                k[6]
            )


            if close_time <= last_closed_end:

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
        # BATCH 2
        #
        # 441 candles BEFORE batch 1
        # ----------------------------------------------------

        batch2 = get_klines(

            symbol,

            KLINE_SECOND_BATCH,

            oldest_open_time - 1
        )


        if not batch2:

            return None


        candles2 = []


        for k in batch2:

            close_time = int(
                k[6]
            )


            if close_time < oldest_open_time:

                candles2.append(
                    kline_to_candle(k)
                )


        if not candles2:

            return None


        # ----------------------------------------------------
        # Combine
        # ----------------------------------------------------

        combined = (
            candles2
            +
            candles1
        )


        combined.sort(
            key=lambda x:
            x["open_time"]
        )


        # ----------------------------------------------------
        # Remove duplicates
        # ----------------------------------------------------

        unique = {}


        for c in combined:

            unique[
                c["open_time"]
            ] = c


        combined = list(
            unique.values()
        )


        combined.sort(
            key=lambda x:
            x["open_time"]
        )


        # ----------------------------------------------------
        # Keep EXACTLY 1441 CLOSED candles
        # ----------------------------------------------------

        combined = combined[
            -HISTORY_LIMIT:
        ]


        if len(combined) < HISTORY_LIMIT:

            print(

                f"1441 LOAD INCOMPLETE "
                f"{symbol}: "
                f"{len(combined)}/"
                f"{HISTORY_LIMIT}"
            )

            return None


        return deque(

            combined,

            maxlen=HISTORY_LIMIT
        )


    except Exception as e:

        print(

            f"1441 history error "
            f"{symbol}: {e}"
        )

        return None


# ============================================================
# LOAD ALL HISTORIES
# ============================================================

def load_binance_histories(symbols):

    print(
        "=" * 60
    )


    print(
        "Loading 1441 CLOSED 5M candles"
    )


    print(
        "1440 previous resistance candles"
    )


    print(
        "1 latest CLOSED candle"
    )


    print(
        "REST: 1000 + 441"
    )


    print(
        "=" * 60
    )


    loaded = 0

    success = 0


    def one(symbol):

        candles = (
            load_1441_closed_klines(
                symbol
            )
        )

        return symbol, candles


    with ThreadPoolExecutor(
        max_workers=BINANCE_MAX_WORKERS
    ) as ex:

        futures = [

            ex.submit(
                one,
                symbol
            )

            for symbol in symbols
        ]


        for f in as_completed(
            futures
        ):

            loaded += 1


            try:

                symbol, candles = (
                    f.result()
                )


                if (
                    candles
                    and len(candles)
                    == HISTORY_LIMIT
                ):

                    with STATE_LOCK:

                        BINANCE_HISTORY[
                            symbol
                        ] = candles


                    success += 1


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
                    f"Ready: "
                    f"{success}"
                )


    print(
        "=" * 60
    )


    print(
        "1441 CLOSED HISTORY READY:",
        len(BINANCE_HISTORY)
    )


    print(
        "=" * 60
    )


# ============================================================
# 24H TICKER
# ============================================================

def get_24h(symbol):

    return binance_get(

        "/api/v3/ticker/24hr",

        {
            "symbol":
            symbol
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
            "symbol":
            symbol
        },

        timeout=REST_BOOK_TIMEOUT
    )


# ============================================================
# UPDATE BOOK
# ============================================================

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


# ============================================================
# CACHED BOOK
# ============================================================

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

        with STATE_LOCK:

            last = BINANCE_LAST_BOOK_REST.get(
                symbol,
                0
            )


        if (
            now_ts()
            - last
            >= REST_BOOK_MIN_INTERVAL
        ):

            with STATE_LOCK:

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
                ),
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


        streams.append(
            f"{s}@kline_5m"
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


                    # ----------------------------------------
                    # BOOK
                    # ----------------------------------------

                    if event == "bookTicker":

                        update_book(

                            symbol,

                            data.get("b"),

                            data.get("a"),

                            data.get("B"),

                            data.get("A"),
                        )


                    # ----------------------------------------
                    # 24H TICKER
                    # ----------------------------------------

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


                    # ----------------------------------------
                    # 5M KLINE
                    # ----------------------------------------

                    elif event == "kline":

                        k = data.get(
                            "k"
                        ) or {}


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

                            BINANCE_LIVE.setdefault(
                                symbol,
                                {}
                            )


                            BINANCE_LIVE[
                                symbol
                            ][
                                "candle"
                            ] = candle


                            # --------------------------------
                            # CLOSED CANDLE
                            # --------------------------------

                            if candle["closed"]:

                                hist = (
                                    BINANCE_HISTORY.get(
                                        symbol
                                    )
                                )


                                if hist is None:

                                    hist = deque(
                                        maxlen=HISTORY_LIMIT
                                    )


                                    BINANCE_HISTORY[
                                        symbol
                                    ] = hist


                                # --------------------------------
                                # Add only if new candle
                                # --------------------------------

                                if (

                                    not hist

                                    or

                                    hist[-1][
                                        "open_time"
                                    ]

                                    !=

                                    candle[
                                        "open_time"
                                    ]

                                ):

                                    hist.append(
                                        candle
                                    )


                                # --------------------------------
                                # Keep 1441 candles:
                                #
                                # 1440 previous
                                # +
                                # latest closed
                                # --------------------------------

                                while len(hist) > HISTORY_LIMIT:

                                    hist.popleft()


                                BINANCE_LIVE[
                                    symbol
                                ].pop(
                                    "candle",
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
                msg
            ):

                print(
                    "Binance WS closed:",
                    code,
                    msg
                )


                print(

                    f"Binance WS "
                    f"reconnecting in "
                    f"{RECONNECT_SECONDS}s..."
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
    candle
):

    if not history or not candle:

        return 0.0


    data = list(
        history
    )


    # --------------------------------------------------------
    # IMPORTANT
    #
    # Current breakout candle MUST NOT be part of
    # the average.
    #
    # Therefore take candles immediately BEFORE current.
    # --------------------------------------------------------

    current_open_time = candle[
        "open_time"
    ]


    previous_candles = [

        c for c in data

        if c["open_time"]
        < current_open_time
    ]


    if len(previous_candles) < (
        AVERAGE_VOLUME_CANDLES
    ):

        return 0.0


    previous = previous_candles[
        -AVERAGE_VOLUME_CANDLES:
    ]


    values = [

        c["quote_volume"]

        for c in previous

        if c["quote_volume"] > 0
    ]


    if not values:

        return 0.0


    avg = (
        sum(values)
        / len(values)
    )


    if avg <= 0:

        return 0.0


    return (

        candle["quote_volume"]
        / avg

    )


# ============================================================
# BUY PRESSURE
# ============================================================

def buy_pressure_estimate(
    candle
):

    if not candle:

        return 0.0


    total = candle.get(
        "quote_volume",
        0.0
    )


    buy = candle.get(
        "taker_buy_quote",
        0.0
    )


    if (
        total > 0
        and buy > 0
    ):

        return (

            buy
            / total
        ) * 100.0


    # --------------------------------------------------------
    # Fallback
    # --------------------------------------------------------

    rng = (

        candle["high"]
        -
        candle["low"]
    )


    if rng <= 0:

        return 50.0


    return (

        (
            candle["close"]
            -
            candle["low"]
        )
        / rng

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
# RESISTANCE
#
# IMPORTANT:
#
# We EXCLUDE current breakout candle.
#
# Resistance = highest HIGH of previous 1440
# CLOSED candles.
# ============================================================

def find_resistance(
    history,
    current
):

    if (
        not history
        or not current
    ):

        return None


    data = list(
        history
    )


    current_open_time = current[
        "open_time"
    ]


    previous = [

        c for c in data

        if c["open_time"]
        < current_open_time
    ]


    if len(previous) < RESISTANCE_LOOKBACK:

        return None


    # Exactly previous 1440 CLOSED candles

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

        "age":
        len(previous)
        -
        1
        -
        previous.index(
            highest
        ),

        "open_time":
        highest["open_time"],
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


    # --------------------------------------------------------
    # Distance from resistance
    # --------------------------------------------------------

    breakout_pct = pct_change(

        level,

        current["close"]
    )


    # --------------------------------------------------------
    # Current candle volume / previous 20 average
    # --------------------------------------------------------

    vr = volume_ratio(

        history,

        current
    )


    # --------------------------------------------------------
    # Close position
    # --------------------------------------------------------

    rng = (

        current["high"]
        -
        current["low"]
    )


    if rng > 0:

        close_position = (

            (
                current["close"]
                -
                current["low"]
            )
            / rng

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


    if rng > 0:

        upper_wick_pct = (

            upper_wick
            / rng

        ) * 100.0

    else:

        upper_wick_pct = 100.0


    # --------------------------------------------------------
    # HARD BREAKOUT VALIDATION
    # --------------------------------------------------------

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
            / 100.0
        )
    )


# ============================================================
# BINANCE ANALYSIS
# ============================================================

def analyze_binance(
    symbol,
    status
):

    try:

        # ----------------------------------------------------
        # HISTORY
        # ----------------------------------------------------

        with STATE_LOCK:

            history = (
                BINANCE_HISTORY.get(
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
            not history
            or len(history)
            < HISTORY_LIMIT
        ):

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


        if (
            qvol
            < MIN_24H_QUOTE_VOLUME
        ):

            return None


        status["24h"] += 1


        # ----------------------------------------------------
        # CURRENT CLOSED CANDLE
        # ----------------------------------------------------

        candle = None


        live_candle = live.get(
            "candle"
        )


        if (
            live_candle
            and
            live_candle.get(
                "closed"
            )
        ):

            candle = live_candle

        else:

            candle = list(
                history
            )[-1]


        if not candle:

            return None


        # ----------------------------------------------------
        # 5M MOMENTUM
        # ----------------------------------------------------

        momentum = momentum_5m(
            candle
        )


        if (
            momentum
            < MIN_PRICE_CHANGE
        ):

            return None


        status["momentum"] += 1


        # ----------------------------------------------------
        # VOLUME
        # ----------------------------------------------------

        vr = volume_ratio(

            history,

            candle
        )


        if (
            vr
            < VOLUME_MIN_RATIO
        ):

            return None


        status["volume"] += 1


        # ----------------------------------------------------
        # BUY PRESSURE
        # ----------------------------------------------------

        buy_pressure = (
            buy_pressure_estimate(
                candle
            )
        )


        if (
            buy_pressure
            < MIN_BUY_PRESSURE
        ):

            return None


        status["buy"] += 1


        # ----------------------------------------------------
        # SPREAD
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


        if (
            spread
            > MAX_SPREAD_PERCENT
        ):

            return None


        status["spread"] += 1


        # ----------------------------------------------------
        # PREVIOUS 1440 RESISTANCE
        #
        # CURRENT CANDLE IS EXCLUDED
        # ----------------------------------------------------

        resistance = find_resistance(

            history,

            candle
        )


        if not resistance:

            return None


        status["resistance"] += 1


        # ----------------------------------------------------
        # BREAKOUT
        # ----------------------------------------------------

        br = breakout_data(

            history,

            candle,

            resistance
        )


        if not br:

            return None


        # ----------------------------------------------------
        # HARD +1% BREAKOUT
        # ----------------------------------------------------

        if (
            br["breakout_pct"]
            < MIN_BREAKOUT_PERCENT
        ):

            return None


        status["breakout"] += 1


        # ----------------------------------------------------
        # BREAKOUT VOLUME
        # ----------------------------------------------------

        if (
            br["volume_ratio"]
            < MIN_BREAKOUT_VOLUME_RATIO
        ):

            return None


        status[
            "breakout_volume"
        ] += 1


        # ----------------------------------------------------
        # CANDLE QUALITY
        # ----------------------------------------------------

        if (
            br["close_position"]
            < MIN_CLOSE_POSITION
        ):

            return None


        if (
            br["upper_wick_pct"]
            > MAX_UPPER_WICK_PERCENT
        ):

            return None


        status["candle"] += 1


        # ----------------------------------------------------
        # SCORE
        # ----------------------------------------------------

        score = 0


        # Momentum
        if momentum >= 1:

            score += 15


        if momentum >= 2:

            score += 10


        if momentum >= 3:

            score += 10


        # Volume
        if vr >= 1.2:

            score += 10


        if vr >= 1.5:

            score += 10


        # Buy pressure
        if buy_pressure >= 55:

            score += 10


        if buy_pressure >= 70:

            score += 5


        # Breakout
        if br["breakout_pct"] >= 1.0:

            score += 10


        if br["volume_ratio"] >= 1.5:

            score += 10


        # Candle
        if br["close_position"] >= 70:

            score += 5


        # Spread
        if spread <= 0.10:

            score += 5


        if score < MIN_SIGNAL_SCORE:

            return None


        status["score"] += 1


        # ----------------------------------------------------
        # COOLDOWN
        # ----------------------------------------------------

        key = symbol

        now = now_ts()


        with STATE_LOCK:

            last_signal = (

                BINANCE_LAST_SIGNAL.get(

                    key,

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


        with STATE_LOCK:

            BINANCE_LAST_SIGNAL[
                key
            ] = now


        # ----------------------------------------------------
        # ALERT
        # ----------------------------------------------------

        price = candle[
            "close"
        ]


        resistance_level = (
            resistance[
                "level"
            ]
        )


        strength = (

            "🚀 STRONG BUY"

            if score >= STRONG_SIGNAL_SCORE

            else

            "🔥 BUY"
        )


        return (

            f"{strength}\n"

            f"🟡 <b>BINANCE SPOT "
            f"5M REAL BREAKOUT</b>\n\n"

            f"🪙 <b>{symbol}</b>\n"

            f"💰 Price: "
            f"{price:g}\n"

            f"📈 5M Momentum: "
            f"{momentum:+.2f}%\n"

            f"📊 Volume ratio: "
            f"{vr:.2f}x\n"

            f"🟢 Buy pressure: "
            f"{buy_pressure:.1f}%\n"

            f"🏔 1440 Resistance: "
            f"{resistance_level:g}\n"

            f"🚀 Breakout: "
            f"{br['breakout_pct']:+.2f}%\n"

            f"📊 Breakout volume: "
            f"{br['volume_ratio']:.2f}x\n"

            f"🕯 Close position: "
            f"{br['close_position']:.1f}%\n"

            f"📐 Spread: "
            f"{spread:.3f}%\n"

            f"⭐ Score: "
            f"{score}\n\n"

            f"🛑 Stop: "
            f"~{level_stop(resistance_level):g}\n"

            f"🎯 TP1: +{TP1}%\n"

            f"🎯 TP2: +{TP2}%\n"

            f"🎯 TP3: +{TP3}%\n\n"

            f"🕐 Cooldown: 24H\n\n"

            f"⚠️ ALERT ONLY\n"

            f"NO AUTOMATIC ORDER"
        )


# ============================================================
# BINANCE SCAN
# ============================================================

def binance_scan_loop():

    while not STOP_EVENT.is_set():

        try:

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

                max_workers=
                BINANCE_MAX_WORKERS

            ) as ex:

                futures = {

                    ex.submit(

                        analyze_binance,

                        symbol,

                        status

                    ):

                    symbol

                    for symbol in symbols
                }


                for f in as_completed(
                    futures
                ):

                    checked += 1


                    try:

                        result = f.result()


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

                f"1441 History: "
                f"{status['history']} | "

                f"24H Volume: "
                f"{status['24h']} | "

                f"Momentum: "
                f"{status['momentum']} | "

                f"Volume: "
                f"{status['volume']} | "

                f"Buy Pressure: "
                f"{status['buy']} | "

                f"Spread: "
                f"{status['spread']} | "

                f"1440 Resistance: "
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


        time.sleep(20)


# ============================================================
# CONFIG
# ============================================================

def print_config():

    print(
        "=" * 60
    )


    print(
        "BINANCE SPOT ONLY"
    )


    print(
        "=" * 60
    )


    print()

    print(
        "TIMEFRAME:"
    )

    print(
        "  5M"
    )


    print()

    print(
        "HISTORY:"
    )

    print(
        "  1441 CLOSED 5M candles"
    )

    print(
        "  1000 + 441 REST"
    )

    print(
        "  1440 = resistance history"
    )

    print(
        "  1 = current breakout candle"
    )


    print()

    print(
        "RESISTANCE:"
    )

    print(
        "  Highest HIGH of previous "
        "1440 CLOSED candles"
    )


    print()

    print(
        "BREAKOUT:"
    )

    print(
        "  CLOSED candle >= +1.00% "
        "above resistance"
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
        "MOMENTUM:"
    )

    print(
        "  5M momentum >= +1%"
    )


    print()

    print(
        "VOLUME:"
    )

    print(
        "  5M volume >= 1.2x average"
    )

    print(
        "  24H quote volume >= $1M"
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
        "ACTIVE:"
    )

    print(
        "  🟢 Binance Spot"
    )

    print(
        "  🟢 Binance USDT"
    )

    print(
        "  🟢 5M Momentum"
    )

    print(
        "  🟢 1440 Candle Breakout"
    )


    print()

    print(
        "REMOVED:"
    )

    print(
        "  ❌ Solana Meme"
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


    print(
        "=" * 60
    )


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
    # Binance Spot symbols
    # --------------------------------------------------------

    symbols = (
        load_binance_symbols()
    )


    if not symbols:

        print(
            "ERROR: No Binance Spot symbols loaded."
        )

        return


    # --------------------------------------------------------
    # Load 1441 CLOSED candles
    #
    # 1000 + 441
    # --------------------------------------------------------

    load_binance_histories(
        symbols
    )


    # --------------------------------------------------------
    # WebSocket
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
    # Binance scanner
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
        "🟢 HISTORY: 1441 CLOSED"
    )


    print(
        "🟢 RESISTANCE: PREVIOUS 1440"
    )


    print(
        "🟢 REST: 1000 + 441"
    )


    print(
        "🟢 BREAKOUT: +1%"
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
