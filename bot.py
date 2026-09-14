import os
import time
import requests
from datetime import datetime, timezone


# ============================================================
# BINANCE MOMENTUM + VWAP BUY BOT
# ONLY BULLISH BUY SIGNALS
# 24-HOUR COOLDOWN PER SYMBOL
# ============================================================

BINANCE_BASE_URL = "https://api.binance.com"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# ============================================================
# SETTINGS
# ============================================================

INTERVAL = "5m"

SCAN_SECONDS = 20

# ------------------------------------------------------------
# AFTER A BUY SIGNAL:
# THE SAME COIN CANNOT SIGNAL AGAIN FOR 24 HOURS
# ------------------------------------------------------------

COOLDOWN_HOURS = 24


# Previous candles used for breakout
BREAKOUT_LOOKBACK = 10

# Volume calculation
VOLUME_LOOKBACK = 20
VOLUME_MULTIPLIER = 1.5

# Strong bullish candle
MIN_BODY_RATIO = 0.60

# Minimum 24h quote volume
MIN_24H_QUOTE_VOLUME = 5_000_000

# Maximum symbols checked in one cycle
# None = all eligible Binance Spot USDT symbols
MAX_SYMBOLS = None


# ============================================================
# SESSION STATE
# ============================================================

processed_signals = set()

# Stores the time of the last BUY signal for each symbol
#
# Example:
# {
#     "DOGSUSDT": 1726300000,
#     "BTCUSDT": 1726305000
# }
#
# Each symbol has its own 24-hour cooldown.
last_signal_time = {}

first_scan = True


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "MomentumVWAPBot/1.0"
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

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:

        print(
            "[TELEGRAM ERROR] "
            "Missing environment variables"
        )

        return False

    url = (
        f"https://api.telegram.org/bot"
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

def get_symbols():

    data = binance_get(
        "/api/v3/exchangeInfo"
    )

    if not data:

        return []

    symbols = []

    for item in data.get("symbols", []):

        if item.get("status") != "TRADING":
            continue

        if item.get("quoteAsset") != "USDT":
            continue

        if item.get("isSpotTradingAllowed") is not True:
            continue

        symbol = item.get("symbol")

        if symbol:
            symbols.append(symbol)

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

    result = {}

    for item in data:

        symbol = item.get("symbol")

        if not symbol:
            continue

        try:

            quote_volume = float(
                item.get("quoteVolume", 0)
            )

            result[symbol] = quote_volume

        except Exception:

            continue

    return result


# ============================================================
# GET CLOSED 5M CANDLES
# ============================================================

def get_klines(symbol, limit=100):

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

    # Remove currently forming candle
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

            closed.append(candle)

    return closed


# ============================================================
# CALCULATE SESSION VWAP
#
# VWAP resets at 00:00 UTC every day.
#
# Typical Price =
# (High + Low + Close) / 3
#
# VWAP =
# Sum(Typical Price * Volume)
# /
# Sum(Volume)
# ============================================================

def calculate_session_vwap(candles):

    if not candles:

        return None

    today = datetime.now(
        timezone.utc
    ).date()

    cumulative_pv = 0.0
    cumulative_volume = 0.0

    for candle in candles:

        open_time = int(
            candle[0]
        )

        candle_date = datetime.fromtimestamp(
            open_time / 1000,
            timezone.utc
        ).date()

        if candle_date != today:

            continue

        high = float(
            candle[2]
        )

        low = float(
            candle[3]
        )

        close = float(
            candle[4]
        )

        volume = float(
            candle[5]
        )

        typical_price = (
            high + low + close
        ) / 3.0

        cumulative_pv += (
            typical_price * volume
        )

        cumulative_volume += volume

    if cumulative_volume <= 0:

        return None

    return (
        cumulative_pv /
        cumulative_volume
    )


# ============================================================
# CALCULATE PREVIOUS VWAP
# ============================================================

def calculate_previous_session_vwap(candles):

    if len(candles) < 2:

        return None

    return calculate_session_vwap(
        candles[:-1]
    )


# ============================================================
# FORMAT PRICE
# ============================================================

def format_price(price):

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
# CHECK 24-HOUR COOLDOWN
# ============================================================

def symbol_is_in_cooldown(symbol):

    if symbol not in last_signal_time:

        return False

    current_time = time.time()

    elapsed = (
        current_time -
        last_signal_time[symbol]
    )

    cooldown_seconds = (
        COOLDOWN_HOURS * 60 * 60
    )

    if elapsed < cooldown_seconds:

        remaining = (
            cooldown_seconds -
            elapsed
        )

        remaining_hours = (
            remaining / 3600
        )

        print(
            f"[COOLDOWN] {symbol} | "
            f"{remaining_hours:.1f}h remaining"
        )

        return True

    # Cooldown expired
    del last_signal_time[symbol]

    print(
        f"[COOLDOWN EXPIRED] {symbol}"
    )

    return False


# ============================================================
# ANALYZE SYMBOL
# ============================================================

def analyze_symbol(symbol, quote_volume):

    global first_scan

    # --------------------------------------------------------
    # 24H VOLUME FILTER
    # --------------------------------------------------------

    if quote_volume < MIN_24H_QUOTE_VOLUME:

        return None

    # --------------------------------------------------------
    # 24-HOUR SYMBOL COOLDOWN
    # --------------------------------------------------------

    if symbol_is_in_cooldown(symbol):

        return None

    # --------------------------------------------------------
    # GET CANDLES
    # --------------------------------------------------------

    candles = get_klines(
        symbol,
        limit=300
    )

    if len(candles) < 30:

        return None

    # --------------------------------------------------------
    # LAST CLOSED CANDLE
    # --------------------------------------------------------

    signal_candle = candles[-1]

    signal_open_time = int(
        signal_candle[0]
    )

    signal_key = (
        symbol,
        signal_open_time
    )

    # --------------------------------------------------------
    # DON'T PROCESS SAME CANDLE AGAIN
    # --------------------------------------------------------

    if signal_key in processed_signals:

        return None

    # --------------------------------------------------------
    # ON BOT STARTUP:
    # DON'T SEND OLD SIGNALS
    # --------------------------------------------------------

    if first_scan:

        processed_signals.add(
            signal_key
        )

        return None

    # --------------------------------------------------------
    # OHLCV
    # --------------------------------------------------------

    open_price = float(
        signal_candle[1]
    )

    high = float(
        signal_candle[2]
    )

    low = float(
        signal_candle[3]
    )

    close = float(
        signal_candle[4]
    )

    volume = float(
        signal_candle[5]
    )

    # --------------------------------------------------------
    # 1. BULLISH CANDLE
    # --------------------------------------------------------

    if close <= open_price:

        processed_signals.add(
            signal_key
        )

        return None

    # --------------------------------------------------------
    # RANGE
    # --------------------------------------------------------

    candle_range = high - low

    if candle_range <= 0:

        processed_signals.add(
            signal_key
        )

        return None

    # --------------------------------------------------------
    # BODY
    # --------------------------------------------------------

    body = close - open_price

    body_ratio = (
        body / candle_range
    )

    # --------------------------------------------------------
    # 2. STRONG BULLISH BODY
    # --------------------------------------------------------

    if body_ratio < MIN_BODY_RATIO:

        processed_signals.add(
            signal_key
        )

        return None

    # --------------------------------------------------------
    # 3. PREVIOUS 10 CANDLE HIGH
    # --------------------------------------------------------

    previous_candles = candles[
        -(BREAKOUT_LOOKBACK + 1):-1
    ]

    if len(previous_candles) < BREAKOUT_LOOKBACK:

        return None

    previous_high = max(
        float(c[2])
        for c in previous_candles
    )

    # --------------------------------------------------------
    # BREAKOUT
    # --------------------------------------------------------

    if close <= previous_high:

        processed_signals.add(
            signal_key
        )

        return None

    # --------------------------------------------------------
    # 4. VOLUME MOMENTUM
    # --------------------------------------------------------

    volume_candles = candles[
        -(VOLUME_LOOKBACK + 1):-1
    ]

    if len(volume_candles) < VOLUME_LOOKBACK:

        return None

    average_volume = sum(
        float(c[5])
        for c in volume_candles
    ) / len(volume_candles)

    if average_volume <= 0:

        processed_signals.add(
            signal_key
        )

        return None

    volume_ratio = (
        volume / average_volume
    )

    if volume_ratio < VOLUME_MULTIPLIER:

        processed_signals.add(
            signal_key
        )

        return None

    # --------------------------------------------------------
    # 5. VWAP
    # --------------------------------------------------------

    vwap = calculate_session_vwap(
        candles
    )

    previous_vwap = (
        calculate_previous_session_vwap(
            candles
        )
    )

    if vwap is None or previous_vwap is None:

        processed_signals.add(
            signal_key
        )

        return None

    # --------------------------------------------------------
    # PRICE MUST BE ABOVE VWAP
    # --------------------------------------------------------

    if close <= vwap:

        processed_signals.add(
            signal_key
        )

        return None

    # --------------------------------------------------------
    # VWAP MUST BE RISING
    # --------------------------------------------------------

    if vwap <= previous_vwap:

        processed_signals.add(
            signal_key
        )

        return None

    # --------------------------------------------------------
    # ALL CONDITIONS PASSED
    # --------------------------------------------------------

    processed_signals.add(
        signal_key
    )

    return {

        "symbol": symbol,

        "price": close,

        "open": open_price,

        "high": high,

        "low": low,

        "vwap": vwap,

        "previous_vwap": previous_vwap,

        "body_ratio": body_ratio,

        "volume_ratio": volume_ratio,

        "previous_high": previous_high,

        "quote_volume": quote_volume,

        "candle_time": signal_open_time
    }


# ============================================================
# SEND BUY SIGNAL
# ============================================================

def send_buy_signal(signal):

    symbol = signal["symbol"]

    price = signal["price"]

    vwap = signal["vwap"]

    previous_high = signal["previous_high"]

    body_percent = (
        signal["body_ratio"] * 100
    )

    volume_multiple = (
        signal["volume_ratio"]
    )

    quote_volume = (
        signal["quote_volume"]
    )

    candle_time = datetime.fromtimestamp(
        signal["candle_time"] / 1000,
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M UTC"
    )

    message = (

        "🟢 <b>MOMENTUM BUY</b>\n\n"

        f"<b>{symbol}</b> — {INTERVAL}\n\n"

        f"💰 <b>Price:</b> "
        f"{format_price(price)}\n"

        f"📊 <b>VWAP:</b> "
        f"{format_price(vwap)}\n"

        f"🚀 <b>Breakout High:</b> "
        f"{format_price(previous_high)}\n\n"

        f"💪 <b>Candle Body:</b> "
        f"{body_percent:.1f}%\n"

        f"🔥 <b>Volume:</b> "
        f"{volume_multiple:.2f}× average\n\n"

        f"💵 <b>24H Volume:</b> "
        f"${quote_volume:,.0f}\n\n"

        f"🕐 <b>Candle:</b> "
        f"{candle_time}\n\n"

        "✅ VWAP ABOVE\n"
        "✅ VWAP RISING\n"
        "✅ STRONG BULLISH CANDLE\n"
        "✅ HIGH BREAKOUT\n"
        "✅ VOLUME MOMENTUM\n\n"

        "⏳ <b>24H COOLDOWN STARTED</b>"
    )

    # --------------------------------------------------------
    # IMPORTANT:
    # START COOLDOWN ONLY AFTER TELEGRAM MESSAGE
    # IS SUCCESSFULLY SENT.
    # --------------------------------------------------------

    sent = send_telegram(
        message
    )

    if sent:

        last_signal_time[symbol] = time.time()

        print(
            f"[COOLDOWN START] "
            f"{symbol} | "
            f"{COOLDOWN_HOURS} hours"
        )

    else:

        print(
            f"[COOLDOWN NOT STARTED] "
            f"{symbol} | "
            f"Telegram failed"
        )


# ============================================================
# MAIN SCAN
# ============================================================

def scan():

    global first_scan

    print(
        "\n"
        "=================================================="
    )

    print(
        f"SCAN "
        f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC"
    )

    print(
        "=================================================="
    )

    symbols = get_symbols()

    if not symbols:

        print(
            "[ERROR] No symbols"
        )

        return

    print(
        f"[INFO] Binance Spot USDT symbols: "
        f"{len(symbols)}"
    )

    # --------------------------------------------------------
    # 24H VOLUME
    # --------------------------------------------------------

    volumes = get_24h_volumes()

    # --------------------------------------------------------
    # SORT BY 24H VOLUME
    # --------------------------------------------------------

    symbols = sorted(
        symbols,
        key=lambda s: volumes.get(
            s,
            0
        ),
        reverse=True
    )

    # --------------------------------------------------------
    # OPTIONAL SYMBOL LIMIT
    # --------------------------------------------------------

    if MAX_SYMBOLS is not None:

        symbols = symbols[
            :MAX_SYMBOLS
        ]

    print(
        f"[INFO] Symbols to scan: "
        f"{len(symbols)}"
    )

    # --------------------------------------------------------
    # ANALYZE
    # --------------------------------------------------------

    for index, symbol in enumerate(
        symbols,
        start=1
    ):

        try:

            quote_volume = volumes.get(
                symbol,
                0
            )

            signal = analyze_symbol(
                symbol,
                quote_volume
            )

            if signal:

                print(
                    f"[BUY] {symbol} | "
                    f"Price="
                    f"{signal['price']} | "
                    f"VWAP="
                    f"{signal['vwap']} | "
                    f"Volume="
                    f"{signal['volume_ratio']:.2f}x"
                )

                send_buy_signal(
                    signal
                )

            if index % 50 == 0:

                print(
                    f"[PROGRESS] "
                    f"{index}/"
                    f"{len(symbols)}"
                )

        except Exception as e:

            print(
                f"[ERROR] {symbol}: {e}"
            )

    first_scan = False

    # --------------------------------------------------------
    # CLEAN OLD PROCESSED CANDLE STATE
    # --------------------------------------------------------

    if len(processed_signals) > 10000:

        processed_signals.clear()


# ============================================================
# MAIN LOOP
# ============================================================

def main():

    print(
        "\n"
        "==================================================\n"
        "   BINANCE MOMENTUM + VWAP BUY BOT\n"
        "==================================================\n"
    )

    print(
        f"Timeframe: {INTERVAL}"
    )

    print(
        f"Breakout lookback: "
        f"{BREAKOUT_LOOKBACK}"
    )

    print(
        f"Volume multiplier: "
        f"{VOLUME_MULTIPLIER}x"
    )

    print(
        f"Minimum body ratio: "
        f"{MIN_BODY_RATIO * 100:.0f}%"
    )

    print(
        f"Minimum 24H volume: "
        f"${MIN_24H_QUOTE_VOLUME:,.0f}"
    )

    print(
        f"Symbol cooldown: "
        f"{COOLDOWN_HOURS} hours"
    )

    print(
        "Strategy: BUY ONLY"
    )

    print(
        "==================================================\n"
    )

    while True:

        try:

            scan()

        except Exception as e:

            print(
                f"[MAIN ERROR] {e}"
            )

        time.sleep(
            SCAN_SECONDS
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
