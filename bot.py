import os
import time
import json
import threading

from datetime import datetime
from zoneinfo import ZoneInfo

from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue, Empty

import requests
import websocket


# ============================================================
# BINANCE SPOT 5M MOMENTUM + 1440 CANDLE REAL BREAKOUT
#
# ALERT ONLY
# NO AUTOMATIC ORDER
# ============================================================


# ============================================================
# BINANCE
# ============================================================

BINANCE_REST = "https://api.binance.com"
BINANCE_WS = "wss://stream.binance.com:443"

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
)

INTERVAL = "5m"


# ============================================================
# AZERBAIJAN TIME
# ============================================================

AZ_TZ = ZoneInfo("Asia/Baku")

TRADING_START_HOUR = 7
TRADING_END_HOUR = 1


# ============================================================
# HISTORY
# ============================================================

HISTORY_LIMIT = 1440

AVERAGE_VOLUME_CANDLES = 20


# ============================================================
# 24H SPOT VOLUME
# ============================================================

MIN_24H_QUOTE_VOLUME = 1_000_000


# ============================================================
# 5M MOMENTUM
# ============================================================

# Current 5M candle must be at least +1%.

MIN_PRICE_CHANGE = 1.0

# NO MAXIMUM LIMIT.
#
# +1%
# +2%
# +5%
# +8%
# +15%
# etc.
#
# are NOT rejected because of momentum percentage.

MAX_CURRENT_5M_PRICE = None


# ============================================================
# BUY PRESSURE
# ============================================================

MIN_BUY_PRESSURE = 55.0


# ============================================================
# REAL BREAKOUT
# ============================================================

# Resistance =
# HIGHEST HIGH of previous 1440 CLOSED 5M candles.
#
# Current live candle is NOT included.

BREAKOUT_LOOKBACK = 1440

MIN_BREAKOUT_PERCENT = 1.0

MIN_BREAKOUT_VOLUME_RATIO = 1.5


# ============================================================
# CANDLE QUALITY
# ============================================================

MIN_CLOSE_POSITION = 70.0

MAX_UPPER_WICK_PERCENT = 30.0


# ============================================================
# SCORE
# ============================================================

MIN_SIGNAL_SCORE = 60

STRONG_SIGNAL_SCORE = 75


# ============================================================
# SPREAD
# ============================================================

MAX_SPREAD_PERCENT = 0.20

BOOK_CACHE_MAX_AGE = 10

REST_BOOK_TIMEOUT = 5

REST_BOOK_MIN_INTERVAL = 1.0


# ============================================================
# SIGNAL COOLDOWN
# ============================================================

# SAME COIN:
# minimum 24 hours between signals.

SIGNAL_COOLDOWN_SECONDS = 24 * 60 * 60


# ============================================================
# WEBSOCKET
# ============================================================

WS_CHUNK_SIZE = 50

RECONNECT_SECONDS = 5


# ============================================================
# STATUS
# ============================================================

STATUS_INTERVAL = 60


# ============================================================
# TP / STOP
# ============================================================

STOP_BELOW_RESISTANCE_PERCENT = 0.50

TP1_PERCENT = 3.0

TP2_PERCENT = 5.0

TP3_PERCENT = 8.0


# ============================================================
# GLOBAL
# ============================================================

session = requests.Session()

session.headers.update({

    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),

    "Accept": "application/json",
})


symbols = []

volume_24h = {}

candle_history = {}

live_candles = {}

book_data = {}

last_signal_time = {}

running = True

data_lock = threading.RLock()

signal_lock = threading.Lock()

rest_book_last_request = {}

book_updates = 0

last_book_update = 0


# ============================================================
# WEBSOCKET CONNECTION REGISTRY
# ============================================================

ws_connections = []

ws_connections_lock = threading.Lock()


# ============================================================
# TELEGRAM QUEUE
# ============================================================

telegram_queue = Queue(
    maxsize=500
)


# ============================================================
# STATS
# ============================================================

stats_lock = threading.Lock()

stats = {

    "checked": 0,

    "momentum": 0,

    "volume": 0,

    "buy_pressure": 0,

    "breakout": 0,

    "breakout_volume": 0,

    "candle_quality": 0,

    "spread": 0,

    "spread_missing": 0,

    "spread_rejected": 0,

    "score": 0,

    "signals": 0,

    "telegram_ok": 0,

    "telegram_failed": 0,
}


def reset_stats():

    with stats_lock:

        for key in stats:

            stats[key] = 0


def inc_stat(name):

    with stats_lock:

        if name in stats:

            stats[name] += 1


def get_stats():

    with stats_lock:

        return dict(stats)


# ============================================================
# TRADING HOURS
# ============================================================

def is_trading_time():

    """
    Azərbaycan vaxtı:

    07:00 - 01:00 -> AKTİV

    01:00 - 07:00 -> SLEEP
    """

    now = datetime.now(AZ_TZ)

    hour = now.hour

    # 01:00 <= hour < 07:00

    if 1 <= hour < 7:

        return False

    return True


def get_azerbaijan_time():

    return datetime.now(
        AZ_TZ
    )


def wait_until_trading_time():

    while running:

        if is_trading_time():

            return

        now = get_azerbaijan_time()

        print(
            "🌙 BOT SLEEP MODE | "
            f"Azərbaycan vaxtı: "
            f"{now:%Y-%m-%d %H:%M:%S}"
        )

        time.sleep(30)


# ============================================================
# WEBSOCKET REGISTRY
# ============================================================

def register_websocket(ws):

    with ws_connections_lock:

        if ws not in ws_connections:

            ws_connections.append(ws)


def unregister_websocket(ws):

    with ws_connections_lock:

        if ws in ws_connections:

            ws_connections.remove(ws)


def close_all_websockets():

    with ws_connections_lock:

        connections = list(
            ws_connections
        )

        ws_connections.clear()

    for ws in connections:

        try:

            ws.close()

        except Exception:

            pass


# ============================================================
# SLEEP / ACTIVE WORKER
# ============================================================

def trading_hours_worker():

    was_active = is_trading_time()

    while running:

        active = is_trading_time()

        # ====================================================
        # 01:00 -> SLEEP
        # ====================================================

        if not active and was_active:

            print(
                "\n"
                "==================================================\n"
                "🌙 BOT SLEEP MODE\n"
                "🌙 Azərbaycan vaxtı ilə 01:00\n"
                "🌙 WebSocket-lər bağlanır\n"
                "==================================================\n"
            )

            close_all_websockets()

            with data_lock:

                live_candles.clear()

                book_data.clear()

        # ====================================================
        # 07:00 -> ACTIVE
        # ====================================================

        if active and not was_active:

            print(
                "\n"
                "==================================================\n"
                "🌅 BOT AKTİVDİR\n"
                "🌅 Azərbaycan vaxtı ilə 07:00\n"
                "🌅 WebSocket-lər yenidən qoşulur\n"
                "==================================================\n"
            )

        was_active = active

        time.sleep(10)


# ============================================================
# HELPERS
# ============================================================

def safe_float(
    value,
    default=0.0
):

    try:

        if value is None:

            return default

        return float(value)

    except Exception:

        return default


def round_price(price):

    if price >= 1000:

        return round(
            price,
            2
        )

    if price >= 1:

        return round(
            price,
            4
        )

    if price >= 0.01:

        return round(
            price,
            6
        )

    return round(
        price,
        8
    )


def percent_change(
    open_price,
    current_price
):

    if open_price <= 0:

        return 0.0

    return (

        (
            current_price
            - open_price
        )
        / open_price

    ) * 100.0


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_now(message):

    if not TELEGRAM_BOT_TOKEN:

        print(
            "ERROR: TELEGRAM_BOT_TOKEN yoxdur."
        )

        inc_stat(
            "telegram_failed"
        )

        return False


    if not TELEGRAM_CHAT_ID:

        print(
            "ERROR: TELEGRAM_CHAT_ID yoxdur."
        )

        inc_stat(
            "telegram_failed"
        )

        return False


    url = (

        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}"
        "/sendMessage"
    )


    payload = {

        "chat_id":
            TELEGRAM_CHAT_ID,

        "text":
            message,

        "parse_mode":
            "HTML",

        "disable_web_page_preview":
            True,
    }


    try:

        response = session.post(

            url,

            json=payload,

            timeout=10,
        )


        if response.status_code == 200:

            inc_stat(
                "telegram_ok"
            )

            return True


        print(

            "Telegram ERROR:",

            response.status_code,

            response.text[:500],
        )

        inc_stat(
            "telegram_failed"
        )


    except Exception as e:

        print(
            "Telegram exception:",
            e
        )

        inc_stat(
            "telegram_failed"
        )


    return False


def queue_telegram(message):

    try:

        telegram_queue.put_nowait(
            message
        )

    except Exception:

        print(
            "Telegram queue full - "
            "message dropped."
        )


def telegram_worker():

    while running:

        try:

            message = (
                telegram_queue.get(
                    timeout=1
                )
            )

        except Empty:

            continue


        try:

            send_telegram_now(
                message
            )

        finally:

            telegram_queue.task_done()


def telegram_startup_test():

    queue_telegram(

        "🟢 <b>BINANCE SPOT BOT STARTED</b>\n\n"

        "⏱ 5M Momentum\n"

        "📚 1440 Closed 5M Candles\n"

        "🏔 1440 Candle Highest High Breakout\n"

        "🚀 Breakout: ≥+1%\n"

        "📊 Breakout Volume: ≥1.5x\n"

        "🟢 Buy Pressure: ≥55%\n"

        "💧 24H Volume: ≥$1M\n"

        "📖 Spread: ≤0.20%\n"

        "📈 Momentum: ≥+1%\n"

        "🕐 Signal cooldown: 24H\n\n"

        "🌙 Sleep: 01:00-07:00 AZ\n"

        "⚠️ ALERT ONLY\n"

        "No automatic order."
    )


# ============================================================
# BINANCE EXCHANGE INFO
# ============================================================

def load_exchange_info():

    print(
        "Loading Binance Spot exchange info..."
    )

    url = (

        f"{BINANCE_REST}"
        "/api/v3/exchangeInfo"
    )


    response = session.get(

        url,

        timeout=20,
    )

    response.raise_for_status()


    data = response.json()

    result = []


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


        if not item.get(
            "isSpotTradingAllowed",
            False
        ):

            continue


        base = item.get(
            "baseAsset",
            ""
        )


        # Leveraged tokens excluded

        if (

            base.endswith("UP")

            or base.endswith("DOWN")

            or base.endswith("BULL")

            or base.endswith("BEAR")

        ):

            continue


        result.append(
            item["symbol"].lower()
        )


    print(
        "Binance Spot USDT symbols:",
        len(result)
    )


    return result


# ============================================================
# 24H VOLUME
# ============================================================

def load_24h_volumes(
    all_symbols
):

    print(
        "Loading Binance 24H Spot volume..."
    )


    url = (

        f"{BINANCE_REST}"
        "/api/v3/ticker/24hr"
    )


    response = session.get(

        url,

        timeout=30,
    )

    response.raise_for_status()


    data = response.json()


    allowed = set(
        all_symbols
    )

    result = {}


    for ticker in data:

        symbol = (

            ticker.get(
                "symbol",
                ""
            ).lower()
        )


        if symbol not in allowed:

            continue


        quote_volume = safe_float(

            ticker.get(
                "quoteVolume"
            )
        )


        if (
            quote_volume
            < MIN_24H_QUOTE_VOLUME
        ):

            continue


        result[symbol] = (
            quote_volume
        )


    print(
        "After 24H volume filter:",
        len(result)
    )


    return result


# ============================================================
# LOAD 1440 CLOSED 5M CANDLES
# ============================================================

def load_symbol_history(
    symbol
):

    url = (

        f"{BINANCE_REST}"
        "/api/v3/klines"
    )


    params = {

        "symbol":
            symbol.upper(),

        "interval":
            INTERVAL,

        "limit":
            HISTORY_LIMIT,
    }


    try:

        response = session.get(

            url,

            params=params,

            timeout=20,
        )

        response.raise_for_status()


        data = response.json()


        candles = deque(

            maxlen=HISTORY_LIMIT

        )


        now_ms = int(
            time.time()
            * 1000
        )


        for k in data:

            close_time = int(
                k[6]
            )


            # Only CLOSED candles

            if close_time >= now_ms:

                continue


            candles.append({

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

                "quote_volume":
                    safe_float(k[7]),

                "trades":
                    int(k[8]),

                "taker_buy_base":
                    safe_float(k[9]),

                "taker_buy_quote":
                    safe_float(k[10]),

                "closed":
                    True,
            })


        return (
            symbol,
            candles
        )


    except Exception as e:

        print(
            f"History error "
            f"{symbol}: {e}"
        )

        return (
            symbol,
            None
        )


def load_all_histories():

    print(

        "Loading 1440 x 5M history for "
        f"{len(symbols)} symbols..."
    )


    with ThreadPoolExecutor(

        max_workers=15

    ) as executor:


        futures = [

            executor.submit(

                load_symbol_history,

                symbol

            )

            for symbol in symbols

        ]


        completed = 0


        for future in as_completed(
            futures
        ):

            symbol, candles = (

                future.result()
            )


            completed += 1


            if candles:

                with data_lock:

                    candle_history[
                        symbol
                    ] = candles


            if completed % 50 == 0:

                print(

                    "History:",

                    f"{completed}/"
                    f"{len(symbols)}"
                )


    print(

        "History ready:",

        len(candle_history)
    )


# ============================================================
# 1440 CANDLE HIGH
#
# HIGHEST HIGH OF PREVIOUS 1440 CLOSED CANDLES
# ============================================================

def find_1440_candle_high(
    symbol
):

    with data_lock:

        history = list(

            candle_history.get(

                symbol,

                []

            )
        )


    if len(history) < BREAKOUT_LOOKBACK:

        return None


    candles = history[
        -BREAKOUT_LOOKBACK:
    ]


    highest_candle = max(

        candles,

        key=lambda x:
            x["high"]

    )


    resistance = (
        highest_candle["high"]
    )


    if resistance <= 0:

        return None


    highest_index = (

        len(candles)
        - 1
        - candles.index(
            highest_candle
        )
    )


    return {

        "price":
            resistance,

        "open_time":
            highest_candle[
                "open_time"
            ],

        "age":
            highest_index,

        "candle":
            highest_candle,
    }


# ============================================================
# SCORE
# ============================================================

def momentum_score(
    price_change
):

    if price_change >= 5:

        return 25

    if price_change >= 4:

        return 20

    if price_change >= 3:

        return 15

    if price_change >= 2:

        return 10

    if price_change >= 1:

        return 5

    return 0


def volume_score(
    ratio
):

    if ratio >= 4:

        return 20

    if ratio >= 3:

        return 17

    if ratio >= 2:

        return 14

    if ratio >= 1.5:

        return 8

    if ratio >= 1.2:

        return 4

    return 0


def buy_pressure_score(
    pressure
):

    if pressure >= 65:

        return 20

    if pressure >= 60:

        return 15

    if pressure >= 55:

        return 10

    if pressure >= 50:

        return 5

    return 0


def breakout_score(
    price,
    resistance
):

    if resistance <= 0:

        return (
            0,
            0.0
        )


    breakout = (

        (
            price
            - resistance
        )
        / resistance

    ) * 100.0


    if breakout >= 2.0:

        return (
            15,
            breakout
        )


    if breakout >= 1.5:

        return (
            12,
            breakout
        )


    if breakout >= 1.0:

        return (
            10,
            breakout
        )


    return (
        0,
        breakout
    )


def breakout_volume_score(
    current_volume,
    average_volume
):

    if average_volume <= 0:

        return (
            0,
            0.0
        )


    ratio = (

        current_volume
        / average_volume
    )


    if ratio >= 4:

        return (
            10,
            ratio
        )


    if ratio >= 3:

        return (
            8,
            ratio
        )


    if ratio >= 2:

        return (
            6,
            ratio
        )


    if ratio >= 1.5:

        return (
            4,
            ratio
        )


    return (
        0,
        ratio
    )


def candle_quality(
    candle
):

    open_price = candle[
        "open"
    ]

    high = candle[
        "high"
    ]

    low = candle[
        "low"
    ]

    close = candle[
        "close"
    ]


    if high <= low:

        return (
            0,
            0.0,
            0.0
        )


    candle_range = (
        high - low
    )


    close_position = (

        (
            close - low
        )
        / candle_range

    ) * 100.0


    upper_wick = (

        high
        - max(
            open_price,
            close
        )
    )


    upper_wick_percent = (

        upper_wick
        / candle_range

    ) * 100.0


    if close <= open_price:

        return (

            0,

            close_position,

            upper_wick_percent
        )


    if (
        close_position
        < MIN_CLOSE_POSITION
    ):

        return (

            0,

            close_position,

            upper_wick_percent
        )


    if (
        upper_wick_percent
        > MAX_UPPER_WICK_PERCENT
    ):

        return (

            0,

            close_position,

            upper_wick_percent
        )


    return (

        10,

        close_position,

        upper_wick_percent
    )


# ============================================================
# BOOK
# ============================================================

def store_book(
    symbol,
    bid,
    ask
):

    global book_updates
    global last_book_update


    if bid <= 0 or ask <= 0:

        return


    now = time.time()


    with data_lock:

        book_data[symbol] = {

            "bid":
                bid,

            "ask":
                ask,

            "timestamp":
                now,
        }


        book_updates += 1

        last_book_update = now


def get_spread(
    symbol
):

    with data_lock:

        data = book_data.get(
            symbol
        )


    if data:

        age = (

            time.time()
            - data["timestamp"]
        )


        if age <= BOOK_CACHE_MAX_AGE:

            bid = data["bid"]

            ask = data["ask"]


            if bid > 0 and ask > 0:

                mid = (
                    bid + ask
                ) / 2.0


                return (

                    (
                        ask - bid
                    )
                    / mid

                ) * 100.0


    # REST fallback

    now = time.time()


    last_req = (

        rest_book_last_request.get(

            symbol,

            0

        )
    )


    if (

        now - last_req
        < REST_BOOK_MIN_INTERVAL

    ):

        inc_stat(
            "spread_missing"
        )

        return None


    rest_book_last_request[
        symbol
    ] = now


    try:

        url = (

            f"{BINANCE_REST}"
            "/api/v3/ticker/bookTicker"
        )


        response = session.get(

            url,

            params={

                "symbol":
                    symbol.upper()

            },

            timeout=
                REST_BOOK_TIMEOUT,
        )


        if response.status_code != 200:

            inc_stat(
                "spread_missing"
            )

            return None


        data = response.json()


        bid = safe_float(

            data.get(
                "bidPrice"
            )
        )

        ask = safe_float(

            data.get(
                "askPrice"
            )
        )


        if bid <= 0 or ask <= 0:

            inc_stat(
                "spread_missing"
            )

            return None


        store_book(

            symbol,

            bid,

            ask
        )


        mid = (
            bid + ask
        ) / 2.0


        return (

            (
                ask - bid
            )
            / mid

        ) * 100.0


    except Exception as e:

        print(

            f"REST book error "
            f"{symbol}: {e}"
        )

        inc_stat(
            "spread_missing"
        )

        return None


# ============================================================
# ANALYZE BINANCE
# ============================================================

def analyze_binance_symbol(
    symbol
):

    # Do not analyze during sleep hours.

    if not is_trading_time():

        return None


    inc_stat(
        "checked"
    )


    with data_lock:

        candle = live_candles.get(
            symbol
        )

        history = list(

            candle_history.get(

                symbol,

                []

            )
        )

        quote_24h = volume_24h.get(

            symbol,

            0

        )


    if not candle:

        return None


    # Need 1440 closed candles.

    if len(history) < BREAKOUT_LOOKBACK:

        return None


    # ========================================================
    # 24H VOLUME
    # ========================================================

    if (

        quote_24h
        < MIN_24H_QUOTE_VOLUME

    ):

        return None


    # ========================================================
    # 5M MOMENTUM
    # ========================================================

    open_price = candle[
        "open"
    ]

    price = candle[
        "close"
    ]


    price_change = percent_change(

        open_price,

        price
    )


    # Minimum +1%

    if (

        price_change
        < MIN_PRICE_CHANGE

    ):

        return None


    # NO MAXIMUM MOMENTUM LIMIT.

    momentum_points = (
        momentum_score(
            price_change
        )
    )


    if momentum_points <= 0:

        return None


    inc_stat(
        "momentum"
    )


    # ========================================================
    # VOLUME
    # ========================================================

    previous = history[
        -AVERAGE_VOLUME_CANDLES:
    ]


    volumes = [

        x["quote_volume"]

        for x in previous

        if x["quote_volume"] > 0

    ]


    if not volumes:

        return None


    average_volume = (

        sum(volumes)
        / len(volumes)
    )


    current_volume = candle[
        "quote_volume"
    ]


    if average_volume <= 0:

        return None


    volume_ratio = (

        current_volume
        / average_volume
    )


    if volume_ratio < 1.2:

        return None


    inc_stat(
        "volume"
    )


    volume_points = volume_score(

        volume_ratio
    )


    # ========================================================
    # BUY PRESSURE
    # ========================================================

    taker_buy_quote = candle[
        "taker_buy_quote"
    ]


    if current_volume <= 0:

        return None


    buy_pressure = (

        taker_buy_quote
        / current_volume

    ) * 100.0


    if (

        buy_pressure
        < MIN_BUY_PRESSURE

    ):

        return None


    inc_stat(
        "buy_pressure"
    )


    buy_points = (

        buy_pressure_score(

            buy_pressure

        )
    )


    # ========================================================
    # 1440 CANDLE HIGH
    # ========================================================

    resistance_data = (

        find_1440_candle_high(

            symbol

        )
    )


    if not resistance_data:

        return None


    resistance = (
        resistance_data["price"]
    )


    # ========================================================
    # +1% REAL BREAKOUT
    # ========================================================

    (
        breakout_points,
        breakout_percent

    ) = breakout_score(

        price,

        resistance
    )


    # HARD REQUIREMENT:
    #
    # Current candle close must be
    # at least +1% above resistance.

    if (

        breakout_percent
        < MIN_BREAKOUT_PERCENT

    ):

        return None


    inc_stat(
        "breakout"
    )


    # ========================================================
    # BREAKOUT VOLUME
    # ========================================================

    (
        breakout_volume_points,
        breakout_volume_ratio

    ) = breakout_volume_score(

        current_volume,

        average_volume
    )


    if (

        breakout_volume_ratio
        < MIN_BREAKOUT_VOLUME_RATIO

    ):

        return None


    inc_stat(
        "breakout_volume"
    )


    # ========================================================
    # CANDLE QUALITY
    # ========================================================

    (
        candle_points,
        close_position,
        upper_wick

    ) = candle_quality(

        candle
    )


    if candle_points <= 0:

        return None


    inc_stat(
        "candle_quality"
    )


    # ========================================================
    # SPREAD
    # ========================================================

    spread = get_spread(
        symbol
    )


    if spread is None:

        return None


    if (

        spread
        > MAX_SPREAD_PERCENT

    ):

        inc_stat(
            "spread_rejected"
        )

        return None


    inc_stat(
        "spread"
    )


    # ========================================================
    # TOTAL SCORE
    # ========================================================

    total_score = (

        momentum_points

        + min(
            volume_points,
            20
        )

        + buy_points

        + min(
            breakout_points,
            15
        )

        + min(
            breakout_volume_points,
            10
        )

        + candle_points
    )


    if (

        total_score
        < MIN_SIGNAL_SCORE

    ):

        return None


    inc_stat(
        "score"
    )


    # ========================================================
    # SIGNAL COOLDOWN
    # ========================================================

    now = time.time()


    with signal_lock:

        last_time = (

            last_signal_time.get(

                symbol,

                0

            )
        )


        if (

            now - last_time
            < SIGNAL_COOLDOWN_SECONDS

        ):

            return None


        last_signal_time[
            symbol
        ] = now


    inc_stat(
        "signals"
    )


    # ========================================================
    # STATUS
    # ========================================================

    if (

        total_score
        >= STRONG_SIGNAL_SCORE

    ):

        status = "🔥 STRONG BUY"

    else:

        status = "🟢 BUY"


    # ========================================================
    # ENTRY / STOP / TP
    # ========================================================

    entry = price


    stop = (

        resistance
        * (

            1
            - STOP_BELOW_RESISTANCE_PERCENT
            / 100.0

        )
    )


    tp1 = (

        entry
        * (

            1
            + TP1_PERCENT
            / 100.0

        )
    )


    tp2 = (

        entry
        * (

            1
            + TP2_PERCENT
            / 100.0

        )
    )


    tp3 = (

        entry
        * (

            1
            + TP3_PERCENT
            / 100.0

        )
    )


    return {

        "symbol":
            symbol.upper(),

        "price":
            price,

        "price_change":
            price_change,

        "current_volume":
            current_volume,

        "average_volume":
            average_volume,

        "volume_ratio":
            volume_ratio,

        "buy_pressure":
            buy_pressure,

        "resistance":
            resistance,

        "resistance_age":
            resistance_data["age"],

        "resistance_time":
            resistance_data[
                "open_time"
            ],

        "breakout_percent":
            breakout_percent,

        "breakout_volume_ratio":
            breakout_volume_ratio,

        "spread":
            spread,

        "close_position":
            close_position,

        "upper_wick":
            upper_wick,

        "momentum_points":
            momentum_points,

        "volume_points":
            volume_points,

        "buy_points":
            buy_points,

        "breakout_points":
            breakout_points,

        "breakout_volume_points":
            breakout_volume_points,

        "candle_points":
            candle_points,

        "score":
            total_score,

        "status":
            status,

        "entry":
            entry,

        "stop":
            stop,

        "tp1":
            tp1,

        "tp2":
            tp2,

        "tp3":
            tp3,
    }


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

def format_signal(
    signal
):

    symbol = signal[
        "symbol"
    ]


    return f"""

{signal["status"]}

<b>🚀 BINANCE SPOT 5M REAL BREAKOUT</b>

🪙 <b>{symbol}</b>

💰 Price:
<b>{round_price(signal["price"])}</b>

📈 5M Momentum:
<b>+{signal["price_change"]:.2f}%</b>

━━━━━━━━━━━━━━━━━━

🏔 1440 CANDLE HIGH:
<b>{round_price(signal["resistance"])}</b>

🚀 Breakout:
<b>+{signal["breakout_percent"]:.2f}%</b>

🔥 Breakout Volume:
<b>{signal["breakout_volume_ratio"]:.2f}×</b>

🟢 Buy Pressure:
<b>{signal["buy_pressure"]:.1f}%</b>

📊 Current Volume:
<b>${signal["current_volume"]:,.0f}</b>

📊 Volume vs Average:
<b>{signal["volume_ratio"]:.2f}×</b>

📖 Spread:
<b>{signal["spread"]:.3f}%</b>

🕯 Close Position:
<b>{signal["close_position"]:.1f}%</b>

━━━━━━━━━━━━━━━━━━

🏆 SCORE:
<b>{signal["score"]}/100</b>

🚀 Momentum:
<b>{signal["momentum_points"]}/25</b>

🔥 Volume:
<b>{min(signal["volume_points"],20)}/20</b>

🟢 Buy Pressure:
<b>{signal["buy_points"]}/20</b>

💥 Breakout:
<b>{min(signal["breakout_points"],15)}/15</b>

🔥 Breakout Volume:
<b>{min(signal["breakout_volume_points"],10)}/10</b>

🕯 Candle:
<b>{signal["candle_points"]}/10</b>

━━━━━━━━━━━━━━━━━━

🎯 ENTRY:
<b>{round_price(signal["entry"])}</b>

🛑 STOP LOSS:
<b>{round_price(signal["stop"])}</b>

🎯 TP1:
<b>{round_price(signal["tp1"])}</b> +3%

🎯 TP2:
<b>{round_price(signal["tp2"])}</b> +5%

🎯 TP3:
<b>{round_price(signal["tp3"])}</b> +8%

━━━━━━━━━━━━━━━━━━

💧 24H Spot Volume:
<b>${volume_24h.get(symbol.lower(),0):,.0f}</b>

🕐 Signal cooldown:
<b>24 HOURS</b>

🕒 Trading hours:
<b>07:00 - 01:00 AZ</b>

⚠️ <b>ALERT ONLY</b>
No automatic order.
"""


# ============================================================
# KLINE PROCESSING
# ============================================================

def process_kline(
    symbol,
    k
):

    # Do nothing during sleep.

    if not is_trading_time():

        return


    candle = {

        "open_time":
            int(k["t"]),

        "open":
            safe_float(k["o"]),

        "high":
            safe_float(k["h"]),

        "low":
            safe_float(k["l"]),

        "close":
            safe_float(k["c"]),

        "volume":
            safe_float(k["v"]),

        "quote_volume":
            safe_float(k["q"]),

        "trades":
            int(k["n"]),

        "taker_buy_base":
            safe_float(k["V"]),

        "taker_buy_quote":
            safe_float(k["Q"]),

        "closed":
            bool(k["x"]),
    }


    # ========================================================
    # LIVE CANDLE
    # ========================================================

    with data_lock:

        live_candles[
            symbol
        ] = candle


        if candle["closed"]:

            history = (
                candle_history.get(
                    symbol
                )
            )


            if history is None:

                history = deque(
                    maxlen=HISTORY_LIMIT
                )

                candle_history[
                    symbol
                ] = history


            # Avoid duplicate candle

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


            live_candles.pop(
                symbol,
                None
            )


            return


    # ========================================================
    # LIVE ANALYSIS
    # ========================================================

    signal = (
        analyze_binance_symbol(
            symbol
        )
    )


    if signal:

        message = (
            format_signal(
                signal
            )
        )


        print(
            "\n"
            + "=" * 70
        )

        print(message)

        print(
            "=" * 70
        )


        queue_telegram(
            message
        )


# ============================================================
# WEBSOCKET URL
# ============================================================

def make_ws_url(
    symbol_chunk,
    stream_type
):

    if stream_type == "kline":

        streams = [

            f"{symbol}@kline_5m"

            for symbol in symbol_chunk
        ]

    else:

        streams = [

            f"{symbol}@bookTicker"

            for symbol in symbol_chunk
        ]


    stream_string = "/".join(
        streams
    )


    return (

        f"{BINANCE_WS}"
        f"/stream?streams="
        f"{stream_string}"
    )


# ============================================================
# KLINE WEBSOCKET
# ============================================================

def kline_websocket_worker(
    symbol_chunk
):

    url = make_ws_url(

        symbol_chunk,

        "kline"
    )


    while running:

        # ====================================================
        # SLEEP HOURS
        # ====================================================

        if not is_trading_time():

            print(

                "🌙 KLINE WS DAYANDI - "
                "01:00-07:00 AZ"
            )

            wait_until_trading_time()

            if not running:

                break


            print(

                "🌅 KLINE WS YENİDƏN "
                "BAŞLAYIR - 07:00 AZ"
            )


        try:

            print(

                "KLINE WS connecting "
                f"{len(symbol_chunk)} symbols..."
            )


            def on_open(ws):

                print(

                    "KLINE WS CONNECTED "
                    f"{len(symbol_chunk)} symbols"
                )


            def on_message(
                ws,
                message
            ):

                try:

                    if not is_trading_time():

                        return


                    data = json.loads(
                        message
                    )


                    payload = data.get(

                        "data",

                        data
                    )


                    if (

                        payload.get("e")
                        != "kline"

                    ):

                        return


                    symbol = (

                        payload["s"]
                        .lower()
                    )


                    process_kline(

                        symbol,

                        payload["k"]
                    )


                except Exception as e:

                    print(

                        "Kline message error:",

                        e
                    )


            def on_error(
                ws,
                error
            ):

                print(

                    "KLINE WS ERROR:",

                    error
                )


            def on_close(
                ws,
                code,
                reason
            ):

                print(

                    "KLINE WS CLOSED:",

                    code,

                    reason
                )


            ws = websocket.WebSocketApp(

                url,

                on_open=on_open,

                on_message=on_message,

                on_error=on_error,

                on_close=on_close,
            )


            register_websocket(ws)


            try:

                ws.run_forever(

                    ping_interval=20,

                    ping_timeout=10,

                    origin=
                        "https://www.binance.com",
                )

            finally:

                unregister_websocket(
                    ws
                )


        except Exception as e:

            print(

                "Kline WS exception:",

                e
            )


        if running:

            if is_trading_time():

                print(

                    "Kline WS reconnecting..."
                )

                time.sleep(
                    RECONNECT_SECONDS
                )

            else:

                wait_until_trading_time()


# ============================================================
# BOOKTICKER WEBSOCKET
# ============================================================

def book_ticker_worker(
    symbol_chunk
):

    url = make_ws_url(

        symbol_chunk,

        "book"
    )


    while running:

        # ====================================================
        # SLEEP HOURS
        # ====================================================

        if not is_trading_time():

            print(

                "🌙 BOOK WS DAYANDI - "
                "01:00-07:00 AZ"
            )

            wait_until_trading_time()

            if not running:

                break


            print(

                "🌅 BOOK WS YENİDƏN "
                "BAŞLAYIR - 07:00 AZ"
            )


        try:

            print(

                "BOOK WS connecting "
                f"{len(symbol_chunk)} symbols..."
            )


            def on_open(ws):

                print(

                    "BOOK WS CONNECTED "
                    f"{len(symbol_chunk)} symbols"
                )


            def on_message(
                ws,
                message
            ):

                try:

                    if not is_trading_time():

                        return


                    data = json.loads(
                        message
                    )


                    payload = data.get(

                        "data",

                        data
                    )


                    symbol = payload.get(
                        "s"
                    )


                    if not symbol:

                        return


                    symbol = (
                        symbol.lower()
                    )


                    if (

                        symbol
                        not in volume_24h

                    ):

                        return


                    bid = safe_float(

                        payload.get("b")
                    )


                    ask = safe_float(

                        payload.get("a")
                    )


                    if bid <= 0 or ask <= 0:

                        return


                    store_book(

                        symbol,

                        bid,

                        ask
                    )


                except Exception as e:

                    print(

                        "Book message error:",

                        e
                    )


            def on_error(
                ws,
                error
            ):

                print(

                    "BOOK WS ERROR:",

                    error
                )


            def on_close(
                ws,
                code,
                reason
            ):

                print(

                    "BOOK WS CLOSED:",

                    code,

                    reason
                )


            ws = websocket.WebSocketApp(

                url,

                on_open=on_open,

                on_message=on_message,

                on_error=on_error,

                on_close=on_close,
            )


            register_websocket(ws)


            try:

                ws.run_forever(

                    ping_interval=20,

                    ping_timeout=10,

                    origin=
                        "https://www.binance.com",
                )

            finally:

                unregister_websocket(
                    ws
                )


        except Exception as e:

            print(

                "Book WS exception:",

                e
            )


        if running:

            if is_trading_time():

                print(

                    "Book WS reconnecting..."
                )

                time.sleep(
                    RECONNECT_SECONDS
                )

            else:

                wait_until_trading_time()


# ============================================================
# STATUS
# ============================================================

def status_worker():

    while running:

        time.sleep(
            STATUS_INTERVAL
        )


        current = get_stats()


        with data_lock:

            symbol_count = len(
                symbols
            )

            history_count = len(
                candle_history
            )

            live_count = len(
                live_candles
            )

            book_count = len(
                book_data
            )

            updates = (
                book_updates
            )

            last_update = (
                last_book_update
            )


        if last_update > 0:

            book_age = (

                time.time()
                - last_update
            )

        else:

            book_age = -1


        now = get_azerbaijan_time()


        print(
            "\n"
            "================ STATUS ================\n"
        )


        print(

            "Azərbaycan vaxtı:",

            now.strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        )


        print(

            "Trading:",
            "ACTIVE"
            if is_trading_time()
            else "SLEEP"
        )


        print(
            "BINANCE SPOT"
        )


        print(
            "Symbols:",
            symbol_count
        )


        print(
            "1440-candle histories:",
            history_count
        )


        print(
            "Live candles:",
            live_count
        )


        print(
            "Book ticker:",
            book_count
        )


        print(
            "Book updates:",
            updates
        )


        print(

            "Last book update:",

            (

                f"{book_age:.1f}s ago"

                if book_age >= 0

                else "NEVER"

            )
        )


        print(

            "Checked:",

            current["checked"]
        )


        print(

            "Momentum passed:",

            current["momentum"]
        )


        print(

            "Volume passed:",

            current["volume"]
        )


        print(

            "Buy pressure passed:",

            current["buy_pressure"]
        )


        print(

            "1440H Breakout passed:",

            current["breakout"]
        )


        print(

            "Breakout volume:",

            current["breakout_volume"]
        )


        print(

            "Candle quality:",

            current["candle_quality"]
        )


        print(

            "Spread passed:",

            current["spread"]
        )


        print(

            "Spread missing:",

            current["spread_missing"]
        )


        print(

            "Spread rejected:",

            current["spread_rejected"]
        )


        print(

            "Score:",

            current["score"]
        )


        print(

            "Signals:",

            current["signals"]
        )


        print(
            "\nTELEGRAM"
        )


        print(

            "OK:",

            current["telegram_ok"]
        )


        print(

            "Failed:",

            current["telegram_failed"]
        )


        print(

            "=========================================\n"
        )


        reset_stats()


# ============================================================
# VOLUME REFRESH
# ============================================================

def volume_refresh_worker():

    global volume_24h

    global symbols


    while running:

        time.sleep(
            30 * 60
        )


        # Don't refresh during sleep.

        if not is_trading_time():

            continue


        try:

            all_symbols = (
                load_exchange_info()
            )


            new_volume = (

                load_24h_volumes(

                    all_symbols
                )
            )


            with data_lock:

                volume_24h = (
                    new_volume
                )

                symbols = list(
                    new_volume.keys()
                )


            print(

                "24H volume refreshed:",

                len(new_volume)
            )


        except Exception as e:

            print(

                "Volume refresh error:",

                e
            )


# ============================================================
# MAIN
# ============================================================

def main():

    global symbols

    global volume_24h

    global running


    print(

        """

============================================================
              BINANCE SPOT ALERT BOT
============================================================

⏱ TIMEFRAME
5M

🔵 MARKET
Binance Spot USDT

📚 HISTORY
1440 CLOSED 5M CANDLES

🏔 RESISTANCE
Highest High of previous 1440 candles

🚀 BREAKOUT
Minimum +1.00% ABOVE resistance

📈 BREAKOUT MAXIMUM
NONE

📊 BREAKOUT VOLUME
Minimum 1.5x average volume

🟢 BUY PRESSURE
Minimum 55%

📈 5M MOMENTUM
Minimum +1.00%

📈 MOMENTUM MAXIMUM
NONE

💧 24H SPOT VOLUME
Minimum $1,000,000

📖 SPREAD
Maximum 0.20%

🕯 CANDLE QUALITY
Close position >=70%
Upper wick <=30%

🏆 MINIMUM SCORE
60

🔥 STRONG SCORE
75

🕐 SAME COIN COOLDOWN
24 HOURS

🌙 SLEEP HOURS
01:00 - 07:00 AZERBAIJAN TIME

🟢 ACTIVE HOURS
07:00 - 01:00 AZERBAIJAN TIME

⚠️ ALERT ONLY
NO AUTOMATIC BUY

============================================================
"""
    )


    # ========================================================
    # TRADING HOURS WORKER
    # ========================================================

    threading.Thread(

        target=
            trading_hours_worker,

        daemon=True,

    ).start()


    # ========================================================
    # TELEGRAM
    # ========================================================

    threading.Thread(

        target=
            telegram_worker,

        daemon=True,

    ).start()


    telegram_startup_test()


    # ========================================================
    # WAIT IF STARTED DURING SLEEP
    # ========================================================

    if not is_trading_time():

        print(

            "🌙 Bot hazırda sleep saatındadır."
        )

        print(

            "🌙 Azərbaycan vaxtı ilə "
            "07:00-da aktivləşəcək."
        )


        wait_until_trading_time()


    # ========================================================
    # BINANCE
    # ========================================================

    all_symbols = (
        load_exchange_info()
    )


    volume_24h = (

        load_24h_volumes(

            all_symbols
        )
    )


    symbols = list(
        volume_24h.keys()
    )


    print(

        "Final Binance Spot symbols:",

        len(symbols)
    )


    if not symbols:

        queue_telegram(

            "🔴 <b>BINANCE BOT ERROR</b>\n\n"

            "Heç bir Binance Spot USDT "
            "coin filterdən keçmədi."
        )


    else:

        # ====================================================
        # 1440 CANDLE HISTORY
        # ====================================================

        load_all_histories()


        # ====================================================
        # VOLUME REFRESH
        # ====================================================

        threading.Thread(

            target=
                volume_refresh_worker,

            daemon=True,

        ).start()


        # ====================================================
        # WEBSOCKET CHUNKS
        # ====================================================

        chunks = [

            symbols[i:i + WS_CHUNK_SIZE]

            for i in range(

                0,

                len(symbols),

                WS_CHUNK_SIZE

            )
        ]


        print(

            "WebSocket chunks:",

            len(chunks)
        )


        for chunk in chunks:

            threading.Thread(

                target=
                    kline_websocket_worker,

                args=(chunk,),

                daemon=True,

            ).start()


            threading.Thread(

                target=
                    book_ticker_worker,

                args=(chunk,),

                daemon=True,

            ).start()


            time.sleep(1)


    # ========================================================
    # STATUS
    # ========================================================

    threading.Thread(

        target=
            status_worker,

        daemon=True,

    ).start()


    queue_telegram(

        "🟢 <b>BINANCE SPOT BOT AKTİVDİR</b>\n\n"

        f"🪙 Spot Symbols: "
        f"{len(symbols)}\n"

        "📡 5M Kline WS: ACTIVE\n"

        "📖 BookTicker WS: ACTIVE\n"

        "📚 History: 1440 candles\n"

        "🏔 Resistance: 1440-candle highest high\n"

        "🚀 Breakout: ≥+1%\n"

        "📈 Momentum: ≥+1%\n"

        "📊 Breakout volume: ≥1.5x\n"

        "🟢 Buy pressure: ≥55%\n"

        "🕐 Cooldown: 24H\n"

        "🌙 Sleep: 01:00-07:00 AZ\n\n"

        "⚠️ ALERT ONLY\n"

        "No automatic order."
    )


    print(

        "\n"
        "==================================================\n"
        "BINANCE SPOT BOT STARTED\n"
        "==================================================\n"
    )


    try:

        while True:

            time.sleep(5)


    except KeyboardInterrupt:

        running = False

        close_all_websockets()

        print(
            "Bot stopped."
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
