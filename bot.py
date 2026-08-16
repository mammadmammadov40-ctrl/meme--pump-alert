import  os

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

# Pair yaşı: 3 - 120 gün
MIN_AGE_DAYS = 3
MAX_AGE_DAYS = 120

# Likvidlik: minimum $10,000
MIN_LIQUIDITY = 10_000

# Market Cap: $10,000 - $1,000,000
MIN_MARKET_CAP = 10_000
MAX_MARKET_CAP = 1_000_000

# 5 dəqiqəlik minimum volume
MIN_5M_VOLUME = 5_000

# Eyni pair-i təkrar göndərməmək üçün
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
# DEXSCREENER
# =========================

def get_latest_tokens():
    url = "https://api.dexscreener.com/token-profiles/latest/v1"

    response = requests.get(
        url,
        timeout=20
    )

    response.raise_for_status()

    data = response.json()

    if isinstance(data, list):
        return data

    return []


def get_pairs(token_address):
    url = (
        f"https://api.dexscreener.com/"
        f"token-pairs/v1/{CHAIN}/{token_address}"
    )

    response = requests.get(
        url,
        timeout=20
    )

    response.raise_for_status()

    data = response.json()

    if isinstance(data, list):
        return data

    return []


# =========================
# CHECK
# =========================

def check():

    tokens = get_latest_tokens()

    print("TOKENS FOUND:", len(tokens))

    for token in tokens:

        try:

            # Yalnız Solana
            if token.get("chainId") != CHAIN:
                continue

            address = token.get("tokenAddress")

            if not address:
                continue

            # Tokenin pair-lərini al
            pairs = get_pairs(address)

            if not pairs:
                continue

            for pair in pairs:

                try:

                    # Yalnız Solana
                    if pair.get("chainId") != CHAIN:
                        continue

                    pair_address = pair.get("pairAddress")

                    if not pair_address:
                        continue

                    # Eyni pair artıq göndərilibsə keç
                    if pair_address in seen:
                        continue

                    # =========================
                    # PAIR AGE
                    # =========================

                    created = pair.get("pairCreatedAt")

                    if not created:
                        continue

                    age_days = (
                        (time.time() * 1000 - created)
                        / 1000
                        / 60
                        / 60
                        / 24
                    )

                    # 3 - 120 gün
                    if age_days < MIN_AGE_DAYS:
                        continue

                    if age_days > MAX_AGE_DAYS:
                        continue

                    # =========================
                    # LIQUIDITY
                    # =========================

                    liquidity_data = pair.get("liquidity") or {}

                    liquidity = float(
                        liquidity_data.get("usd") or 0
                    )

                    if liquidity < MIN_LIQUIDITY:
                        continue

                    # =========================
                    # MARKET CAP
                    # =========================

                    market_cap = float(
                        pair.get("marketCap")
                        or pair.get("fdv")
                        or 0
                    )

                    if market_cap < MIN_MARKET_CAP:
                        continue

                    if market_cap > MAX_MARKET_CAP:
                        continue

                    # =========================
                    # 5M VOLUME
                    # =========================

                    volume_data = pair.get("volume") or {}

                    volume_5m = float(
                        volume_data.get("m5") or 0
                    )

                    if volume_5m < MIN_5M_VOLUME:
                        continue

                    # =========================
                    # TOKEN INFO
                    # =========================

                    base_token = pair.get("baseToken") or {}

                    symbol = base_token.get(
                        "symbol",
                        "UNKNOWN"
                    )

                    name = base_token.get(
                        "name",
                        "UNKNOWN"
                    )

                    pair_url = pair.get(
                        "url",
                        f"https://dexscreener.com/solana/{pair_address}"
                    )

                    # =========================
                    # 5M PRICE CHANGE
                    # =========================

                    price_change_data = pair.get(
                        "priceChange"
                    ) or {}

                    price_change_5m = float(
                        price_change_data.get("m5") or 0
                    )

                    # =========================
                    # MARK AS SEEN
                    # =========================

                    seen.add(pair_address)

                    # =========================
                    # TELEGRAM ALERT
                    # =========================

                    message = (
                        "🚨 MEME PUMP ALERT\n\n"
                        f"🪙 {name} ({symbol})\n"
                        f"⏱ Yaş: {age_days:.1f} gün\n"
                        f"📈 5 dəq: {price_change_5m:+.1f}%\n"
                        f"💰 Market Cap: ${market_cap:,.0f}\n"
                        f"💧 Liquidity: ${liquidity:,.0f}\n"
                        f"📊 5 dəq Volume: ${volume_5m:,.0f}\n\n"
                        f"🔗 {pair_url}"
                    )

                    send_message(message)

                    print(
                        "ALERT SENT:",
                        symbol,
                        pair_address
                    )

                except Exception as e:

                    print(
                        "PAIR ERROR:",
                        e
                    )

        except Exception as e:

            print(
                "TOKEN ERROR:",
                e
            )


# =========================
# START
# =========================

send_message(
    "🟢 BOT STARTED\n\n"
    "Meme Pump Alert işləyir.\n"
    "Solana pair-ləri yoxlanılır...\n\n"
    "Filtrlər:\n"
    "• Yaş: 3 - 120 gün\n"
    "• Liquidity: ≥ $10,000\n"
    "• Market Cap: $10,000 - $1,000,000\n"
    "• 5 dəq Volume: ≥ $5,000"
)

while True:

    try:

        check()

    except Exception as e:

        print(
            "MAIN ERROR:",
            e
        )

    # 30 saniyədən bir yoxla
    time.sleep(30)
