import os
import time
import requests
from datetime import datetime, timezone


# ============================================================
# BINANCE GAINERS + EMA20 / EMA50 BUY BOT
# ============================================================
#
# TIMEFRAME:
#   1H
#
# SCAN:
#   Every 20 seconds
#
# UNIVERSE:
#   ALL Binance Spot USDT pairs
#
# FILTER 1:
#   24H Quote Volume >= 20,000,000 USDT
#
# FILTER 2:
#   Binance 24H price change >= +3%
#
# FILTER 3:
#   EMA20 > EMA50
#
# FILTER 4:
#   Last CLOSED 1H candle:
#
#       OPEN  > EMA20
#       OPEN  > EMA50
#
#       CLOSE > EMA20
#       CLOSE > EMA50
#
# SIGNAL:
#   BUY signal when all conditions are satisfied.
#
# COOLDOWN:
#   24 hours per symbol after signal.
#
# NO:
#   Top 100
#   Breakout
#   90% body
#   Confirmation candles
#   Reset candles
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

INTERVAL = "1h"

SCAN_SECONDS = 20

MIN_24H_QUOTE_VOLUME = 20_000_000

MIN_24H_GAIN_PERCENT = 3.0

EMA_FAST = 20

EMA_SLOW = 50

COOLDOWN_HOURS = 24

COOLDOWN_SECONDS = (
    COOLDOWN_HOURS * 60 * 60
)

KLINE_LIMIT = 100


# ============================================================
# STATE
# ============================================================

# symbol -> cooldown start timestamp
cooldowns = {}


# symbol -> last processed closed candle open time
last_processed_candle = {}


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "BinanceGainersEMABot/1.0"
})


# ============================================================
# BINANCE REQUEST
# ============================================================

def binance_get(
    endpoint,
    params=None
):

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
            "Missing TELEGRAM_BOT_TOKEN "
            "or TELEGRAM_CHAT_ID"
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

            print(
                "[TELEGRAM] Signal sent"
            )

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

        return set()

    symbols = set()

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

            symbols.add(
                symbol
            )

    return symbols


# ============================================================
# GET BINANCE 24H GAINERS
# ============================================================
#
# Returns:
#
# symbol
# priceChangePercent
# quoteVolume
#
# Then:
#
# 1. Spot USDT only
# 2. Volume >= 20M
# 3. Gain >= +3%
# 4. Sort highest gain first
#
# ============================================================

def get_gainers():

    spot_symbols = (
        get_spot_usdt_symbols()
    )

    if not spot_symbols:

        return []

    data = binance_get(
        "/api/v3/ticker/24hr"
    )

    if not data:

        return []

    gainers = []

    for item in data:

        symbol = item.get(
            "symbol"
        )

        if symbol not in spot_symbols:

            continue

        try:

            change_percent = float(
                item.get(
                    "priceChangePercent",
                    0
                )
            )

            quote_volume = float(
                item.get(
                    "quoteVolume",
                    0
                )
            )

        except Exception:

            continue

        # ====================================================
        # 24H VOLUME FILTER
        # ====================================================

        if (
            quote_volume
            < MIN_24H_QUOTE_VOLUME
        ):

            continue

        # ====================================================
        # 24H GAIN FILTER
        # ====================================================

        if (
            change_percent
            < MIN_24H_GAIN_PERCENT
        ):

            continue

        gainers.append({

            "symbol": symbol,

            "change_percent":
                change_percent,

            "quote_volume":
                quote_volume

        })


    # ========================================================
    # HIGHEST GAIN FIRST
    # ========================================================

    gainers.sort(
        key=lambda x:
        x["change_percent"],
        reverse=True
    )

    return gainers


# ============================================================
# GET CLOSED 1H KLINES
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

        # Only completely closed candles
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

    # SMA seed
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

    return float(
        candle[1]
    )


def candle_high(candle):

    return float(
        candle[2]
    )


def candle_low(candle):

    return float(
        candle[3]
    )


def candle_close(candle):

    return float(
        candle[4]
    )


def candle_time(candle):

    return int(
        candle[0]
    )


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
# FORMAT VOLUME
# ============================================================

def format_volume(volume):

    if volume >= 1_000_000_000:

        return (
            f"{volume / 1_000_000_000:.2f}B"
        )

    if volume >= 1_000_000:

        return (
            f"{volume / 1_000_000:.2f}M"
        )

    if volume >= 1_000:

        return (
            f"{volume / 1_000:.2f}K"
        )

    return f"{volume:.2f}"


# ============================================================
# FORMAT TIME
# ============================================================

def format_time(
    timestamp_ms
):

    return datetime.fromtimestamp(
        timestamp_ms / 1000,
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M UTC"
    )


# ============================================================
# COOLDOWN
# ============================================================

def is_on_cooldown(
    symbol
):

    signal_time = cooldowns.get(
        symbol
    )

    if signal_time is None:

        return False

    elapsed = (
        time.time()
        - signal_time
    )

    if elapsed >= COOLDOWN_SECONDS:

        cooldowns.pop(
            symbol,
            None
        )

        print(
            f"[COOLDOWN EXPIRED] "
            f"{symbol}"
        )

        return False

    remaining = (
        COOLDOWN_SECONDS
        - elapsed
    )

    remaining_hours = (
        remaining / 3600
    )

    print(
        f"[COOLDOWN] {symbol} | "
        f"{remaining_hours:.1f}h remaining"
    )

    return True


# ============================================================
# GET EMA VALUES FOR LAST CLOSED CANDLE
# ============================================================

def get_latest_ema_values(
    candles
):

    if len(candles) < EMA_SLOW:

        return None, None

    closes = [
        candle_close(c)
        for c in candles
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
# CHECK STRATEGY
# ============================================================

def check_strategy(
    symbol,
    candles,
    gain_percent,
    quote_volume
):

    if len(candles) < EMA_SLOW:

        return None


    # ========================================================
    # LAST CLOSED 1H CANDLE
    # ========================================================

    candle = candles[-1]


    open_price = candle_open(
        candle
    )

    close_price = candle_close(
        candle
    )


    # ========================================================
    # EMA
    # ========================================================

    ema20, ema50 = (
        get_latest_ema_values(
            candles
        )
    )

    if (
        ema20 is None
        or ema50 is None
    ):

        return None


    # ========================================================
    # CONDITION 1
    #
    # EMA20 > EMA50
    # ========================================================

    if not (
        ema20 > ema50
    ):

        return None


    # ========================================================
    # CONDITION 2
    #
    # OPEN ABOVE BOTH EMAs
    # ========================================================

    if not (
        open_price > ema20
        and
        open_price > ema50
    ):

        return None


    # ========================================================
    # CONDITION 3
    #
    # CLOSE ABOVE BOTH EMAs
    # ========================================================

    if not (
        close_price > ema20
        and
        close_price > ema50
    ):

        return None


    # ========================================================
    # ALL CONDITIONS PASSED
    # ========================================================

    return {

        "symbol": symbol,

        "open": open_price,

        "close": close_price,

        "ema20": ema20,

        "ema50": ema50,

        "gain_percent":
            gain_percent,

        "quote_volume":
            quote_volume,

        "candle_time":
            candle_time(candle)
    }


# ============================================================
# SEND BUY SIGNAL
# ============================================================

def send_buy_signal(
    data
):

    symbol = data[
        "symbol"
    ]

    message = (

        "🟢 <b>EMA20 / EMA50 BUY SIGNAL</b>\n\n"

        f"<b>{symbol}</b> — 1H\n\n"

        "📈 <b>BINANCE GAINER</b>\n"

        f"24H Change: "
        f"+{data['gain_percent']:.2f}%\n"

        f"24H Volume: "
        f"{format_volume(data['quote_volume'])} USDT\n\n"

        "📊 <b>EMA</b>\n"

        f"EMA20: "
        f"{format_price(data['ema20'])}\n"

        f"EMA50: "
        f"{format_price(data['ema50'])}\n\n"

        "🕯 <b>LAST CLOSED 1H CANDLE</b>\n"

        f"Open: "
        f"{format_price(data['open'])}\n"

        f"Close: "
        f"{format_price(data['close'])}\n\n"

        "✅ EMA20 > EMA50\n"
        "✅ Open > EMA20 & EMA50\n"
        "✅ Close > EMA20 & EMA50\n"
        "✅ 24H Gain ≥ +3%\n"
        "✅ 24H Volume ≥ 20M USDT\n\n"

        f"💰 <b>SIGNAL PRICE:</b> "
        f"{format_price(data['close'])}\n\n"

        f"🕐 Candle: "
        f"{format_time(data['candle_time'])}\n\n"

        "🔒 <b>24H COOLDOWN</b>"
    )


    sent = send_telegram(
        message
    )

    return sent


# ============================================================
# ANALYZE ONE SYMBOL
# ============================================================

def analyze_symbol(
    item
):

    symbol = item[
        "symbol"
    ]

    gain_percent = item[
        "change_percent"
    ]

    quote_volume = item[
        "quote_volume"
    ]


    # ========================================================
    # COOLDOWN
    # ========================================================

    if is_on_cooldown(
        symbol
    ):

        return


    # ========================================================
    # GET 1H CLOSED CANDLES
    # ========================================================

    candles = get_closed_klines(
        symbol
    )

    if len(candles) < EMA_SLOW:

        return


    latest = candles[-1]

    latest_time = candle_time(
        latest
    )


    # ========================================================
    # ONLY PROCESS EACH CLOSED CANDLE ONCE
    # ========================================================

    previous_time = (
        last_processed_candle.get(
            symbol
        )
    )

    if previous_time == latest_time:

        return


    last_processed_candle[
        symbol
    ] = latest_time


    # ========================================================
    # CHECK STRATEGY
    # ========================================================

    signal = check_strategy(
        symbol,
        candles,
        gain_percent,
        quote_volume
    )

    if not signal:

        return


    # ========================================================
    # SEND TELEGRAM
    # ========================================================

    sent = send_buy_signal(
        signal
    )


    # ========================================================
    # START COOLDOWN ONLY AFTER
    # SUCCESSFUL TELEGRAM SEND
    # ========================================================

    if sent:

        cooldowns[
            symbol
        ] = time.time()

        print(
            f"[BUY] {symbol} | "
            f"24H +{gain_percent:.2f}% | "
            f"Volume="
            f"{format_volume(quote_volume)} | "
            f"EMA20="
            f"{format_price(signal['ema20'])} | "
            f"EMA50="
            f"{format_price(signal['ema50'])} | "
            f"Signal="
            f"{format_price(signal['close'])}"
        )

    else:

        print(
            f"[WARNING] {symbol} | "
            "Telegram failed - "
            "cooldown NOT started"
        )


# ============================================================
# CLEAN OLD STATE
# ============================================================

def cleanup_state(
    current_symbols
):

    current_set = set(
        current_symbols
    )

    # ========================================================
    # Last processed candle state
    # ========================================================

    for symbol in list(
        last_processed_candle.keys()
    ):

        if symbol not in current_set:

            last_processed_candle.pop(
                symbol,
                None
            )


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
    # GET BINANCE GAINERS
    # ========================================================

    gainers = get_gainers()

    if not gainers:

        print(
            "[INFO] No Binance Gainers "
            "meeting filters."
        )

        return


    print(
        f"[INFO] Eligible Gainers: "
        f"{len(gainers)}"
    )


    # ========================================================
    # SHOW GAINERS
    # ========================================================

    print(
        "[GAINERS]"
    )

    for rank, item in enumerate(
        gainers,
        start=1
    ):

        print(
            f"{rank}. "
            f"{item['symbol']} | "
            f"+{item['change_percent']:.2f}% | "
            f"Volume="
            f"{format_volume(item['quote_volume'])}"
        )


    # ========================================================
    # CLEAN STATE
    # ========================================================

    cleanup_state(
        [
            item["symbol"]
            for item in gainers
        ]
    )


    # ========================================================
    # ANALYZE
    # ========================================================

    for index, item in enumerate(
        gainers,
        start=1
    ):

        symbol = item[
            "symbol"
        ]

        try:

            analyze_symbol(
                item
            )

        except Exception as e:

            print(
                f"[ERROR] "
                f"{symbol}: {e}"
            )

        if index % 10 == 0:

            print(
                f"[PROGRESS] "
                f"{index}/{len(gainers)}"
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
        "   BINANCE GAINERS EMA20 / EMA50 BUY BOT"
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
        f"Minimum 24H gain: "
        f"+{MIN_24H_GAIN_PERCENT:.1f}%"
    )

    print(
        f"Minimum 24H quote volume: "
        f"{format_volume(MIN_24H_QUOTE_VOLUME)} USDT"
    )

    print(
        f"EMA: "
        f"{EMA_FAST} / {EMA_SLOW}"
    )

    print(
        "EMA20 > EMA50: REQUIRED"
    )

    print(
        "Open > EMA20 & EMA50: REQUIRED"
    )

    print(
        "Close > EMA20 & EMA50: REQUIRED"
    )

    print(
        f"Cooldown: "
        f"{COOLDOWN_HOURS} hours"
    )

    print(
        "Top 100: DISABLED"
    )

    print(
        "Breakout: DISABLED"
    )

    print(
        "Confirmation candles: DISABLED"
    )

    print(
        "Reset candles: DISABLED"
    )

    print(
        "=================================================="
    )

    print("")


    # ========================================================
    # MAIN LOOP
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
