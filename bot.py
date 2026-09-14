import os
import time
import requests
from datetime import datetime, timezone


# ============================================================
# BINANCE MOMENTUM + VWAP BUY BOT
# ONLY BULLISH BUY SIGNALS
#
# LOGIC:
# 1. Every 20 seconds the bot checks Binance.
# 2. Only the latest CLOSED 5m candle is analyzed.
# 3. Every new closed candle is checked independently.
# 4. If ALL conditions pass -> BUY signal.
# 5. If 1 or 2 conditions fail -> DEBUG log.
# 6. If 3+ conditions fail -> no debug log.
# 7. Same symbol has 24-hour cooldown after successful BUY.
# ============================================================


BINANCE_BASE_URL = "https://api.binance.com"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# ============================================================
# SETTINGS
# ============================================================

INTERVAL = "5m"

# Check Binance every 20 seconds
SCAN_SECONDS = 20


# ------------------------------------------------------------
# SAME COIN COOLDOWN
# ------------------------------------------------------------

COOLDOWN_HOURS = 24


# ------------------------------------------------------------
# BREAKOUT
# ------------------------------------------------------------

BREAKOUT_LOOKBACK = 10


# ------------------------------------------------------------
# VOLUME MOMENTUM
# ------------------------------------------------------------

VOLUME_LOOKBACK = 20
VOLUME_MULTIPLIER = 1.5


# ------------------------------------------------------------
# STRONG BULLISH CANDLE
# ------------------------------------------------------------

MIN_BODY_RATIO = 0.60


# ------------------------------------------------------------
# MINIMUM 24H QUOTE VOLUME
# ------------------------------------------------------------

MIN_24H_QUOTE_VOLUME = 5_000_000


# ------------------------------------------------------------
# ALL BINANCE SPOT USDT SYMBOLS
#
# None = scan all eligible symbols
# ------------------------------------------------------------

MAX_SYMBOLS = None


# ============================================================
# SESSION STATE
# ============================================================

# Prevent processing the exact same candle repeatedly.
#
# Example:
# {
#     ("ETHFIUSDT", 1757822400000),
#     ("DOGSUSDT", 1757822400000)
# }
#
processed_signals = set()


# ------------------------------------------------------------
# LAST SUCCESSFUL BUY TIME
#
# Example:
#
# {
#     "ETHFIUSDT": 1757822400.0
# }
#
# ------------------------------------------------------------

last_signal_time = {}


# ------------------------------------------------------------
# LAST CLOSED CANDLE SEEN PER SYMBOL
#
# This helps us recognize a NEW closed candle.
#
# Example:
#
# {
#     "ETHFIUSDT": 1757822400000
# }
#
# ------------------------------------------------------------

last_closed_candle = {}


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
                item.get(
                    "quoteVolume",
                    0
                )
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

        # Only fully closed candles
        if close_time <= now_ms:

            closed.append(candle)

    return closed


# ============================================================
# CALCULATE SESSION VWAP
#
# VWAP resets at 00:00 UTC.
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
# PREVIOUS SESSION VWAP
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
# 24-HOUR COOLDOWN CHECK
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

        return True

    # Cooldown expired
    del last_signal_time[symbol]

    return False


# ============================================================
# DEBUG HELPER
#
# Only show debug when:
#
# 1 failed condition
# OR
# 2 failed conditions
#
# 3+ failed conditions = SILENT
# ============================================================

def print_debug_if_needed(
    symbol,
    signal_candle,
    checks
):

    failed = [
        name
        for name, passed, value
        in checks
        if not passed
    ]

    failed_count = len(failed)

    # 3 or more failures:
    # don't write debug log
    if failed_count > 2:

        return

    # All conditions passed:
    # BUY signal handles this
    if failed_count == 0:

        return

    signal_time = datetime.fromtimestamp(
        int(signal_candle[0]) / 1000,
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M UTC"
    )

    print("")
    print(
        "--------------------------------------------------"
    )

    print(
        f"[DEBUG] {symbol} | "
        f"{signal_time}"
    )

    for name, passed, value in checks:

        if passed:

            print(
                f"  {name}: "
                f"OK"
            )

        else:

            print(
                f"  {name}: "
                f"FAILED"
                f" ({value})"
            )

    print(
        f"[RESULT] NO SIGNAL | "
        f"Failed: {', '.join(failed)}"
    )

    print(
        "--------------------------------------------------"
    )


# ============================================================
# ANALYZE SYMBOL
# ============================================================

def analyze_symbol(
    symbol,
    quote_volume
):

    # --------------------------------------------------------
    # 24H VOLUME
    # --------------------------------------------------------

    if quote_volume < MIN_24H_QUOTE_VOLUME:

        return None


    # --------------------------------------------------------
    # 24H COOLDOWN
    # --------------------------------------------------------

    if symbol_is_in_cooldown(symbol):

        return None


    # --------------------------------------------------------
    # GET CLOSED CANDLES
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
    # SAME CANDLE ALREADY PROCESSED
    # --------------------------------------------------------

    if signal_key in processed_signals:

        return None


    # --------------------------------------------------------
    # MARK CANDLE AS PROCESSED
    #
    # This happens once.
    # The candle is still fully analyzed below.
    # --------------------------------------------------------

    processed_signals.add(
        signal_key
    )


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


    # ========================================================
    # ALL CONDITIONS ARE CALCULATED
    # BEFORE DECIDING WHETHER TO SIGNAL.
    #
    # This allows us to know exactly which condition failed.
    # ========================================================


    checks = []


    # --------------------------------------------------------
    # 1. BULLISH CANDLE
    # --------------------------------------------------------

    bullish = (
        close > open_price
    )

    checks.append(
        (
            "Bullish Candle",
            bullish,
            f"Close={format_price(close)} "
            f"<= Open={format_price(open_price)}"
            if not bullish
            else f"Close={format_price(close)} "
                 f"> Open={format_price(open_price)}"
        )
    )


    # --------------------------------------------------------
    # RANGE
    # --------------------------------------------------------

    candle_range = (
        high - low
    )

    if candle_range <= 0:

        checks.append(
            (
                "Strong Bullish Body",
                False,
                "Candle range <= 0"
            )
        )

        # Other conditions cannot be meaningfully calculated.
        # Mark them as failed.
        checks.append(
            (
                "10-Candle Breakout",
                False,
                "Invalid candle range"
            )
        )

        checks.append(
            (
                "Volume Momentum",
                False,
                "Invalid candle range"
            )
        )

        checks.append(
            (
                "Price > VWAP",
                False,
                "Invalid candle range"
            )
        )

        checks.append(
            (
                "VWAP Rising",
                False,
                "Invalid candle range"
            )
        )

        print_debug_if_needed(
            symbol,
            signal_candle,
            checks
        )

        return None


    # --------------------------------------------------------
    # 2. STRONG BULLISH BODY
    # --------------------------------------------------------

    body = (
        close - open_price
    )

    body_ratio = (
        body /
        candle_range
    )

    body_pass = (
        body_ratio >= MIN_BODY_RATIO
    )

    checks.append(
        (
            "Strong Bullish Body",
            body_pass,
            f"{body_ratio * 100:.1f}% "
            f"< {MIN_BODY_RATIO * 100:.0f}%"
            if not body_pass
            else f"{body_ratio * 100:.1f}%"
        )
    )


    # --------------------------------------------------------
    # 3. PREVIOUS 10 CANDLE HIGH
    # --------------------------------------------------------

    previous_candles = candles[
        -(BREAKOUT_LOOKBACK + 1):-1
    ]

    if len(previous_candles) < BREAKOUT_LOOKBACK:

        breakout_pass = False

        previous_high = None

        breakout_value = (
            "Not enough previous candles"
        )

    else:

        previous_high = max(
            float(c[2])
            for c in previous_candles
        )

        breakout_pass = (
            close > previous_high
        )

        if breakout_pass:

            breakout_value = (
                f"Close {format_price(close)} "
                f"> High {format_price(previous_high)}"
            )

        else:

            breakout_value = (
                f"Close {format_price(close)} "
                f"<= High {format_price(previous_high)}"
            )

    checks.append(
        (
            "10-Candle Breakout",
            breakout_pass,
            breakout_value
        )
    )


    # --------------------------------------------------------
    # 4. VOLUME MOMENTUM
    # --------------------------------------------------------

    volume_candles = candles[
        -(VOLUME_LOOKBACK + 1):-1
    ]

    if len(volume_candles) < VOLUME_LOOKBACK:

        volume_pass = False

        average_volume = 0.0

        volume_ratio = 0.0

        volume_value = (
            "Not enough previous candles"
        )

    else:

        average_volume = sum(
            float(c[5])
            for c in volume_candles
        ) / len(volume_candles)

        if average_volume <= 0:

            volume_pass = False

            volume_ratio = 0.0

            volume_value = (
                "Average volume <= 0"
            )

        else:

            volume_ratio = (
                volume /
                average_volume
            )

            volume_pass = (
                volume_ratio >=
                VOLUME_MULTIPLIER
            )

            if volume_pass:

                volume_value = (
                    f"{volume_ratio:.2f}x"
                )

            else:

                volume_value = (
                    f"{volume_ratio:.2f}x "
                    f"< {VOLUME_MULTIPLIER:.2f}x"
                )

    checks.append(
        (
            "Volume Momentum",
            volume_pass,
            volume_value
        )
    )


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

    if vwap is None:

        price_vwap_pass = False

        price_vwap_value = (
            "VWAP unavailable"
        )

    else:

        price_vwap_pass = (
            close > vwap
        )

        if price_vwap_pass:

            price_vwap_value = (
                f"Close {format_price(close)} "
                f"> VWAP {format_price(vwap)}"
            )

        else:

            price_vwap_value = (
                f"Close {format_price(close)} "
                f"<= VWAP {format_price(vwap)}"
            )

    checks.append(
        (
            "Price > VWAP",
            price_vwap_pass,
            price_vwap_value
        )
    )


    # --------------------------------------------------------
    # 6. VWAP RISING
    # --------------------------------------------------------

    if (
        vwap is None
        or previous_vwap is None
    ):

        vwap_rising_pass = False

        vwap_rising_value = (
            "Previous VWAP unavailable"
        )

    else:

        vwap_rising_pass = (
            vwap > previous_vwap
        )

        if vwap_rising_pass:

            vwap_rising_value = (
                f"{format_price(previous_vwap)} "
                f"→ {format_price(vwap)}"
            )

        else:

            vwap_rising_value = (
                f"{format_price(previous_vwap)} "
                f"→ {format_price(vwap)} "
                f"(not rising)"
            )

    checks.append(
        (
            "VWAP Rising",
            vwap_rising_pass,
            vwap_rising_value
        )
    )


    # --------------------------------------------------------
    # 7. 24H QUOTE VOLUME
    # --------------------------------------------------------

    quote_volume_pass = (
        quote_volume >=
        MIN_24H_QUOTE_VOLUME
    )

    checks.append(
        (
            "24H Volume",
            quote_volume_pass,
            f"${quote_volume:,.0f} "
            f"< ${MIN_24H_QUOTE_VOLUME:,.0f}"
            if not quote_volume_pass
            else f"${quote_volume:,.0f}"
        )
    )


    # ========================================================
    # COUNT FAILED CONDITIONS
    # ========================================================

    failed_conditions = [
        item
        for item in checks
        if not item[1]
    ]

    failed_count = len(
        failed_conditions
    )


    # ========================================================
    # DEBUG
    #
    # Only 1 or 2 failed conditions.
    # ========================================================

    if failed_count > 0:

        print_debug_if_needed(
            symbol,
            signal_candle,
            checks
        )


    # ========================================================
    # ALL CONDITIONS MUST PASS
    # ========================================================

    if failed_count != 0:

        return None


    # ========================================================
    # ALL CONDITIONS PASSED
    # ========================================================

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
        signal["body_ratio"] *
        100
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
    # SEND TELEGRAM FIRST
    # --------------------------------------------------------

    sent = send_telegram(
        message
    )


    # --------------------------------------------------------
    # ONLY START COOLDOWN IF TELEGRAM SUCCESSFUL
    # --------------------------------------------------------

    if sent:

        last_signal_time[symbol] = (
            time.time()
        )

        print(
            f"[COOLDOWN START] "
            f"{symbol} | "
            f"{COOLDOWN_HOURS} hours"
        )

    else:

        print(
            f"[COOLDOWN NOT STARTED] "
            f"{symbol} | Telegram failed"
        )


# ============================================================
# MAIN SCAN
# ============================================================

def scan():

    print("")
    print(
        "=================================================="
    )

    print(
        f"SCAN "
        f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC"
    )

    print(
        "=================================================="
    )


    # --------------------------------------------------------
    # GET SYMBOLS
    # --------------------------------------------------------

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
    # GET 24H VOLUMES
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
    # OPTIONAL LIMIT
    # --------------------------------------------------------

    if MAX_SYMBOLS is not None:

        symbols = symbols[
            :MAX_SYMBOLS
        ]


    print(
        f"[INFO] Symbols to scan: "
        f"{len(symbols)}"
    )


    # ========================================================
    # ANALYZE EACH SYMBOL
    # ========================================================

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
                    f"{format_price(signal['price'])} | "
                    f"VWAP="
                    f"{format_price(signal['vwap'])} | "
                    f"Volume="
                    f"{signal['volume_ratio']:.2f}x"
                )


                send_buy_signal(
                    signal
                )


            # ------------------------------------------------
            # PROGRESS
            # ------------------------------------------------

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


    # ========================================================
    # CLEAN OLD CANDLE STATE
    # ========================================================

    if len(processed_signals) > 20000:

        # Keep the most recent portion.
        processed_signals.clear()


# ============================================================
# MAIN LOOP
# ============================================================

def main():

    print("")
    print(
        "=================================================="
    )

    print(
        "   BINANCE MOMENTUM + VWAP BUY BOT"
    )

    print(
        "=================================================="
    )

    print(
        f"Timeframe: {INTERVAL}"
    )

    print(
        f"Scan interval: {SCAN_SECONDS} seconds"
    )

    print(
        f"Breakout lookback: "
        f"{BREAKOUT_LOOKBACK} candles"
    )

    print(
        f"Volume multiplier: "
        f"{VOLUME_MULTIPLIER}x"
    )

    print(
        f"Volume lookback: "
        f"{VOLUME_LOOKBACK} candles"
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
        "Candle processing: "
        "CLOSED CANDLES ONLY"
    )

    print(
        "Debug: "
        "ONLY 1-2 FAILED CONDITIONS"
    )

    print(
        "Strategy: BUY ONLY"
    )

    print(
        "=================================================="
    )

    print("")


    # ========================================================
    # CONTINUOUS LOOP
    # ========================================================

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
