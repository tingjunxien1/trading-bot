#!/usr/bin/env python3
"""
=============================================================
  AUTONOMOUS PAPER TRADING ENGINE — CLOUD EDITION v3.0
  Deploy to PythonAnywhere and run 24/7 for free.

  Strategy : Long/Short Momentum on Top Crypto
  Markets  : Top 30 Crypto (CoinCap + Binance)
  Capital  : $2,000 USD (paper)
  Alerts   : Push notifications via ntfy.sh (free, no signup)

  HOW TO SET UP NOTIFICATIONS (takes 2 minutes):
  1. Change NTFY_TOPIC below to any unique name (e.g. "chris-trading-xyz")
  2. Download the "ntfy" app on your phone (free)
  3. Subscribe to your topic in the app
  4. You'll get instant push alerts for every trade!

  PYTHONANYWHERE SETUP:
  1. Create free account at pythonanywhere.com
  2. Upload this file (Files tab → Upload)
  3. Go to Tasks tab → Add new task
  4. Command: python3 /home/YOUR_USERNAME/pt_cloud.py
  5. Set it to run hourly (repeat for each hour 0-23)
=============================================================
"""

import json, requests, os, time
from datetime import datetime

# ── CONFIG ────────────────────────────────────────────────────────────────────
NTFY_TOPIC   = "chris-trading-2026"              # ← your ntfy topic
STATE_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pt_state.json")
LOG_FILE     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pt_log.txt")
STARTING_CAP = 2000.00

STABLES = {"USDT","USDC","BUSD","DAI","TUSD","FRAX","USDP","GUSD","USDD","FDUSD","PYUSD"}

INITIAL_STATE = {
    "account": {
        "starting_capital": STARTING_CAP,
        "cash": STARTING_CAP,
        "equity": STARTING_CAP,
        "peak_equity": STARTING_CAP,
        "max_drawdown_pct": 0.0,
        "total_pnl": 0.00,
        "total_pnl_pct": 0.00
    },
    "longs": {},          # long positions: profit when price rises
    "shorts": {},         # short positions: profit when price falls
    "trade_history": [],
    "equity_curve": [{"ts": datetime.now().strftime("%Y-%m-%d %H:%M"), "equity": STARTING_CAP}],
    "stats": {
        "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
        "total_fees_paid": 0.0, "runs": 0,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "last_run": None,
        "best_trade_pct": 0.0, "worst_trade_pct": 0.0,
        "active_strategy": "long_short_momentum_v1",
        "data_source": "unknown"
    },
    "config": {
        # Position sizing
        "max_longs": 3,           # max 3 long positions at once
        "max_shorts": 2,          # max 2 short positions at once
        "position_size_pct": 0.18, # 18% of equity per position
        # Long entry
        "long_min_score": 3.5,
        "long_min_24h": 1.5,       # 24h must be > +1.5% to go long
        "long_extreme_fear_block": 12,  # don't buy if F&G below this
        # Short entry
        "short_min_score": -3.0,   # score must be < -3.0 to short
        "short_max_24h": -2.5,     # 24h must be < -2.5% to short
        "short_max_fg": 45,        # only short when sentiment is fearful/neutral
        "short_extreme_greed_block": 80, # don't short in extreme greed
        # Exit
        "stop_loss_pct": 0.07,     # 7% stop loss (both directions)
        "take_profit_pct": 0.15,   # 15% take profit (both directions)
        "fee_pct": 0.001,          # 0.1% simulated exchange fee
        "equity_curve_max_points": 500
    }
}

# ── I/O ───────────────────────────────────────────────────────────────────────
def ts():    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
def tss():   return datetime.now().strftime("%Y-%m-%d %H:%M")

def log(msg, lvl="INFO"):
    line = f"[{ts()}][{lvl}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f: f.write(line + "\n")
    except: pass

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                state = json.load(f)
            # Migrate old config keys → new keys (so old pt_state.json works)
            cfg = state.setdefault("config", {})
            defaults = INITIAL_STATE["config"]
            for key, val in defaults.items():
                if key not in cfg:
                    cfg[key] = val
            # Migrate old positions key → longs
            if "positions" in state and "longs" not in state:
                state["longs"] = state.pop("positions")
            state.setdefault("longs", {})
            state.setdefault("shorts", {})
            return state
        except: pass
    return dict(INITIAL_STATE)

def save_state(state):
    with open(STATE_FILE, "w") as f: json.dump(state, f, indent=2)

def notify(title, body, priority="default"):
    """Send push notification via ntfy.sh (free)."""
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=body.encode("utf-8"),
            headers={"Title": title, "Priority": priority, "Tags": "chart_increasing"},
            timeout=10
        )
    except: pass

# ── MARKET DATA (PythonAnywhere-compatible sources) ───────────────────────────
# CoinCap and Binance are blocked on PythonAnywhere free tier.
# We use CoinGecko (whitelisted) and Kraken (whitelisted) instead.

def fetch_coingecko():
    """Primary: CoinGecko — whitelisted on PythonAnywhere free tier."""
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 50,
            "sparkline": False,
            "price_change_percentage": "1h,24h,7d"
        }
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        raw = r.json()
        if not isinstance(raw, list) or len(raw) == 0:
            return [], ""
        coins = []
        for i, c in enumerate(raw):
            try:
                sym   = c["symbol"].upper()
                price = float(c.get("current_price") or 0)
                c24h  = float(c.get("price_change_percentage_24h_in_currency")
                              or c.get("price_change_percentage_24h") or 0)
                c1h   = c.get("price_change_percentage_1h_in_currency")
                c7d   = c.get("price_change_percentage_7d_in_currency")
                vol   = float(c.get("total_volume") or 0)
                mcap  = float(c.get("market_cap") or 0)
                if price > 0:
                    coins.append({
                        "symbol": sym, "name": c["name"],
                        "current_price": price, "c24h": c24h,
                        "c1h": float(c1h) if c1h is not None else None,
                        "c7d": float(c7d) if c7d is not None else None,
                        "volume_24h": vol, "market_cap": mcap, "rank": i + 1
                    })
            except: continue
        if coins: return coins, "coingecko"
    except Exception as e: log(f"CoinGecko: {e}", "WARN")
    return [], ""

def fetch_kraken():
    """Fallback: Kraken public API — whitelisted on PythonAnywhere."""
    # Kraken pair names → our symbol names
    PAIRS = {
        "XXBTZUSD":"BTC", "XETHZUSD":"ETH", "XLTCZUSD":"LTC",
        "SOLUSD":"SOL",   "DOTUSD":"DOT",   "ADAUSD":"ADA",
        "AVAXUSD":"AVAX", "LINKUSD":"LINK", "ATOMUSD":"ATOM",
        "UNIUSD":"UNI",   "AAVEUSD":"AAVE", "ALGOUSD":"ALGO",
        "XDGUSD":"DOGE",  "FILUSD":"FIL",   "BNBUSD":"BNB",
        "XRPUSD":"XRP",   "MATICUSD":"MATIC","NEARUSD":"NEAR",
        "SANDUSD":"SAND", "MANAUSD":"MANA"
    }
    try:
        pair_str = ",".join(PAIRS.keys())
        r = requests.get(
            f"https://api.kraken.com/0/public/Ticker?pair={pair_str}",
            timeout=25
        )
        r.raise_for_status()
        result = r.json().get("result", {})
        coins = []
        for i, (kraken_pair, sym) in enumerate(PAIRS.items()):
            # Kraken may return slightly different pair names
            data = result.get(kraken_pair) or result.get(kraken_pair.replace("X","").replace("Z",""))
            if not data: continue
            try:
                price  = float(data["c"][0])   # last trade price
                open_p = float(data["o"])       # 24h open price
                vol    = float(data["v"][1])    # 24h volume
                high   = float(data["h"][1])    # 24h high
                low    = float(data["l"][1])    # 24h low
                c24h   = ((price - open_p) / open_p) * 100 if open_p > 0 else 0
                if price > 0:
                    coins.append({
                        "symbol": sym, "name": sym,
                        "current_price": price, "c24h": round(c24h, 3),
                        "c1h": None, "c7d": None,
                        "volume_24h": vol * price,
                        "market_cap": 0, "rank": i + 1
                    })
            except: continue
        if coins: return coins, "kraken"
    except Exception as e: log(f"Kraken: {e}", "WARN")
    return [], ""

def get_market():
    for fn in [fetch_coingecko, fetch_kraken]:
        coins, src = fn()
        if coins:
            log(f"Market data: {len(coins)} coins from {src}")
            return coins, src
    return [], "none"

def fetch_fg():
    try:
        r = requests.get("https://api.alternative.me/fng/", timeout=10)
        d = r.json()["data"][0]
        return int(d["value"]), d["value_classification"]
    except: return 50, "Neutral"

# ── STRATEGY: LONG/SHORT MOMENTUM ────────────────────────────────────────────
def compute_score(coin, all_coins):
    """
    Momentum score — positive = bullish, negative = bearish.
    Range: roughly -8 to +8.
    """
    c24h = coin["c24h"]
    c1h  = coin.get("c1h") or 0.0
    c7d  = coin.get("c7d") or 0.0

    # Volume confirmation: high volume amplifies signal
    vols = sorted([c["volume_24h"] for c in all_coins if c["volume_24h"] > 0], reverse=True)
    v_rank = vols.index(coin["volume_24h"]) / len(vols) if vols and coin["volume_24h"] in vols else 0.5
    v_bonus = (1 - v_rank) * 1.5  # 0–1.5 points

    # 7d trend context
    trend = 0.8 if c7d > 5 else (0.3 if c7d > 0 else (-0.3 if c7d < 0 else -0.8))

    # 1h momentum direction confirmation
    h1_bonus = 0.5 if c1h and c1h > 0.5 else (-0.5 if c1h and c1h < -0.5 else 0)

    # Core: 24h change drives the signal
    score = (c24h * 0.55) + v_bonus + trend + h1_bonus
    return round(score, 3)

def fg_size_mult(fg):
    """Scale position size based on market sentiment."""
    if fg >= 80: return 0.50   # extreme greed — smaller longs, normal shorts
    if fg >= 65: return 0.75
    if fg <= 20: return 0.65   # extreme fear — smaller positions overall
    if fg <= 40: return 1.05
    return 1.00

# ── PAPER EXECUTION ────────────────────────────────────────────────────────────
def open_long(state, coin, fg, score):
    cfg  = state["config"]
    cash = state["account"]["cash"]
    eq   = state["account"]["equity"]
    size = min(eq * cfg["position_size_pct"] * fg_size_mult(fg), cash * 0.95)
    if size < 15: return False
    fee  = size * cfg["fee_pct"]
    if size + fee > cash: return False
    qty  = size / coin["current_price"]
    now  = ts()
    state["longs"][coin["symbol"]] = {
        "direction": "LONG",
        "name": coin["name"], "entry_price": coin["current_price"],
        "current_price": coin["current_price"],
        "quantity": round(qty, 8), "cost_basis": round(size, 4), "fee_open": round(fee, 4),
        "stop_loss":   round(coin["current_price"] * (1 - cfg["stop_loss_pct"]), 8),
        "take_profit": round(coin["current_price"] * (1 + cfg["take_profit_pct"]), 8),
        "score": score, "c24h_entry": coin["c24h"],
        "pnl_pct": 0.0, "pnl_usd": 0.0, "opened_at": now
    }
    state["account"]["cash"] = round(cash - size - fee, 4)
    state["stats"]["total_trades"]    += 1
    state["stats"]["total_fees_paid"]  = round(state["stats"]["total_fees_paid"] + fee, 4)
    state["trade_history"].append({
        "action":"LONG_OPEN","symbol":coin["symbol"],"price":coin["current_price"],
        "qty":round(qty,8),"value":round(size,2),"fee":round(fee,4),
        "score":score,"c24h":coin["c24h"],
        "reason":f"Bullish momentum score {score:.2f} | 24h {coin['c24h']:+.2f}%",
        "timestamp":now
    })
    log(f"  📈 LONG  OPEN  {coin['symbol']:8} @ ${coin['current_price']:>12,.4f}  val=${size:.2f}  score={score:.2f}")
    notify(f"📈 LONG OPENED: {coin['symbol']}",
           f"Bought {coin['symbol']} @ ${coin['current_price']:,.4f}\nScore: {score:.2f} | 24h: {coin['c24h']:+.2f}%\nSL: ${state['longs'][coin['symbol']]['stop_loss']:,.4f} | TP: ${state['longs'][coin['symbol']]['take_profit']:,.4f}")
    return True

def open_short(state, coin, fg, score):
    cfg  = state["config"]
    cash = state["account"]["cash"]
    eq   = state["account"]["equity"]
    size = min(eq * cfg["position_size_pct"] * fg_size_mult(fg), cash * 0.95)
    if size < 15: return False
    fee  = size * cfg["fee_pct"]
    if size + fee > cash: return False
    qty  = size / coin["current_price"]
    now  = ts()
    state["shorts"][coin["symbol"]] = {
        "direction": "SHORT",
        "name": coin["name"], "entry_price": coin["current_price"],
        "current_price": coin["current_price"],
        "quantity": round(qty, 8), "cost_basis": round(size, 4), "fee_open": round(fee, 4),
        # SHORT: stop loss is ABOVE entry, take profit is BELOW entry
        "stop_loss":   round(coin["current_price"] * (1 + cfg["stop_loss_pct"]), 8),
        "take_profit": round(coin["current_price"] * (1 - cfg["take_profit_pct"]), 8),
        "score": score, "c24h_entry": coin["c24h"],
        "pnl_pct": 0.0, "pnl_usd": 0.0, "opened_at": now
    }
    state["account"]["cash"] = round(cash - size - fee, 4)
    state["stats"]["total_trades"]    += 1
    state["stats"]["total_fees_paid"]  = round(state["stats"]["total_fees_paid"] + fee, 4)
    state["trade_history"].append({
        "action":"SHORT_OPEN","symbol":coin["symbol"],"price":coin["current_price"],
        "qty":round(qty,8),"value":round(size,2),"fee":round(fee,4),
        "score":score,"c24h":coin["c24h"],
        "reason":f"Bearish momentum score {score:.2f} | 24h {coin['c24h']:+.2f}%",
        "timestamp":now
    })
    log(f"  📉 SHORT OPEN  {coin['symbol']:8} @ ${coin['current_price']:>12,.4f}  val=${size:.2f}  score={score:.2f}")
    notify(f"📉 SHORT OPENED: {coin['symbol']}",
           f"Shorted {coin['symbol']} @ ${coin['current_price']:,.4f}\nScore: {score:.2f} | 24h: {coin['c24h']:+.2f}%\nSL: ${state['shorts'][coin['symbol']]['stop_loss']:,.4f} | TP: ${state['shorts'][coin['symbol']]['take_profit']:,.4f}",
           priority="high")
    return True

def close_long(state, sym, price, reason):
    pos = state["longs"].get(sym)
    if not pos: return
    cfg  = state["config"]
    proc = pos["quantity"] * price
    fee  = proc * cfg["fee_pct"]
    net  = proc - fee
    pnl  = net - pos["cost_basis"]
    pct  = ((price - pos["entry_price"]) / pos["entry_price"]) * 100
    state["account"]["cash"] = round(state["account"]["cash"] + net, 4)
    state["stats"]["total_fees_paid"] = round(state["stats"]["total_fees_paid"] + fee, 4)
    _record_close(state, sym, price, proc, pnl, pct, fee, pos, "LONG_CLOSE", reason)
    icon = "✅" if pnl >= 0 else "❌"
    log(f"  {icon} LONG  CLOSE {sym:8} @ ${price:>12,.4f}  pnl=${pnl:+.2f} ({pct:+.2f}%)  [{reason}]")
    notify(f"{'✅' if pnl>=0 else '❌'} LONG CLOSED: {sym}",
           f"Sold {sym} @ ${price:,.4f}\nP&L: ${pnl:+.2f} ({pct:+.2f}%)\nReason: {reason}",
           priority="high" if pnl < 0 else "default")
    del state["longs"][sym]

def close_short(state, sym, price, reason):
    pos = state["shorts"].get(sym)
    if not pos: return
    cfg  = state["config"]
    # Short P&L: profit when price falls
    pct  = ((pos["entry_price"] - price) / pos["entry_price"]) * 100
    pnl  = pos["cost_basis"] * (pct / 100)
    fee  = pos["cost_basis"] * cfg["fee_pct"]
    net  = pos["cost_basis"] + pnl - fee
    state["account"]["cash"] = round(state["account"]["cash"] + net, 4)
    state["stats"]["total_fees_paid"] = round(state["stats"]["total_fees_paid"] + fee, 4)
    proc = pos["cost_basis"]
    _record_close(state, sym, price, proc, pnl, pct, fee, pos, "SHORT_CLOSE", reason)
    icon = "✅" if pnl >= 0 else "❌"
    log(f"  {icon} SHORT CLOSE {sym:8} @ ${price:>12,.4f}  pnl=${pnl:+.2f} ({pct:+.2f}%)  [{reason}]")
    notify(f"{'✅' if pnl>=0 else '❌'} SHORT CLOSED: {sym}",
           f"Covered {sym} @ ${price:,.4f}\nP&L: ${pnl:+.2f} ({pct:+.2f}%)\nReason: {reason}",
           priority="high" if pnl < 0 else "default")
    del state["shorts"][sym]

def _record_close(state, sym, price, proc, pnl, pct, fee, pos, action, reason):
    if pnl >= 0:
        state["stats"]["winning_trades"] += 1
        if pct > state["stats"]["best_trade_pct"]:  state["stats"]["best_trade_pct"] = round(pct,3)
    else:
        state["stats"]["losing_trades"] += 1
        if pct < state["stats"]["worst_trade_pct"]: state["stats"]["worst_trade_pct"] = round(pct,3)
    state["trade_history"].append({
        "action":action,"symbol":sym,"price":price,"value":round(proc,4),
        "pnl_usd":round(pnl,4),"pnl_pct":round(pct,3),
        "fee":round(fee,4),"opened_at":pos["opened_at"],"reason":reason,"timestamp":ts()
    })

def check_long_exits(state, coins):
    if not state.get("longs"): return
    cfg = state["config"]
    pm  = {c["symbol"]: c["current_price"] for c in coins}
    to_close = []
    for sym, pos in state["longs"].items():
        curr = pm.get(sym)
        if curr is None: continue
        pos["current_price"] = curr
        pct = ((curr - pos["entry_price"]) / pos["entry_price"]) * 100
        pos["pnl_pct"] = round(pct, 3)
        pos["pnl_usd"] = round(pct/100 * pos["cost_basis"], 4)
        if pct <= -(cfg["stop_loss_pct"]  * 100): to_close.append((sym, curr, f"STOP_LOSS ({pct:+.2f}%)"))
        elif pct >= (cfg["take_profit_pct"] * 100): to_close.append((sym, curr, f"TAKE_PROFIT ({pct:+.2f}%)"))
    for sym, price, reason in to_close:
        close_long(state, sym, price, reason)

def check_short_exits(state, coins):
    if not state.get("shorts"): return
    cfg = state["config"]
    pm  = {c["symbol"]: c["current_price"] for c in coins}
    to_close = []
    for sym, pos in state["shorts"].items():
        curr = pm.get(sym)
        if curr is None: continue
        pos["current_price"] = curr
        pct = ((pos["entry_price"] - curr) / pos["entry_price"]) * 100  # positive when price falls
        pos["pnl_pct"] = round(pct, 3)
        pos["pnl_usd"] = round(pct/100 * pos["cost_basis"], 4)
        # SHORT stop loss: price went UP (our loss)
        if curr >= pos["stop_loss"]:  to_close.append((sym, curr, f"SHORT_STOP_LOSS ({pct:+.2f}%)"))
        # SHORT take profit: price went DOWN (our gain)
        elif curr <= pos["take_profit"]: to_close.append((sym, curr, f"SHORT_TAKE_PROFIT ({pct:+.2f}%)"))
    for sym, price, reason in to_close:
        close_short(state, sym, price, reason)

def update_equity(state, coins):
    pm = {c["symbol"]: c["current_price"] for c in coins}
    # Long position values increase with price
    long_val  = sum(p["quantity"] * pm.get(s, p["entry_price"])
                    for s, p in state.get("longs",{}).items())
    # Short position values: cost_basis +/- pnl
    short_val = 0
    for s, p in state.get("shorts",{}).items():
        curr = pm.get(s, p["entry_price"])
        pct  = ((p["entry_price"] - curr) / p["entry_price"])
        short_val += p["cost_basis"] * (1 + pct)

    eq    = round(state["account"]["cash"] + long_val + short_val, 4)
    start = state["account"]["starting_capital"]
    state["account"]["equity"]        = eq
    state["account"]["total_pnl"]     = round(eq - start, 4)
    state["account"]["total_pnl_pct"] = round((eq/start - 1)*100, 4)
    peak = state["account"].get("peak_equity", eq)
    if eq > peak: state["account"]["peak_equity"] = eq
    dd = ((eq - state["account"]["peak_equity"]) / state["account"]["peak_equity"]) * 100
    if dd < state["account"]["max_drawdown_pct"]: state["account"]["max_drawdown_pct"] = round(dd,4)
    curve = state.setdefault("equity_curve", [])
    curve.append({"ts": tss(), "equity": eq})
    if len(curve) > state["config"].get("equity_curve_max_points",500):
        state["equity_curve"] = curve[-500:]

# ── MAIN ──────────────────────────────────────────────────────────────────────
def run():
    log("=" * 68)

    state = load_state()
    state["stats"]["runs"] = state["stats"].get("runs", 0) + 1
    state["stats"]["last_run"] = ts()

    # Ensure new keys exist (for migration from older state)
    if "longs"  not in state: state["longs"]  = state.pop("positions", {})
    if "shorts" not in state: state["shorts"] = {}

    log(f"RUN #{state['stats']['runs']} START")

    coins, src = get_market()
    fg_val, fg_lbl = fetch_fg()
    state["stats"]["data_source"] = src

    if not coins:
        log("No market data. Skipping.", "ERROR")
        save_state(state); return state

    log(f"Source: {src} | Coins: {len(coins)} | F&G: {fg_val} ({fg_lbl})")
    update_equity(state, coins)

    cfg = state["config"]
    n_longs  = len(state["longs"])
    n_shorts = len(state["shorts"])

    # ── CHECK EXITS ──
    log("--- EXIT CHECK ---")
    check_long_exits(state,  coins)
    check_short_exits(state, coins)
    update_equity(state, coins)

    # ── SCORE ALL COINS ──
    scored = []
    for c in coins:
        if c["symbol"] in STABLES: continue
        if c["current_price"] <= 0: continue
        sc = compute_score(c, coins)
        scored.append({**c, "score": sc})

    # Sort: highest score first for longs, lowest first for shorts
    long_cands  = [c for c in sorted(scored, key=lambda x: x["score"], reverse=True)
                   if c["score"] >= cfg["long_min_score"] and c["c24h"] >= cfg["long_min_24h"]
                   and c["symbol"] not in state["longs"] and c["symbol"] not in state["shorts"]]

    short_cands = [c for c in sorted(scored, key=lambda x: x["score"])
                   if c["score"] <= cfg["short_min_score"] and c["c24h"] <= cfg["short_max_24h"]
                   and c["symbol"] not in state["longs"] and c["symbol"] not in state["shorts"]]

    # ── LONG ENTRIES ──
    log(f"--- LONG ENTRY CHECK ({len(state['longs'])}/{cfg['max_longs']}) ---")
    if fg_val < cfg["long_extreme_fear_block"]:
        log(f"  SKIP longs — Extreme Fear ({fg_val})")
    elif len(state["longs"]) < cfg["max_longs"]:
        for c in long_cands[: cfg["max_longs"] - len(state["longs"])]:
            if state["account"]["cash"] >= 15:
                open_long(state, c, fg_val, c["score"])
    else:
        log(f"  Max longs ({cfg['max_longs']}) reached.")

    # ── SHORT ENTRIES ──
    log(f"--- SHORT ENTRY CHECK ({len(state['shorts'])}/{cfg['max_shorts']}) ---")
    if fg_val > cfg["short_extreme_greed_block"]:
        log(f"  SKIP shorts — Extreme Greed ({fg_val})")
    elif len(state["shorts"]) < cfg["max_shorts"] and fg_val <= cfg["short_max_fg"]:
        for c in short_cands[: cfg["max_shorts"] - len(state["shorts"])]:
            if state["account"]["cash"] >= 15 and c["symbol"] not in state["longs"]:
                open_short(state, c, fg_val, c["score"])
    else:
        if fg_val > cfg["short_max_fg"]:
            log(f"  SKIP shorts — sentiment too bullish (F&G {fg_val} > {cfg['short_max_fg']})")
        else:
            log(f"  Max shorts ({cfg['max_shorts']}) reached.")

    update_equity(state, coins)

    # ── SUMMARY ──
    a  = state["account"]
    st = state["stats"]
    wl = st.get("winning_trades",0) + st.get("losing_trades",0)
    wr = (st.get("winning_trades",0)/wl*100) if wl > 0 else 0.0
    n_longs  = len(state["longs"])
    n_shorts = len(state["shorts"])

    log("─" * 68)
    log(f"EQUITY  ${a['equity']:,.2f}  ({a['total_pnl_pct']:+.3f}%)  P&L ${a['total_pnl']:+.2f}")
    log(f"CASH    ${a['cash']:,.2f}  |  Drawdown {a['max_drawdown_pct']:.2f}%  |  Win {wr:.0f}%")
    log(f"LONGS: {n_longs}  SHORTS: {n_shorts}  TOTAL TRADES: {st['total_trades']}")

    for sym, p in state["longs"].items():
        pct = p.get("pnl_pct", 0)
        log(f"  📈 LONG  {sym:8} entry=${p['entry_price']:,.4f}  now=${p.get('current_price',p['entry_price']):,.4f}  pnl={pct:+.2f}%")
    for sym, p in state["shorts"].items():
        pct = p.get("pnl_pct", 0)
        log(f"  📉 SHORT {sym:8} entry=${p['entry_price']:,.4f}  now=${p.get('current_price',p['entry_price']):,.4f}  pnl={pct:+.2f}%")

    log("=" * 68)
    save_state(state)
    log("Saved. Run complete.\n")

    # Hourly summary notification
    if state["stats"]["runs"] % 6 == 0:  # every ~6 hours
        notify(
            f"📊 Portfolio Update — ${a['equity']:,.2f}",
            f"P&L: ${a['total_pnl']:+.2f} ({a['total_pnl_pct']:+.2f}%)\n"
            f"Longs: {n_longs} | Shorts: {n_shorts}\n"
            f"Win Rate: {wr:.0f}% | Trades: {st['total_trades']}"
        )

    return state

if __name__ == "__main__":
    run()
