import os, time, sys, json, hmac, hashlib, threading
from datetime import datetime
import requests
from dotenv import load_dotenv
from flask import Flask

load_dotenv()
app = Flask(__name__)

@app.route('/')
def home():
    return "Delta Live Auto-Trading Bot is Running 24/7!"

# ================= CONFIGURATION =================
BASE = os.getenv("DELTA_BASE_URL", "https://api.india.delta.exchange").rstrip("/")
CANDLE_BASE = os.getenv("CANDLE_BASE_URL", "https://api.india.delta.exchange").rstrip("/")
API_KEY = os.getenv("DELTA_API_KEY", "").strip()
API_SECRET = os.getenv("DELTA_API_SECRET", "").strip()

SYMBOLS = [x.strip().upper() for x in os.getenv("SYMBOLS", "BTCUSD,ETHUSD,SOLUSD,XRPUSD").split(",") if x.strip()]
TIMEFRAME = os.getenv("TIMEFRAME", "5m").strip().lower()
PERIOD = int(os.getenv("ATR_PERIOD", "10"))
MULT = float(os.getenv("ATR_MULTIPLIER", "3"))

DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
ENABLE_LIVE_ORDERS = os.getenv("ENABLE_LIVE_ORDERS", "false").lower() == "true"
DEFAULT_ORDER_SIZE = int(os.getenv("ORDER_SIZE", "1"))

def parse_symbol_lots():
    lots = {}
    raw = os.getenv("SYMBOL_LOTS", "").strip()
    if raw:
        for item in raw.split(","):
            if ":" in item:
                k, v = item.split(":", 1)
                try:
                    lots[k.strip().upper()] = int(v.strip())
                except ValueError:
                    pass
    return lots

SYMBOL_LOTS_MAP = parse_symbol_lots()

def get_lot_size(symbol):
    return SYMBOL_LOTS_MAP.get(symbol.upper(), DEFAULT_ORDER_SIZE)

# ================= API FUNCTIONS =================
def api_get(path, params=None, base_url=None):
    url = (base_url or BASE) + path
    r = requests.get(url, params=params or {}, timeout=15)
    r.raise_for_status()
    d = r.json()
    if d.get("success") is False:
        raise RuntimeError(d)
    return d.get("result", [])

def api_private(method, path, payload=None, params=None):
    if not API_KEY or not API_SECRET:
        raise RuntimeError("DELTA_API_KEY ya DELTA_API_SECRET .env me nahi mila!")
    method = method.upper()
    ts = str(int(time.time()))
    query_string = "?" + "&".join(f"{k}={v}" for k, v in params.items()) if params else ""
    body_str = json.dumps(payload, separators=(",", ":")) if payload is not None else ""
    sig_data = method + ts + path + query_string + body_str
    signature = hmac.new(API_SECRET.encode("utf-8"), sig_data.encode("utf-8"), hashlib.sha256).hexdigest()

    headers = {
        "api-key": API_KEY,
        "timestamp": ts,
        "signature": signature,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "delta-supertrend-live-bot"
    }
    url = BASE + path + query_string
    r = requests.request(method, url, data=body_str if body_str else None, headers=headers, timeout=15)
    try:
        d = r.json()
    except Exception:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text}")
    if not r.ok or d.get("success") is False:
        raise RuntimeError(f"HTTP {r.status_code}: {d}")
    return d.get("result")

def get_tick_info(product):
    tick_str = str(product.get("tick_size", "0.01") or "0.01")
    return float(tick_str), max(0, len(tick_str.split(".")[1].rstrip("0"))) if "." in tick_str else 0

def format_price(product, price):
    tick_size, decimals = get_tick_info(product)
    rounded = round(round(float(price) / tick_size) * tick_size, decimals)
    return (f"{rounded:.{decimals}f}" if decimals > 0 else str(int(rounded))), rounded

def get_demo_ticker_prices(symbol):
    try:
        res = api_get(f"/v2/tickers/{symbol}", base_url=BASE)
        if isinstance(res, dict):
            last_p = float(res.get("close") or res.get("mark_price") or 0)
            mark_p = float(res.get("mark_price") or last_p or 0)
            quotes = res.get("quotes") or {}
            return {"last": last_p, "mark": mark_p, "bid": float(quotes.get("best_bid") or last_p), "ask": float(quotes.get("best_ask") or last_p)}
    except Exception:
        pass
    return {"last": 0.0, "mark": 0.0, "bid": 0.0, "ask": 0.0}

def build_10m_candles(rows_5m, now_ts):
    closed_5m = {int(x.get("time", 0)): x for x in rows_5m if int(x.get("time", 0)) + 300 <= now_ts}
    candles_10m = []
    bucket_starts = sorted(set((t // 600) * 600 for t in closed_5m.keys()))
    for b in bucket_starts:
        if b + 600 <= now_ts and b in closed_5m and (b + 300) in closed_5m:
            c1, c2 = closed_5m[b], closed_5m[b + 300]
            candles_10m.append({
                "time": b, "open": float(c1["open"]),
                "high": max(float(c1["high"]), float(c2["high"])),
                "low": min(float(c1["low"]), float(c2["low"])),
                "close": float(c2["close"]),
                "volume": float(c1.get("volume", 0) or 0) + float(c2.get("volume", 0) or 0)
            })
    return candles_10m

def supertrend(rows):
    if len(rows) < PERIOD + 3: return None
    h, l, c = [float(x["high"]) for x in rows], [float(x["low"]) for x in rows], [float(x["close"]) for x in rows]
    tr = [max(h[i] - l[i], abs(h[i] - (c[i - 1] if i else c[i])), abs(l[i] - (c[i - 1] if i else c[i]))) for i in range(len(c))]
    a, up, dn, trend = [None]*len(c), [None]*len(c), [None]*len(c), [None]*len(c)
    
    a[PERIOD - 1] = sum(tr[:PERIOD]) / PERIOD
    for i in range(PERIOD, len(c)): a[i] = (a[i - 1] * (PERIOD - 1) + tr[i]) / PERIOD

    for i in range(PERIOD - 1, len(c)):
        mid = (h[i] + l[i]) / 2.0
        bu, bd = mid + MULT * a[i], mid - MULT * a[i]
        if i == PERIOD - 1:
            up[i], dn[i], trend[i] = bu, bd, 1
            continue
        up[i] = bu if (bu < up[i - 1] or c[i - 1] > up[i - 1]) else up[i - 1]
        dn[i] = bd if (bd > dn[i - 1] or c[i - 1] < dn[i - 1]) else dn[i - 1]
        trend[i] = (1 if c[i] > up[i - 1] else -1) if trend[i - 1] == -1 else (-1 if c[i] < dn[i - 1] else 1)

    i = len(c) - 1
    signal = "BUY" if (trend[i - 1] == -1 and trend[i] == 1) else ("SELL" if (trend[i - 1] == 1 and trend[i] == -1) else None)
    return {"signal": signal, "trend": "UP" if trend[i] == 1 else "DOWN", "close": round(c[i], 4), 
            "stop": round(dn[i] if trend[i] == 1 else up[i], 4), "atr": round(a[i], 4), "candle": int(rows[i].get("time", 0))}

# ================= TRADING LOGIC =================
def get_open_positions_map():
    pos_map = {}
    try:
        for pos in (api_private("GET", "/v2/positions/margined") or []):
            size = int(float(pos.get("size") or 0))
            if size != 0: pos_map[int(pos.get("product_id") or 0)] = size
    except Exception: pass
    return pos_map

def get_symbol_open_orders(pid):
    try: return [o for o in (api_private("GET", "/v2/orders", params={"product_ids": str(pid), "states": "open,pending"}) or []) if int(o.get("product_id") or 0) == int(pid)]
    except Exception: return []

def close_existing_position(product, current_size):
    pid = int(product["id"])
    if current_size == 0: return
    for oo in get_symbol_open_orders(pid):
        try: api_private("DELETE", "/v2/orders", {"id": oo["id"], "product_id": pid})
        except Exception: pass
    try:
        api_private("POST", "/v2/orders", {"product_id": pid, "size": abs(int(current_size)), "side": "sell" if current_size > 0 else "buy", "order_type": "market_order", "reduce_only": True})
        time.sleep(0.5)
    except Exception: pass

def validate_stop_price(product, pos_side, desired_stop, atr_val=0.0):
    sym, (tick_size, _) = str(product.get("symbol", "")), get_tick_info(product)
    ticker = get_demo_ticker_prices(sym)
    ref_price = max(ticker["last"], ticker["mark"]) if pos_side == "sell" else (ticker["last"] or ticker["mark"])
    safe_distance = max(float(atr_val) * MULT, ref_price * 0.005, 5 * tick_size)
    
    if ref_price > 0:
        if pos_side == "buy" and desired_stop >= ref_price - (2 * tick_size): desired_stop = ref_price - safe_distance
        elif pos_side == "sell" and desired_stop <= ref_price + (2 * tick_size): desired_stop = ref_price + safe_distance
    return format_price(product, desired_stop)

def ensure_or_trail_sl(product, pos_side, pos_size, supertrend_stop, atr_val=0.0):
    pid, sym, close_side, (tick_size, _) = int(product["id"]), product.get("symbol"), "sell" if pos_side == "buy" else "buy", get_tick_info(product)
    new_stop_str, new_stop_val = validate_stop_price(product, pos_side, supertrend_stop, atr_val)
    sl_orders = [o for o in get_symbol_open_orders(pid) if str(o.get("side", "")).lower() == close_side and (str(o.get("stop_order_type", "")).lower() == "stop_loss_order" or o.get("stop_price") is not None)]
    
    if len(sl_orders) > 1:
        for extra in sl_orders[1:]:
            try: api_private("DELETE", "/v2/orders", {"id": extra["id"], "product_id": pid})
            except Exception: pass

    if sl_orders:
        sl_order, old_stop, old_size = sl_orders[0], float(sl_orders[0].get("stop_price") or 0), int(sl_orders[0].get("size") or 0)
        should_trail = (pos_side == "buy" and new_stop_val >= old_stop + tick_size) or (pos_side == "sell" and (old_stop == 0 or new_stop_val <= old_stop - tick_size))
        if should_trail or (old_size != int(pos_size)):
            target_stop_str = new_stop_str if should_trail else format_price(product, old_stop)[0]
            try:
                api_private("PUT", "/v2/orders", {"id": sl_order["id"], "product_id": pid, "size": int(pos_size), "stop_price": target_stop_str})
                return f"TRAILED SL {sym}: {old_stop} -> {target_stop_str}"
            except Exception:
                try:
                    res = api_private("POST", "/v2/orders", {"product_id": pid, "size": int(pos_size), "side": close_side, "order_type": "market_order", "stop_order_type": "stop_loss_order", "stop_price": target_stop_str, "stop_trigger_method": "last_traded_price", "reduce_only": True})
                    try: api_private("DELETE", "/v2/orders", {"id": sl_order["id"], "product_id": pid})
                    except Exception: pass
                    return f"REPLACED SL {sym}: {old_stop} -> {target_stop_str}"
                except Exception as e2: return f"SL Trail Error ({sym}): {e2}"
        return f"SL OK {sym}: active @ {old_stop}"
    else:
        try:
            api_private("POST", "/v2/orders", {"product_id": pid, "size": int(pos_size), "side": close_side, "order_type": "market_order", "stop_order_type": "stop_loss_order", "stop_price": new_stop_str, "stop_trigger_method": "last_traded_price", "reduce_only": True})
            return f"CREATED MISSING SL for {sym} @ {new_stop_str}"
        except Exception as e: return f"SL Create Error ({sym}): {e}"

def place_demo_order(product, signal, candle_close, stop_price, atr_val=0.0):
    pid, sym, desired_lots, (tick_size, _) = int(product["id"]), str(product.get("symbol", "")).upper(), get_lot_size(product.get("symbol", "")), get_tick_info(product)
    curr_size = get_open_positions_map().get(pid, 0)
    curr_side = "BUY" if curr_size > 0 else ("SELL" if curr_size < 0 else None)

    if curr_side == signal: return f"SKIPPED (Already {signal}) | " + ensure_or_trail_sl(product, signal.lower(), abs(curr_size), float(stop_price), atr_val)
    if curr_side and curr_side != signal: close_existing_position(product, curr_size)

    side, ticker = "buy" if signal == "BUY" else "sell", get_demo_ticker_prices(sym)
    best_limit_px = (ticker["ask"] if ticker["ask"] > 0 else float(candle_close)) + (2 * tick_size) if side == "buy" else (ticker["bid"] if ticker["bid"] > 0 else float(candle_close)) - (2 * tick_size)
    limit_str, _ = format_price(product, best_limit_px)

    entry_res = api_private("POST", "/v2/orders", {"product_id": pid, "size": int(desired_lots), "side": side, "order_type": "limit_order", "limit_price": limit_str, "time_in_force": "gtc"})
    time.sleep(1.5)
    
    for oo in get_symbol_open_orders(pid):
        if oo.get("id") == entry_res.get("id"):
            unfilled = int(oo.get("unfilled_size") or desired_lots)
            try:
                api_private("DELETE", "/v2/orders", {"id": oo["id"], "product_id": pid})
                if unfilled > 0: api_private("POST", "/v2/orders", {"product_id": pid, "size": unfilled, "side": side, "order_type": "market_order"})
            except Exception: pass

    actual_size = abs(get_open_positions_map().get(pid, desired_lots)) or desired_lots
    return f"Entry ({side.upper()} {actual_size} lots) | " + ensure_or_trail_sl(product, side, actual_size, float(stop_price), atr_val)

def telegram(msg):
    t, chat = os.getenv("TELEGRAM_BOT_TOKEN", "").strip(), os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if t and chat:
        try: requests.post(f"https://api.telegram.org/bot{t}/sendMessage", data={"chat_id": chat, "text": msg}, timeout=10)
        except Exception: pass

# ================= BACKGROUND WORKER LOOP =================
def trading_loop():
    print(f"=== LIVE AUTO-TRADING BOT STARTED ({TIMEFRAME.upper()}) ===", flush=True)
    products = api_get("/v2/products", base_url=BASE)
    mapped = {}
    for s in SYMBOLS:
        matches = [p for p in products if (str(p.get("symbol", "")).upper() == s or s.replace("USD", "") in str(p.get("symbol", "")).upper()) and "perp" in str(p.get("contract_type", "")).lower()]
        if len(matches) == 1: mapped[s] = matches[0]

    sent = set()
    while True:
        latest_st = {}
        for s, p in mapped.items():
            try:
                now = int(time.time())
                try: rows_5m = api_get("/v2/history/candles", {"resolution": "5m", "symbol": str(p["symbol"]), "start": now - 60*5*200, "end": now}, base_url=CANDLE_BASE)
                except Exception: rows_5m = api_get("/v2/history/candles", {"resolution": "5m", "symbol": str(p["symbol"]), "start": now - 60*5*200, "end": now}, base_url=BASE)
                
                rows_5m = sorted(rows_5m, key=lambda x: int(x.get("time", 0)))
                candles = build_10m_candles(rows_5m, now) if TIMEFRAME == "10m" else [x for x in rows_5m if int(x.get("time", 0)) + 300 <= now]
                out = supertrend(candles)

                if out:
                    latest_st[s] = out
                    k = (s, out["candle"])
                    if out["signal"] and k not in sent:
                        sent.add(k)
                        msg = f"[LIVE {TIMEFRAME.upper()} SIGNAL] {s} {out['signal']} | Price={out['close']} | SL={out['stop']}"
                        if (not DRY_RUN) and ENABLE_LIVE_ORDERS:
                            try: msg += f"\n{place_demo_order(p, out['signal'], out['close'], out['stop'], out.get('atr', 0.0))}"
                            except Exception as oe: msg += f"\nError: {oe}"
                        telegram(msg)
            except Exception as e:
                print(f"Error checking {s}: {e}", flush=True)

        if (not DRY_RUN) and ENABLE_LIVE_ORDERS and latest_st:
            pos_map = get_open_positions_map()
            for sym, prod in mapped.items():
                pid = int(prod["id"])
                if pid in pos_map and sym in latest_supertrend:
                    st, size = latest_supertrend[sym], pos_map[pid]
                    res_msg = ensure_or_trail_sl(prod, "buy" if size > 0 else "sell", abs(size), st["stop"], st.get("atr", 0.0))
                    if "TRAILED SL" in res_msg or "REPLACED SL" in res_msg or "CREATED MISSING SL" in res_msg:
                        telegram(f"[SL UPDATE] {sym}\n{res_msg}")

        time.sleep(max(30, int(os.getenv("POLL_SECONDS", "60"))))

if __name__ == "__main__":
    t = threading.Thread(target=trading_loop)
    t.daemon = True
    t.start()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
