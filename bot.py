import os
import time
import json
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

# Hər 5 dəqiqədən bir
CHECK_INTERVAL = 300

REQUEST_TIMEOUT = 15

REQUEST_DELAY = 0.05


# ============================================================
# VOLUME RULES
# ============================================================

# Son tamamlanmış 5M candle minimum volume
MIN_5M_VOLUME = 50_000

# Cari volume əvvəlki 3 candle ortalamasından
# minimum 2 dəfə böyük olmalıdır
MIN_VOLUME_MULTIPLIER = 2.0

# Volume ən azı $25K artmalıdır
MIN_VOLUME_INCREASE = 25_000


# ============================================================
# PRICE RULES
# ============================================================

# ÇOX VACİB:
# 5M candle qiymət artımı minimum +20% olmalıdır.
#
# +1%  -> ALERT YOX
# +10% -> ALERT YOX
# +15% -> ALERT YOX
# +19% -> ALERT YOX
# +20% -> ALERT VAR
# +30% -> ALERT VAR
# +50% -> ALERT VAR

MIN_PRICE_CHANGE_5M = 20.0


# ============================================================
# ALERT RULES
# ============================================================

# Eyni coin yalnız 1 dəfə alert
ONE_ALERT_PER_COIN = True

# Bir scan zamanı maksimum alert
MAX_ALERTS_PER_SCAN = 5


# ============================================================
# MEMORY FILE
# ============================================================

# Railway restart etsə belə alert edilmiş coinləri
# yadda saxlamaq üçün fayl
ALERTED_FILE = "alerted_coins.json"


# ============================================================
# SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "Binance-5M-Volume-Alert/2.0"
})


# ============================================================
# LOAD ALERTED COINS
# ============================================================

def load_alerted_coins():

    try:

        if not os.path.exists(ALERTED_FILE):
            return set()

        with open(
            ALERTED_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

            if isinstance(data, list):

                return set(data)

    except Exception as e:

        print(
            "ALERT MEMORY LOAD ERROR:",
            e
        )

    return set()


# ============================================================
# SAVE ALERTED COINS
# ============================================================

def save_alerted_coins():

    try:

        with open(
            ALERTED_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                sorted(alerted_coins),
                f,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:

        print(
            "ALERT MEMORY SAVE ERROR:",
            e
        )


# ============================================================
# MEMORY
# ============================================================

alerted_coins = load_alerted_coins()

print(
    "ALREADY ALERTED COINS:",
    len(alerted_coins)
)


# ============================================================
# SAFE FLOAT
# ============================================================

def safe_float(
    value,
    default=0.0
):

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

            # Aktiv olmalıdır
            if item.get("status") != "TRADING":
                continue

            # Yalnız USDT
            if item.get("quoteAsset") != "USDT":
                continue

            # Spot
            if item.get(
                "isSpotTradingAllowed",
                True
            ) is False:

                continue

            symbol = item.get(
                "symbol"
            )

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
# GET 5M DATA
# ============================================================

def get_5m_data(symbol):

    url = (
        f"{BINANCE_URL}/api/v3/klines"
    )

    params = {
        "symbol": symbol,
        "interval": "5m",

        # Son 5 candle
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


        # ====================================================
        # ƏVVƏLKİ 3 TAMAMLANMIŞ CANDLE
        # ====================================================

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
        # PREVIOUS VOLUMES
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
# CHECK SIGNAL
# ============================================================

def is_volume_signal(data):

    symbol = data[
        "symbol"
    ]

    volume = data[
        "volume"
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
    # PRICE +20% RULE
    # ========================================================

    if price_change < MIN_PRICE_CHANGE_5M:

        return False


    # ========================================================
    # SIGNAL PASSED
    # ========================================================

    return True


# ============================================================
# SCORE
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

    if multiplier >= 2:
        score += 30

    if multiplier >= 3:
        score += 15

    if multiplier >= 5:
        score += 20

    if multiplier >= 10:
        score += 20


    # ========================================================
    # ABSOLUTE VOLUME
    # ========================================================

    if volume >= 100_000:
        score += 10

    if volume >= 250_000:
        score += 10

    if volume >= 500_000:
        score += 10

    if volume >= 1_000_000:
        score += 10


    # ========================================================
    # PRICE SCORE
    # ========================================================

    if price_change >= 20:
        score += 20

    if price_change >= 30:
        score += 10

    if price_change >= 50:
        score += 10

    if price_change >= 100:
        score += 20


    return score


# ============================================================
# SCAN BINANCE
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
    # SCAN COINS
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


        # ====================================================
        # PRINT ONLY STRONG CANDIDATES
        # ====================================================

        if is_volume_signal(data):

            data["score"] = (
                calculate_score(data)
            )

            candidates.append(
                data
            )

            print(
                "🔥 STRONG CANDIDATE:",
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
    # SORT
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

            "🚨 BINANCE 5M STRONG PUMP ALERT\n\n"

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

            "✅ PRICE FILTER:\n"
            "5M price change ≥ +20%\n\n"

            "⚠️ Bu coin üçün yalnız "
            "1 dəfə alert veriləcək.\n"

            "Sonradan +20%, +50%, +100% "
            "qalxsa ikinci alert gəlməyəcək.\n\n"

            f"🔗 https://www.binance.com/"
            f"en/trade/{symbol}?type=spot"
        )


        # ====================================================
        # SEND
        # ====================================================

        if send_message(
            message
        ):

            # Yaddaşa əlavə et
            alerted_coins.add(
                symbol
            )

            # Fayla yaz
            save_alerted_coins()

            alerts_sent += 1


            print(
                "🚨 ALERT SENT:",
                symbol,
                "| PRICE:",
                f"{price_change:+.2f}%",
                "| VOLUME:",
                f"{multiplier:.2f}x"
            )


    print()
    print(
        "ALERTS SENT:",
        alerts_sent
    )


# ============================================================
# WAIT FOR NEXT 5 MINUTE
# ============================================================

def wait_for_next_5m():

    now = time.time()

    next_run = (
        ((int(now) // CHECK_INTERVAL) + 1)
        * CHECK_INTERVAL
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
    "🟢 BINANCE 5M STRONG MOMENTUM BOT"
)
print()

print(
    "Source: Binance Spot"
)

print(
    "Scan interval: 5 minutes"
)

print(
    "Minimum 5M Volume:",
    f"${MIN_5M_VOLUME:,}"
)

print(
    "Minimum Volume Multiplier:",
    f"{MIN_VOLUME_MULTIPLIER}x"
)

print(
    "Minimum Volume Increase:",
    f"${MIN_VOLUME_INCREASE:,}"
)

print(
    "🔥 MINIMUM PRICE CHANGE:",
    f"+{MIN_PRICE_CHANGE_5M}%"
)

print(
    "One Alert Per Coin: ON"
)

print(
    "Alert Memory:",
    "PERSISTENT"
)

print()


# ============================================================
# TELEGRAM START MESSAGE
# ============================================================

send_message(

    "🟢 BINANCE 5M STRONG MOMENTUM BOT STARTED\n\n"

    "🎯 Məqsəd:\n"
    "Binance Spot-da güclü hərəkət edən "
    "coinləri tapmaq.\n\n"

    "📊 Yoxlama:\n"
    "• Hər 5 dəqiqədən bir\n"
    "• Son tamamlanmış 5M candle\n"
    "• Əvvəlki 3×5M orta volume ilə müqayisə\n\n"

    "🔥 ƏSAS QAYDA:\n"
    "• 5M qiymət artımı minimum +20%\n"
    "• +1%, +5%, +10%, +15% → ALERT YOX\n"
    "• +20% və yuxarı → ALERT MÜMKÜNDÜR\n\n"

    "💰 Volume qaydaları:\n"
    "• 5M Volume ≥ $50K\n"
    "• Volume ≥ 2x əvvəlki 3×5M orta\n"
    "• Volume artımı ≥ $25K\n\n"

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

    wait_for_next_5m()
