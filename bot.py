import os
import time
import requests


# ============================================================
# ENV VARIABLES
# ============================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]


# ============================================================
# SETTINGS
# ============================================================

CHAIN = "solana"

# Pair yaşı:
# 3 gündən KİÇİK -> keçmir
# 3-120 gün -> keçir
# 120 gündən BÖYÜK -> keçmir
MIN_AGE_DAYS = 3
MAX_AGE_DAYS = 120

# Minimum liquidity
MIN_LIQUIDITY = 10_000

# Market Cap
MIN_MARKET_CAP = 10_000
MAX_MARKET_CAP = 1_000_000

# 5 dəqiqəlik minimum volume
MIN_5M_VOLUME = 5_000

# Eyni pair-i ikinci dəfə göndərməmək
seen = set()

# Neçə saniyədən bir yoxlasın
CHECK_INTERVAL = 60


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
        response = requests.post(
            url,
            json=payload,
            timeout=20
        )

        print("TELEGRAM STATUS:", response.status_code)
        print("TELEGRAM RESPONSE:", response.text)

        response.raise_for_status()

        return True

    except Exception as e:

        print("TELEGRAM ERROR:", e)

        return False


# ============================================================
# DEXSCREENER - LATEST TOKENS
# ============================================================

def get_latest_tokens():

    url = (
        "https://api.dexscreener.com/"
        "token-profiles/latest/v1"
    )

    try:

        response = requests.get(
            url,
            timeout=20
        )

        response.raise_for_status()

        data = response.json()

        if isinstance(data, list):
            return data

        return []

    except Exception as e:

        print("LATEST TOKENS ERROR:", e)

        return []


# ============================================================
# DEXSCREENER - TOKEN PAIRS
# ============================================================

def get_pairs(token_address):

    url = (
        f"https://api.dexscreener.com/"
        f"token-pairs/v1/{CHAIN}/{token_address}"
    )

    try:

        response = requests.get(
            url,
            timeout=20
        )

        response.raise_for_status()

        data = response.json()

        if isinstance(data, list):
            return data

        return []

    except Exception as e:

        print("PAIR ERROR:", e)

        return []


# ============================================================
# CHECK TOKEN
# ============================================================

def check():

    tokens = get_latest_tokens()

    print()
    print("========================================")
    print("TOKENS FOUND:", len(tokens))
    print("========================================")

    for token in tokens:

        try:

            # ------------------------------------------------
            # YALNIZ SOLANA
            # ------------------------------------------------

            if token.get("chainId") != CHAIN:
                continue

            address = token.get("tokenAddress")

            if not address:
                continue

            # ------------------------------------------------
            # TOKEN PAIRS
            # ------------------------------------------------

            pairs = get_pairs(address)

            if not pairs:
                continue

            for pair in pairs:

                try:

                    # ----------------------------------------
                    # YALNIZ SOLANA
                    # ----------------------------------------

                    if pair.get("chainId") != CHAIN:
                        continue

                    pair_address = pair.get("pairAddress")

                    if not pair_address:
                        continue

                    # ----------------------------------------
                    # EYNİ PAIR TƏKRAR GÖNDƏRİLMƏSİN
                    # ----------------------------------------

                    if pair_address in seen:
                        continue

                    # ----------------------------------------
                    # PAIR AGE
                    # ----------------------------------------

                    created = pair.get("pairCreatedAt")

                    if not created:
                        print(
                            "SKIP:",
                            pair_address,
                            "-> pairCreatedAt yoxdur"
                        )
                        continue

                    age_days = (
                        (time.time() * 1000 - created)
                        / 1000
                        / 60
                        / 60
                        / 24
                    )

                    # 3 gündən kiçik
                    if age_days < MIN_AGE_DAYS:

                        print(
                            "SKIP:",
                            pair_address,
                            "-> YAŞ ÇOX YENİ:",
                            round(age_days, 2),
                            "gün"
                        )

                        continue

                    # 120 gündən böyük
                    if age_days > MAX_AGE_DAYS:

                        print(
                            "SKIP:",
                            pair_address,
                            "-> YAŞ KÖHNƏ:",
                            round(age_days, 2),
                            "gün"
                        )

                        continue

                    # ----------------------------------------
                    # LIQUIDITY
                    # ----------------------------------------

                    liquidity_data = pair.get(
                        "liquidity"
                    ) or {}

                    liquidity = float(
                        liquidity_data.get("usd") or 0
                    )

                    if liquidity < MIN_LIQUIDITY:

                        print(
                            "SKIP:",
                            pair_address,
                            "-> LIQUIDITY:",
                            liquidity
                        )

                        continue

                    # ----------------------------------------
                    # MARKET CAP
                    # ----------------------------------------

                    market_cap = float(
                        pair.get("marketCap")
                        or pair.get("fdv")
                        or 0
                    )

                    if market_cap < MIN_MARKET_CAP:

                        print(
                            "SKIP:",
                            pair_address,
                            "-> MARKET CAP:",
                            market_cap
                        )

                        continue

                    if market_cap > MAX_MARKET_CAP:

                        print(
                            "SKIP:",
                            pair_address,
                            "-> MARKET CAP:",
                            market_cap
                        )

                        continue

                    # ----------------------------------------
                    # 5M VOLUME
                    # ----------------------------------------

                    volume_data = pair.get(
                        "volume"
                    ) or {}

                    volume_5m = float(
                        volume_data.get("m5") or 0
                    )

                    if volume_5m < MIN_5M_VOLUME:

                        print(
                            "SKIP:",
                            pair_address,
                            "-> 5M VOLUME:",
                            volume_5m
                        )

                        continue

                    # ----------------------------------------
                    # TOKEN INFO
                    # ----------------------------------------

                    base_token = pair.get(
                        "baseToken"
                    ) or {}

                    symbol = base_token.get(
                        "symbol",
                        "UNKNOWN"
                    )

                    name = base_token.get(
                        "name",
                        "UNKNOWN"
                    )

                    # ----------------------------------------
                    # PRICE
                    # ----------------------------------------

                    price_change_data = pair.get(
                        "priceChange"
                    ) or {}

                    price_change_5m = float(
                        price_change_data.get("m5") or 0
                    )

                    # ----------------------------------------
                    # URL
                    # ----------------------------------------

                    pair_url = pair.get(
                        "url",
                        f"https://dexscreener.com/"
                        f"{CHAIN}/{pair_address}"
                    )

                    # ----------------------------------------
                    # MARK AS SEEN
                    # ----------------------------------------

                    seen.add(pair_address)

                    # ----------------------------------------
                    # TELEGRAM ALERT
                    # ----------------------------------------

                    message = (
                        "🚨 MEME PUMP ALERT\n\n"
                        f"🪙 {name} ({symbol})\n"
                        f"⏱ Yaş: {age_days:.1f} gün\n"
                        f"📈 5 dəq: {price_change_5m:+.2f}%\n"
                        f"💰 Market Cap: ${market_cap:,.0f}\n"
                        f"💧 Liquidity: ${liquidity:,.0f}\n"
                        f"📊 5 dəq Volume: ${volume_5m:,.0f}\n\n"
                        f"🔗 {pair_url}"
                    )

                    if send_message(message):

                        print()
                        print("========================================")
                        print("🚨 ALERT SENT:", symbol)
                        print("AGE:", round(age_days, 2), "days")
                        print("5M:", price_change_5m, "%")
                        print("MC:", market_cap)
                        print("LIQ:", liquidity)
                        print("5M VOL:", volume_5m)
                        print("========================================")
                        print()

                except Exception as e:

                    print(
                        "PAIR CHECK ERROR:",
                        e
                    )

        except Exception as e:

            print(
                "TOKEN CHECK ERROR:",
                e
            )


# ============================================================
# START
# ============================================================

print("🟢 MEME PUMP ALERT STARTED")
print()
print("Chain:", CHAIN)
print(
    "Pair age:",
    MIN_AGE_DAYS,
    "-",
    MAX_AGE_DAYS,
    "days"
)
print(
    "Min liquidity:",
    MIN_LIQUIDITY
)
print(
    "Market Cap:",
    MIN_MARKET_CAP,
    "-",
    MAX_MARKET_CAP
)
print(
    "Min 5M volume:",
    MIN_5M_VOLUME
)
print()


# ============================================================
# TELEGRAM START MESSAGE
# ============================================================

send_message(
    "🟢 BOT STARTED\n\n"
    "Meme Pump Alert işləyir.\n"
    "Solana pair-ləri yoxlanılır...\n\n"
    "Filtrlər:\n"
    f"• Yaş: {MIN_AGE_DAYS} - {MAX_AGE_DAYS} gün\n"
    f"• Liquidity: ≥ ${MIN_LIQUIDITY:,}\n"
    f"• Market Cap: "
    f"${MIN_MARKET_CAP:,} - ${MAX_MARKET_CAP:,}\n"
    f"• 5 dəq Volume: ≥ ${MIN_5M_VOLUME:,}"
)


# ============================================================
# MAIN LOOP
# ============================================================

while True:

    try:

        check()

    except Exception as e:

        print("MAIN LOOP ERROR:", e)

    print(
        f"\nNext check in {CHECK_INTERVAL} seconds...\n"
    )

    time.sleep(CHECK_INTERVAL)
