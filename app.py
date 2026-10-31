import time
import threading
import requests
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "Delta Exchange Supertrend Bot is Running 24/7!"

# ================= CONFIGURATION =================
TELEGRAM_BOT_TOKEN = "9052629155:AAHWJhy0xfQUWH5s2WSIe--Z7fT_mXYUU1I"
CHANNEL_ID = "@theshopcare"
SYMBOL = "BTCUSDT"       # Delta Exchange standard perpetual symbol
RESOLUTION = "5m"       # 5m timeframe
PERIOD = 10
MULTIPLIER = 3.0
# =================================================

def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHANNEL_ID, "text": message}
    try:
        res = requests.post(url, json=payload, timeout=10)
        print(f"Telegram sent: {res.status_code}", flush=True)
    except Exception as e:
        print(f"Telegram error: {e}", flush=True)

def get_delta_klines():
    now = int(time.time())
    start = now - (120 * 300)
    
    # Delta Exchange public candles endpoint with fallback
    urls = [
        f"https://api.delta.exchange/v2/history/candles?resolution={RESOLUTION}&symbol={SYMBOL}&start={start}&end={now}",
        f"https://api.delta.exchange/v2/history/candles?resolution={RESOLUTION}&symbol=BTCUSD&start={start}&end={now}"
    ]
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json"
    }

    for url in urls:
        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                result = data.get('result', [])
                if result and len(result) > 0:
                    # Time ke hisab se sort karein
                    result = sorted(result, key=lambda x: x['time'])
                    candles = []
                    for c in result:
                        candles.append({
                            'time': c.get('time'),
                            'high': float(c['high']),
                            'low': float(c['low']),
                            'close': float(c['close'])
                        })
                    return candles
        except Exception as e:
            print(f"API Fetch Error: {e}", flush=True)

    return []

def get_supertrend_direction(candles):
    if len(candles) < PERIOD + 2:
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

    atr_list = [0.0] * len(candles)
    for i in range(PERIOD - 1, len(candles)):
        atr_list[i] = sum(tr_list[i - PERIOD + 1:i + 1]) / PERIOD

    upperbands = [0.0] * len(candles)
    lowerbands = [0.0] * len(candles)
    trend = [True] * len(candles)

    start_idx = PERIOD - 1
    for i in range(start_idx, len(candles)):
        h = candles[i]['high']
        l = candles[i]['low']
        c = candles[i]['close']
        atr = atr_list[i]
        hl2 = (h + l) / 2.0

        ub = hl2 + (MULTIPLIER * atr)
        lb = hl2 - (MULTIPLIER * atr)

        if i == start_idx:
            upperbands[i] = ub
            lowerbands[i] = lb
            trend[i] = True
            continue

        prev_ub = upperbands[i - 1]
        prev_lb = lowerbands[i - 1]

        if c > prev_ub:
            upperbands[i] = ub
        else:
            upperbands[i] = min(ub, prev_ub)

        if c < prev_lb:
            lowerbands[i] = lb
        else:
            lowerbands[i] = max(lb, prev_lb)

        if c > prev_ub:
            trend[i] = True
        elif c < prev_lb:
            trend[i] = False
        else:
            trend[i] = trend[i - 1]
            if trend[i] and lowerbands[i] < prev_lb:
                lowerbands[i] = prev_lb
            if not trend[i] and upperbands[i] > prev_ub:
                upperbands[i] = prev_ub

    return trend[-2], candles[-2]['close']

def run_bot():
    print(">>> Delta Bot Loop Running <<<", flush=True)
    send_telegram_alert("🟢 Delta Bot Engine Updated & Active!")
    
    last_trend = None
    last_candle_time = None

    while True:
        try:
            candles = get_delta_klines()
            if candles and len(candles) >= PERIOD + 2:
                current_candle_time = candles[-2]['time']
                current_trend, current_close = get_supertrend_direction(candles)

                if current_candle_time != last_candle_time:
                    last_candle_time = current_candle_time
                    trend_str = "BUY (Green)" if current_trend else "SELL (Red)"
                    print(f"[Candle Checked] Close: {current_close} | Supertrend: {trend_str}", flush=True)

                    if last_trend is not None and current_trend != last_trend:
                        if current_trend:
                            msg = f"🟢 SUPERTREND BUY SIGNAL\nExchange: Delta\nPair: BTCUSDT\nInterval: {RESOLUTION}\nPrice: {current_close}"
                        else:
                            msg = f"🔴 SUPERTREND SELL SIGNAL\nExchange: Delta\nPair: BTCUSDT\nInterval: {RESOLUTION}\nPrice: {current_close}"
                        print(f"Triggering Alert: {msg}", flush=True)
                        send_telegram_alert(msg)

                    last_trend = current_trend
            else:
                print("Warning: Delta candles not received properly.", flush=True)

        except Exception as e:
            print(f"Loop error: {e}", flush=True)

        time.sleep(20)

threading.Thread(target=run_bot, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
