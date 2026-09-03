import os
import time
import requests
from datetime import datetime, timezone

# ============================================================
# BINANCE BEARISH FVG TELEGRAM BOT
# ============================================================
#
# FLOW:
#
# 1) 24H quote volume >= $20M
# 2) Live bullish trend:
#       Current Price > EMA20 > EMA50 > EMA100
#    EMA values are calculated from 120 x 1H candles,
#    with the current live price used for the current 1H candle.
#
# 3) ONLY NEW FVGs are allowed:
#    - FVG must form AFTER BOT_START_TIME
#    - FVG must form AFTER bullish trend confirmation
#
# 4) FINAL FVG SIGNAL -> Telegram
#
# 5) Monitor:
#    - Target = C3 High - 1.7%
#    - If target reached -> TARGET
#    - If price crosses above C3 High -> CANCELLED
#
# IMPORTANT:
# Historical candles are used ONLY for EMA calculation.
# Historical FVGs are NEVER activated.
#
# ============================================================


# ============================================================
# SETTINGS
# ============================================================

BINANCE_BASE_URL = "https://data-api.binance.vision"

MIN_QUOTE_VOLUME_24H = 20_000_000

FVG_MIN_RATIO = 0.50
TARGET_PERCENT = 1.7

FVG_INTERVALS = ["15m", "1h"]

EMA_FAST = 20
EMA_MID = 50
EMA_SLOW = 100

# EMA100 needs at least 100 values.
# 120 gives a small warm-up buffer.
TREND_KLINE_LIMIT = 120

# FVG does NOT use 100 historical candles.
# We only need a few recent candles to build C1/C2/C3.
FVG_KLINE_LIMIT = 6

SCAN_INTERVAL_SECONDS = 60


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
).strip()


# ============================================================
# BOT START TIME
# ============================================================
#
# VERY IMPORTANT:
#
# Any FVG whose Candle 3 closed BEFORE this moment
# will NEVER generate a signal.
#
# This prevents old/historical FVG signals after restart.
#
# ============================================================

BOT_START_TIME = time.time()


# ============================================================
# STATE
# ============================================================

# One active FVG per symbol.
active_fvgs = {}

# Last FVG Candle 3 timestamp already processed.
last_seen_fvg = {}

# For every symbol we remember when bullish trend
# was first confirmed during the current bullish phase.
trend_confirmed_since = {}


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "Binance-FVG-Telegram-Bot/1.0"
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
            timeout=20
        )

        response.raise_for_status()

        return response.json()

    except Exception as e:
        print(
            f"[BINANCE ERROR] "
            f"{endpoint}: {e}"
        )

        return None


# ============================================================
# TELEGRAM
# ============================================================

def telegram_request(method, payload=None):

    if not TELEGRAM_BOT_TOKEN:
        print("[TELEGRAM ERROR] TELEGRAM_BOT_TOKEN is missing.")
        return None

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/{method}"
    )

    try:
        response = session.post(
            url,
            json=payload or {},
            timeout=20
        )

        response.raise_for_status()

        return response.json()

    except Exception as e:
        print(
            f"[TELEGRAM ERROR] "
            f"{method}: {e}"
        )

        return None


def send_telegram(message):

    if not TELEGRAM_CHAT_ID:
        print(
            "[TELEGRAM ERROR] "
            "TELEGRAM_CHAT_ID is missing."
        )
        return False

    result = telegram_request(
        "sendMessage",
        {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
    )

    if result and result.get("ok"):
        return True

    return False


def test_telegram_connection():

    result = telegram_request("getMe")

    if result and result.get("ok"):

        bot_name = result["result"].get(
            "username",
            "unknown"
        )

        print(
            f"[TELEGRAM] Connected: @{bot_name}"
        )

        return True

    print(
        "[TELEGRAM] Connection test failed."
    )

    return False


# ============================================================
# SYMBOLS
# ============================================================

def get_spot_usdt_symbols():

    data = binance_get(
        "/api/v3/exchangeInfo"
    )

    if not data:
        return []

    symbols = []

    for item in data.get("symbols", []):

        if (
            item.get("status") == "TRADING"
            and item.get("quoteAsset") == "USDT"
            and item.get("isSpotTradingAllowed") is True
        ):
            symbols.append(
                item["symbol"]
            )

    return symbols


# ============================================================
# 24H VOLUME + CURRENT PRICE
# ============================================================

def get_24h_market_data():

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
        except Exception:
            quote_volume = 0.0

        try:
            last_price = float(
                item.get(
                    "lastPrice",
                    0
                )
            )
        except Exception:
            last_price = 0.0

        result[symbol] = {
            "quote_volume": quote_volume,
            "last_price": last_price
        }

    return result


# ============================================================
# KLINES
# ============================================================

def get_klines(
    symbol,
    interval,
    limit
):

    data = binance_get(
        "/api/v3/klines",
        {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        }
    )

    if not data:
        return []

    return data


def get_closed_klines(
    symbol,
    interval,
    limit
):

    candles = get_klines(
        symbol,
        interval,
        limit + 2
    )

    if not candles:
        return []

    now_ms = int(
        time.time() * 1000
    )

    closed = []

    for candle in candles:

        close_time = int(
            candle[6]
        )

        if close_time <= now_ms:
            closed.append(candle)

    return closed[-limit:]


# ============================================================
# EMA
# ============================================================

def calculate_ema(values, period):

    if not values:
        return None

    if len(values) < period:
        return None

    multiplier = 2 / (
        period + 1
    )

    # Initial EMA seed = SMA
    ema = sum(
        values[:period]
    ) / period

    for price in values[period:]:

        ema = (
            price * multiplier
            + ema * (
                1 - multiplier
            )
        )

    return ema


# ============================================================
# LIVE 1H TREND
# ============================================================

def get_bullish_trend(
    symbol,
    current_price
):
    """
    Uses 120 x 1H candles.

    EMA20 / EMA50 / EMA100 are calculated using:
        - closed historical 1H closes
        - current live price replacing the current 1H close

    Final condition:

        Current Price > EMA20 > EMA50 > EMA100
    """

    candles = get_klines(
        symbol,
        "1h",
        TREND_KLINE_LIMIT
    )

    if not candles:
        return None

    if len(candles) < EMA_SLOW:
        return None

    now_ms = int(
        time.time() * 1000
    )

    closes = []

    for candle in candles:

        close_time = int(
            candle[6]
        )

        close_price = float(
            candle[4]
        )

        # If candle is still open,
        # use LIVE price instead of old candle close.
        if close_time > now_ms:

            closes.append(
                current_price
            )

        else:

            closes.append(
                close_price
            )

    if len(closes) < EMA_SLOW:
        return None

    ema20 = calculate_ema(
        closes,
        EMA_FAST
    )

    ema50 = calculate_ema(
        closes,
        EMA_MID
    )

    ema100 = calculate_ema(
        closes,
        EMA_SLOW
    )

    if (
        ema20 is None
        or ema50 is None
        or ema100 is None
    ):
        return None

    bullish = (
        current_price > ema20
        and ema20 > ema50
        and ema50 > ema100
    )

    return {
        "bullish": bullish,
        "current_price": current_price,
        "ema20": ema20,
        "ema50": ema50,
        "ema100": ema100
    }


# ============================================================
# BEARISH FVG DETECTION
# ============================================================

def detect_bearish_fvg(
    candles,
    i
):

    """
    i = Candle 3 index.

    Uses:
        C1 = i - 2
        C2 = i - 1
        C3 = i

    Original FVG rules are unchanged.
    """

    if (
        i < 2
        or i >= len(candles)
    ):
        return None

    c1 = candles[i - 2]
    c2 = candles[i - 1]
    c3 = candles[i]

    c1_open = float(
        c1[1]
    )

    c1_low = float(
        c1[3]
    )

    c1_close = float(
        c1[4]
    )

    c2_open = float(
        c2[1]
    )

    c2_close = float(
        c2[4]
    )

    c3_high = float(
        c3[2]
    )

    # --------------------------------------------------------
    # C1 bearish
    # --------------------------------------------------------

    if c1_close >= c1_open:
        return None

    # --------------------------------------------------------
    # C2 bearish
    # --------------------------------------------------------

    if c2_close >= c2_open:
        return None

    # --------------------------------------------------------
    # C2 body
    # --------------------------------------------------------

    body_low = min(
        c2_open,
        c2_close
    )

    body_high = max(
        c2_open,
        c2_close
    )

    body_size = abs(
        c2_open - c2_close
    )

    if body_size <= 0:
        return None

    # --------------------------------------------------------
    # Bearish FVG
    #
    # C1 Low > C3 High
    # --------------------------------------------------------

    if c1_low <= c3_high:
        return None

    fvg_low = c3_high

    fvg_high = c1_low

    fvg_size = (
        fvg_high
        - fvg_low
    )

    if fvg_size <= 0:
        return None

    # --------------------------------------------------------
    # FVG completely inside C2 body
    # --------------------------------------------------------

    if fvg_low < body_low:
        return None

    if fvg_high > body_high:
        return None

    # --------------------------------------------------------
    # FVG >= 50% of C2 body
    # --------------------------------------------------------

    fvg_ratio = (
        fvg_size
        / body_size
    )

    if fvg_ratio < FVG_MIN_RATIO:
        return None

    # --------------------------------------------------------
    # Target
    #
    # C3 High - 1.7%
    # --------------------------------------------------------

    target = (
        c3_high
        * (
            1
            - TARGET_PERCENT / 100
        )
    )

    return {
        "c1_open_time": int(
            c1[0]
        ),

        "c2_open_time": int(
            c2[0]
        ),

        "c3_open_time": int(
            c3[0]
        ),

        "c3_close_time": int(
            c3[6]
        ),

        "c3_high": c3_high,

        "fvg_low": fvg_low,

        "fvg_high": fvg_high,

        "fvg_size": fvg_size,

        "fvg_ratio": fvg_ratio,

        "target": target
    }


# ============================================================
# FIND NEW FVG
# ============================================================

def find_new_bearish_fvg(
    symbol,
    interval,
    eligible_after
):

    """
    IMPORTANT:

    We do NOT scan 100 historical candles.

    Only the latest few candles are used to construct
    a possible C1/C2/C3 FVG.

    C3 MUST close after eligible_after.

    eligible_after is:
        max(
            BOT_START_TIME,
            trend confirmation time
        )
    """

    candles = get_closed_klines(
        symbol,
        interval,
        FVG_KLINE_LIMIT
    )

    if len(candles) < 3:
        return None

    # Latest possible C3
    # is the last closed candle.
    latest_index = len(candles) - 1

    # Check newest first.
    for i in range(
        latest_index,
        1 - 1,
        -1
    ):

        fvg = detect_bearish_fvg(
            candles,
            i
        )

        if not fvg:
            continue

        c3_close_time = (
            fvg["c3_close_time"]
        )

        # ----------------------------------------------------
        # CRITICAL:
        # Ignore all FVGs formed before bot/trend eligibility.
        # ----------------------------------------------------

        if (
            c3_close_time / 1000
            <= eligible_after
        ):
            continue

        # ----------------------------------------------------
        # Avoid duplicate detection.
        # ----------------------------------------------------

        fvg_id = (
            interval,
            fvg["c3_open_time"]
        )

        if (
            last_seen_fvg.get(symbol)
            == fvg_id
        ):
            continue

        last_seen_fvg[
            symbol
        ] = fvg_id

        return fvg

    return None


# ============================================================
# FVG SIGNAL MESSAGE
# ============================================================

def build_signal_message(
    symbol,
    interval,
    fvg,
    trend
):

    now = datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )

    return (
        f"🔴 <b>BEARISH FVG SIGNAL</b>\n\n"

        f"<b>Symbol:</b> {symbol}\n"
        f"<b>Timeframe:</b> {interval}\n"
        f"<b>Time:</b> {now}\n\n"

        f"<b>Current Price:</b> "
        f"{trend['current_price']:.12g}\n"

        f"<b>EMA20:</b> "
        f"{trend['ema20']:.12g}\n"

        f"<b>EMA50:</b> "
        f"{trend['ema50']:.12g}\n"

        f"<b>EMA100:</b> "
        f"{trend['ema100']:.12g}\n\n"

        f"<b>C3 High:</b> "
        f"{fvg['c3_high']:.12g}\n"

        f"<b>FVG Low:</b> "
        f"{fvg['fvg_low']:.12g}\n"

        f"<b>FVG High:</b> "
        f"{fvg['fvg_high']:.12g}\n"

        f"<b>FVG Ratio:</b> "
        f"{fvg['fvg_ratio'] * 100:.2f}%\n\n"

        f"🎯 <b>Target:</b> "
        f"{fvg['target']:.12g}\n"

        f"⚠️ <b>Cancel if price crosses C3 High:</b> "
        f"{fvg['c3_high']:.12g}"
    )


# ============================================================
# ACTIVATE FVG
# ============================================================

def activate_fvg(
    symbol,
    interval,
    fvg,
    trend
):

    active_fvgs[symbol] = {
        "symbol": symbol,
        "interval": interval,
        "c3_high": fvg["c3_high"],
        "target": fvg["target"],
        "fvg_low": fvg["fvg_low"],
        "fvg_high": fvg["fvg_high"],
        "fvg_ratio": fvg["fvg_ratio"],
        "c3_open_time": fvg["c3_open_time"],
        "c3_close_time": fvg["c3_close_time"]
    }

    message = build_signal_message(
        symbol,
        interval,
        fvg,
        trend
    )

    sent = send_telegram(
        message
    )

    if sent:
        print(
            f"[FINAL SIGNAL] "
            f"{symbol} | {interval} | "
            f"C3 High={fvg['c3_high']:.12g} | "
            f"Target={fvg['target']:.12g}"
        )
    else:
        print(
            f"[SIGNAL ERROR] "
            f"{symbol} | {interval}"
        )


# ============================================================
# MONITOR ACTIVE FVG
# ============================================================

def monitor_active_fvg(
    symbol,
    current_price
):

    active = active_fvgs.get(
        symbol
    )

    if not active:
        return False

    c3_high = active[
        "c3_high"
    ]

    target = active[
        "target"
    ]

    # --------------------------------------------------------
    # TARGET HIT
    # --------------------------------------------------------

    if current_price <= target:

        message = (
            f"🎯 <b>TARGET HIT</b>\n\n"
            f"<b>Symbol:</b> {symbol}\n"
            f"<b>Interval:</b> "
            f"{active['interval']}\n"
            f"<b>Target:</b> "
            f"{target:.12g}\n"
            f"<b>Price:</b> "
            f"{current_price:.12g}"
        )

        send_telegram(
            message
        )

        print(
            f"[TARGET HIT] "
            f"{symbol} | "
            f"Price={current_price:.12g} | "
            f"Target={target:.12g}"
        )

        del active_fvgs[
            symbol
        ]

        return True

    # --------------------------------------------------------
    # CANCEL
    #
    # Price crosses above C3 High.
    # --------------------------------------------------------

    if current_price > c3_high:

        message = (
            f"❌ <b>FVG CANCELLED</b>\n\n"
            f"<b>Symbol:</b> {symbol}\n"
            f"<b>Interval:</b> "
            f"{active['interval']}\n"
            f"<b>C3 High:</b> "
            f"{c3_high:.12g}\n"
            f"<b>Price:</b> "
            f"{current_price:.12g}"
        )

        send_telegram(
            message
        )

        print(
            f"[CANCELLED] "
            f"{symbol} | "
            f"Price={current_price:.12g} | "
            f"C3 High={c3_high:.12g}"
        )

        del active_fvgs[
            symbol
        ]

        return True

    return False


# ============================================================
# PROCESS ONE SYMBOL
# ============================================================

def process_symbol(
    symbol,
    market_data
):

    market = market_data.get(
        symbol
    )

    if not market:
        return

    volume = market[
        "quote_volume"
    ]

    current_price = market[
        "last_price"
    ]

    # --------------------------------------------------------
    # Volume filter
    # --------------------------------------------------------

    if volume < MIN_QUOTE_VOLUME_24H:
        return

    # --------------------------------------------------------
    # If active FVG exists, monitor it first.
    # --------------------------------------------------------

    if symbol in active_fvgs:

        monitor_active_fvg(
            symbol,
            current_price
        )

        return

    # --------------------------------------------------------
    # LIVE TREND
    #
    # Current Price > EMA20 > EMA50 > EMA100
    # --------------------------------------------------------

    trend = get_bullish_trend(
        symbol,
        current_price
    )

    if not trend:
        return

    # --------------------------------------------------------
    # TREND NOT BULLISH
    #
    # Reset trend confirmation.
    # Therefore an FVG from the previous bullish phase
    # cannot be used later.
    # --------------------------------------------------------

    if not trend["bullish"]:

        if symbol in trend_confirmed_since:
            del trend_confirmed_since[
                symbol
            ]

        return

    # --------------------------------------------------------
    # FIRST MOMENT OF CURRENT BULLISH TREND
    # --------------------------------------------------------

    if symbol not in trend_confirmed_since:

        trend_confirmed_since[
            symbol
        ] = time.time()

        print(
            f"[TREND CONFIRMED] "
            f"{symbol} | "
            f"Price={current_price:.12g} | "
            f"EMA20={trend['ema20']:.12g} | "
            f"EMA50={trend['ema50']:.12g} | "
            f"EMA100={trend['ema100']:.12g}"
        )

    # --------------------------------------------------------
    # FVG eligibility begins ONLY after:
    #
    # 1) Bot started
    # 2) Current bullish trend was confirmed
    #
    # --------------------------------------------------------

    eligible_after = max(
        BOT_START_TIME,
        trend_confirmed_since[
            symbol
        ]
    )

    # --------------------------------------------------------
    # Check 15m then 1h
    # --------------------------------------------------------

    for interval in FVG_INTERVALS:

        fvg = find_new_bearish_fvg(
            symbol,
            interval,
            eligible_after
        )

        if not fvg:
            continue

        # ----------------------------------------------------
        # FINAL SIGNAL
        # ----------------------------------------------------

        activate_fvg(
            symbol,
            interval,
            fvg,
            trend
        )

        # Only one active FVG per symbol.
        break


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print(
        "BINANCE BEARISH FVG TELEGRAM BOT"
    )
    print("=" * 70)

    print(
        f"Min 24H volume: "
        f"${MIN_QUOTE_VOLUME_24H:,.0f}"
    )

    print(
        f"FVG minimum ratio: "
        f"{FVG_MIN_RATIO * 100:.0f}%"
    )

    print(
        f"Target: "
        f"{TARGET_PERCENT}% below C3 High"
    )

    print(
        f"FVG intervals: "
        f"{FVG_INTERVALS}"
    )

    print(
        f"Trend candles: "
        f"{TREND_KLINE_LIMIT} x 1H"
    )

    print(
        "Trend condition: "
        "Current Price > EMA20 > EMA50 > EMA100"
    )

    print(
        "Historical FVGs: BLOCKED"
    )

    print(
        "FVG eligibility: "
        "AFTER BOT START + AFTER TREND CONFIRMATION"
    )

    print(
        f"Scan interval: "
        f"{SCAN_INTERVAL_SECONDS}s"
    )

    print(
        f"Bot start: "
        f"{datetime.fromtimestamp("
        f"BOT_START_TIME, "
        f"tz=timezone.utc"
        f").strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )

    print("=" * 70)

    # --------------------------------------------------------
    # Telegram connection test
    # Does NOT send a Telegram message.
    # --------------------------------------------------------

    test_telegram_connection()

    # --------------------------------------------------------
    # Get symbols
    # --------------------------------------------------------

    symbols = get_spot_usdt_symbols()

    if not symbols:

        print(
            "[FATAL] "
            "No Spot USDT symbols found."
        )

        return

    print(
        f"[SYMBOLS] "
        f"{len(symbols)} Spot USDT symbols found."
    )

    # --------------------------------------------------------
    # Main loop
    # --------------------------------------------------------

    while True:

        cycle_start = time.time()

        print(
            "\n"
            + "=" * 70
        )

        print(
            f"[SCAN] "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )

        # ----------------------------------------------------
        # One bulk 24H request
        # Gives:
        #   volume
        #   current price
        # ----------------------------------------------------

        market_data = (
            get_24h_market_data()
        )

        if not market_data:

            print(
                "[SCAN ERROR] "
                "Could not get market data."
            )

            time.sleep(
                SCAN_INTERVAL_SECONDS
            )

            continue

        # ----------------------------------------------------
        # Process symbols
        # ----------------------------------------------------

        qualified_count = 0

        for symbol in symbols:

            market = market_data.get(
                symbol
            )

            if not market:
                continue

            volume = market[
                "quote_volume"
            ]

            if volume < MIN_QUOTE_VOLUME_24H:
                continue

            qualified_count += 1

            try:

                process_symbol(
                    symbol,
                    market_data
                )

            except Exception as e:

                print(
                    f"[SYMBOL ERROR] "
                    f"{symbol}: {e}"
                )

            # Small delay helps avoid API bursts.
            time.sleep(0.03)

        print(
            f"[VOLUME QUALIFIED COUNT] "
            f"{qualified_count}"
        )

        # ----------------------------------------------------
        # Cycle timing
        # ----------------------------------------------------

        elapsed = (
            time.time()
            - cycle_start
        )

        sleep_for = max(
            1,
            SCAN_INTERVAL_SECONDS
            - elapsed
        )

        print(
            f"[SCAN COMPLETE] "
            f"{elapsed:.1f}s | "
            f"Next scan in {sleep_for:.1f}s"
        )

        time.sleep(
            sleep_for
        )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "\n[BOT] Stopped by user."
        )

    except Exception as e:

        print(
            f"[FATAL ERROR] {e}"
        )

        raise
