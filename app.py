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

# Delta Contract symbols match exactly to chart
PAIRS = [
    {"symbol": "BTCUSD", "resolution": "5m"},
    {"symbol": "XRPUSD", "resolution": "10m"}
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

def get_delta_klines(symbol, resolution):
    now = int(time.time())
    res_seconds = 300 if resolution == "5m" else 600
    # Supertrend warmup ke liye 250 candles
    start = now - (250 * res_seconds)
    
    url = f"https://api.delta.exchange/v2/history/candles?resolution={resolution}&symbol={symbol}&start={start}&end={now}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json"
    }

    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            result = res.json().get('result', [])
            if result:
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
        print(f"Fetch Error ({symbol}): {e}", flush=True)

    return []

def calculate_wilder_atr(candles, period):
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

    # Wilder's Smoothing (RMA) - TradingView native formula
    atr = [0.0] * len(candles)
    if len(candles) <= period:
        return atr

    # First ATR is simple SMA
    atr[period - 1] = sum(tr_list[:period]) / period
    for i in range(period, len(candles)):
        atr[i] = (atr[i - 1] * (period - 1) + tr_list[i]) / period

    return atr

def get_supertrend_direction(candles):
    if len(candles) < PERIOD + 20:
        return None, None

    atr = calculate_wilder_atr(candles, PERIOD)
    n = len(candles)

    upperbands = [0.0] * n
    lowerbands = [0.0] * n
    trend = [True] * n

    start_idx = PERIOD
    for i in range(start_idx, n):
        h = candles[i]['high']
        l = candles[i]['low']
        c = candles[i]['close']
        hl2 = (h + l) / 2.0

        basic_ub = hl2 + (MULTIPLIER * atr[i])
        basic_lb = hl2 - (MULTIPLIER * atr[i])

        prev_ub = upperbands[i - 1]
        prev_lb = lowerbands[i - 1]

        # Upper band adjustment
        if basic_ub < prev_ub or candles[i - 1]['close'] > prev_ub:
            upperbands[i] = basic_ub
        else:
            upperbands[i] = prev_ub

        # Lower band adjustment
        if basic_lb > prev_lb or candles[i - 1]['close'] < prev_lb:
            lowerbands[i] = basic_lb
        else:
            lowerbands[i] = prev_lb

        # Direction flip
        if trend[i - 1]:
            if c < lowerbands[i]:
                trend[i] = False
            else:
                trend[i] = True
        else:
            if c > upperbands[i]:
                trend[i] = True
            else:
                trend[i] = False

    # Return completed/closed candle [-2]
    return trend[-2], candles[-2]['close']

def run_bot():
    print(">>> Native Delta Supertrend Bot Running <<<", flush=True)
    send_telegram_alert("🟢 Delta Supertrend Calibrated to Native TV Formula!")
    
    last_trends = {}
    last_candle_times = {}

    while True:
        for pair in PAIRS:
            sym = pair["symbol"]
            res = pair["resolution"]
            pair_key = f"{sym}_{res}"

            try:
                candles = get_delta_klines(sym, res)
                if candles and len(candles) >= PERIOD + 20:
                    current_candle_time = candles[-2]['time']
                    current_trend, current_close = get_supertrend_direction(candles)

                    if last_candle_times.get(pair_key) != current_candle_time:
                        last_candle_times[pair_key] = current_candle_time
                        trend_str = "BUY (Green)" if current_trend else "SELL (Red)"
                        print(f"[{sym} {res}] Close: {current_close} | Supertrend: {trend_str}", flush=True)

                        prev_trend = last_trends.get(pair_key)
                        if prev_trend is not None and current_trend != prev_trend:
                            if current_trend:
                                msg = f"🟢 SUPERTREND BUY SIGNAL\nExchange: Delta\nPair: {sym}\nInterval: {res}\nPrice: {current_close}"
                            else:
                                msg = f"🔴 SUPERTREND SELL SIGNAL\nExchange: Delta\nPair: {sym}\nInterval: {res}\nPrice: {current_close}"
                            print(f"Triggering Alert: {msg}", flush=True)
                            send_telegram_alert(msg)

                        last_trends[pair_key] = current_trend
            except Exception as e:
                print(f"Loop error on {sym}: {e}", flush=True)

            time.sleep(2)

        time.sleep(20)

threading.Thread(target=run_bot, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
