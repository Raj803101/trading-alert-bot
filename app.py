import time
import threading
import requests
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "Delta Exchange Supertrend Pro is Running 24/7!"

# ================= CONFIGURATION =================
TELEGRAM_BOT_TOKEN = "9052629155:AAHWJhy0xfQUWH5s2WSIe--Z7fT_mXYUU1I"
CHANNEL_ID = "@theshopcare"
PERIOD = 10
MULTIPLIER = 3.0

PAIRS = [
    {"name": "BTC", "symbol": "BTCUSD", "resolution": "5", "res_sec": 300, "interval_label": "5m"},
    {"name": "XRP", "symbol": "XRPUSD", "resolution": "10", "res_sec": 600, "interval_label": "10m"}
]
# =================================================

def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHANNEL_ID, "text": message}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}", flush=True)

def fetch_delta_candles(symbol, resolution, res_sec):
    now = int(time.time())
    # FLAW FIXED: Delta API allow maximum 300-500 candles per request. We ask for 200.
    start = now - (200 * res_sec)
    
    url = f"https://api.delta.exchange/v2/history/candles?resolution={resolution}&symbol={symbol}&start={start}&end={now}"
    headers = {"User-Agent": "Mozilla/5.0"}
    
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            result = r.json().get("result", [])
            # Changed safety check to 50 candles to ensure loop runs smoothly
            if result and len(result) >= 50:
                result = sorted(result, key=lambda x: int(x["time"]))
                candles = [{
                    "time": int(c["time"]),
                    "high": float(c["high"]),
                    "low": float(c["low"]),
                    "close": float(c["close"])
                } for c in result]
                return candles
    except Exception as e:
        print(f"Delta API error ({symbol}): {e}", flush=True)
        
    return []

def calculate_supertrend(candles):
    if len(candles) < PERIOD + 20:
        return None, None

    tr_list = []
    for i in range(len(candles)):
        h = candles[i]['high']
        l = candles[i]['low']
        if i == 0:
            tr_list.append(h - l)
        else:
            prev_close = candles[i - 1]['close']
            tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
            tr_list.append(tr)

    # Wilder's Smoothing Math
    atr = [0.0] * len(candles)
    atr[PERIOD - 1] = sum(tr_list[:PERIOD]) / PERIOD
    for i in range(PERIOD, len(candles)):
        atr[i] = (atr[i - 1] * (PERIOD - 1) + tr_list[i]) / PERIOD

    n = len(candles)
    upperbands = [0.0] * n
    lowerbands = [0.0] * n
    trend = [True] * n

    for i in range(PERIOD, n):
        h = candles[i]['high']
        l = candles[i]['low']
        c = candles[i]['close']
        hl2 = (h + l) / 2.0

        ub = hl2 + (MULTIPLIER * atr[i])
        lb = hl2 - (MULTIPLIER * atr[i])

        prev_ub = upperbands[i - 1]
        prev_lb = lowerbands[i - 1]

        if ub < prev_ub or candles[i - 1]['close'] > prev_ub:
            upperbands[i] = ub
        else:
            upperbands[i] = prev_ub

        if lb > prev_lb or candles[i - 1]['close'] < prev_lb:
            lowerbands[i] = lb
        else:
            lowerbands[i] = prev_lb

        if trend[i - 1]:
            trend[i] = False if c < lowerbands[i] else True
        else:
            trend[i] = True if c > upperbands[i] else False

    return trend[-2], candles[-2]['close']

def run_bot():
    print(">>> Supertrend Bot (Fixed API Limit) Started <<<", flush=True)
    send_telegram_alert("🟢 Bot Fixed: Real-time signals now active for BTC & XRP!")
    
    last_trends = {}
    last_candle_times = {}

    while True:
        for pair in PAIRS:
            name = pair["name"]
            sym = pair["symbol"]
            res = pair["resolution"]
            res_sec = pair["res_sec"]
            label = pair["interval_label"]

            try:
                candles = fetch_delta_candles(sym, res, res_sec)
                if candles:
                    current_candle_time = candles[-2]['time']
                    current_trend, current_close = calculate_supertrend(candles)

                    if last_candle_times.get(name) != current_candle_time:
                        last_candle_times[name] = current_candle_time
                        trend_text = "BUY (Green)" if current_trend else "SELL (Red)"
                        print(f"[{name} {label}] Closed: {current_close} | Trend: {trend_text}", flush=True)

                        prev_trend = last_trends.get(name)
                        if prev_trend is not None and current_trend != prev_trend:
                            if current_trend:
                                msg = f"🟢 SUPERTREND BUY SIGNAL\nExchange: Delta\nPair: {sym}\nInterval: {label}\nPrice: {current_close}"
                            else:
                                msg = f"🔴 SUPERTREND SELL SIGNAL\nExchange: Delta\nPair: {sym}\nInterval: {label}\nPrice: {current_close}"
                            
                            print(f">>> SIGNAL FIRED: {msg} <<<", flush=True)
                            send_telegram_alert(msg)

                        last_trends[name] = current_trend
                else:
                    print(f"Waiting for candles on {name}...", flush=True)
            except Exception as e:
                print(f"Loop Error ({name}): {e}", flush=True)

            time.sleep(2)

        time.sleep(15)

threading.Thread(target=run_bot, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
