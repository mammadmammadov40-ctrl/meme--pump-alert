import os
import time
import json
import threading
from collections import defaultdict, deque

import requests
import websocket


# ============================================================
# ENVIRONMENT
# ============================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
CMC_API_KEY = os.environ["CMC_API_KEY"]


# ============================================================
# SETTINGS
# ============================================================

BINANCE_URL = "https://api.binance.com"
BINANCE_WS = "wss://stream.binance.com:9443/stream?streams="

# CoinMarketCap rank filter
CMC_MIN_RANK = 1
CMC_MAX_RANK = 2000

# Binance candle
INTERVAL = "5m"

# CMC list refresh
CMC_REFRESH_SECONDS = 3600

# ------------------------------------------------------------
# MAIN SIGNAL RULES
# ------------------------------------------------------------

# 3 x 5M total price increase
MIN_TOTAL_PRICE_CHANGE = 5.0

# 3 x 5M total USDT volume
MIN_TOTAL_VOLUME_USDT = 50_000.0

# NO maximum percentage limit
MAX_PRICE_CHANGE = None

# ------------------------------------------------------------
# LIVE RULE
# ------------------------------------------------------------

# If the latest 2 candles already satisfy:
#
# price >= 5%
# volume >= 50K
#
# signal immediately.
#
# No need to wait for the 3rd candle to close.
LIVE_TWO_CANDLE_ENABLED = True

# Number of candles kept in memory
MAX_CANDLES = 6

# Same coin won't send another signal immediately
ALERT_COOLDOWN_SECONDS = 30 * 60


# ============================================================
# HTTP
# ============================================================

session = requests.Session()
session.headers.update({
    "User-Agent": "meme-pump-alert/1.0"
})


# ============================================================
# DATA
# ============================================================

# Binance symbol -> information
tracked = {}

# symbol -> recent candles
candles = defaultdict(lambda: deque(maxlen=MAX_CANDLES))

# symbol -> last alert timestamp
last_alert = {}

state_lock = threading.RLock()


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(text):

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    try:

        response = session.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "text": text
            },
            timeout=15
        )

        if response.status_code != 200:

            print(
                "TELEGRAM ERROR:",
                response.status_code,
                response.text[:500],
                flush=True
            )

            return False

        return True

    except Exception as e:

        print(
            "TELEGRAM EXCEPTION:",
            e,
            flush=True
        )

        return False


# ============================================================
# COINMARKETCAP
# ============================================================

def fetch_cmc_top_2000():

    url = (
        "https://pro-api.coinmarketcap.com"
        "/v3/cryptocurrency/listings/latest"
    )

    headers = {
        "Accept": "application/json",
        "X-CMC_PRO_API_KEY": CMC_API_KEY
    }

    params = {

        "start": 1,

        "limit": 2000,

        "convert": "USD",

        "sort": "market_cap",

        "sort_dir": "desc"
    }

    response = session.get(
        url,
        headers=headers,
        params=params,
        timeout=30
    )

    response.raise_for_status()

    payload = response.json()

    result = {}

    for coin in payload.get("data", []):

        rank = coin.get("cmc_rank")

        symbol = str(
            coin.get("symbol", "")
        ).upper().strip()

        if not symbol:
            continue

        if rank is None:
            continue

        rank = int(rank)

        if not (
            CMC_MIN_RANK
            <= rank
            <= CMC_MAX_RANK
        ):
            continue

        result[symbol] = {

            "rank": rank,

            "name": coin.get(
                "name",
                symbol
            ),

            "slug": coin.get(
                "slug",
                ""
            )
        }

    print(
        f"CMC COINS: {len(result)} "
        f"(rank {CMC_MIN_RANK}-{CMC_MAX_RANK})",
        flush=True
    )

    return result


# ============================================================
# BINANCE SPOT USDT SYMBOLS
# ============================================================

def fetch_binance_spot_usdt_symbols():

    url = (
        f"{BINANCE_URL}"
        "/api/v3/exchangeInfo"
    )

    response = session.get(
        url,
        timeout=30
    )

    response.raise_for_status()

    data = response.json()

    result = {}

    for item in data.get(
        "symbols",
        []
    ):

        if item.get("status") != "TRADING":
            continue

        if item.get("quoteAsset") != "USDT":
            continue

        if item.get(
            "isSpotTradingAllowed"
        ) is False:
            continue

        base = str(
            item.get(
                "baseAsset",
                ""
            )
        ).upper()

        symbol = str(
            item.get(
                "symbol",
                ""
            )
        ).upper()

        if not base or not symbol:
            continue

        # Ignore leveraged token style assets
        if base.endswith(
            (
                "UP",
                "DOWN",
                "BULL",
                "BEAR"
            )
        ):
            continue

        result[base] = symbol

    print(
        f"BINANCE USDT SPOT: {len(result)}",
        flush=True
    )

    return result


# ============================================================
# BUILD CMC + BINANCE UNIVERSE
# ============================================================

def rebuild_tracked():

    cmc = fetch_cmc_top_2000()

    binance = fetch_binance_spot_usdt_symbols()

    new_tracked = {}

    for base, info in cmc.items():

        symbol = binance.get(base)

        if not symbol:
            continue

        new_tracked[symbol] = {

            "base": base,

            "rank": info["rank"],

            "name": info["name"]
        }

    with state_lock:

        old_symbols = set(tracked)

        tracked.clear()

        tracked.update(
            new_tracked
        )

        removed = (
            old_symbols
            - set(new_tracked)
        )

        for symbol in removed:

            candles.pop(
                symbol,
                None
            )

            last_alert.pop(
                symbol,
                None
            )

    print(
        f"TRACKED COINS: "
        f"{len(new_tracked)}",
        flush=True
    )

    return list(
        new_tracked.keys()
    )


# ============================================================
# BINANCE HISTORICAL 5M CANDLES
# ============================================================

def fetch_klines(
    symbol,
    limit=5
):

    url = (
        f"{BINANCE_URL}"
        "/api/v3/klines"
    )

    params = {

        "symbol": symbol,

        "interval": INTERVAL,

        "limit": limit
    }

    response = session.get(
        url,
        params=params,
        timeout=15
    )

    if response.status_code != 200:

        return []

    rows = response.json()

    result = []

    now_ms = int(
        time.time() * 1000
    )

    for row in rows:

        start_time = int(
            row[0]
        )

        # Binance 5M candle
        closed = (
            start_time + 300000
            <= now_ms
        )

        result.append({

            "start": start_time,

            "open": float(row[1]),

            "high": float(row[2]),

            "low": float(row[3]),

            "close": float(row[4]),

            "volume": float(row[5]),

            # IMPORTANT:
            # quote volume = USDT volume
            "quote_volume": float(row[7]),

            "closed": closed
        })

    return result


# ============================================================
# BOOTSTRAP HISTORY
# ============================================================

def bootstrap_history(symbols):

    print(
        f"BOOTSTRAP START: "
        f"{len(symbols)} coins",
        flush=True
    )

    total = len(symbols)

    for index, symbol in enumerate(
        symbols,
        start=1
    ):

        try:

            rows = fetch_klines(
                symbol,
                limit=5
            )

            if rows:

                with state_lock:

                    candles[
                        symbol
                    ].clear()

                    for candle in rows:

                        candles[
                            symbol
                        ].append(
                            candle
                        )

        except Exception as e:

            print(
                "BOOTSTRAP ERROR",
                symbol,
                e,
                flush=True
            )

        if index % 100 == 0:

            print(
                f"BOOTSTRAP: "
                f"{index}/{total}",
                flush=True
            )

        # Binance API-ni yükləməmək üçün
        # yumşaq sürət
        time.sleep(0.07)

    print(
        "BOOTSTRAP FINISHED",
        flush=True
    )


# ============================================================
# PRICE CALCULATION
# ============================================================

def calculate_price_change(
    window
):

    if len(window) < 2:
        return 0.0

    first_open = window[0]["open"]

    last_close = window[-1]["close"]

    if first_open <= 0:
        return 0.0

    return (
        (
            last_close
            / first_open
        ) - 1
    ) * 100


# ============================================================
# TOTAL VOLUME
# ============================================================

def calculate_total_volume(
    window
):

    return sum(
        candle["quote_volume"]
        for candle in window
    )


# ============================================================
# RULE CHECK
# ============================================================

def rules_match(
    window
):

    if len(window) < 2:
        return False

    price_change = calculate_price_change(
        window
    )

    total_volume = calculate_total_volume(
        window
    )

    # Minimum price
    if (
        price_change
        < MIN_TOTAL_PRICE_CHANGE
    ):
        return False

    # NO maximum limit
    if (
        MAX_PRICE_CHANGE
        is not None
        and price_change
        > MAX_PRICE_CHANGE
    ):
        return False

    # Minimum total volume
    if (
        total_volume
        < MIN_TOTAL_VOLUME_USDT
    ):
        return False

    return True


# ============================================================
# FIND SIGNAL WINDOW
# ============================================================

def find_signal(
    symbol
):

    with state_lock:

        data = list(
            candles.get(
                symbol,
                []
            )
        )

    if len(data) < 2:
        return None

    # --------------------------------------------------------
    # 1) LIVE 2-CANDLE CHECK
    # --------------------------------------------------------

    if LIVE_TWO_CANDLE_ENABLED:

        window_2 = data[-2:]

        if rules_match(
            window_2
        ):

            return (
                window_2,
                "LIVE 2-CANDLE"
            )

    # --------------------------------------------------------
    # 2) ROLLING 3-CANDLE CHECK
    #
    # 1-2-3
    # 2-3-4
    # 3-4-5
    # ...
    #
    # Current candle may still be OPEN.
    # --------------------------------------------------------

    if len(data) >= 3:

        start_index = max(
            0,
            len(data) - 5
        )

        for i in range(
            start_index,
            len(data) - 2
        ):

            window = data[
                i:i + 3
            ]

            if rules_match(
                window
            ):

                return (
                    window,
                    "ROLLING 3-CANDLE"
                )

    return None


# ============================================================
# ALERT
# ============================================================

def maybe_alert(
    symbol,
    reason="live"
):

    signal = find_signal(
        symbol
    )

    if signal is None:
        return

    window, window_type = signal

    with state_lock:

        info = tracked.get(
            symbol
        )

        if not info:
            return

        now = time.time()

        previous = last_alert.get(
            symbol,
            0
        )

        # Anti-spam
        if (
            now - previous
            < ALERT_COOLDOWN_SECONDS
        ):
            return

        last_alert[
            symbol
        ] = now

    price_change = calculate_price_change(
        window
    )

    total_volume = calculate_total_volume(
        window
    )

    first_price = window[0]["open"]

    last_price = window[-1]["close"]

    base = info["base"]

    rank = info["rank"]

    name = info["name"]

    text = (
        "🚨 PUMP SIGNAL\n\n"

        f"🪙 {base}/USDT\n"

        f"📌 {name}\n"

        f"🏆 CMC Rank: #{rank}\n\n"

        f"📈 Price: +{price_change:.2f}%\n"

        f"💰 Total Volume: "
        f"${total_volume:,.0f}\n\n"

        f"💵 Start: "
        f"{first_price:.8g}\n"

        f"💵 Current: "
        f"{last_price:.8g}\n\n"

        f"📊 Window: {window_type}\n"

        f"⚡ Trigger: {reason}"
    )

    print(
        "\n" + "=" * 50,
        flush=True
    )

    print(
        text,
        flush=True
    )

    print(
        "=" * 50 + "\n",
        flush=True
    )

    send_telegram(
        text
    )


# ============================================================
# BINANCE KLINE EVENT
# ============================================================

def process_kline(
    data
):

    kline = data.get(
        "k",
        {}
    )

    symbol = str(
        kline.get(
            "s",
            ""
        )
    ).upper()

    if not symbol:
        return

    with state_lock:

        if symbol not in tracked:
            return

        candle = {

            "start": int(
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

            "volume": float(
                kline["v"]
            ),

            # THIS IS USDT QUOTE VOLUME
            "quote_volume": float(
                kline["q"]
            ),

            "closed": bool(
                kline["x"]
            )
        }

        dq = candles[
            symbol
        ]

        # Current 5M candle update
        if (
            dq
            and dq[-1]["start"]
            == candle["start"]
        ):

            dq[-1] = candle

        else:

            dq.append(
                candle
            )

        while len(dq) > MAX_CANDLES:

            dq.popleft()

    # --------------------------------------------------------
    # IMPORTANT:
    # Check every live WebSocket update.
    #
    # So we DO NOT wait for the 5M candle to close.
    # --------------------------------------------------------

    maybe_alert(
        symbol,
        "LIVE 5M UPDATE"
    )


# ============================================================
# WEBSOCKET MESSAGE
# ============================================================

def ws_on_message(
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

        if (
            data.get("e")
            == "kline"
        ):

            process_kline(
                data
            )

    except Exception as e:

        print(
            "WS MESSAGE ERROR:",
            e,
            flush=True
        )


# ============================================================
# WEBSOCKET WORKER
# ============================================================

def ws_worker(
    symbols,
    worker_id
):

    streams = "/".join(
        f"{symbol.lower()}@kline_5m"
        for symbol in symbols
    )

    url = (
        BINANCE_WS
        + streams
    )

    while True:

        try:

            print(
                f"WS {worker_id}: "
                f"CONNECTING "
                f"({len(symbols)} streams)",
                flush=True
            )

            ws = websocket.WebSocketApp(

                url,

                on_message=lambda ws, msg:
                    ws_on_message(msg),

                on_error=lambda ws, error:
                    print(
                        f"WS {worker_id} ERROR:",
                        error,
                        flush=True
                    ),

                on_close=lambda ws,
                    code,
                    msg:
                    print(
                        f"WS {worker_id} CLOSED:",
                        code,
                        msg,
                        flush=True
                    )
            )

            ws.run_forever(
                ping_interval=15,
                ping_timeout=10
            )

        except Exception as e:

            print(
                f"WS {worker_id} EXCEPTION:",
                e,
                flush=True
            )

        print(
            f"WS {worker_id}: "
            "RECONNECTING IN 5 SEC",
            flush=True
        )

        time.sleep(5)


# ============================================================
# CMC REFRESH
# ============================================================

def cmc_refresh_loop():

    while True:

        time.sleep(
            CMC_REFRESH_SECONDS
        )

        try:

            print(
                "CMC REFRESH...",
                flush=True
            )

            rebuild_tracked()

            print(
                "CMC REFRESH FINISHED",
                flush=True
            )

        except Exception as e:

            print(
                "CMC REFRESH ERROR:",
                e,
                flush=True
            )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "\n"
        "========================================\n"
        "      MEME PUMP ALERT STARTING\n"
        "========================================",
        flush=True
    )

    print(
        f"CMC RANK: "
        f"{CMC_MIN_RANK}-{CMC_MAX_RANK}",
        flush=True
    )

    print(
        f"PRICE: >= "
        f"{MIN_TOTAL_PRICE_CHANGE}%",
        flush=True
    )

    print(
        f"VOLUME: >= "
        f"${MIN_TOTAL_VOLUME_USDT:,.0f}",
        flush=True
    )

    print(
        "MAX PRICE: DISABLED",
        flush=True
    )

    print(
        "LIVE 2 CANDLE: ENABLED",
        flush=True
    )

    print(
        "ROLLING 3 CANDLE: ENABLED",
        flush=True
    )

    print(
        "========================================\n",
        flush=True
    )


    # --------------------------------------------------------
    # CMC + BINANCE
    # --------------------------------------------------------

    symbols = rebuild_tracked()


    # --------------------------------------------------------
    # GET INITIAL 5M HISTORY
    # --------------------------------------------------------

    bootstrap_history(
        symbols
    )


    # --------------------------------------------------------
    # BINANCE MAX 1024 STREAMS / CONNECTION
    #
    # Use 900 to keep safety margin.
    # --------------------------------------------------------

    chunk_size = 900

    chunks = [
        symbols[i:i + chunk_size]
        for i in range(
            0,
            len(symbols),
            chunk_size
        )
    ]

    print(
        f"WEBSOCKET CONNECTIONS: "
        f"{len(chunks)}",
        flush=True
    )


    # --------------------------------------------------------
    # START WEBSOCKETS
    # --------------------------------------------------------

    for index, chunk in enumerate(
        chunks,
        start=1
    ):

        thread = threading.Thread(

            target=ws_worker,

            args=(
                chunk,
                index
            ),

            daemon=True
        )

        thread.start()

        time.sleep(1)


    # --------------------------------------------------------
    # CMC REFRESH THREAD
    # --------------------------------------------------------

    refresh_thread = threading.Thread(

        target=cmc_refresh_loop,

        daemon=True
    )

    refresh_thread.start()


    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    while True:

        time.sleep(60)

        with state_lock:

            ready = sum(
                1
                for item in candles.values()
                if len(item) >= 2
            )

            print(
                f"STATUS | "
                f"TRACKED={len(tracked)} | "
                f"HISTORY_READY={ready}",
                flush=True
            )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
