import os
import time
import requests


# ============================================================
# ENVIRONMENT
# ============================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]


# ============================================================
# BINANCE
# ============================================================

BINANCE_URL = "https://api.binance.com"

# Hər 5 dəqiqədən bir
CHECK_INTERVAL = 300

REQUEST_TIMEOUT = 15

# API sorğuları arasında kiçik fasilə
REQUEST_DELAY = 0.05


# ============================================================
# VOLUME SETTINGS
# ============================================================

# Əvvəl $50K idi.
# Daha həssas etmək üçün $30K
MIN_5M_VOLUME = 30_000

# Əvvəl 2.0x idi.
# Daha həssas etmək üçün 1.5x
MIN_VOLUME_MULTIPLIER = 1.5

# Əvvəl $25K idi.
# Daha həssas etmək üçün $15K
MIN_VOLUME_INCREASE = 15_000


# ============================================================
# PRICE SETTINGS
# ============================================================

# ƏSAS ŞƏRT:
#
# +1%  -> SIQNAL YOX
# +2%  -> SIQNAL YOX
# +3%  -> SIQNAL YOX
# +4%  -> SIQNAL YOX
# +5%  -> SIQNAL VAR
# +10% -> SIQNAL VAR
# +20% -> SIQNAL VAR
#
MIN_PRICE_CHANGE = 5.0

# Maksimum qiymət limiti yoxdur.
#
# Yəni:
# +5%  -> mümkündür
# +10% -> mümkündür
# +20% -> mümkündür
# +30% -> mümkündür
#
# Sadəcə volume şərtləri də ödənməlidir.


# ============================================================
# ALERT SETTINGS
# ============================================================

# Eyni coin yalnız 1 dəfə alert
ONE_ALERT_PER_COIN = True

# Bir scan zamanı maksimum alert
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
    "User-Agent": "Binance-5M-Momentum-Bot/2.0"
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
            "TELEGRAM ERROR:",
            e
        )

        return False


# ============================================================
# GET BINANCE USDT SPOT COINS
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

            # Aktiv coin
            if item.get("status") != "TRADING":
                continue

            # Yalnız USDT
            if item.get("quoteAsset") != "USDT":
                continue

            # Yalnız Spot
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

def get_5m_data(symbol):

    url = (
        f"{BINANCE_URL}/api/v3/klines"
    )

    params = {
        "symbol": symbol,
        "interval": "5m",
        "limit": 5
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

        if len(data) < 5:

            return None


        # ====================================================
        # SON TAMAMLANMIŞ 5M CANDLE
        # ====================================================

        current = data[-2]

        # Əvvəlki 3 tamamlanmış candle
        old_1 = data[-3]
        old_2 = data[-4]
        old_3 = data[-5]


        # ====================================================
        # CURRENT CANDLE
        # ====================================================

        open_price = safe_float(
            current[1]
        )

        high_price = safe_float(
            current[2]
        )

        low_price = safe_float(
            current[3]
        )

        close_price = safe_float(
            current[4]
        )

        # Quote volume = USDT
        current_volume = safe_float(
            current[7]
        )


        # ====================================================
        # PREVIOUS 3 VOLUMES
        # ====================================================

        volume_1 = safe_float(
            old_1[7]
        )

        volume_2 = safe_float(
            old_2[7]
        )

        volume_3 = safe_float(
            old_3[7]
        )


        average_volume = (
            volume_1
            + volume_2
            + volume_3
        ) / 3


        # ====================================================
        # PRICE CHANGE
        # ====================================================

        if open_price <= 0:

            return None


        price_change = (
            (
                close_price
                - open_price
            )
            / open_price
        ) * 100


        # ====================================================
        # VOLUME MULTIPLIER
        # ====================================================

        if average_volume > 0:

            volume_multiplier = (
                current_volume
                / average_volume
            )

        else:

            volume_multiplier = 0


        # ====================================================
        # VOLUME INCREASE
        # ====================================================

        volume_increase = (
            current_volume
            - average_volume
        )


        return {

            "symbol": symbol,

            "open": open_price,

            "high": high_price,

            "low": low_price,

            "close": close_price,

            "volume": current_volume,

            "average_volume":
                average_volume,

            "volume_multiplier":
                volume_multiplier,

            "volume_increase":
                volume_increase,

            "price_change":
                price_change
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

    symbol = data["symbol"]

    volume = data["volume"]

    multiplier = data[
        "volume_multiplier"
    ]

    volume_increase = data[
        "volume_increase"
    ]

    price_change = data[
        "price_change"
    ]


    # ========================================================
    # ONE ALERT PER COIN
    # ========================================================

    if ONE_ALERT_PER_COIN:

        if symbol in alerted_coins:

            return False


    # ========================================================
    # MINIMUM VOLUME
    # ========================================================

    if volume < MIN_5M_VOLUME:

        return False


    # ========================================================
    # VOLUME MULTIPLIER
    # ========================================================

    if multiplier < MIN_VOLUME_MULTIPLIER:

        return False


    # ========================================================
    # REAL VOLUME INCREASE
    # ========================================================

    if volume_increase < MIN_VOLUME_INCREASE:

        return False


    # ========================================================
    # PRICE +5% OR HIGHER
    # ========================================================

    if price_change < MIN_PRICE_CHANGE:

        return False


    # ========================================================
    # SIGNAL PASSED
    # ========================================================

    return True


# ============================================================
# SIGNAL SCORE
# ============================================================

def calculate_score(data):

    score = 0

    multiplier = data[
        "volume_multiplier"
    ]

    price_change = data[
        "price_change"
    ]

    volume = data[
        "volume"
    ]


    # ========================================================
    # VOLUME SCORE
    # ========================================================

    if multiplier >= 1.5:

        score += 20

    if multiplier >= 2:

        score += 20

    if multiplier >= 3:

        score += 15

    if multiplier >= 5:

        score += 20

    if multiplier >= 10:

        score += 20


    # ========================================================
    # ABSOLUTE VOLUME
    # ========================================================

    if volume >= 50_000:

        score += 10

    if volume >= 100_000:

        score += 10

    if volume >= 250_000:

        score += 10

    if volume >= 500_000:

        score += 10

    if volume >= 1_000_000:

        score += 10


    # ========================================================
    # PRICE MOMENTUM
    # ========================================================

    if price_change >= 5:

        score += 10

    if price_change >= 10:

        score += 10

    if price_change >= 20:

        score += 15

    if price_change >= 30:

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
        "🔎 BINANCE 5M VOLUME + PRICE SCAN"
    )

    print(
        "=========================================="
    )


    candidates = []

    total = len(symbols)


    # ========================================================
    # CHECK ALL COINS
    # ========================================================

    for index, symbol in enumerate(
        symbols,
        start=1
    ):

        data = get_5m_data(
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
                "| PRICE:",
                f"{data['price_change']:+.2f}%",
                "| VOLUME:",
                f"{data['volume_multiplier']:.2f}x"
            )


        time.sleep(
            REQUEST_DELAY
        )


    # ========================================================
    # SORT BY SCORE
    # ========================================================

    candidates.sort(
        key=lambda x: (
            x["score"],
            x["price_change"],
            x["volume_multiplier"],
            x["volume"]
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
    # SEND ALERTS
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

        volume = data[
            "volume"
        ]

        average_volume = data[
            "average_volume"
        ]

        multiplier = data[
            "volume_multiplier"
        ]

        volume_increase = data[
            "volume_increase"
        ]

        price_change = data[
            "price_change"
        ]

        score = data[
            "score"
        ]


        # ====================================================
        # TELEGRAM MESSAGE
        # ====================================================

        message = (

            "🚨 BINANCE 5M MOMENTUM ALERT\n\n"

            f"🪙 Coin: {symbol}\n\n"

            f"📈 5M Price Change: "
            f"{price_change:+.2f}%\n\n"

            f"💰 5M Volume: "
            f"${volume:,.0f}\n"

            f"📊 Previous 3×5M Average: "
            f"${average_volume:,.0f}\n"

            f"🔥 Volume Increase: "
            f"+${volume_increase:,.0f}\n"

            f"🚀 Volume Multiplier: "
            f"{multiplier:.2f}x\n\n"

            f"⭐ Signal Score: "
            f"{score}\n\n"

            "✅ SIGNAL FILTER:\n"
            "• Price ≥ +5%\n"
            "• Volume ≥ $30K\n"
            "• Volume ≥ 1.5x average\n"
            "• Volume increase ≥ $15K\n\n"

            "⚠️ Bu coin üçün yalnız "
            "1 dəfə alert veriləcək.\n"

            "Sonradan +20%, +50%, +100% "
            "qalxsa ikinci alert gəlməyəcək.\n\n"

            f"🔗 https://www.binance.com/"
            f"en/trade/{symbol}?type=spot"
        )


        # ====================================================
        # SEND TELEGRAM
        # ====================================================

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
                f"{multiplier:.2f}x",
                "| SCORE:",
                score
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
        f"⏳ Next 5M scan in "
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
    "🟢 BINANCE 5M VOLUME + PRICE MOMENTUM BOT"
)

print()

print(
    "🔵 Source: Binance Spot"
)

print(
    "⏱ Scan interval: 5 minutes"
)

print(
    "📈 Minimum price change:",
    f"+{MIN_PRICE_CHANGE}%"
)

print(
    "💰 Minimum 5M volume:",
    f"${MIN_5M_VOLUME:,}"
)

print(
    "🚀 Minimum volume multiplier:",
    f"{MIN_VOLUME_MULTIPLIER}x"
)

print(
    "🔥 Minimum volume increase:",
    f"${MIN_VOLUME_INCREASE:,}"
)

print(
    "⚠️ One alert per coin: ON"
)

print()


# ============================================================
# TELEGRAM START MESSAGE
# ============================================================

send_message(

    "🟢 BINANCE 5M MOMENTUM BOT STARTED\n\n"

    "🎯 Məqsəd:\n"
    "Binance Spot-da qiyməti +5% və daha çox "
    "qalxan və eyni zamanda volume yüklənən "
    "coinləri tapmaq.\n\n"

    "📊 Hər 5 dəqiqədən bir yoxlayır.\n\n"

    "🔥 Qaydalar:\n"
    "• 5M qiymət ≥ +5%\n"
    "• 5M volume ≥ $30K\n"
    "• Volume ≥ 1.5x əvvəlki 3×5M orta\n"
    "• Volume artımı ≥ $15K\n\n"

    "❌ +1%, +2%, +3%, +4% "
    "hərəkətlərə alert yoxdur.\n\n"

    "✅ +5% və yuxarı hərəkət edən "
    "coinlər yoxlanılır.\n\n"

    "⚠️ Eyni coin yalnız 1 dəfə alert.\n"
    "Sonradan +20%, +50%, +100% "
    "qalxsa ikinci alert gəlməyəcək.\n\n"

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


    # Növbəti 5M sərhədini gözlə
    wait_for_next_5m()
