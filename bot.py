import os
import time
import json
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import websocket


# ============================================================
# 5M MOMENTUM + REAL RESISTANCE BREAKOUT BOT
# BINANCE SPOT
# TELEGRAM ALERT ONLY
# ============================================================


BINANCE_REST = "https://api.binance.com"

BINANCE_WS = "wss://stream.binance.com:9443/stream"

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
)


# ============================================================
# SETTINGS
# ============================================================

INTERVAL = "5m"

HISTORY_LIMIT = 100

AVERAGE_VOLUME_CANDLES = 20

RESISTANCE_LOOKBACK = 30

MIN_RESISTANCE_AGE = 5

RESISTANCE_TOLERANCE_PERCENT = 0.40

MIN_TEST_DISTANCE_CANDLES = 2

MIN_RESISTANCE_TESTS = 2


# ============================================================
# MARKET FILTERS
# ============================================================

MIN_24H_QUOTE_VOLUME = 1_000_000

MAX_SPREAD_PERCENT = 0.15

MAX_CURRENT_5M_PRICE = 10.0

MIN_BUY_PRESSURE = 55.0


# ============================================================
# SCORE
# ============================================================

MIN_SIGNAL_SCORE = 70

STRONG_SIGNAL_SCORE = 80


# ============================================================
# VOLUME
# ============================================================

VOLUME_MIN_RATIO = 1.2

VOLUME_GOOD_RATIO = 1.5

VOLUME_STRONG_RATIO = 2.0

VOLUME_VERY_STRONG_RATIO = 3.0

VOLUME_EXTREME_RATIO = 4.0


# ============================================================
# BREAKOUT
# ============================================================

MIN_BREAKOUT_PERCENT = 0.50

STRONG_BREAKOUT_PERCENT = 0.75

VERY_STRONG_BREAKOUT_PERCENT = 1.00

MIN_BREAKOUT_VOLUME_RATIO = 2.0


# ============================================================
# CANDLE
# ============================================================

MIN_CLOSE_POSITION = 70.0

MAX_UPPER_WICK_PERCENT = 30.0


# ============================================================
# RISK
# ============================================================

STOP_BELOW_RESISTANCE_PERCENT = 0.50

TP1_PERCENT = 3.0

TP2_PERCENT = 5.0

TP3_PERCENT = 8.0


# ============================================================
# WEBSOCKET
# ============================================================

WS_CHUNK_SIZE = 100

RECONNECT_SECONDS = 5

STATUS_INTERVAL = 60


# ============================================================
# DUPLICATE PROTECTION
# ============================================================

SIGNAL_COOLDOWN_SECONDS = 30 * 60

SAME_RESISTANCE_TOLERANCE = 0.10


# ============================================================
# GLOBAL DATA
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent":
        "FiveMinuteMomentumBreakoutBot/2.0"
})


symbols = []

volume_24h = {}

candle_history = {}

live_candles = {}

book_data = {}

last_signal_time = {}

alerted_breakouts = {}


data_lock = threading.RLock()

signal_lock = threading.Lock()

running = True


# ============================================================
# FLOAT
# ============================================================

def safe_float(
    value,
    default=0.0
):

    try:
        return float(value)

    except Exception:
        return default


# ============================================================
# PRICE ROUND
# ============================================================

def round_price(
    price
):

    if price >= 1000:
        return round(price, 2)

    if price >= 1:
        return round(price, 4)

    if price >= 0.01:
        return round(price, 6)

    return round(price, 8)


# ============================================================
# PERCENT
# ============================================================

def percent_change(
    open_price,
    current_price
):

    if open_price <= 0:
        return 0.0

    return (
        (current_price - open_price)
        / open_price
    ) * 100.0


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(
    message
):

    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):

        print(
            "\nTelegram token/chat ID yoxdur."
        )

        print(message)

        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {

        "chat_id":
            TELEGRAM_CHAT_ID,

        "text":
            message,

        "parse_mode":
            "HTML",

        "disable_web_page_preview":
            True
    }

    try:

        response = session.post(
            url,
            json=payload,
            timeout=10
        )

        if response.status_code == 200:
            return True

        print(
            "Telegram error:",
            response.status_code,
            response.text[:500]
        )

    except Exception as e:

        print(
            "Telegram exception:",
            e
        )

    return False


# ============================================================
# BINANCE EXCHANGE INFO
# ============================================================

def load_exchange_info():

    print(
        "Loading Binance exchange info..."
    )

    url = (
        f"{BINANCE_REST}/api/v3/exchangeInfo"
    )

    response = session.get(
        url,
        timeout=20
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
        "USDT Spot symbols:",
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
        "Loading 24H ticker data..."
    )

    url = (
        f"{BINANCE_REST}/api/v3/ticker/24hr"
    )

    response = session.get(
        url,
        timeout=30
    )

    response.raise_for_status()

    data = response.json()

    allowed = set(
        all_symbols
    )

    result = {}

    for ticker in data:

        symbol = ticker.get(
            "symbol",
            ""
        ).lower()

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

        result[symbol] = quote_volume

    print(
        "After 24H volume filter:",
        len(result)
    )

    return result


# ============================================================
# LOAD 5M HISTORY
# ============================================================

def load_symbol_history(
    symbol
):

    url = (
        f"{BINANCE_REST}/api/v3/klines"
    )

    params = {

        "symbol":
            symbol.upper(),

        "interval":
            INTERVAL,

        "limit":
            HISTORY_LIMIT
    }

    try:

        response = session.get(
            url,
            params=params,
            timeout=15
        )

        response.raise_for_status()

        data = response.json()

        candles = deque(
            maxlen=HISTORY_LIMIT
        )

        now_ms = int(
            time.time() * 1000
        )

        for k in data:

            close_time = int(
                k[6]
            )

            if close_time >= now_ms:
                continue

            candle = {

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
                    True
            }

            candles.append(
                candle
            )

        return (
            symbol,
            candles
        )

    except Exception as e:

        print(
            f"History error {symbol}: {e}"
        )

        return (
            symbol,
            None
        )


# ============================================================
# LOAD ALL HISTORIES
# ============================================================

def load_all_histories():

    print(
        f"Loading 5M history for "
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
                    f"History: "
                    f"{completed}/"
                    f"{len(symbols)}"
                )

    print(
        "History ready:",
        len(candle_history)
    )


# ============================================================
# RESISTANCE
# ============================================================

def find_resistance(
    symbol
):

    with data_lock:

        history = list(
            candle_history.get(
                symbol,
                []
            )
        )

    if len(history) < 20:
        return None

    candles = history[
        -RESISTANCE_LOOKBACK:
    ]

    if len(candles) < 20:
        return None

    candidates = []

    for i in range(
        2,
        len(candles) - 2
    ):

        high = candles[i]["high"]

        left_highs = [

            candles[j]["high"]

            for j in range(
                i - 2,
                i
            )
        ]

        right_highs = [

            candles[j]["high"]

            for j in range(
                i + 1,
                i + 3
            )
        ]

        if high < max(
            left_highs
        ):
            continue

        if high < max(
            right_highs
        ):
            continue

        age = (
            len(candles)
            - 1
            - i
        )

        if age < MIN_RESISTANCE_AGE:
            continue

        candidates.append({

            "price":
                high,

            "index":
                i,

            "age":
                age
        })

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: x["index"],
        reverse=True
    )

    current_price = candles[-1][
        "close"
    ]

    for candidate in candidates:

        resistance = candidate[
            "price"
        ]

        pivot_index = candidate[
            "index"
        ]

        if resistance <= 0:
            continue

        distance = (
            (
                current_price
                - resistance
            )
            / resistance
        ) * 100.0

        if distance > 5.0:
            continue

        tolerance = (
            resistance
            * (
                RESISTANCE_TOLERANCE_PERCENT
                / 100.0
            )
        )

        tests = []

        last_test_index = None

        for i in range(
            len(candles)
        ):

            if i == pivot_index:
                continue

            high = candles[i]["high"]

            if abs(
                high - resistance
            ) > tolerance:
                continue

            if (
                last_test_index
                is not None
                and (
                    i
                    - last_test_index
                    < MIN_TEST_DISTANCE_CANDLES
                )
            ):
                continue

            tests.append(i)

            last_test_index = i

        test_count = (
            len(tests) + 1
        )

        if (
            test_count
            < MIN_RESISTANCE_TESTS
        ):
            continue

        recent_test = False

        for test_index in tests:

            age = (
                len(candles)
                - 1
                - test_index
            )

            if age <= 15:

                recent_test = True

                break

        if not recent_test:
            continue

        strength = min(
            test_count * 10,
            50
        )

        return {

            "price":
                resistance,

            "index":
                pivot_index,

            "age":
                candidate["age"],

            "tests":
                test_count,

            "strength":
                strength
        }

    return None


# ============================================================
# MOMENTUM SCORE
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


# ============================================================
# VOLUME SCORE
# ============================================================

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


# ============================================================
# BUY PRESSURE SCORE
# ============================================================

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


# ============================================================
# BREAKOUT SCORE
# ============================================================

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

    if breakout >= 1.0:
        return (
            15,
            breakout
        )

    if breakout >= 0.75:
        return (
            12,
            breakout
        )

    if breakout >= 0.50:
        return (
            8,
            breakout
        )

    return (
        0,
        breakout
    )


# ============================================================
# BREAKOUT VOLUME
# ============================================================

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
            3,
            ratio
        )

    return (
        0,
        ratio
    )


# ============================================================
# CANDLE QUALITY
# ============================================================

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
# SPREAD
# ============================================================

def get_spread(
    symbol
):

    with data_lock:

        data = book_data.get(
            symbol
        )

    if not data:
        return None

    bid = data[
        "bid"
    ]

    ask = data[
        "ask"
    ]

    if (
        bid <= 0
        or ask <= 0
    ):
        return None

    mid = (
        bid + ask
    ) / 2

    spread = (
        (
            ask - bid
        )
        / mid
    ) * 100.0

    return spread


# ============================================================
# ANALYZE
# ============================================================

def analyze_symbol(
    symbol
):

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

    if len(history) < 20:
        return None

    if (
        quote_24h
        < MIN_24H_QUOTE_VOLUME
    ):
        return None

    # --------------------------------------------------------
    # PRICE
    # --------------------------------------------------------

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

    if price_change <= 0:
        return None

    if (
        price_change
        > MAX_CURRENT_5M_PRICE
    ):
        return None

    # --------------------------------------------------------
    # MOMENTUM
    # --------------------------------------------------------

    momentum_points = (
        momentum_score(
            price_change
        )
    )

    if momentum_points <= 0:
        return None

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

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

    if (
        volume_ratio
        < VOLUME_MIN_RATIO
    ):
        return None

    volume_points = (
        volume_score(
            volume_ratio
        )
    )

    # --------------------------------------------------------
    # BUY PRESSURE
    # --------------------------------------------------------

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

    buy_points = (
        buy_pressure_score(
            buy_pressure
        )
    )

    # --------------------------------------------------------
    # RESISTANCE
    # --------------------------------------------------------

    resistance_data = (
        find_resistance(
            symbol
        )
    )

    if not resistance_data:
        return None

    resistance = (
        resistance_data[
            "price"
        ]
    )

    resistance_age = (
        resistance_data[
            "age"
        ]
    )

    resistance_tests = (
        resistance_data[
            "tests"
        ]
    )

    resistance_strength = (
        resistance_data[
            "strength"
        ]
    )

    # --------------------------------------------------------
    # BREAKOUT
    # --------------------------------------------------------

    if price <= resistance:
        return None

    (
        breakout_points,
        breakout_percent
    ) = breakout_score(
        price,
        resistance
    )

    if (
        breakout_percent
        < MIN_BREAKOUT_PERCENT
    ):
        return None

    # --------------------------------------------------------
    # BREAKOUT VOLUME
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # CANDLE
    # --------------------------------------------------------

    (
        candle_points,
        close_position,
        upper_wick
    ) = candle_quality(
        candle
    )

    if candle_points <= 0:
        return None

    # --------------------------------------------------------
    # SPREAD
    # --------------------------------------------------------

    spread = get_spread(
        symbol
    )

    # Book ticker yoxdursa hələ siqnal
    # göndərmirik.
    #
    # Artıq ayrıca global bookTicker
    # WebSocket işləyir.
    # --------------------------------------------------------

    if spread is None:
        return None

    if (
        spread
        > MAX_SPREAD_PERCENT
    ):
        return None

    # --------------------------------------------------------
    # TOTAL SCORE
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # DUPLICATE
    # --------------------------------------------------------

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

        previous_resistance = (
            alerted_breakouts.get(
                symbol
            )
        )

        if (
            previous_resistance
            is not None
        ):

            difference = abs(
                resistance
                - previous_resistance
            )

            tolerance = (
                previous_resistance
                * (
                    SAME_RESISTANCE_TOLERANCE
                    / 100
                )
            )

            if (
                difference
                <= tolerance
            ):
                return None

        last_signal_time[
            symbol
        ] = now

        alerted_breakouts[
            symbol
        ] = resistance

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    if (
        total_score
        >= STRONG_SIGNAL_SCORE
    ):

        status = (
            "🔥 STRONG BUY"
        )

    else:

        status = (
            "🟢 BUY"
        )

    # --------------------------------------------------------
    # ENTRY / SL / TP
    # --------------------------------------------------------

    entry = price

    stop = (
        resistance
        * (
            1
            - (
                STOP_BELOW_RESISTANCE_PERCENT
                / 100
            )
        )
    )

    tp1 = (
        entry
        * (
            1
            + TP1_PERCENT / 100
        )
    )

    tp2 = (
        entry
        * (
            1
            + TP2_PERCENT / 100
        )
    )

    tp3 = (
        entry
        * (
            1
            + TP3_PERCENT / 100
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
            resistance_age,

        "resistance_tests":
            resistance_tests,

        "resistance_strength":
            resistance_strength,

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
            tp3
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

<b>🚀 5M MOMENTUM + REAL BREAKOUT</b>

🪙 <b>{symbol}</b>

💰 Price:
<b>{round_price(signal["price"])}</b>

📈 5M Momentum:
<b>+{signal["price_change"]:.2f}%</b>

🔥 Current Volume:
<b>${signal["current_volume"]:,.0f}</b>

📊 Volume:
<b>{signal["volume_ratio"]:.2f}× average</b>

🟢 Buy Pressure:
<b>{signal["buy_pressure"]:.1f}%</b>

━━━━━━━━━━━━━━━━━━

💥 Resistance:
<b>{round_price(signal["resistance"])}</b>

🧪 Resistance Tests:
<b>{signal["resistance_tests"]}</b>

⏳ Resistance Age:
<b>{signal["resistance_age"]} candles</b>

💪 Resistance Strength:
<b>{signal["resistance_strength"]}/50</b>

🚀 Breakout:
<b>+{signal["breakout_percent"]:.2f}%</b>

🔥 Breakout Volume:
<b>{signal["breakout_volume_ratio"]:.2f}×</b>

📊 Spread:
<b>{signal["spread"]:.3f}%</b>

🕯 Close Position:
<b>{signal["close_position"]:.1f}%</b>

━━━━━━━━━━━━━━━━━━

🏆 SCORE:
<b>{signal["score"]}/100</b>

🚀 Momentum:
<b>{signal["momentum_points"]}/25</b>

🔥 Volume:
<b>{min(signal["volume_points"], 20)}/20</b>

🟢 Buy Pressure:
<b>{signal["buy_points"]}/20</b>

💥 Breakout:
<b>{min(signal["breakout_points"], 15)}/15</b>

🔥 Breakout Volume:
<b>{min(signal["breakout_volume_points"], 10)}/10</b>

🕯 Candle:
<b>{signal["candle_points"]}/10</b>

━━━━━━━━━━━━━━━━━━

🎯 ENTRY:
<b>{round_price(signal["entry"])}</b>

🛑 STOP LOSS:
<b>{round_price(signal["stop"])}</b>

🎯 TP1:
<b>{round_price(signal["tp1"])}</b>
+3%

🎯 TP2:
<b>{round_price(signal["tp2"])}</b>
+5%

🎯 TP3:
<b>{round_price(signal["tp3"])}</b>
+8%

━━━━━━━━━━━━━━━━━━

💧 24H Volume:
<b>${volume_24h.get(symbol.lower(), 0):,.0f}</b>

⚠️ <b>ALERT ONLY</b>
No automatic order.
"""


# ============================================================
# KLINE PROCESS
# ============================================================

def process_kline(
    symbol,
    k
):

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
            bool(k["x"])
    }

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
    # LIVE SIGNAL
    # ========================================================

    signal = analyze_symbol(
        symbol
    )

    if signal:

        message = format_signal(
            signal
        )

        print(
            "\n"
            + "=" * 70
            + "\n"
            + message
            + "\n"
            + "=" * 70
        )

        send_telegram(
            message
        )


# ============================================================
# BOOK TICKER PROCESS
# ============================================================

def process_book_ticker(
    data
):

    """
    Binance global !bookTicker
    stream-i müxtəlif formatlarda
    göndərilə bilər.

    Həm list, həm dict qəbul edirik.
    """

    if isinstance(
        data,
        list
    ):

        for item in data:

            process_single_book(
                item
            )

        return

    if isinstance(
        data,
        dict
    ):

        # Combined stream format
        if "data" in data:

            payload = data[
                "data"
            ]

            if isinstance(
                payload,
                list
            ):

                for item in payload:

                    process_single_book(
                        item
                    )

            elif isinstance(
                payload,
                dict
            ):

                process_single_book(
                    payload
                )

            return

        process_single_book(
            data
        )


# ============================================================
# SINGLE BOOK
# ============================================================

def process_single_book(
    data
):

    symbol = data.get(
        "s"
    )

    if not symbol:
        return

    symbol = symbol.lower()

    bid = safe_float(
        data.get("b")
    )

    ask = safe_float(
        data.get("a")
    )

    if (
        bid <= 0
        or ask <= 0
    ):
        return

    # Yalnız bizim filterdən keçən
    # coinləri saxlayırıq.
    if symbol not in volume_24h:
        return

    with data_lock:

        book_data[
            symbol
        ] = {

            "bid":
                bid,

            "ask":
                ask,

            "timestamp":
                time.time()
        }


# ============================================================
# KLINE WEBSOCKET
# ============================================================

def kline_websocket_worker(
    symbol_chunk
):

    streams = []

    for symbol in symbol_chunk:

        streams.append(
            f"{symbol}@kline_5m"
        )

    stream_string = "/".join(
        streams
    )

    url = (
        BINANCE_WS
        + "?streams="
        + stream_string
    )

    while running:

        try:

            print(
                f"KLINE WS connecting "
                f"{len(symbol_chunk)} symbols..."
            )

            def on_open(ws):

                print(
                    f"KLINE WS CONNECTED "
                    f"{len(symbol_chunk)} symbols"
                )

            def on_message(
                ws,
                message
            ):

                try:

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
                        payload[
                            "s"
                        ].lower()
                    )

                    k = payload[
                        "k"
                    ]

                    process_kline(
                        symbol,
                        k
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
                    "Kline WS error:",
                    error
                )

            def on_close(
                ws,
                code,
                reason
            ):

                print(
                    "Kline WS closed:",
                    code,
                    reason
                )

            ws = websocket.WebSocketApp(

                url,

                on_open=on_open,

                on_message=on_message,

                on_error=on_error,

                on_close=on_close
            )

            ws.run_forever(

                ping_interval=20,

                ping_timeout=10
            )

        except Exception as e:

            print(
                "Kline WS exception:",
                e
            )

        if running:

            time.sleep(
                RECONNECT_SECONDS
            )


# ============================================================
# GLOBAL BOOK TICKER WEBSOCKET
# ============================================================

def global_book_ticker_worker():

    """
    ƏSAS DÜZƏLİŞ BURADADIR.

    Artıq hər symbol üçün
    @bookTicker stream istifadə etmirik.

    Bunun əvəzinə Binance-in
    bütün Spot bazarı üçün
    global !bookTicker stream
    istifadə olunur.
    """

    # Binance global book ticker
    # combined stream
    url = (
        BINANCE_WS
        + "?streams=!bookTicker"
    )

    while running:

        try:

            print(
                "GLOBAL BOOK TICKER connecting..."
            )

            def on_open(ws):

                print(
                    "GLOBAL BOOK TICKER CONNECTED"
                )

            def on_message(
                ws,
                message
            ):

                try:

                    data = json.loads(
                        message
                    )

                    process_book_ticker(
                        data
                    )

                except Exception as e:

                    print(
                        "Book ticker error:",
                        e
                    )

            def on_error(
                ws,
                error
            ):

                print(
                    "GLOBAL BOOK TICKER error:",
                    error
                )

            def on_close(
                ws,
                code,
                reason
            ):

                print(
                    "GLOBAL BOOK TICKER closed:",
                    code,
                    reason
                )

            ws = websocket.WebSocketApp(

                url,

                on_open=on_open,

                on_message=on_message,

                on_error=on_error,

                on_close=on_close
            )

            ws.run_forever(

                ping_interval=20,

                ping_timeout=10
            )

        except Exception as e:

            print(
                "Global book ticker exception:",
                e
            )

        if running:

            time.sleep(
                RECONNECT_SECONDS
            )


# ============================================================
# STATUS
# ============================================================

def status_worker():

    while running:

        time.sleep(
            STATUS_INTERVAL
        )

        with data_lock:

            book_count = len(
                book_data
            )

            print(
                "\n"
                "================ STATUS ================"
            )

            print(
                "Symbols:",
                len(symbols)
            )

            print(
                "History:",
                len(candle_history)
            )

            print(
                "Live candles:",
                len(live_candles)
            )

            print(
                "Book ticker:",
                book_count
            )

            print(
                "Signals:",
                len(last_signal_time)
            )

            print(
                "=========================================\n"
            )


# ============================================================
# VOLUME REFRESH
# ============================================================

def volume_refresh_worker():

    global volume_24h

    while running:

        time.sleep(
            30 * 60
        )

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

    print(
        """
============================================================
       5M MOMENTUM + REAL BREAKOUT BOT v2
============================================================

PRICE MOMENTUM       25
VOLUME               20
BUY PRESSURE         20
BREAKOUT             15
BREAKOUT VOLUME      10
CANDLE QUALITY       10
--------------------------------
TOTAL                100

MINIMUM SIGNAL       70
STRONG SIGNAL        80

RESISTANCE:
- 30 candle lookback
- minimum 5 candle age
- minimum 2 tests
- 0.4% tolerance

BREAKOUT:
- minimum +0.5%
- minimum 2x volume
- spread <= 0.15%
- 24H volume >= $1M

WEBSOCKET:
- 5M KLINE streams
- GLOBAL BOOK TICKER

============================================================
"""
    )

    # ========================================================
    # EXCHANGE
    # ========================================================

    all_symbols = (
        load_exchange_info()
    )

    # ========================================================
    # VOLUME
    # ========================================================

    volume_24h = (
        load_24h_volumes(
            all_symbols
        )
    )

    symbols = list(
        volume_24h.keys()
    )

    print(
        "Final symbols:",
        len(symbols)
    )

    if not symbols:

        print(
            "No symbols passed filters."
        )

        return

    # ========================================================
    # HISTORY
    # ========================================================

    load_all_histories()

    # ========================================================
    # STATUS
    # ========================================================

    threading.Thread(
        target=status_worker,
        daemon=True
    ).start()

    # ========================================================
    # VOLUME REFRESH
    # ========================================================

    threading.Thread(
        target=volume_refresh_worker,
        daemon=True
    ).start()

    # ========================================================
    # GLOBAL BOOK TICKER
    # ========================================================

    threading.Thread(
        target=global_book_ticker_worker,
        daemon=True
    ).start()

    # ========================================================
    # KLINE CHUNKS
    # ========================================================

    chunks = [

        symbols[i:i + WS_CHUNK_SIZE]

        for i in range(
            0,
            len(symbols),
            WS_CHUNK_SIZE
        )
    ]

    print(
        "Kline WebSocket connections:",
        len(chunks)
    )

    # ========================================================
    # START KLINE WS
    # ========================================================

    for chunk in chunks:

        thread = threading.Thread(

            target=kline_websocket_worker,

            args=(chunk,),

            daemon=True
        )

        thread.start()

        time.sleep(1)

    print(
        "\n"
        "==================================================\n"
        "BOT STARTED\n"
        "==================================================\n"
    )

    # ========================================================
    # KEEP ALIVE
    # ========================================================

    try:

        while True:

            time.sleep(5)

    except KeyboardInterrupt:

        global running

        running = False

        print(
            "\nBot stopped."
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
