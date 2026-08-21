import os
import time
import json
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import websocket


# ============================================================
# 5M MOMENTUM + REAL BREAKOUT BOT
# BINANCE SPOT
# TELEGRAM ALERT
#
# RESISTANCE DETECTION FIXED
# 500 CANDLES HISTORY
# BOOK-TICKER + REST SPREAD FALLBACK
# ============================================================


BINANCE_REST = "https://api.binance.com"
BINANCE_WS = "wss://stream.binance.com:9443"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

INTERVAL = "5m"


# ============================================================
# HISTORY
# ============================================================

HISTORY_LIMIT = 200
AVERAGE_VOLUME_CANDLES = 20
RESISTANCE_LOOKBACK = 200


# ============================================================
#
