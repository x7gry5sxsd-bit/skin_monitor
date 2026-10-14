# xinv.py — бумага: схема «запас» + пакетный перевод (+ оценка хеджа) по нескольким монетам.
#   Продажа из запаса на дорогой бирже и покупка на дешёвой — в одну секунду; монеты везём пачками.
import os, time, json, threading, logging
from flask import jsonify, request
import mm_scanner as mm
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

FEE = xpaper.FEE
TRADE = float(env("XI_TRADE_USD", "20"))     # одна сделка, $
STEP = float(env("XI_STEP_SEC", "15"))
COOL = float(env("XI_COOL_SEC", "90"))       # пауза по монете после сделки (стакан должен обновиться)
PROFIT = float(env("XI_PROFIT", "0.005"))    # минимальная прибыль сверх доли комиссии пачки
FUND_DAY = 0.0003                            # фандинг хеджа ≈0.03% в сутки от запаса
# монета: (покупка, продажа, вывод $, перевод мин, запас $, пачка $)
COINS = {
    "BLUE":  ("mexc", "htx", 0.04, 2, 60, 20),
    "MAGIC": ("mexc", "htx", 0.15, 2, 60, 20),
    "US":    ("mexc", "htx", 0.14, 2, 60, 20),
    "TRAC":  ("mexc", "htx", 0.81, 15, 200, 200),
    "MINA":  ("mexc", "htx", 0.10, 60, 100, 100),
    "DKA":   ("htx", "gate", 1.93, 15, 200, 200),
    "RSR":   ("mexc", "htx", 0.38, 10, 100, 100),
    "ETHW":  ("mexc", "htx", 0.03, 60, 100, 50),
    "US@kucoin": ("kucoin", "htx", 0.47, 3, 100, 100),
    "SXT":   ("mexc", "htx", 0.21, 20, 100, 100),
    "DOS":   ("mexc", "htx", 0.22, 20, 100, 100),
    "M":     ("mexc", "htx", 0.17, 60, 100, 100),
    "XTZ":   ("mexc", "htx", 0.03, 20, 100, 100),
    "BLUR":  ("mexc", "htx", 0.10, 20, 100, 100),
    "NES":   ("mexc", "htx", 0.28, 20, 100, 100),
    "AXS":   ("mexc", "htx", 0.44, 20, 100, 100),
    "HIPPO":        ("htx", "gate", 0.05, 3, 100, 50),
    "HIPPO@kucoin": ("htx", "kucoin", 0.05, 3, 100, 50),
    "BILL":         ("kucoin", "htx", 0.34, 20, 100, 100),
}
log = logging.getLogger("xinv")
S = {}

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'inv_v5'", fetch=True)
    if r:
        S.update(json.loads(r[0][0]))
    S.setdefault("c", {})
    S.setdefault("t0", time.time())
    S.setdefault("last_rep", time.time())

def save():
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('inv_v5', %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(S),))

def buy_cost(asks, qty):
    left, usd = qty, 0.0
    for p, a in asks:
        take = min(left, a)
        usd += take * p
        left -= take
        if left <= 1e-12:
            return usd * (1 + FEE)
    return None

def step(coin, now):
    buy, sell, wd, tmin, inv_usd, batch = COINS[coin]
    sym = coin.split("@")[0]                          # "US@kucoin" → монета US, биржа покупки kucoin
    bs, bb = xpaper.book(sell, sym), xpaper.book(buy, sym)
    if not bs or not bb:
        return
    price = bs[0][0][0]
    c = S["c"].get(coin)
    if not c:
        c = S["c"][coin] = {"inv": inv_usd / price, "acc": 0.0, "transit": [], "p0": price,
                            "q0": inv_usd / price, "trades": 0, "miss": 0, "pnl": 0.0, "wd": 0.0,
                            "cool": 0, "log": []}
    c["px"] = price
    for t in list(c["transit"]):                       # пачка доехала → пополняем запас
        if now >= t[0]:
            c["inv"] += t[1]
            c["transit"].remove(t)
    thr = wd / batch + PROFIT
    q = TRADE / price
    got = xpaper.sell_usdt(bs[0], q)                   # продать из запаса на дорогой бирже
    cost = buy_cost(bb[1], q)                          # купить столько же на дешёвой
    edge = got / cost - 1 if got and cost else None
    c["edge"] = round(edge * 100, 2) if edge is not None else None
    sig = [bs[0][0][0], bb[1][0][0]]                   # цены стаканов: одно окно — одна сделка
    if edge is not None and edge >= thr and now >= c["cool"] and c.get("sig") != sig:
        c["sig"] = sig
        c["cool"] = now + COOL
        if c["inv"] >= q:
            c["inv"] -= q
            c["acc"] += q
            c["pnl"] += got - cost
            c["trades"] += 1
            c["log"] = (c["log"] + [[int(now), round(edge * 100, 2), round(got - cost, 3)]])[-10:]
        else:
            c["miss"] += 1                             # окно было, но запас кончился
    if c["acc"] > 0 and (c["acc"] * price >= batch or c["inv"] < q):   # пачка набралась или запас кончился → везём
        c["transit"].append([now + tmin * 60, c["acc"] - wd / price])
        c["wd"] += wd
        c["acc"] = 0.0

def summary():
    days = max((time.time() - S.get("t0", time.time())) / 86400, 1e-6)
    out, tot_h, tot_u = {}, 0.0, 0.0
    for coin, c in S.get("c", {}).items():
        buy, sell, wd, tmin, inv_usd, batch = COINS.get(coin, ("?", "?", 0, 0, 0, 1))
        price_fx = c["q0"] * (c.get("px", c["p0"]) - c["p0"])      # рост/падение цены запаса
        funding = FUND_DAY * inv_usd * days
        hedged = c["pnl"] - c["wd"] - funding
        unhedged = c["pnl"] - c["wd"] + price_fx
        tot_h += hedged
        tot_u += unhedged
        out[coin] = {"route": f"{buy}>{sell}", "trades": c["trades"], "missed": c["miss"],
                     "edge_now_pct": c.get("edge"), "threshold_pct": round((wd / batch + PROFIT) * 100, 2),
                     "trading_pnl": round(c["pnl"], 2), "transfer_fees": round(c["wd"], 2),
                     "price_effect": round(price_fx, 2), "net_hedged": round(hedged, 2),
                     "net_unhedged": round(unhedged, 2), "hedged_per_day": round(hedged / days, 2),
                     "last": c["log"][-5:]}
    return {"days": round(days, 2), "total_hedged": round(tot_h, 2), "total_unhedged": round(tot_u, 2),
            "per_day_hedged": round(tot_h / days, 2), "coins": out}

def loop():
    time.sleep(300)
    load()
    while True:
        now = time.time()
        for coin in COINS:
            try:
                step(coin, now)
            except Exception:
                log.exception("xinv %s", coin)
        if now - S["last_rep"] > 6 * 3600:
            S["last_rep"] = now
            s = summary()
            lines = [f"{k}: {v['trades']} сд., с хеджем {v['net_hedged']:+.2f}$, без {v['net_unhedged']:+.2f}$"
                     for k, v in s["coins"].items()]
            mm.tg(f"📦 [бумага] Запас + пачки, {s['days']} дн.: с хеджем {s['total_hedged']:+.2f}$ "
                  f"({s['per_day_hedged']:+.2f}$/день), без хеджа {s['total_unhedged']:+.2f}$\n" + "\n".join(lines))
        try:
            save()
        except Exception:
            log.exception("xinv save")
        time.sleep(STEP)

@mm.app.route("/inv")
def inv_page():
    if not (mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN):
        return "forbidden", 403
    return jsonify(summary())

threading.Thread(target=loop, daemon=True).start()