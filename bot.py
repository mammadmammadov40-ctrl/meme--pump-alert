import os
import time
import requests

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

MIN_LIQUIDITY = 100_000
MIN_MARKET_CAP = 1_000_000
MIN_AGE_DAYS = 3
MAX_AGE_DAYS = 120
MIN_5M_CHANGE = 5
MIN_5M_VOLUME = 20_000

seen = set()

def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    r = requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": True
    }, timeout=20)

    print("TELEGRAM STATUS:", r.status_code)
    print("TELEGRAM RESPONSE:", r.text)

    r.raise_for_status()

def get_tokens():
    url = "https://api.dexscreener.com/token-profiles/latest/v1"
    r = requests.get(url, timeout=20)
    return r.json()

def get_pairs(chain, address):
    url = f"https://api.dexscreener.com/latest/dex/tokens/{address}"
    r = requests.get(url, timeout=20)
    data = r.json()
    return data.get("pairs", [])

def check():
    tokens = get_tokens()

    for token in tokens:
        if token.get("chainId") != "solana":
            continue

        address = token.get("tokenAddress")
        if not address:
            continue

        for pair in get_pairs("solana", address):
            if pair.get("chainId") != "solana":
                continue

            liquidity = (pair.get("liquidity") or {}).get("usd") or 0
            market_cap = pair.get("marketCap") or 0
            change_5m = (pair.get("priceChange") or {}).get("m5") or 0
            volume_5m = (pair.get("volume") or {}).get("m5") or 0
            created = pair.get("pairCreatedAt")

            if not created:
                continue

            age_days = (time.time() * 1000 - created) / 86400000

            if not (MIN_AGE_DAYS <= age_days <= MAX_AGE_DAYS):
                continue

            if liquidity < MIN_LIQUIDITY:
                continue

            if market_cap < MIN_MARKET_CAP:
                continue

            if change_5m < MIN_5M_CHANGE:
                continue

            if volume_5m < MIN_5M_VOLUME:
                continue

            key = pair.get("pairAddress")

            if key in seen:
                continue

            seen.add(key)

            symbol = pair.get("baseToken", {}).get("symbol", "?")
            name = pair.get("baseToken", {}).get("name", "?")

            message = (
                "🚨 MEME PUMP ALERT\n\n"
                f"🪙 {name} ({symbol})\n"
                f"📈 5 dəq: +{change_5m:.1f}%\n"
                f"💰 Market Cap: ${market_cap:,.0f}\n"
                f"💧 Liquidity: ${liquidity:,.0f}\n"
                f"📊 5 dəq Volume: ${volume_5m:,.0f}\n"
                f"🕒 Yaş: {age_days:.0f} gün\n\n"
                f"🔗 https://dexscreener.com/solana/{key}"
            )

            send_message(message)
            send_message("🟢 BOT STARTED - Telegram bağlantısı işləyir")

while True:
    try:
        check()
    except Exception as e:
        print("Error:", e)

    time.sleep(30)
