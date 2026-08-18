import os
import time
import json
import threading
from datetime import datetime, timezone

import requests
import websocket


# ============================================================
# CONFIG
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
CMC_API_KEY = os.getenv("CMC_API_KEY", "")

# 3 x 5M PRICE RULE
MIN_3CANDLE_PRICE_CHANGE = 5.0

# 3 x 5M VOLUME RULE
MIN_3CANDLE_VOLUME = 50_000

# CMC RANK
CMC_RANK_MIN = 1
CMC_RANK_MAX = 2000

# Scan interval
SCAN_INTERVAL = 30

# Binance
BINANCE_REST = "https://api.binance.com"
BINANCE_WS = "wss://stream.binance.com:9443/stream"

# ============================================================
# GLOBAL DATA
# ============================================================

coins = {}
ranks = {}

lock = threading.Lock()

alerted = set()

last_scan = 0
ws_connected = False


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM CONFIG MISSING")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    try:
        r = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=15,
        )

        if r.status_code == 200:
            print("TELEGRAM SENT")
            return True

        print("TELEGRAM ERROR:", r.status_code, r.text[:300])
        return False

    except Exception as e:
        print("TELEGRAM EXCEPTION:", e)
        return False


# ============================================================
# CMC RANKS
# ============================================================

def load_cmc_ranks():
    global ranks

    if not CMC_API_KEY:
        print("CMC_API_KEY MISSING")
        return

    print("CMC: loading rankings...")

    url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"

    headers = {
        "X-CMC_PRO_API_KEY": CMC_API_KEY
    }

    params = {
        "start": 1,
        "limit": 2000,
        "convert": "USD",
    }

    try:
        r = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=30,
        )

        r.raise_for_status()

        data = r.json().get("data", [])

        new_ranks = {}

        for coin in data:
            symbol = coin.get("symbol", "").upper()
            rank = coin.get("cmc_rank")

            if symbol and rank:
                # Keep first occurrence of a symbol
                if symbol not in new_ranks:
                    new_ranks[symbol] = int(rank)

        ranks = new_ranks

        print(f"CMC RANKS LOADED: {len(ranks)}")

    except Exception as e:
        print("CMC ERROR:", e)


# ============================================================
# BINANCE SYMBOLS
# ============================================================

def get_binance_symbols():
    print("BINANCE: loading USDT spot symbols...")

    url = f"{BINANCE_REST}/api/v3/exchangeInfo"

    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()

        data = r.json()

        result = []

        for s in data.get("symbols", []):

            if s.get("status") != "TRADING":
                continue

            if s.get("quoteAsset") != "USDT":
                continue

            if s.get("isSpotTradingAllowed") is not True:
                continue

            symbol = s.get("symbol")

            base = s.get("baseAsset", "").upper()

            # CMC rank filtering
            rank = ranks.get(base)

            if rank is None:
                continue

            if not (CMC_RANK_MIN <= rank <= CMC_RANK_MAX):
                continue

            result.append(symbol)

        print(f"BINANCE USDT SPOT: {len(result)}")
        print(f"TRACKED COINS: {len(result)}")

        return result

    except Exception as e:
        print("BINANCE SYMBOL ERROR:", e)
        return []


# ============================================================
# HISTORICAL 5M CANDLES
# ============================================================

def load_history(symbols):

    print(f"BOOTSTRAP START: {len(symbols)} coins")

    total = len(symbols)

    for i, symbol in enumerate(symbols, 1):

        try:

            url = f"{BINANCE_REST}/api/v3/klines"

            params = {
                "symbol": symbol,
                "interval": "5m",
                "limit": 3,
            }

            r = requests.get(
                url,
                params=params,
                timeout=10,
            )

            if r.status_code != 200:
                continue

            data = r.json()

            candles = []

            for k in data:

                candles.append({
                    "open_time": int(k[0]),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                    "close_time": int(k[6]),
                })

            with lock:
                coins[symbol] = candles

            if i % 100 == 0:
                print(f"BOOTSTRAP: {i}/{total}")

            # Small delay to avoid hammering REST API
            time.sleep(0.02)

        except Exception as e:
            print(f"HISTORY ERROR {symbol}: {e}")

    print("BOOTSTRAP FINISHED")


# ============================================================
# CANDLE UPDATE
# ============================================================

def update_candle(symbol, k):

    candle = {
        "open_time": int(k["t"]),
        "open": float(k["o"]),
        "high": float(k["h"]),
        "low": float(k["l"]),
        "close": float(k["c"]),
        "volume": float(k["v"]),
        "close_time": int(k["T"]),
    }

    with lock:

        if symbol not in coins:
            coins[symbol] = []

        arr = coins[symbol]

        if arr and arr[-1]["open_time"] == candle["open_time"]:

            # LIVE UPDATE OF CURRENT 5M CANDLE
            arr[-1] = candle

        else:

            arr.append(candle)

        # Keep only latest candles
        coins[symbol] = arr[-5:]


# ============================================================
# 3 x 5M CONDITION
# ============================================================

def check_signal(symbol):

    with lock:

        candles = list(coins.get(symbol, []))

    if len(candles) < 3:
        return None

    # Last 3 candles
    c1 = candles[-3]
    c2 = candles[-2]
    c3 = candles[-1]

    # --------------------------------------------------------
    # PRICE
    # Overall price change:
    # first candle OPEN -> current third candle CLOSE
    # --------------------------------------------------------

    first_open = c1["open"]
    current_close = c3["close"]

    if first_open <= 0:
        return None

    price_change = ((current_close / first_open) - 1) * 100

    if price_change < MIN_3CANDLE_PRICE_CHANGE:
        return None

    # --------------------------------------------------------
    # VOLUME
    # Sum of all 3 x 5M candles
    # --------------------------------------------------------

    total_volume = (
        c1["volume"] +
        c2["volume"] +
        c3["volume"]
    )

    if total_volume < MIN_3CANDLE_VOLUME:
        return None

    # --------------------------------------------------------
    # SIGNAL ID
    # One alert per 3-candle window
    # --------------------------------------------------------

    window_id = f"{symbol}:{c1['open_time']}:{c3['open_time']}"

    if window_id in alerted:
        return None

    return {
        "symbol": symbol,
        "price_change": price_change,
        "volume": total_volume,
        "price": current_close,
        "window_id": window_id,
        "c1": c1,
        "c2": c2,
        "c3": c3,
    }


# ============================================================
# SEND SIGNAL
# ============================================================

def send_signal(signal):

    symbol = signal["symbol"]

    rank = ranks.get(
        symbol.replace("USDT", ""),
        "?"
    )

    price_change = signal["price_change"]
    volume = signal["volume"]
    price = signal["price"]

    c1 = signal["c1"]
    c2 = signal["c2"]
    c3 = signal["c3"]

    # Mark BEFORE sending to prevent duplicates
    alerted.add(signal["window_id"])

    message = (
        "🚨 BINANCE 3×5M EARLY MOMENTUM\n\n"

        f"🪙 {symbol}\n"
        f"🏆 CMC Rank: #{rank}\n\n"

        f"📈 3×5M PRICE: +{price_change:.2f}%\n"
        f"💰 3×5M VOLUME: ${volume:,.0f}\n"
        f"💵 Current Price: {price:.10g}\n\n"

        "🕯 5M candles:\n"
        f"1️⃣ +{((c1['close']/c1['open'])-1)*100:.2f}%\n"
        f"2️⃣ +{((c2['close']/c2['open'])-1)*100:.2f}%\n"
        f"3️⃣ +{((c3['close']/c3['open'])-1)*100:.2f}%\n\n"

        "⚡ LIVE 3×5M momentum detected\n"
        "🔵 Binance Spot"
    )

    print("\n" + "=" * 50)
    print(message)
    print("=" * 50 + "\n")

    telegram_send(message)


# ============================================================
# SCANNER
# ============================================================

def scanner_loop():

    global last_scan

    while True:

        try:

            now = time.time()

            if now - last_scan >= SCAN_INTERVAL:

                last_scan = now

                checked = 0
                signals = 0

                with lock:
                    symbols = list(coins.keys())

                for symbol in symbols:

                    checked += 1

                    signal = check_signal(symbol)

                    if signal:

                        signals += 1
                        send_signal(signal)

                print(
                    f"SCAN | TRACKED={len(symbols)} "
                    f"| CHECKED={checked} "
                    f"| SIGNALS={signals} "
                    f"| WS={ws_connected}"
                )

            time.sleep(1)

        except Exception as e:

            print("SCANNER ERROR:", e)
            time.sleep(5)


# ============================================================
# WEBSOCKET
# ============================================================

def websocket_message(ws, message):

    try:

        data = json.loads(message)

        payload = data.get("data", data)

        stream = data.get("stream", "")

        if payload.get("e") != "kline":
            return

        k = payload.get("k")

        if not k:
            return

        symbol = k["s"].upper()

        update_candle(symbol, k)

    except Exception as e:

        print("WS MESSAGE ERROR:", e)


def websocket_open(ws):

    global ws_connected

    ws_connected = True

    print("WEBSOCKET CONNECTED")

    telegram_send(
        "🟢 MEME PUMP ALERT ACTIVE\n\n"
        "Binance canlı WebSocket bağlantısı hazırdır.\n"
        "3×5M early momentum scanner işləyir."
    )


def websocket_close(ws, close_status_code, close_msg):

    global ws_connected

    ws_connected = False

    print(
        "WEBSOCKET CLOSED:",
        close_status_code,
        close_msg
    )


def websocket_error(ws, error):

    print("WEBSOCKET ERROR:", error)


# ============================================================
# WEBSOCKET LOOP
# ============================================================

def websocket_loop(symbols):

    global ws_connected

    # Binance stream names
    streams = []

    for symbol in symbols:

        streams.append(
            f"{symbol.lower()}@kline_5m"
        )

    # Binance combined stream URL
    url = (
        BINANCE_WS
        + "?streams="
        + "/".join(streams)
    )

    print(
        f"WS 1: CONNECTING ({len(streams)} streams)"
    )

    while True:

        try:

            ws = websocket.WebSocketApp(
                url,
                on_open=websocket_open,
                on_message=websocket_message,
                on_error=websocket_error,
                on_close=websocket_close,
            )

            ws.run_forever(
                ping_interval=20,
                ping_timeout=10,
            )

        except Exception as e:

            ws_connected = False

            print("WS LOOP ERROR:", e)

        print("WEBSOCKET RECONNECTING IN 5 SECONDS...")

        time.sleep(5)


# ============================================================
# STARTUP
# ============================================================

def main():

    print("=" * 60)
    print("MEME PUMP ALERT STARTING")
    print("=" * 60)

    print()
    print("RULES:")
    print("CMC RANK: 1-2000")
    print("PRICE: 3 x 5M >= +5.0%")
    print("VOLUME: 3 x 5M >= $50,000")
    print("MAX PRICE: DISABLED")
    print("LIVE 3RD CANDLE: ENABLED")
    print("SCAN: EVERY 30 SECONDS")
    print("REPEAT ALERT: DISABLED")
    print()

    # Telegram test
    telegram_send(
        "✅ MEME PUMP ALERT STARTED\n\n"
        "Bot Telegram bağlantısı işləyir.\n"
        "Binance canlı WebSocket bağlantıları hazırlanır."
    )

    # CMC
    load_cmc_ranks()

    # Binance
    symbols = get_binance_symbols()

    if not symbols:
        print("NO SYMBOLS FOUND")
        return

    # History
    load_history(symbols)

    print(
        f"STATUS | TRACKED={len(coins)} "
        f"| HISTORY_READY={len(coins)}"
    )

    # Scanner thread
    scanner = threading.Thread(
        target=scanner_loop,
        daemon=True,
    )

    scanner.start()

    # WebSocket
    websocket_loop(symbols)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
