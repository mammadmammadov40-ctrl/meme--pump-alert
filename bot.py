import os
import time
import requests


# ============================================================
# ENVIRONMENT
# ============================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]


# ============================================================
# SETTINGS
# ============================================================

BINANCE_URL = "https://api.binance.com"

# Təxminən hər 30 saniyədən bir yoxlayırıq.
# Beləliklə 5 dəqiqəlik sərhədi gözləmədən
# hərəkəti daha tez görə bilər.
CHECK_INTERVAL = 30

# ============================================================
# 3 x 5M PRICE RULE
# ============================================================

# Son 3 tamamlanmış / cari 5M şam birlikdə
# minimum neçə faiz qalxmalıdır?
MIN_3CANDLE_PRICE_CHANGE = 5.0


# ============================================================
# 3 x 5M VOLUME RULE
# ============================================================

# Son 3 x 5M şamın ümumi USDT volume-u
# ən azı bu qədər olmalıdır.
MIN_3CANDLE_VOLUME = 100_000


# Son 3 şamın ümumi volume-u əvvəlki
# 3 şamın ümumi volume-undan neçə dəfə böyük olmalıdır?
MIN_VOLUME_MULTIPLIER = 1.5


# ============================================================
# EARLY SIGNAL
# ============================================================

# Cari hərəkətdə minimum müsbət qiymət.
MIN_CURRENT_PRICE_CHANGE = 0.0

# Bir anda həddən artıq qaçmış coinləri azaltmaq.
MAX_3CANDLE_PRICE_CHANGE = 15.0


# ============================================================
# REPEAT ALERT
# ============================================================

# Eyni coin üçün yenidən siqnal verməmək.
ONE_ALERT_PER_COIN = True

# İstəsən sonradan False edə bilərsən.
# True olduqda bot həmin coinə bir dəfə siqnal verir.
# 20%, 50%, 100% qalxanda yenidən vermir.


# ============================================================
# SCAN
# ============================================================

MAX_ALERTS_PER_SCAN = 5

REQUEST_TIMEOUT = 15

REQUEST_DELAY = 0.03


# ============================================================
# MEMORY
# ============================================================

alerted_coins = set()


# ============================================================
# SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "Binance-3x5M-Early-Momentum-Bot/1.0"
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
# GET BINANCE SYMBOLS
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

            if item.get("status") != "TRADING":
                continue

            if item.get("quoteAsset") != "USDT":
                continue

            if item.get(
                "isSpotTradingAllowed",
                True
            ) is False:

                continue

            symbol = item.get("symbol")

            if symbol:

                symbols.append(symbol)

        return symbols

    except Exception as e:

        print(
            "SYMBOL ERROR:",
            e
        )

        return []


# ============================================================
# GET 5M KLINES
# ============================================================

def get_5m_data(symbol):

    url = (
        f"{BINANCE_URL}/api/v3/klines"
    )

    params = {
        "symbol": symbol,
        "interval": "5m",

        # 7 candle:
        # 3 əvvəlki
        # 3 son candle
        # 1 əlavə
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


        # ====================================================
        # CANDLE STRUCTURE
        #
        # Binance:
        #
        # [0] open time
        # [1] open
        # [2] high
        # [3] low
        # [4] close
        # [5] volume
        # [6] close time
        # [7] quote volume
        #
        # Biz USDT volume üçün [7] istifadə edirik.
        # ====================================================

        # Son bağlanmış candle
        closed_1 = data[-2]

        # Ondan əvvəl
        closed_2 = data[-3]

        # Ondan əvvəl
        closed_3 = data[-4]

        # Əvvəlki 3 candle
        old_1 = data[-5]
        old_2 = data[-6]
        old_3 = data[-7]


        # ====================================================
        # SON 3 ŞAM
        # ====================================================

        candles = [
            closed_3,
            closed_2,
            closed_1
        ]


        first_open = safe_float(
            closed_3[1]
        )

        last_close = safe_float(
            closed_1[4]
        )


        if first_open <= 0:

            return None


        # ====================================================
        # 3 CANDLE PRICE CHANGE
        # ====================================================

        price_change = (

            (
                last_close
                - first_open
            )
            / first_open

        ) * 100


        # ====================================================
        # 3 CANDLE TOTAL VOLUME
        # ====================================================

        total_volume = sum(

            safe_float(
                candle[7]
            )

            for candle in candles
        )


        # ====================================================
        # PREVIOUS 3 CANDLE VOLUME
        # ====================================================

        previous_volume = sum(

            safe_float(
                candle[7]
            )

            for candle in [
                old_3,
                old_2,
                old_1
            ]
        )


        # ====================================================
        # VOLUME MULTIPLIER
        # ====================================================

        if previous_volume > 0:

            volume_multiplier = (

                total_volume
                / previous_volume

            )

        else:

            volume_multiplier = 0


        # ====================================================
        # CURRENT 5M CANDLE
        # ====================================================

        current = data[-1]

        current_open = safe_float(
            current[1]
        )

        current_close = safe_float(
            current[4]
        )

        current_volume = safe_float(
            current[7]
        )


        if current_open > 0:

            current_price_change = (

                (
                    current_close
                    - current_open
                )
                / current_open

            ) * 100

        else:

            current_price_change = 0


        # ====================================================
        # RETURN
        # ====================================================

        return {

            "symbol": symbol,

            "first_open": first_open,

            "last_close": last_close,

            "price_change": price_change,

            "total_volume": total_volume,

            "previous_volume":
                previous_volume,

            "volume_multiplier":
                volume_multiplier,

            "current_price_change":
                current_price_change,

            "current_volume":
                current_volume
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
# SIGNAL
# ============================================================

def is_signal(data):

    symbol = data[
        "symbol"
    ]

    price_change = data[
        "price_change"
    ]

    total_volume = data[
        "total_volume"
    ]

    volume_multiplier = data[
        "volume_multiplier"
    ]

    current_price_change = data[
        "current_price_change"
    ]


    # ========================================================
    # ONE ALERT
    # ========================================================

    if ONE_ALERT_PER_COIN:

        if symbol in alerted_coins:

            return False


    # ========================================================
    # 3 CANDLE PRICE
    # ========================================================

    if price_change < MIN_3CANDLE_PRICE_CHANGE:

        return False


    # ========================================================
    # TOO LATE
    # ========================================================

    if price_change > MAX_3CANDLE_PRICE_CHANGE:

        return False


    # ========================================================
    # CURRENT MOMENTUM
    # ========================================================

    if current_price_change < MIN_CURRENT_PRICE_CHANGE:

        return False


    # ========================================================
    # TOTAL VOLUME
    # ========================================================

    if total_volume < MIN_3CANDLE_VOLUME:

        return False


    # ========================================================
    # VOLUME MULTIPLIER
    # ========================================================

    if volume_multiplier < MIN_VOLUME_MULTIPLIER:

        return False


    return True


# ============================================================
# SCORE
# ============================================================

def calculate_score(data):

    score = 0

    price = data[
        "price_change"
    ]

    volume = data[
        "total_volume"
    ]

    multiplier = data[
        "volume_multiplier"
    ]


    # ========================================================
    # PRICE
    # ========================================================

    if price >= 5:

        score += 30

    if price >= 7:

        score += 10

    if price >= 10:

        score += 10


    # ========================================================
    # VOLUME
    # ========================================================

    if volume >= 100_000:

        score += 20

    if volume >= 250_000:

        score += 15

    if volume >= 500_000:

        score += 15

    if volume >= 1_000_000:

        score += 15


    # ========================================================
    # MULTIPLIER
    # ========================================================

    if multiplier >= 1.5:

        score += 20

    if multiplier >= 2:

        score += 10

    if multiplier >= 3:

        score += 10

    if multiplier >= 5:

        score += 10


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
        "🔎 BINANCE 3×5M MOMENTUM SCAN"
    )

    print(
        "=========================================="
    )


    candidates = []

    for symbol in symbols:

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
                f"{data['price_change']:.2f}%",
                "| VOLUME:",
                f"${data['total_volume']:,.0f}",
                "| MULT:",
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

            x["total_volume"]

        ),

        reverse=True
    )


    print()
    print(
        "CANDIDATES:",
        len(candidates)
    )


    # ========================================================
    # ALERT
    # ========================================================

    sent = 0


    for data in candidates:

        if sent >= MAX_ALERTS_PER_SCAN:

            break


        symbol = data[
            "symbol"
        ]

        price = data[
            "price_change"
        ]

        volume = data[
            "total_volume"
        ]

        previous_volume = data[
            "previous_volume"
        ]

        multiplier = data[
            "volume_multiplier"
        ]

        current_price = data[
            "current_price_change"
        ]

        score = data[
            "score"
        ]


        # ====================================================
        # TELEGRAM
        # ====================================================

        message = (

            "🚨 BINANCE EARLY MOMENTUM\n\n"

            f"🪙 {symbol}\n\n"

            "📊 SON 3 × 5M:\n"

            f"📈 Ümumi qiymət: "
            f"+{price:.2f}%\n"

            f"💰 Ümumi volume: "
            f"${volume:,.0f}\n"

            f"📊 Əvvəlki 3×5M volume: "
            f"${previous_volume:,.0f}\n"

            f"🔥 Volume artımı: "
            f"{multiplier:.2f}x\n\n"

            f"🕐 Cari 5M hərəkət: "
            f"{current_price:+.2f}%\n\n"

            f"⭐ Score: {score}\n\n"

            "🎯 Şərt:\n"

            "3 ardıcıl 5M şamın "
            "ümumi qiymət +5%-dən yuxarıdır.\n"

            "3 şamın volume-u birlikdə "
            "hesablanıb.\n\n"

            "⚠️ Eyni coin üçün "
            "təkrar alert verilmir.\n\n"

            f"🔗 https://www.binance.com/"
            f"en/trade/{symbol}?type=spot"
        )


        if send_message(
            message
        ):

            alerted_coins.add(
                symbol
            )

            sent += 1

            print(
                "🚨 ALERT SENT:",
                symbol
            )


    print()
    print(
        "ALERTS SENT:",
        sent
    )


# ============================================================
# START
# ============================================================

print()
print(
    "🟢 BINANCE 3×5M EARLY MOMENTUM BOT"
)
print()

print(
    "Source: Binance Spot"
)

print(
    "Scan: every 30 seconds"
)

print(
    "3×5M minimum price:",
    f"{MIN_3CANDLE_PRICE_CHANGE}%"
)

print(
    "3×5M minimum volume:",
    f"${MIN_3CANDLE_VOLUME:,}"
)

print(
    "Volume multiplier:",
    f"{MIN_VOLUME_MULTIPLIER}x"
)

print(
    "Maximum 3×5M price:",
    f"{MAX_3CANDLE_PRICE_CHANGE}%"
)

print(
    "One alert per coin: ON"
)

print()


# ============================================================
# TELEGRAM START
# ============================================================

send_message(

    "🟢 BINANCE 3×5M EARLY MOMENTUM BOT STARTED\n\n"

    "🎯 Məqsəd:\n"
    "3 ardıcıl 5 dəqiqəlik şamda "
    "toplanan momentum-u tapmaq.\n\n"

    "📈 Qiymət:\n"
    "3×5M ümumi artım ≥ +5%\n\n"

    "💰 Volume:\n"
    "3×5M volume birlikdə hesablanır.\n\n"

    "🔥 Volume:\n"
    "Ümumi volume əvvəlki 3×5M "
    "volume-dan ən azı 1.5x olmalıdır.\n\n"

    "⚡ Scan hər 30 saniyədə aparılır.\n\n"

    "⚠️ Eyni coin üçün təkrar alert yoxdur.\n\n"

    "🔵 Binance Spot"
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


    print()

    print(
        "⏳ Next scan in",
        CHECK_INTERVAL,
        "seconds..."
    )

    print()

    time.sleep(
        CHECK_INTERVAL
    )
