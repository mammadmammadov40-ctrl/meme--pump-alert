import os
import time
import json
import asyncio
import requests
import websockets


# ============================================================
# ENV
# ============================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]


# ============================================================
# SETTINGS
# ============================================================

# Binance spot USDT coinləri
QUOTE_ASSET = "USDT"

# 5 dəqiqəlik şam
INTERVAL = "5m"

# Cari 5M volume əvvəlki neçə şamla müqayisə edilsin
BASELINE_CANDLES = 12

# Volume orta göstəricidən neçə dəfə çox olmalıdır
VOLUME_MULTIPLIER = 2.5

# Qiymət minimum neçə faiz hərəkət etməlidir
MIN_PRICE_CHANGE = 1.0

# Eyni coin yalnız 1 dəfə alert
ONE_ALERT_PER_SYMBOL = True

# Binance REST
BINANCE_API = "https://api.binance.com"

# Binance WebSocket
BINANCE_WS = "wss://stream.binance.com:9443/stream"

# Telegram
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"


# ============================================================
# SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "BinanceVolumeAlert/1.0"
})


# ============================================================
# MEMORY
# ============================================================

# Əvvəllər alert verilmiş coinlər
alerted_symbols = set()

# Son volume məlumatları
volume_history = {}

# Son qiymətlər
price_history = {}


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(text):

    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": False
    }

    try:

        response = session.post(
            TELEGRAM_API,
            json=payload,
            timeout=20
        )

        print(
            "TELEGRAM:",
            response.status_code
        )

        if not response.ok:
            print(
                "TELEGRAM ERROR:",
                response.text
            )

        return response.ok

    except Exception as e:

        print(
            "TELEGRAM EXCEPTION:",
            e
        )

        return False


# ============================================================
# GET BINANCE SYMBOLS
# ============================================================

def get_symbols():

    url = (
        f"{BINANCE_API}"
        "/api/v3/exchangeInfo"
    )

    try:

        response = session.get(
            url,
            timeout=30
        )

        response.raise_for_status()

        data = response.json()

        symbols = []

        for item in data.get(
            "symbols",
            []
        ):

            # Yalnız SPOT
            if item.get(
                "status"
            ) != "TRADING":

                continue

            if item.get(
                "quoteAsset"
            ) != QUOTE_ASSET:

                continue

            if item.get(
                "isSpotTradingAllowed"
            ) is not True:

                continue

            symbol = item.get(
                "symbol"
            )

            if symbol:
                symbols.append(
                    symbol.lower()
                )

        return symbols

    except Exception as e:

        print(
            "SYMBOL ERROR:",
            e
        )

        return []


# ============================================================
# GET HISTORICAL VOLUME
# ============================================================

def get_initial_volume_history(
    symbol
):

    url = (
        f"{BINANCE_API}"
        "/api/v3/klines"
    )

    params = {
        "symbol": symbol.upper(),
        "interval": INTERVAL,
        "limit": BASELINE_CANDLES + 1
    }

    try:

        response = session.get(
            url,
            params=params,
            timeout=20
        )

        response.raise_for_status()

        candles = response.json()

        volumes = []

        prices = []

        # Son açıq şamı baseline-a daxil etmirik
        for candle in candles[:-1]:

            volume = float(
                candle[5]
            )

            open_price = float(
                candle[1]
            )

            close_price = float(
                candle[4]
            )

            volumes.append(
                volume
            )

            prices.append(
                close_price
            )

        if volumes:

            volume_history[
                symbol.lower()
            ] = volumes

        if prices:

            price_history[
                symbol.lower()
            ] = prices[-1]

        return True

    except Exception as e:

        print(
            "HISTORY ERROR",
            symbol,
            e
        )

        return False


# ============================================================
# BOOTSTRAP
# ============================================================

def bootstrap(symbols):

    print()
    print(
        "Loading historical volume..."
    )

    total = len(symbols)

    for index, symbol in enumerate(
        symbols,
        start=1
    ):

        get_initial_volume_history(
            symbol
        )

        if index % 50 == 0:

            print(
                "Loaded:",
                index,
                "/",
                total
            )

        # Binance API-yə həddindən artıq yük verməmək
        time.sleep(0.03)

    print(
        "Historical data loaded:",
        len(volume_history)
    )


# ============================================================
# FORMAT VOLUME
# ============================================================

def format_volume(value):

    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"

    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.2f}K"

    return f"${value:.2f}"


# ============================================================
# PROCESS KLINE
# ============================================================

def process_kline(data):

    try:

        kline = data.get(
            "k"
        )

        if not kline:
            return

        symbol = kline[
            "s"
        ].lower()

        # Cari 5M şamın volume-u
        current_volume = float(
            kline["v"]
        )

        open_price = float(
            kline["o"]
        )

        current_price = float(
            kline["c"]
        )

        # ----------------------------------------------------
        # BASELINE
        # ----------------------------------------------------

        history = volume_history.get(
            symbol,
            []
        )

        if len(history) < BASELINE_CANDLES:

            history.append(
                current_volume
            )

            volume_history[
                symbol
            ] = history[-BASELINE_CANDLES:]

            return

        # ----------------------------------------------------
        # ORTA VOLUME
        # ----------------------------------------------------

        average_volume = (
            sum(history)
            / len(history)
        )

        if average_volume <= 0:

            return

        volume_ratio = (
            current_volume
            / average_volume
        )

        # ----------------------------------------------------
        # PRICE CHANGE
        # ----------------------------------------------------

        price_change = (
            (
                current_price
                - open_price
            )
            / open_price
        ) * 100

        # ----------------------------------------------------
        # CURRENT CANDLE
        # ----------------------------------------------------

        candle_closed = bool(
            kline["x"]
        )

        # ----------------------------------------------------
        # LOG
        # ----------------------------------------------------

        if volume_ratio >= VOLUME_MULTIPLIER:

            print(
                "🔥 VOLUME SPIKE:",
                symbol.upper(),
                "|",
                round(
                    volume_ratio,
                    2
                ),
                "x",
                "| PRICE:",
                round(
                    price_change,
                    2
                ),
                "%"
            )

        # ----------------------------------------------------
        # ALREADY ALERTED
        # ----------------------------------------------------

        if (
            ONE_ALERT_PER_SYMBOL
            and symbol in alerted_symbols
        ):

            # Yaddaşı yenilə
            if candle_closed:

                history.append(
                    current_volume
                )

                volume_history[
                    symbol
                ] = history[
                    -BASELINE_CANDLES:
                ]

            return

        # ----------------------------------------------------
        # FINAL SIGNAL
        # ----------------------------------------------------

        volume_signal = (
            volume_ratio
            >= VOLUME_MULTIPLIER
        )

        price_signal = (
            price_change
            >= MIN_PRICE_CHANGE
        )

        if (
            volume_signal
            and price_signal
        ):

            message = (
                "🚨 BINANCE VOLUME ALERT\n\n"

                f"🪙 Coin: "
                f"{symbol.upper()}\n\n"

                f"📊 5M Volume: "
                f"{format_volume(current_volume)}\n"

                f"📈 Average 5M Volume: "
                f"{format_volume(average_volume)}\n"

                f"🔥 Volume Spike: "
                f"{volume_ratio:.2f}x\n\n"

                f"💰 5M Price Change: "
                f"{price_change:+.2f}%\n\n"

                f"📍 Market: "
                f"Binance Spot\n\n"

                "⚠️ Bu avtomatik həcm siqnalıdır.\n"
                "Qiymətin gələcəkdə mütləq "
                "qalxacağı demək deyil."
            )

            success = send_telegram(
                message
            )

            if success:

                alerted_symbols.add(
                    symbol
                )

                print(
                    "🚨 ALERT SENT:",
                    symbol.upper(),
                    "|",
                    round(
                        volume_ratio,
                        2
                    ),
                    "x"
                )

        # ----------------------------------------------------
        # UPDATE HISTORY
        # ----------------------------------------------------

        if candle_closed:

            history.append(
                current_volume
            )

            volume_history[
                symbol
            ] = history[
                -BASELINE_CANDLES:
            ]

    except Exception as e:

        print(
            "PROCESS ERROR:",
            e
        )


# ============================================================
# WEBSOCKET
# ============================================================

async def websocket_loop(
    symbols
):

    streams = "/".join(
        f"{symbol}@kline_{INTERVAL}"
        for symbol in symbols
    )

    url = (
        f"{BINANCE_WS}"
        f"?streams={streams}"
    )

    print()
    print(
        "Connecting Binance WebSocket..."
    )

    async with websockets.connect(
        url,
        ping_interval=20,
        ping_timeout=20,
        max_size=None
    ) as websocket:

        print(
            "🟢 BINANCE WEBSOCKET CONNECTED"
        )

        while True:

            try:

                message = await websocket.recv()

                data = json.loads(
                    message
                )

                # Combined stream
                payload = data.get(
                    "data"
                )

                if payload:

                    process_kline(
                        payload
                    )

            except Exception as e:

                print(
                    "WEBSOCKET ERROR:",
                    e
                )

                raise


# ============================================================
# START
# ============================================================

async def main():

    print()
    print(
        "=========================================="
    )

    print(
        "🟢 BINANCE VOLUME ALERT BOT"
    )

    print(
        "=========================================="
    )

    print()
    print(
        "Market: Binance Spot"
    )

    print(
        "Interval: 5 minutes"
    )

    print(
        "Baseline:",
        BASELINE_CANDLES,
        "completed candles"
    )

    print(
        "Volume multiplier:",
        VOLUME_MULTIPLIER,
        "x"
    )

    print(
        "Minimum price change:",
        MIN_PRICE_CHANGE,
        "%"
    )

    print(
        "One alert per coin: ON"
    )

    print()

    # --------------------------------------------------------
    # TELEGRAM START MESSAGE
    # --------------------------------------------------------

    send_telegram(
        "🟢 BINANCE VOLUME ALERT BOT STARTED\n\n"

        "📊 Market: Binance Spot\n"
        "⏱ Timeframe: 5M\n"
        "🔥 Volume spike: ≥ 2.5x average\n"
        "📈 Price movement: ≥ +1%\n\n"

        "⚠️ Eyni coinə yalnız 1 dəfə alert "
        "veriləcək.\n\n"

        "Gündəlik volume filtri yoxdur."
    )

    # --------------------------------------------------------
    # SYMBOLS
    # --------------------------------------------------------

    symbols = get_symbols()

    print(
        "BINANCE USDT SYMBOLS:",
        len(symbols)
    )

    if not symbols:

        print(
            "No symbols found."
        )

        return

    # --------------------------------------------------------
    # HISTORICAL DATA
    # --------------------------------------------------------

    bootstrap(
        symbols
    )

    # --------------------------------------------------------
    # WEBSOCKET
    # --------------------------------------------------------

    while True:

        try:

            await websocket_loop(
                symbols
            )

        except Exception as e:

            print(
                "CONNECTION LOST:",
                e
            )

            print(
                "Reconnecting in 10 seconds..."
            )

            await asyncio.sleep(
                10
            )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    asyncio.run(
        main()
    )
