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
# CMC RANK: 1 - 2000
# BINANCE SPOT USDT
# TIMEFRAME: 5M
#
# 1-CI ŞAM:
#   - Bağlanmış olmalıdır
#   - Open -> Close >= +4%
#   - Volume >= $50K
#   - Volume Acceleration >= 1.5x
#   - Buy Pressure >= 60%
#   - Body >= 60%
#   - Upper Wick <= 30%
#
# ƏGƏR 1-Cİ ŞAM BÜTÜN ŞƏRTLƏRİ KEÇİRSƏ:
#   -> DƏRHAL SİQNAL
#
# ƏGƏR 1-Cİ ŞAM KEÇMƏSƏ:
#   -> 2-Cİ ŞAM LIVE İZLƏNİR
#   -> 1-ci + 2-ci şam birlikdə:
#        ümumi artım >= +4%
#        ümumi volume >= $50K
#      olduqda siqnal.
#
# 2-Cİ ŞAMA:
#   - Wick/Body tətbiq olunmur
#   - Buy Pressure ayrıca tələb olunmur
#   - Volume Acceleration ayrıca tələb olunmur
#
# HƏR COIN ÜÇÜN:
#   - Siqnaldan sonra 24 saat cooldown
#   - Qiymətə bağlı LOCK YOXDUR
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
# ƏSAS PUMP ŞƏRTLƏRİ
# ============================================================

MIN_PRICE_CHANGE = 4.0
MIN_TOTAL_VOLUME = 50_000.0


# ============================================================
# ƏLAVƏ FİLTRLƏR
# ============================================================

MIN_VOLUME_ACCELERATION = 1.5
MIN_BUY_PRESSURE = 60.0

MIN_BODY_PERCENT = 60.0
MAX_UPPER_WICK_PERCENT = 30.0


# ============================================================
# 24 SAAT COOLDOWN
# ============================================================

COOLDOWN_SECONDS = 24 * 60 * 60


# ============================================================
# VOLUME ACCELERATION
# ============================================================

VOLUME_LOOKBACK = 3


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
# 24 SAAT COOLDOWN STATE
# ============================================================

last_signal_time = {}


# ============================================================
# SIGNAL LOCKS
# ============================================================

alerted_windows = set()

data_lock = threading.RLock()
signal_lock = threading.Lock()


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
                "❌ CMC RANK MAP 0",
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
            f"{len(new_ranks)}",
            flush=True
        )

        return True

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

            if (
                base.endswith("UP")
                or base.endswith("DOWN")
                or base.endswith("BULL")
                or base.endswith("BEAR")
            ):
                continue

            result[symbol] = {
                "base": base,
                "rank": rank,
                "name": base,
            }

        with data_lock:

            coins.clear()
            coins.update(result)

        print(
            f"✅ BINANCE USDT SPOT: "
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
        "open_time": int(row[0]),
        "open": float(row[1]),
        "high": float(row[2]),
        "low": float(row[3]),
        "close": float(row[4]),
        "base_volume": float(row[5]),
        "close_time": int(row[6]),
        "quote_volume": float(row[7]),

        # Binance kline:
        # row[9] = taker buy base volume
        # row[10] = taker buy quote volume

        "taker_buy_base_volume": float(row[9]),
        "taker_buy_quote_volume": float(row[10]),

        "closed": True,
    }


# ============================================================
# HISTORY
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
# LIVE CANDLE
# ============================================================

def update_live_candle(
    symbol,
    kline
):

    candle = {
        "open_time": int(kline["t"]),
        "open": float(kline["o"]),
        "high": float(kline["h"]),
        "low": float(kline["l"]),
        "close": float(kline["c"]),
        "base_volume": float(kline["v"]),
        "close_time": int(kline["T"]),
        "quote_volume": float(kline["q"]),

        "taker_buy_base_volume":
            float(kline["V"]),

        "taker_buy_quote_volume":
            float(kline["Q"]),

        "closed": bool(kline["x"]),
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
        )
        - 1.0
    ) * 100.0


def candle_percent(candle):

    return price_change(
        candle["open"],
        candle["close"]
    )


# ============================================================
# BUY PRESSURE
# ============================================================

def buy_pressure(candle):

    total = candle[
        "quote_volume"
    ]

    buy = candle[
        "taker_buy_quote_volume"
    ]

    if total <= 0:
        return 0.0

    return (
        buy / total
    ) * 100.0


# ============================================================
# WICK / BODY
# ============================================================

def wick_body_filter(candle):

    high = candle["high"]
    low = candle["low"]
    open_price = candle["open"]
    close_price = candle["close"]

    candle_range = (
        high - low
    )

    if candle_range <= 0:

        return {
            "body_percent": 0.0,
            "upper_wick_percent": 0.0,
            "valid": False,
        }

    body = abs(
        close_price
        - open_price
    )

    upper_wick = (
        high
        - max(
            open_price,
            close_price
        )
    )

    body_percent = (
        body
        / candle_range
    ) * 100.0

    upper_wick_percent = (
        upper_wick
        / candle_range
    ) * 100.0

    valid = (
        body_percent
        >= MIN_BODY_PERCENT
        and
        upper_wick_percent
        <= MAX_UPPER_WICK_PERCENT
    )

    return {
        "body_percent":
            body_percent,

        "upper_wick_percent":
            upper_wick_percent,

        "valid":
            valid,
    }


# ============================================================
# VOLUME ACCELERATION
# ============================================================

def volume_acceleration(
    candles,
    target_index
):

    start = (
        target_index
        - VOLUME_LOOKBACK
    )

    if start < 0:
        return 0.0

    previous = candles[
        start:target_index
    ]

    if len(previous) < VOLUME_LOOKBACK:
        return 0.0

    volumes = [
        c["quote_volume"]
        for c in previous
        if c["closed"]
    ]

    if len(volumes) < VOLUME_LOOKBACK:
        return 0.0

    average_volume = (
        sum(volumes)
        / len(volumes)
    )

    if average_volume <= 0:
        return 0.0

    current_volume = (
        candles[target_index]
        ["quote_volume"]
    )

    return (
        current_volume
        / average_volume
    )


# ============================================================
# 24 SAAT COOLDOWN
# ============================================================

def cooldown_active(symbol):

    now = time.time()

    with data_lock:

        last_time = last_signal_time.get(
            symbol
        )

    if last_time is None:
        return False

    return (
        now - last_time
        <
        COOLDOWN_SECONDS
    )


def cooldown_remaining(symbol):

    with data_lock:

        last_time = last_signal_time.get(
            symbol
        )

    if last_time is None:
        return 0

    remaining = (
        COOLDOWN_SECONDS
        -
        (
            time.time()
            - last_time
        )
    )

    return max(
        0,
        int(remaining)
    )


# ============================================================
# WINDOW ID
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
                c["open_time"]
            )
            for c in candles
        )
    )


# ============================================================
# 1-Cİ ŞAM FILTER
# ============================================================

def first_candle_quality(
    candles,
    first_index
):

    first = candles[
        first_index
    ]

    if not first["closed"]:
        return None

    first_percent = (
        candle_percent(
            first
        )
    )

    first_volume = (
        first["quote_volume"]
    )

    acceleration = (
        volume_acceleration(
            candles,
            first_index
        )
    )

    pressure = (
        buy_pressure(
            first
        )
    )

    wick = (
        wick_body_filter(
            first
        )
    )

    price_ok = (
        first_percent
        >= MIN_PRICE_CHANGE
    )

    volume_ok = (
        first_volume
        >= MIN_TOTAL_VOLUME
    )

    acceleration_ok = (
        acceleration
        >= MIN_VOLUME_ACCELERATION
    )

    pressure_ok = (
        pressure
        >= MIN_BUY_PRESSURE
    )

    wick_ok = (
        wick["valid"]
    )

    all_ok = (
        price_ok
        and
        volume_ok
        and
        acceleration_ok
        and
        pressure_ok
        and
        wick_ok
    )

    return {
        "price_ok": price_ok,
        "volume_ok": volume_ok,
        "acceleration_ok":
            acceleration_ok,
        "pressure_ok":
            pressure_ok,
        "wick_ok":
            wick_ok,

        "all_ok": all_ok,

        "percent":
            first_percent,

        "volume":
            first_volume,

        "acceleration":
            acceleration,

        "pressure":
            pressure,

        "body_percent":
            wick["body_percent"],

        "upper_wick_percent":
            wick["upper_wick_percent"],
    }


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

    if info is None:
        return None

    if cooldown_active(
        symbol
    ):
        return None

    if len(candles) < 2:
        return None


    # ========================================================
    # SON 2 ŞAM
    # ========================================================

    first = candles[-2]
    second = candles[-1]

    if not first["closed"]:
        return None


    # ========================================================
    # 1-Cİ ŞAMIN BÜTÜN FİLTRLƏRİ
    # ========================================================

    quality = first_candle_quality(
        candles,
        len(candles) - 2
    )

    if quality is None:
        return None


    # ========================================================
    # VARİANT 1:
    # 1-Cİ ŞAM TƏK BAŞINA BÜTÜN ŞƏRTLƏRİ ÖDƏYİR
    # ========================================================

    if quality["all_ok"]:

        window = [
            first
        ]

        window_id = make_window_id(
            symbol,
            window
        )

        with signal_lock:

            if window_id in alerted_windows:
                return None

            alerted_windows.add(
                window_id
            )

        return {
            "type":
                "PUMP_1_CANDLE",

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
                quality["percent"],

            "second_percent":
                None,

            "total_percent":
                quality["percent"],

            "first_volume":
                quality["volume"],

            "second_volume":
                0.0,

            "total_volume":
                quality["volume"],

            "acceleration":
                quality["acceleration"],

            "buy_pressure":
                quality["pressure"],

            "body_percent":
                quality["body_percent"],

            "upper_wick_percent":
                quality[
                    "upper_wick_percent"
                ],

            "count":
                1,

            "live":
                False,

            "first_quality":
                quality,
        }


    # ========================================================
    # VARİANT 2:
    # 1-Cİ ŞAM KEÇMƏDİ
    # 2-Cİ ŞAM LIVE
    #
    # 1-ci + 2-ci birlikdə:
    #   ümumi >= +4%
    #   ümumi volume >= $50K
    #
    # 2-ci şama wick/body tətbiq olunmur.
    # ========================================================

    if (
        second["open_time"]
        ==
        first["open_time"]
    ):
        return None


    first_percent = (
        candle_percent(
            first
        )
    )

    second_percent = (
        candle_percent(
            second
        )
    )

    total_percent = (
        first_percent
        +
        second_percent
    )

    first_volume = (
        first["quote_volume"]
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

        window_id = make_window_id(
            symbol,
            window
        )

        with signal_lock:

            if window_id in alerted_windows:
                return None

            alerted_windows.add(
                window_id
            )

        return {
            "type":
                "PUMP_2_CANDLE",

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

            "acceleration":
                quality["acceleration"],

            "buy_pressure":
                quality["pressure"],

            "body_percent":
                quality["body_percent"],

            "upper_wick_percent":
                quality[
                    "upper_wick_percent"
                ],

            "count":
                2,

            "live":
                not second["closed"],

            "first_quality":
                quality,
        }

    return None


# ============================================================
# REGISTER 24H SIGNAL
# ============================================================

def register_signal(
    symbol
):

    with data_lock:

        last_signal_time[
            symbol
        ] = time.time()

    print(
        f"⏱️ 24H COOLDOWN: "
        f"{symbol}",
        flush=True
    )


# ============================================================
# SEND SIGNAL
# ============================================================

def send_pump_signal(
    signal
):

    symbol = signal[
        "symbol"
    ]

    register_signal(
        symbol
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
        "🕯️ 1-Cİ ŞAM"
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
        f"📊 Volume Acceleration: "
        f"{signal['acceleration']:.2f}x"
    )

    message.append(
        f"🟢 Buy Pressure: "
        f"{signal['buy_pressure']:.1f}%"
    )

    message.append(
        f"🧱 Body: "
        f"{signal['body_percent']:.1f}%"
    )

    message.append(
        f"↗️ Upper Wick: "
        f"{signal['upper_wick_percent']:.1f}%"
    )


    if signal["count"] == 2:

        message.append("")

        message.append(
            "🕯️ 2-Cİ ŞAM LIVE"
        )

        message.append(
            f"Open: "
            f"{signal['second_open']:.10g}"
        )

        message.append(
            f"Qiymət: "
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
                "🟢 2-ci şam hələ LIVE-dır"
            )

        else:

            message.append(
                "🔵 2-ci şam bağlanıb"
            )


    message.append("")

    message.append(
        f"📊 ÜMUMİ ARTIM: "
        f"{signal['total_percent']:+.2f}%"
    )

    message.append(
        f"💵 ÜMUMİ VOLUME: "
        f"${signal['total_volume']:,.0f}"
    )

    message.append("")

    message.append(
        "⏱️ 24 saat cooldown"
    )

    message.append(
        "❌ Bu müddətdə eyni coin "
        "yenidən siqnal verməyəcək."
    )

    message.append("")

    message.append(
        "📊 Binance Spot"
    )

    message.append(
        "⏱️ 5 dəqiqə"
    )

    message.append(
        "🎯 +4% / $50K"
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

    if cooldown_active(
        symbol
    ):
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

        if interval != PUMP_INTERVAL:
            return

        update_live_candle(
            symbol,
            kline
        )

        process_pump(
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
        "LIVE 5M STREAMS ACTIVE",
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
        f"MIN VOLUME: "
        f"${MIN_TOTAL_VOLUME:,.0f}",
        flush=True
    )

    print(
        f"VOLUME ACCELERATION: "
        f"{MIN_VOLUME_ACCELERATION:.2f}x",
        flush=True
    )

    print(
        f"BUY PRESSURE: "
        f">={MIN_BUY_PRESSURE:.0f}%",
        flush=True
    )

    print(
        f"BODY: "
        f">={MIN_BODY_PERCENT:.0f}%",
        flush=True
    )

    print(
        f"UPPER WICK: "
        f"<={MAX_UPPER_WICK_PERCENT:.0f}%",
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
        "========================================\n",
        flush=True
    )


    # ========================================================
    # TELEGRAM TEST
    # ========================================================

    send_telegram(
        "✅ FAST MEME PUMP ALERT STARTED\n\n"
        "🕯️ Timeframe: 5M\n"
        "🎯 1-ci şam: +4% + $50K\n"
        "📊 Volume Acceleration: ≥1.5x\n"
        "🟢 Buy Pressure: ≥60%\n"
        "🧱 Body: ≥60%\n"
        "↗️ Upper Wick: ≤30%\n\n"
        "🕯️ 1-ci şam keçməsə:\n"
        "2-ci şam LIVE izlənəcək.\n"
        "1-ci + 2-ci birlikdə +4% + $50K olarsa siqnal.\n\n"
        "⏱️ Eyni coin üçün 24 saat cooldown."
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

            pump_ready = sum(
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

        print(
            f"STATUS | "
            f"CMC={rank_count} | "
            f"TRACKED={tracked} | "
            f"5M_READY={pump_ready} | "
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
