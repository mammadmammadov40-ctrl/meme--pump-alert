import os
import time
import requests

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

# ============================================================
# SETTINGS
# ============================================================

CHAIN = "solana"

# Pair age: 3 - 60 gün
MIN_AGE_HOURS = 72
MAX_AGE_HOURS = 60 * 24

MIN_MARKET_CAP = 10_000
MIN_LIQUIDITY = 10_000

# 5M-də +15%-dən çox qaçmış tokenə erkən siqnal vermə
MAX_CURRENT_5M_PRICE = 15

# Çox ölü tokenləri azalt
MIN_5M_VOLUME = 500

# Alış aktivliyi
MIN_BUYS = 3
MIN_BUY_RATIO = 52
MIN_BUY_INCREASE = 1

# Erkən siqnal score-u
MIN_EARLY_SCORE = 30

CHECK_INTERVAL = 60
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
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    try:
        response = session.post(
            url,
            json={
                "chat_id": CHAT_ID,
                "text": text,
                "disable_web_page_preview": False
            },
            timeout=20
        )

        print("TELEGRAM STATUS:", response.status_code)

        if not response.ok:
            print("TELEGRAM RESPONSE:", response.text)

        return response.ok

    except Exception as e:
        print("TELEGRAM ERROR:", e)
        return False


# ============================================================
# DEXSCREENER
# ============================================================

def get_dex_profiles():
    url = "https://api.dexscreener.com/token-profiles/latest/v1"

    try:
        response = session.get(url, timeout=20)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print("DEX PROFILE ERROR:", e)
        return []


def get_dex_pairs(token_address):
    url = (
        f"https://api.dexscreener.com/"
        f"token-pairs/v1/{CHAIN}/{token_address}"
    )

    try:
        response = session.get(url, timeout=20)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print("DEX PAIR ERROR:", e)
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
        response = session.get(url, timeout=20)
        response.raise_for_status()
        data = response.json()
        return data.get("data", [])
    except Exception as e:
        print("GECKO ERROR:", e)
        return []


def get_gecko_pool_addresses():
    addresses = set()

    for pool in get_gecko_pools():
        try:
            pool_id = pool.get("id", "")
            if pool_id:
                addresses.add(pool_id.split("_", 1)[-1])
        except Exception:
            pass

    return addresses


# ============================================================
# AGE
# ============================================================

def calculate_age_hours(created_ms):
    if not created_ms:
        return None

    try:
        return (
            time.time() * 1000 - float(created_ms)
        ) / 1000 / 60 / 60
    except Exception:
        return None


# ============================================================
# EXTRACT / FILTER
# ============================================================

def extract_pair(pair, source, has_dex_profile=False, gecko_seen=False):
    try:
        if pair.get("chainId") != CHAIN:
            return None

        pair_address = pair.get("pairAddress")
        if not pair_address:
            return None

        # 3 - 60 gün
        age_hours = calculate_age_hours(pair.get("pairCreatedAt"))

        if age_hours is None:
            return None

        if age_hours < MIN_AGE_HOURS or age_hours > MAX_AGE_HOURS:
            return None

        liquidity = safe_float(
            (pair.get("liquidity") or {}).get("usd")
        )

        if liquidity < MIN_LIQUIDITY:
            return None

        market_cap = safe_float(pair.get("marketCap"))

        if market_cap <= 0:
            market_cap = safe_float(pair.get("fdv"))

        if market_cap < MIN_MARKET_CAP:
            return None

        m5 = (pair.get("txns") or {}).get("m5") or {}
        buys = safe_int(m5.get("buys"))
        sells = safe_int(m5.get("sells"))

        total = buys + sells
        buy_ratio = (buys / total * 100) if total else 0

        if buys < MIN_BUYS:
            return None

        if buy_ratio < MIN_BUY_RATIO:
            return None

        volume_5m = safe_float(
            (pair.get("volume") or {}).get("m5")
        )

        if volume_5m < MIN_5M_VOLUME:
            return None

        price_5m = safe_float(
            (pair.get("priceChange") or {}).get("m5")
        )

        if price_5m > MAX_CURRENT_5M_PRICE:
            return None

        base = pair.get("baseToken") or {}

        # Keyfiyyət göstəricisi.
        # Bu "verified" zəmanəti deyil; əlavə profil/cross-source üstünlüyüdür.
        quality = 0

        if has_dex_profile:
            quality += 2

        if gecko_seen:
            quality += 2

        if liquidity >= 25_000:
            quality += 1

        if buys >= 10:
            quality += 1

        if buy_ratio >= 65:
            quality += 1

        return {
            "pair": pair_address,
            "token": base.get("address", ""),
            "name": base.get("name", "UNKNOWN"),
            "symbol": base.get("symbol", "UNKNOWN"),
            "age_hours": age_hours,
            "market_cap": market_cap,
            "liquidity": liquidity,
            "buys": buys,
            "sells": sells,
            "buy_ratio": buy_ratio,
            "volume_5m": volume_5m,
            "price_5m": price_5m,
            "source": source,
            "quality": quality,
            "url": pair.get(
                "url",
                f"https://dexscreener.com/{CHAIN}/{pair_address}"
            )
        }

    except Exception as e:
        print("EXTRACT ERROR:", e)
        return None


# ============================================================
# MOMENTUM SCORE
# ============================================================

def calculate_score(current, old):
    score = 0

    buys = current["buys"]
    buy_ratio = current["buy_ratio"]
    volume = current["volume_5m"]
    price = current["price_5m"]

    # Buy sayı
    if buys >= 3:
        score += 5
    if buys >= 5:
        score += 5
    if buys >= 10:
        score += 8
    if buys >= 20:
        score += 8

    # Buy pressure
    if buy_ratio >= 55:
        score += 5
    if buy_ratio >= 60:
        score += 5
    if buy_ratio >= 70:
        score += 8
    if buy_ratio >= 80:
        score += 8

    # Scan-lar arasında artım
    if old:
        old_buys = old.get("buys", 0)
        old_volume = old.get("volume_5m", 0)

        buy_increase = buys - old_buys
        volume_increase = volume - old_volume

        if buy_increase >= 1:
            score += 8
        if buy_increase >= 3:
            score += 8
        if buy_increase >= 5:
            score += 8

        if volume_increase > 0:
            score += 4

        if old_volume > 0 and volume >= old_volume * 1.5:
            score += 8

        if old_volume > 0 and volume >= old_volume * 2:
            score += 5

    # Volume
    if volume >= 500:
        score += 3
    if volume >= 1_000:
        score += 3
    if volume >= 5_000:
        score += 4
    if volume >= 10_000:
        score += 4

    # Erkən price zonası
    if 0 <= price <= 5:
        score += 10
    elif 5 < price <= 10:
        score += 7
    elif 10 < price <= MAX_CURRENT_5M_PRICE:
        score += 3

    # Buy > sell
    if buys > current["sells"]:
        score += 5

    if buys >= current["sells"] * 2:
        score += 7

    # Cross-source / profile üstünlüyü
    score += current.get("quality", 0)

    return score


def analyze(current):
    pair = current["pair"]
    old = previous.get(pair)

    # İlk dəfə görürük: yadda saxla, növbəti scan-da dəyişiklik axtar.
    if old is None:
        current["score"] = 0
        return False

    buy_increase = current["buys"] - old.get("buys", 0)
    volume_increase = current["volume_5m"] - old.get("volume_5m", 0)

    score = calculate_score(current, old)
    current["score"] = score

    momentum = (
        buy_increase >= MIN_BUY_INCREASE
        or volume_increase > 0
    )

    strong_momentum = (
        buy_increase >= 2
        or (
            old.get("volume_5m", 0) > 0
            and current["volume_5m"] >= old["volume_5m"] * 1.25
        )
    )

    required_score = MIN_EARLY_SCORE - (5 if strong_momentum else 0)

    return (
        score >= required_score
        and momentum
        and current["buy_ratio"] >= MIN_BUY_RATIO
        and current["price_5m"] <= MAX_CURRENT_5M_PRICE
    )


# ============================================================
# MEMORY
# ============================================================

def remember(current):
    previous[current["pair"]] = {
        "buys": current["buys"],
        "sells": current["sells"],
        "volume_5m": current["volume_5m"],
        "price_5m": current["price_5m"],
        "buy_ratio": current["buy_ratio"],
        "timestamp": time.time()
    }


def is_on_cooldown(pair):
    last = alerted.get(pair)

    if not last:
        return False

    return (
        time.time() - last
        < ALERT_COOLDOWN_HOURS * 3600
    )


# ============================================================
# SCAN DEX
# ============================================================

def scan_dex(gecko_addresses):
    results = {}

    profiles = get_dex_profiles()
    print("DEXSCREENER PROFILES:", len(profiles))

    for profile in profiles:

        if profile.get("chainId") != CHAIN:
            continue

        token_address = profile.get("tokenAddress")

        if not token_address:
            continue

        for pair in get_dex_pairs(token_address):

            pair_address = pair.get("pairAddress", "")

            result = extract_pair(
                pair,
                "DEX Screener",
                has_dex_profile=True,
                gecko_seen=pair_address in gecko_addresses
            )

            if result:
                results[result["pair"]] = result

    return results


# ============================================================
# SCAN GECKO
# ============================================================

def scan_gecko():
    results = {}
    pools = get_gecko_pools()

    print("GECKOTERMINAL POOLS:", len(pools))

    for pool in pools:

        try:
            pool_id = pool.get("id", "")

            if not pool_id:
                continue

            pool_address = pool_id.split("_", 1)[-1]

            search_url = (
                "https://api.dexscreener.com/"
                "latest/dex/search"
                f"?q={pool_address}"
            )

            response = session.get(search_url, timeout=20)
            response.raise_for_status()

            for pair in response.json().get("pairs", []):

                result = extract_pair(
                    pair,
                    "DEX Screener + GeckoTerminal",
                    has_dex_profile=False,
                    gecko_seen=True
                )

                if result:
                    results[result["pair"]] = result

        except Exception as e:
            print("GECKO SCAN ERROR:", e)

    return results


# ============================================================
# MAIN SCAN
# ============================================================

def scan():

    results = {}

    try:
        gecko_addresses = get_gecko_pool_addresses()
    except Exception as e:
        print("GECKO ADDRESS ERROR:", e)
        gecko_addresses = set()

    try:
        results.update(scan_dex(gecko_addresses))
    except Exception as e:
        print("DEX SCAN ERROR:", e)

    try:
        results.update(scan_gecko())
    except Exception as e:
        print("GECKO SCAN ERROR:", e)

    print()
    print("==========================================")
    print("CANDIDATES:", len(results))
    print("==========================================")

    alerts = []

    for current in results.values():

        if analyze(current) and not is_on_cooldown(current["pair"]):
            alerts.append(current)

        remember(current)

    alerts.sort(
        key=lambda x: (
            x.get("score", 0),
            x.get("quality", 0),
            x.get("buy_ratio", 0),
            x.get("buys", 0),
            x.get("volume_5m", 0)
        ),
        reverse=True
    )

    print("EARLY SIGNALS:", len(alerts))

    sent = 0

    for result in alerts:

        message = (
            "🚨 EARLY MOMENTUM ALERT\n\n"
            f"🪙 {result['name']} ({result['symbol']})\n\n"
            f"⏱ Pair age: {result['age_hours']:.1f} saat\n"
            f"💰 Market Cap: ${result['market_cap']:,.0f}\n"
            f"💧 Liquidity: ${result['liquidity']:,.0f}\n\n"
            f"🟢 5M Buys: {result['buys']}\n"
            f"🔴 5M Sells: {result['sells']}\n"
            f"📊 Buy ratio: {result['buy_ratio']:.1f}%\n"
            f"💵 5M Volume: ${result['volume_5m']:,.0f}\n"
            f"📈 5M Price: {result['price_5m']:+.2f}%\n\n"
            f"🔥 Early Score: {result['score']}\n"
            f"⭐ Quality: {result['quality']}\n"
            f"🔎 Source: {result['source']}\n\n"
            f"🔗 {result['url']}"
        )

        if send_message(message):
            alerted[result["pair"]] = time.time()
            sent += 1

            print(
                "🚨 ALERT:",
                result["symbol"],
                "| SCORE:", result["score"],
                "| BUYS:", result["buys"],
                "| BUY RATIO:", round(result["buy_ratio"], 1),
                "| PRICE:", round(result["price_5m"], 2)
            )

        if sent >= MAX_ALERTS_PER_SCAN:
            break

    print("ALERTS SENT:", sent)


# ============================================================
# START
# ============================================================

print()
print("🟢 MEME PUMP EARLY SCANNER V5")
print()
print("Sources:")
print("• DEX Screener")
print("• GeckoTerminal")
print()
print("Pair age: 3 - 60 days")
print("Minimum MC: $10,000")
print("Minimum Liquidity: $10,000")
print("Minimum 5M volume:", f"${MIN_5M_VOLUME:,}")
print("Minimum 5M buys:", MIN_BUYS)
print("Minimum buy ratio:", f"{MIN_BUY_RATIO}%")
print("Minimum buy increase:", MIN_BUY_INCREASE)
print("Maximum current 5M price:", f"{MAX_CURRENT_5M_PRICE}%")
print("Early momentum detection: ON")
print("Jupiter: OFF")
print()

send_message(
    "🟢 MEME PUMP EARLY SCANNER V5 STARTED\n\n"
    "🎯 Məqsəd:\n"
    "Artıq uçmuş tokeni yox,\n"
    "yeni momentum başlayan tokeni tapmaq.\n\n"
    "🔎 Sources:\n"
    "• DEX Screener\n"
    "• GeckoTerminal\n\n"
    "⚙️ Filtrlər:\n"
    "• Pair age: 3 - 60 gün\n"
    "• Market Cap: ≥ $10K\n"
    "• Liquidity: ≥ $10K\n"
    "• 5M Buys: ≥ 3\n"
    "• Buy ratio: ≥ 52%\n"
    "• 5M Volume: ≥ $500\n"
    "• Yeni Buy artımı: ≥ 1\n\n"
    "🔥 Buy artımı + Buy pressure + "
    "Volume artımı + erkən price birlikdə "
    "qiymətləndirilir.\n\n"
    "⭐ DEX profile / Gecko cross-source "
    "məlumatına əlavə üstünlük verilir.\n\n"
    "Jupiter istifadə olunmur."
)

while True:

    try:
        scan()
    except Exception as e:
        print("MAIN LOOP ERROR:", e)

    print()
    print("Next scan in", CHECK_INTERVAL, "seconds...")
    print()

    time.sleep(CHECK_INTERVAL)
