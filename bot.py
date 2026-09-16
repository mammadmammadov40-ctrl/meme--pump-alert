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
#   Binance Spot USDT pairs
#   Top 100 by Binance 24H quote volume
#
# STRATEGY:
#
# 1. EMA20 və EMA50 hesablanır.
#
# 2. Bullish breakout candle gözlənilir.
#
# 3. Breakout candle:
#      - Bullish olmalıdır
#      - Açılışı EMA20 və EMA50-dən yuxarı olmalıdır
#      - Bağlanışı EMA20 və EMA50-dən yuxarı olmalıdır
#      - Bağlanışı əvvəlki 20 şamın HIGH səviyyəsindən
#        yuxarı olmalıdır
#      - Yəni breakout fitillə yox, BAĞLANIŞLA olmalıdır
#
# 4. Breakout şamından sonra gələn 2 CLOSED candle gözlənilir.
#
# 5. Bu 2 şamın heç birinin HIGH-i breakout candle
#    HIGH səviyyəsini keçməməlidir.
#
# 6. Əgər 2 şam ərzində breakout HIGH keçilərsə:
#      - Setup ləğv edilir
#      - Yeni reset gözlənilir
#
# 7. Əgər 2 şam breakout HIGH-i keçməzsə:
#      - BUY SIGNAL
#
# 8. Siqnaldan sonra qiymət EMA-ların üzərində qalırsa
#    yeni siqnal verilmir.
#
# 9. RESET:
#      Ən azı 20 ardıcıl CLOSED candle ərzində:
#        - Open EMA20 və EMA50-dən aşağı
#        - Close EMA20 və EMA50-dən aşağı
#
#    olduqda həmin coin yenidən aktivləşir.
#
# 10. Resetdən sonra yeni bullish breakout yenidən axtarılır.
#
# IMPORTANT:
#   Ema20 > Ema50 şərti YOXDUR.
#
#   Sadəcə breakout candle həm EMA20,
#   həm də EMA50 üzərində olmalıdır.
#
# ============================================================


BINANCE_BASE_URL = "https://api.binance.com"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


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

KLINE_LIMIT = 200


# ============================================================
# STATE
# ============================================================

# Son görülmüş CLOSED candle
last_closed_candle = {}


# ============================================================
# COIN STATE
#
# States:
#
# READY
#   Yeni breakout axtarılır
#
# WAIT_CONFIRMATION
#   Breakout gəlib, 2 şam gözlənilir
#
# SIGNALLED
#   Siqnal verilib.
#   Reset gözlənilir.
# ============================================================

coin_state = {}

breakout_data = {}


# ============================================================
# RESET COUNT
# ============================================================

reset_count = {}


# ============================================================
# PROCESSED SIGNALS
# ============================================================

processed_signals = set()


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "BinanceTop100EMABot/1.0"
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
            "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID"
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

def get_spot_usdt_symbols():

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
# GET 24H VOLUMES
# ============================================================

def get_24h_volumes():

    data = binance_get(
        "/api/v3/ticker/24hr"
    )

    if not data:

        return {}

    volumes = {}

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

            volumes[symbol] = quote_volume

        except Exception:

            continue

    return volumes


# ============================================================
# GET TOP 100 BINANCE COINS
# ============================================================

def get_top_100_symbols():

    spot_symbols = get_spot_usdt_symbols()

    if not spot_symbols:

        return []

    volumes = get_24h_volumes()

    if not volumes:

        return []

    # Yalnız Binance Spot USDT cütləri
    eligible = [
        symbol
        for symbol in spot_symbols
        if symbol in volumes
    ]

    # Binance 24H quote volume-a görə
    # böyükdən kiçiyə sıralayırıq
    eligible.sort(
        key=lambda symbol: volumes.get(
            symbol,
            0
        ),
        reverse=True
    )

    top_100 = eligible[
        :TOP_COINS
    ]

    return top_100


# ============================================================
# GET CLOSED CANDLES
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

            closed.append(candle)

    return closed


# ============================================================
# EMA
# ============================================================

def calculate_ema(values, period):

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
# CANDLE DATA
# ============================================================

def candle_open(candle):

    return float(candle[1])


def candle_high(candle):

    return float(candle[2])


def candle_low(candle):

    return float(candle[3])


def candle_close(candle):

    return float(candle[4])


# ============================================================
# EMA VALUES FOR A CANDLE
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
# RESET CONDITION
#
# Candle OPEN and CLOSE must BOTH be below
# EMA20 and EMA50.
# ============================================================

def candle_is_reset_candle(
    candle,
    ema20,
    ema50
):

    if ema20 is None or ema50 is None:

        return False

    open_price = candle_open(candle)

    close_price = candle_close(candle)

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
# BREAKOUT CONDITION
#
# IMPORTANT:
# Breakout is based on CLOSE.
#
# Candle HIGH is NOT used for breakout.
# ============================================================

def check_breakout(
    candles,
    index
):

    if index < BREAKOUT_LOOKBACK:

        return None

    candle = candles[index]

    open_price = candle_open(candle)

    high = candle_high(candle)

    close = candle_close(candle)

    # --------------------------------------------------------
    # EMA
    # --------------------------------------------------------

    ema20, ema50 = get_ema_values(
        candles,
        index
    )

    if ema20 is None or ema50 is None:

        return None

    # --------------------------------------------------------
    # 1. BULLISH CANDLE
    # --------------------------------------------------------

    if close <= open_price:

        return None

    # --------------------------------------------------------
    # 2. OPEN ABOVE BOTH EMAs
    # --------------------------------------------------------

    if not (
        open_price > ema20
        and
        open_price > ema50
    ):

        return None

    # --------------------------------------------------------
    # 3. CLOSE ABOVE BOTH EMAs
    # --------------------------------------------------------

    if not (
        close > ema20
        and
        close > ema50
    ):

        return None

    # --------------------------------------------------------
    # 4. PREVIOUS 20 CANDLE HIGH
    #
    # Breakout candle itself is excluded.
    # --------------------------------------------------------

    previous_candles = candles[
        index - BREAKOUT_LOOKBACK:index
    ]

    if len(previous_candles) < BREAKOUT_LOOKBACK:

        return None

    previous_20_high = max(
        candle_high(c)
        for c in previous_candles
    )

    # --------------------------------------------------------
    # CLOSE must break previous 20 HIGH
    # --------------------------------------------------------

    if close <= previous_20_high:

        return None

    return {

        "index": index,

        "open": open_price,

        "high": high,

        "close": close,

        "ema20": ema20,

        "ema50": ema50,

        "previous_20_high": previous_20_high,

        "open_time": int(
            candle[0]
        )
    }


# ============================================================
# TELEGRAM BUY SIGNAL
# ============================================================

def send_buy_signal(
    symbol,
    data,
    confirmation_candles
):

    candle_time = datetime.fromtimestamp(
        data["open_time"] / 1000,
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M UTC"
    )

    message = (

        "🟢 <b>EMA20 / EMA50 BREAKOUT BUY</b>\n\n"

        f"<b>{symbol}</b> — {INTERVAL}\n\n"

        f"💰 <b>Breakout Close:</b> "
        f"{format_price(data['close'])}\n"

        f"🚀 <b>Breakout High:</b> "
        f"{format_price(data['high'])}\n"

        f"📈 <b>Previous 20 High:</b> "
        f"{format_price(data['previous_20_high'])}\n\n"

        f"📊 <b>EMA20:</b> "
        f"{format_price(data['ema20'])}\n"

        f"📊 <b>EMA50:</b> "
        f"{format_price(data['ema50'])}\n\n"

        "✅ Bullish candle\n"
        "✅ Open above EMA20 & EMA50\n"
        "✅ Close above EMA20 & EMA50\n"
        "✅ 20-candle HIGH broken by CLOSE\n"
        "✅ Next 2 candles did NOT break breakout HIGH\n\n"

        f"🕐 <b>Breakout candle:</b> "
        f"{candle_time}\n\n"

        "🔒 <b>Signal active — reset required</b>"
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

    # --------------------------------------------------------
    # Current latest closed candle
    # --------------------------------------------------------

    latest = candles[-1]

    latest_open_time = int(
        latest[0]
    )

    # --------------------------------------------------------
    # Process each NEW candle only once
    # --------------------------------------------------------

    previous_time = last_closed_candle.get(
        symbol
    )

    if previous_time == latest_open_time:

        return

    last_closed_candle[symbol] = (
        latest_open_time
    )

    # --------------------------------------------------------
    # Initialize state
    # --------------------------------------------------------

    if symbol not in coin_state:

        coin_state[symbol] = "READY"

        reset_count[symbol] = 0

    state = coin_state[symbol]


    # ========================================================
    # WAITING FOR CONFIRMATION
    # ========================================================

    if state == "WAIT_CONFIRMATION":

        data = breakout_data.get(
            symbol
        )

        if not data:

            coin_state[symbol] = "READY"

            return

        breakout_high = data["high"]

        confirmation_count = (
            data["confirmation_count"]
        )

        current_high = candle_high(
            latest
        )

        # ----------------------------------------------------
        # If current candle HIGH breaks breakout HIGH:
        # CANCEL
        # ----------------------------------------------------

        if current_high > breakout_high:

            print(
                f"[CANCEL] {symbol} | "
                f"Confirmation candle HIGH "
                f"{format_price(current_high)} "
                f"> breakout HIGH "
                f"{format_price(breakout_high)}"
            )

            coin_state[symbol] = "READY"

            breakout_data.pop(
                symbol,
                None
            )

            return

        # ----------------------------------------------------
        # Confirmation candle accepted
        # ----------------------------------------------------

        confirmation_count += 1

        data["confirmation_count"] = (
            confirmation_count
        )

        print(
            f"[CONFIRM] {symbol} | "
            f"{confirmation_count}/"
            f"{CONFIRMATION_CANDLES} | "
            f"High="
            f"{format_price(current_high)} | "
            f"Breakout High="
            f"{format_price(breakout_high)}"
        )

        # ----------------------------------------------------
        # Need 2 candles
        # ----------------------------------------------------

        if confirmation_count < CONFIRMATION_CANDLES:

            return

        # ----------------------------------------------------
        # 2 candles passed
        # BUY SIGNAL
        # ----------------------------------------------------

        signal_key = (
            symbol,
            data["open_time"]
        )

        if signal_key not in processed_signals:

            processed_signals.add(
                signal_key
            )

            send_buy_signal(
                symbol,
                data,
                confirmation_count
            )

        # ----------------------------------------------------
        # Lock symbol until reset
        # ----------------------------------------------------

        coin_state[symbol] = "SIGNALLED"

        breakout_data.pop(
            symbol,
            None
        )

        reset_count[symbol] = 0

        return


    # ========================================================
    # SIGNALLED STATE
    #
    # No more signals until RESET.
    # ========================================================

    if state == "SIGNALLED":

        ema20, ema50 = get_ema_values(
            candles,
            len(candles) - 1
        )

        if ema20 is None or ema50 is None:

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
                    f"Counter returned to 0"
                )

            reset_count[symbol] = 0

        # ----------------------------------------------------
        # 20 consecutive candles below both EMAs
        # ----------------------------------------------------

        if reset_count[symbol] >= RESET_CANDLES:

            print(
                f"[RESET COMPLETE] {symbol} | "
                f"{RESET_CANDLES} consecutive candles "
                f"below EMA20 & EMA50"
            )

            coin_state[symbol] = "READY"

            reset_count[symbol] = 0

        return


    # ========================================================
    # READY STATE
    #
    # Search for new breakout
    # ========================================================

    if state == "READY":

        index = len(candles) - 1

        breakout = check_breakout(
            candles,
            index
        )

        if not breakout:

            return

        # ----------------------------------------------------
        # BREAKOUT FOUND
        # ----------------------------------------------------

        print("")
        print(
            "=================================================="
        )

        print(
            f"[BREAKOUT] {symbol}"
        )

        print(
            f"Close: "
            f"{format_price(breakout['close'])}"
        )

        print(
            f"Breakout High: "
            f"{format_price(breakout['high'])}"
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
            "[WAIT] Next 2 candles..."
        )

        print(
            "=================================================="
        )

        # ----------------------------------------------------
        # Save breakout
        # ----------------------------------------------------

        breakout["confirmation_count"] = 0

        breakout_data[symbol] = breakout

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

    # --------------------------------------------------------
    # Get current Binance Top 100
    # --------------------------------------------------------

    symbols = get_top_100_symbols()

    if not symbols:

        print(
            "[ERROR] Could not get Binance Top 100"
        )

        return

    print(
        f"[INFO] Binance Top {len(symbols)} symbols"
    )

    # --------------------------------------------------------
    # Remove state for coins that are no longer Top 100
    # --------------------------------------------------------

    current_symbols = set(symbols)

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

    # --------------------------------------------------------
    # Analyze Top 100
    # --------------------------------------------------------

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
        f"Top coins: {TOP_COINS}"
    )

    print(
        f"EMA: {EMA_FAST} / {EMA_SLOW}"
    )

    print(
        f"Breakout: "
        f"Previous {BREAKOUT_LOOKBACK} candle HIGH"
    )

    print(
        "Breakout method: CLOSE only"
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
        "Reset requirement: "
        "OPEN + CLOSE below BOTH EMAs"
    )

    print(
        "EMA20 > EMA50 requirement: NONE"
    )

    print(
        "Daily volume filter: NONE"
    )

    print(
        "Coin source: BINANCE LIVE DATA"
    )

    print(
        "Strategy: BUY ONLY"
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
            time.time() -
            start_time
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
