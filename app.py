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
    {"name": "BTC", "symbol": "BTCUSD", "resolution": "5m", "res_sec": 300},
    {"name": "XRP", "symbol": "XRPUSD", "resolution": "10m", "res_sec": 600}
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

def fetch_delta_candles(symbol, resolution, res_sec):
    now = int(time.time())
    start = now - (150 * res_sec)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json"
    }

    symbol_variants = [symbol, symbol.replace("USD", "_USDT"), f"{symbol}T"]
    
    for sym in symbol_variants:
        try:
            url = f"https://api.delta.exchange/v2/history/candles?resolution={resolution}&symbol={sym}&start={start}&end={now}"
            r = requests.get(url, headers=headers, timeout=8)
            if r.status_code == 200:
                result = r.json().get("result", [])
                if result and len(result) >= PERIOD + 5:
                    result = sorted(result, key=lambda x: x["time"])
                    candles = [{
                        "time": c["time"],
                        "high": float(c["high"]),
                        "low": float(c["low"]),
                        "close": float(c["close"])
                    } for c in result]
                    return candles
        except Exception as e:
            pass

    # Global unblocked fallback
    try:
        bybit_res = "5" if resolution == "5m" else "15"
        bybit_sym = "BTCUSDT" if "BTC" in symbol else "XRPUSDT"
        url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={bybit_sym}&interval={bybit_res}&limit=120"
        r = requests.get(url, timeout=8).json()
        raw_list = r.get("result", {}).get("list", [])
        if raw_list and len(raw_list) >= PERIOD + 5:
            raw_list = sorted(raw_list, key=lambda x: int(x[0]))
            candles = [{
                "time": int(c[0]) // 1000,
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4])
            } for c in raw_list]
            return candles
    except Exception as e:
        pass

    return []

def calculate_supertrend(candles):
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
    print(">>> 24/7 Delta Supertrend Engine Online <<<", flush=True)
    send_telegram_alert("🟢 Delta Supertrend Bot Live & Active (BTC 5m & XRP 10m)!")
    
    last_trends = {}
    last_candle_times = {}

    while True:
        for pair in PAIRS:
            pair_name = pair["name"]
            sym = pair["symbol"]
            res = pair["resolution"]
            res_sec = pair["res_sec"]

            try:
                candles = fetch_delta_candles(sym, res, res_sec)
                if candles:
                    current_candle_time = candles[-2]['time']
                    current_trend, current_close = calculate_supertrend(candles)

                    if last_candle_times.get(pair_name) != current_candle_time:
                        last_candle_times[pair_name] = current_candle_time
                        trend_label = "BUY (Green)" if current_trend else "SELL (Red)"
                        print(f"[{pair_name} {res}] Close: {current_close} | Trend: {trend_label}", flush=True)

                        prev_trend = last_trends.get(pair_name)
                        if prev_trend is not None and current_trend != prev_trend:
                            if current_trend:
                                msg = f"🟢 SUPERTREND BUY SIGNAL\nExchange: Delta\nPair: {sym}\nInterval: {res}\nPrice: {current_close}"
                            else:
                                msg = f"🔴 SUPERTREND SELL SIGNAL\nExchange: Delta\nPair: {sym}\nInterval: {res}\nPrice: {current_close}"
                            
                            print(f">>> SIGNAL SENT: {msg} <<<", flush=True)
                            send_telegram_alert(msg)

                        last_trends[pair_name] = current_trend
                else:
                    print(f"Waiting for candles on {pair_name}...", flush=True)

            except Exception as e:
                print(f"Loop error on {pair_name}: {e}", flush=True)

            time.sleep(2)

        time.sleep(20)

threading.Thread(target=run_bot, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
