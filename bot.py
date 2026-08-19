import os
import time
import json
import threading
from collections import defaultdict, deque

import requests
import websocket

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

coins = {}
cmc_ranks = {}
history = defaultdict(lambda: deque(maxlen=MAX_CANDLES))
alerted_windows = set()
data_lock = threading.RLock()
alert_lock = threading.Lock()


def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN MISSING", flush=True)
        return False
    if not TELEGRAM_CHAT_ID:
        print("TELEGRAM_CHAT_ID MISSING", flush=True)
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        response = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "disable_web_page_preview": True},
            timeout=10,
        )
        if response.status_code == 200:
            print("TELEGRAM SENT", flush=True)
            return True
        print("TELEGRAM ERROR:", response.status_code,
              response.text[:500], flush=True)
        return False
    except Exception as e:
        print("TELEGRAM EXCEPTION:", e, flush=True)
        return False


def load_cmc():
    if not CMC_API_KEY:
        print("CMC_API_KEY MISSING", flush=True)
        return False
    print("CMC: LOADING TOP 2000...", flush=True)
    url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
    headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY, "Accept": "application/json"}
    params = {"start": 1, "limit": 2000, "convert": "USD",
              "sort": "market_cap", "sort_dir": "desc"}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        data = response.json().get("data", [])
        new_ranks = {}
        for coin in data:
            symbol = str(coin.get("symbol", "")).upper()
            rank = coin.get("cmc_rank")
            if not symbol or rank is None:
                continue
            rank = int(rank)
            if CMC_MIN_RANK <= rank <= CMC_MAX_RANK and symbol not in new_ranks:
                new_ranks[symbol] = rank
        with data_lock:
            cmc_ranks.clear()
            cmc_ranks.update(new_ranks)
        print(f"CMC COINS: {len(new_ranks)} (RANK {CMC_MIN_RANK}-{CMC_MAX_RANK})", flush=True)
        return True
    except Exception as e:
        print("CMC ERROR:", e, flush=True)
        return False


def load_binance_symbols():
    print("BINANCE: LOADING USDT SPOT...", flush=True)
    url = f"{BINANCE_REST}/api/v3/exchangeInfo"
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
        result = {}
        with data_lock:
            ranks = dict(cmc_ranks)
        for item in data.get("symbols", []):
            if item.get("status") != "TRADING":
                continue
            if item.get("quoteAsset") != "USDT":
                continue
            if item.get("isSpotTradingAllowed") is False:
                continue
            symbol = str(item.get("symbol", "")).upper()
            base = str(item.get("baseAsset", "")).upper()
            if not symbol or not base:
                continue
            rank = ranks.get(base)
            if rank is None or not (CMC_MIN_RANK <= rank <= CMC_MAX_RANK):
                continue
            if base.endswith(("UP", "DOWN", "BULL", "BEAR")):
                continue
            result[symbol] = {"base": base, "rank": rank, "name": base}
        with data_lock:
            coins.clear()
            coins.update(result)
        print(f"BINANCE USDT SPOT: {len(result)}", flush=True)
        print(f"TRACKED COINS: {len(result)}", flush=True)
        return list(result.keys())
    except Exception as e:
        print("BINANCE SYMBOL ERROR:", e, flush=True)
        return []


def row_to_candle(row):
    return {
        "open_time": int(row[0]), "open": float(row[1]),
        "high": float(row[2]), "low": float(row[3]),
        "close": float(row[4]), "base_volume": float(row[5]),
        "close_time": int(row[6]), "quote_volume": float(row[7]),
        "closed": True,
    }


def load_history(symbols):
    print(f"BOOTSTRAP START: {len(symbols)} coins", flush=True)
    ready = 0
    url = f"{BINANCE_REST}/api/v3/klines"
    for index, symbol in enumerate(symbols, start=1):
        try:
            response = requests.get(
                url, params={"symbol": symbol, "interval": INTERVAL, "limit": 5}, timeout=10
            )
            if response.status_code != 200:
                continue
            rows = response.json()
            with data_lock:
                history[symbol].clear()
                for row in rows:
                    history[symbol].append(row_to_candle(row))
            if len(rows) >= 3:
                ready += 1
            if index % 100 == 0:
                print(f"BOOTSTRAP: {index}/{len(symbols)}", flush=True)
            time.sleep(0.03)
        except Exception as e:
            print(f"HISTORY ERROR {symbol}: {e}", flush=True)
    print(f"BOOTSTRAP FINISHED HISTORY_READY={ready}", flush=True)


def update_live_candle(symbol, kline):
    candle = {
        "open_time": int(kline["t"]), "open": float(kline["o"]),
        "high": float(kline["h"]), "low": float(kline["l"]),
        "close": float(kline["c"]), "base_volume": float(kline["v"]),
        "close_time": int(kline["T"]), "quote_volume": float(kline["q"]),
        "closed": bool(kline["x"]),
    }
    with data_lock:
        if symbol not in coins:
            return
        candles = history[symbol]
        if candles and candles[-1]["open_time"] == candle["open_time"]:
            candles[-1] = candle
        else:
            candles.append(candle)


def check_rolling_3(symbol):
    with data_lock:
        candles = list(history.get(symbol, []))
        info = coins.get(symbol)
    if info is None or len(candles) < 3:
        return None

    c1, c2, c3 = candles[-3], candles[-2], candles[-1]
    first_open = c1["open"]
    current_price = c3["close"]
    if first_open <= 0:
        return None

    price_change = ((current_price / first_open) - 1.0) * 100.0
    total_volume = c1["quote_volume"] + c2["quote_volume"] + c3["quote_volume"]

    if price_change < MIN_PRICE_CHANGE or total_volume < MIN_TOTAL_VOLUME:
        return None

    window_id = f"{symbol}:{c1['open_time']}:{c2['open_time']}:{c3['open_time']}"
    with alert_lock:
        if window_id in alerted_windows:
            return None
        alerted_windows.add(window_id)

    return {
        "symbol": symbol, "rank": info["rank"], "price": current_price,
        "price_change": price_change, "volume": total_volume,
        "window_id": window_id, "c1": c1, "c2": c2, "c3": c3,
    }


def send_signal(signal):
    symbol = signal["symbol"]
    rank = signal["rank"]
    price = signal["price"]
    price_change = signal["price_change"]
    total_volume = signal["volume"]
    c1, c2, c3 = signal["c1"], signal["c2"], signal["c3"]

    def candle_percent(c):
        return ((c["close"] / c["open"]) - 1) * 100 if c["open"] > 0 else 0.0

    p1, p2, p3 = candle_percent(c1), candle_percent(c2), candle_percent(c3)
    live_status = ("🟢 LIVE / 3-cü şam hələ bağlanmayıb"
                   if not c3.get("closed", False)
                   else "🔵 3-cü şam bağlanıb")

    message = (
        "🚨 PUMP SIGNAL\n\n"
        f"🪙 {symbol}\n"
        f"🏆 CMC Rank: #{rank}\n\n"
        f"📈 3×5M ÜMUMİ: +{price_change:.2f}%\n"
        f"💰 3×5M ÜMUMİ VOLUME: ${total_volume:,.0f}\n\n"
        f"🕯️ 1-ci şam: {p1:+.2f}%\n"
        f"🕯️ 2-ci şam: {p2:+.2f}%\n"
        f"🕯️ 3-cü şam: {p3:+.2f}%\n\n"
        f"💵 Cari qiymət: {price:.10g}\n\n"
        f"{live_status}\n"
        "⚡ Rolling 3×5M\n"
        "📊 Binance Spot\n"
        "🎯 Şərt: ≥5% + ≥$50K"
    )
    print("\n" + "=" * 70, flush=True)
    print(message, flush=True)
    print("=" * 70 + "\n", flush=True)
    send_telegram(message)


def process_signal_immediately(symbol):
    signal = check_rolling_3(symbol)
    if signal is None:
        return
    threading.Thread(target=send_signal, args=(signal,), daemon=True).start()


def websocket_message(ws, message):
    try:
        payload = json.loads(message)
        data = payload.get("data", payload)
        if data.get("e") != "kline":
            return
        kline = data.get("k")
        if not kline:
            return
        symbol = str(kline.get("s", "")).upper()
        if not symbol:
            return
        update_live_candle(symbol, kline)
        process_signal_immediately(symbol)
    except Exception as e:
        print("WS MESSAGE ERROR:", e, flush=True)


def websocket_open(ws, worker_id, symbols):
    print(f"WS {worker_id}: CONNECTED ({len(symbols)} streams)", flush=True)
    streams = [f"{s.lower()}@kline_5m" for s in symbols]
    ws.send(json.dumps({"method": "SUBSCRIBE", "params": streams, "id": worker_id}))
    print(f"WS {worker_id}: LIVE 5M STREAMS ACTIVE", flush=True)


def websocket_close(ws, code, message, worker_id):
    print(f"WS {worker_id}: CLOSED code={code} message={message}", flush=True)


def websocket_error(ws, error, worker_id):
    print(f"WS {worker_id}: ERROR {error}", flush=True)


def websocket_worker(symbols, worker_id):
    while True:
        try:
            print(f"WS {worker_id}: CONNECTING ({len(symbols)} streams)", flush=True)
            ws = websocket.WebSocketApp(
                BINANCE_WS,
                on_open=lambda ws: websocket_open(ws, worker_id, symbols),
                on_message=websocket_message,
                on_error=lambda ws, error: websocket_error(ws, error, worker_id),
                on_close=lambda ws, code, message: websocket_close(ws, code, message, worker_id),
            )
            ws.run_forever(ping_interval=20, ping_timeout=10)
        except Exception as e:
            print(f"WS {worker_id}: EXCEPTION {e}", flush=True)
        print(f"WS {worker_id}: RECONNECTING IN {RECONNECT_SECONDS}s", flush=True)
        time.sleep(RECONNECT_SECONDS)


def start_websockets(symbols):
    chunks = [symbols[i:i + WS_CHUNK_SIZE] for i in range(0, len(symbols), WS_CHUNK_SIZE)]
    print(f"WEBSOCKET CONNECTIONS: {len(chunks)}", flush=True)
    for worker_id, chunk in enumerate(chunks, start=1):
        threading.Thread(target=websocket_worker, args=(chunk, worker_id), daemon=True).start()
        time.sleep(1)


def main():
    print("\n========================================\n       FAST MEME PUMP ALERT STARTING\n========================================", flush=True)
    print(f"CMC RANK: {CMC_MIN_RANK}-{CMC_MAX_RANK}", flush=True)
    print(f"ROLLING WINDOW: 3 x {INTERVAL}", flush=True)
    print(f"MIN PRICE: +{MIN_PRICE_CHANGE}%", flush=True)
    print(f"MIN TOTAL USDT VOLUME: ${MIN_TOTAL_VOLUME:,.0f}", flush=True)
    print("MAX PRICE LIMIT: NONE", flush=True)
    print("LIVE 3RD CANDLE: ENABLED", flush=True)
    print("SCAN DELAY: NONE", flush=True)
    print("WEBSOCKET MODE: SUBSCRIBE", flush=True)
    print("========================================\n", flush=True)

    send_telegram(
        "✅ FAST MEME PUMP ALERT STARTED\n\n"
        "Telegram bağlantısı işləyir.\n"
        "Rolling 3×5M LIVE scanner aktivdir.\n"
        "3-cü şam bağlanmadan canlı yoxlanılır."
    )

    if not load_cmc():
        print("CMC LOAD FAILED", flush=True)
        return

    symbols = load_binance_symbols()
    if not symbols:
        print("NO TRACKED COINS", flush=True)
        return

    load_history(symbols)
    start_websockets(symbols)

    while True:
        with data_lock:
            tracked_count = len(coins)
            history_ready = sum(1 for symbol in coins if len(history.get(symbol, [])) >= 3)
        with alert_lock:
            alert_count = len(alerted_windows)
        print(
            f"STATUS | TRACKED={tracked_count} | HISTORY_READY={history_ready} | "
            f"ROLLING=LIVE | ALERTS={alert_count}",
            flush=True,
        )
        time.sleep(STATUS_INTERVAL)


if __name__ == "__main__":
    main()
