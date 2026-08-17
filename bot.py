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

# Pair age: 3 - 60 gün
MIN_AGE_HOURS = 72
MAX_AGE_HOURS = 60 * 24

# Əsas filtrlər
MIN_MARKET_CAP = 10_000
MIN_LIQUIDITY = 10_000

# Erkən momentum üçün:
# Böyük pump olmuş tokenləri aşağı prioritet edirik.
MAX_CURRENT_5M_PRICE = 15

# Çox ölü tokenləri azaltmaq üçün
MIN_5M_VOLUME = 500

# Scan intervalı
CHECK_INTERVAL = 60

# Bir tokenə alert verdikdən sonra
# neçə saat yenidən alert verməsin
ALERT_COOLDOWN_HOURS = 6

# Eyni scan-da maksimum alert
MAX_ALERTS_PER_SCAN = 5


# ============================================================
# MEMORY
# ============================================================

# Əvvəlki scan məlumatları
previous = {}

# Alert verilmiş tokenlər
alerted = {}

# Session
session = requests.Session()

session.headers.update({
    "User-Agent": "MemePumpEarlyScanner/4.0"
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

def get_dex_profiles():

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
# DEXSCREENER - TOKEN PAIRS
# ============================================================

def get_dex_pairs(token_address):

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
# GECKOTERMINAL
# ============================================================

def get_gecko_pools():

    url = (
        "https://api.geckoterminal.com/"
        "api/v2/networks/solana/new_pools"
    )

    try:

        response = session.get(
            url,
            timeout=20
        )

        response.raise_for_status()

        data = response.json()

        return data.get(
            "data",
            []
        )

    except Exception as e:

        print(
            "GECKO ERROR:",
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
# GET PAIR DATA
# ============================================================

def extract_pair(pair, source):

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

        total = buys + sells

        buy_ratio = 0

        if total > 0:

            buy_ratio = (
                buys / total
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

        # ----------------------------------------------------
        # PRICE
        # ----------------------------------------------------

        price_change = (
            pair.get("priceChange")
            or {}
        )

        price_5m = safe_float(
            price_change.get("m5")
        )

        # ----------------------------------------------------
        # TOKEN
        # ----------------------------------------------------

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

        address = base.get(
            "address",
            ""
        )

        # ----------------------------------------------------
        # URL
        # ----------------------------------------------------

        pair_url = pair.get(
            "url",
            f"https://dexscreener.com/"
            f"{CHAIN}/{pair_address}"
        )

        return {

            "pair": pair_address,

            "token": address,

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

            "source": source,

            "url": pair_url
        }

    except Exception as e:

        print(
            "EXTRACT ERROR:",
            e
        )

        return None


# ============================================================
# EARLY MOMENTUM SCORE
# ============================================================

def calculate_early_score(current, old):

    score = 0

    buys = current["buys"]
    sells = current["sells"]

    buy_ratio = current["buy_ratio"]

    volume = current["volume_5m"]

    price = current["price_5m"]


    # ========================================================
    # 1. BUY ACTIVITY
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
    # 2. BUY RATIO
    # ========================================================

    if buy_ratio >= 50:

        score += 5

    if buy_ratio >= 60:

        score += 10

    if buy_ratio >= 70:

        score += 10

    if buy_ratio >= 80:

        score += 10


    # ========================================================
    # 3. BUY COUNT INCREASE
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

        # Yeni buy-lar gəlir
        if buy_increase >= 1:

            score += 10

        if buy_increase >= 3:

            score += 10

        if buy_increase >= 5:

            score += 10

        # Volume artır
        if volume_increase > 0:

            score += 5

        if (
            old_volume > 0
            and volume >= old_volume * 1.5
        ):

            score += 10


    # ========================================================
    # 4. VOLUME
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
    # 5. PRICE
    #
    # Məqsəd artıq +30% qaçmış tokeni tutmamaqdır.
    # ========================================================

    if price >= 0:

        score += 3

    if price >= 2:

        score += 5

    if price >= 5:

        score += 5

    if price >= 10:

        score += 3

    # +15%-dən yuxarı artıq qaçmış ola bilər
    if price > MAX_CURRENT_5M_PRICE:

        score -= 20


    # ========================================================
    # 6. HEALTHY BUY/SELL
    # ========================================================

    if buys > sells:

        score += 5

    if buys >= sells * 2:

        score += 10


    return score


# ============================================================
# FIND EARLY SIGNAL
# ============================================================

def analyze(current):

    pair = current["pair"]

    old = previous.get(
        pair
    )

    score = calculate_early_score(
        current,
        old
    )

    current["score"] = score

    # ========================================================
    # FIRST APPEARANCE
    #
    # İlk dəfə görəndə dərhal alert vermirik.
    # Növbəti scan-da dəyişiklik görmək istəyirik.
    # ========================================================

    if old is None:

        return False

    buy_increase = (
        current["buys"]
        - old.get("buys", 0)
    )

    volume_increase = (
        current["volume_5m"]
        - old.get("volume_5m", 0)
    )

    price = current["price_5m"]

    buy_ratio = current["buy_ratio"]

    # ========================================================
    # ƏSAS ERKƏN MOMENTUM
    # ========================================================

    momentum = False

    if buy_increase >= 1:

        momentum = True

    if buy_increase >= 3:

        momentum = True

    if volume_increase > 0:

        momentum = True

    # ========================================================
    # BUY PRESSURE
    # ========================================================

    buy_pressure = (
        buy_ratio >= 55
    )

    # ========================================================
    # PRICE
    # ========================================================

    early_price = (
        price <= MAX_CURRENT_5M_PRICE
    )

    # ========================================================
    # FINAL
    #
    # Minimum score 35.
    # Bu əvvəlki sistemdən xeyli yumşaqdır.
    # ========================================================

    if (
        score >= 35
        and momentum
        and buy_pressure
        and early_price
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
# UPDATE MEMORY
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
# SCAN DEXSCREENER
# ============================================================

def scan_dex():

    results = {}

    profiles = get_dex_profiles()

    print(
        "DEXSCREENER PROFILES:",
        len(profiles)
    )

    for profile in profiles:

        if profile.get(
            "chainId"
        ) != CHAIN:

            continue

        token_address = profile.get(
            "tokenAddress"
        )

        if not token_address:

            continue

        pairs = get_dex_pairs(
            token_address
        )

        for pair in pairs:

            result = extract_pair(
                pair,
                "DEX Screener"
            )

            if result:

                results[
                    result["pair"]
                ] = result

    return results


# ============================================================
# SCAN GECKOTERMINAL
# ============================================================

def scan_gecko():

    results = {}

    pools = get_gecko_pools()

    print(
        "GECKOTERMINAL POOLS:",
        len(pools)
    )

    for pool in pools:

        try:

            pool_id = pool.get(
                "id"
            )

            if not pool_id:

                continue

            # solana_POOL_ADDRESS
            pool_address = pool_id.split(
                "_"
            )[-1]

            if not pool_address:

                continue

            # Gecko pool-u DexScreener-də tap
            search_url = (
                "https://api.dexscreener.com/"
                "latest/dex/search"
                f"?q={pool_address}"
            )

            response = session.get(
                search_url,
                timeout=20
            )

            response.raise_for_status()

            data = response.json()

            pairs = data.get(
                "pairs",
                []
            )

            for pair in pairs:

                result = extract_pair(
                    pair,
                    "GeckoTerminal"
                )

                if result:

                    results[
                        result["pair"]
                    ] = result

        except Exception as e:

            print(
                "GECKO SCAN ERROR:",
                e
            )

    return results


# ============================================================
# MAIN SCAN
# ============================================================

def scan():

    results = {}

    # --------------------------------------------------------
    # DEXSCREENER
    # --------------------------------------------------------

    try:

        dex_results = scan_dex()

        results.update(
            dex_results
        )

    except Exception as e:

        print(
            "DEX SCAN ERROR:",
            e
        )


    # --------------------------------------------------------
    # GECKOTERMINAL
    # --------------------------------------------------------

    try:

        gecko_results = scan_gecko()

        results.update(
            gecko_results
        )

    except Exception as e:

        print(
            "GECKO SCAN ERROR:",
            e
        )


    print()
    print(
        "=========================================="
    )

    print(
        "CANDIDATES:",
        len(results)
    )

    print(
        "=========================================="
    )


    # ========================================================
    # ANALYZE
    # ========================================================

    alerts = []

    for current in results.values():

        pair = current["pair"]

        # İlk dəfə gördüyümüz tokeni yadda saxla
        # amma dərhal alert vermə.
        should_alert = analyze(
            current
        )

        if should_alert:

            if not is_on_cooldown(
                pair
            ):

                alerts.append(
                    current
                )

        # Sonrakı scan üçün yadda saxla
        remember(
            current
        )


    # ========================================================
    # SCORE-YA GÖRƏ SIRALA
    # ========================================================

    alerts.sort(
        key=lambda x: (
            x.get("score", 0),
            x.get("buy_ratio", 0),
            x.get("buys", 0),
            x.get("volume_5m", 0)
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

        pair = result["pair"]

        old = previous.get(
            pair,
            {}
        )

        # Əslində previous artıq yenilənib,
        # ona görə delta üçün sadə məlumatı
        # mesajdan əvvəl ayrıca çıxarmaq mümkün deyil.
        # Score və cari göstəriciləri göstəririk.

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

            f"🔎 Source: "
            f"{result['source']}\n\n"

            f"🔗 {result['url']}"
        )

        if send_message(
            message
        ):

            alerted[
                pair
            ] = time.time()

            sent += 1

            print(
                "🚨 EARLY ALERT:",
                result["symbol"],
                "| SCORE:",
                result["score"],
                "| BUYS:",
                result["buys"],
                "| BUY RATIO:",
                round(
                    result["buy_ratio"],
                    1
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
    "🟢 MEME PUMP EARLY SCANNER V4"
)
print()

print(
    "Sources:"
)

print(
    "• DEX Screener"
)

print(
    "• GeckoTerminal"
)

print()

print(
    "Pair age: 0 - 60 days"
)

print(
    "Minimum MC: $10,000"
)

print(
    "Minimum Liquidity: $10,000"
)

print(
    "Minimum 5M volume:",
    f"${MIN_5M_VOLUME:,}"
)

print(
    "Early momentum detection: ON"
)

print(
    "Jupiter: OFF"
)

print()


# ============================================================
# TELEGRAM START
# ============================================================

send_message(
    "🟢 MEME PUMP EARLY SCANNER V4 STARTED\n\n"

    "🎯 Məqsəd:\n"
    "Artıq uçmuş tokeni yox,\n"
    "yeni momentum başlayan tokeni tapmaq.\n\n"

    "🔎 Sources:\n"
    "• DEX Screener\n"
    "• GeckoTerminal\n\n"

    "⚙️ Filtrlər:\n"
    "• Pair age: 0 - 60 gün\n"
    "• Market Cap: ≥ $10K\n"
    "• Liquidity: ≥ $10K\n"
    "• Minimum Buy: YOXDUR\n\n"

    "🔥 Buy artımı + Buy pressure + "
    "Volume artımı birlikdə qiymətləndirilir.\n\n"

    "Jupiter istifadə olunmur."
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
