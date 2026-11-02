import time
import threading
import requests
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "Delta Supertrend Final Fixed Bot Running!"

# ================= CONFIGURATION =================
TELEGRAM_BOT_TOKEN = "9052629155:AAHWJhy0xfQUWH5s2WSIe--Z7fT_mXYUU1I"
CHANNEL_ID = "@theshopcare"
PERIOD = 10
MULTIPLIER = 3.0

# BLUNDER FIXED: Delta strictly needs "5m" and "10m" string, not just integers.
PAIRS = [
    {"name": "BTC", "symbol": "BTCUSD", "resolution": "5m", "res_sec": 300},
    {"name": "XRP", "symbol": "XRPUSD", "resolution": "10m", "res_sec": 600}
]
# =================================================

def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHANNEL_ID, "text": message}, timeout=10)
    except Exception:
        pass

def fetch_delta_candles(symbol, resolution, res_sec):
    now = int(time.time())
    start = now - (300 * res_sec) # 300 candles to perfectly sync TradingView Supertrend math
    
    url = f"https://api.delta.exchange/v2/history/candles?resolution={resolution}&symbol={symbol}&start={start}&end={now}"
    
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            res = r.json().get("result", [])
            if res and len(res) > 50:
                res = sorted(res, key=lambda x: int(x["time"]))
                return [{"time": int(c["time"]), "high": float(c["high"]), "low": float(c["low"]), "close": float(c["close"])} for c in res]
        else:
            # Agar ab fail hua toh error message saaf print hoga logs me
            print(f"API Error ({symbol}): {r.status_code} - {r.text}", flush=True)
    except Exception as e:
        print(f"Fetch Error ({symbol}): {e}", flush=True)
    
    return []

def calculate_supertrend(candles):
    if len(candles) < PERIOD + 10:
        return None, None

    # True Range (TR)
    tr = [0.0] * len(candles)
    for i in range(1, len(candles)):
        h, l, pc = candles[i]['high'], candles[i]['low'], candles[i-1]['close']
        tr[i] = max(h - l, abs(h - pc), abs(l - pc))

    # Wilder's Smoothing Math (TradingView Exact)
    atr = [0.0] * len(candles)
    atr[PERIOD] = sum(tr[1:PERIOD+1]) / PERIOD
    for i in range(PERIOD + 1, len(candles)):
        atr[i] = (atr[i - 1] * (PERIOD - 1) + tr[i]) / PERIOD

    upperbands, lowerbands, trend = [0.0] * len(candles), [0.0] * len(candles), [True] * len(candles)

    for i in range(PERIOD, len(candles)):
        hl2 = (candles[i]['high'] + candles[i]['low']) / 2.0
        ub, lb = hl2 + (MULTIPLIER * atr[i]), hl2 - (MULTIPLIER * atr[i])
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
    print(">>> Bot Started with FIXED Resolution Strings <<<", flush=True)
    send_telegram_alert("🟢 Bot Finally Fixed: Error resolved, tracking real-time prices!")
    
    last_trends = {}
    last_times = {}

    while True:
        for p in PAIRS:
            try:
                candles = fetch_delta_candles(p["symbol"], p["resolution"], p["res_sec"])
                if candles:
                    c_time = candles[-2]['time']
                    c_trend, c_close = calculate_supertrend(candles)

                    if last_times.get(p["name"]) != c_time:
                        last_times[p["name"]] = c_time
                        t_str = "BUY (Green)" if c_trend else "SELL (Red)"
                        print(f"[{p['name']} {p['resolution']}] Close: {c_close} | Trend: {t_str}", flush=True)

                        p_trend = last_trends.get(p["name"])
                        if p_trend is not None and c_trend != p_trend:
                            msg = f"🟢 SUPERTREND BUY SIGNAL" if c_trend else f"🔴 SUPERTREND SELL SIGNAL"
                            msg += f"\nExchange: Delta\nPair: {p['symbol']}\nInterval: {p['resolution']}\nPrice: {c_close}"
                            send_telegram_alert(msg)
                            print(f">>> SIGNAL FIRED: {msg} <<<", flush=True)

                        last_trends[p["name"]] = c_trend
                else:
                    print(f"Waiting for {p['name']}...", flush=True)
            except Exception as e:
                print(f"Loop Error ({p['name']}): {e}", flush=True)
            
            time.sleep(2)
        
        time.sleep(15)

threading.Thread(target=run_bot, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
