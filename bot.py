import os
import time
import requests


# ============================================================
# ENV VARIABLES
# ============================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]


# ============================================================
# MAIN SETTINGS
# ============================================================

CHAIN = "solana"

# ============================================================
# TOKEN AGE
# 72 saat = 3 gün
# 3000 saat = 125 gün
# ============================================================

MIN_AGE_HOURS = 72
MAX_AGE_HOURS = 3000


# ============================================================
# MARKET FILTERS
# ============================================================

MIN_LIQUIDITY = 10_000

MIN_MARKET_CAP = 10_000
MAX_MARKET_CAP = 1_000_000

MIN_5M_VOLUME = 5_000

# 5 dəqiqədə minimum qiymət artımı
MIN_5M_PRICE_CHANGE = 3.0

# Minimum 5 dəqiqəlik əməliyyat sayı
MIN_5M_TXNS = 3

# Buy/Sell balansı
# Məsələn 60% buy -> alış üstünlüyü
MIN_BUY_RATIO = 0.55


# ============================================================
# SCANNER
# ============================================================

CHECK_INTERVAL = 60

REQUEST_TIMEOUT = 20

# Eyni pair ikinci dəfə göndərilməsin
seen_pairs = set()

# Eyni token müxtəlif pair-lərlə təkrar gələndə
seen_tokens = set()


# ============================================================
# DEXSCREENER API
# ============================================================

BASE_URL = "https://api.dexscreener.com"


session = requests.Session()

session.headers.update({
    "User-Agent": "MemePumpAlertBot/1.0"
})


# ============================================================
# SAFE NUMBER
# ============================================================

def safe_float(value, default=0):

    try:
        if value is None:
            return default

        return float(value)

    except Exception:
        return default


def safe_int(value, default=0):

    try:
        if value is None:
            return default

        return int(value)

    except Exception:
        return default


# ============================================================
# TELEGRAM
# ============================================================

def send_message(text):

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

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
            "TELEGRAM:",
            response.status_code
        )

        response.raise_for_status()

        return True

    except Exception as e:

        print(
            "TELEGRAM ERROR:",
            e
        )

        return False


# ============================================================
# GENERIC GET
# ============================================================

def api_get(url, params=None):

    try:

        response = session.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        return response.json()

    except Exception as e:

        print(
            "API ERROR:",
            url,
            e
        )

        return None


# ============================================================
# LATEST TOKEN PROFILES
# ============================================================

def get_latest_profiles():

    url = (
        f"{BASE_URL}/token-profiles/latest/v1"
    )

    data = api_get(url)

    if isinstance(data, list):
        return data

    return []


# ============================================================
# LATEST BOOSTED TOKENS
# ============================================================

def get_latest_boosts():

    url = (
        f"{BASE_URL}/token-boosts/latest/v1"
    )

    data = api_get(url)

    if isinstance(data, list):
        return data

    return []


# ============================================================
# TOP BOOSTED TOKENS
# ============================================================

def get_top_boosts():

    url = (
        f"{BASE_URL}/token-boosts/top/v1"
    )

    data = api_get(url)

    if isinstance(data, list):
        return data

    return []


# ============================================================
# TOKEN PAIRS
# ============================================================

def get_token_pairs(token_address):

    url = (
        f"{BASE_URL}/token-pairs/v1/"
        f"{CHAIN}/{token_address}"
    )

    data = api_get(url)

    if isinstance(data, list):
        return data

    return []


# ============================================================
# SEARCH PAIRS
# ============================================================

def search_pairs(query):

    url = (
        f"{BASE_URL}/latest/dex/search"
    )

    data = api_get(
        url,
        params={
            "q": query
        }
    )

    if not isinstance(data, dict):
        return []

    pairs = data.get("pairs")

    if isinstance(pairs, list):
        return pairs

    return []


# ============================================================
# COLLECT CANDIDATES
# ============================================================

def collect_candidates():

    candidates = {}

    print()
    print("========================================")
    print("COLLECTING CANDIDATES")
    print("========================================")

    # --------------------------------------------------------
    # 1. Latest profiles
    # --------------------------------------------------------

    profiles = get_latest_profiles()

    print(
        "Latest profiles:",
        len(profiles)
    )

    for item in profiles:

        if item.get("chainId") != CHAIN:
            continue

        address = item.get("tokenAddress")

        if address:
            candidates[address] = True


    # --------------------------------------------------------
    # 2. Latest boosts
    # --------------------------------------------------------

    boosts = get_latest_boosts()

    print(
        "Latest boosts:",
        len(boosts)
    )

    for item in boosts:

        if item.get("chainId") != CHAIN:
            continue

        address = item.get("tokenAddress")

        if address:
            candidates[address] = True


    # --------------------------------------------------------
    # 3. Top boosts
    # --------------------------------------------------------

    top_boosts = get_top_boosts()

    print(
        "Top boosts:",
        len(top_boosts)
    )

    for item in top_boosts:

        if item.get("chainId") != CHAIN:
            continue

        address = item.get("tokenAddress")

        if address:
            candidates[address] = True


    # --------------------------------------------------------
    # 4. Search
    #
    # Search endpoint geniş namizəd hovuzu yaradır.
    # --------------------------------------------------------

    search_queries = [
        "SOL",
        "USDC",
        "USDT",
        "meme",
        "pump",
        "dog",
        "cat",
        "frog",
        "pepe",
        "ai",
        "elon",
        "trump"
    ]

    for query in search_queries:

        print(
            "Searching:",
            query
        )

        pairs = search_pairs(query)

        for pair in pairs:

            if pair.get("chainId") != CHAIN:
                continue

            base_token = (
                pair.get("baseToken")
                or {}
            )

            address = base_token.get(
                "address"
            )

            if address:
                candidates[address] = True

        # API-yə lazımsız yük verməmək
        time.sleep(0.15)


    print()
    print(
        "UNIQUE TOKEN CANDIDATES:",
        len(candidates)
    )

    return list(candidates.keys())


# ============================================================
# AGE
# ============================================================

def get_age_hours(pair):

    created = pair.get(
        "pairCreatedAt"
    )

    if not created:
        return None

    now_ms = time.time() * 1000

    age_hours = (
        (now_ms - created)
        / 1000
        / 60
        / 60
    )

    return age_hours


# ============================================================
# CHECK PAIR
# ============================================================

def check_pair(pair):

    # --------------------------------------------------------
    # CHAIN
    # --------------------------------------------------------

    if pair.get("chainId") != CHAIN:
        return


    # --------------------------------------------------------
    # PAIR ADDRESS
    # --------------------------------------------------------

    pair_address = pair.get(
        "pairAddress"
    )

    if not pair_address:
        return


    # --------------------------------------------------------
    # DUPLICATE
    # --------------------------------------------------------

    if pair_address in seen_pairs:
        return


    # --------------------------------------------------------
    # AGE
    # --------------------------------------------------------

    age_hours = get_age_hours(pair)

    if age_hours is None:

        print(
            "SKIP:",
            pair_address,
            "NO AGE"
        )

        return


    if age_hours < MIN_AGE_HOURS:

        print(
            "SKIP:",
            pair_address,
            "AGE:",
            round(age_hours, 1),
            "hours -> TOO NEW"
        )

        return


    if age_hours > MAX_AGE_HOURS:

        print(
            "SKIP:",
            pair_address,
            "AGE:",
            round(age_hours, 1),
            "hours -> TOO OLD"
        )

        return


    # --------------------------------------------------------
    # LIQUIDITY
    # --------------------------------------------------------

    liquidity_data = (
        pair.get("liquidity")
        or {}
    )

    liquidity = safe_float(
        liquidity_data.get("usd")
    )

    if liquidity < MIN_LIQUIDITY:

        print(
            "SKIP:",
            pair_address,
            "LIQ:",
            liquidity
        )

        return


    # --------------------------------------------------------
    # MARKET CAP
    # --------------------------------------------------------

    market_cap = safe_float(
        pair.get("marketCap")
    )

    if market_cap <= 0:

        market_cap = safe_float(
            pair.get("fdv")
        )


    if market_cap < MIN_MARKET_CAP:

        print(
            "SKIP:",
            pair_address,
            "MC:",
            market_cap
        )

        return


    if market_cap > MAX_MARKET_CAP:

        print(
            "SKIP:",
            pair_address,
            "MC:",
            market_cap
        )

        return


    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    volume = (
        pair.get("volume")
        or {}
    )

    volume_5m = safe_float(
        volume.get("m5")
    )

    if volume_5m < MIN_5M_VOLUME:

        print(
            "SKIP:",
            pair_address,
            "5M VOL:",
            volume_5m
        )

        return


    # --------------------------------------------------------
    # PRICE CHANGE
    # --------------------------------------------------------

    price_change = (
        pair.get("priceChange")
        or {}
    )

    price_change_5m = safe_float(
        price_change.get("m5")
    )

    if price_change_5m < MIN_5M_PRICE_CHANGE:

        print(
            "SKIP:",
            pair_address,
            "5M:",
            price_change_5m,
            "%"
        )

        return


    # --------------------------------------------------------
    # TRANSACTIONS
    # --------------------------------------------------------

    txns = (
        pair.get("txns")
        or {}
    )

    txns_5m = (
        txns.get("m5")
        or {}
    )

    buys_5m = safe_int(
        txns_5m.get("buys")
    )

    sells_5m = safe_int(
        txns_5m.get("sells")
    )

    total_txns = (
        buys_5m +
        sells_5m
    )

    if total_txns < MIN_5M_TXNS:

        print(
            "SKIP:",
            pair_address,
            "TXNS:",
            total_txns
        )

        return


    # --------------------------------------------------------
    # BUY RATIO
    # --------------------------------------------------------

    if total_txns > 0:

        buy_ratio = (
            buys_5m /
            total_txns
        )

    else:

        buy_ratio = 0


    if buy_ratio < MIN_BUY_RATIO:

        print(
            "SKIP:",
            pair_address,
            "BUY RATIO:",
            round(
                buy_ratio * 100,
                1
            ),
            "%"
        )

        return


    # --------------------------------------------------------
    # TOKEN INFO
    # --------------------------------------------------------

    base_token = (
        pair.get("baseToken")
        or {}
    )

    symbol = base_token.get(
        "symbol",
        "UNKNOWN"
    )

    name = base_token.get(
        "name",
        "UNKNOWN"
    )

    token_address = base_token.get(
        "address"
    )


    # --------------------------------------------------------
    # PRICE
    # --------------------------------------------------------

    price_usd = pair.get(
        "priceUsd"
    )

    if not price_usd:
        price_usd = "N/A"


    # --------------------------------------------------------
    # 1H PRICE CHANGE
    # --------------------------------------------------------

    price_change_1h = safe_float(
        price_change.get("h1")
    )


    # --------------------------------------------------------
    # 24H PRICE CHANGE
    # --------------------------------------------------------

    price_change_24h = safe_float(
        price_change.get("h24")
    )


    # --------------------------------------------------------
    # DEX
    # --------------------------------------------------------

    dex_id = pair.get(
        "dexId",
        "unknown"
    )


    # --------------------------------------------------------
    # URL
    # --------------------------------------------------------

    pair_url = pair.get(
        "url"
    )

    if not pair_url:

        pair_url = (
            "https://dexscreener.com/"
            f"{CHAIN}/{pair_address}"
        )


    # --------------------------------------------------------
    # BOOST
    # --------------------------------------------------------

    boosts = (
        pair.get("boosts")
        or {}
    )

    boost_active = safe_int(
        boosts.get("active")
    )


    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    score = 0

    # 5M momentum
    if price_change_5m >= 5:
        score += 1

    if price_change_5m >= 10:
        score += 1

    if price_change_5m >= 20:
        score += 1

    # Buy pressure
    if buy_ratio >= 0.60:
        score += 1

    if buy_ratio >= 0.70:
        score += 1

    # Volume / liquidity
    if volume_5m >= 10_000:
        score += 1

    if volume_5m >= 25_000:
        score += 1

    # 1h momentum
    if price_change_1h > 10:
        score += 1

    # Boost
    if boost_active > 0:
        score += 1


    # --------------------------------------------------------
    # MARK AS SEEN
    # --------------------------------------------------------

    seen_pairs.add(pair_address)


    # --------------------------------------------------------
    # TELEGRAM ALERT
    # --------------------------------------------------------

    message = (
        "🚨 MEME PUMP ALERT\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"🪙 {name}\n"
        f"🔤 ${symbol}\n\n"

        f"⭐ Score: {score}/10\n\n"

        f"⏱ Age: {age_hours:.1f} saat\n"

        f"📈 5M: "
        f"{price_change_5m:+.2f}%\n"

        f"📊 1H: "
        f"{price_change_1h:+.2f}%\n"

        f"📅 24H: "
        f"{price_change_24h:+.2f}%\n\n"

        f"💰 MC: "
        f"${market_cap:,.0f}\n"

        f"💧 Liquidity: "
        f"${liquidity:,.0f}\n"

        f"📊 5M Volume: "
        f"${volume_5m:,.0f}\n\n"

        f"🟢 Buys 5M: {buys_5m}\n"
        f"🔴 Sells 5M: {sells_5m}\n"

        f"⚖️ Buy ratio: "
        f"{buy_ratio * 100:.1f}%\n\n"

        f"🏦 DEX: {dex_id}\n"

        f"💵 Price: ${price_usd}\n\n"

        f"🔗 {pair_url}\n\n"

        f"🧾 Contract:\n"
        f"{token_address}"
    )


    if send_message(message):

        print()
        print("========================================")
        print("🚨 ALERT SENT")
        print("TOKEN:", symbol)
        print("AGE:", round(age_hours, 1), "hours")
        print("5M:", price_change_5m, "%")
        print("1H:", price_change_1h, "%")
        print("MC:", market_cap)
        print("LIQ:", liquidity)
        print("5M VOL:", volume_5m)
        print("BUYS:", buys_5m)
        print("SELLS:", sells_5m)
        print("BUY RATIO:", round(buy_ratio * 100, 1), "%")
        print("SCORE:", score)
        print("========================================")
        print()


# ============================================================
# CHECK ALL
# ============================================================

def check():

    token_addresses = (
        collect_candidates()
    )

    print()
    print(
        "Checking",
        len(token_addresses),
        "tokens..."
    )
    print()

    checked_pairs = set()

    for token_address in token_addresses:

        try:

            pairs = get_token_pairs(
                token_address
            )

            for pair in pairs:

                pair_address = pair.get(
                    "pairAddress"
                )

                if not pair_address:
                    continue

                if pair_address in checked_pairs:
                    continue

                checked_pairs.add(
                    pair_address
                )

                try:

                    check_pair(pair)

                except Exception as e:

                    print(
                        "PAIR CHECK ERROR:",
                        e
                    )

            # Bir az fasilə
            time.sleep(0.05)

        except Exception as e:

            print(
                "TOKEN ERROR:",
                token_address,
                e
            )


# ============================================================
# START
# ============================================================

print()
print("🟢 MEME PUMP ALERT STARTED")
print()
print("Chain:", CHAIN)
print(
    "Age:",
    MIN_AGE_HOURS,
    "-",
    MAX_AGE_HOURS,
    "hours"
)
print(
    "Liquidity >= $",
    f"{MIN_LIQUIDITY:,}"
)
print(
    "Market Cap:",
    f"${MIN_MARKET_CAP:,}",
    "-",
    f"${MAX_MARKET_CAP:,}"
)
print(
    "5M Volume >= $",
    f"{MIN_5M_VOLUME:,}"
)
print(
    "5M Price Change >= ",
    MIN_5M_PRICE_CHANGE,
    "%"
)
print(
    "5M Transactions >= ",
    MIN_5M_TXNS
)
print(
    "Buy Ratio >= ",
    MIN_BUY_RATIO * 100,
    "%"
)
print()


# ============================================================
# TELEGRAM START
# ============================================================

send_message(
    "🟢 MEME PUMP ALERT V2 STARTED\n\n"

    "Solana token scanner işləyir.\n\n"

    "🎯 Əsas filtr:\n"
    f"• Yaş: {MIN_AGE_HOURS} - "
    f"{MAX_AGE_HOURS} saat\n\n"

    "💰 Market:\n"
    f"• MC: ${MIN_MARKET_CAP:,} - "
    f"${MAX_MARKET_CAP:,}\n"
    f"• Liquidity: ≥ ${MIN_LIQUIDITY:,}\n\n"

    "📈 Momentum:\n"
    f"• 5M Volume: ≥ ${MIN_5M_VOLUME:,}\n"
    f"• 5M Price: ≥ "
    f"{MIN_5M_PRICE_CHANGE}%\n"
    f"• 5M Transactions: ≥ "
    f"{MIN_5M_TXNS}\n"
    f"• Buy ratio: ≥ "
    f"{MIN_BUY_RATIO * 100:.0f}%\n\n"

    "🔎 Scanner aktivdir."
)


# ============================================================
# MAIN LOOP
# ============================================================

while True:

    try:

        check()

    except Exception as e:

        print(
            "MAIN LOOP ERROR:",
            e
        )

    print()
    print(
        f"Next scan in "
        f"{CHECK_INTERVAL} seconds..."
    )
    print()

    time.sleep(
        CHECK_INTERVAL
    )
