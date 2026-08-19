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
# SON RAZILAŞILMIŞ MƏNTİQ
#
# 1) CMC Rank 1-2000
# 2) Binance Spot USDT
# 3) 5 dəqiqəlik şam
# 4) Hədəf: +5%
# 5) Volume: minimum $50,000 USDT
#
# 1-Cİ ŞAM:
#   - Şam MÜTLƏQ BAĞLANMALIDIR
#   - Open -> Close hesablanır
#   - +5% və volume >= $50K olarsa SIGNAL
#
# 2-Cİ ŞAM:
#   - 1-ci şam +5% etməyibsə
#   - 1-ci şamın bağlanmış faizi yadda saxlanılır
#   - 2-ci şam canlı izlənir
#   - 2-ci şamın Open -> LIVE PRICE faizi hesablanır
#   - 1-ci şam % + 2-ci şam % >= 5% olarsa
#     2-ci şam BAĞLANMADAN signal
#
# TƏKRAR SİQNAL:
#   - İlk signal qiyməti yadda saxlanılır
#   - Qiymət həmin qiymətin ALTINA düşməyənə qədər
#     yeni signal YOXDUR
#   - Qiymət aşağı düşəndə coin yenidən aktiv olur
#
# ============================================================


CMC_API_KEY = os.getenv("CMC_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

BINANCE_REST = "https://api.binance.com"
BINANCE_WS = "wss://stream.binance.com:9443/ws"

INTERVAL = "5m"

CMC_MIN_RANK = 1
CMC_MAX_RANK = 2000

MIN_PRICE_CHANGE = 5.0
MIN_TOTAL_VOLUME = 50_000.0

WS_CHUNK_SIZE = 100
MAX_CANDLES = 10

RECONNECT_SECONDS = 3
STATUS_INTERVAL = 60
CMC_REFRESH_SECONDS = 1800


# ============================================================
# GLOBAL DATA
# ============================================================

coins = {}
cmc_ranks = {}

history = defaultdict(
    lambda: deque(maxlen=MAX_CANDLES)
)

# Hər coin üçün:
#
# active = yeni signal verməyə icazə var
# signal_price = son signal qiyməti
#
signal_state = {}

# Eyni şam pəncərəsində duplicate signal qarşısı
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
            flush=True,
        )
        return False

    if not TELEGRAM_CHAT_ID:
        print(
            "TELEGRAM_CHAT_ID MISSING",
            flush=True,
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
                flush=True,
            )

            return True

        print(
            "TELEGRAM ERROR:",
            response.status_code,
            response.text[:500],
            flush=True,
        )

        return False

    except Exception as e:

        print(
            "TELEGRAM EXCEPTION:",
            e,
            flush=True,
        )

        return False


# ============================================================
# CMC
# ============================================================

def load_cmc():

    if not CMC_API_KEY:

        print(
            "CMC_API_KEY MISSING",
            flush=True,
        )

        return False

    print(
        "CMC: LOADING TOP 2000...",
        flush=True,
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
        "sort": "market_cap",
        "sort_dir": "asc",
    }

    try:

        response = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=30,
        )

        response.raise_for_status()

        new_ranks = {}

        for coin in response.json().get(
            "data",
            [],
        ):

            symbol = str(
                coin.get(
                    "symbol",
                    "",
                )
            ).upper()

            rank = coin.get("cmc_rank")

            if not symbol or rank is None:
                continue

            rank = int(rank)

            if (
                CMC_MIN_RANK
                <= rank
                <= CMC_MAX_RANK
            ):

                if symbol not in new_ranks:

                    new_ranks[symbol] = rank

        with data_lock:

            cmc_ranks.clear()
            cmc_ranks.update(new_ranks)

        print(
            f"CMC COINS: {len(new_ranks)} "
            f"(RANK {CMC_MIN_RANK}-{CMC_MAX_RANK})",
            flush=True,
        )

        return True

    except Exception as e:

        print(
            "CMC ERROR:",
            e,
            flush=True,
        )

        return False


def cmc_refresh_worker():

    while True:

        time.sleep(
            CMC_REFRESH_SECONDS
        )

        if not load_cmc():
            continue

        with data_lock:

            for symbol, info in coins.items():

                rank = cmc_ranks.get(
                    info["base"]
                )

                if rank is not None:
                    info["rank"] = rank


# ============================================================
# BINANCE SPOT SYMBOLS
# ============================================================

def load_binance_symbols():

    print(
        "BINANCE: LOADING USDT SPOT...",
        flush=True,
    )

    url = (
        f"{BINANCE_REST}"
        "/api/v3/exchangeInfo"
    )

    try:

        response = requests.get(
            url,
            timeout=30,
        )

        response.raise_for_status()

        data = response.json()

        with data_lock:
            ranks = dict(cmc_ranks)

        result = {}

        for item in data.get(
            "symbols",
            [],
        ):

            if item.get("status") != "TRADING":
                continue

            if item.get("quoteAsset") != "USDT":
                continue

            if item.get(
                "isSpotTradingAllowed"
            ) is False:
                continue

            symbol = str(
                item.get(
                    "symbol",
                    "",
                )
            ).upper()

            base = str(
                item.get(
                    "baseAsset",
                    "",
                )
            ).upper()

            if not symbol or not base:
                continue

            rank = ranks.get(base)

            if rank is None:
                continue

            if not (
                CMC_MIN_RANK
                <= rank
                <= CMC_MAX_RANK
            ):
                continue

            # Leveraged tokenləri çıxar
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
            coins.update(result)

            for symbol in result:

                signal_state.setdefault(
                    symbol,
                    {
                        "active": True,
                        "signal_price": None,
                    },
                )

        print(
            f"BINANCE USDT SPOT: "
            f"{len(result)}",
            flush=True,
        )

        print(
            f"TRACKED COINS: "
            f"{len(result)}",
            flush=True,
        )

        return list(result.keys())

    except Exception as e:

        print(
            "BINANCE SYMBOL ERROR:",
            e,
            flush=True,
        )

        return []


# ============================================================
# CANDLE HELPERS
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
        "closed": True,
    }


def load_history(symbols):

    print(
        f"BOOTSTRAP START: "
        f"{len(symbols)} coins",
        flush=True,
    )

    ready = 0

    url = (
        f"{BINANCE_REST}"
        "/api/v3/klines"
    )

    for index, symbol in enumerate(
        symbols,
        start=1,
    ):

        try:

            response = requests.get(
                url,
                params={
                    "symbol": symbol,
                    "interval": INTERVAL,
                    "limit": 5,
                },
                timeout=10,
            )

            if response.status_code != 200:
                continue

            rows = response.json()

            with data_lock:

                history[symbol].clear()

                for row in rows:

                    history[symbol].append(
                        row_to_candle(row)
                    )

            if rows:
                ready += 1

            if index % 100 == 0:

                print(
                    f"BOOTSTRAP: "
                    f"{index}/{len(symbols)}",
                    flush=True,
                )

            time.sleep(0.03)

        except Exception as e:

            print(
                f"HISTORY ERROR "
                f"{symbol}: {e}",
                flush=True,
            )

    print(
        f"BOOTSTRAP FINISHED "
        f"HISTORY_READY={ready}",
        flush=True,
    )


def update_live_candle(
    symbol,
    kline,
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
        "closed": bool(kline["x"]),
    }

    with data_lock:

        if symbol not in coins:
            return

        candles = history[symbol]

        if (
            candles
            and candles[-1]["open_time"]
            == candle["open_time"]
        ):

            candles[-1] = candle

        else:

            candles.append(candle)


# ============================================================
# PRICE CALCULATION
# ============================================================

def price_change(
    start_price,
    end_price,
):

    if start_price <= 0:
        return 0.0

    return (
        (
            end_price
            / start_price
        ) - 1.0
    ) * 100.0


def candle_closed_percent(candle):

    return price_change(
        candle["open"],
        candle["close"],
    )


def candle_live_percent(candle):

    return price_change(
        candle["open"],
        candle["close"],
    )


# ============================================================
# RE-ACTIVATION
# ============================================================

def update_activation_state(
    symbol,
    current_price,
):

    with data_lock:

        state = signal_state.setdefault(
            symbol,
            {
                "active": True,
                "signal_price": None,
            },
        )

        last_signal_price = state[
            "signal_price"
        ]

        # Coin əvvəl signal veribsə,
        # yalnız əvvəlki signal qiymətinin ALTINA
        # düşəndə yenidən aktiv olur.

        if (
            not state["active"]
            and last_signal_price is not None
            and current_price
            < last_signal_price
        ):

            state["active"] = True

            print(
                f"RE-ACTIVATED {symbol}: "
                f"{current_price:.10g} < "
                f"{last_signal_price:.10g}",
                flush=True,
            )


# ============================================================
# WINDOW ID
# ============================================================

def make_window_id(
    symbol,
    count,
    candles,
):

    return (
        f"{symbol}:"
        f"{count}:"
        + ":".join(
            str(c["open_time"])
            for c in candles
        )
    )


# ============================================================
# SIGNAL CHECK
# ============================================================

def check_signal(symbol):

    """
    SON MƏNTİQ:

    1-Cİ ŞAM
    -------------------------
    1-ci şam BAĞLANMALIDIR.

    Open -> Close hesablanır.

    Əgər:
        1-ci şam % >= 5%
        və
        1-ci şam volume >= $50K

    SIGNAL.


    2-Cİ ŞAM
    -------------------------
    Əgər 1-ci şam +5% etməyibsə:

    1-ci şamın BAĞLANMIŞ faizi
    yadda saxlanılır.

    2-ci şam canlı izlənir.

    2-ci şam:
        Open -> Current Price

    hesablanır.

    1-ci şam % +
    2-ci şam canlı %

    >= 5% olarsa

    2-ci şam BAĞLANMADAN SIGNAL.


    TƏKRAR SIGNAL
    -------------------------
    İlk signal qiyməti yadda qalır.

    Qiymət həmin qiymətin altına
    düşməyənə qədər yeni signal yoxdur.
    """

    with data_lock:

        candles = list(
            history.get(
                symbol,
                [],
            )
        )

        info = coins.get(
            symbol
        )

        state = signal_state.get(
            symbol,
            {
                "active": True,
                "signal_price": None,
            },
        )

    if info is None:
        return None

    if len(candles) < 1:
        return None

    if not state["active"]:
        return None


    # ========================================================
    # 1-Cİ ŞAM
    # ========================================================

    # Son şam canlıdırsa, bu hələ 1-ci şam
    # kimi bağlanmış hesab edilmir.

    if len(candles) >= 2:

        first = candles[-2]
        current = candles[-1]

        # Əgər əvvəlki şam bağlanıbsa,
        # onu 1-ci şam kimi yoxlayırıq.

        if first["closed"]:

            first_percent = (
                candle_closed_percent(
                    first
                )
            )

            first_volume = (
                first["quote_volume"]
            )

            # ------------------------------------------------
            # 1-Cİ ŞAM ÖZÜ +5%
            # ------------------------------------------------

            if (
                first_percent
                >= MIN_PRICE_CHANGE
                and
                first_volume
                >= MIN_TOTAL_VOLUME
            ):

                window = [first]

                window_id = make_window_id(
                    symbol,
                    1,
                    window,
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
                    "symbol": symbol,
                    "rank": info["rank"],

                    "start_price":
                        first["open"],

                    "signal_price":
                        first["close"],

                    "price_change":
                        first_percent,

                    "first_percent":
                        first_percent,

                    "second_percent":
                        None,

                    "volume":
                        first_volume,

                    "first_volume":
                        first_volume,

                    "second_volume":
                        0.0,

                    "candles":
                        window,

                    "count": 1,

                    "live": False,
                }


            # ------------------------------------------------
            # 1-Cİ ŞAM + 2-Cİ ŞAM LIVE
            # ------------------------------------------------

            second_percent = (
                candle_live_percent(
                    current
                )
            )

            total_percent = (
                first_percent
                + second_percent
            )

            total_volume = (
                first_volume
                + current[
                    "quote_volume"
                ]
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
                    current,
                ]

                window_id = make_window_id(
                    symbol,
                    2,
                    window,
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
                    "symbol": symbol,
                    "rank": info["rank"],

                    "start_price":
                        first["open"],

                    "signal_price":
                        current["close"],

                    "price_change":
                        total_percent,

                    "first_percent":
                        first_percent,

                    "second_percent":
                        second_percent,

                    "volume":
                        total_volume,

                    "first_volume":
                        first_volume,

                    "second_volume":
                        current[
                            "quote_volume"
                        ],

                    "candles":
                        window,

                    "count": 2,

                    "live":
                        not current["closed"],
                }


    return None


# ============================================================
# LOCK AFTER SIGNAL
# ============================================================

def lock_after_signal(
    symbol,
    signal_price,
):

    with data_lock:

        signal_state[symbol] = {
            "active": False,
            "signal_price": signal_price,
        }

    print(
        f"LOCKED {symbol} "
        f"@ {signal_price:.10g}",
        flush=True,
    )


# ============================================================
# SEND SIGNAL
# ============================================================

def send_signal(signal):

    symbol = signal[
        "symbol"
    ]

    signal_price = signal[
        "signal_price"
    ]

    # ƏVVƏL lock et.
    # Beləliklə Telegram geciksə belə
    # eyni coin duplicate signal vermir.

    lock_after_signal(
        symbol,
        signal_price,
    )

    first = signal[
        "candles"
    ][0]

    message_lines = []

    message_lines.append(
        "🚨 PUMP SIGNAL"
    )

    message_lines.append("")

    message_lines.append(
        f"🪙 {symbol}"
    )

    message_lines.append(
        f"🏆 CMC Rank: "
        f"#{signal['rank']}"
    )

    message_lines.append("")

    # ========================================================
    # 1-Cİ ŞAM
    # ========================================================

    message_lines.append(
        "🕯️ 1-ci şam"
    )

    message_lines.append(
        f"Open: "
        f"{first['open']:.10g}"
    )

    message_lines.append(
        f"Close: "
        f"{first['close']:.10g}"
    )

    message_lines.append(
        f"📈 Artım: "
        f"{signal['first_percent']:+.2f}%"
    )

    message_lines.append(
        f"💰 Volume: "
        f"${signal['first_volume']:,.0f}"
    )

    # ========================================================
    # 2-Cİ ŞAM
    # ========================================================

    if signal["count"] == 2:

        second = signal[
            "candles"
        ][1]

        message_lines.append("")

        message_lines.append(
            "🕯️ 2-ci şam LIVE"
        )

        message_lines.append(
            f"Open: "
            f"{second['open']:.10g}"
        )

        message_lines.append(
            f"Siqnal qiyməti: "
            f"{second['close']:.10g}"
        )

        message_lines.append(
            f"📈 2-ci şam artımı: "
            f"{signal['second_percent']:+.2f}%"
        )

        message_lines.append(
            f"💰 2-ci şam Volume: "
            f"${signal['second_volume']:,.0f}"
        )

        message_lines.append(
            "🟢 2-ci şam hələ bağlanmayıb"
        )

    else:

        message_lines.append("")

        message_lines.append(
            "🔵 1-ci şam bağlanıb"
        )

    message_lines.append("")

    message_lines.append(
        f"📊 ÜMUMİ artım: "
        f"+{signal['price_change']:.2f}%"
    )

    message_lines.append(
        f"💵 ÜMUMİ Volume: "
        f"${signal['volume']:,.0f}"
    )

    message_lines.append("")

    message_lines.append(
        f"🔒 Siqnal/referans qiyməti: "
        f"{signal_price:.10g}"
    )

    message_lines.append(
        "🔓 Yeni signal üçün qiymət "
        "bu qiymətin ALTINA düşməlidir."
    )

    message_lines.append("")

    message_lines.append(
        "📊 Binance Spot"
    )

    message_lines.append(
        "⏱️ 5 dəqiqə"
    )

    message_lines.append(
        "🎯 Şərt: ≥5% + ≥$50K"
    )

    message = "\n".join(
        message_lines
    )

    print(
        "\n" + "=" * 70,
        flush=True,
    )

    print(
        message,
        flush=True,
    )

    print(
        "=" * 70 + "\n",
        flush=True,
    )

    send_telegram(
        message
    )


# ============================================================
# PROCESS LIVE UPDATE
# ============================================================

def process_live_update(
    symbol,
):

    with data_lock:

        candles = list(
            history.get(
                symbol,
                [],
            )
        )

    if not candles:
        return

    current_price = candles[
        -1
    ]["close"]

    # Əvvəlki signal qiymətinin altına düşübsə
    # coin yenidən aktiv olur.

    update_activation_state(
        symbol,
        current_price,
    )

    with data_lock:

        state = signal_state.get(
            symbol,
            {
                "active": True,
                "signal_price": None,
            },
        )

    if not state["active"]:
        return

    signal = check_signal(
        symbol
    )

    if signal is None:
        return

    # Telegram request WebSocket-i bloklamasın.

    threading.Thread(
        target=send_signal,
        args=(signal,),
        daemon=True,
    ).start()


# ============================================================
# WEBSOCKET MESSAGE
# ============================================================

def websocket_message(
    ws,
    message,
):

    try:

        payload = json.loads(
            message
        )

        data = payload.get(
            "data",
            payload,
        )

        if data.get("e") != "kline":
            return

        kline = data.get(
            "k"
        )

        if not kline:
            return

        symbol = str(
            kline.get(
                "s",
                "",
            )
        ).upper()

        if not symbol:
            return

        update_live_candle(
            symbol,
            kline,
        )

        # Hər canlı update-də yoxlayırıq.
        #
        # 1-ci şam:
        # yalnız bağlanmış şam kimi yoxlanır.
        #
        # 2-ci şam:
        # canlı qiymətlə yoxlanır.

        process_live_update(
            symbol
        )

    except Exception as e:

        print(
            "WS MESSAGE ERROR:",
            e,
            flush=True,
        )


# ============================================================
# WEBSOCKET OPEN
# ============================================================

def websocket_open(
    ws,
    worker_id,
    symbols,
):

    print(
        f"WS {worker_id}: "
        f"CONNECTED "
        f"({len(symbols)} streams)",
        flush=True,
    )

    streams = [
        f"{symbol.lower()}@kline_5m"
        for symbol in symbols
    ]

    ws.send(
        json.dumps(
            {
                "method": "SUBSCRIBE",
                "params": streams,
                "id": worker_id,
            }
        )
    )

    print(
        f"WS {worker_id}: "
        "LIVE 5M STREAMS ACTIVE",
        flush=True,
    )


# ============================================================
# WEBSOCKET ERROR
# ============================================================

def websocket_error(
    ws,
    error,
    worker_id,
):

    print(
        f"WS {worker_id}: "
        f"ERROR {error}",
        flush=True,
    )


# ============================================================
# WEBSOCKET CLOSE
# ============================================================

def websocket_close(
    ws,
    code,
    message,
    worker_id,
):

    print(
        f"WS {worker_id}: "
        f"CLOSED "
        f"code={code} "
        f"message={message}",
        flush=True,
    )


# ============================================================
# WEBSOCKET WORKER
# ============================================================

def websocket_worker(
    symbols,
    worker_id,
):

    while True:

        try:

            print(
                f"WS {worker_id}: "
                f"CONNECTING "
                f"({len(symbols)} streams)",
                flush=True,
            )

            ws = websocket.WebSocketApp(
                BINANCE_WS,

                on_open=lambda ws:
                    websocket_open(
                        ws,
                        worker_id,
                        symbols,
                    ),

                on_message=
                    websocket_message,

                on_error=lambda ws, error:
                    websocket_error(
                        ws,
                        error,
                        worker_id,
                    ),

                on_close=lambda ws,
                    code,
                    message:
                    websocket_close(
                        ws,
                        code,
                        message,
                        worker_id,
                    ),
            )

            ws.run_forever(
                ping_interval=20,
                ping_timeout=10,
            )

        except Exception as e:

            print(
                f"WS {worker_id}: "
                f"EXCEPTION {e}",
                flush=True,
            )

        print(
            f"WS {worker_id}: "
            f"RECONNECTING IN "
            f"{RECONNECT_SECONDS}s",
            flush=True,
        )

        time.sleep(
            RECONNECT_SECONDS
        )


# ============================================================
# START WEBSOCKETS
# ============================================================

def start_websockets(
    symbols,
):

    chunks = [
        symbols[i:i + WS_CHUNK_SIZE]
        for i in range(
            0,
            len(symbols),
            WS_CHUNK_SIZE,
        )
    ]

    print(
        f"WEBSOCKET CONNECTIONS: "
        f"{len(chunks)}",
        flush=True,
    )

    for worker_id, chunk in enumerate(
        chunks,
        start=1,
    ):

        thread = threading.Thread(
            target=websocket_worker,
            args=(
                chunk,
                worker_id,
            ),
            daemon=True,
        )

        thread.start()

        time.sleep(1)


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "\n"
        "========================================\n"
        "        FAST MEME PUMP ALERT\n"
        "========================================",
        flush=True,
    )

    print(
        "MODE: CLOSED 1-CANDLE + LIVE 2-CANDLE",
        flush=True,
    )

    print(
        f"CMC RANK: "
        f"{CMC_MIN_RANK}-"
        f"{CMC_MAX_RANK}",
        flush=True,
    )

    print(
        f"MIN PRICE: "
        f"+{MIN_PRICE_CHANGE}%",
        flush=True,
    )

    print(
        f"MIN USDT VOLUME: "
        f"${MIN_TOTAL_VOLUME:,.0f}",
        flush=True,
    )

    print(
        "1-CANDLE: OPEN -> CLOSE",
        flush=True,
    )

    print(
        "2-CANDLE: "
        "1ST CLOSED % + 2ND LIVE %",
        flush=True,
    )

    print(
        "2ND CANDLE CLOSE WAIT: NO",
        flush=True,
    )

    print(
        "HIGH PRICE CALCULATION: NO",
        flush=True,
    )

    print(
        "LOW PRICE CALCULATION: NO",
        flush=True,
    )

    print(
        "RE-SIGNAL: "
        "ONLY AFTER PRICE < LAST SIGNAL PRICE",
        flush=True,
    )

    print(
        "3-CANDLE MODE: DISABLED",
        flush=True,
    )

    print(
        "========================================\n",
        flush=True,
    )

    send_telegram(
        "✅ FAST MEME PUMP ALERT STARTED\n\n"

        "🕯️ 1-ci şam yalnız bağlanandan "
        "sonra hesablanır.\n"

        "📈 Open → Close ≥5% "
        "və Volume ≥$50K.\n\n"

        "🕯️ 1-ci şam alınmasa, "
        "2-ci şam canlı izlənir.\n"

        "📈 1-ci şam % + "
        "2-ci şam canlı % ≥5% "
        "olarsa dərhal signal.\n\n"

        "🔒 İlk signal qiyməti yadda qalır.\n"

        "🔓 Qiymət onun altına düşməyənə "
        "qədər təkrar signal yoxdur."
    )

    # --------------------------------------------------------
    # CMC
    # --------------------------------------------------------

    if not load_cmc():

        print(
            "CMC LOAD FAILED",
            flush=True,
        )

        return

    # --------------------------------------------------------
    # BINANCE
    # --------------------------------------------------------

    symbols = load_binance_symbols()

    if not symbols:

        print(
            "NO TRACKED COINS",
            flush=True,
        )

        return

    # --------------------------------------------------------
    # HISTORY
    # --------------------------------------------------------

    load_history(
        symbols
    )

    # --------------------------------------------------------
    # CMC REFRESH
    # --------------------------------------------------------

    threading.Thread(
        target=cmc_refresh_worker,
        daemon=True,
    ).start()

    # --------------------------------------------------------
    # WEBSOCKETS
    # --------------------------------------------------------

    start_websockets(
        symbols
    )

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    while True:

        with data_lock:

            tracked = len(
                coins
            )

            history_ready = sum(
                1
                for symbol in coins
                if len(
                    history.get(
                        symbol,
                        [],
                    )
                ) >= 1
            )

            active = sum(
                1
                for symbol in coins
                if signal_state.get(
                    symbol,
                    {
                        "active": True
                    },
                )["active"]
            )

        print(
            f"STATUS | "
            f"TRACKED={tracked} | "
            f"HISTORY_READY="
            f"{history_ready} | "
            f"ACTIVE={active} | "
            f"MODE=CLOSED1+LIVE2",
            flush=True,
        )

        time.sleep(
            STATUS_INTERVAL
        )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
