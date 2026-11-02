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
    {
        "name": "BTC",
        "symbol": "BTCUSD",
        "resolution": "5m"
    },
    {
        "name": "SOL",
        "symbol": "SOLUSD",
        "resolution": "15m"
    }
]
# =================================================

def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHANNEL_ID, "text": message}, timeout=10)
    except Exception:
        pass

def fetch_delta_candles(symbol, resolution):
    # Direct live candles fetch (latest 100 candles bina stale start/end ke)
    url = f"https://api.delta.exchange/v2/history/candles?resolution={resolution}&symbol={symbol}&limit=100"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json"
    }

    try:
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code == 200:
            res = r.json().get("result", [])
            if isinstance(res, list) and len(res) >= 20:
                # Time ascending order me sort karein (oldest -> newest)
                res = sorted(res, key=lambda x: int(x["time"]))
                return [{
                    "time": int(c["time"]),
                    "high": float(c["high"]),
                    "low": float(c["low"]),
                    "close": float(c["close"])
                } for c in res]
        
        # Fallback agar symbol me USDT lagana pade
        if not symbol.endswith("T"):
            url_fallback = f"https://api.delta.exchange/v2/history/candles?resolution={resolution}&symbol={symbol}T&limit=100"
            r_fb = requests.get(url_fallback, headers=headers, timeout=8)
            if r_fb.status_code == 200:
                res_fb = r_fb.json().get("result", [])
                if isinstance(res_fb, list) and len(res_fb) >= 20:
                    res_fb = sorted(res_fb, key=lambda x: int(x["time"]))
                    return [{
                        "time": int(c["time"]),
                        "high": float(c["high"]),
                        "low": float(c["low"]),
                        "close": float(c["close"])
                    } for c in res_fb]
    except Exception as e:
        print(f"Fetch Error ({symbol}): {e}", flush=True)

    return []

def calculate_supertrend(candles):
    if len(candles) < PERIOD + 2:
        return None, None

    # True Range (TR)
    tr = [0.0] * len(candles)
    for i in range(1, len(candles)):
        h, l, pc = candles[i]['high'], candles[i]['low'], candles[i-1]['close']
        tr[i] = max(h - l, abs(h - pc), abs(l - pc))

    # Wilder's Smoothing (TradingView Exact RMA)
    atr = [0.0] * len(candles)
    atr[PERIOD] = sum(tr[1:PERIOD+1]) / PERIOD
    for i in range(PERIOD + 1, len(candles)):
        atr[i] = (atr[i - 1] * (PERIOD - 1) + tr[i]) / PERIOD

    upperbands = [0.0] * len(candles)
    lowerbands = [0.0] * len(candles)
    trend = [True] * len(candles)

    for i in range(PERIOD, len(candles)):
        hl2 = (candles[i]['high'] + candles[i]['low']) / 2.0
        ub = hl2 + (MULTIPLIER * atr[i])
        lb = hl2 - (MULTIPLIER * atr[i])
        c, prev_c = candles[i]['close'], candles[i-1]['close']
        
        upperbands[i] = ub if ub < upperbands[i-1] or prev_c > upperbands[i-1] else upperbands[i-1]
        lowerbands[i] = lb if lb > lowerbands[i-1] or prev_c < lowerbands[i-1] else lowerbands[i-1]

        if trend[i-1]:
            trend[i] = False if c < lowerbands[i] else True
        else:
            trend[i] = True if c > upperbands[i] else False

    # Strictly signal on [-2] (Closed Candle)
    return trend[-2], candles[-2]['close']

def run_bot():
    print(">>> Delta Native Engine (Fresh Fetch Sync) Online <<<", flush=True)
    send_telegram_alert("🟢 Delta Bot Engine Active (Fresh Sync: BTC 5m & SOL 15m)")
    
    last_trends = {}
    last_times = {}

    while True:
        for p in PAIRS:
            name = p["name"]
            sym = p["symbol"]
            res = p["resolution"]

            try:
                candles = fetch_delta_candles(sym, res)
                if candles:
                    c_time = candles[-2]['time']
                    c_trend, c_close = calculate_supertrend(candles)

                    if last_times.get(name) != c_time:
                        last_times[name] = c_time
                        t_str = "BUY (Green)" if c_trend else "SELL (Red)"
                        print(f"[{name} {res}] Closed: {c_close} | Trend: {t_str}", flush=True)

                        prev_trend = last_trends.get(name)
                        if prev_trend is not None and c_trend != prev_trend:
                            tag = "🟢 SUPERTREND BUY SIGNAL" if c_trend else "🔴 SUPERTREND SELL SIGNAL"
                            msg = f"{tag}\nExchange: Delta\nPair: {sym}\nInterval: {res}\nPrice: {c_close}"
                            send_telegram_alert(msg)
                            print(f">>> SIGNAL SENT: {msg} <<<", flush=True)

                        last_trends[name] = c_trend
                else:
                    print(f"Waiting for fresh candles on {name}...", flush=True)
            except Exception as e:
                print(f"Loop Error ({name}): {e}", flush=True)
            
            time.sleep(2)
        
        time.sleep(15)

threading.Thread(target=run_bot, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
