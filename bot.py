import os
import time
import requests

# =========================
# ENV VARIABLES
# =========================

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

# =========================
# SETTINGS
# =========================

CHAIN = "solana"

# Yeni tokenləri tutmaq üçün
MAX_AGE_MINUTES = 60

# Çox kiçik/zəif pool-ları azaltmaq üçün
MIN_LIQUIDITY = 10_000

# Market cap üçün minimum
MIN_MARKET_CAP = 20_000

# 5 dəqiqəlik dəyişiklik
MIN_5M_CHANGE = 5

# 5 dəqiqəlik minimum volume
MIN_5M_VOLUME = 5_000

# Eyni tokeni təkrar göndərməsin
seen = set()


# =========================
# TELEGRAM
# =========================

def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    response = requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": text,
            "disable_web_page_preview": True
        },
        timeout=20
    )

    print("TELEGRAM STATUS:", response.status_code)
    print("TELEGRAM RESPONSE:", response.text)

    response.raise_for_status()


# =========================
# GET LATEST TOKENS
# =========================

def get_latest_tokens():
    url = "https://api.dexscreener.com/token-profiles/latest/v1"

    response = requests.get(
        url,
        timeout=20
    )

    response.raise_for_status()

    data = response.json()

    if not isinstance(data, list):
        return []

    return data


# =========================
# GET TOKEN PAIRS
# =========================

def get_pairs(token_address):

    url = (
        f"https://api.dexscreener.com/"
        f"tokens/v1/{CHAIN}/{token_address}"
    )

    response = requests.get(
        url,
        timeout=20
    )

    response.raise_for_status()

    data = response.json()

    if not isinstance(data, list):
        return []

    return data


# =========================
# CHECK TOKENS
# =========================

def check():

    print("Checking new tokens...")

    tokens = get_latest_tokens()

    print("Latest profiles:", len(tokens))

    for token in tokens:

        try:

            # Yalnız Solana
            if token.get("chainId") != CHAIN:
                continue

            address = token.get("tokenAddress")

            if not address:
                continue

            # Təkrar token
            if address in seen:
                continue

            pairs = get_pairs(address)

            if not pairs:
                continue

            # Ən uyğun pair-i seç
            valid_pairs = [
                p for p in pairs
                if p.get("chainId") == CHAIN
            ]

            if not valid_pairs:
                continue

            # Liquidity ən yüksək olan pair
            pair = max(
                valid_pairs,
                key=lambda p: (
                    p.get("liquidity") or {}
                ).get("usd") or 0
            )

            # =========================
            # DATA
            # =========================

            liquidity = (
                pair.get("liquidity") or {}
            ).get("usd") or 0

            market_cap = (
                pair.get("marketCap")
                or pair.get("fdv")
                or 0
            )

            price_change = (
                pair.get("priceChange") or {}
            ).get("m5") or 0

            volume_5m = (
                pair.get("volume") or {}
            ).get("m5") or 0

            created = pair.get("pairCreatedAt")

            if not created:
                continue

            # milliseconds -> seconds
            age_minutes = (
                time.time() - (created / 1000)
            ) / 60

            # =========================
            # FILTERS
            # =========================

            if age_minutes < 0:
                continue

            if age_minutes > MAX_AGE_MINUTES:
                continue

            if liquidity < MIN_LIQUIDITY:
                continue

            if market_cap < MIN_MARKET_CAP:
                continue

            if price_change < MIN_5M_CHANGE:
                continue

            if volume_5m < MIN_5M_VOLUME:
                continue

            # =========================
            # TOKEN INFO
            # =========================

            base_token = pair.get(
                "baseToken"
            ) or {}

            name = base_token.get(
                "name",
                "Unknown"
            )

            symbol = base_token.get(
                "symbol",
                "?"
            )

            pair_url = pair.get(
                "url",
                f"https://dexscreener.com/solana/{address}"
            )

            # =========================
            # MARK AS SEEN
            # =========================

            seen.add(address)

            # =========================
            # ALERT
            # =========================

            message = (
                "🚨 MEME PUMP ALERT\n\n"
                f"🪙 {name} ({symbol})\n\n"
                f"⏱ Yaş: {age_minutes:.1f} dəq\n"
                f"📈 5 dəq: +{price_change:.1f}%\n"
                f"💰 Market Cap: ${market_cap:,.0f}\n"
                f"💧 Liquidity: ${liquidity:,.0f}\n"
                f"📊 5 dəq Volume: ${volume_5m:,.0f}\n\n"
                f"🔗 {pair_url}"
            )

            send_message(message)

            print(
                "ALERT SENT:",
                symbol,
                address
            )

        except Exception as e:

            print(
                "TOKEN ERROR:",
                e
            )


# =========================
# STARTUP
# =========================

print("================================")
print("🟢 MEME PUMP ALERT STARTING")
print("================================")

try:

    send_message(
        "🟢 BOT STARTED\n\n"
        "Meme Pump Alert işləyir.\n"
        "Solana tokenləri yoxlanılır..."
    )

except Exception as e:

    print(
        "STARTUP TELEGRAM ERROR:",
        e
    )


# =========================
# MAIN LOOP
# =========================

while True:

    try:

        check()

    except Exception as e:

        print(
            "MAIN ERROR:",
            e
        )

    print(
        "Waiting 30 seconds..."
    )

    time.sleep(30)
