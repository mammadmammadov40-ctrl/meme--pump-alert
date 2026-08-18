import os
import time
import requests


# ============================================================
# ENVIRONMENT
# ============================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]


# ============================================================
# BINANCE SETTINGS
# ============================================================

BINANCE_URL = "https://api.binance.com"

# Hər 5 dəqiqədən bir
CHECK_INTERVAL = 300

REQUEST_TIMEOUT = 15

REQUEST_DELAY = 0.05


# ============================================================
# 3 x 5M CUMULATIVE RULES
# ============================================================

# Son 3 tamamlanmış 5M şamın
# birlikdə minimum qiymət artımı
MIN_3C_PRICE_CHANGE = 5.0


# Son 3 tamamlanmış 5M şamın
# birlikdə minimum volume-u
MIN_3C_VOLUME = 100_000


# Son 3 şamın volume-u əvvəlki
# 3 şamın volume-undan ən azı 1.5x çox olmalıdır
MIN_VOLUME_MULTIPLIER = 1.5


# ============================================================
# ALERT SETTINGS
# ============================================================

# Eyni coin yalnız 1 dəfə alert
ONE_ALERT_PER_COIN = True

# Bir scan-da maksimum alert
MAX_ALERTS_PER_SCAN = 5


# ============================================================
# MEMORY
# ============================================================

alerted_coins = set()


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "Binance-3x5M-Momentum-Bot/1.0"
})


# ============================================================
# SAFE FLOAT
# ============================================================

def safe_float(value, default=0.0):

    try:
        return float(value)

    except Exception:
        return default


# ============================================================
# TELEGRAM
# ============================================================

def send_message(text):

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": False
    }

    try:

        response = session.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        print(
            "TELEGRAM STATUS:",
            response.status_code
        )

        if not response.ok:

            print(
                "TELEGRAM ERROR:",
                response.text
            )

        return response.ok

    except Exception as e:

        print(
            "TELEGRAM CONNECTION ERROR:",
            e
        )

        return False


# ============================================================
# BINANCE USDT SPOT SYMBOLS
# ============================================================

def get_symbols():

    url = (
        f"{BINANCE_URL}/api/v3/exchangeInfo"
    )

    try:

        response = session.get(
            url,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

        symbols = []

        for item in data.get(
            "symbols",
            []
        ):

            # Aktiv olmalıdır
            if item.get("status") != "TRADING":
                continue

            # Yalnız USDT
            if item.get("quoteAsset") != "USDT":
                continue

            # Spot trading
            if item.get(
                "isSpotTradingAllowed",
                True
            ) is False:
                continue

            symbol = item.get("symbol")

            if not symbol:
                continue

            symbols.append(symbol)

        return symbols

    except Exception as e:

        print(
            "BINANCE SYMBOL ERROR:",
            e
        )

        return []


# ============================================================
# GET 5M CANDLES
# ============================================================

def get_5m_candles(symbol):

    url = (
        f"{BINANCE_URL}/api/v3/klines"
    )

    params = {
        "symbol": symbol,
        "interval": "5m",

        # Bizə 6 tamamlanmış candle lazımdır:
        #
        # əvvəlki 3:
        # 1,2,3
        #
        # son 3:
        # 4,5,6
        #
        # + hazırda açıq candle
        "limit": 7
    }

    try:

        response = session.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        if not response.ok:

            return None

        data = response.json()

        if len(data) < 7:

            return None

        # ----------------------------------------------------
        # SON AÇIQ CANDLE-ı ÇIXARIRIQ
        # ----------------------------------------------------

        completed = data[:-1]

        if len(completed) < 6:

            return None

        # ----------------------------------------------------
        # SON 3 TAMAMLANMIŞ CANDLE
        # ----------------------------------------------------

        current_1 = completed[-3]
        current_2 = completed[-2]
        current_3 = completed[-1]

        # ----------------------------------------------------
        # ONDAN ƏVVƏLKİ 3 CANDLE
        # ----------------------------------------------------

        previous_1 = completed[-6]
        previous_2 = completed[-5]
        previous_3 = completed[-4]


        # ====================================================
        # SON 3 CANDLE QİYMƏT
        # ====================================================

        first_open = safe_float(
            current_1[1]
        )

        last_close = safe_float(
            current_3[4]
        )

        if first_open <= 0:

            return None


        # ====================================================
        # ÜMUMİ 3 CANDLE PRICE CHANGE
        #
        # İlk candle OPEN
        # →
        # Üçüncü candle CLOSE
        # ====================================================

        price_change = (
            (
                last_close
                - first_open
            )
            / first_open
        ) * 100


        # ====================================================
        # SON 3 CANDLE VOLUME
        #
        # Ayrı-ayrılıqda yox!
        #
        # Hamısını toplayırıq.
        # ====================================================

        current_volume = (

            safe_float(current_1[7])

            +

            safe_float(current_2[7])

            +

            safe_float(current_3[7])
        )


        # ====================================================
        # ƏVVƏLKİ 3 CANDLE VOLUME
        # ====================================================

        previous_volume = (

            safe_float(previous_1[7])

            +

            safe_float(previous_2[7])

            +

            safe_float(previous_3[7])
        )


        # ====================================================
        # VOLUME MULTIPLIER
        # ====================================================

        if previous_volume > 0:

            volume_multiplier = (
                current_volume
                / previous_volume
            )

        else:

            volume_multiplier = 0


        # ====================================================
        # CANDLE VALUES
        # ====================================================

        candle_1_open = safe_float(
            current_1[1]
        )

        candle_1_close = safe_float(
            current_1[4]
        )

        candle_2_open = safe_float(
            current_2[1]
        )

        candle_2_close = safe_float(
            current_2[4]
        )

        candle_3_open = safe_float(
            current_3[1]
        )

        candle_3_close = safe_float(
            current_3[4]
        )


        return {

            "symbol": symbol,

            "first_open":
                first_open,

            "last_close":
                last_close,

            "price_change":
                price_change,

            "current_volume":
                current_volume,

            "previous_volume":
                previous_volume,

            "volume_multiplier":
                volume_multiplier,

            "candle_1_open":
                candle_1_open,

            "candle_1_close":
                candle_1_close,

            "candle_2_open":
                candle_2_open,

            "candle_2_close":
                candle_2_close,

            "candle_3_open":
                candle_3_open,

            "candle_3_close":
                candle_3_close
        }


    except Exception as e:

        print(
            "KLINE ERROR:",
            symbol,
            "|",
            e
        )

        return None


# ============================================================
# SIGNAL CHECK
# ============================================================

def is_signal(data):

    symbol = data[
        "symbol"
    ]

    price_change = data[
        "price_change"
    ]

    current_volume = data[
        "current_volume"
    ]

    volume_multiplier = data[
        "volume_multiplier"
    ]


    # ========================================================
    # ONE ALERT PER COIN
    # ========================================================

    if ONE_ALERT_PER_COIN:

        if symbol in alerted_coins:

            return False


    # ========================================================
    # 3 CANDLE PRICE
    # ========================================================

    if price_change < MIN_3C_PRICE_CHANGE:

        return False


    # ========================================================
    # 3 CANDLE TOTAL VOLUME
    # ========================================================

    if current_volume < MIN_3C_VOLUME:

        return False


    # ========================================================
    # VOLUME COMPARED WITH PREVIOUS 3
    # ========================================================

    if volume_multiplier < MIN_VOLUME_MULTIPLIER:

        return False


    return True


# ============================================================
# SIGNAL SCORE
# ============================================================

def calculate_score(data):

    score = 0


    price_change = data[
        "price_change"
    ]

    current_volume = data[
        "current_volume"
    ]

    multiplier = data[
        "volume_multiplier"
    ]


    # ========================================================
    # PRICE
    # ========================================================

    if price_change >= 5:

        score += 20

    if price_change >= 7.5:

        score += 10

    if price_change >= 10:

        score += 15

    if price_change >= 15:

        score += 15


    # ========================================================
    # TOTAL VOLUME
    # ========================================================

    if current_volume >= 100_000:

        score += 20

    if current_volume >= 250_000:

        score += 10

    if current_volume >= 500_000:

        score += 10

    if current_volume >= 1_000_000:

        score += 10


    # ========================================================
    # VOLUME MULTIPLIER
    # ========================================================

    if multiplier >= 1.5:

        score += 20

    if multiplier >= 2:

        score += 10

    if multiplier >= 3:

        score += 15

    if multiplier >= 5:

        score += 15


    return score


# ============================================================
# SCAN
# ============================================================

def scan(symbols):

    print()
    print(
        "=========================================="
    )

    print(
        "🔎 BINANCE 3×5M CUMULATIVE SCAN"
    )

    print(
        "=========================================="
    )


    candidates = []

    total = len(symbols)


    # ========================================================
    # SCAN ALL COINS
    # ========================================================

    for symbol in symbols:

        data = get_5m_candles(
            symbol
        )

        if data is None:

            continue


        if is_signal(data):

            data["score"] = (
                calculate_score(
                    data
                )
            )

            candidates.append(
                data
            )

            print(
                "🔥 CANDIDATE:",
                symbol,
                "| 3×5M PRICE:",
                f"{data['price_change']:+.2f}%",
                "| 3×5M VOLUME:",
                f"${data['current_volume']:,.0f}",
                "| MULTIPLIER:",
                f"{data['volume_multiplier']:.2f}x"
            )


        time.sleep(
            REQUEST_DELAY
        )


    # ========================================================
    # SORT
    # ========================================================

    candidates.sort(
        key=lambda x: (
            x["score"],
            x["price_change"],
            x["volume_multiplier"],
            x["current_volume"]
        ),
        reverse=True
    )


    print()

    print(
        "COINS SCANNED:",
        total
    )

    print(
        "STRONG CANDIDATES:",
        len(candidates)
    )

    print(
        "ALREADY ALERTED:",
        len(alerted_coins)
    )


    # ========================================================
    # SEND TELEGRAM
    # ========================================================

    alerts_sent = 0


    for data in candidates:

        if (
            alerts_sent
            >= MAX_ALERTS_PER_SCAN
        ):

            break


        symbol = data[
            "symbol"
        ]

        price_change = data[
            "price_change"
        ]

        current_volume = data[
            "current_volume"
        ]

        previous_volume = data[
            "previous_volume"
        ]

        multiplier = data[
            "volume_multiplier"
        ]

        score = data[
            "score"
        ]


        # ====================================================
        # MESSAGE
        # ====================================================

        message = (

            "🚨 BINANCE 3×5M MOMENTUM ALERT\n\n"

            f"🪙 Coin: {symbol}\n\n"

            f"📈 3×5M Ümumi Price Change: "
            f"{price_change:+.2f}%\n\n"

            f"💰 3×5M Ümumi Volume: "
            f"${current_volume:,.0f}\n"

            f"📊 Əvvəlki 3×5M Volume: "
            f"${previous_volume:,.0f}\n"

            f"🔥 Volume Multiplier: "
            f"{multiplier:.2f}x\n\n"

            f"⭐ Signal Score: "
            f"{score}\n\n"

            "✅ ŞƏRTLƏR:\n"

            "• 3 ardıcıl 5M candle\n"

            "• Ümumi price change ≥ +5%\n"

            "• Ümumi 3×5M volume ≥ $100K\n"

            "• Volume əvvəlki 3×5M-dən ≥ 1.5x\n\n"

            "⚠️ Eyni coin üçün yalnız "
            "1 dəfə alert veriləcək.\n\n"

            f"🔗 https://www.binance.com/"
            f"en/trade/{symbol}?type=spot"
        )


        if send_message(
            message
        ):

            alerted_coins.add(
                symbol
            )

            alerts_sent += 1

            print(
                "🚨 ALERT SENT:",
                symbol,
                "| PRICE:",
                f"{price_change:+.2f}%",
                "| VOLUME:",
                f"${current_volume:,.0f}",
                "| MULTIPLIER:",
                f"{multiplier:.2f}x"
            )


    print()

    print(
        "ALERTS SENT:",
        alerts_sent
    )


# ============================================================
# WAIT FOR NEXT 5M
# ============================================================

def wait_for_next_5m():

    now = time.time()

    next_run = (
        ((int(now) // 300) + 1)
        * 300
    )

    wait_seconds = (
        next_run - now
    )


    if wait_seconds < 1:

        wait_seconds = 1


    print(
        f"⏳ Next 3×5M scan in "
        f"{int(wait_seconds)} seconds..."
    )


    time.sleep(
        wait_seconds
    )


# ============================================================
# START
# ============================================================

print()

print(
    "🟢 BINANCE 3×5M CUMULATIVE MOMENTUM BOT"
)

print()

print(
    "🔵 Source: Binance Spot"
)

print(
    "⏱ Scan: Every 5 minutes"
)

print(
    "📊 Candle window: 3 completed 5M candles"
)

print(
    "📈 Minimum 3×5M price change:",
    f"+{MIN_3C_PRICE_CHANGE}%"
)

print(
    "💰 Minimum 3×5M total volume:",
    f"${MIN_3C_VOLUME:,}"
)

print(
    "🔥 Minimum volume multiplier:",
    f"{MIN_VOLUME_MULTIPLIER}x"
)

print(
    "⚠️ One alert per coin: ON"
)

print()


# ============================================================
# TELEGRAM START
# ============================================================

send_message(

    "🟢 BINANCE 3×5M MOMENTUM BOT STARTED\n\n"

    "🎯 Yeni sistem:\n"
    "Son 3 ardıcıl tamamlanmış 5M candle "
    "birlikdə hesablanır.\n\n"

    "📈 Price:\n"
    "İlk candle OPEN → üçüncü candle CLOSE\n"
    "Ümumi dəyişmə ≥ +5%\n\n"

    "💰 Volume:\n"
    "Son 3 candle volume-u BİRLİKDƏ "
    "hesablanır.\n"
    "Minimum: $100K\n\n"

    "🔥 Volume müqayisəsi:\n"
    "Son 3 candle volume-u əvvəlki "
    "3 candle volume-undan ≥ 1.5x olmalıdır.\n\n"

    "⏱ Hər 5 dəqiqədə yeni 3-lük yoxlanılır.\n\n"

    "⚠️ Eyni coin yalnız 1 dəfə alert.\n\n"

    "🔵 Mənbə: Binance Spot"
)


# ============================================================
# MAIN LOOP
# ============================================================

while True:

    try:

        symbols = get_symbols()

        print(
            "BINANCE USDT SPOT COINS:",
            len(symbols)
        )


        if symbols:

            scan(
                symbols
            )


    except Exception as e:

        print(
            "MAIN LOOP ERROR:",
            e
        )


    wait_for_next_5m()
