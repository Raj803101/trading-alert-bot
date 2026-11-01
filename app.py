import time
import threading
import requests
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "Supertrend Multi-Bot is Running 24/7!"

# ================= CONFIGURATION =================
TELEGRAM_BOT_TOKEN = "9052629155:AAHWJhy0xfQUWH5s2WSIe--Z7fT_mXYUU1I"
CHANNEL_ID = "@theshopcare"
PERIOD = 10
MULTIPLIER = 3.0

PAIRS = [
    {"name": "BTC", "symbol": "BTCUSDT", "interval": "5m"},
    {"name": "XRP", "symbol": "XRPUSDT", "interval": "15m"}  # Binance me standard 5m/15m hota hai, XRP ke liye 15m best sync hai
]
# =================================================

def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHANNEL_ID, "text": message}
    try:
        res = requests.post(url, json=payload, timeout=10)
        print(f"Telegram sent: {res.status_code}", flush=True)
    except Exception as e:
        print(f"Telegram error: {e}", flush=True)

def fetch_candles(symbol, interval):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit=120"
    try:
        res = requests.get(url, timeout=10).json()
        if isinstance(res, list) and len(res) >= PERIOD + 5:
            candles = []
            for c in res:
                candles.append({
                    'time': c[0],
                    'high': float(c[2]),
                    'low': float(c[3]),
                    'close': float(c[4])
                })
            return candles
    except Exception as e:
        print(f"Fetch error on {symbol}: {e}", flush=True)
    return []

def calculate_supertrend(candles):
    if len(candles) < PERIOD + 2:
        return None, None

    # 1. True Range (TR)
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

    # 2. Wilder's RMA (TradingView native ATR formula)
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

    # Return completed/closed candle [-2]
    return trend[-2], candles[-2]['close']

def run_bot():
    print(">>> Real-Time Supertrend Engine Started <<<", flush=True)
    send_telegram_alert("🟢 Supertrend Real-Time Feed Active (BTC 5m & XRP)!")
    
    last_trends = {}
    last_candle_times = {}

    while True:
        for pair in PAIRS:
            pair_name = pair["name"]
            sym = pair["symbol"]
            interval = pair["interval"]

            try:
                candles = fetch_candles(sym, interval)
                if candles:
                    current_candle_time = candles[-2]['time']
                    current_trend, current_close = calculate_supertrend(candles)

                    if last_candle_times.get(pair_name) != current_candle_time:
                        last_candle_times[pair_name] = current_candle_time
                        trend_label = "BUY (Green)" if current_trend else "SELL (Red)"
                        print(f"[{pair_name} - {interval}] Close: {current_close} | Trend: {trend_label}", flush=True)

                        prev_trend = last_trends.get(pair_name)
                        if prev_trend is not None and current_trend != prev_trend:
                            if current_trend:
                                msg = f"🟢 SUPERTREND BUY SIGNAL\nAsset: {pair_name}\nInterval: {interval}\nPrice: {current_close}"
                            else:
                                msg = f"🔴 SUPERTREND SELL SIGNAL\nAsset: {pair_name}\nInterval: {interval}\nPrice: {current_close}"
                            
                            print(f">>> SIGNAL SENT: {msg} <<<", flush=True)
                            send_telegram_alert(msg)

                        last_trends[pair_name] = current_trend
            except Exception as e:
                print(f"Error on {pair_name}: {e}", flush=True)

            time.sleep(2)

        time.sleep(20)

threading.Thread(target=run_bot, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
