import os
import time
import requests


# ============================================================
# ENV
# ============================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]


# ============================================================
# SETTINGS
# ============================================================

CHAIN = "solana"

# ============================================================
# PAIR AGE
#
# Yalnız 3 gün - 60 gün arası tokenlər
# ============================================================

MIN_AGE_HOURS = 3 * 24
MAX_AGE_HOURS = 60 * 24


# ============================================================
# ƏSAS FİLTRLƏR
# ============================================================

MIN_MARKET_CAP = 10_000
MIN_LIQUIDITY = 10_000


# ============================================================
# MOMENTUM
#
# +50% olmuş tokeni avtomatik silmirik.
# Məqsəd:
# "50% qalxdı -> bundan sonra yenə güclü alış başlayırsa"
# onu tutmaqdır.
# ============================================================

MAX_5M_PRICE_CHANGE = 100


# Minimum 5M volume
MIN_5M_VOLUME = 500


# ============================================================
# SCAN
# ============================================================

CHECK_INTERVAL = 30


# ============================================================
# ALERT
# ============================================================

ALERT_COOLDOWN_HOURS = 6

MAX_ALERTS_PER_SCAN = 5


# ============================================================
# MEMORY
# ============================================================

previous = {}

alerted = {}

session = requests.Session()

session.headers.update({
    "User-Agent": "MemePumpEarlyScanner/5.0"
})


# ============================================================
# SAFE
# ============================================================

def safe_float(value, default=0):

    try:
        return float(value or default)

    except Exception:
        return default


def safe_int(value, default=0):

    try:
        return int(float(value or default))

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
            timeout=20
        )

        print(
            "TELEGRAM STATUS:",
            response.status_code
        )

        if not response.ok:

            print(
                "TELEGRAM RESPONSE:",
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
# DEXSCREENER DISCOVERY
# ============================================================

def get_latest_profiles():

    url = (
        "https://api.dexscreener.com/"
        "token-profiles/latest/v1"
    )

    try:

        r = session.get(
            url,
            timeout=20
        )

        r.raise_for_status()

        data = r.json()

        if isinstance(data, list):

            return data

    except Exception as e:

        print(
            "PROFILE ERROR:",
            e
        )

    return []


def get_latest_boosts():

    url = (
        "https://api.dexscreener.com/"
        "token-boosts/latest/v1"
    )

    try:

        r = session.get(
            url,
            timeout=20
        )

        r.raise_for_status()

        data = r.json()

        if isinstance(data, list):

            return data

    except Exception as e:

        print(
            "BOOST ERROR:",
            e
        )

    return []


def get_top_boosts():

    url = (
        "https://api.dexscreener.com/"
        "token-boosts/top/v1"
    )

    try:

        r = session.get(
            url,
            timeout=20
        )

        r.raise_for_status()

        data = r.json()

        if isinstance(data, list):

            return data

    except Exception as e:

        print(
            "TOP BOOST ERROR:",
            e
        )

    return []


# ============================================================
# DISCOVER TOKEN ADDRESSES
# ============================================================

def discover_tokens():

    tokens = set()

    sources = [
        ("profiles", get_latest_profiles()),
        ("latest boosts", get_latest_boosts()),
        ("top boosts", get_top_boosts())
    ]

    for source_name, items in sources:

        print(
            source_name.upper(),
            ":",
            len(items)
        )

        for item in items:

            if item.get(
                "chainId"
            ) != CHAIN:

                continue

            address = item.get(
                "tokenAddress"
            )

            if address:

                tokens.add(address)

    print(
        "DISCOVERED TOKENS:",
        len(tokens)
    )

    return tokens


# ============================================================
# TOKEN PAIRS
# ============================================================

def get_token_pairs(token_address):

    url = (
        f"https://api.dexscreener.com/"
        f"token-pairs/v1/{CHAIN}/{token_address}"
    )

    try:

        r = session.get(
            url,
            timeout=20
        )

        r.raise_for_status()

        data = r.json()

        if isinstance(data, list):

            return data

    except Exception as e:

        print(
            "PAIR ERROR:",
            e
        )

    return []


# ============================================================
# AGE
# ============================================================

def calculate_age_hours(created_ms):

    if not created_ms:

        return None

    try:

        return (
            time.time() * 1000
            - float(created_ms)
        ) / 1000 / 60 / 60

    except Exception:

        return None


# ============================================================
# EXTRACT PAIR
# ============================================================

def extract_pair(pair):

    try:

        if pair.get(
            "chainId"
        ) != CHAIN:

            return None


        pair_address = pair.get(
            "pairAddress"
        )

        if not pair_address:

            return None


        # ====================================================
        # AGE
        # ====================================================

        age_hours = calculate_age_hours(
            pair.get("pairCreatedAt")
        )

        if age_hours is None:

            return None

        if age_hours < MIN_AGE_HOURS:

            return None

        if age_hours > MAX_AGE_HOURS:

            return None


        # ====================================================
        # LIQUIDITY
        # ====================================================

        liquidity_data = (
            pair.get("liquidity")
            or {}
        )

        liquidity = safe_float(
            liquidity_data.get("usd")
        )

        if liquidity < MIN_LIQUIDITY:

            return None


        # ====================================================
        # MARKET CAP
        # ====================================================

        market_cap = safe_float(
            pair.get("marketCap")
        )

        if market_cap <= 0:

            market_cap = safe_float(
                pair.get("fdv")
            )

        if market_cap < MIN_MARKET_CAP:

            return None


        # ====================================================
        # TRANSACTIONS
        # ====================================================

        txns = (
            pair.get("txns")
            or {}
        )

        m5 = (
            txns.get("m5")
            or {}
        )

        buys = safe_int(
            m5.get("buys")
        )

        sells = safe_int(
            m5.get("sells")
        )

        total = buys + sells

        buy_ratio = 0

        if total > 0:

            buy_ratio = (
                buys / total
            ) * 100


        # ====================================================
        # VOLUME
        # ====================================================

        volume_data = (
            pair.get("volume")
            or {}
        )

        volume_5m = safe_float(
            volume_data.get("m5")
        )

        if volume_5m < MIN_5M_VOLUME:

            return None


        # ====================================================
        # PRICE
        # ====================================================

        price_change = (
            pair.get("priceChange")
            or {}
        )

        price_5m = safe_float(
            price_change.get("m5")
        )

        if price_5m > MAX_5M_PRICE_CHANGE:

            return None


        # ====================================================
        # TOKEN
        # ====================================================

        base = (
            pair.get("baseToken")
            or {}
        )

        name = base.get(
            "name",
            "UNKNOWN"
        )

        symbol = base.get(
            "symbol",
            "UNKNOWN"
        )

        token_address = base.get(
            "address",
            ""
        )


        # ====================================================
        # URL
        # ====================================================

        pair_url = pair.get(
            "url"
        )

        if not pair_url:

            pair_url = (
                "https://dexscreener.com/"
                f"{CHAIN}/{pair_address}"
            )


        return {

            "pair": pair_address,

            "token": token_address,

            "name": name,

            "symbol": symbol,

            "age_hours": age_hours,

            "market_cap": market_cap,

            "liquidity": liquidity,

            "buys": buys,

            "sells": sells,

            "buy_ratio": buy_ratio,

            "volume_5m": volume_5m,

            "price_5m": price_5m,

            "url": pair_url
        }


    except Exception as e:

        print(
            "EXTRACT ERROR:",
            e
        )

        return None


# ============================================================
# MOMENTUM ANALYSIS
# ============================================================

def analyze(current):

    pair = current["pair"]

    old = previous.get(
        pair
    )

    # İlk dəfə görürüksə:
    # yadda saxla, amma alert vermə.
    if old is None:

        return False


    buys = current["buys"]

    old_buys = old.get(
        "buys",
        0
    )

    volume = current["volume_5m"]

    old_volume = old.get(
        "volume_5m",
        0
    )

    buy_ratio = current["buy_ratio"]

    old_buy_ratio = old.get(
        "buy_ratio",
        0
    )

    price = current["price_5m"]


    # ========================================================
    # DƏYİŞİKLİKLƏR
    # ========================================================

    buy_increase = (
        buys - old_buys
    )

    volume_increase = (
        volume - old_volume
    )

    buy_ratio_increase = (
        buy_ratio - old_buy_ratio
    )


    # ========================================================
    # SCORE
    # ========================================================

    score = 0


    # --------------------------------------------------------
    # BUY SAYI
    # --------------------------------------------------------

    if buy_increase >= 1:

        score += 20

    if buy_increase >= 3:

        score += 15

    if buy_increase >= 5:

        score += 15

    if buy_increase >= 10:

        score += 20


    # --------------------------------------------------------
    # BUY RATIO
    # --------------------------------------------------------

    if buy_ratio >= 55:

        score += 10

    if buy_ratio >= 65:

        score += 10

    if buy_ratio >= 75:

        score += 10


    # --------------------------------------------------------
    # BUY PRESSURE ARTIMI
    # --------------------------------------------------------

    if buy_ratio_increase >= 5:

        score += 10

    if buy_ratio_increase >= 10:

        score += 10


    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    if volume_increase > 0:

        score += 10

    if (
        old_volume > 0
        and volume >= old_volume * 1.25
    ):

        score += 10

    if (
        old_volume > 0
        and volume >= old_volume * 1.50
    ):

        score += 15


    # --------------------------------------------------------
    # BUY > SELL
    # --------------------------------------------------------

    if buys > current["sells"]:

        score += 10

    if buys >= current["sells"] * 2:

        score += 15


    current["score"] = score

    current["buy_increase"] = buy_increase

    current["volume_increase"] = volume_increase

    current["buy_ratio_increase"] = (
        buy_ratio_increase
    )


    # ========================================================
    # FINAL SIGNAL
    #
    # Əsas məqsəd:
    # token artıq 30-50% qalxmış olsa belə,
    # yeni alış dalğası başlayırsa xəbər vermək.
    # ========================================================

    strong_buy_activity = (
        buy_increase >= 2
    )

    strong_volume = (
        volume_increase > 0
    )

    healthy_buy_pressure = (
        buy_ratio >= 55
    )

    # Score aşağı hədd:
    # 45
    #
    # Çox sərt deyil, amma hər kiçik hərəkətə də
    # siqnal vermir.
    if (
        score >= 45
        and (
            strong_buy_activity
            or strong_volume
        )
        and healthy_buy_pressure
    ):

        return True


    return False


# ============================================================
# COOLDOWN
# ============================================================

def is_on_cooldown(pair):

    last = alerted.get(
        pair
    )

    if not last:

        return False

    elapsed = (
        time.time()
        - last
    )

    return (
        elapsed
        < ALERT_COOLDOWN_HOURS * 3600
    )


# ============================================================
# MEMORY
# ============================================================

def remember(current):

    previous[
        current["pair"]
    ] = {

        "buys":
            current["buys"],

        "sells":
            current["sells"],

        "volume_5m":
            current["volume_5m"],

        "price_5m":
            current["price_5m"],

        "buy_ratio":
            current["buy_ratio"],

        "timestamp":
            time.time()
    }


# ============================================================
# SCAN
# ============================================================

def scan():

    print()
    print(
        "=========================================="
    )

    print(
        "🔎 NEW SCAN"
    )

    print(
        "=========================================="
    )


    # ========================================================
    # DISCOVERY
    # ========================================================

    token_addresses = discover_tokens()


    results = {}


    # ========================================================
    # GET PAIRS
    # ========================================================

    for token_address in token_addresses:

        pairs = get_token_pairs(
            token_address
        )

        for pair in pairs:

            result = extract_pair(
                pair
            )

            if result:

                results[
                    result["pair"]
                ] = result


    print()
    print(
        "CANDIDATES:",
        len(results)
    )


    # ========================================================
    # ANALYZE
    # ========================================================

    alerts = []


    for current in results.values():

        should_alert = analyze(
            current
        )


        if should_alert:

            pair = current["pair"]

            if not is_on_cooldown(
                pair
            ):

                alerts.append(
                    current
                )


        # yadda saxla
        remember(
            current
        )


    # ========================================================
    # SORT
    # ========================================================

    alerts.sort(
        key=lambda x: (
            x.get("score", 0),
            x.get("buy_increase", 0),
            x.get("volume_increase", 0),
            x.get("buy_ratio", 0)
        ),
        reverse=True
    )


    print(
        "EARLY SIGNALS:",
        len(alerts)
    )


    # ========================================================
    # TELEGRAM
    # ========================================================

    sent = 0


    for result in alerts:

        message = (
            "🚨 EARLY MOMENTUM ALERT\n\n"

            f"🪙 {result['name']} "
            f"({result['symbol']})\n\n"

            f"⏱ Pair age: "
            f"{result['age_hours']:.1f} saat\n"

            f"💰 Market Cap: "
            f"${result['market_cap']:,.0f}\n"

            f"💧 Liquidity: "
            f"${result['liquidity']:,.0f}\n\n"

            f"🟢 5M Buys: "
            f"{result['buys']}\n"

            f"📈 Buy artımı: "
            f"+{result['buy_increase']}\n"

            f"🔴 5M Sells: "
            f"{result['sells']}\n"

            f"📊 Buy ratio: "
            f"{result['buy_ratio']:.1f}%\n"

            f"🔥 Buy pressure artımı: "
            f"{result['buy_ratio_increase']:+.1f}%\n\n"

            f"💵 5M Volume: "
            f"${result['volume_5m']:,.0f}\n"

            f"📊 Volume artımı: "
            f"${result['volume_increase']:,.0f}\n"

            f"📈 5M Price: "
            f"{result['price_5m']:+.2f}%\n\n"

            f"🔥 Early Score: "
            f"{result['score']}\n\n"

            "🔎 Source: DEX Screener\n\n"

            f"🔗 {result['url']}"
        )


        if send_message(
            message
        ):

            alerted[
                result["pair"]
            ] = time.time()

            sent += 1


            print(
                "🚨 ALERT:",
                result["symbol"],
                "| SCORE:",
                result["score"],
                "| BUY +:",
                result["buy_increase"],
                "| VOLUME +:",
                round(
                    result["volume_increase"],
                    2
                )
            )


        if sent >= MAX_ALERTS_PER_SCAN:

            break


    print(
        "ALERTS SENT:",
        sent
    )


# ============================================================
# START
# ============================================================

print()
print(
    "🟢 MEME PUMP EARLY SCANNER V5"
)
print()

print(
    "Source: DEX Screener"
)

print(
    "Pair age: 3 - 60 gün"
)

print(
    "Minimum Market Cap: $10,000"
)

print(
    "Minimum Liquidity: $10,000"
)

print(
    "Minimum 5M Volume: $500"
)

print(
    "Momentum: BUY + VOLUME + BUY PRESSURE"
)

print(
    "5M Price limit: +100%"
)

print(
    "Scan interval: 30 seconds"
)

print(
    "Jupiter: OFF"
)

print()


# ============================================================
# TELEGRAM START MESSAGE
# ============================================================

send_message(
    "🟢 MEME PUMP EARLY SCANNER V5 STARTED\n\n"

    "🎯 Məqsəd:\n"
    "3-60 günlük tokenlərdə yeni momentum "
    "başlayanda erkən xəbər vermək.\n\n"

    "🔎 Source:\n"
    "• DEX Screener\n\n"

    "⚙️ Filtrlər:\n"
    "• Pair age: 3 - 60 gün\n"
    "• Market Cap: ≥ $10K\n"
    "• Liquidity: ≥ $10K\n"
    "• 5M Volume: ≥ $500\n\n"

    "🔥 Siqnal:\n"
    "• Buy artımı\n"
    "• Buy pressure\n"
    "• Volume artımı\n\n"

    "📈 Token artıq yüksəlibsə belə, "
    "yeni alış dalğası başlayarsa siqnal verilə bilər.\n\n"

    "⏱ Scan: 30 saniyə\n\n"

    "Jupiter: OFF"
)


# ============================================================
# LOOP
# ============================================================

while True:

    try:

        scan()

    except Exception as e:

        print(
            "MAIN LOOP ERROR:",
            e
        )

    print()
    print(
        "Next scan in",
        CHECK_INTERVAL,
        "seconds..."
    )
    print()

    time.sleep(
        CHECK_INTERVAL
    )
