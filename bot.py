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

# ------------------------------------------------------------
# TOKEN YAŞI
# 3 gün - 60 gün
# ------------------------------------------------------------

MIN_AGE_HOURS = 3 * 24
MAX_AGE_HOURS = 60 * 24


# ------------------------------------------------------------
# ƏSAS FİLTRLƏR
# ------------------------------------------------------------

MIN_MARKET_CAP = 10_000
MIN_LIQUIDITY = 10_000
MIN_5M_VOLUME = 500


# ------------------------------------------------------------
# MOMENTUM
# ------------------------------------------------------------

# 5 dəqiqəlik qiymət maksimum +50%-ə qədər qəbul edilir.
#
# Məsələn:
# +5%  -> qəbul
# +20% -> qəbul
# +40% -> qəbul
# +50% -> qəbul
# +51% -> artıq gec hesab edilir
# ------------------------------------------------------------

MAX_CURRENT_5M_PRICE = 50


# ------------------------------------------------------------
# BUY PRESSURE
# ------------------------------------------------------------

MIN_BUY_RATIO = 55


# ------------------------------------------------------------
# SCAN
# ------------------------------------------------------------

CHECK_INTERVAL = 30


# ------------------------------------------------------------
# BİR TOKENƏ YALNIZ 1 ALERT
# ------------------------------------------------------------

# Token bir dəfə siqnal aldıqdan sonra:
#
# +20%
# +40%
# +50%
# +100%
#
# olsa belə yenidən alert verməyəcək.
#
# Yalnız bot restart olunanda RAM yaddaşı sıfırlanır.
# ------------------------------------------------------------

alerted_tokens = set()


# ============================================================
# MEMORY
# ============================================================

# Əvvəlki scan məlumatları
previous = {}


# ============================================================
# SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "MemePumpEarlyScannerV5"
})


# ============================================================
# SAFE FUNCTIONS
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
# DEXSCREENER - LATEST PROFILES
# ============================================================

def get_latest_profiles():

    url = (
        "https://api.dexscreener.com/"
        "token-profiles/latest/v1"
    )

    try:

        response = session.get(
            url,
            timeout=20
        )

        response.raise_for_status()

        data = response.json()

        if isinstance(data, list):

            return data

        return []

    except Exception as e:

        print(
            "DEX PROFILE ERROR:",
            e
        )

        return []


# ============================================================
# DEXSCREENER - LATEST BOOSTS
# ============================================================

def get_latest_boosts():

    url = (
        "https://api.dexscreener.com/"
        "token-boosts/latest/v1"
    )

    try:

        response = session.get(
            url,
            timeout=20
        )

        response.raise_for_status()

        data = response.json()

        if isinstance(data, list):

            return data

        return []

    except Exception as e:

        print(
            "DEX BOOST ERROR:",
            e
        )

        return []


# ============================================================
# DEXSCREENER - TOP BOOSTS
# ============================================================

def get_top_boosts():

    url = (
        "https://api.dexscreener.com/"
        "token-boosts/top/v1"
    )

    try:

        response = session.get(
            url,
            timeout=20
        )

        response.raise_for_status()

        data = response.json()

        if isinstance(data, list):

            return data

        return []

    except Exception as e:

        print(
            "DEX TOP BOOST ERROR:",
            e
        )

        return []


# ============================================================
# DEXSCREENER - TOKEN PAIRS
# ============================================================

def get_token_pairs(token_address):

    url = (
        f"https://api.dexscreener.com/"
        f"token-pairs/v1/{CHAIN}/{token_address}"
    )

    try:

        response = session.get(
            url,
            timeout=20
        )

        response.raise_for_status()

        data = response.json()

        if isinstance(data, list):

            return data

        return []

    except Exception as e:

        print(
            "DEX PAIR ERROR:",
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

        age = (
            time.time() * 1000
            - float(created_ms)
        )

        return age / 1000 / 60 / 60

    except Exception:

        return None


# ============================================================
# EXTRACT PAIR
# ============================================================

def extract_pair(pair):

    try:

        # ----------------------------------------------------
        # CHAIN
        # ----------------------------------------------------

        if pair.get("chainId") != CHAIN:

            return None


        # ----------------------------------------------------
        # PAIR ADDRESS
        # ----------------------------------------------------

        pair_address = pair.get(
            "pairAddress"
        )

        if not pair_address:

            return None


        # ----------------------------------------------------
        # TOKEN
        # ----------------------------------------------------

        base = (
            pair.get("baseToken")
            or {}
        )

        token_address = base.get(
            "address",
            ""
        )

        if not token_address:

            return None

        name = base.get(
            "name",
            "UNKNOWN"
        )

        symbol = base.get(
            "symbol",
            "UNKNOWN"
        )


        # ----------------------------------------------------
        # AGE
        # ----------------------------------------------------

        created = pair.get(
            "pairCreatedAt"
        )

        age_hours = calculate_age_hours(
            created
        )

        if age_hours is None:

            return None


        # ----------------------------------------------------
        # 3 - 60 GÜN
        # ----------------------------------------------------

        if age_hours < MIN_AGE_HOURS:

            return None

        if age_hours > MAX_AGE_HOURS:

            return None


        # ----------------------------------------------------
        # LIQUIDITY
        # ----------------------------------------------------

        liquidity_data = (
            pair.get("liquidity")
            or {}
        )

        liquidity = safe_float(
            liquidity_data.get("usd")
        )

        if liquidity < MIN_LIQUIDITY:

            return None


        # ----------------------------------------------------
        # MARKET CAP
        # ----------------------------------------------------

        market_cap = safe_float(
            pair.get("marketCap")
        )

        if market_cap <= 0:

            market_cap = safe_float(
                pair.get("fdv")
            )

        if market_cap < MIN_MARKET_CAP:

            return None


        # ----------------------------------------------------
        # TRANSACTIONS
        # ----------------------------------------------------

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

        total_txns = buys + sells

        buy_ratio = 0

        if total_txns > 0:

            buy_ratio = (
                buys / total_txns
            ) * 100


        # ----------------------------------------------------
        # VOLUME
        # ----------------------------------------------------

        volume_data = (
            pair.get("volume")
            or {}
        )

        volume_5m = safe_float(
            volume_data.get("m5")
        )

        if volume_5m < MIN_5M_VOLUME:

            return None


        # ----------------------------------------------------
        # PRICE CHANGE
        # ----------------------------------------------------

        price_change = (
            pair.get("priceChange")
            or {}
        )

        price_5m = safe_float(
            price_change.get("m5")
        )


        # ----------------------------------------------------
        # ÇOX GECİKMİŞ TOKENİ İSTƏMİRİK
        # ----------------------------------------------------

        if price_5m > MAX_CURRENT_5M_PRICE:

            return None


        # ----------------------------------------------------
        # URL
        # ----------------------------------------------------

        pair_url = pair.get(
            "url"
        )

        if not pair_url:

            pair_url = (
                f"https://dexscreener.com/"
                f"{CHAIN}/{pair_address}"
            )


        # ----------------------------------------------------
        # RESULT
        # ----------------------------------------------------

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
# MOMENTUM SCORE
# ============================================================

def calculate_score(current, old):

    score = 0

    buys = current["buys"]

    sells = current["sells"]

    buy_ratio = current["buy_ratio"]

    volume = current["volume_5m"]

    price = current["price_5m"]


    # ========================================================
    # BUY COUNT
    # ========================================================

    if buys >= 2:

        score += 5

    if buys >= 5:

        score += 5

    if buys >= 10:

        score += 10

    if buys >= 20:

        score += 10


    # ========================================================
    # BUY RATIO
    # ========================================================

    if buy_ratio >= 55:

        score += 5

    if buy_ratio >= 60:

        score += 5

    if buy_ratio >= 70:

        score += 10

    if buy_ratio >= 80:

        score += 10


    # ========================================================
    # YENİ BUY-LAR
    # ========================================================

    if old:

        old_buys = old.get(
            "buys",
            0
        )

        old_volume = old.get(
            "volume_5m",
            0
        )

        buy_increase = (
            buys - old_buys
        )

        volume_increase = (
            volume - old_volume
        )


        if buy_increase >= 1:

            score += 10

        if buy_increase >= 3:

            score += 10

        if buy_increase >= 5:

            score += 10


        # Volume artımı

        if volume_increase > 0:

            score += 5

        if (
            old_volume > 0
            and volume >= old_volume * 1.5
        ):

            score += 10


    # ========================================================
    # VOLUME
    # ========================================================

    if volume >= 500:

        score += 3

    if volume >= 1_000:

        score += 5

    if volume >= 5_000:

        score += 5

    if volume >= 10_000:

        score += 5


    # ========================================================
    # PRICE MOMENTUM
    # ========================================================

    if price >= 0:

        score += 3

    if price >= 5:

        score += 5

    if price >= 10:

        score += 5

    if price >= 20:

        score += 5

    if price >= 30:

        score += 5

    if price >= 40:

        score += 5

    if price >= 50:

        score += 5


    # ========================================================
    # BUY > SELL
    # ========================================================

    if buys > sells:

        score += 5

    if buys >= sells * 2:

        score += 10


    return score


# ============================================================
# ANALYZE
# ============================================================

def analyze(current):

    pair = current["pair"]

    token = current["token"]


    # --------------------------------------------------------
    # TOKEN ARTİQ ALERT ALIBSA
    # --------------------------------------------------------

    if token in alerted_tokens:

        return False


    # --------------------------------------------------------
    # ƏVVƏLKİ SCAN
    # --------------------------------------------------------

    old = previous.get(
        pair
    )

    # İlk dəfə görürüksə,
    # yadda saxla, amma alert vermə.
    if old is None:

        return False


    # --------------------------------------------------------
    # DƏYİŞİKLİK
    # --------------------------------------------------------

    buy_increase = (
        current["buys"]
        - old.get("buys", 0)
    )

    volume_increase = (
        current["volume_5m"]
        - old.get("volume_5m", 0)
    )


    # --------------------------------------------------------
    # MOMENTUM
    # --------------------------------------------------------

    momentum = False


    if buy_increase >= 1:

        momentum = True


    if buy_increase >= 3:

        momentum = True


    if volume_increase > 0:

        momentum = True


    # --------------------------------------------------------
    # BUY PRESSURE
    # --------------------------------------------------------

    buy_pressure = (
        current["buy_ratio"]
        >= MIN_BUY_RATIO
    )


    # --------------------------------------------------------
    # PRICE
    # --------------------------------------------------------

    price_ok = (
        current["price_5m"]
        <= MAX_CURRENT_5M_PRICE
    )


    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    score = calculate_score(
        current,
        old
    )

    current["score"] = score


    # --------------------------------------------------------
    # FINAL SIGNAL
    # --------------------------------------------------------

    if (
        score >= 35
        and momentum
        and buy_pressure
        and price_ok
    ):

        return True


    return False


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
# COLLECT TOKEN ADDRESSES
# ============================================================

def get_token_addresses():

    addresses = set()


    # --------------------------------------------------------
    # PROFILES
    # --------------------------------------------------------

    profiles = get_latest_profiles()

    print(
        "PROFILES:",
        len(profiles)
    )


    for item in profiles:

        if item.get(
            "chainId"
        ) != CHAIN:

            continue

        address = item.get(
            "tokenAddress"
        )

        if address:

            addresses.add(
                address
            )


    # --------------------------------------------------------
    # LATEST BOOSTS
    # --------------------------------------------------------

    latest_boosts = get_latest_boosts()

    print(
        "LATEST BOOSTS:",
        len(latest_boosts)
    )


    for item in latest_boosts:

        if item.get(
            "chainId"
        ) != CHAIN:

            continue

        address = item.get(
            "tokenAddress"
        )

        if address:

            addresses.add(
                address
            )


    # --------------------------------------------------------
    # TOP BOOSTS
    # --------------------------------------------------------

    top_boosts = get_top_boosts()

    print(
        "TOP BOOSTS:",
        len(top_boosts)
    )


    for item in top_boosts:

        if item.get(
            "chainId"
        ) != CHAIN:

            continue

        address = item.get(
            "tokenAddress"
        )

        if address:

            addresses.add(
                address
            )


    return addresses


# ============================================================
# SCAN DEXSCREENER
# ============================================================

def scan_dex():

    results = {}

    token_addresses = (
        get_token_addresses()
    )


    print(
        "DISCOVERED TOKENS:",
        len(token_addresses)
    )


    for token_address in token_addresses:

        pairs = get_token_pairs(
            token_address
        )


        for pair in pairs:

            result = extract_pair(
                pair
            )

            if result is None:

                continue


            pair_address = result[
                "pair"
            ]


            # Eyni pair-i iki dəfə əlavə etmə

            results[
                pair_address
            ] = result


    return results


# ============================================================
# MAIN SCAN
# ============================================================

def scan():

    print()
    print(
        "=================================================="
    )
    print(
        "🔎 NEW SCAN"
    )
    print(
        "=================================================="
    )


    # --------------------------------------------------------
    # DEXSCREENER
    # --------------------------------------------------------

    try:

        results = scan_dex()

    except Exception as e:

        print(
            "DEX SCAN ERROR:",
            e
        )

        return


    print()
    print(
        "CANDIDATES:",
        len(results)
    )

    print(
        "ALREADY ALERTED TOKENS:",
        len(alerted_tokens)
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

            alerts.append(
                current
            )


        # Sonrakı scan üçün yadda saxla

        remember(
            current
        )


    # ========================================================
    # SCORE
    # ========================================================

    alerts.sort(
        key=lambda x: (
            x.get(
                "score",
                0
            ),

            x.get(
                "buy_ratio",
                0
            ),

            x.get(
                "buys",
                0
            ),

            x.get(
                "volume_5m",
                0
            )
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

        token = result[
            "token"
        ]


        # ----------------------------------------------------
        # TƏHLÜKƏSİZLİK
        # Eyni tokeni bu scan zamanı da təkrar göndərmə
        # ----------------------------------------------------

        if token in alerted_tokens:

            continue


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

            f"🔴 5M Sells: "
            f"{result['sells']}\n"

            f"📊 Buy ratio: "
            f"{result['buy_ratio']:.1f}%\n"

            f"💵 5M Volume: "
            f"${result['volume_5m']:,.0f}\n"

            f"📈 5M Price: "
            f"{result['price_5m']:+.2f}%\n\n"

            f"🔥 Early Score: "
            f"{result['score']}\n\n"

            f"🔎 Source: DEX Screener\n\n"

            f"🔗 {result['url']}"
        )


        if send_message(
            message
        ):

            # ------------------------------------------------
            # ƏN VACİB:
            # TOKENİ ALERT EDİLMİŞ KİMİ YADDA SAXLA
            # ------------------------------------------------

            alerted_tokens.add(
                token
            )


            sent += 1


            print(
                "🚨 ALERT:",
                result["symbol"],

                "| SCORE:",
                result["score"],

                "| PRICE:",
                round(
                    result["price_5m"],
                    2
                ),

                "| BUYS:",
                result["buys"],

                "| BUY RATIO:",
                round(
                    result["buy_ratio"],
                    1
                )
            )


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
    "Chain:",
    CHAIN
)


print(
    "Pair age: 3 - 60 days"
)


print(
    "Minimum Market Cap:",
    f"${MIN_MARKET_CAP:,}"
)


print(
    "Minimum Liquidity:",
    f"${MIN_LIQUIDITY:,}"
)


print(
    "Minimum 5M Volume:",
    f"${MIN_5M_VOLUME:,}"
)


print(
    "Minimum Buy Ratio:",
    f"{MIN_BUY_RATIO}%"
)


print(
    "Maximum 5M Price:",
    f"+{MAX_CURRENT_5M_PRICE}%"
)


print(
    "Scan interval:",
    f"{CHECK_INTERVAL} seconds"
)


print(
    "One alert per token: ON"
)


print()


# ============================================================
# TELEGRAM START MESSAGE
# ============================================================

send_message(

    "🟢 MEME PUMP EARLY SCANNER V5 STARTED\n\n"

    "🔎 Source:\n"
    "• DEX Screener\n\n"

    "⚙️ Filtrlər:\n"
    "• Solana\n"
    "• Pair age: 3 - 60 gün\n"
    "• Market Cap: ≥ $10K\n"
    "• Liquidity: ≥ $10K\n"
    "• 5M Volume: ≥ $500\n"
    "• Buy ratio: ≥ 55%\n"
    "• 5M Price: maksimum +50%\n\n"

    "🔥 Momentum:\n"
    "• Yeni buy artımı\n"
    "• Volume artımı\n"
    "• Buy pressure\n"
    "• Momentum score\n\n"

    "🚨 Eyni tokenə yalnız 1 dəfə alert.\n"
    "Token daha sonra +20%, +40%, +50% "
    "və ya daha çox qalxsa belə ikinci alert gəlməyəcək.\n\n"

    "⏱ Scan: hər 30 saniyə"
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
