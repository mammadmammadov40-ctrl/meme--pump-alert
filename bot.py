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
CMC_REFRESH_SECONDS = 1800

MIN_PRICE_CHANGE = 5.0
MIN_TOTAL_VOLUME = 50_000.0

WS_CHUNK_SIZE = 100
MAX_CANDLES = 12
RECONNECT_SECONDS = 3

coins = {}
cmc_ranks = {}
history = defaultdict(lambda: deque(maxlen=MAX_CANDLES))
data_lock = threading.RLock()

# One signal per rise cycle.
# After a signal, the coin is blocked until its price goes
# strictly below the last signal price.
signal_state = {}
window_alerted = set()


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
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        if response.status_code == 200:
            print("TELEGRAM SENT", flush=True)
            return True

        print(
            "TELEGRAM ERROR:",
            response.status_code,
            response.text[:500],
            flush=True,
        )
        return False
    except Exception as e:
        print("TELEGRAM EXCEPTION:", e, flush=True)
        return False


def load_cmc():
    if not CMC_API_KEY:
        print("CMC_API_KEY MISSING", flush=True)
        return False

    print("CMC: LOADING TOP 2000...", flush=True)

    try:
        response = requests.get(
            "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest",
            headers={
                "X-CMC_PRO_API_KEY": CMC_API_KEY,
                "Accept": "application/json",
            },
            params={
                "start": 1,
                "limit": 2000,
                "convert": "USD",
                "sort": "market_cap",
                "sort_dir": "desc",
            },
            timeout=30,
        )
        response.raise_for_status()

        new_ranks = {}
        for coin in response.json().get("data", []):
            symbol = str(coin.get("symbol", "")).upper()
            rank = coin.get("cmc_rank")
            if not symbol or rank is None:
                continue
            rank = int(rank)
            if CMC_MIN_RANK <= rank <= CMC_MAX_RANK:
                new_ranks.setdefault(symbol, rank)

        with data_lock:
            cmc_ranks.clear()
            cmc_ranks.update(new_ranks)

            # A coin that leaves Top 2000 becomes ineligible immediately.
            for symbol, info in list(coins.items()):
                if info["base"] not in cmc_ranks:
                    coins.pop(symbol, None)

        print(
            f"CMC COINS: {len(new_ranks)} "
            f"(RANK {CMC_MIN_RANK}-{CMC_MAX_RANK})",
            flush=True,
        )
        return True

    except Exception as e:
        print("CMC ERROR:", e, flush=True)
        return False


def cmc_refresh_worker():
    while True:
        time.sleep(CMC_REFRESH_SECONDS)
        load_cmc()


def load_binance_symbols():
    print("BINANCE: LOADING USDT SPOT...", flush=True)

    try:
        response = requests.get(
            f"{BINANCE_REST}/api/v3/exchangeInfo",
            timeout=30,
        )
        response.raise_for_status()

        with data_lock:
            ranks = dict(cmc_ranks)

        result = {}
        for item in response.json().get("symbols", []):
            if item.get("status") != "TRADING":
                continue
            if item.get("quoteAsset") != "USDT":
                continue
            if item.get("isSpotTradingAllowed") is False:
                continue

            symbol = str(item.get("symbol", "")).upper()
            base = str(item.get("baseAsset", "")).upper()
            rank = ranks.get(base)

            if not symbol or not base or rank is None:
                continue
            if not (CMC_MIN_RANK <= rank <= CMC_MAX_RANK):
                continue
            if base.endswith(("UP", "DOWN", "BULL", "BEAR")):
                continue

            result[symbol] = {"base": base, "rank": rank, "name": base}

        with data_lock:
            coins.clear()
            coins.update(result)
            for symbol in result:
                signal_state.setdefault(
                    symbol, {"active": True, "signal_price": None}
                )

        print(f"BINANCE USDT SPOT: {len(result)}", flush=True)
        print(f"TRACKED COINS: {len(result)}", flush=True)
        return list(result.keys())

    except Exception as e:
        print("BINANCE SYMBOL ERROR:", e, flush=True)
        return []


def load_history(symbols):
    print(f"BOOTSTRAP START: {len(symbols)} coins", flush=True)
    ready = 0

    for index, symbol in enumerate(symbols, start=1):
        try:
            response = requests.get(
                f"{BINANCE_REST}/api/v3/klines",
                params={"symbol": symbol, "interval": INTERVAL, "limit": 8},
                timeout=10,
            )
            if response.status_code != 200:
                continue

            with data_lock:
                history[symbol].clear()
                for row in response.json():
                    history[symbol].append({
                        "open_time": int(row[0]),
                        "open": float(row[1]),
                        "high": float(row[2]),
                        "low": float(row[3]),
                        "close": float(row[4]),
                        "base_volume": float(row[5]),
                        "close_time": int(row[6]),
                        "quote_volume": float(row[7]),
                        "closed": True,
                    })

            ready += 1
            if index % 100 == 0:
                print(f"BOOTSTRAP: {index}/{len(symbols)}", flush=True)
            time.sleep(0.03)

        except Exception as e:
            print(f"HISTORY ERROR {symbol}: {e}", flush=True)

    print(f"BOOTSTRAP FINISHED HISTORY_READY={ready}", flush=True)


def update_activation_state(symbol, current_price):
    with data_lock:
        state = signal_state.setdefault(
            symbol, {"active": True, "signal_price": None}
        )
        last_signal_price = state["signal_price"]

        if (
            not state["active"]
            and last_signal_price is not None
            and current_price < last_signal_price
        ):
            state["active"] = True
            print(
                f"RE-ACTIVATED {symbol}: "
                f"{current_price:.10g} < {last_signal_price:.10g}",
                flush=True,
            )


def mark_signal(symbol, signal_price):
    with data_lock:
        state = signal_state.setdefault(
            symbol, {"active": True, "signal_price": None}
        )
        state["active"] = False
        state["signal_price"] = signal_price


def update_live_candle(symbol, kline):
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
        if candles and candles[-1]["open_time"] == candle["open_time"]:
            candles[-1] = candle
        else:
            candles.append(candle)


def evaluate_window(symbol, candles):
    """
    Early rolling logic:
      1-2: if rules are met, signal immediately.
      1-2-3: if 1-2 did not meet, check the 3-candle window.
      Then the window advances:
      2-3, then 2-3-4, then 3-4, then 3-4-5, etc.

    A blocked coin cannot signal again until its current price is
    below its previous signal price.
    """
    if len(candles) < 2:
        return None

    with data_lock:
        info = coins.get(symbol)
        state = signal_state.get(
            symbol, {"active": True, "signal_price": None}
        )

    if info is None or not state["active"]:
        return None

    # 2-candle early trigger.
    c1, c2 = candles[-2], candles[-1]
    if c1["open"] <= 0:
        return None

    change_2 = ((c2["close"] / c1["open"]) - 1.0) * 100.0
    volume_2 = c1["quote_volume"] + c2["quote_volume"]

    id2 = f"{symbol}:2:{c1['open_time']}:{c2['open_time']}"

    if (
        change_2 >= MIN_PRICE_CHANGE
        and volume_2 >= MIN_TOTAL_VOLUME
        and id2 not in window_alerted
    ):
        window_alerted.add(id2)
        return {
            "symbol": symbol,
            "rank": info["rank"],
            "price": c2["close"],
            "price_change": change_2,
            "volume": volume_2,
            "window_candles": [c1, c2],
            "signal_candle_index": 2,
            "live": not c2["closed"],
        }

    # 3-candle fallback.
    if len(candles) < 3:
        return None

    c1, c2, c3 = candles[-3], candles[-2], candles[-1]
    if c1["open"] <= 0:
        return None

    change_3 = ((c3["close"] / c1["open"]) - 1.0) * 100.0
    volume_3 = (
        c1["quote_volume"]
        + c2["quote_volume"]
        + c3["quote_volume"]
    )

    id3 = (
        f"{symbol}:3:{c1['open_time']}:"
        f"{c2['open_time']}:{c3['open_time']}"
    )

    if (
        change_3 >= MIN_PRICE_CHANGE
        and volume_3 >= MIN_TOTAL_VOLUME
        and id3 not in window_alerted
    ):
        window_alerted.add(id3)
        return {
            "symbol": symbol,
            "rank": info["rank"],
            "price": c3["close"],
            "price_change": change_3,
            "volume": volume_3,
            "window_candles": [c1, c2, c3],
            "signal_candle_index": 3,
            "live": not c3["closed"],
        }

    return None


def candle_percent(candle):
    if candle["open"] <= 0:
        return 0.0
    return ((candle["close"] / candle["open"]) - 1.0) * 100.0


def send_signal(signal):
    symbol = signal["symbol"]
    mark_signal(symbol, signal["price"])

    candles = signal["window_candles"]
    first_open = candles[0]["open"]

    message = (
        "🚨 PUMP SIGNAL\n\n"
        f"🪙 {symbol}\n"
        f"🏆 CMC Rank: #{signal['rank']}\n\n"
        f"🕯️ Başlanğıc qiyməti: {first_open:.10g}\n"
        f"⚡ Siqnal qiyməti: {signal['price']:.10g}\n"
        f"📈 Artım: +{signal['price_change']:.2f}%\n"
        f"📍 Siqnal: {signal['signal_candle_index']}-ci şamda\n"
        f"💰 Volume: ${signal['volume']:,.0f}\n\n"
        + "\n".join(
            f"🕯️ {i}-ci şam: {candle_percent(c):+.2f}%"
            for i, c in enumerate(candles, start=1)
        )
        + "\n\n"
        + (
            "🟢 LIVE — şam hələ bağlanmayıb"
            if signal["live"]
            else "🔵 Şam bağlanıb"
        )
        + "\n⚡ Rolling 3×5M\n"
        "📊 Binance Spot\n"
        "🎯 Şərt: ≥5% + ≥$50K\n"
        "🔒 Yenidən siqnal üçün qiymət əvvəlki siqnal "
        "qiymətindən aşağı düşməlidir."
    )

    print("\n" + "=" * 70, flush=True)
    print(message, flush=True)
    print("=" * 70 + "\n", flush=True)
    send_telegram(message)


def process_signal_immediately(symbol):
    with data_lock:
        candles = list(history.get(symbol, []))

    if len(candles) < 2:
        return

    update_activation_state(symbol, candles[-1]["close"])
    signal = evaluate_window(symbol, candles)

    if signal is not None:
        threading.Thread(
            target=send_signal,
            args=(signal,),
            daemon=True,
        ).start()


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
    print(
        f"WS {worker_id}: CONNECTED ({len(symbols)} streams)",
        flush=True,
    )

    params = [f"{s.lower()}@kline_5m" for s in symbols]
    ws.send(json.dumps({
        "method": "SUBSCRIBE",
        "params": params,
        "id": worker_id,
    }))

    print(
        f"WS {worker_id}: LIVE 5M STREAMS ACTIVE",
        flush=True,
    )


def websocket_close(ws, code, message, worker_id):
    print(
        f"WS {worker_id}: CLOSED code={code} message={message}",
        flush=True,
    )


def websocket_error(ws, error, worker_id):
    print(f"WS {worker_id}: ERROR {error}", flush=True)


def websocket_worker(symbols, worker_id):
    while True:
        try:
            print(
                f"WS {worker_id}: CONNECTING "
                f"({len(symbols)} streams)",
                flush=True,
            )

            ws = websocket.WebSocketApp(
                BINANCE_WS,
                on_open=lambda ws: websocket_open(
                    ws, worker_id, symbols
                ),
                on_message=websocket_message,
                on_error=lambda ws, error: websocket_error(
                    ws, error, worker_id
                ),
                on_close=lambda ws, code, message: websocket_close(
                    ws, code, message, worker_id
                ),
            )

            ws.run_forever(
                ping_interval=20,
                ping_timeout=10,
            )

        except Exception as e:
            print(
                f"WS {worker_id}: EXCEPTION {e}",
                flush=True,
            )

        print(
            f"WS {worker_id}: RECONNECTING IN "
            f"{RECONNECT_SECONDS}s",
            flush=True,
        )
        time.sleep(RECONNECT_SECONDS)


def start_websockets(symbols):
    chunks = [
        symbols[i:i + WS_CHUNK_SIZE]
        for i in range(0, len(symbols), WS_CHUNK_SIZE)
    ]

    print(
        f"WEBSOCKET CONNECTIONS: {len(chunks)}",
        flush=True,
    )

    for worker_id, chunk in enumerate(chunks, start=1):
        threading.Thread(
            target=websocket_worker,
            args=(chunk, worker_id),
            daemon=True,
        ).start()
        time.sleep(1)


def main():
    print(
        "\n"
        "========================================\n"
        "      FAST MEME PUMP ALERT STARTING\n"
        "========================================",
        flush=True,
    )
    print(f"CMC RANK: {CMC_MIN_RANK}-{CMC_MAX_RANK}", flush=True)
    print("ROLLING: 2-CANDLE EARLY + 3-CANDLE FALLBACK", flush=True)
    print(f"MIN PRICE: +{MIN_PRICE_CHANGE}%", flush=True)
    print(
        f"MIN TOTAL USDT VOLUME: ${MIN_TOTAL_VOLUME:,.0f}",
        flush=True,
    )
    print("MAX PRICE LIMIT: NONE", flush=True)
    print("LIVE CANDLE: ENABLED", flush=True)
    print("SCAN DELAY: NONE", flush=True)
    print(
        "RE-SIGNAL: ONLY AFTER PRICE DROPS "
        "BELOW LAST SIGNAL PRICE",
        flush=True,
    )

    send_telegram(
        "✅ FAST MEME PUMP ALERT STARTED\n\n"
        "Rolling 3×5M LIVE scanner aktivdir.\n"
        "1-2 şam şərtləri ödəyərsə 3-cü şam gözlənilmir."
    )

    if not load_cmc():
        return

    symbols = load_binance_symbols()
    if not symbols:
        print("NO TRACKED COINS", flush=True)
        return

    load_history(symbols)

    threading.Thread(
        target=cmc_refresh_worker,
        daemon=True,
    ).start()

    start_websockets(symbols)

    while True:
        with data_lock:
            tracked = len(coins)
            ready = sum(
                1 for symbol in coins
                if len(history.get(symbol, [])) >= 2
            )
            active = sum(
                1 for symbol in coins
                if signal_state.get(
                    symbol, {"active": True}
                )["active"]
            )

        print(
            f"STATUS | TRACKED={tracked} | "
            f"HISTORY_READY={ready} | "
            f"ACTIVE={active} | ROLLING=LIVE",
            flush=True,
        )
        time.sleep(60)


if __name__ == "__main__":
    main()
