import os
import time
import threading
import requests

from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# BINANCE SPOT BOT
#
# 5M MOMENTUM
# +
# 600 CLOSED 15M REAL BREAKOUT
#
# BINANCE SPOT ONLY
# NO SOLANA
# NO BINANCE SQUARE
#
# ALERT ONLY
# NO AUTOMATIC ORDER
# ============================================================


# ============================================================
# ENV
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


# ============================================================
# BINANCE
# ============================================================

BINANCE_REST = "https://api.binance.com"


# ============================================================
# SETTINGS
# ============================================================

# Timeframes
INTERVAL_5M = "5m"
INTERVAL_15M = "15m"


# ------------------------------------------------------------
# HISTORY
# ------------------------------------------------------------

# Desired CLOSED 5M history
HISTORY_5M = 1440

# Desired CLOSED 15M history
HISTORY_15M = 600


# ------------------------------------------------------------
# 5M MOMENTUM
# ------------------------------------------------------------

MIN_5M_PRICE_CHANGE = 1.0
MAX_CURRENT_5M_PRICE = 8.0

AVERAGE_5M_VOLUME_CANDLES = 20
MIN_5M_VOLUME_RATIO = 1.20

MIN_BUY_PRESSURE = 55.0


# ------------------------------------------------------------
# 24H LIQUIDITY
# ------------------------------------------------------------

MIN_24H_QUOTE_VOLUME = 1_000_000


# ------------------------------------------------------------
# SPREAD
# ------------------------------------------------------------

MAX_SPREAD_PERCENT = 0.20


# ------------------------------------------------------------
# 15M BREAKOUT
# ------------------------------------------------------------

MIN_BREAKOUT_PERCENT = 1.0
MIN_BREAKOUT_VOLUME_RATIO = 1.50

BREAKOUT_VOLUME_AVERAGE_CANDLES = 20


# ------------------------------------------------------------
# SCAN
# ------------------------------------------------------------

SCAN_INTERVAL = 60

MAX_WORKERS = 8

REQUEST_TIMEOUT = 10


# ------------------------------------------------------------
# ALERT COOLDOWN
# ------------------------------------------------------------

SIGNAL_COOLDOWN_SECONDS = 30 * 60


# ============================================================
# SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "BinanceSpotMomentumBreakoutBot/2.0"
})


# ============================================================
# GLOBALS
# ============================================================

spot_symbols = {}

last_alert = {}

lock = threading.Lock()


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN missing")
        return False

    if not TELEGRAM_CHAT_ID:
        print("ERROR: TELEGRAM_CHAT_ID missing")
        return False

    url = (
        "https://api.telegram.org/bot"
        + TELEGRAM_BOT_TOKEN
        + "/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": True
    }

    try:

        r = session.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        if r.status_code == 200:
            return True

        print(
            "Telegram error:",
            r.status_code,
            r.text[:300]
        )

    except Exception as e:

        print("Telegram exception:", repr(e))

    return False


# ============================================================
# BINANCE GET
# ============================================================

def binance_get(path, params=None):

    try:

        r = session.get(
            BINANCE_REST + path,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        if r.status_code != 200:

            print(
                "Binance HTTP error:",
                r.status_code,
                path
            )

            return None

        return r.json()

    except Exception as e:

        print(
            "Binance request error:",
            path,
            repr(e)
        )

        return None


# ============================================================
# LOAD SPOT SYMBOLS
# ============================================================

def load_spot_symbols():

    global spot_symbols

    data = binance_get(
        "/api/v3/exchangeInfo"
    )

    if not data:
        return False

    result = {}

    for item in data.get("symbols", []):

        try:

            symbol = item["symbol"]

            if item.get("status") != "TRADING":
                continue

            if item.get("quoteAsset") != "USDT":
                continue

            if not item.get(
                "isSpotTradingAllowed",
                False
            ):
                continue

            base = item.get(
                "baseAsset",
                ""
            ).upper()

            # Remove leveraged tokens
            if base.endswith(
                ("UP", "DOWN", "BULL", "BEAR")
            ):
                continue

            result[symbol] = item

        except Exception:
            continue

    spot_symbols = result

    print(
        "SPOT SYMBOLS:",
        len(spot_symbols)
    )

    return True


# ============================================================
# 24H TICKERS
# ============================================================

def get_24h_tickers():

    data = binance_get(
        "/api/v3/ticker/24hr"
    )

    if not data:
        return {}

    result = {}

    for item in data:

        symbol = item.get("symbol")

        if symbol not in spot_symbols:
            continue

        try:

            quote_volume = float(
                item.get(
                    "quoteVolume",
                    0
                )
            )

            last_price = float(
                item.get(
                    "lastPrice",
                    0
                )
            )

            if quote_volume < MIN_24H_QUOTE_VOLUME:
                continue

            if last_price <= 0:
                continue

            result[symbol] = {
                "quote_volume": quote_volume,
                "price": last_price
            }

        except Exception:
            continue

    return result


# ============================================================
# GET KLINES
# ============================================================

def get_klines(
    symbol,
    interval,
    limit=1000,
    end_time=None
):

    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": min(limit, 1000)
    }

    if end_time is not None:
        params["endTime"] = end_time

    return binance_get(
        "/api/v3/klines",
        params
    )


# ============================================================
# CLOSED CANDLES ONLY
# ============================================================

def only_closed(klines):

    if not klines:
        return []

    now_ms = int(
        time.time() * 1000
    )

    result = []

    for candle in klines:

        try:

            close_time = int(
                candle[6]
            )

            # VERY IMPORTANT:
            # Ignore current/open candle
            if close_time <= now_ms:
                result.append(candle)

        except Exception:
            continue

    return result


# ============================================================
# LOAD EXACT NUMBER OF CLOSED CANDLES
#
# IMPORTANT:
# Binance maximum request = 1000.
#
# For 1440 x 5M:
# request in multiple pages.
#
# For 600 x 15M:
# request 602 so the currently open candle
# can be removed and 600 CLOSED candles remain.
# ============================================================

def load_closed_history(
    symbol,
    interval,
    required
):

    collected = {}

    # --------------------------------------------------------
    # First request
    #
    # Ask for EXTRA candles.
    # This protects against the currently open candle.
    # --------------------------------------------------------

    first_limit = min(
        1000,
        required + 2
    )

    data = get_klines(
        symbol,
        interval,
        first_limit
    )

    if not data:
        return []

    for candle in data:

        try:
            open_time = int(candle[0])
            collected[open_time] = candle
        except Exception:
            pass

    # --------------------------------------------------------
    # If we still need more candles,
    # go backwards using endTime.
    # --------------------------------------------------------

    while True:

        closed = only_closed(
            list(collected.values())
        )

        if len(closed) >= required:
            break

        if not collected:
            break

        oldest_open_time = min(
            collected.keys()
        )

        # Request previous page
        data = get_klines(
            symbol,
            interval,
            1000,
            oldest_open_time - 1
        )

        if not data:
            break

        before = len(collected)

        for candle in data:

            try:

                open_time = int(
                    candle[0]
                )

                collected[
                    open_time
                ] = candle

            except Exception:
                pass

        if len(collected) == before:
            break

        # Safety
        if len(collected) > required + 2500:
            break

    closed = only_closed(
        list(collected.values())
    )

    closed.sort(
        key=lambda x: int(x[0])
    )

    # Take the latest EXACT required amount
    if len(closed) >= required:

        return closed[-required:]

    return closed


# ============================================================
# SPREAD
# ============================================================

def get_spread(symbol):

    data = binance_get(
        "/api/v3/ticker/bookTicker",
        {
            "symbol": symbol
        }
    )

    if not data:
        return None

    try:

        bid = float(
            data["bidPrice"]
        )

        ask = float(
            data["askPrice"]
        )

        if bid <= 0 or ask <= 0:
            return None

        mid = (
            bid + ask
        ) / 2

        spread = (
            (ask - bid)
            / mid
            * 100
        )

        return spread

    except Exception:
        return None


# ============================================================
# 5M ANALYSIS
# ============================================================

def analyze_5m(
    symbol,
    history
):

    if len(history) < 30:
        return None

    # Latest CLOSED 5M candle
    current = history[-1]

    try:

        open_price = float(
            current[1]
        )

        high = float(
            current[2]
        )

        low = float(
            current[3]
        )

        close_price = float(
            current[4]
        )

        volume = float(
            current[5]
        )

        quote_volume = float(
            current[7]
        )

        taker_buy_quote = float(
            current[10]
        )

    except Exception:
        return None

    if open_price <= 0:
        return None

    # --------------------------------------------------------
    # Price change
    # --------------------------------------------------------

    price_change = (
        (close_price - open_price)
        / open_price
        * 100
    )

    if price_change < MIN_5M_PRICE_CHANGE:
        return None

    if price_change > MAX_CURRENT_5M_PRICE:
        return None

    # --------------------------------------------------------
    # Volume
    # --------------------------------------------------------

    previous = history[
        -(
            AVERAGE_5M_VOLUME_CANDLES + 1
        ):
        -1
    ]

    volumes = []

    for candle in previous:

        try:
            volumes.append(
                float(candle[5])
            )
        except Exception:
            pass

    if len(volumes) < AVERAGE_5M_VOLUME_CANDLES:
        return None

    average_volume = (
        sum(volumes)
        / len(volumes)
    )

    if average_volume <= 0:
        return None

    volume_ratio = (
        volume
        / average_volume
    )

    if volume_ratio < MIN_5M_VOLUME_RATIO:
        return None

    # --------------------------------------------------------
    # Buy pressure
    # --------------------------------------------------------

    if quote_volume <= 0:
        return None

    buy_pressure = (
        taker_buy_quote
        / quote_volume
        * 100
    )

    if buy_pressure < MIN_BUY_PRESSURE:
        return None

    return {
        "price": close_price,
        "price_change": price_change,
        "volume_ratio": volume_ratio,
        "buy_pressure": buy_pressure,
        "high": high,
        "low": low
    }


# ============================================================
# 15M BREAKOUT
# ============================================================

def analyze_15m(
    symbol,
    history
):

    # Need:
    #
    # 600 CLOSED candles = resistance
    # +
    # 1 CLOSED candle = breakout candle
    #
    if len(history) < HISTORY_15M + 1:
        return None

    # --------------------------------------------------------
    # Latest CLOSED 15M candle
    # --------------------------------------------------------

    breakout = history[-1]

    # --------------------------------------------------------
    # PREVIOUS EXACT 600 CLOSED 15M CANDLES
    # --------------------------------------------------------

    resistance_history = history[
        -(
            HISTORY_15M + 1
        ):
        -1
    ]

    if len(resistance_history) != HISTORY_15M:
        return None

    try:

        breakout_open = float(
            breakout[1]
        )

        breakout_high = float(
            breakout[2]
        )

        breakout_low = float(
            breakout[3]
        )

        breakout_close = float(
            breakout[4]
        )

        breakout_volume = float(
            breakout[5]
        )

    except Exception:
        return None

    # --------------------------------------------------------
    # RESISTANCE
    #
    # Highest HIGH of previous 600 CLOSED 15M candles
    # --------------------------------------------------------

    highs = []

    for candle in resistance_history:

        try:
            highs.append(
                float(candle[2])
            )
        except Exception:
            pass

    if len(highs) != HISTORY_15M:
        return None

    resistance = max(highs)

    if resistance <= 0:
        return None

    # --------------------------------------------------------
    # BREAKOUT
    #
    # Latest CLOSED 15M close must be >= +1%
    # above resistance.
    # --------------------------------------------------------

    breakout_percent = (
        (
            breakout_close
            - resistance
        )
        / resistance
        * 100
    )

    if breakout_percent < MIN_BREAKOUT_PERCENT:
        return None

    # --------------------------------------------------------
    # BREAKOUT VOLUME
    #
    # Compare breakout candle with previous 20
    # CLOSED 15M candles.
    # --------------------------------------------------------

    volume_reference = resistance_history[
        -BREAKOUT_VOLUME_AVERAGE_CANDLES:
    ]

    reference_volumes = []

    for candle in volume_reference:

        try:
            reference_volumes.append(
                float(candle[5])
            )
        except Exception:
            pass

    if len(reference_volumes) == 0:
        return None

    average_breakout_volume = (
        sum(reference_volumes)
        / len(reference_volumes)
    )

    if average_breakout_volume <= 0:
        return None

    breakout_volume_ratio = (
        breakout_volume
        / average_breakout_volume
    )

    if (
        breakout_volume_ratio
        < MIN_BREAKOUT_VOLUME_RATIO
    ):
        return None

    # --------------------------------------------------------
    # CLOSE POSITION
    # --------------------------------------------------------

    candle_range = (
        breakout_high
        - breakout_low
    )

    if candle_range > 0:

        close_position = (
            (
                breakout_close
                - breakout_low
            )
            / candle_range
            * 100
        )

    else:

        close_position = 100.0

    return {
        "resistance": resistance,
        "close": breakout_close,
        "breakout_percent": breakout_percent,
        "volume_ratio": breakout_volume_ratio,
        "close_position": close_position,
        "open": breakout_open
    }


# ============================================================
# COOLDOWN
# ============================================================

def is_on_cooldown(symbol):

    now = time.time()

    with lock:

        previous = last_alert.get(
            symbol
        )

        if previous is None:
            return False

        return (
            now - previous
            < SIGNAL_COOLDOWN_SECONDS
        )


def set_alert_time(symbol):

    with lock:

        last_alert[
            symbol
        ] = time.time()


# ============================================================
# BUILD TELEGRAM MESSAGE
# ============================================================

def build_signal(
    symbol,
    ticker,
    momentum,
    breakout,
    spread
):

    return (
        "🟢 BINANCE SPOT BREAKOUT\n"
        "\n"
        f"🪙 {symbol}\n"
        f"💰 Price: {momentum['price']:.12g}\n"
        "\n"
        "⚡ 5M MOMENTUM\n"
        f"📈 5M Change: "
        f"+{momentum['price_change']:.2f}%\n"
        f"📊 5M Volume: "
        f"{momentum['volume_ratio']:.2f}x\n"
        f"🟢 Buy Pressure: "
        f"{momentum['buy_pressure']:.1f}%\n"
        "\n"
        "🚀 REAL 15M BREAKOUT\n"
        f"🔴 Resistance: "
        f"{breakout['resistance']:.12g}\n"
        f"🚀 Breakout: "
        f"+{breakout['breakout_percent']:.2f}%\n"
        f"📊 Breakout Volume: "
        f"{breakout['volume_ratio']:.2f}x\n"
        f"🕯️ Close Position: "
        f"{breakout['close_position']:.1f}%\n"
        "\n"
        f"💧 24H Volume: "
        f"${ticker['quote_volume']:,.0f}\n"
        f"↔️ Spread: "
        f"{spread:.3f}%\n"
        "\n"
        "📚 Resistance = previous "
        "600 CLOSED 15M candles\n"
        "🕯️ Breakout = CLOSED 15M candle\n"
        "\n"
        "🟢 Binance Spot ONLY\n"
        "❌ Solana OFF\n"
        "❌ Binance Square OFF\n"
        "\n"
        "⚠️ ALERT ONLY\n"
        "❌ NO AUTOMATIC ORDER"
    )


# ============================================================
# ANALYZE SYMBOL
# ============================================================

def analyze_symbol(
    symbol,
    ticker
):

    try:

        if is_on_cooldown(symbol):
            return None

        # ----------------------------------------------------
        # 5M
        # ----------------------------------------------------

        history_5m = load_closed_history(
            symbol,
            INTERVAL_5M,
            HISTORY_5M
        )

        if len(history_5m) != HISTORY_5M:

            print(
                f"5M history incomplete "
                f"{symbol}: "
                f"{len(history_5m)}/{HISTORY_5M}"
            )

            return None

        momentum = analyze_5m(
            symbol,
            history_5m
        )

        if not momentum:
            return None

        # ----------------------------------------------------
        # SPREAD
        # ----------------------------------------------------

        spread = get_spread(
            symbol
        )

        if spread is None:
            return None

        if spread > MAX_SPREAD_PERCENT:
            return None

        # ----------------------------------------------------
        # 15M
        # ----------------------------------------------------

        history_15m = load_closed_history(
            symbol,
            INTERVAL_15M,
            HISTORY_15M + 1
        )

        if len(history_15m) != HISTORY_15M + 1:

            print(
                f"15M history incomplete "
                f"{symbol}: "
                f"{len(history_15m)}/"
                f"{HISTORY_15M + 1}"
            )

            return None

        breakout = analyze_15m(
            symbol,
            history_15m
        )

        if not breakout:
            return None

        # ----------------------------------------------------
        # SIGNAL
        # ----------------------------------------------------

        return build_signal(
            symbol,
            ticker,
            momentum,
            breakout,
            spread
        )

    except Exception as e:

        print(
            f"{symbol} ERROR:",
            repr(e)
        )

        return None


# ============================================================
# BINANCE SCAN LOOP
# ============================================================

def binance_scan_loop():

    print()
    print("=" * 60)
    print("BINANCE SPOT BOT STARTED")
    print("=" * 60)
    print("5M HISTORY       :", HISTORY_5M)
    print("15M RESISTANCE   :", HISTORY_15M)
    print("BREAKOUT         :", "+1%")
    print("BREAKOUT VOLUME  :", ">= 1.5x")
    print("24H VOLUME       :", ">= $1,000,000")
    print("SPREAD           :", "<= 0.20%")
    print("BUY PRESSURE     :", ">= 55%")
    print("BINANCE SPOT     : ON")
    print("SOLANA           : OFF")
    print("BINANCE SQUARE   : OFF")
    print("AUTO ORDER       : OFF")
    print("=" * 60)
    print()

    while True:

        cycle_start = time.time()

        try:

            # ------------------------------------------------
            # 24H filter
            # ------------------------------------------------

            tickers = get_24h_tickers()

            symbols = list(
                tickers.keys()
            )

            print()
            print(
                f"[SCAN] "
                f"{len(symbols)} symbols "
                f"passed 24H volume filter"
            )

            if not symbols:

                time.sleep(
                    SCAN_INTERVAL
                )

                continue

            signals = 0

            # ------------------------------------------------
            # Parallel scan
            # ------------------------------------------------

            with ThreadPoolExecutor(
                max_workers=MAX_WORKERS
            ) as executor:

                futures = {}

                for symbol in symbols:

                    futures[
                        executor.submit(
                            analyze_symbol,
                            symbol,
                            tickers[symbol]
                        )
                    ] = symbol

                for future in as_completed(
                    futures
                ):

                    symbol = futures[
                        future
                    ]

                    try:

                        message = (
                            future.result()
                        )

                        if not message:
                            continue

                        set_alert_time(
                            symbol
                        )

                        signals += 1

                        print(
                            f"[SIGNAL] {symbol}"
                        )

                        send_telegram(
                            message
                        )

                    except Exception as e:

                        print(
                            f"{symbol} "
                            f"FUTURE ERROR:",
                            repr(e)
                        )

            elapsed = (
                time.time()
                - cycle_start
            )

            print(
                f"[SCAN COMPLETE] "
                f"{elapsed:.1f}s | "
                f"Signals: {signals}"
            )

        except Exception as e:

            print(
                "[SCAN LOOP ERROR]",
                repr(e)
            )

        # ----------------------------------------------------
        # Next scan
        # ----------------------------------------------------

        elapsed = (
            time.time()
            - cycle_start
        )

        sleep_time = max(
            1,
            SCAN_INTERVAL - elapsed
        )

        print(
            f"Next scan in "
            f"{sleep_time:.1f}s"
        )

        time.sleep(
            sleep_time
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 60)
    print("🟢 BINANCE SPOT BOT TEST")
    print("=" * 60)
    print()
    print("5M Momentum + 600 CLOSED 15M Real Breakout")
    print()
    print("Binance Spot ONLY")
    print("Solana OFF")
    print("Binance Square OFF")
    print("Automatic Order OFF")
    print()

    # --------------------------------------------------------
    # Telegram check
    # --------------------------------------------------------

    if not TELEGRAM_BOT_TOKEN:

        print(
            "ERROR: TELEGRAM_BOT_TOKEN "
            "not configured."
        )

        return

    if not TELEGRAM_CHAT_ID:

        print(
            "ERROR: TELEGRAM_CHAT_ID "
            "not configured."
        )

        return

    # --------------------------------------------------------
    # Binance symbols
    # --------------------------------------------------------

    if not load_spot_symbols():

        print(
            "ERROR: Binance Spot symbols "
            "could not be loaded."
        )

        return

    # --------------------------------------------------------
    # Telegram startup alert
    # --------------------------------------------------------

    send_telegram(
        "🟢 BINANCE SPOT BOT STARTED\n\n"
        "⚡ 5M Momentum\n"
        "🚀 600 CLOSED 15M Real Breakout\n"
        "📈 Breakout: +1%\n"
        "📊 Breakout Volume: >= 1.5x\n\n"
        "🟢 Binance Spot ONLY\n"
        "❌ Solana OFF\n"
        "❌ Binance Square OFF\n\n"
        "⚠️ ALERT ONLY\n"
        "❌ NO AUTOMATIC ORDER"
    )

    # --------------------------------------------------------
    # Start
    # --------------------------------------------------------

    binance_scan_loop()


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    main()
