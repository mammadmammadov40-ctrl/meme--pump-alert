import os
import time
import requests
from datetime import datetime, timezone


# ============================================================
# BINANCE GAINERS + EMA BUY BOT
# ============================================================
#
# STRATEGY
#
# 1. Binance Spot USDT pairs only
# 2. 24H gain >= +3%
# 3. 24H quote volume >= 20,000,000 USDT
# 4. Timeframe = 1H
# 5. EMA20 > EMA50
# 6. Latest NEW closed 1H candle:
#       Open  > EMA20
#       Open  > EMA50
#       Close > EMA20
#       Close > EMA50
# 7. BUY signal
# 8. After successful Telegram signal -> 24H cooldown
#
# IMPORTANT STARTUP BEHAVIOR
#
# When bot starts:
# - It does NOT analyze the already-closed candle.
# - It only remembers that candle as the starting point.
# - It waits for the NEXT newly closed 1H candle.
#
# Therefore:
# START BOT
#      ↓
# Remember current closed candle
#      ↓
# NO SIGNAL
#      ↓
# Next 1H candle closes
#      ↓
# Check strategy
#      ↓
# If conditions pass -> BUY
#      ↓
# 24H cooldown
#
# ============================================================


# ============================================================
# CONFIG
# ============================================================

BINANCE_BASE_URL = "https://api.binance.com"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

INTERVAL = "1h"

SCAN_SECONDS = 20

MIN_24H_QUOTE_VOLUME = 20_000_000
MIN_24H_GAIN_PERCENT = 3.0

EMA_FAST = 20
EMA_SLOW = 50

COOLDOWN_HOURS = 24

KLINE_LIMIT = 100


# ============================================================
# SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "GainersEMABot/1.0"
})


# ============================================================
# STATE
# ============================================================

# symbol -> timestamp when cooldown started
cooldowns = {}

# symbol -> latest processed closed candle open time
last_processed_candle = {}


# ============================================================
# HTTP GET
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

        print(f"[BINANCE ERROR] {endpoint} | {e}")

        return None


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:

        print("[TELEGRAM ERROR] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing")

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

        response.raise_for_status()

        data = response.json()

        if data.get("ok") is True:

            return True

        print(f"[TELEGRAM ERROR] {data}")

        return False

    except Exception as e:

        print(f"[TELEGRAM ERROR] {e}")

        return False


# ============================================================
# GET SPOT USDT SYMBOLS
# ============================================================

def get_spot_usdt_symbols():

    data = binance_get("/api/v3/exchangeInfo")

    if not data:

        return set()

    symbols = set()

    for item in data.get("symbols", []):

        symbol = item.get("symbol")
        status = item.get("status")
        quote_asset = item.get("quoteAsset")
        spot_allowed = item.get("isSpotTradingAllowed")

        if (
            status == "TRADING"
            and quote_asset == "USDT"
            and spot_allowed is True
        ):

            symbols.add(symbol)

    return symbols


# ============================================================
# GET BINANCE GAINERS
# ============================================================
#
# Binance 24H ticker provides:
# - priceChangePercent
# - quoteVolume
#
# We use these values to create the gainers list.
#
# ============================================================

def get_gainers():

    spot_symbols = get_spot_usdt_symbols()

    if not spot_symbols:

        return []

    data = binance_get("/api/v3/ticker/24hr")

    if not data:

        return []

    gainers = []

    for ticker in data:

        symbol = ticker.get("symbol")

        if symbol not in spot_symbols:
            continue

        try:

            gain_percent = float(
                ticker.get("priceChangePercent", 0)
            )

            quote_volume = float(
                ticker.get("quoteVolume", 0)
            )

        except (TypeError, ValueError):

            continue

        # Minimum 24H gain
        if gain_percent < MIN_24H_GAIN_PERCENT:
            continue

        # Minimum 24H quote volume
        if quote_volume < MIN_24H_QUOTE_VOLUME:
            continue

        gainers.append({
            "symbol": symbol,
            "gain_percent": gain_percent,
            "quote_volume": quote_volume
        })

    # Highest gain first
    gainers.sort(
        key=lambda x: x["gain_percent"],
        reverse=True
    )

    return gainers


# ============================================================
# GET CLOSED KLINES
# ============================================================

def get_closed_klines(symbol):

    data = binance_get(
        "/api/v3/klines",
        params={
            "symbol": symbol,
            "interval": INTERVAL,
            "limit": KLINE_LIMIT
        }
    )

    if not data:

        return []

    now_ms = int(time.time() * 1000)

    closed = []

    for candle in data:

        close_time = int(candle[6])

        # Ignore currently forming candle
        if close_time <= now_ms:

            closed.append(candle)

    return closed


# ============================================================
# EMA
# ============================================================

def calculate_ema(values, period):

    if len(values) < period:

        return None

    multiplier = 2 / (period + 1)

    # SMA seed
    ema = sum(values[:period]) / period

    for price in values[period:]:

        ema = (
            (price - ema) * multiplier
            + ema
        )

    return ema


# ============================================================
# COOLDOWN
# ============================================================

def get_cooldown_remaining(symbol):

    started = cooldowns.get(symbol)

    if started is None:

        return 0

    elapsed = time.time() - started

    remaining = (
        COOLDOWN_HOURS * 3600
        - elapsed
    )

    if remaining <= 0:

        del cooldowns[symbol]

        return 0

    return remaining


def is_on_cooldown(symbol):

    remaining = get_cooldown_remaining(symbol)

    return remaining > 0


# ============================================================
# STRATEGY CHECK
# ============================================================

def check_strategy(candles):

    if len(candles) < EMA_SLOW:

        return None

    closes = [
        float(candle[4])
        for candle in candles
    ]

    ema20 = calculate_ema(
        closes,
        EMA_FAST
    )

    ema50 = calculate_ema(
        closes,
        EMA_SLOW
    )

    if ema20 is None or ema50 is None:

        return None

    latest = candles[-1]

    open_price = float(latest[1])
    close_price = float(latest[4])

    # ========================================================
    # CONDITION 1
    # ========================================================

    if not (ema20 > ema50):

        return None

    # ========================================================
    # CONDITION 2
    # ========================================================

    if not (
        open_price > ema20
        and
        open_price > ema50
    ):

        return None

    # ========================================================
    # CONDITION 3
    # ========================================================

    if not (
        close_price > ema20
        and
        close_price > ema50
    ):

        return None

    return {
        "ema20": ema20,
        "ema50": ema50,
        "open": open_price,
        "close": close_price
    }


# ============================================================
# FORMAT VOLUME
# ============================================================

def format_volume(value):

    if value >= 1_000_000_000:

        return f"{value / 1_000_000_000:.2f}B"

    if value >= 1_000_000:

        return f"{value / 1_000_000:.2f}M"

    if value >= 1_000:

        return f"{value / 1_000:.2f}K"

    return f"{value:.0f}"


# ============================================================
# FORMAT TIME
# ============================================================

def format_utc(ms):

    dt = datetime.fromtimestamp(
        ms / 1000,
        tz=timezone.utc
    )

    return dt.strftime(
        "%Y-%m-%d %H:%M UTC"
    )


# ============================================================
# SEND BUY SIGNAL
# ============================================================

def send_buy_signal(
    symbol,
    gain_percent,
    quote_volume,
    result,
    candle
):

    close_price = result["close"]
    ema20 = result["ema20"]
    ema50 = result["ema50"]

    candle_open_time = int(candle[0])

    candle_time = format_utc(
        candle_open_time
    )

    message = (
        f"🟢 <b>BUY SIGNAL</b>\n\n"

        f"<b>{symbol}</b>\n"

        f"⏱ Timeframe: <b>1H</b>\n\n"

        f"📈 <b>24H Gain:</b> +{gain_percent:.2f}%\n"

        f"💰 <b>24H Volume:</b> "
        f"{format_volume(quote_volume)} USDT\n\n"

        f"📊 <b>EMA20:</b> {ema20:.8f}\n"
        f"📊 <b>EMA50:</b> {ema50:.8f}\n\n"

        f"🕯 <b>Candle Open:</b> "
        f"{result['open']:.8f}\n"

        f"🕯 <b>Candle Close:</b> "
        f"{close_price:.8f}\n\n"

        f"💵 <b>Signal Price:</b> "
        f"{close_price:.8f}\n\n"

        f"🕐 <b>Candle:</b> {candle_time}\n\n"

        f"⏳ <b>24H COOLDOWN</b>"
    )

    return send_telegram(message)


# ============================================================
# ANALYZE SYMBOL
# ============================================================

def analyze_symbol(
    symbol,
    gain_percent,
    quote_volume
):

    # ========================================================
    # CHECK COOLDOWN
    # ========================================================

    if is_on_cooldown(symbol):

        return

    # ========================================================
    # GET CLOSED CANDLES
    # ========================================================

    candles = get_closed_klines(symbol)

    if not candles:

        return

    latest_candle = candles[-1]

    latest_time = int(
        latest_candle[0]
    )

    # ========================================================
    # STARTUP PROTECTION
    # ========================================================
    #
    # IMPORTANT:
    #
    # If this symbol has never been seen since startup,
    # DO NOT analyze the existing candle.
    #
    # Just remember it.
    #
    # The strategy starts with the NEXT newly closed candle.
    #
    # ========================================================

    previous_time = last_processed_candle.get(symbol)

    if previous_time is None:

        last_processed_candle[symbol] = latest_time

        print(
            f"[INIT] {symbol} | "
            f"Baseline candle saved | "
            f"Waiting for next 1H close"
        )

        return

    # ========================================================
    # SAME CANDLE
    # ========================================================

    if previous_time == latest_time:

        return

    # ========================================================
    # NEW CLOSED CANDLE
    # ========================================================

    last_processed_candle[symbol] = latest_time

    print(
        f"[NEW 1H CANDLE] {symbol} | "
        f"{format_utc(latest_time)}"
    )

    # ========================================================
    # CHECK STRATEGY
    # ========================================================

    result = check_strategy(candles)

    if result is None:

        print(
            f"[NO SIGNAL] {symbol} | "
            f"1H conditions not satisfied"
        )

        return

    # ========================================================
    # SEND TELEGRAM
    # ========================================================

    sent = send_buy_signal(
        symbol=symbol,
        gain_percent=gain_percent,
        quote_volume=quote_volume,
        result=result,
        candle=latest_candle
    )

    # ========================================================
    # VERY IMPORTANT
    #
    # COOLDOWN STARTS ONLY AFTER SUCCESSFUL TELEGRAM SEND
    # ========================================================

    if sent:

        cooldowns[symbol] = time.time()

        print(
            f"[SIGNAL SENT] {symbol} | "
            f"24H cooldown started"
        )

    else:

        print(
            f"[SIGNAL NOT SENT] {symbol} | "
            f"Cooldown NOT started"
        )


# ============================================================
# CLEANUP
# ============================================================

def cleanup_state(active_symbols):

    active_set = set(active_symbols)

    # Remove candle tracking for symbols
    # that are no longer in the +3% gainers list.

    for symbol in list(last_processed_candle.keys()):

        if symbol not in active_set:

            del last_processed_candle[symbol]


# ============================================================
# MAIN LOOP
# ============================================================

def main():

    print("=" * 60)

    print("BINANCE GAINERS + EMA BUY BOT")

    print("=" * 60)

    print(
        f"Timeframe: {INTERVAL}"
    )

    print(
        f"Scan interval: {SCAN_SECONDS} seconds"
    )

    print(
        f"Minimum 24H gain: +{MIN_24H_GAIN_PERCENT:.2f}%"
    )

    print(
        f"Minimum 24H volume: "
        f"{MIN_24H_QUOTE_VOLUME:,} USDT"
    )

    print(
        f"EMA: {EMA_FAST}/{EMA_SLOW}"
    )

    print(
        f"Cooldown: {COOLDOWN_HOURS} hours"
    )

    print(
        "Startup mode: WAIT FOR NEW 1H CANDLE"
    )

    print("=" * 60)

    while True:

        try:

            print(
                f"\n[SCAN] "
                f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC"
            )

            # =================================================
            # GET CURRENT GAINERS
            # =================================================

            gainers = get_gainers()

            if not gainers:

                print(
                    "[INFO] No eligible gainers found"
                )

                time.sleep(SCAN_SECONDS)

                continue

            print(
                f"[GAINERS] "
                f"{len(gainers)} coins meet filters"
            )

            active_symbols = [
                item["symbol"]
                for item in gainers
            ]

            cleanup_state(active_symbols)

            # =================================================
            # ANALYZE
            # =================================================

            for index, item in enumerate(gainers, start=1):

                symbol = item["symbol"]

                gain_percent = item["gain_percent"]

                quote_volume = item["quote_volume"]

                analyze_symbol(
                    symbol=symbol,
                    gain_percent=gain_percent,
                    quote_volume=quote_volume
                )

                # Progress every 10 symbols
                if index % 10 == 0:

                    print(
                        f"[PROGRESS] "
                        f"{index}/{len(gainers)}"
                    )

            # =================================================
            # WAIT
            # =================================================

            time.sleep(SCAN_SECONDS)

        except KeyboardInterrupt:

            print(
                "\n[BOT STOPPED]"
            )

            break

        except Exception as e:

            print(
                f"[MAIN ERROR] {e}"
            )

            time.sleep(SCAN_SECONDS)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
