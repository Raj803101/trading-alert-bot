import time
import threading
import requests
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "Supertrend Bot is Running 24/7!"

# ================= CONFIGURATION =================
TELEGRAM_BOT_TOKEN = "9052629155:AAHWJhy0xfQUWH5s2WSIe--Z7fT_mXYUU1I"
CHANNEL_ID = "@theshopcare"
SYMBOL = "BTCUSDT"
INTERVAL = "5m"
PERIOD = 10
MULTIPLIER = 3.0
# =================================================

def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHANNEL_ID, "text": message}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

def get_klines():
    url = f"https://api.binance.com/api/v3/klines?symbol={SYMBOL}&interval={INTERVAL}&limit=100"
    res = requests.get(url, timeout=10).json()
    candles = []
    for k in res:
        candles.append({
            'high': float(k[2]),
            'low': float(k[3]),
            'close': float(k[4])
        })
    return candles

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
    print("Bot loop started...")
    send_telegram_alert("🚀 Supertrend Bot Hosted on Render Successfully!")
    last_trend = None

    while True:
        try:
            candles = get_klines()
            current_trend, current_close = get_supertrend_direction(candles)

            if current_trend is not None:
                if last_trend is not None and current_trend != last_trend:
                    if current_trend:
                        msg = f"🟢 SUPERTREND BUY SIGNAL\nPair: {SYMBOL}\nInterval: {INTERVAL}\nPrice: {current_close}"
                    else:
                        msg = f"🔴 SUPERTREND SELL SIGNAL\nPair: {SYMBOL}\nInterval: {INTERVAL}\nPrice: {current_close}"
                    send_telegram_alert(msg)

                last_trend = current_trend
        except Exception as e:
            print(f"Loop error: {e}")

        time.sleep(15)

threading.Thread(target=run_bot, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
