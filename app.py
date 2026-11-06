import os, time, sys, json, hmac, hashlib, threading
from datetime import datetime
import requests
from flask import Flask
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURATION ---
# Live Account ke liye: https://api.india.delta.exchange
# Demo Account ke liye: https://cdn-ind.testnet.deltaex.org
BASE = os.getenv("DELTA_BASE_URL", "https://api.india.delta.exchange").rstrip("/")
CANDLE_BASE = os.getenv("CANDLE_BASE_URL", "https://api.india.delta.exchange").rstrip("/")

API_KEY = os.getenv("DELTA_API_KEY", "").strip()
API_SECRET = os.getenv("DELTA_API_SECRET", "").strip()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "@theshopcare").strip()

TIMEFRAME = os.getenv("TIMEFRAME", "5m").strip().lower()
PERIOD = int(os.getenv("ATR_PERIOD", "10"))
MULT = float(os.getenv("ATR_MULTIPLIER", "3"))
POLL_SECONDS = max(30, int(os.getenv("POLL_SECONDS", "60")))

DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
ENABLE_ORDERS = os.getenv("ENABLE_TESTNET_ORDERS", "true").lower() == "true"
DEFAULT_ORDER_SIZE = int(os.getenv("ORDER_SIZE", "5"))

# Dynamic Watchlist & Lot Size Dictionary
# Default setup: BTCUSD=5, ETHUSD=10, SOLUSD=5, XRPUSD=20
WATCHLIST_LOCK = threading.Lock()
WATCHLIST = {
    "BTCUSD": 5,
    "ETHUSD": 10,
    "SOLUSD": 5,
    "XRPUSD": 20
}

def init_watchlist():
    raw_syms = [x.strip().upper() for x in os.getenv("SYMBOLS", "").split(",") if x.strip()]
    if raw_syms:
        WATCHLIST.clear()
        for s in raw_syms:
            WATCHLIST[s] = DEFAULT_ORDER_SIZE
        if "ETHUSD" in WATCHLIST:
            WATCHLIST["ETHUSD"] = 10
        if "XRPUSD" in WATCHLIST:
            WATCHLIST["XRPUSD"] = 20

    raw_lots = os.getenv("SYMBOL_LOTS", "").strip()
    if raw_lots:
        for item in raw_lots.split(","):
            if ":" in item:
                k, v = item.split(":", 1)
                try:
                    WATCHLIST[k.strip().upper()] = int(v.strip())
                except ValueError:
                    pass

init_watchlist()

LATEST_ST = {}
SENT_SIGNALS = set()


# --- DELTA API HELPERS ---

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
        raise RuntimeError("DELTA_API_KEY ya DELTA_API_SECRET set nahi hai!")
    method = method.upper()
    ts = str(int(time.time()))
    query_string = ""
    if params:
        query_string = "?" + "&".join(f"{k}={v}" for k, v in params.items())
    body_str = json.dumps(payload, separators=(",", ":")) if payload is not None else ""
    sig_data = method + ts + path + query_string + body_str
    signature = hmac.new(API_SECRET.encode("utf-8"), sig_data.encode("utf-8"), hashlib.sha256).hexdigest()

    headers = {
        "api-key": API_KEY,
        "timestamp": ts,
        "signature": signature,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "delta-supertrend-live-controller"
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
    tick_size = float(tick_str)
    decimals = max(0, len(tick_str.split(".")[1].rstrip("0"))) if "." in tick_str else 0
    return tick_size, decimals


def format_price(product, price):
    tick_size, decimals = get_tick_info(product)
    rounded = round(round(float(price) / tick_size) * tick_size, decimals)
    return (f"{rounded:.{decimals}f}" if decimals > 0 else str(int(rounded))), rounded


def get_ticker_prices(symbol):
    try:
        res = api_get(f"/v2/tickers/{symbol}", base_url=BASE)
        if isinstance(res, dict):
            last_p = float(res.get("close") or res.get("mark_price") or 0)
            mark_p = float(res.get("mark_price") or last_p or 0)
            quotes = res.get("quotes") or {}
            best_bid = float(quotes.get("best_bid") or last_p or 0)
            best_ask = float(quotes.get("best_ask") or last_p or 0)
            return {"last": last_p, "mark": mark_p, "bid": best_bid, "ask": best_ask}
    except Exception:
        pass
    return {"last": 0.0, "mark": 0.0, "bid": 0.0, "ask": 0.0}


# --- SUPERTREND CALCULATION ---

def build_10m_candles(rows_5m, now_ts):
    closed_5m = {int(x["time"]): x for x in rows_5m if int(x.get("time", 0)) + 300 <= now_ts}
    candles_10m = []
    bucket_starts = sorted(set((t // 600) * 600 for t in closed_5m.keys()))
    for b in bucket_starts:
        if b + 600 <= now_ts and (b in closed_5m) and ((b + 300) in closed_5m):
            c1, c2 = closed_5m[b], closed_5m[b + 300]
            candles_10m.append({
                "time": b,
                "open": float(c1["open"]),
                "high": max(float(c1["high"]), float(c2["high"])),
                "low": min(float(c1["low"]), float(c2["low"])),
                "close": float(c2["close"]),
                "volume": float(c1.get("volume", 0) or 0) + float(c2.get("volume", 0) or 0)
            })
    return candles_10m


def supertrend(rows):
    if len(rows) < PERIOD + 3:
        return None
    h = [float(x["high"]) for x in rows]
    l = [float(x["low"]) for x in rows]
    c = [float(x["close"]) for x in rows]

    tr = [
        max(h[i] - l[i], abs(h[i] - (c[i - 1] if i else c[i])), abs(l[i] - (c[i - 1] if i else c[i])))
        for i in range(len(c))
    ]

    a = [None] * len(c)
    a[PERIOD - 1] = sum(tr[:PERIOD]) / PERIOD
    for i in range(PERIOD, len(c)):
        a[i] = (a[i - 1] * (PERIOD - 1) + tr[i]) / PERIOD

    up, dn, trend = [None] * len(c), [None] * len(c), [None] * len(c)

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
    candle_ts = int(rows[i].get("time", 0))
    return {
        "signal": signal,
        "trend": "UP" if trend[i] == 1 else "DOWN",
        "close": round(c[i], 4),
        "stop": round(dn[i] if trend[i] == 1 else up[i], 4),
        "atr": round(a[i], 4),
        "candle": candle_ts,
        "candle_time": datetime.fromtimestamp(candle_ts).strftime("%H:%M")
    }


# --- ORDER EXECUTION & TRAILING SL ENGINE ---

def get_open_positions_map():
    pos_map = {}
    try:
        positions = api_private("GET", "/v2/positions/margined") or []
        for pos in positions:
            pid = int(pos.get("product_id") or 0)
            size = int(float(pos.get("size") or 0))
            if size != 0:
                pos_map[pid] = size
    except Exception as e:
        print("  >>> Position fetch error:", e, flush=True)
    return pos_map


def get_symbol_open_orders(pid):
    pid = int(pid)
    try:
        raw_orders = api_private("GET", "/v2/orders", params={"product_ids": str(pid), "states": "open,pending"}) or []
    except Exception:
        raw_orders = api_private("GET", "/v2/orders", params={"state": "open"}) or []
    return [o for o in raw_orders if int(o.get("product_id") or 0) == pid]


def cancel_all_open_orders(pid):
    for oo in get_symbol_open_orders(int(pid)):
        try:
            api_private("DELETE", "/v2/orders", {"id": oo["id"], "product_id": int(pid)})
        except Exception:
            pass


def close_existing_position(product, current_size):
    pid = int(product["id"])
    if current_size == 0:
        return
    close_side = "sell" if current_size > 0 else "buy"
    cancel_all_open_orders(pid)
    try:
        api_private("POST", "/v2/orders", {
            "product_id": pid,
            "size": abs(int(current_size)),
            "side": close_side,
            "order_type": "market_order",
            "reduce_only": True
        })
        time.sleep(0.5)
    except Exception as e:
        print(f"  >>> Close position error ({product.get('symbol')}):", e, flush=True)


def validate_stop_price(product, pos_side, desired_stop, atr_val=0.0):
    sym = str(product.get("symbol", ""))
    tick_size, _ = get_tick_info(product)
    ticker = get_ticker_prices(sym)
    ref_price = max(ticker["last"], ticker["mark"]) if pos_side == "sell" else (ticker["last"] or ticker["mark"])
    safe_distance = max(float(atr_val) * MULT, ref_price * 0.005, 5 * tick_size)

    if ref_price > 0:
        if pos_side == "buy" and desired_stop >= ref_price - (2 * tick_size):
            desired_stop = ref_price - safe_distance
        elif pos_side == "sell" and desired_stop <= ref_price + (2 * tick_size):
            desired_stop = ref_price + safe_distance
    return format_price(product, desired_stop)


def ensure_or_trail_sl(product, pos_side, pos_size, supertrend_stop, atr_val=0.0):
    pid = int(product["id"])
    sym = product.get("symbol")
    close_side = "sell" if pos_side == "buy" else "buy"
    tick_size, _ = get_tick_info(product)

    new_stop_str, new_stop_val = validate_stop_price(product, pos_side, supertrend_stop, atr_val)
    symbol_orders = get_symbol_open_orders(pid)
    sl_orders = [
        o for o in symbol_orders
        if str(o.get("side", "")).lower() == close_side and (
            str(o.get("stop_order_type", "")).lower() == "stop_loss_order" or o.get("stop_price") is not None
        )
    ]

    if len(sl_orders) > 1:
        for extra in sl_orders[1:]:
            try:
                api_private("DELETE", "/v2/orders", {"id": extra["id"], "product_id": pid})
            except Exception:
                pass

    if sl_orders:
        sl_order = sl_orders[0]
        old_stop = float(sl_order.get("stop_price") or 0)
        old_size = int(sl_order.get("size") or 0)

        should_trail = False
        if pos_side == "buy" and (new_stop_val >= old_stop + tick_size):
            should_trail = True
        elif pos_side == "sell" and (old_stop == 0 or new_stop_val <= old_stop - tick_size):
            should_trail = True

        size_mismatch = (old_size != int(pos_size))

        if should_trail or size_mismatch:
            target_stop_str = new_stop_str if should_trail else format_price(product, old_stop)[0]
            try:
                api_private("PUT", "/v2/orders", {
                    "id": sl_order["id"],
                    "product_id": pid,
                    "size": int(pos_size),
                    "stop_price": target_stop_str
                })
                msg = f"TRAILED SL {sym}: {old_stop} -> {target_stop_str} (Qty={pos_size})"
                print("  >>>", msg, flush=True)
                return msg
            except Exception:
                try:
                    res = api_private("POST", "/v2/orders", {
                        "product_id": pid,
                        "size": int(pos_size),
                        "side": close_side,
                        "order_type": "market_order",
                        "stop_order_type": "stop_loss_order",
                        "stop_price": target_stop_str,
                        "stop_trigger_method": "last_traded_price",
                        "reduce_only": True
                    })
                    try:
                        api_private("DELETE", "/v2/orders", {"id": sl_order["id"], "product_id": pid})
                    except Exception:
                        pass
                    msg = f"REPLACED SL {sym}: {old_stop} -> {target_stop_str} (ID={res.get('id')})"
                    print("  >>>", msg, flush=True)
                    return msg
                except Exception as e2:
                    return f"SL Trail Error ({sym}): {e2}"

        return f"SL OK {sym}: active @ {old_stop} (Qty={old_size})"
    else:
        try:
            res = api_private("POST", "/v2/orders", {
                "product_id": pid,
                "size": int(pos_size),
                "side": close_side,
                "order_type": "market_order",
                "stop_order_type": "stop_loss_order",
                "stop_price": new_stop_str,
                "stop_trigger_method": "last_traded_price",
                "reduce_only": True
            })
            msg = f"CREATED MISSING SL for {sym} @ {new_stop_str} (Qty={pos_size}, ID={res.get('id')})"
            print("  >>>", msg, flush=True)
            return msg
        except Exception as e:
            return f"SL Create Error ({sym} @ {new_stop_str}): {e}"


def execute_trade_order(product, signal, candle_close, stop_price, desired_lots, atr_val=0.0):
    pid = int(product["id"])
    sym = str(product.get("symbol", "")).upper()
    tick_size, _ = get_tick_info(product)

    pos_map = get_open_positions_map()
    curr_size = pos_map.get(pid, 0)
    curr_side = "BUY" if curr_size > 0 else ("SELL" if curr_size < 0 else None)

    if curr_side == signal:
        sl_info = ensure_or_trail_sl(product, signal.lower(), abs(curr_size), float(stop_price), atr_val)
        return f"SKIPPED (Already {signal} {abs(curr_size)} lots) | {sl_info}"

    if curr_side and curr_side != signal:
        close_existing_position(product, curr_size)

    side = "buy" if signal == "BUY" else "sell"
    ticker = get_ticker_prices(sym)
    if side == "buy":
        base_px = ticker["ask"] if ticker["ask"] > 0 else float(candle_close)
        best_limit_px = base_px + (2 * tick_size)
    else:
        base_px = ticker["bid"] if ticker["bid"] > 0 else float(candle_close)
        best_limit_px = base_px - (2 * tick_size)

    limit_str, _ = format_price(product, best_limit_px)

    entry_res = api_private("POST", "/v2/orders", {
        "product_id": pid,
        "size": int(desired_lots),
        "side": side,
        "order_type": "limit_order",
        "limit_price": limit_str,
        "time_in_force": "gtc"
    })

    time.sleep(1.5)
    for oo in get_symbol_open_orders(pid):
        if oo.get("id") == entry_res.get("id"):
            unfilled = int(oo.get("unfilled_size") or desired_lots)
            try:
                api_private("DELETE", "/v2/orders", {"id": oo["id"], "product_id": pid})
                if unfilled > 0:
                    api_private("POST", "/v2/orders", {
                        "product_id": pid,
                        "size": unfilled,
                        "side": side,
                        "order_type": "market_order"
                    })
            except Exception:
                pass

    updated_pos = get_open_positions_map()
    actual_size = abs(updated_pos.get(pid, desired_lots)) or desired_lots
    sl_status = ensure_or_trail_sl(product, side, actual_size, float(stop_price), atr_val)

    return f"Entry ID={entry_res.get('id')} ({side.upper()} {actual_size} lots @ Limit {limit_str}) | {sl_status}"


def sync_and_trail_open_positions(mapped_products, latest_supertrend):
    pos_map = get_open_positions_map()
    if not pos_map:
        return
    for sym, prod in mapped_products.items():
        pid = int(prod["id"])
        if pid in pos_map and sym in latest_supertrend:
            size = pos_map[pid]
            pos_side = "buy" if size > 0 else "sell"
            st = latest_supertrend[sym]
            res_msg = ensure_or_trail_sl(prod, pos_side, abs(size), st["stop"], st.get("atr", 0.0))
            if "TRAILED SL" in res_msg or "CREATED MISSING SL" in res_msg or "REPLACED SL" in res_msg:
                send_telegram(f"🔄 [SL UPDATE] {sym}\n{res_msg}")


# --- TELEGRAM BOT INTERACTIVE CONTROLLER ---

def send_telegram(msg, chat_id=None):
    target_chat = chat_id or TELEGRAM_CHAT_ID
    if TELEGRAM_TOKEN and target_chat:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data={"chat_id": target_chat, "text": msg},
                timeout=10
            )
        except Exception as e:
            print("Telegram send error:", e, flush=True)


def handle_telegram_command(text, reply_chat_id):
    parts = text.strip().split()
    if not parts:
        return
    cmd = parts[0].split("@")[0].lower()

    # 1. /add <SYMBOL> <QTY>
    if cmd == "/add":
        if len(parts) < 3:
            send_telegram("⚠️ Format: /add BTCUSD 5", reply_chat_id)
            return
        sym = parts[1].upper()
        try:
            qty = int(parts[2])
            if qty <= 0:
                raise ValueError()
            with WATCHLIST_LOCK:
                WATCHLIST[sym] = qty
            send_telegram(f"✅ Added/Updated: {sym} -> {qty} Lots", reply_chat_id)
        except ValueError:
            send_telegram("❌ Quantity number me honi chahiye. Example: /add SOLUSD 10", reply_chat_id)

    # 2. /lot <SYMBOL> <QTY> or /edit <SYMBOL> <QTY> (Adjust Quantity)
    elif cmd in ("/lot", "/edit", "/qty"):
        if len(parts) < 3:
            send_telegram("⚠️ Format: /lot ETHUSD 15", reply_chat_id)
            return
        sym = parts[1].upper()
        try:
            qty = int(parts[2])
            if qty <= 0:
                raise ValueError()
            with WATCHLIST_LOCK:
                WATCHLIST[sym] = qty
            send_telegram(f"✏️ Lot Size Adjusted: {sym} -> {qty} Lots", reply_chat_id)
        except ValueError:
            send_telegram("❌ Valid lot size daalein. Example: /lot XRPUSD 25", reply_chat_id)

    # 3. /remove <SYMBOL>
    elif cmd in ("/remove", "/delete", "/del"):
        if len(parts) < 2:
            send_telegram("⚠️ Format: /remove XRPUSD", reply_chat_id)
            return
        sym = parts[1].upper()
        with WATCHLIST_LOCK:
            if sym in WATCHLIST:
                del WATCHLIST[sym]
                send_telegram(f"🗑️ Removed {sym} from auto-trade watchlist.", reply_chat_id)
            else:
                send_telegram(f"⚠️ {sym} watchlist me nahi hai.", reply_chat_id)

    # 4. /list (Dashboard of Active Watchlist + Lots + Current Trend)
    elif cmd == "/list":
        with WATCHLIST_LOCK:
            current_list = dict(WATCHLIST)
        if not current_list:
            send_telegram("📭 Watchlist khali hai. /add BTCUSD 5 se add karein.", reply_chat_id)
            return
        lines = [f"📊 ACTIVE WATCHLIST ({TIMEFRAME.upper()}):"]
        for sym, qty in current_list.items():
            st = LATEST_ST.get(sym)
            if st:
                lines.append(f"• {sym} | Lots: {qty} | Trend: {st['trend']} | Price: {st['close']} | SL: {st['stop']}")
            else:
                lines.append(f"• {sym} | Lots: {qty} | Waiting for next scan...")
        send_telegram("\n".join(lines), reply_chat_id)

    # 5. /status (Wallet Balance + Open Positions + Active Trailing SL)
    elif cmd == "/status":
        lines = ["🏦 DELTA ACCOUNT STATUS:"]
        try:
            balances = api_private("GET", "/v2/wallet/balances") or []
            for b in balances:
                bal = float(b.get("balance") or 0)
                if bal > 0:
                    lines.append(f"💰 Wallet ({b.get('asset_symbol')}): {bal:.2f} (Avail: {float(b.get('available_balance') or 0):.2f})")
        except Exception as e:
            lines.append(f"⚠️ Wallet fetch error: {e}")

        try:
            positions = api_private("GET", "/v2/positions/margined") or []
            open_pos = [p for p in positions if int(float(p.get("size") or 0)) != 0]
            if not open_pos:
                lines.append("\n📭 Open Positions: None")
            else:
                lines.append(f"\n📈 Open Positions ({len(open_pos)}):")
                for p in open_pos:
                    pid = int(p.get("product_id") or 0)
                    sym = p.get("product_symbol") or f"ID-{pid}"
                    size = int(float(p.get("size") or 0))
                    side_str = "BUY" if size > 0 else "SELL"
                    entry = float(p.get("entry_price") or 0)
                    orders = get_symbol_open_orders(pid)
                    sl_px = "None"
                    for o in orders:
                        if o.get("stop_price") is not None:
                            sl_px = o.get("stop_price")
                            break
                    lines.append(f"• {sym}: {side_str} {abs(size)} lots @ {entry} | Active SL: {sl_px}")
        except Exception as e:
            lines.append(f"⚠️ Position fetch error: {e}")

        send_telegram("\n".join(lines), reply_chat_id)

    # 6. /help or /start
    elif cmd in ("/help", "/start"):
        help_txt = (
            "🤖 Bot Controller Commands:\n"
            "• /add BTCUSD 5 - Add coin with lots\n"
            "• /lot ETHUSD 15 - Edit/Adjust lot size\n"
            "• /remove XRPUSD - Remove coin\n"
            "• /list - Active watchlist & trends\n"
            "• /status - Balance, Positions & Trailing SL"
        )
        send_telegram(help_txt, reply_chat_id)


def telegram_Command_listener():
    print("Telegram Command Controller Started...", flush=True)
    # Delete any old webhook so getUpdates polling works smoothly
    try:
        requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook", timeout=10)
    except Exception:
        pass

    last_update_id = 0
    while True:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params={"offset": last_update_id + 1, "timeout": 20},
                timeout=25
            )
            data = r.json()
            if data.get("ok"):
                for item in data.get("result", []):
                    last_update_id = item["update_id"]
                    # Supports both Direct Chat messages and Channel posts!
                    msg_obj = item.get("message") or item.get("channel_post") or {}
                    text = msg_obj.get("text", "").strip()
                    chat_id = msg_obj.get("chat", {}).get("id")
                    if text.startswith("/") and chat_id:
                        handle_telegram_command(text, chat_id)
        except Exception:
            time.sleep(5)
        time.sleep(1)


# --- MAIN TRADING LOOP ---

def trading_loop():
    print(f"=== TRADING ENGINE STARTED ({TIMEFRAME.upper()}) ===", flush=True)
    orders_active = (not DRY_RUN) and ENABLE_ORDERS

    while True:
        with WATCHLIST_LOCK:
            active_watchlist = dict(WATCHLIST)

        if not active_watchlist:
            time.sleep(10)
            continue

        try:
            products = api_get("/v2/products", base_url=BASE)
            mapped = {}
            for s in active_watchlist.keys():
                base = s.replace("USD", "")
                matches = [
                    p for p in products
                    if (str(p.get("symbol", "")).upper() == s or base in str(p.get("symbol", "")).upper())
                    and ("perpetual" in str(p.get("contract_type", "")).lower() or "perp" in str(p.get("symbol", "")).lower())
                ]
                if len(matches) == 1:
                    mapped[s] = matches[0]

            latest_st = {}
            for s, p in mapped.items():
                try:
                    now = int(time.time())
                    try:
                        rows_5m = api_get("/v2/history/candles", {
                            "resolution": "5m",
                            "symbol": str(p["symbol"]),
                            "start": now - 60 * 5 * 300,
                            "end": now
                        }, base_url=CANDLE_BASE)
                    except Exception:
                        rows_5m = api_get("/v2/history/candles", {
                            "resolution": "5m",
                            "symbol": str(p["symbol"]),
                            "start": now - 60 * 5 * 300,
                            "end": now
                        }, base_url=BASE)

                    rows_5m = sorted(rows_5m, key=lambda x: int(x.get("time", 0)))
                    if TIMEFRAME == "10m":
                        candles = build_10m_candles(rows_5m, now)
                    else:
                        candles = [x for x in rows_5m if int(x.get("time", 0)) + 300 <= now]

                    out = supertrend(candles)
                    if out:
                        latest_st[s] = out
                        LATEST_ST[s] = out
                        print(datetime.now().strftime("%H:%M:%S"), s, out, flush=True)
                        k = (s, out["candle"])

                        if out["signal"] and k not in SENT_SIGNALS:
                            SENT_SIGNALS.add(k)
                            lots = active_watchlist.get(s, DEFAULT_ORDER_SIZE)
                            msg = f"🚀 [{TIMEFRAME.upper()} SIGNAL] {s} {out['signal']} | Close={out['close']} | SL={out['stop']} | Lots={lots}"
                            if orders_active:
                                try:
                                    order_info = execute_trade_order(p, out["signal"], out["close"], out["stop"], lots, out.get("atr", 0.0))
                                    print("  >>> ORDER STATUS:", s, order_info, flush=True)
                                    msg += f"\n{order_info}"
                                except Exception as oe:
                                    print("  >>> ERROR:", s, oe, flush=True)
                                    msg += f"\nError: {oe}"
                            send_telegram(msg)
                except Exception as e:
                    print(s, "ERROR", e, flush=True)

            if orders_active and latest_st:
                sync_and_trail_open_positions(mapped, latest_st)

        except Exception as e:
            print("Main loop error:", e, flush=True)

        time.sleep(POLL_SECONDS)


# --- FLASK WEB SERVER FOR RENDER 24/7 ---

app = Flask(__name__)

@app.route("/")
def home():
    return f"Delta Auto-Trading Bot ({TIMEFRAME.upper()}) Running 24/7! Active Watchlist: {WATCHLIST}"

# Start background threads safely (works with both `python app.py` and `gunicorn app:app`)
def start_background_workers():
    if TELEGRAM_TOKEN:
        threading.Thread(target=telegram_Command_listener, daemon=True).start()
    threading.Thread(target=trading_loop, daemon=True).start()

start_background_workers()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
