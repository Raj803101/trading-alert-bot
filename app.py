import time
import threading
import requests
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "Delta Supertrend Live 24/7!"

# ================= CONFIGURATION =================
TELEGRAM_BOT_TOKEN = "9052629155:AAHWJhy0xfQUWH5s2WSIe--Z7fT_mXYUU1I"
CHANNEL_ID = "@theshopcare"
PERIOD = 10
MULTIPLIER = 3.0

PAIRS = [
    {"name": "BTC", "symbol": "BTCUSD", "resolution": "5m"},
    {"name": "XRP", "symbol": "XRPUSD", "resolution": "5m"}
]
# =================================================

def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHANNEL_ID, "text": message}, timeout=10)
    except Exception:
        pass

def fetch_delta_candles(symbol, resolution):
    # start aur end hatakar direct latest candles mangwayi ja rahi hain
    url = f"https://api.delta.exchange/v2/history/candles?resolution={resolution}&symbol={symbol}"
    headers = {"User-Agent": "Mozilla/5.0"}
    
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            res = r.json().get("result", [])
            if res and len(res) >= 20:
                res = sorted(res, key=lambda x: int(x["time"]))
                return [{
                    "time": int(c["time"]),
                    "high": float(c["high"]),
                    "low": float(c["low"]),
                    "close": float(c["close"])
                } for c in res]
            else:
                print(f"[{symbol}] API returned empty or small list: {len(res)} items", flush=True)
        else:
            print(f"API Error ({symbol}): {r.status_code}", flush=True)
    except Exception as e:
