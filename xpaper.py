# xpaper.py — v3: бумажная стратегия «двусторонний запас» (межбиржевой арбитраж без перевода в сделке)
import os, time, json, logging
import requests
import psycopg2
import mm_scanner as mm
import xfer

def env(k, d=""):
    return os.getenv(k, d).strip()

START = float(env("XI_START_USD", "200"))          # бумажный капитал
MAX_PAIRS = int(env("XI_MAX_PAIRS", "4"))          # пар «монета + 2 биржи» одновременно
TRADE_USD = float(env("XI_TRADE_USD", "15"))       # одна сделка
MIN_EDGE = float(env("XI_MIN_EDGE", "0.008"))      # от 0.8% после комиссий, по реальному стакану
MAX_WD_USD = float(env("XI_MAX_WD_USD", "0.3"))    # только монеты с выводом до $0.30
SELECT_MIN = float(env("XI_SELECT_MIN", "60"))     # выбор новых пар раз в час
MIN_SEEN = float(env("XI_MIN_SEEN", "10"))         # пара должна мелькнуть 10+ раз
REBAL_AT = float(env("XI_REBAL_AT", "0.2"))        # перевоз, если на бирже < 20% запаса
REBAL_MIN = float(env("XI_REBAL_MIN", "30"))       # перевоз идёт 30 минут
USDT_WD = float(env("XI_USDT_WD_USD", "1.0"))      # комиссия перевода USDT
DROP_H = float(env("XI_DROP_H", "24"))             # нет сделок 24ч — пара закрывается
FEE = float(env("XA_FEE", "0.001"))
DEPTH_EX = ("gate", "kucoin", "htx", "mexc", "bingx", "bitget", "okx")
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0"
log = logging.getLogger("xinv")
A = {}

def q(sql, params=(), fetch=False):
    conn = psycopg2.connect(mm.DB_URL, connect_timeout=20)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall() if fetch else None
    finally:
        conn.close()

q("CREATE TABLE IF NOT EXISTS paper_xarb(id TEXT PRIMARY KEY, js TEXT)")

def load():
    r = q("SELECT js FROM paper_xarb WHERE id = 'inv_v3'", fetch=True)
    A.update(json.loads(r[0][0]) if r else {
        "cash": START, "pairs": {}, "seen": {}, "trades": 0, "tpnl": 0.0, "rebal_cost": 0.0,
        "t0": time.time(), "sel_ts": 0, "eq": START, "log": []})

def save():
    q("""INSERT INTO paper_xarb(id, js) VALUES ('inv_v3', %s)
         ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(A),))

def note(coin, what, usd, pct=None):
    A["log"] = (A["log"] + [[int(time.time()), coin, what, round(usd, 3), pct]])[-50:]

def _lv(rows):
    return [(float(r[0]), float(r[1])) for r in rows or []]

def book(ex, c):
    try:
        if ex == "gate":
            d = S.get("https://api.gateio.ws/api/v4/spot/order_book",
                      params={"currency_pair": f"{c}_USDT", "limit": 20}, timeout=10).json()
        elif ex == "kucoin":
            d = S.get("https://api.kucoin.com/api/v1/market/orderbook/level2_20",
                      params={"symbol": f"{c}-USDT"}, timeout=10).json().get("data") or {}
        elif ex == "htx":
            d = S.get("https://api.huobi.pro/market/depth", timeout=10,
                      params={"symbol": f"{c.lower()}usdt", "type": "step0", "depth": 20}).json().get("tick") or {}
        elif ex == "mexc":
            d = S.get("https://api.mexc.com/api/v3/depth", params={"symbol": f"{c}USDT", "limit": 20},
                      timeout=10).json()
        elif ex == "bingx":
            d = S.get("https://open-api.bingx.com/openApi/spot/v1/market/depth", timeout=10,
                      params={"symbol": f"{c}-USDT", "limit": 20,
                              "timestamp": int(time.time() * 1000)}).json().get("data") or {}
        elif ex == "bitget":
            d = S.get("https://api.bitget.com/api/v2/spot/market/orderbook", timeout=10,
                      params={"symbol": f"{c}USDT", "type": "step0", "limit": 20}).json().get("data") or {}
        elif ex == "okx":
            d = (S.get("https://www.okx.com/api/v5/market/books", params={"instId": f"{c}-USDT", "sz": 20},
                       timeout=10).json().get("data") or [{}])[0]
        else:
            return None
        bids, asks = _lv(d.get("bids")), _lv(d.get("asks"))
        return (bids, asks) if bids and asks else None
    except Exception:
        return None

def buy_qty(asks, usdt):                     # сколько монет купим на usdt (проход по стакану)
    left, qty = usdt / (1 + FEE), 0.0
    for p, a in asks:
        take = min(left, p * a)
        qty += take / p
        left -= take
        if left <= 1e-9:
            return qty
    return None

def sell_usdt(bids, qty):                    # сколько USDT получим за qty монет
    left, usd = qty, 0.0
    for p, a in bids:
        take = min(left, a)
        usd += take * p
        left -= take
        if left <= 1e-12:
            return usd * (1 - FEE)
    return None

def mid_of(by, coin, exs, fallback):
    qd = by.get(coin, {})
    ms = [(qd[e][0] + qd[e][1]) / 2 for e in exs if e in qd]
    return sum(ms) / len(ms) if ms else fallback

def pair_value(p):
    v = sum(x["usdt"] + x["coin"] * p["mid"] for x in p["inv"].values())
    return v + sum(m["amt"] * (p["mid"] if m["asset"] == "coin" else 1) for m in p["moves"])

def select(by, now):
    A["sel_ts"] = now
    ranked = sorted(((n, k) for k, n in A["seen"].items() if n >= MIN_SEEN and k not in A["pairs"]),
                    reverse=True)
    for n, k in ranked:
        slots = MAX_PAIRS - len(A["pairs"])
        coin, e1, e2 = k.split("|")
        mid = mid_of(by, coin, (e1, e2), None)
        if slots <= 0 or A["cash"] / slots < 20:
            break
        if not mid:
            continue
        alloc = A["cash"] / slots
        A["cash"] -= alloc
        side = alloc / 4
        A["pairs"][k] = {"coin": coin, "ex": [e1, e2], "mid": mid, "trades": 0, "tpnl": 0.0,
                         "opened": now, "last_trade": now, "moves": [], "stuck": None,
                         "inv": {e: {"usdt": side, "coin": side / mid * (1 - FEE)} for e in (e1, e2)}}
        note(coin, f"открыта пара {e1}↔{e2} на ${alloc:.0f}", 0)
    A["seen"] = {k: n / 2 for k, n in A["seen"].items() if n >= 1}

def trade(p, now):
    e1, e2 = p["ex"]
    b = {e1: book(e1, p["coin"]), e2: book(e2, p["coin"])}
    if not b[e1] or not b[e2]:
        return
    best = None
    for sx, bx in ((e1, e2), (e2, e1)):                     # продаём на sx, покупаем на bx
        inv_s, inv_b = p["inv"][sx], p["inv"][bx]
        usd = min(TRADE_USD, inv_b["usdt"], inv_s["coin"] * b[sx][0][0][0] * 0.98)
        if usd < 5:
            continue
        qty = buy_qty(b[bx][1], usd)
        if not qty or qty > inv_s["coin"]:
            continue
        got = sell_usdt(b[sx][0], qty)
        if got and got / usd - 1 >= MIN_EDGE and (not best or got / usd > best[0]):
            best = (got / usd, sx, bx, usd, qty, got)
    if not best:
        return
    _, sx, bx, usd, qty, got = best
    p["inv"][sx]["coin"] -= qty
    p["inv"][sx]["usdt"] += got
    p["inv"][bx]["usdt"] -= usd
    p["inv"][bx]["coin"] += qty
    p["trades"] += 1
    p["tpnl"] += got - usd
    p["last_trade"] = now
    A["trades"] += 1
    A["tpnl"] += got - usd
    note(p["coin"], f"{bx}>{sx}", got - usd, round((got / usd - 1) * 100, 2))

def rebalance(p, now):
    inv, coin = p["inv"], p["coin"]
    e1, e2 = p["ex"]
    for asset in ("coin", "usdt"):
        if any(m["asset"] == asset for m in p["moves"]):
            continue
        tot = inv[e1][asset] + inv[e2][asset]
        for low, high in ((e1, e2), (e2, e1)):
            amt = tot / 2 - inv[low][asset]
            if inv[low][asset] >= REBAL_AT * tot or amt <= 0 or inv[high][asset] < amt:
                continue
            if asset == "coin":
                rt = xfer.route(high, low, coin, p["mid"])
                if rt["state"] not in ("open", "half"):
                    p["stuck"] = rt["text"]
                    continue
                fee_usd = rt["fee_usd"] if rt["fee_usd"] is not None else MAX_WD_USD
                fee_amt = fee_usd / p["mid"]
            else:
                fee_usd = fee_amt = USDT_WD
            inv[high][asset] -= amt
            p["moves"].append({"asset": asset, "to": low, "amt": max(amt - fee_amt, 0.0),
                               "ready": now + REBAL_MIN * 60})
            p["stuck"] = None
            A["rebal_cost"] += fee_usd
            note(coin, f"перевоз {asset} {high}>{low}", -fee_usd)
            break

def step(rows, by):
    if not A:
        load()
    now = time.time()
    for r in rows:                                            # 1) наблюдаем окна
        wd = r.get("wd_usd")
        if (r.get("state") == "open" and wd is not None and wd <= MAX_WD_USD
                and r["buy"] in DEPTH_EX and r["sell"] in DEPTH_EX):
            e1, e2 = sorted((r["buy"], r["sell"]))
            k = f"{r['coin']}|{e1}|{e2}"
            A["seen"][k] = A["seen"].get(k, 0) + 1
    if now - A["sel_ts"] >= SELECT_MIN * 60:                  # 2) выбираем пары
        select(by, now)
    for k, p in list(A["pairs"].items()):                    # 3) торгуем, перевозим, закрываем
        p["mid"] = mid_of(by, p["coin"], p["ex"], p["mid"])
        keep = []
        for m in p["moves"]:
            if now >= m["ready"]:
                p["inv"][m["to"]][m["asset"]] += m["amt"]
            else:
                keep.append(m)
        p["moves"] = keep
        trade(p, now)
        rebalance(p, now)
        if now - p["last_trade"] > DROP_H * 3600 and not p["moves"]:
            coins = sum(x["coin"] for x in p["inv"].values())
            A["cash"] += pair_value(p) - coins * p["mid"] * FEE
            note(p["coin"], f"пара {k} закрыта: нет сделок {DROP_H:.0f}ч", 0)
            del A["pairs"][k]
    A["eq"] = A["cash"] + sum(pair_value(p) for p in A["pairs"].values())
    save()

def summary():
    if not A:
        return {}
    days = max((time.time() - A["t0"]) / 86400, 0.01)
    eq = A.get("eq", START)
    return {"inv": {
        "equity": round(eq, 2), "pnl": round(eq - START, 2), "trades": A["trades"], "wins": A["trades"],
        "per_day_usd": round((eq - START) / days, 2), "days": round(days, 2),
        "trading_pnl": round(A["tpnl"], 2), "rebal_cost": round(A["rebal_cost"], 2),
        "price_effect": round(eq - START - A["tpnl"] + A["rebal_cost"], 2),
        "pairs": {k: {"trades": p["trades"], "pnl": round(p["tpnl"], 3), "stuck": p["stuck"]}
                  for k, p in A["pairs"].items()},
        "watch": sorted(((round(n, 1), k) for k, n in A["seen"].items()), reverse=True)[:5],
        "last_trades": A["log"][-5:]}}