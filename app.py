import time
import threading
import requests
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "Delta Exchange Supertrend Multi-Bot is Running 24/7!"

# ================= CONFIGURATION =================
TELEGRAM_BOT_TOKEN = "9052629155:AAHWJhy0xfQUWH5s2WSIe--Z7fT_mXYUU1I"
CHANNEL_ID = "@theshopcare"
PERIOD = 10
MULTIPLIER = 3.0

PAIRS = [
    {"name": "BTC", "delta_symbol": "BTCUSD", "binance_symbol": "BTCUSDT", "resolution": "5m", "res_sec": 300},
    {"name": "XRP", "delta_symbol": "XRPUSD", "binance_symbol": "XRPUSDT", "resolution": "10m", "res_sec": 600}
]
# =================================================

def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHANNEL_ID, "text": message}
    try:
        res = requests.post(url, json=payload, timeout=10)
        print(f"Telegram alert sent: {res.status_code}", flush=True)
    except Exception as e:
        print(f"Telegram error: {e}", flush=True)

def fetch_candles(pair):
    now = int(time.time())
    start = now - (150 * pair["res_sec"])
    headers = {"User-Agent": "Mozilla/5.0"}
    
    # 1. Primary: Delta Exchange
    try:
        url = f"https://api.delta.exchange/v2/history/candles?resolution={pair['resolution']}&symbol={pair['delta_symbol']}&start={start}&end={now}"
        r = requests.get(url, headers=headers, timeout=8).json()
        result = r.get("result", [])
        if result and len(result) >= PERIOD + 5:
            result = sorted(result, key=lambda x: x["time"])
            candles = [{"time": c["time"], "high": float(c["high"]), "low": float(c["low"]), "close": float(c["close"])} for c in result]
            return candles, "Delta"
    except Exception as e:
        print(f"Delta fetch err ({pair['name']}): {e}", flush=True)

    # 2. Backup: Binance API (agar Delta response delay kare)
    try:
        binance_interval = "5m" if pair["resolution"] == "5m" else "15m"
        url = f"https://api.binance.com/api/v3/klines?symbol={pair['binance_symbol']}&interval={binance_interval}&limit=120"
        r = requests.get(url, timeout=8).json()
        if isinstance(r, list) and len(r) >= PERIOD + 5:
            candles = [{"time": int(c[0]/1000), "high": float(c[2]), "low": float(c[3]), "close": float(c[4])} for c in r]
            return candles, "Binance-Mirror"
    except Exception as e:
        print(f"Backup fetch err ({pair['name']}): {e}", flush=True)

    return [], "None"

def calculate_supertrend(candles):
    if len(candles) < PERIOD + 2:
        return None, None

    # True Range (TR)
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

    # Wilder's RMA (TradingView Exact ATR)
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

    # Completed candle (-2)
    return trend[-2], candles[-2]['close']

def run_bot():
    print(">>> Supertrend Background Worker Started <<<", flush=True)
    send_telegram_alert("🟢 Delta Supertrend Engine Active & Watching Candles!")
    
    last_trends = {}
    last_candle_times = {}

    while True:
        for pair in PAIRS:
            pair_key = pair["name"]
            try:
                candles, source = fetch_candles(pair)
                if candles:
                    current_candle_time = candles[-2]['time']
                    current_trend, current_close = calculate_supertrend(candles)

                    if last_candle_times.get(pair_key) != current_candle_time:
                        last_candle_times[pair_key] = current_candle_time
                        trend_label = "BUY (Green)" if current_trend else "SELL (Red)"
                        print(f"[{pair_key} - {pair['resolution']}] Source: {source} | Price: {current_close} | Trend: {trend_label}", flush=True)

                        prev_trend = last_trends.get(pair_key)
                        if prev_trend is not None and current_trend != prev_trend:
                            if current_trend:
                                msg = f"🟢 SUPERTREND BUY SIGNAL\nAsset: {pair['name']}\nInterval: {pair['resolution']}\nPrice: {current_close}"
                            else:
                                msg = f"🔴 SUPERTREND SELL SIGNAL\nAsset: {pair['name']}\nInterval: {pair['resolution']}\nPrice: {current_close}"
                            
                            print(f">>> ALERT FIRED: {msg} <<<", flush=True)
                            send_telegram_alert(msg)

                        last_trends[pair_key] = current_trend
                else:
                    print(f"Warning: No data for {pair_key}", flush=True)
            except Exception as e:
                print(f"Loop error on {pair_key}: {e}", flush=True)

            time.sleep(2)

        time.sleep(20)

threading.Thread(target=run_bot, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
