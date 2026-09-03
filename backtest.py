import time
import requests
import csv
import math
from datetime import datetime, timezone

# ============================================================
# BINANCE BEARISH FVG BACKTEST
# Based on the current bot.py strategy
#
# IMPORTANT:
# This is a SIGNAL/SETUP backtest, not an order-execution simulator.
# It tests:
#   1) 24H volume >= $20M
#   2) 1H Close > EMA20 > EMA50 > EMA100
#   3) Bearish FVG on 15m or 1h
#   4) FVG completely inside Candle 2 body
#   5) FVG >= 50% of Candle 2 body
#   6) Target = Candle 3 High - 1.7%
#
# Historical trend timing uses only a 1H candle that was already closed
# when Candle 3 closed, avoiding look-ahead from an unfinished 1H candle.
#
# It then looks forward candle-by-candle:
#   - TARGET: price reaches target
#   - CANCELLED: price breaks above Candle 3 High
#
# Public market-data endpoint:
#   https://data-api.binance.vision
#   (official Binance market-data-only endpoint)
#
# Run:
#   python backtest.py
# ============================================================

BINANCE_BASE_URL = "https://data-api.binance.vision"

# -------------------- SETTINGS ------------------------------

MIN_QUOTE_VOLUME_24H = 20_000_000
FVG_MIN_RATIO = 0.50
TARGET_PERCENT = 1.7

FVG_INTERVALS = ["15m", "1h"]

EMA_FAST = 20
EMA_MID = 50
EMA_SLOW = 100

# Backtest period in days.
BACKTEST_DAYS = 180

# Maximum number of qualifying symbols to test.
# Set to 0 for all qualifying symbols.
MAX_SYMBOLS = 0

# Approximate trading fee per side.
# Change if your actual Binance fee is different.
FEE_PER_SIDE = 0.001

# V2 risk / execution model
STARTING_BALANCE = 1000.0
RISK_PER_TRADE = 0.01          # 1% of current equity
SL_BUFFER_PERCENT = 0.05       # small buffer above Candle 3 high
MAX_HOLD_CANDLES = 96          # safety cap for unresolved trades


OUTPUT_CSV = "backtest_v2_results.csv"


# ============================================================
# BINANCE REQUEST
# ============================================================

def binance_get(endpoint, params=None):
    url = BINANCE_BASE_URL + endpoint

    try:
        response = requests.get(
            url,
            params=params,
            timeout=20
        )
        response.raise_for_status()
        return response.json()

    except Exception as e:
        print(f"[BINANCE ERROR] {endpoint}: {e}")
        raise RuntimeError(
            f"Binance request failed for {endpoint}: {e}"
        ) from e


# ============================================================
# SYMBOLS
# ============================================================

def get_spot_usdt_symbols():
    data = binance_get("/api/v3/exchangeInfo")

    if not data:
        return []

    symbols = []

    for item in data.get("symbols", []):
        if (
            item.get("status") == "TRADING"
            and item.get("quoteAsset") == "USDT"
            and item.get("isSpotTradingAllowed") is True
        ):
            symbols.append(item["symbol"])

    return symbols


# ============================================================
# KLINES
# ============================================================

def interval_ms(interval):
    values = {
        "15m": 15 * 60 * 1000,
        "1h": 60 * 60 * 1000,
    }
    return values[interval]


def get_klines_range(symbol, interval, start_ms, end_ms):
    """
    Downloads historical klines in pages of <=1000 candles.
    """

    all_candles = []
    current_start = start_ms

    while current_start < end_ms:
        data = binance_get(
            "/api/v3/klines",
            {
                "symbol": symbol,
                "interval": interval,
                "startTime": current_start,
                "endTime": end_ms,
                "limit": 1000,
            }
        )

        if not data:
            break

        all_candles.extend(data)

        if len(data) < 1000:
            break

        last_open = int(data[-1][0])
        next_start = last_open + interval_ms(interval)

        if next_start <= current_start:
            break

        current_start = next_start

        time.sleep(0.05)

    # Remove duplicates
    unique = {}
    for candle in all_candles:
        unique[int(candle[0])] = candle

    return [
        unique[key]
        for key in sorted(unique)
    ]


# ============================================================
# EMA
# ============================================================

def calculate_ema_series(values, period):
    """
    Returns an EMA value for every index where enough
    historical data exists. Earlier values are None.
    """

    result = [None] * len(values)

    if len(values) < period:
        return result

    ema = sum(values[:period]) / period
    result[period - 1] = ema

    multiplier = 2 / (period + 1)

    for i in range(period, len(values)):
        ema = (
            (values[i] - ema) * multiplier
            + ema
        )
        result[i] = ema

    return result


# ============================================================
# 1H TREND
# ============================================================

def build_hourly_trend(hourly_candles):
    closes = [float(c[4]) for c in hourly_candles]

    ema20 = calculate_ema_series(
        closes,
        EMA_FAST
    )
    ema50 = calculate_ema_series(
        closes,
        EMA_MID
    )
    ema100 = calculate_ema_series(
        closes,
        EMA_SLOW
    )

    trend = {}

    for i, candle in enumerate(hourly_candles):
        if None in (
            ema20[i],
            ema50[i],
            ema100[i]
        ):
            continue

        close = closes[i]

        trend[int(candle[0])] = (
            close > ema20[i]
            and ema20[i] > ema50[i]
            and ema50[i] > ema100[i]
        )

    return trend


# ============================================================
# 24H VOLUME
# ============================================================

def get_current_24h_volumes():
    """
    Downloads all current 24H tickers in one public market-data request.
    This is much faster and uses less API weight than requesting one
    ticker per symbol.
    """
    data = binance_get("/api/v3/ticker/24hr")

    if not isinstance(data, list):
        raise RuntimeError("Unexpected Binance /ticker/24hr response.")

    volumes = {}

    for item in data:
        symbol = item.get("symbol")
        if not symbol:
            continue

        try:
            volumes[symbol] = float(item["quoteVolume"])
        except (KeyError, TypeError, ValueError):
            continue

    return volumes


# ============================================================
# HISTORICAL APPROXIMATION FOR VOLUME FILTER
# ============================================================

def calculate_historical_24h_volumes(candles):
    """
    Uses quote asset volume from each candle.

    This creates a historical rolling 24H quote-volume filter
    instead of incorrectly using today's volume for old signals.

    Binance kline index 7 = quote asset volume.
    """

    result = [None] * len(candles)

    window_ms = 24 * 60 * 60 * 1000

    left = 0
    running = 0.0

    for right, candle in enumerate(candles):
        right_open = int(candle[0])
        right_close = int(candle[6])

        try:
            quote_volume = float(candle[7])
        except Exception:
            quote_volume = 0.0

        running += quote_volume

        while (
            left <= right
            and right_close - int(candles[left][0])
            >= window_ms
        ):
            try:
                running -= float(candles[left][7])
            except Exception:
                pass
            left += 1

        if right_close - int(candles[left][0]) < window_ms:
            result[right] = running

    return result


# ============================================================
# BEARISH FVG
# ============================================================

def detect_bearish_fvg(candles, i):
    """
    i = Candle 3 index.
    Uses candles i-2, i-1, i.
    """

    if i < 2 or i >= len(candles):
        return None

    c1 = candles[i - 2]
    c2 = candles[i - 1]
    c3 = candles[i]

    c1_open = float(c1[1])
    c1_low = float(c1[3])
    c1_close = float(c1[4])

    c2_open = float(c2[1])
    c2_close = float(c2[4])

    c3_high = float(c3[2])

    # C1 bearish
    if c1_close >= c1_open:
        return None

    # C2 bearish
    if c2_close >= c2_open:
        return None

    body_low = min(c2_open, c2_close)
    body_high = max(c2_open, c2_close)
    body_size = abs(c2_open - c2_close)

    if body_size <= 0:
        return None

    # Bearish FVG
    if c1_low <= c3_high:
        return None

    fvg_low = c3_high
    fvg_high = c1_low
    fvg_size = fvg_high - fvg_low

    if fvg_size <= 0:
        return None

    # FVG completely inside Candle 2 body
    if fvg_low < body_low:
        return None

    if fvg_high > body_high:
        return None

    fvg_ratio = fvg_size / body_size

    if fvg_ratio < FVG_MIN_RATIO:
        return None

    target = c3_high * (1 - TARGET_PERCENT / 100)

    return {
        "c1_open_time": int(c1[0]),
        "c2_open_time": int(c2[0]),
        "c3_open_time": int(c3[0]),
        "c3_high": c3_high,
        "fvg_low": fvg_low,
        "fvg_high": fvg_high,
        "fvg_size": fvg_size,
        "fvg_ratio": fvg_ratio,
        "target": target,
    }


# ============================================================
# RESULT SIMULATION
# ============================================================

def simulate_setup(candles, signal_index, interval):
    """
    Starts AFTER Candle 3 has closed.

    The first future candle is signal_index + 1.

    If a future candle touches target and breaks C3 high
    in the same candle, OHLC alone cannot tell which happened
    first. We conservatively classify that case as CANCELLED.
    """

    fvg = detect_bearish_fvg(candles, signal_index)

    if not fvg:
        return None

    target = fvg["target"]
    c3_high = fvg["c3_high"]

    entry_price = fvg["fvg_high"]

    for j in range(signal_index + 1, len(candles)):
        candle = candles[j]

        high = float(candle[2])
        low = float(candle[3])

        # Both touched in same candle: conservative result
        if high > c3_high and low <= target:
            return {
                "result": "CANCELLED_AMBIGUOUS",
                "signal_time": fvg["c3_open_time"],
                "exit_time": int(candle[0]),
                "entry": entry_price,
                "exit": c3_high,
                "target": target,
                "fvg_ratio": fvg["fvg_ratio"],
                "interval": interval,
            }

        if high > c3_high:
            return {
                "result": "CANCELLED",
                "signal_time": fvg["c3_open_time"],
                "exit_time": int(candle[0]),
                "entry": entry_price,
                "exit": c3_high,
                "target": target,
                "fvg_ratio": fvg["fvg_ratio"],
                "interval": interval,
            }

        if low <= target:
            return {
                "result": "TARGET",
                "signal_time": fvg["c3_open_time"],
                "exit_time": int(candle[0]),
                "entry": entry_price,
                "exit": target,
                "target": target,
                "fvg_ratio": fvg["fvg_ratio"],
                "interval": interval,
            }

    return {
        "result": "OPEN_AT_END",
        "signal_time": fvg["c3_open_time"],
        "exit_time": None,
        "entry": entry_price,
        "exit": None,
        "target": target,
        "fvg_ratio": fvg["fvg_ratio"],
        "interval": interval,
    }


# ============================================================
# MAIN SYMBOL BACKTEST
# ============================================================

def backtest_symbol(symbol, start_ms, end_ms):
    print(f"\n[SYMBOL] {symbol}")

    # Extra history is needed for EMA100 and 24H volume.
    history_start = start_ms - max(
        150 * 60 * 60 * 1000,
        48 * 60 * 60 * 1000
    )

    hourly = get_klines_range(
        symbol,
        "1h",
        history_start,
        end_ms
    )

    if len(hourly) < EMA_SLOW + 5:
        print("  Not enough 1H data.")
        return []

    trend = build_hourly_trend(hourly)

    hourly_volume = calculate_historical_24h_volumes(
        hourly
    )

    hourly_volume_by_time = {
        int(c[0]): hourly_volume[i]
        for i, c in enumerate(hourly)
    }

    results = []

    for interval in FVG_INTERVALS:
        candles = get_klines_range(
            symbol,
            interval,
            history_start,
            end_ms
        )

        if len(candles) < 10:
            continue

        # Historical 24H volume for this timeframe
        volumes = calculate_historical_24h_volumes(candles)

        for i in range(2, len(candles)):
            c3_time = int(candles[i][0])

            if c3_time < start_ms:
                continue

            if c3_time >= end_ms:
                continue

            # ------------------------------------------------
            # 24H volume filter at the time of signal
            # ------------------------------------------------
            hist_volume = volumes[i]

            if (
                hist_volume is None
                or hist_volume < MIN_QUOTE_VOLUME_24H
            ):
                continue

            # ------------------------------------------------
            # 1H bullish trend using the latest CLOSED 1H candle
            # available when Candle 3 closes.
            # ------------------------------------------------
            c3_close_time = int(candles[i][6])

            trend_keys = [
                k for k in trend.keys()
                if (k + 60 * 60 * 1000 - 1) <= c3_close_time
            ]

            if not trend_keys:
                continue

            latest_hour = max(trend_keys)

            if not trend[latest_hour]:
                continue

            # ------------------------------------------------
            # FVG
            # ------------------------------------------------
            fvg = detect_bearish_fvg(
                candles,
                i
            )

            if not fvg:
                continue

            simulated = simulate_setup(
                candles,
                i,
                interval
            )

            if not simulated:
                continue

            simulated["symbol"] = symbol
            simulated["volume_24h_at_signal"] = hist_volume
            simulated["c3_close"] = float(candles[i][4])

            # Gross price move relative to FVG high.
            # This is a proxy for a short entry at FVG high.
            if simulated["result"] == "TARGET":
                gross_return = (
                    simulated["entry"]
                    - simulated["exit"]
                ) / simulated["entry"]

            elif simulated["result"] in (
                "CANCELLED",
                "CANCELLED_AMBIGUOUS"
            ):
                gross_return = (
                    simulated["entry"]
                    - simulated["exit"]
                ) / simulated["entry"]

            else:
                current_close = float(candles[-1][4])
                gross_return = (
                    simulated["entry"]
                    - current_close
                ) / simulated["entry"]

            # Approximate round-trip fee.
            net_return = (
                gross_return
                - (2 * FEE_PER_SIDE)
            )

            simulated["gross_return"] = gross_return
            simulated["net_return"] = net_return

            results.append(simulated)

            # The live bot activates one FVG per symbol and waits
            # until that setup finishes before looking for another.
            # To approximate that behavior, skip overlapping setups
            # after recording the current one.
            if simulated["exit_time"] is not None:
                break

    return results


# ============================================================
# REPORT
# ============================================================

def pct(value):
    return f"{value * 100:.2f}%"


def print_report(results):
    print("\n")
    print("=" * 78)
    print("BACKTEST REPORT")
    print("=" * 78)

    if not results:
        print("No setups found.")
        return

    total = len(results)
    targets = sum(
        r["result"] == "TARGET"
        for r in results
    )
    cancelled = sum(
        r["result"] == "CANCELLED"
        for r in results
    )
    ambiguous = sum(
        r["result"] == "CANCELLED_AMBIGUOUS"
        for r in results
    )
    open_end = sum(
        r["result"] == "OPEN_AT_END"
        for r in results
    )

    decided = targets + cancelled + ambiguous

    win_rate = (
        targets / decided
        if decided
        else 0
    )

    net_returns = [
        r["net_return"]
        for r in results
        if r["result"] != "OPEN_AT_END"
    ]

    total_net = sum(net_returns)

    wins = [
        r["net_return"]
        for r in results
        if r["result"] == "TARGET"
    ]

    losses = [
        r["net_return"]
        for r in results
        if r["result"] in (
            "CANCELLED",
            "CANCELLED_AMBIGUOUS"
        )
    ]

    gross_profit = sum(
        x for x in wins
        if x > 0
    )

    gross_loss = abs(sum(
        x for x in losses
        if x < 0
    ))

    profit_factor = (
        gross_profit / gross_loss
        if gross_loss > 0
        else math.inf
    )

    print(f"Total setups:        {total}")
    print(f"Target hits:         {targets}")
    print(f"Cancelled:           {cancelled}")
    print(f"Ambiguous candles:   {ambiguous}")
    print(f"Open at test end:    {open_end}")
    print(f"Win rate:            {pct(win_rate)}")
    print(f"Net setup sum:       {pct(total_net)}")

    if profit_factor == math.inf:
        print("Profit factor:       INF")
    else:
        print(f"Profit factor:       {profit_factor:.2f}")

    if net_returns:
        print(
            f"Average net/setup:   "
            f"{pct(sum(net_returns) / len(net_returns))}"
        )

    print("\nBy timeframe:")

    for interval in FVG_INTERVALS:
        subset = [
            r for r in results
            if r["interval"] == interval
        ]

        if not subset:
            continue

        t = sum(
            r["result"] == "TARGET"
            for r in subset
        )

        d = sum(
            r["result"] in (
                "TARGET",
                "CANCELLED",
                "CANCELLED_AMBIGUOUS"
            )
            for r in subset
        )

        wr = t / d if d else 0

        print(
            f"  {interval}: "
            f"{len(subset)} setups | "
            f"Win rate {pct(wr)}"
        )

    print("\nTop symbols by number of setups:")

    symbol_counts = {}

    for r in results:
        symbol_counts[r["symbol"]] = (
            symbol_counts.get(r["symbol"], 0) + 1
        )

    for symbol, count in sorted(
        symbol_counts.items(),
        key=lambda x: x[1],
        reverse=True
    )[:10]:
        print(f"  {symbol}: {count}")

    print("=" * 78)


# ============================================================
# CSV
# ============================================================

def save_csv(results):
    if not results:
        return

    fields = [
        "symbol",
        "interval",
        "result",
        "signal_time",
        "exit_time",
        "entry",
        "exit",
        "target",
        "fvg_ratio",
        "volume_24h_at_signal",
        "c3_close",
        "gross_return",
        "net_return",
    ]

    with open(
        OUTPUT_CSV,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fields
        )

        writer.writeheader()

        for row in results:
            writer.writerow({
                key: row.get(key)
                for key in fields
            })

    print(
        f"\n[CSV] Saved: {OUTPUT_CSV}"
    )


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 78)
    print("BINANCE BEARISH FVG BACKTEST")
    print("=" * 78)

    print(f"Backtest days:       {BACKTEST_DAYS}")
    print("V2 execution model: entry=FVG high | SL=C3 high+buffer | TP=1.7%")
    print(f"Risk per trade:      {RISK_PER_TRADE * 100:.1f}%")
    print(f"Starting balance:    ${STARTING_BALANCE:,.2f}")
    print(f"Min 24H volume:      ${MIN_QUOTE_VOLUME_24H:,.0f}")
    print(f"FVG minimum ratio:   {FVG_MIN_RATIO * 100:.0f}%")
    print(f"Target:              {TARGET_PERCENT}%")
    print(f"Intervals:           {FVG_INTERVALS}")
    print(f"Fee per side:        {FEE_PER_SIDE * 100:.3f}%")
    print(f"Data endpoint:       {BINANCE_BASE_URL}")
    print("=" * 78)

    now_ms = int(time.time() * 1000)

    start_ms = (
        now_ms
        - BACKTEST_DAYS * 24 * 60 * 60 * 1000
    )

    # --------------------------------------------------------
    # Find symbols.
    # We first get currently active Binance Spot USDT symbols.
    # Historical delisted symbols are intentionally excluded.
    # --------------------------------------------------------

    try:
        symbols = get_spot_usdt_symbols()
    except Exception as e:
        print(f"FATAL: Could not get Binance symbols: {e}")
        raise SystemExit(1)

    if not symbols:
        print("FATAL: Could not get Binance symbols.")
        raise SystemExit(1)

    print(
        f"Current Spot USDT symbols: {len(symbols)}"
    )

    # Identify symbols currently above the volume threshold
    # with one bulk public-market-data request.
    try:
        current_volumes = get_current_24h_volumes()
    except Exception as e:
        print(f"FATAL: Could not get 24H volumes: {e}")
        raise SystemExit(1)

    qualified = []

    for index, symbol in enumerate(symbols, 1):
        volume = current_volumes.get(symbol)

        if volume is not None and volume >= MIN_QUOTE_VOLUME_24H:
            qualified.append(symbol)
            print(
                f"[QUALIFIED] {symbol} "
                f"${volume:,.0f}"
            )

        if index % 100 == 0:
            print(
                f"Volume scan: {index}/{len(symbols)}"
            )

    if MAX_SYMBOLS > 0:
        qualified = qualified[:MAX_SYMBOLS]

    print(
        f"\nSymbols selected for backtest: "
        f"{len(qualified)}"
    )

    all_results = []

    for number, symbol in enumerate(
        qualified,
        1
    ):
        print(
            f"\n[{number}/{len(qualified)}] "
            f"Testing {symbol}"
        )

        try:
            results = backtest_symbol(
                symbol,
                start_ms,
                now_ms
            )

            all_results.extend(results)

            print(
                f"  Results found: {len(results)}"
            )

        except Exception as e:
            print(
                f"[BACKTEST ERROR] "
                f"{symbol}: {e}"
            )

    print_report(all_results)
    save_csv(all_results)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FATAL BACKTEST ERROR: {e}")
        raise

