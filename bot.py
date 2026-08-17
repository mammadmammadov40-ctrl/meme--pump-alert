import os
import time
import json
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

# TOKEN YAŞI
# 3 gün - 60 gün
MIN_AGE_HOURS = 3 * 24
MAX_AGE_HOURS = 60 * 24

# ƏSAS FİLTRLƏR
MIN_MARKET_CAP = 10_000
MIN_LIQUIDITY = 10_000

# 5 dəqiqəlik minimum volume
MIN_5M_VOLUME = 500

# Buy pressure
MIN_BUY_RATIO = 55

# 5M-də minimum hərəkət
# 0 qoyuruq ki, yalnız qiymətə görə bloklanmasın.
MIN_PRICE_CHANGE_5M = 0

# Scan intervalı
CHECK_INTERVAL = 30

# Bir scan-da maksimum alert
MAX_ALERTS_PER_SCAN = 5


# ============================================================
# MEMORY
# ============================================================

previous = {}

# Bu tokenlərə artıq SIQNAL verilib
# Eyni token bir daha alert almayacaq.
alerted_tokens = set()

# Railway restart zamanı da mümkün qədər yadda saxlamaq üçün
ALERT_FILE = "alerted_tokens.json"


# ============================================================
# SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "MemePumpEarlyScanner/5.0"
})


# ============================================================
# LOAD ALERTED TOKENS
# ============================================================

def load_alerted_tokens():

    global alerted_tokens

    try:

        if os.path.exists(ALERT_FILE):

            with open(
                ALERT_FILE,
                "r",
                encoding="utf-8"
            ) as f:

                data = json.load(f)

                if isinstance(data, list):

                    alerted_tokens = set(data)

        print(
            "ALREADY ALERTED TOKENS:",
            len(alerted_tokens)
        )

    except Exception as e:

        print(
            "LOAD MEMORY ERROR:",
            e
        )


# ============================================================
# SAVE ALERTED TOKENS
# ============================================================

def save_alerted_tokens():

    try:

        with open(
            ALERT_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                list(alerted_tokens),
                f
            )

    except Exception as e:

        print(
            "SAVE MEMORY ERROR:",
            e
        )


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

        # ----------------------------------------------------
        # CHAIN
        # ----------------------------------------------------

        if pair.get("chainId") != CHAIN:

            return None


        # ----------------------------------------------------
        # PAIR
        # ----------------------------------------------------

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

        token_address = base.get(
            "address",
            ""
        )

        if not token_address:

            return None


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

    token = current["token"]

    pair = current["pair"]


    # ========================================================
    # ƏVVƏL ALERT VERİLİBSƏ
    #
    # NƏ OLURSA OLSUN YENİDƏN ALERT YOXDUR.
    # ========================================================

    if token in alerted_tokens:

        return False


    old = previous.get(
        pair
    )


    # İlk dəfə gördük
    if old is None:

        return False


    # ========================================================
    # DELTA
    # ========================================================

    buy_increase = (
        current["buys"]
        - old.get("buys", 0)
    )

    volume_increase = (
        current["volume_5m"]
        - old.get("volume_5m", 0)
    )


    # ========================================================
    # SCORE
    # ========================================================

    score = calculate_score(
        current,
        old
    )

    current["score"] = score


    # ========================================================
    # MOMENTUM
    # ========================================================

    momentum = False


    if buy_increase >= 1:

        momentum = True


    if buy_increase >= 3:

        momentum = True


    if volume_increase > 0:

        momentum = True


    # Qiymət ciddi hərəkət edirsə
    if current["price_5m"] >= 5:

        momentum = True


    # ========================================================
    # BUY PRESSURE
    # ========================================================

    buy_pressure = (
        current["buy_ratio"]
        >= MIN_BUY_RATIO
    )


    # ========================================================
    # PRICE
    # ========================================================

    price_ok = (
        current["price_5m"]
        >= MIN_PRICE_CHANGE_5M
    )


    # ========================================================
    # FINAL SIGNAL
    # ========================================================

    if (
        score >= 35
        and momentum
        and buy_pressure
        and price_ok
    ):

        return True


    return False


# ============================================================
# REMEMBER
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
        "PROFILES:",
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
                pair
            )

            if result:

                pair_address = result[
                    "pair"
                ]

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
        "=========================================="
    )

    print(
        "🔎 NEW SCAN"
    )

    print(
        "=========================================="
    )


    # --------------------------------------------------------
    # DEX
    # --------------------------------------------------------

    try:

        results = scan_dex()

    except Exception as e:

        print(
            "DEX ERROR:",
            e
        )

        results = {}


    print(
        "DISCOVERED TOKENS:",
        len(results)
    )


    # ========================================================
    # ANALYZE
    # ========================================================

    candidates = 0

    early_signals = []

    already_alerted = 0


    for current in results.values():

        token = current["token"]


        # Artıq alert verilib
        if token in alerted_tokens:

            already_alerted += 1

            remember(
                current
            )

            continue


        # Əsas qaydalara düşür
        candidates += 1


        should_alert = analyze(
            current
        )


        if should_alert:

            early_signals.append(
                current
            )


        # Sonrakı scan üçün yadda saxla
        remember(
            current
        )


    # ========================================================
    # SORT
    # ========================================================

    early_signals.sort(
        key=lambda x: (
            x.get("score", 0),
            x.get("buy_ratio", 0),
            x.get("price_5m", 0),
            x.get("buys", 0)
        ),
        reverse=True
    )


    print(
        "CANDIDATES:",
        candidates
    )

    print(
        "ALREADY ALERTED TOKENS:",
        already_alerted
    )

    print(
        "EARLY SIGNALS:",
        len(early_signals)
    )


    # ========================================================
    # SEND TELEGRAM
    # ========================================================

    sent = 0


    for result in early_signals:

        token = result["token"]


        # Təhlükəsizlik:
        # eyni scan-da da iki dəfə göndərilməsin.
        if token in alerted_tokens:

            continue


        message = (
            "🚨 EARLY MOMENTUM SIGNAL\n\n"

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

            f"🔥 Score: "
            f"{result['score']}\n\n"

            f"🔗 {result['url']}"
        )


        if send_message(
            message
        ):

            # =================================================
            # ƏN VACİB HİSSƏ
            #
            # Bu token artıq bir dəfə alert aldı.
            # Sonrakı +20%, +40%, +50%, +100%
            # hərəkətlərdə YENİ alert gəlməyəcək.
            # =================================================

            alerted_tokens.add(
                token
            )

            save_alerted_tokens()

            sent += 1


            print(
                "🚨 ALERT SENT:",
                result["symbol"],
                "| SCORE:",
                result["score"],
                "| PRICE:",
                result["price_5m"],
                "%"
            )


        if sent >= MAX_ALERTS_PER_SCAN:

            break


    print(
        "ALERTS SENT:",
        sent
    )

    print(
        "=========================================="
    )


# ============================================================
# START
# ============================================================

load_alerted_tokens()


print()
print(
    "🟢 MEME PUMP EARLY SCANNER V5"
)
print()

print(
    "Source: DEX Screener"
)

print(
    "Pair age: 3 - 60 days"
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
    "Minimum Buy Ratio: 55%"
)

print(
    "Scan interval: 30 seconds"
)

print(
    "ONE TOKEN = ONE ALERT"
)

print()


# ============================================================
# TELEGRAM START
# ============================================================

send_message(
    "🟢 MEME PUMP EARLY SCANNER V5 STARTED\n\n"

    "🔎 Source:\n"
    "• DEX Screener\n\n"

    "⚙️ Rules:\n"
    "• Pair age: 3 - 60 gün\n"
    "• Market Cap: ≥ $10K\n"
    "• Liquidity: ≥ $10K\n"
    "• 5M Volume: ≥ $500\n"
    "• Buy ratio: ≥ 55%\n\n"

    "🔥 Early momentum aktivdir.\n\n"

    "⚠️ Eyni tokenə yalnız 1 dəfə alert veriləcək.\n"
    "Token daha sonra +20%, +40%, +50% və ya +100%\n"
    "qalxsa belə ikinci alert gəlməyəcək."
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
