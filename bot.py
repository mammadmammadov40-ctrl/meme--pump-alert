import os
import time
import requests
from datetime import datetime, timezone


# ============================================================
# BINANCE MOMENTUM + VWAP BUY BOT
# ============================================================
#
# TIMEFRAME:
# 5m
#
# BUY CONDITIONS:
#
# 1. Latest CLOSED candle must be bullish
# 2. Bullish body >= 80% of total candle range
# 3. Current candle volume > EACH of previous 10 candles
# 4. Current candle range > EACH of previous 10 candles
# 5. Buy Volume > Sell Volume
# 6. Current close breaks previous 10 candle high
# 7. Current close > VWAP
# 8. Current VWAP > previous VWAP
# 9. 24H quote volume >= $10M
#
# AFTER SIGNAL:
# Same coin is blocked for 50 CLOSED 5m candles.
#
# OLD SYSTEM REMOVED:
# - 20 candle average volume
# - 1.5x volume multiplier
# - 24 hour cooldown
# ============================================================


BINANCE_BASE_URL = "https://api.binance.com"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# ============================================================
# SETTINGS
# ============================================================

INTERVAL = "5m"

SCAN_SECONDS = 20


# ============================================================
# PREVIOUS CANDLE COMPARISON
# ============================================================

LOOKBACK_CANDLES = 10


# ============================================================
# STRONG BULLISH BODY
# ============================================================

MIN_BODY_RATIO = 0.80


# ============================================================
# 50-CANDLE COOLDOWN
# ============================================================

COOLDOWN_CANDLES = 50


# ============================================================
# MINIMUM 24H QUOTE VOLUME
# ============================================================

MIN_24H_QUOTE_VOLUME = 10_000_000


# ============================================================
# ALL BINANCE SPOT USDT SYMBOLS
# ============================================================

MAX_SYMBOLS = None


# ============================================================
# SESSION STATE
# ============================================================

# Prevent processing same candle more than once.
processed_signals = set()


# ============================================================
# COOLDOWN STATE
#
# symbol:
# {
#     "signal_candle": candle_open_time,
#     "signal_index": global candle index
# }
#
# We use candle timestamps instead of wall-clock time.
# ============================================================

cooldown_state = {}


# ============================================================
# LAST CLOSED CANDLE
# ============================================================

last_closed_candle = {}


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "MomentumVWAPBot/2.0"
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

def get_klines(symbol, limit=300):

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

        # Only fully closed candles.
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
            high +
            low +
            close
        ) / 3.0

        cumulative_pv += (
            typical_price *
            volume
        )

        cumulative_volume += volume

    if cumulative_volume <= 0:

        return None

    return (
        cumulative_pv /
        cumulative_volume
    )


# ============================================================
# PREVIOUS VWAP
#
# VWAP calculated without latest signal candle.
# ============================================================

def calculate_previous_vwap(candles):

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
# FORMAT VOLUME
# ============================================================

def format_volume(value):

    if value >= 1_000_000:

        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:

        return f"${value / 1_000:.2f}K"

    return f"${value:.2f}"


# ============================================================
# COOLDOWN CHECK
#
# IMPORTANT:
#
# Signal candle itself = candle 0
#
# Next closed candles:
# 1, 2, 3 ... 50
#
# During these 50 candles:
# NO NEW SIGNAL.
#
# After candle 50:
# NEW SIGNAL IS ALLOWED.
# ============================================================

def is_in_cooldown(
    symbol,
    current_candle_time
):

    if symbol not in cooldown_state:

        return False

    signal_candle_time = cooldown_state[
        symbol
    ]

    interval_seconds = 5 * 60

    elapsed_candles = int(
        (
            current_candle_time -
            signal_candle_time
        ) /
        (interval_seconds * 1000)
    )

    # Signal candle = 0
    #
    # 1 through 50 = blocked
    if elapsed_candles <= COOLDOWN_CANDLES:

        remaining = (
            COOLDOWN_CANDLES -
            elapsed_candles
        )

        print(
            f"[50-CANDLE COOLDOWN] "
            f"{symbol} | "
            f"{remaining} candles remaining"
        )

        return True

    # Cooldown finished.

    del cooldown_state[symbol]

    print(
        f"[COOLDOWN EXPIRED] "
        f"{symbol}"
    )

    return False


# ============================================================
# DEBUG
#
# Only print when 1 or 2 conditions fail.
# 3+ failures = silent.
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

    failed_count = len(
        failed
    )

    if failed_count == 0:

        return

    if failed_count > 2:

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
                f"  {name}: OK"
            )

        else:

            print(
                f"  {name}: FAILED ({value})"
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

    # ========================================================
    # 24H VOLUME
    # ========================================================

    if quote_volume < MIN_24H_QUOTE_VOLUME:

        return None


    # ========================================================
    # GET CLOSED CANDLES
    # ========================================================

    candles = get_klines(
        symbol,
        limit=300
    )

    minimum_required = (
        LOOKBACK_CANDLES + 2
    )

    if len(candles) < minimum_required:

        return None


    # ========================================================
    # LATEST CLOSED CANDLE
    # ========================================================

    signal_candle = candles[-1]

    signal_open_time = int(
        signal_candle[0]
    )

    signal_key = (
        symbol,
        signal_open_time
    )


    # ========================================================
    # SAME CANDLE PROCESSED
    # ========================================================

    if signal_key in processed_signals:

        return None


    # ========================================================
    # MARK AS PROCESSED
    # ========================================================

    processed_signals.add(
        signal_key
    )


    # ========================================================
    # 50 CANDLE COOLDOWN
    #
    # Check before expensive calculations.
    # ========================================================

    if is_in_cooldown(
        symbol,
        signal_open_time
    ):

        return None


    # ========================================================
    # CURRENT OHLCV
    # ========================================================

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

    # Binance kline index [7]:
    # Quote Asset Volume
    quote_asset_volume = float(
        signal_candle[7]
    )

    # Binance kline index [10]:
    # Taker Buy Quote Asset Volume
    buy_quote_volume = float(
        signal_candle[10]
    )

    sell_quote_volume = (
        quote_asset_volume -
        buy_quote_volume
    )


    # ========================================================
    # PREVIOUS 10 CANDLES
    # ========================================================

    previous_candles = candles[
        -(LOOKBACK_CANDLES + 1):-1
    ]

    if len(previous_candles) < LOOKBACK_CANDLES:

        return None


    # ========================================================
    # CHECKS
    # ========================================================

    checks = []


    # ========================================================
    # 1. BULLISH CANDLE
    # ========================================================

    bullish = (
        close > open_price
    )

    checks.append(
        (
            "Bullish Candle",
            bullish,
            (
                f"Close {format_price(close)} "
                f"> Open {format_price(open_price)}"
                if bullish
                else
                f"Close {format_price(close)} "
                f"<= Open {format_price(open_price)}"
            )
        )
    )


    # ========================================================
    # CURRENT RANGE
    # ========================================================

    current_range = (
        high - low
    )


    # ========================================================
    # 2. BODY >= 80%
    # ========================================================

    if current_range <= 0:

        body_ratio = 0.0

        body_pass = False

        body_value = (
            "Range <= 0"
        )

    else:

        body = (
            close -
            open_price
        )

        body_ratio = (
            body /
            current_range
        )

        body_pass = (
            body_ratio >=
            MIN_BODY_RATIO
        )

        body_value = (
            f"{body_ratio * 100:.1f}%"
        )

    checks.append(
        (
            "Bullish Body >= 80%",
            body_pass,
            body_value
        )
    )


    # ========================================================
    # 3. VOLUME > ALL PREVIOUS 10
    # ========================================================

    previous_volumes = [
        float(c[5])
        for c in previous_candles
    ]

    max_previous_volume = max(
        previous_volumes
    )

    volume_pass = (
        volume >
        max_previous_volume
    )

    if volume_pass:

        volume_value = (
            f"{volume:.2f} > "
            f"{max_previous_volume:.2f}"
        )

    else:

        volume_value = (
            f"{volume:.2f} <= "
            f"{max_previous_volume:.2f}"
        )

    checks.append(
        (
            "Volume > Previous 10",
            volume_pass,
            volume_value
        )
    )


    # ========================================================
    # 4. RANGE > ALL PREVIOUS 10
    # ========================================================

    previous_ranges = [

        float(c[2]) -
        float(c[3])

        for c in previous_candles
    ]

    max_previous_range = max(
        previous_ranges
    )

    range_pass = (
        current_range >
        max_previous_range
    )

    if range_pass:

        range_value = (
            f"{current_range:.8f} > "
            f"{max_previous_range:.8f}"
        )

    else:

        range_value = (
            f"{current_range:.8f} <= "
            f"{max_previous_range:.8f}"
        )

    checks.append(
        (
            "Range > Previous 10",
            range_pass,
            range_value
        )
    )


    # ========================================================
    # 5. BUY > SELL
    # ========================================================

    buy_sell_pass = (
        buy_quote_volume >
        sell_quote_volume
    )

    if buy_sell_pass:

        buy_sell_value = (
            f"Buy {format_volume(buy_quote_volume)} "
            f"> Sell {format_volume(sell_quote_volume)}"
        )

    else:

        buy_sell_value = (
            f"Buy {format_volume(buy_quote_volume)} "
            f"<= Sell {format_volume(sell_quote_volume)}"
        )

    checks.append(
        (
            "Buy Volume > Sell Volume",
            buy_sell_pass,
            buy_sell_value
        )
    )


    # ========================================================
    # 6. 10-CANDLE BREAKOUT
    # ========================================================

    previous_high = max(
        float(c[2])
        for c in previous_candles
    )

    breakout_pass = (
        close >
        previous_high
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


    # ========================================================
    # 7. VWAP
    # ========================================================

    vwap = calculate_session_vwap(
        candles
    )

    previous_vwap = (
        calculate_previous_vwap(
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
            close >
            vwap
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


    # ========================================================
    # 8. VWAP RISING
    # ========================================================

    if (
        vwap is None
        or previous_vwap is None
    ):

        vwap_rising_pass = False

        vwap_rising_value = (
            "VWAP unavailable"
        )

    else:

        vwap_rising_pass = (
            vwap >
            previous_vwap
        )

        if vwap_rising_pass:

            vwap_rising_value = (
                f"{format_price(previous_vwap)} "
                f"-> {format_price(vwap)}"
            )

        else:

            vwap_rising_value = (
                f"{format_price(previous_vwap)} "
                f"-> {format_price(vwap)} "
                f"(not rising)"
            )

    checks.append(
        (
            "VWAP Rising",
            vwap_rising_pass,
            vwap_rising_value
        )
    )


    # ========================================================
    # 9. 24H QUOTE VOLUME
    # ========================================================

    quote_volume_pass = (
        quote_volume >=
        MIN_24H_QUOTE_VOLUME
    )

    checks.append(
        (
            "24H Volume >= $10M",
            quote_volume_pass,
            (
                f"${quote_volume:,.0f}"
                if quote_volume_pass
                else
                f"${quote_volume:,.0f} "
                f"< $10,000,000"
            )
        )
    )


    # ========================================================
    # COUNT FAILED
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
    # ========================================================

    if failed_count > 0:

        print_debug_if_needed(
            symbol,
            signal_candle,
            checks
        )

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

        "range": current_range,

        "vwap": vwap,

        "previous_vwap": previous_vwap,

        "body_ratio": body_ratio,

        "volume": volume,

        "previous_max_volume": max_previous_volume,

        "previous_max_range": max_previous_range,

        "buy_volume": buy_quote_volume,

        "sell_volume": sell_quote_volume,

        "quote_volume": quote_volume,

        "previous_high": previous_high,

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

    current_volume = (
        signal["volume"]
    )

    previous_max_volume = (
        signal["previous_max_volume"]
    )

    current_range = (
        signal["range"]
    )

    previous_max_range = (
        signal["previous_max_range"]
    )

    buy_volume = (
        signal["buy_volume"]
    )

    sell_volume = (
        signal["sell_volume"]
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

        f"💪 <b>Bullish Body:</b> "
        f"{body_percent:.1f}%\n"

        f"🔥 <b>Current Volume:</b> "
        f"{current_volume:.2f}\n"

        f"📊 <b>Previous Max Volume:</b> "
        f"{previous_max_volume:.2f}\n\n"

        f"📏 <b>Current Range:</b> "
        f"{current_range:.8f}\n"

        f"📏 <b>Previous Max Range:</b> "
        f"{previous_max_range:.8f}\n\n"

        f"🟢 <b>Buy Volume:</b> "
        f"{format_volume(buy_volume)}\n"

        f"🔴 <b>Sell Volume:</b> "
        f"{format_volume(sell_volume)}\n\n"

        f"💵 <b>24H Volume:</b> "
        f"${quote_volume:,.0f}\n\n"

        f"🕐 <b>Candle:</b> "
        f"{candle_time}\n\n"

        "✅ BULLISH CANDLE\n"
        "✅ BODY ≥ 80%\n"
        "✅ VOLUME > PREVIOUS 10\n"
        "✅ RANGE > PREVIOUS 10\n"
        "✅ BUY > SELL\n"
        "✅ 10-CANDLE BREAKOUT\n"
        "✅ PRICE > VWAP\n"
        "✅ VWAP RISING\n"
        "✅ 24H VOLUME ≥ $10M\n\n"

        "⏳ <b>50-CANDLE COOLDOWN STARTED</b>"
    )


    # ========================================================
    # SEND TELEGRAM
    # ========================================================

    sent = send_telegram(
        message
    )


    # ========================================================
    # START 50-CANDLE COOLDOWN
    # ONLY IF TELEGRAM SUCCESSFUL
    # ========================================================

    if sent:

        cooldown_state[
            symbol
        ] = signal["candle_time"]

        print(
            f"[COOLDOWN START] "
            f"{symbol} | "
            f"{COOLDOWN_CANDLES} candles"
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


    # ========================================================
    # SYMBOLS
    # ========================================================

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


    # ========================================================
    # 24H VOLUMES
    # ========================================================

    volumes = get_24h_volumes()


    # ========================================================
    # SORT BY 24H VOLUME
    # ========================================================

    symbols = sorted(
        symbols,
        key=lambda s: volumes.get(
            s,
            0
        ),
        reverse=True
    )


    # ========================================================
    # OPTIONAL LIMIT
    # ========================================================

    if MAX_SYMBOLS is not None:

        symbols = symbols[
            :MAX_SYMBOLS
        ]


    print(
        f"[INFO] Symbols to scan: "
        f"{len(symbols)}"
    )


    # ========================================================
    # ANALYZE
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

                print("")

                print(
                    f"[BUY] {symbol} | "
                    f"Price="
                    f"{format_price(signal['price'])} | "
                    f"VWAP="
                    f"{format_price(signal['vwap'])} | "
                    f"Body="
                    f"{signal['body_ratio'] * 100:.1f}% | "
                    f"Buy="
                    f"{format_volume(signal['buy_volume'])} | "
                    f"Sell="
                    f"{format_volume(signal['sell_volume'])}"
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
    # CLEAN OLD PROCESSED STATE
    # ========================================================

    if len(processed_signals) > 20000:

        processed_signals.clear()


# ============================================================
# MAIN
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
        f"Previous candle comparison: "
        f"{LOOKBACK_CANDLES}"
    )

    print(
        f"Minimum bullish body: "
        f"{MIN_BODY_RATIO * 100:.0f}%"
    )

    print(
        "Volume rule: "
        "CURRENT > ALL PREVIOUS 10"
    )

    print(
        "Range rule: "
        "CURRENT > ALL PREVIOUS 10"
    )

    print(
        "Buy/Sell rule: "
        "BUY > SELL"
    )

    print(
        "Breakout: "
        "PREVIOUS 10 CANDLE HIGH"
    )

    print(
        f"Minimum 24H volume: "
        f"${MIN_24H_QUOTE_VOLUME:,.0f}"
    )

    print(
        f"Cooldown: "
        f"{COOLDOWN_CANDLES} CLOSED CANDLES"
    )

    print(
        "Candle processing: "
        "CLOSED CANDLES ONLY"
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
