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
# CMC Rank: 1 - 2000
# Binance Spot USDT
# 5 dəqiqəlik şam
#
# 1-ci şam:
#   Open -> Close >= +5%
#   Volume >= $50K
#
# 2-ci şam:
#   1-ci bağlanmış şamın %
#   +
#   2-ci canlı şamın %
#   >= +5%
#
# BREAKOUT SİSTEMİ TAMAMİLƏ SİLİNİB.
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
# PUMP CONDITIONS
# ============================================================

MIN_PRICE_CHANGE = 5.0
MIN_TOTAL_VOLUME = 50_000.0


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


# 5M pump history
pump_history = defaultdict(
    lambda: deque(
        maxlen=MAX_PUMP_CANDLES
    )
)


# ============================================================
# PUMP SIGNAL STATE
# ============================================================

signal_state = {}


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
# UPDATE LIVE CANDLE
# ============================================================

def update_live_candle(
    symbol,
    kline
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

        candles = pump_history[
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
        # YALNIZ 5M
        # ====================================================

        if interval == PUMP_INTERVAL:

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
                f"({len(symbols)} coins / "
                f"{len(symbols)} streams)",
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
        f"PUMP INTERVAL: "
        f"{PUMP_INTERVAL}",
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
        "BREAKOUT: DISABLED / REMOVED",
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
        "🎯 Pump şərti: ≥5% + ≥$50K\n"
        "🔒 Pump lock sistemi aktivdir.\n\n"

        "❌ BREAKOUT SİSTEMİ TAMAMİLƏ SİLİNİB."
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

        print(
            f"STATUS | "
            f"CMC={rank_count} | "
            f"TRACKED={tracked} | "
            f"5M_READY={pump_ready} | "
            f"PUMP_ACTIVE={active} | "
            f"PUMP_LOCKED={locked}",
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
