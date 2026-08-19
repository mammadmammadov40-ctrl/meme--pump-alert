import os
import time
import json
import threading
from collections import defaultdict, deque

import requests
import websocket


# ============================================================
# FAST MEME PUMP ALERT + 15M BREAKOUT ALERT
# ============================================================
#
# PUMP SİSTEMİ
# ------------------------------------------------------------
# CMC Rank: 1 - 2000
# Binance Spot USDT
# 5 dəqiqəlik şam
#
# 1-ci şam:
#   Open -> Close >= +5%
#   Volume >= $50K
#
# 2-ci şam:
#   1-ci bağlanmış şamın % +
#   2-ci canlı şamın %
#   >= +5%
#
#
# BREAKOUT SİSTEMİ
# ------------------------------------------------------------
# Tamamilə ayrıca işləyir.
#
# Timeframe: 15 dəqiqə
#
# Son təsdiqlənmiş lokal təpə / müqavimət tapılır.
#
# Breakout:
#
#   Current Price >= Resistance * 1.01
#
# yəni müqavimətin üzərində +1%.
#
# Breakout üçün:
#   - +5% lazım deyil
#   - $50K volume lazım deyil
#   - Pump signal lock nəzərə alınmır
#
# Eyni resistance üçün yalnız 1 breakout signal.
#
# Yeni daha yüksək resistance yaranırsa
# və onun üzərində +1% qalxırsa,
# həmin coin üçün yenidən breakout signal gəlir.
#
# ============================================================


CMC_API_KEY = os.getenv("CMC_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

BINANCE_REST = "https://api.binance.com"
BINANCE_WS = "wss://stream.binance.com:9443/ws"


# ============================================================
# TIMEFRAMES
# ============================================================

PUMP_INTERVAL = "5m"
BREAKOUT_INTERVAL = "15m"


# ============================================================
# CMC
# ============================================================

CMC_MIN_RANK = 1
CMC_MAX_RANK = 2000


# ============================================================
# PUMP CONDITIONS
# ============================================================

MIN_PRICE_CHANGE = 5.0
MIN_TOTAL_VOLUME = 50_000.0


# ============================================================
# BREAKOUT CONDITIONS
# ============================================================

# Resistance-in neçə faiz üstündə qalxmalıdır?
BREAKOUT_PERCENT = 1.0

# 15M lokal təpənin təsdiqi:
#
# əvvəlki 15M şamın high-ından böyük
# və sonrakı 15M şamın high-ından böyük.
#
# Burada 20/25 şamlıq limit YOXDUR.
PIVOT_LEFT = 1
PIVOT_RIGHT = 1


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

# 15M üçün kifayət qədər tarix saxlanılır.
#
# Bu "son 20/25 şamda axtar" qaydası deyil.
#
# Burada məqsəd:
# Binance-dən gələn son böyük tarix daxilində
# son təsdiqlənmiş təpəni tapmaqdır.
MAX_BREAKOUT_CANDLES = 1000


# ============================================================
# GLOBAL DATA
# ============================================================

coins = {}
cmc_ranks = {}


# 5M pump history
pump_history = defaultdict(
    lambda: deque(
        maxlen=MAX_PUMP_CANDLES
    )
)


# 15M breakout history
breakout_history = defaultdict(
    lambda: deque(
        maxlen=MAX_BREAKOUT_CANDLES
    )
)


# ============================================================
# PUMP SIGNAL STATE
# ============================================================

signal_state = {}


# ============================================================
# BREAKOUT STATE
# ============================================================
#
# Hər coin üçün son işlənmiş resistance saxlanılır.
#
# {
#   "resistance": float,
#   "signal_sent": True/False,
#   "pivot_time": int
# }
#
breakout_state = {}


# ============================================================
# SIGNAL LOCKS
# ============================================================

alerted_windows = set()

breakout_alerted_levels = set()

data_lock = threading.RLock()
signal_lock = threading.Lock()
breakout_lock = threading.Lock()


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
            "❌ CMC_API_KEY MISSING",
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
                "❌ CMC API ERROR:",
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
                "❌ CMC STATUS ERROR:",
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

        print(
            f"CMC RAW COINS: "
            f"{len(data)}",
            flush=True
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

        if len(new_ranks) == 0:

            print(
                "❌ CMC DATA GƏLDİ, "
                "AMMA RANK MAP 0 OLDU.",
                flush=True
            )

            return False

        with data_lock:

            cmc_ranks.clear()

            cmc_ranks.update(
                new_ranks
            )

        print(
            f"✅ CMC COINS: "
            f"{len(new_ranks)} "
            f"(RANK "
            f"{CMC_MIN_RANK}-"
            f"{CMC_MAX_RANK})",
            flush=True
        )

        return True

    except requests.exceptions.Timeout:

        print(
            "❌ CMC TIMEOUT",
            flush=True
        )

        return False

    except requests.exceptions.RequestException as e:

        print(
            "❌ CMC REQUEST ERROR:",
            repr(e),
            flush=True
        )

        return False

    except Exception as e:

        print(
            "❌ CMC EXCEPTION:",
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
# BINANCE SPOT SYMBOLS
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

        print(
            f"BINANCE HTTP STATUS: "
            f"{response.status_code}",
            flush=True
        )

        if response.status_code != 200:

            print(
                "❌ BINANCE ERROR:",
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

            if (
                item.get(
                    "isSpotTradingAllowed"
                )
                is False
            ):
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

            if not (
                CMC_MIN_RANK
                <= rank
                <= CMC_MAX_RANK
            ):
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
                        "active": True,
                        "signal_price": None,
                    }

                if symbol not in breakout_state:

                    breakout_state[
                        symbol
                    ] = {
                        "resistance": None,
                        "signal_sent": False,
                        "pivot_time": None,
                    }

        print(
            f"✅ BINANCE USDT SPOT: "
            f"{len(result)}",
            flush=True
        )

        print(
            f"✅ TRACKED COINS: "
            f"{len(result)}",
            flush=True
        )

        return list(
            result.keys()
        )

    except Exception as e:

        print(
            "❌ BINANCE SYMBOL ERROR:",
            repr(e),
            flush=True
        )

        return []


# ============================================================
# CANDLE CONVERSION
# ============================================================

def row_to_candle(row):

    return {
        "open_time": int(
            row[0]
        ),

        "open": float(
            row[1]
        ),

        "high": float(
            row[2]
        ),

        "low": float(
            row[3]
        ),

        "close": float(
            row[4]
        ),

        "base_volume": float(
            row[5]
        ),

        "close_time": int(
            row[6]
        ),

        "quote_volume": float(
            row[7]
        ),

        "closed": True,
    }


# ============================================================
# LOAD 5M HISTORY
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
                    "limit": 5,
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
                f"5M HISTORY ERROR "
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
# LOAD 15M BREAKOUT HISTORY
# ============================================================

def load_breakout_history(symbols):

    print(
        f"15M BREAKOUT BOOTSTRAP START: "
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
                    "interval": BREAKOUT_INTERVAL,
                    "limit": MAX_BREAKOUT_CANDLES,
                },
                timeout=15
            )

            if response.status_code != 200:
                continue

            rows = response.json()

            with data_lock:

                breakout_history[
                    symbol
                ].clear()

                for row in rows:

                    breakout_history[
                        symbol
                    ].append(
                        row_to_candle(
                            row
                        )
                    )

            if rows:
                ready += 1

            if index % 50 == 0:

                print(
                    f"15M BREAKOUT BOOTSTRAP: "
                    f"{index}/"
                    f"{len(symbols)}",
                    flush=True
                )

            time.sleep(
                0.05
            )

        except Exception as e:

            print(
                f"15M HISTORY ERROR "
                f"{symbol}: "
                f"{repr(e)}",
                flush=True
            )

    print(
        f"15M BREAKOUT BOOTSTRAP FINISHED "
        f"HISTORY_READY={ready}",
        flush=True
    )


# ============================================================
# UPDATE LIVE CANDLE
# ============================================================

def update_live_candle(
    symbol,
    kline,
    timeframe
):

    candle = {
        "open_time": int(
            kline["t"]
        ),

        "open": float(
            kline["o"]
        ),

        "high": float(
            kline["h"]
        ),

        "low": float(
            kline["l"]
        ),

        "close": float(
            kline["c"]
        ),

        "base_volume": float(
            kline["v"]
        ),

        "close_time": int(
            kline["T"]
        ),

        "quote_volume": float(
            kline["q"]
        ),

        "closed": bool(
            kline["x"]
        ),
    }

    with data_lock:

        if symbol not in coins:
            return

        if timeframe == PUMP_INTERVAL:

            candles = pump_history[
                symbol
            ]

        else:

            candles = breakout_history[
                symbol
            ]

        if (
            candles
            and
            candles[-1][
                "open_time"
            ]
            ==
            candle[
                "open_time"
            ]
        ):

            candles[-1] = candle

        else:

            candles.append(
                candle
            )


# ============================================================
# PERCENT
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
        )
        - 1.0
    ) * 100.0


def candle_closed_percent(
    candle
):

    return price_change(
        candle["open"],
        candle["close"]
    )


def candle_live_percent(
    candle
):

    return price_change(
        candle["open"],
        candle["close"]
    )


# ============================================================
# PUMP RE-ACTIVATE
# ============================================================

def update_activation_state(
    symbol,
    current_price
):

    with data_lock:

        state = signal_state.setdefault(
            symbol,
            {
                "active": True,
                "signal_price": None,
            }
        )

        last_signal_price = state[
            "signal_price"
        ]

        if (
            not state["active"]
            and
            last_signal_price is not None
            and
            current_price
            < last_signal_price
        ):

            state[
                "active"
            ] = True

            print(
                f"🔓 PUMP RE-ACTIVATED "
                f"{symbol}: "
                f"{current_price:.10g} < "
                f"{last_signal_price:.10g}",
                flush=True
            )


# ============================================================
# PUMP WINDOW
# ============================================================

def make_window_id(
    symbol,
    candles
):

    return (
        f"{symbol}:"
        +
        ":".join(
            str(
                c[
                    "open_time"
                ]
            )
            for c in candles
        )
    )


# ============================================================
# PUMP SIGNAL CHECK
# ============================================================

def check_pump_signal(
    symbol
):

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

        state = signal_state.get(
            symbol,
            {
                "active": True,
                "signal_price": None
            }
        )

    if info is None:
        return None

    if not candles:
        return None

    if not state["active"]:
        return None


    # ========================================================
    # 1-Cİ + 2-Cİ ŞAM
    # ========================================================

    if len(candles) >= 2:

        first = candles[-2]
        second = candles[-1]

        if (
            first["closed"]
            and
            first["open_time"]
            != second["open_time"]
        ):

            first_percent = (
                candle_closed_percent(
                    first
                )
            )

            first_volume = (
                first["quote_volume"]
            )


            # ------------------------------------------------
            # 1-Cİ ŞAMDA SIGNAL
            # ------------------------------------------------

            if (
                first_percent
                >= MIN_PRICE_CHANGE
                and
                first_volume
                >= MIN_TOTAL_VOLUME
            ):

                window = [
                    first
                ]

                window_id = (
                    make_window_id(
                        symbol,
                        window
                    )
                )

                with signal_lock:

                    if (
                        window_id
                        in alerted_windows
                    ):
                        return None

                    alerted_windows.add(
                        window_id
                    )

                return {
                    "type":
                        "PUMP",

                    "symbol":
                        symbol,

                    "rank":
                        info["rank"],

                    "signal_price":
                        first["close"],

                    "first_open":
                        first["open"],

                    "first_close":
                        first["close"],

                    "first_percent":
                        first_percent,

                    "second_percent":
                        None,

                    "total_percent":
                        first_percent,

                    "first_volume":
                        first_volume,

                    "second_volume":
                        0.0,

                    "total_volume":
                        first_volume,

                    "candles":
                        window,

                    "count":
                        1,

                    "live":
                        False,
                }


            # ------------------------------------------------
            # 2-Cİ ŞAM LIVE
            # ------------------------------------------------

            second_percent = (
                candle_live_percent(
                    second
                )
            )

            total_percent = (
                first_percent
                +
                second_percent
            )

            second_volume = (
                second["quote_volume"]
            )

            total_volume = (
                first_volume
                +
                second_volume
            )

            if (
                total_percent
                >= MIN_PRICE_CHANGE
                and
                total_volume
                >= MIN_TOTAL_VOLUME
            ):

                window = [
                    first,
                    second
                ]

                window_id = (
                    make_window_id(
                        symbol,
                        window
                    )
                )

                with signal_lock:

                    if (
                        window_id
                        in alerted_windows
                    ):
                        return None

                    alerted_windows.add(
                        window_id
                    )

                return {
                    "type":
                        "PUMP",

                    "symbol":
                        symbol,

                    "rank":
                        info["rank"],

                    "signal_price":
                        second["close"],

                    "first_open":
                        first["open"],

                    "first_close":
                        first["close"],

                    "second_open":
                        second["open"],

                    "current_price":
                        second["close"],

                    "first_percent":
                        first_percent,

                    "second_percent":
                        second_percent,

                    "total_percent":
                        total_percent,

                    "first_volume":
                        first_volume,

                    "second_volume":
                        second_volume,

                    "total_volume":
                        total_volume,

                    "candles":
                        window,

                    "count":
                        2,

                    "live":
                        not second["closed"],
                }


    # ========================================================
    # YALNIZ 1 ŞAM
    # ========================================================

    if len(candles) == 1:

        first = candles[0]

        if not first["closed"]:
            return None

        first_percent = (
            candle_closed_percent(
                first
            )
        )

        first_volume = (
            first["quote_volume"]
        )

        if (
            first_percent
            >= MIN_PRICE_CHANGE
            and
            first_volume
            >= MIN_TOTAL_VOLUME
        ):

            window = [
                first
            ]

            window_id = (
                make_window_id(
                    symbol,
                    window
                )
            )

            with signal_lock:

                if (
                    window_id
                    in alerted_windows
                ):
                    return None

                alerted_windows.add(
                    window_id
                )

            return {
                "type":
                    "PUMP",

                "symbol":
                    symbol,

                "rank":
                    info["rank"],

                "signal_price":
                    first["close"],

                "first_open":
                    first["open"],

                "first_close":
                    first["close"],

                "first_percent":
                    first_percent,

                "second_percent":
                    None,

                "total_percent":
                    first_percent,

                "first_volume":
                    first_volume,

                "second_volume":
                    0.0,

                "total_volume":
                    first_volume,

                "candles":
                    window,

                "count":
                    1,

                "live":
                    False,
            }

    return None


# ============================================================
# PUMP LOCK
# ============================================================

def lock_after_signal(
    symbol,
    signal_price
):

    with data_lock:

        signal_state[
            symbol
        ] = {
            "active": False,
            "signal_price":
                signal_price,
        }

    print(
        f"🔒 PUMP LOCKED "
        f"{symbol} @ "
        f"{signal_price:.10g}",
        flush=True
    )


# ============================================================
# SEND PUMP SIGNAL
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

    lock_after_signal(
        symbol,
        signal_price
    )

    message = []

    message.append(
        "🚨 PUMP SIGNAL"
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
            f"{signal['current_price']:.10g}"
        )

        message.append(
            f"📈 2-ci şam: "
            f"{signal['second_percent']:+.2f}%"
        )

        message.append(
            f"💰 Volume: "
            f"${signal['second_volume']:,.0f}"
        )

        if signal["live"]:

            message.append(
                "🟢 2-ci şam hələ bağlanmayıb"
            )

        else:

            message.append(
                "🔵 2-ci şam bağlanıb"
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
        f"🔒 İlk siqnal qiyməti: "
        f"{signal_price:.10g}"
    )

    message.append(
        "⛔ Bu qiymətin altına "
        "düşməyənə qədər yeni pump signal yoxdur."
    )

    message.append("")

    message.append(
        "📊 Binance Spot"
    )

    message.append(
        "⏱️ 5 dəqiqə"
    )

    message.append(
        "🎯 Şərt: ≥5% + ≥$50K"
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
# BREAKOUT — FIND LAST CONFIRMED PEAK
# ============================================================
#
# 15M qrafikdə:
#
#          HIGH
#           ▲
#          / \
#         /   \
#
# Bu nöqtə lokal təpədir.
#
# Sadə qayda:
#
# peak.high > əvvəlki 15M high
# peak.high > sonrakı 15M high
#
# ============================================================

def find_latest_resistance(
    candles
):

    if len(candles) < 3:

        return None

    # Yalnız BAĞLANMIŞ 15M şamlar.
    #
    # Son canlı şam təpə kimi qəbul edilmir.

    closed = [
        c
        for c in candles
        if c["closed"]
    ]

    if len(closed) < 3:

        return None

    # Ən sondan geriyə gedirik.
    #
    # 20/25 candle limit yoxdur.
    #
    # Ən son təsdiqlənmiş lokal təpəni tapırıq.

    for i in range(
        len(closed) - 2,
        0,
        -1
    ):

        previous_candle = closed[
            i - 1
        ]

        peak_candle = closed[
            i
        ]

        next_candle = closed[
            i + 1
        ]

        peak_high = peak_candle[
            "high"
        ]

        if (
            peak_high
            > previous_candle["high"]
            and
            peak_high
            > next_candle["high"]
        ):

            return {
                "price":
                    peak_high,

                "open_time":
                    peak_candle[
                        "open_time"
                    ],

                "close_time":
                    peak_candle[
                        "close_time"
                    ],
            }

    return None


# ============================================================
# BREAKOUT CHECK
# ============================================================

def check_breakout(
    symbol
):

    with data_lock:

        candles = list(
            breakout_history.get(
                symbol,
                []
            )
        )

        info = coins.get(
            symbol
        )

    if info is None:
        return None

    if len(candles) < 3:
        return None


    # --------------------------------------------------------
    # SON MÜQAVİMƏT / TƏPƏ
    # --------------------------------------------------------

    resistance = (
        find_latest_resistance(
            candles
        )
    )

    if resistance is None:
        return None


    resistance_price = resistance[
        "price"
    ]

    pivot_time = resistance[
        "open_time"
    ]


    # --------------------------------------------------------
    # CURRENT PRICE
    # --------------------------------------------------------

    current_price = candles[
        -1
    ]["close"]


    # --------------------------------------------------------
    # BREAKOUT LEVEL
    # --------------------------------------------------------
    #
    # Təpə = 0.6000
    #
    # +1%:
    #
    # 0.6000 * 1.01
    # = 0.6060
    #
    # Qiymət 0.6060 və yuxarıdırsa
    # breakout təsdiqlənir.
    #

    breakout_price = (
        resistance_price
        *
        (
            1.0
            +
            BREAKOUT_PERCENT / 100.0
        )
    )


    # --------------------------------------------------------
    # BREAKOUT YOXDUR
    # --------------------------------------------------------

    if current_price < breakout_price:

        return None


    # --------------------------------------------------------
    # BU RESISTANCE ÜÇÜN STATE
    # --------------------------------------------------------

    with data_lock:

        state = breakout_state.setdefault(
            symbol,
            {
                "resistance": None,
                "signal_sent": False,
                "pivot_time": None,
            }
        )

        previous_resistance = state[
            "resistance"
        ]

        previous_pivot_time = state[
            "pivot_time"
        ]


    # --------------------------------------------------------
    # EYNİ RESISTANCE ÜÇÜN TƏKRAR SIGNAL YOX
    # --------------------------------------------------------

    if (
        previous_pivot_time
        == pivot_time
    ):

        if (
            state[
                "signal_sent"
            ]
        ):

            return None


    # --------------------------------------------------------
    # YENİ RESISTANCE
    # --------------------------------------------------------

    with data_lock:

        breakout_state[
            symbol
        ] = {
            "resistance":
                resistance_price,

            "signal_sent":
                True,

            "pivot_time":
                pivot_time,
        }


    # --------------------------------------------------------
    # GLOBAL DUPLICATE PROTECTION
    # --------------------------------------------------------

    level_id = (
        f"{symbol}:"
        f"{pivot_time}:"
        f"{resistance_price:.12g}"
    )

    with breakout_lock:

        if (
            level_id
            in breakout_alerted_levels
        ):

            return None

        breakout_alerted_levels.add(
            level_id
        )


    # --------------------------------------------------------
    # BREAKOUT SIGNAL
    # --------------------------------------------------------

    breakout_percent = price_change(
        resistance_price,
        current_price
    )

    return {

        "type":
            "BREAKOUT",

        "symbol":
            symbol,

        "rank":
            info["rank"],

        "resistance":
            resistance_price,

        "breakout_level":
            breakout_price,

        "current_price":
            current_price,

        "breakout_percent":
            breakout_percent,

        "pivot_time":
            pivot_time,

        "pivot_close_time":
            resistance[
                "close_time"
            ],
    }


# ============================================================
# SEND BREAKOUT SIGNAL
# ============================================================

def send_breakout_signal(
    signal
):

    symbol = signal[
        "symbol"
    ]

    resistance = signal[
        "resistance"
    ]

    breakout_level = signal[
        "breakout_level"
    ]

    current_price = signal[
        "current_price"
    ]

    breakout_percent = signal[
        "breakout_percent"
    ]

    message = []

    message.append(
        "🚀 BREAKOUT SIGNAL"
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
        "📈 15M MÜQAVİMƏT QIRILDI"
    )

    message.append(
        f"🔴 Son təpə: "
        f"{resistance:.10g}"
    )

    message.append(
        f"🟡 Breakout səviyyəsi (+1%): "
        f"{breakout_level:.10g}"
    )

    message.append(
        f"🟢 Cari qiymət: "
        f"{current_price:.10g}"
    )

    message.append(
        f"📊 Təpədən artım: "
        f"+{breakout_percent:.2f}%"
    )

    message.append("")

    message.append(
        "🔥 BULLISH BREAKOUT"
    )

    message.append(
        "Qiymət son 15M təpənin "
        "üzərində +1% qalxıb."
    )

    message.append("")

    message.append(
        "⏱️ Timeframe: 15 dəqiqə"
    )

    message.append(
        "🎯 Breakout: "
        f"+{BREAKOUT_PERCENT:.1f}%"
    )

    message.append(
        "📊 Binance Spot"
    )

    message.append("")

    message.append(
        "ℹ️ Bu breakout siqnalı "
        "pump +5% / $50K şərtlərindən "
        "müstəqildir."
    )

    text = "\n".join(
        message
    )

    print(
        "\n"
        + "🚀" * 20,
        flush=True
    )

    print(
        text,
        flush=True
    )

    print(
        "🚀" * 20
        + "\n",
        flush=True
    )

    send_telegram(
        text
    )


# ============================================================
# PROCESS PUMP
# ============================================================

def process_pump(
    symbol
):

    with data_lock:

        candles = list(
            pump_history.get(
                symbol,
                []
            )
        )

    if not candles:
        return

    current_price = candles[
        -1
    ]["close"]

    update_activation_state(
        symbol,
        current_price
    )

    with data_lock:

        state = signal_state.get(
            symbol,
            {
                "active": True,
                "signal_price":
                    None
            }
        )

    if not state["active"]:
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
# PROCESS BREAKOUT
# ============================================================

def process_breakout(
    symbol
):

    signal = check_breakout(
        symbol
    )

    if signal is None:
        return

    threading.Thread(
        target=send_breakout_signal,
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

        if data.get(
            "e"
        ) != "kline":

            return

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

        if not symbol:
            return

        interval = str(
            kline.get(
                "i",
                ""
            )
        )

        # ====================================================
        # 5M
        # ====================================================

        if interval == PUMP_INTERVAL:

            update_live_candle(
                symbol,
                kline,
                PUMP_INTERVAL
            )

            process_pump(
                symbol
            )

        # ====================================================
        # 15M
        # ====================================================

        elif interval == BREAKOUT_INTERVAL:

            update_live_candle(
                symbol,
                kline,
                BREAKOUT_INTERVAL
            )

            process_breakout(
                symbol
            )

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
        f"({len(symbols)} streams)",
        flush=True
    )

    streams = []

    for symbol in symbols:

        streams.append(
            f"{symbol.lower()}@kline_5m"
        )

        streams.append(
            f"{symbol.lower()}@kline_15m"
        )


    # Binance combined stream limiti nəzərə alınaraq
    # hər worker üçün 100 coin -> 200 stream.
    #
    # Binance WebSocket bu strukturla işləyir.

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
        "LIVE 5M + 15M STREAMS ACTIVE",
        flush=True
    )


# ============================================================
# WEBSOCKET ERROR
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
# WEBSOCKET CLOSE
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
                f"({len(symbols)} coins / "
                f"{len(symbols) * 2} streams)",
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
        "   FAST MEME PUMP + BREAKOUT ALERT\n"
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
        f"PUMP INTERVAL: "
        f"{PUMP_INTERVAL}",
        flush=True
    )

    print(
        f"BREAKOUT INTERVAL: "
        f"{BREAKOUT_INTERVAL}",
        flush=True
    )

    print(
        f"PUMP MIN PRICE: "
        f"+{MIN_PRICE_CHANGE}%",
        flush=True
    )

    print(
        f"PUMP MIN VOLUME: "
        f"${MIN_TOTAL_VOLUME:,.0f}",
        flush=True
    )

    print(
        f"BREAKOUT: "
        f"LAST 15M PEAK + "
        f"{BREAKOUT_PERCENT}%",
        flush=True
    )

    print(
        "BREAKOUT CANDLE LIMIT: NONE",
        flush=True
    )

    print(
        "BREAKOUT INDEPENDENT OF PUMP CONDITIONS",
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
        "✅ FAST MEME PUMP + BREAKOUT ALERT STARTED\n\n"

        "🕯️ PUMP: 5M\n"
        "🎯 Pump şərti: ≥5% + ≥$50K\n"
        "🔒 Pump lock sistemi aktivdir.\n\n"

        "📈 BREAKOUT: 15M\n"
        "🔴 Son lokal təpə / müqavimət izlənir.\n"
        "🚀 Təpədən +1% yuxarı qalxarsa "
        "BREAKOUT signal.\n"
        "♻️ Yeni müqavimət qırılarsa yenidən signal.\n"
        "❌ 20/25 şam limiti yoxdur.\n"
        "ℹ️ Breakout pump şərtlərindən müstəqildir."
    )


    # ========================================================
    # CMC
    # ========================================================

    if not load_cmc():

        print(
            "❌ CMC LOAD FAILED",
            flush=True
        )

        return


    # ========================================================
    # BINANCE
    # ========================================================

    symbols = load_binance_symbols()

    if not symbols:

        print(
            "❌ BOT STOPPED: "
            "NO TRACKED COINS",
            flush=True
        )

        return


    # ========================================================
    # 5M HISTORY
    # ========================================================

    load_pump_history(
        symbols
    )


    # ========================================================
    # 15M BREAKOUT HISTORY
    # ========================================================

    load_breakout_history(
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

            pump_ready = sum(
                1
                for symbol in coins
                if len(
                    pump_history.get(
                        symbol,
                        []
                    )
                ) >= 1
            )

            breakout_ready = sum(
                1
                for symbol in coins
                if len(
                    breakout_history.get(
                        symbol,
                        []
                    )
                ) >= 3
            )

            active = sum(
                1
                for symbol in coins
                if signal_state.get(
                    symbol,
                    {
                        "active":
                            True
                    }
                )["active"]
            )

            locked = (
                tracked
                -
                active
            )

            rank_count = len(
                cmc_ranks
            )

            breakout_count = sum(
                1
                for state
                in breakout_state.values()
                if state.get(
                    "signal_sent"
                )
            )

        print(
            f"STATUS | "
            f"CMC={rank_count} | "
            f"TRACKED={tracked} | "
            f"5M_READY={pump_ready} | "
            f"15M_READY={breakout_ready} | "
            f"PUMP_ACTIVE={active} | "
            f"PUMP_LOCKED={locked} | "
            f"BREAKOUT_LEVELS={breakout_count}",
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
