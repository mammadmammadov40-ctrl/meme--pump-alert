import os
import time
import requests
from datetime import datetime, timezone


# ============================================================
# BINANCE TOP 100 EMA20 / EMA50 BULLISH BREAKOUT BOT
# ============================================================
#
# TIMEFRAME:
#   5m
#
# COINS:
#   Binance Spot USDT
#   Top 100 by Binance 24H quote volume
#
# STRATEGY:
#
# 1. Bullish breakout candle.
#
# 2. Breakout candle OPEN:
#      > EMA20
#      > EMA50
#
# 3. Breakout candle CLOSE:
#      > EMA20
#      > EMA50
#
# 4. Breakout candle BODY must be >= 90%
#    of total candle range.
#
# 5. Breakout candle CLOSE must break
#    the previous 20 candles HIGH.
#
# 6. After breakout:
#    wait for 2 CLOSED candles.
#
# 7. If either of those 2 candles has a HIGH
#    above breakout candle HIGH:
#       CANCEL setup.
#
# 8. If both candles remain below breakout HIGH:
#       BUY SIGNAL after 2nd candle CLOSE.
#
# 9. After signal:
#       NO MORE SIGNAL for this coin.
#
# 10. RESET:
#       20 consecutive CLOSED candles must have:
#
#       OPEN  < EMA20 AND EMA50
#       CLOSE < EMA20 AND EMA50
#
#       Then the coin becomes READY again.
#
# IMPORTANT:
#   EMA20 > EMA50 is NOT required.
#
#   Breakout must be confirmed by CLOSE,
#   not by wick.
#
# ============================================================


BINANCE_BASE_URL = "https://api.binance.com"


TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN"
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID"
)


# ============================================================
# SETTINGS
# ============================================================

INTERVAL = "5m"

SCAN_SECONDS = 20

TOP_COINS = 100

EMA_FAST = 20
EMA_SLOW = 50

BREAKOUT_LOOKBACK = 20

CONFIRMATION_CANDLES = 2

RESET_CANDLES = 20

MIN_BODY_RATIO = 0.90

KLINE_LIMIT = 200


# ============================================================
# STATE
# ============================================================

# READY
# WAIT_CONFIRMATION
# SIGNALLED

coin_state = {}

breakout_data = {}

reset_count = {}

last_closed_candle = {}

processed_signals = set()


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "BinanceTop100EMABot/2.0"
})


# ============================================================
# BINANCE REQUEST
# ============================================================

def binance_get(endpoint, params=None):

    url = BINANCE_BASE_URL + endpoint

    try:

        response = session.get(
            url,
            params=params,
            timeout=10
        )

        response.raise_for_status()

        return response.json()

    except Exception as e:

        print(
            f"[BINANCE ERROR] "
            f"{endpoint} | {e}"
        )

        return None


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):

        print(
            "[TELEGRAM ERROR] "
            "Missing environment variables"
        )

        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {

        "chat_id": TELEGRAM_CHAT_ID,

        "text": message,

        "parse_mode": "HTML",

        "disable_web_page_preview": True
    }

    try:

        response = session.post(
            url,
            json=payload,
            timeout=10
        )

        if response.ok:

            print("[TELEGRAM] Sent")

            return True

        print(
            "[TELEGRAM ERROR]",
            response.status_code,
            response.text
        )

        return False

    except Exception as e:

        print(
            f"[TELEGRAM ERROR] {e}"
        )

        return False


# ============================================================
# GET BINANCE SPOT USDT SYMBOLS
# ============================================================

def get_spot_usdt_symbols():

    data = binance_get(
        "/api/v3/exchangeInfo"
    )

    if not data:

        return []

    symbols = []

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

        if item.get(
            "isSpotTradingAllowed"
        ) is not True:

            continue

        symbol = item.get(
            "symbol"
        )

        if symbol:

            symbols.append(
                symbol
            )

    return symbols


# ============================================================
# GET 24H VOLUME
# ============================================================

def get_24h_volumes():

    data = binance_get(
        "/api/v3/ticker/24hr"
    )

    if not data:

        return {}

    volumes = {}

    for item in data:

        symbol = item.get(
            "symbol"
        )

        if not symbol:

            continue

        try:

            quote_volume = float(
                item.get(
                    "quoteVolume",
                    0
                )
            )

            volumes[symbol] = (
                quote_volume
            )

        except Exception:

            continue

    return volumes


# ============================================================
# GET TOP 100
# ============================================================

def get_top_100_symbols():

    spot_symbols = (
        get_spot_usdt_symbols()
    )

    if not spot_symbols:

        return []

    volumes = (
        get_24h_volumes()
    )

    if not volumes:

        return []

    eligible = [
        symbol
        for symbol in spot_symbols
        if symbol in volumes
    ]

    eligible.sort(
        key=lambda symbol:
        volumes.get(
            symbol,
            0
        ),
        reverse=True
    )

    return eligible[
        :TOP_COINS
    ]


# ============================================================
# GET CLOSED KLINES
# ============================================================

def get_closed_klines(
    symbol,
    limit=KLINE_LIMIT
):

    data = binance_get(
        "/api/v3/klines",
        {
            "symbol": symbol,
            "interval": INTERVAL,
            "limit": limit
        }
    )

    if not data:

        return []

    now_ms = int(
        datetime.now(
            timezone.utc
        ).timestamp() * 1000
    )

    closed = []

    for candle in data:

        close_time = int(
            candle[6]
        )

        if close_time <= now_ms:

            closed.append(
                candle
            )

    return closed


# ============================================================
# EMA
# ============================================================

def calculate_ema(
    values,
    period
):

    if len(values) < period:

        return None

    multiplier = (
        2 /
        (period + 1)
    )

    ema = sum(
        values[:period]
    ) / period

    for price in values[period:]:

        ema = (
            (price - ema)
            * multiplier
            + ema
        )

    return ema


# ============================================================
# CANDLE HELPERS
# ============================================================

def candle_open(candle):

    return float(candle[1])


def candle_high(candle):

    return float(candle[2])


def candle_low(candle):

    return float(candle[3])


def candle_close(candle):

    return float(candle[4])


def candle_time(candle):

    return int(candle[0])


# ============================================================
# FORMAT PRICE
# ============================================================

def format_price(price):

    if price is None:

        return "N/A"

    if price >= 1000:

        return f"{price:.2f}"

    if price >= 1:

        return f"{price:.4f}"

    if price >= 0.01:

        return f"{price:.6f}"

    if price >= 0.0001:

        return f"{price:.8f}"

    return f"{price:.10f}"


# ============================================================
# FORMAT TIME
# ============================================================

def format_time(timestamp_ms):

    return datetime.fromtimestamp(
        timestamp_ms / 1000,
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M UTC"
    )


# ============================================================
# GET EMA VALUES
# ============================================================

def get_ema_values(
    candles,
    index
):

    if index < EMA_SLOW - 1:

        return None, None

    closes = [
        candle_close(c)
        for c in candles[
            :index + 1
        ]
    ]

    ema20 = calculate_ema(
        closes,
        EMA_FAST
    )

    ema50 = calculate_ema(
        closes,
        EMA_SLOW
    )

    return ema20, ema50


# ============================================================
# RESET CANDLE
# ============================================================

def candle_is_reset_candle(
    candle,
    ema20,
    ema50
):

    if (
        ema20 is None
        or ema50 is None
    ):

        return False

    open_price = candle_open(
        candle
    )

    close_price = candle_close(
        candle
    )

    return (
        open_price < ema20
        and
        open_price < ema50
        and
        close_price < ema20
        and
        close_price < ema50
    )


# ============================================================
# CHECK BREAKOUT
# ============================================================

def check_breakout(
    candles,
    index
):

    if index < BREAKOUT_LOOKBACK:

        return None

    candle = candles[index]

    open_price = candle_open(
        candle
    )

    high = candle_high(
        candle
    )

    low = candle_low(
        candle
    )

    close = candle_close(
        candle
    )


    # ========================================================
    # EMA
    # ========================================================

    ema20, ema50 = get_ema_values(
        candles,
        index
    )

    if (
        ema20 is None
        or ema50 is None
    ):

        return None


    # ========================================================
    # 1. BULLISH CANDLE
    # ========================================================

    if close <= open_price:

        return None


    # ========================================================
    # 2. OPEN ABOVE BOTH EMAs
    # ========================================================

    if not (
        open_price > ema20
        and
        open_price > ema50
    ):

        return None


    # ========================================================
    # 3. CLOSE ABOVE BOTH EMAs
    # ========================================================

    if not (
        close > ema20
        and
        close > ema50
    ):

        return None


    # ========================================================
    # 4. BODY >= 90%
    #
    # Body:
    #     Close - Open
    #
    # Total range:
    #     High - Low
    # ========================================================

    candle_range = (
        high - low
    )

    if candle_range <= 0:

        return None

    body = (
        close - open_price
    )

    body_ratio = (
        body /
        candle_range
    )

    if body_ratio < MIN_BODY_RATIO:

        return None


    # ========================================================
    # 5. PREVIOUS 20 HIGH
    # ========================================================

    previous_candles = candles[
        index - BREAKOUT_LOOKBACK:index
    ]

    if len(
        previous_candles
    ) < BREAKOUT_LOOKBACK:

        return None

    previous_20_high = max(
        candle_high(c)
        for c in previous_candles
    )


    # ========================================================
    # 6. BREAKOUT BY CLOSE
    #
    # Wick does NOT count.
    # ========================================================

    if close <= previous_20_high:

        return None


    # ========================================================
    # BREAKOUT FOUND
    # ========================================================

    return {

        "index": index,

        "open": open_price,

        "high": high,

        "low": low,

        "close": close,

        "ema20": ema20,

        "ema50": ema50,

        "body_ratio": body_ratio,

        "previous_20_high": (
            previous_20_high
        ),

        "open_time": candle_time(
            candle
        )
    }


# ============================================================
# SEND BUY SIGNAL
# ============================================================

def send_buy_signal(
    symbol,
    data
):

    breakout_close = (
        data["close"]
    )

    breakout_high = (
        data["high"]
    )

    ema20 = data["ema20"]

    ema50 = data["ema50"]

    previous_20_high = (
        data["previous_20_high"]
    )

    body_percent = (
        data["body_ratio"] * 100
    )

    second_candle_close = (
        data["second_candle_close"]
    )

    signal_price = (
        second_candle_close
    )

    breakout_time = (
        data["open_time"]
    )

    second_candle_time = (
        data["second_candle_time"]
    )


    message = (

        "🟢 <b>EMA20 / EMA50 BREAKOUT BUY</b>\n\n"

        f"<b>{symbol}</b> — {INTERVAL}\n\n"

        "🚀 <b>BREAKOUT CANDLE</b>\n"

        f"Open: "
        f"{format_price(data['open'])}\n"

        f"Close: "
        f"{format_price(breakout_close)}\n"

        f"High: "
        f"{format_price(breakout_high)}\n"

        f"Body: "
        f"{body_percent:.1f}%\n"

        f"Previous 20 High: "
        f"{format_price(previous_20_high)}\n\n"

        "📊 <b>EMAs</b>\n"

        f"EMA20: "
        f"{format_price(ema20)}\n"

        f"EMA50: "
        f"{format_price(ema50)}\n\n"

        "⏳ <b>2-CANDLE CONFIRMATION</b>\n"

        f"2nd Candle Close: "
        f"{format_price(second_candle_close)}\n"

        f"Breakout High: "
        f"{format_price(breakout_high)}\n\n"

        "🟢 <b>SIGNAL PRICE</b>\n"

        f"{format_price(signal_price)}\n\n"

        "✅ Bullish candle\n"
        "✅ Body ≥ 90%\n"
        "✅ Open above EMA20 & EMA50\n"
        "✅ Close above EMA20 & EMA50\n"
        "✅ 20-candle HIGH broken by CLOSE\n"
        "✅ 2 confirmation candles passed\n\n"

        f"🕐 Breakout: "
        f"{format_time(breakout_time)}\n"

        f"🕐 Confirmation: "
        f"{format_time(second_candle_time)}\n\n"

        "🔒 <b>RESET REQUIRED FOR NEXT SIGNAL</b>"
    )


    send_telegram(
        message
    )


# ============================================================
# ANALYZE SYMBOL
# ============================================================

def analyze_symbol(symbol):

    candles = get_closed_klines(
        symbol
    )

    if len(candles) < 80:

        return


    latest = candles[-1]

    latest_open_time = candle_time(
        latest
    )


    # ========================================================
    # ONLY PROCESS NEW CLOSED CANDLE
    # ========================================================

    previous_time = (
        last_closed_candle.get(
            symbol
        )
    )

    if previous_time == latest_open_time:

        return

    last_closed_candle[symbol] = (
        latest_open_time
    )


    # ========================================================
    # INITIALIZE STATE
    # ========================================================

    if symbol not in coin_state:

        coin_state[symbol] = "READY"

        reset_count[symbol] = 0


    state = coin_state[symbol]


    # ========================================================
    # WAIT FOR 2 CONFIRMATION CANDLES
    # ========================================================

    if state == "WAIT_CONFIRMATION":

        data = breakout_data.get(
            symbol
        )

        if not data:

            coin_state[symbol] = "READY"

            return


        breakout_high = (
            data["high"]
        )


        # ====================================================
        # CHECK CURRENT CONFIRMATION CANDLE HIGH
        # ====================================================

        current_high = candle_high(
            latest
        )

        if current_high > breakout_high:

            print(
                f"[CANCEL] {symbol} | "
                f"Confirmation HIGH "
                f"{format_price(current_high)} "
                f"> Breakout HIGH "
                f"{format_price(breakout_high)}"
            )

            coin_state[symbol] = "SIGNALLED"

            reset_count[symbol] = 0

            breakout_data.pop(
                symbol,
                None
            )

            return


        # ====================================================
        # COUNT CONFIRMATION CANDLE
        # ====================================================

        data["confirmation_count"] += 1

        confirmation_count = (
            data["confirmation_count"]
        )


        print(
            f"[CONFIRM] {symbol} | "
            f"{confirmation_count}/"
            f"{CONFIRMATION_CANDLES} | "
            f"High="
            f"{format_price(current_high)} | "
            f"Breakout High="
            f"{format_price(breakout_high)} | "
            f"Close="
            f"{format_price(candle_close(latest))}"
        )


        # ====================================================
        # FIRST CANDLE
        # ====================================================

        if confirmation_count == 1:

            data["first_candle_close"] = (
                candle_close(latest)
            )

            data["first_candle_time"] = (
                candle_time(latest)
            )

            return


        # ====================================================
        # SECOND CANDLE
        # ====================================================

        if confirmation_count == 2:

            data["second_candle_close"] = (
                candle_close(latest)
            )

            data["second_candle_time"] = (
                candle_time(latest)
            )


            # ================================================
            # SIGNAL
            # ================================================

            signal_key = (
                symbol,
                data["open_time"]
            )


            if signal_key not in processed_signals:

                processed_signals.add(
                    signal_key
                )

                print("")
                print(
                    "=================================================="
                )

                print(
                    f"[BUY SIGNAL] {symbol}"
                )

                print(
                    f"Breakout Close: "
                    f"{format_price(data['close'])}"
                )

                print(
                    f"Breakout High: "
                    f"{format_price(data['high'])}"
                )

                print(
                    f"2nd Candle Close: "
                    f"{format_price(data['second_candle_close'])}"
                )

                print(
                    f"Signal Price: "
                    f"{format_price(data['second_candle_close'])}"
                )

                print(
                    "=================================================="
                )


                send_buy_signal(
                    symbol,
                    data
                )


            # ================================================
            # LOCK UNTIL RESET
            # ================================================

            coin_state[symbol] = (
                "SIGNALLED"
            )

            reset_count[symbol] = 0

            breakout_data.pop(
                symbol,
                None
            )

            return


        return


    # ========================================================
    # SIGNALLED STATE
    #
    # No new signal until reset.
    # ========================================================

    if state == "SIGNALLED":

        index = len(candles) - 1

        ema20, ema50 = get_ema_values(
            candles,
            index
        )

        if (
            ema20 is None
            or ema50 is None
        ):

            return


        if candle_is_reset_candle(
            latest,
            ema20,
            ema50
        ):

            reset_count[symbol] += 1

            print(
                f"[RESET] {symbol} | "
                f"{reset_count[symbol]}/"
                f"{RESET_CANDLES} | "
                f"Open="
                f"{format_price(candle_open(latest))} | "
                f"Close="
                f"{format_price(candle_close(latest))} | "
                f"EMA20="
                f"{format_price(ema20)} | "
                f"EMA50="
                f"{format_price(ema50)}"
            )

        else:

            if reset_count.get(
                symbol,
                0
            ) > 0:

                print(
                    f"[RESET BROKEN] {symbol} | "
                    "Counter returned to 0"
                )

            reset_count[symbol] = 0


        # ====================================================
        # RESET COMPLETE
        # ====================================================

        if (
            reset_count[symbol]
            >= RESET_CANDLES
        ):

            print(
                f"[RESET COMPLETE] {symbol} | "
                f"{RESET_CANDLES} consecutive candles "
                "below EMA20 & EMA50"
            )

            coin_state[symbol] = (
                "READY"
            )

            reset_count[symbol] = 0

        return


    # ========================================================
    # READY STATE
    # ========================================================

    if state == "READY":

        index = len(candles) - 1

        breakout = check_breakout(
            candles,
            index
        )

        if not breakout:

            return


        # ====================================================
        # BREAKOUT FOUND
        # ====================================================

        print("")
        print(
            "=================================================="
        )

        print(
            f"[BREAKOUT] {symbol}"
        )

        print(
            f"Open: "
            f"{format_price(breakout['open'])}"
        )

        print(
            f"Close: "
            f"{format_price(breakout['close'])}"
        )

        print(
            f"High: "
            f"{format_price(breakout['high'])}"
        )

        print(
            f"Body: "
            f"{breakout['body_ratio'] * 100:.1f}%"
        )

        print(
            f"Previous 20 High: "
            f"{format_price(breakout['previous_20_high'])}"
        )

        print(
            f"EMA20: "
            f"{format_price(breakout['ema20'])}"
        )

        print(
            f"EMA50: "
            f"{format_price(breakout['ema50'])}"
        )

        print(
            "[WAIT] Next 2 CLOSED candles..."
        )

        print(
            "=================================================="
        )


        # ====================================================
        # SAVE BREAKOUT
        # ====================================================

        breakout[
            "confirmation_count"
        ] = 0

        breakout_data[symbol] = (
            breakout
        )

        coin_state[symbol] = (
            "WAIT_CONFIRMATION"
        )

        return


# ============================================================
# SCAN
# ============================================================

def scan():

    print("")
    print(
        "=================================================="
    )

    print(
        f"SCAN | "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )

    print(
        "=================================================="
    )


    # ========================================================
    # GET CURRENT BINANCE TOP 100
    # ========================================================

    symbols = get_top_100_symbols()

    if not symbols:

        print(
            "[ERROR] Could not get Binance Top 100"
        )

        return


    print(
        f"[INFO] Binance Top {len(symbols)}"
    )


    current_symbols = set(
        symbols
    )


    # ========================================================
    # REMOVE STATE OF COINS NO LONGER IN TOP 100
    # ========================================================

    for symbol in list(
        coin_state.keys()
    ):

        if symbol not in current_symbols:

            coin_state.pop(
                symbol,
                None
            )

            breakout_data.pop(
                symbol,
                None
            )

            reset_count.pop(
                symbol,
                None
            )

            last_closed_candle.pop(
                symbol,
                None
            )


    # ========================================================
    # ANALYZE
    # ========================================================

    for index, symbol in enumerate(
        symbols,
        start=1
    ):

        try:

            analyze_symbol(
                symbol
            )

            if index % 10 == 0:

                print(
                    f"[PROGRESS] "
                    f"{index}/{len(symbols)}"
                )

        except Exception as e:

            print(
                f"[ERROR] {symbol}: {e}"
            )


# ============================================================
# MAIN
# ============================================================

def main():

    print("")
    print(
        "=================================================="
    )

    print(
        "   BINANCE TOP 100 EMA20 / EMA50 BUY BOT"
    )

    print(
        "=================================================="
    )

    print(
        f"Timeframe: {INTERVAL}"
    )

    print(
        f"Scan interval: "
        f"{SCAN_SECONDS} seconds"
    )

    print(
        f"Binance Top coins: "
        f"{TOP_COINS}"
    )

    print(
        f"EMA: "
        f"{EMA_FAST} / {EMA_SLOW}"
    )

    print(
        f"Breakout lookback: "
        f"{BREAKOUT_LOOKBACK} candles"
    )

    print(
        "Breakout: CLOSE above previous 20 HIGH"
    )

    print(
        f"Minimum bullish body: "
        f"{MIN_BODY_RATIO * 100:.0f}%"
    )

    print(
        f"Confirmation candles: "
        f"{CONFIRMATION_CANDLES}"
    )

    print(
        f"Reset candles: "
        f"{RESET_CANDLES}"
    )

    print(
        "Reset: OPEN + CLOSE below both EMAs"
    )

    print(
        "EMA20 > EMA50 requirement: NONE"
    )

    print(
        "24H minimum volume filter: NONE"
    )

    print(
        "24H cooldown: NONE"
    )

    print(
        "=================================================="
    )

    print("")


    # ========================================================
    # LOOP
    # ========================================================

    while True:

        start_time = time.time()

        try:

            scan()

        except Exception as e:

            print(
                f"[MAIN ERROR] {e}"
            )

        elapsed = (
            time.time()
            - start_time
        )

        sleep_time = max(
            1,
            SCAN_SECONDS - elapsed
        )

        time.sleep(
            sleep_time
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
