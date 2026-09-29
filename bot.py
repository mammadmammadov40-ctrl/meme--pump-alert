import os
import time
import requests

from datetime import datetime
from zoneinfo import ZoneInfo


# ============================================================
# BINANCE 15M DIB -> HIGH -> DIB2 -> BREAKOUT
# ALERT ONLY / NO AUTOMATIC ORDER
# ============================================================

BINANCE_URL = "https://api.binance.com"

INTERVAL = "15m"
TOP_COINS = 100

# HIGH 1 üçün solda minimum şam sayı
LEFT_HIGH_CANDLES = 10

# DIB 1 strukturu maksimum 100 şam
MAX_DIB1_CANDLES = 100

# Breakout HIGH 1-dən minimum +1%
MIN_BREAKOUT_PERCENT = 1.0

# DIB 2-dən qalxış 10 şamdan çox olarsa rollover
DIB2_LONG_RISE_CANDLES = 10

# 3 və daha az şamlıq uğursuz qalxışda tam reset
DIB2_SHORT_RISE_CANDLES = 3

# Binance top 100 yenilənməsi
TOP_REFRESH_SECONDS = 300

# Scan intervalı
SCAN_SECONDS = 10

AZ_TZ = ZoneInfo("Asia/Baku")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# ============================================================
# START TIME
# ============================================================

START_TIME_MS = int(time.time() * 1000)


# ============================================================
# SYMBOL STATES
# ============================================================

states = {}


def new_state():
    return {
        "mode": "SEARCH_DIB1",

        # -----------------------------
        # DIB 1
        # -----------------------------
        "dib1": None,
        "dib1_time": None,
        "dib1_start_close_time": None,
        "dib1_candle_count": 0,

        # -----------------------------
        # HIGH 1
        # -----------------------------
        "high1": None,
        "high1_time": None,
        "high1_index": None,

        # Number of candles processed
        # since current DIB1 appeared
        "candles_after_dib1": 0,

        # -----------------------------
        # DIB 2
        # -----------------------------
        "dib2": None,
        "dib2_time": None,

        # candles elapsed since DIB2
        "dib2_candles": 0,

        # candles during the current rise
        "dib2_rise_candles": 0,

        # highest price reached from DIB2
        "dib2_high": None,
        "dib2_high_time": None,

        # -----------------------------
        # Last processed candle
        # -----------------------------
        "last_close_time": None,

        # Alert statistics
        "signals": 0,
    }


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram ENV variables are missing")
        return

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }

    try:
        r = requests.post(
            url,
            json=payload,
            timeout=10
        )

        if r.status_code != 200:
            print(
                "Telegram error:",
                r.status_code,
                r.text
            )

    except Exception as e:
        print("Telegram exception:", e)


# ============================================================
# TIME
# ============================================================

def fmt_time(ms):
    if not ms:
        return "-"

    return datetime.fromtimestamp(
        ms / 1000,
        AZ_TZ
    ).strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# BINANCE
# ============================================================

session = requests.Session()


def get_top_100():

    try:
        r = session.get(
            f"{BINANCE_URL}/api/v3/ticker/24hr",
            timeout=10
        )

        r.raise_for_status()

        data = r.json()

        symbols = []

        for x in data:

            symbol = x.get("symbol", "")

            if not symbol.endswith("USDT"):
                continue

            if any(
                symbol.endswith(x)
                for x in [
                    "UPUSDT",
                    "DOWNUSDT",
                    "BULLUSDT",
                    "BEARUSDT"
                ]
            ):
                continue

            try:
                volume = float(
                    x.get("quoteVolume", 0)
                )
            except:
                continue

            if volume <= 0:
                continue

            symbols.append(
                (symbol, volume)
            )

        symbols.sort(
            key=lambda x: x[1],
            reverse=True
        )

        return [
            x[0]
            for x in symbols[:TOP_COINS]
        ]

    except Exception as e:

        print(
            "Top 100 error:",
            e
        )

        return []


def get_latest_closed_candle(symbol):

    try:

        r = session.get(
            f"{BINANCE_URL}/api/v3/klines",
            params={
                "symbol": symbol,
                "interval": INTERVAL,
                "limit": 2
            },
            timeout=10
        )

        r.raise_for_status()

        data = r.json()

        if len(data) < 2:
            return None

        # The previous candle is closed.
        k = data[-2]

        return {
            "open_time": int(k[0]),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "close_time": int(k[6]),
        }

    except Exception as e:

        print(
            f"{symbol}: candle error:",
            e
        )

        return None


# ============================================================
# RESET
# ============================================================

def reset_state(symbol):

    states[symbol] = new_state()


# ============================================================
# START NEW DIB1
# ============================================================

def start_new_dib1(
    state,
    low,
    candle
):

    state["mode"] = "TRACK_DIB1"

    state["dib1"] = low
    state["dib1_time"] = candle["close_time"]

    state["dib1_start_close_time"] = (
        candle["close_time"]
    )

    state["dib1_candle_count"] = 0
    state["candles_after_dib1"] = 0

    state["high1"] = None
    state["high1_time"] = None
    state["high1_index"] = None

    state["dib2"] = None
    state["dib2_time"] = None
    state["dib2_candles"] = 0
    state["dib2_rise_candles"] = 0
    state["dib2_high"] = None
    state["dib2_high_time"] = None


# ============================================================
# INITIAL DIB1 TRACKING
# ============================================================

def process_search_dib1(
    symbol,
    state,
    candle
):

    low = candle["low"]
    high = candle["high"]

    # --------------------------------------------
    # No DIB1 yet
    # --------------------------------------------

    if state["dib1"] is None:

        start_new_dib1(
            state,
            low,
            candle
        )

        return

    # --------------------------------------------
    # Current candle is lower than DIB1
    # --------------------------------------------

    if low < state["dib1"]:

        # Old DIB1 failed before confirmation.
        #
        # The new lower price becomes the NEW DIB1.
        #
        # Everything connected to old DIB1 is deleted.

        start_new_dib1(
            state,
            low,
            candle
        )

        return

    # --------------------------------------------
    # One more candle after DIB1
    # --------------------------------------------

    state["candles_after_dib1"] += 1
    state["dib1_candle_count"] = (
        state["candles_after_dib1"]
    )

    # --------------------------------------------
    # HIGH 1 tracking
    #
    # HIGH needs at least 10 candles to the left.
    # Therefore we only accept a HIGH after
    # 10 candles have already appeared.
    #
    # Once eligible, keep the HIGHEST high.
    # --------------------------------------------

    if (
        state["candles_after_dib1"]
        >= LEFT_HIGH_CANDLES
    ):

        if (
            state["high1"] is None
            or high > state["high1"]
        ):

            state["high1"] = high
            state["high1_time"] = (
                candle["close_time"]
            )

            state["high1_index"] = (
                state["candles_after_dib1"]
            )

    # --------------------------------------------
    # DIB1 must be confirmed within 100 candles.
    #
    # If this is the 101st candle and DIB1
    # was not broken, cancel the old structure.
    # --------------------------------------------

    if (
        state["candles_after_dib1"]
        > MAX_DIB1_CANDLES
    ):

        # Start completely live from this candle.
        start_new_dib1(
            state,
            low,
            candle
        )

        return

    # --------------------------------------------
    # DIB1 confirmation
    #
    # Current candle must break below DIB1.
    # HIGH1 must already be valid.
    # --------------------------------------------

    if (
        low < state["dib1"]
        and state["high1"] is not None
    ):

        # This branch normally does not happen because
        # lower-low handling above catches it.
        #
        # Kept here for safety.
        confirm_dib1_high1(
            state,
            candle
        )


# ============================================================
# CONFIRM DIB1 + HIGH1
# ============================================================

def confirm_dib1_high1(
    state,
    candle
):

    state["mode"] = "TRACK_DIB2"

    # Breaking candle becomes the first
    # live DIB2 candidate.
    state["dib2"] = candle["low"]
    state["dib2_time"] = candle["close_time"]

    state["dib2_candles"] = 0
    state["dib2_rise_candles"] = 0

    state["dib2_high"] = candle["high"]
    state["dib2_high_time"] = (
        candle["close_time"]
    )


# ============================================================
# TRACK DIB1 / CONFIRMATION
# ============================================================

def process_track_dib1(
    symbol,
    state,
    candle
):

    # This mode is retained separately so the
    # state machine is explicit.

    process_search_dib1(
        symbol,
        state,
        candle
    )


# ============================================================
# DIB2 BREAK ROLLOVER
# ============================================================

def rollover_from_dib2(
    state,
    candle
):

    old_dib2 = state["dib2"]
    old_dib2_time = state["dib2_time"]

    old_dib2_high = state["dib2_high"]
    old_dib2_high_time = (
        state["dib2_high_time"]
    )

    # --------------------------------------------------------
    # OLD DIB2 -> NEW DIB1
    # --------------------------------------------------------

    state["mode"] = "TRACK_DIB1"

    state["dib1"] = old_dib2
    state["dib1_time"] = old_dib2_time

    state["dib1_start_close_time"] = (
        old_dib2_time
    )

    state["candles_after_dib1"] = 0
    state["dib1_candle_count"] = 0

    # --------------------------------------------------------
    # The highest price reached from old DIB2
    # becomes NEW HIGH1 candidate.
    #
    # It is valid only if the high has at least
    # 10 candles to its left.
    # --------------------------------------------------------

    state["high1"] = None
    state["high1_time"] = None
    state["high1_index"] = None

    if old_dib2_high is not None:

        if state["dib2_candles"] >= LEFT_HIGH_CANDLES:

            state["high1"] = old_dib2_high
            state["high1_time"] = old_dib2_high_time
            state["high1_index"] = (
                state["dib2_candles"]
            )

    # --------------------------------------------------------
    # The candle which broke old DIB2 becomes
    # the new live DIB2.
    # --------------------------------------------------------

    state["dib2"] = candle["low"]
    state["dib2_time"] = candle["close_time"]

    state["dib2_candles"] = 0
    state["dib2_rise_candles"] = 0

    state["dib2_high"] = candle["high"]
    state["dib2_high_time"] = (
        candle["close_time"]
    )


# ============================================================
# SHORT DIB2 FAILURE RESET
# ============================================================

def reset_after_short_dib2_failure(
    state,
    candle
):

    # Old DIB1, HIGH1 and DIB2 are all forgotten.
    #
    # Breaking candle's low becomes a fresh DIB1.

    start_new_dib1(
        state,
        candle["low"],
        candle
    )


# ============================================================
# DIB2 PROCESSING
# ============================================================

def process_dib2(
    symbol,
    state,
    candle
):

    dib2 = state["dib2"]
    high1 = state["high1"]

    if dib2 is None or high1 is None:

        # Safety fallback
        reset_after_short_dib2_failure(
            state,
            candle
        )

        return False

    # --------------------------------------------------------
    # FIRST: BREAKOUT CHECK
    #
    # Breakout gets priority over DIB2 failure.
    # --------------------------------------------------------

    breakout_price = high1 * (
        1.0 + MIN_BREAKOUT_PERCENT / 100.0
    )

    if candle["close"] >= breakout_price:

        breakout_percent = (
            (candle["close"] - high1)
            / high1
            * 100.0
        )

        message = (
            "🚨 15M BREAKOUT SIGNAL 🚨\n\n"

            f"🪙 Coin: {symbol}\n\n"

            "📉 DIB 1\n"
            f"Price: {state['dib1']}\n"
            f"Time: {fmt_time(state['dib1_time'])}\n\n"

            "📈 HIGH 1\n"
            f"Price: {high1}\n"
            f"Time: {fmt_time(state['high1_time'])}\n\n"

            "📉 DIB 2\n"
            f"Price: {dib2}\n"
            f"Time: {fmt_time(state['dib2_time'])}\n\n"

            "🔥 BREAKOUT\n"
            f"Close: {candle['close']}\n"
            f"Time: {fmt_time(candle['close_time'])}\n"
            f"Breakout: +{breakout_percent:.2f}%\n\n"

            "✅ HIGH 1 BROKEN\n"
            "✅ Breakout >= +1%\n"
            "⚠️ ALERT ONLY — NO AUTO ORDER"
        )

        send_telegram(message)

        state["signals"] += 1

        # After a signal start a completely new live structure.
        start_new_dib1(
            state,
            candle["low"],
            candle
        )

        return True

    # --------------------------------------------------------
    # CURRENT DIB2 IS STILL HOLDING
    # --------------------------------------------------------

    if candle["low"] >= dib2:

        state["dib2_candles"] += 1

        # If price is moving above DIB2,
        # count the candle as part of the rise.
        if candle["close"] > dib2:

            state["dib2_rise_candles"] += 1

        # Track the highest price from DIB2.
        if (
            state["dib2_high"] is None
            or candle["high"] > state["dib2_high"]
        ):

            state["dib2_high"] = candle["high"]
            state["dib2_high_time"] = (
                candle["close_time"]
            )

        # ----------------------------------------------------
        # If HIGH1 is not broken, the DIB2 structure remains.
        # ----------------------------------------------------

        return False

    # --------------------------------------------------------
    # DIB2 HAS BEEN BROKEN
    # --------------------------------------------------------

    rise_candles = state[
        "dib2_rise_candles"
    ]

    # --------------------------------------------------------
    # MORE THAN 10 CANDLES
    #
    # Apply rule 10:
    #
    # old DIB2 -> new DIB1
    # highest from old DIB2 -> new HIGH1
    # breaking price -> new DIB2
    # --------------------------------------------------------

    if rise_candles > DIB2_LONG_RISE_CANDLES:

        rollover_from_dib2(
            state,
            candle
        )

        return False

    # --------------------------------------------------------
    # 3 OR FEWER CANDLES
    #
    # Complete reset.
    # Breaking candle becomes new DIB1.
    # --------------------------------------------------------

    if rise_candles <= DIB2_SHORT_RISE_CANDLES:

        reset_after_short_dib2_failure(
            state,
            candle
        )

        return False

    # --------------------------------------------------------
    # 4–10 candles
    #
    # Conservative handling:
    # no successful +1% breakout means the old
    # structure is discarded and the breaking low
    # becomes a fresh DIB1.
    #
    # This avoids falsely promoting a weak structure.
    # --------------------------------------------------------

    reset_after_short_dib2_failure(
        state,
        candle
    )

    return False


# ============================================================
# MAIN CANDLE PROCESSOR
# ============================================================

def process_candle(
    symbol,
    candle
):

    if symbol not in states:
        states[symbol] = new_state()

    state = states[symbol]

    close_time = candle["close_time"]

    # --------------------------------------------------------
    # NEVER USE A CANDLE THAT CLOSED BEFORE BOT STARTUP
    # --------------------------------------------------------

    if close_time <= START_TIME_MS:
        return False

    # --------------------------------------------------------
    # Duplicate protection
    # --------------------------------------------------------

    if (
        state["last_close_time"] is not None
        and close_time <= state["last_close_time"]
    ):
        return False

    state["last_close_time"] = close_time

    # --------------------------------------------------------
    # STATE MACHINE
    # --------------------------------------------------------

    if state["mode"] == "SEARCH_DIB1":

        process_search_dib1(
            symbol,
            state,
            candle
        )

    elif state["mode"] == "TRACK_DIB1":

        process_track_dib1(
            symbol,
            state,
            candle
        )

    elif state["mode"] == "TRACK_DIB2":

        process_dib2(
            symbol,
            state,
            candle
        )

    return True


# ============================================================
# MAIN LOOP
# ============================================================

def main():

    print("=" * 60)
    print("BINANCE 15M DIB/HIGH BREAKOUT BOT")
    print("ALERT ONLY / NO AUTOMATIC ORDER")
    print("=" * 60)

    print(
        "START TIME:",
        fmt_time(START_TIME_MS)
    )

    print(
        "TOP COINS:",
        TOP_COINS
    )

    print(
        "TIMEFRAME:",
        INTERVAL
    )

    print(
        "DIB1 MAX CANDLES:",
        MAX_DIB1_CANDLES
    )

    print(
        "HIGH LEFT CANDLES:",
        LEFT_HIGH_CANDLES
    )

    print(
        "BREAKOUT:",
        f"+{MIN_BREAKOUT_PERCENT}%"
    )

    top_symbols = []
    last_top_refresh = 0

    scan_number = 0

    while True:

        try:

            now = time.time()

            # --------------------------------------------
            # Refresh TOP 100
            # --------------------------------------------

            if (
                not top_symbols
                or now - last_top_refresh
                >= TOP_REFRESH_SECONDS
            ):

                new_symbols = get_top_100()

                if new_symbols:

                    top_symbols = new_symbols
                    last_top_refresh = now

                    print(
                        f"TOP 100 updated | "
                        f"Symbols: {len(top_symbols)}"
                    )

            # --------------------------------------------
            # Scan
            # --------------------------------------------

            scan_number += 1

            new_candles = 0

            for symbol in top_symbols:

                candle = get_latest_closed_candle(
                    symbol
                )

                if candle is None:
                    continue

                processed = process_candle(
                    symbol,
                    candle
                )

                if processed:
                    new_candles += 1

            print(
                f"[{datetime.now(AZ_TZ).strftime('%Y-%m-%d %H:%M:%S')}] "
                f"HEARTBEAT | "
                f"Scan #{scan_number} | "
                f"Symbols: {len(top_symbols)} | "
                f"New candles: {new_candles}"
            )

            time.sleep(SCAN_SECONDS)

        except KeyboardInterrupt:

            print("Bot stopped.")
            break

        except Exception as e:

            print(
                "MAIN LOOP ERROR:",
                e
            )

            time.sleep(10)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
