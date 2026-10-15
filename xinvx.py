# xinvx.py — бумажный магазин на ВСЕХ парах бирж (MEXC, HTX, Gate, KuCoin). Маршруты не задаются руками:
#   раз в 10 мин берёт монеты из живых направлений сканера xroute, проверяет путь перевода (xfer: сеть, комиссия),
#   и ведёт по каждому «монета: покупка>продажа» бумажный магазин — запас $100 на бирже продажи, докупка на бирже
#   покупки, пачки с реальной задержкой сети. Маршруты без сделок 48 ч выбывают. /invx?t=...
import os, sys, time, json, threading, logging
from flask import jsonify, request
import mm_scanner as mm
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

TRADE = float(env("XX_TRADE_USD", "15"))
INV = float(env("XX_INV_USD", "100"))
MAXR = int(env("XX_MAX_ROUTES", "40"))
MIN_HITS = int(env("XX_MIN_HITS", "3"))           # монета должна попасть в лучшие по направлению хотя бы 3 раза
PROFIT = float(env("XX_PROFIT", "0.005"))
COOL = 90
FUND_DAY = 0.0003
FAST = {"SUI", "ARB", "SOL", "BASE", "BEP20", "TRC20", "APT", "TON", "OP", "POLYGON", "AVAXC"}
log = logging.getLogger("xinvx")
S = {}
BK = {}

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'invx_v1'", fetch=True)
    if r:
        S.update(json.loads(r[0][0]))
    S.setdefault("c", {})
    S.setdefault("t0", time.time())

def save():
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('invx_v1', %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(S),))

def book(ex, coin):
    k = (ex, coin)
    if k not in BK:
        BK[k] = xpaper.book(ex, coin)
    return BK[k]

def buy_cost(asks, qty, ex):
    left, usd = qty, 0.0
    for p, a in asks:
        take = min(left, a)
        usd += take * p
        left -= take
        if left <= 1e-12:
            return usd * (1 + xpaper.fee(ex))
    return None

def minutes(buy, sell, net):
    xt = sys.modules.get("xtime")
    base = 3 if net in FAST else (20 if net == "ERC20" else 30)
    try:
        return xt.eta(buy, sell, net, base) if xt else base
    except Exception:
        return base

def discover(now):
    """Новые маршруты из направлений сканера: монета, которую xroute часто видит в лучших, и путь открыт."""
    xr, xf = sys.modules.get("xroute"), sys.modules.get("xfer")
    if not xr or not xf:
        return
    for leg, s in (xr.R.get("legs") or {}).items():
        buy, sell = leg.split(">")
        for coin, hits in sorted(s.get("coins", {}).items(), key=lambda kv: -kv[1])[:6]:
            key = f"{coin}:{leg}"
            if key in S["c"] or hits < MIN_HITS or len(S["c"]) >= MAXR:
                continue
            b = book(buy, coin)
            if not b:
                continue
            try:
                rt = xf.route(buy, sell, coin, b[1][0][0])
            except Exception:
                continue
            if rt.get("state") != "open" or rt.get("fee_usd") is None:
                continue
            net = (rt.get("text", "").split("сеть ")[-1].split(",")[0] or "?").upper()
            fee = float(rt["fee_usd"])
            batch = 50.0 if net in FAST else 100.0
            S["c"][key] = {"buy": buy, "sell": sell, "net": net, "wd": fee, "tmin": minutes(buy, sell, net),
                           "batch": batch, "born": now, "last_trade": now, "trades": 0, "miss": 0, "pnl": 0.0,
                           "wdsum": 0.0, "acc": 0.0, "transit": [], "cool": 0, "log": []}

def step(key, c, now):
    coin = key.split(":")[0]
    bs, bb = book(c["sell"], coin), book(c["buy"], coin)
    if not bs or not bb:
        return
    price = bs[0][0][0]
    if "inv" not in c:
        c["inv"], c["p0"], c["q0"] = INV / price, price, INV / price
    c["px"] = price
    for t in list(c["transit"]):
        if now >= t[0]:
            c["inv"] += t[1]
            c["transit"].remove(t)
    thr = c["wd"] / c["batch"] + PROFIT
    q = TRADE / price
    got, cost = xpaper.sell_usdt(bs[0], q, c["sell"]), buy_cost(bb[1], q, c["buy"])
    edge = got / cost - 1 if got and cost else None
    c["edge"] = round(edge * 100, 2) if edge is not None else None
    sig = [bs[0][0][0], bb[1][0][0]]
    if edge is not None and edge >= thr and now >= c["cool"] and c.get("sig") != sig:
        c["sig"], c["cool"] = sig, now + COOL
        if c["inv"] >= q:
            c["inv"] -= q
            c["acc"] += q
            c["pnl"] += got - cost
            c["trades"] += 1
            c["last_trade"] = now
            c["log"] = (c["log"] + [[int(now), round(edge * 100, 2), round(got - cost, 3)]])[-10:]
        else:
            c["miss"] += 1
    if c["acc"] > 0 and (c["acc"] * price >= c["batch"] or c["inv"] < q):
        c["transit"].append([now + c["tmin"] * 60, c["acc"] - c["wd"] / price])
        c["wdsum"] += c["wd"]
        c["acc"] = 0.0

def prune(now):
    """Маршрут 48 ч без сделок — выбывает (место для новых)."""
    for key in [k for k, c in S["c"].items() if now - c["last_trade"] > 48 * 3600 and now - c["born"] > 48 * 3600]:
        S["c"].pop(key)

def row(key, c, now):
    days = max((now - c["born"]) / 86400, 0.25)      # меньше 6 часов — в сутки не пересчитываем
    fx = c.get("q0", 0) * (c.get("px", 0) - c.get("p0", 0))
    hedged = c["pnl"] - c["wdsum"] - FUND_DAY * INV * days
    return {"route": f"{c['buy']}>{c['sell']}", "net": c["net"], "trades": c["trades"], "missed": c["miss"],
            "edge_now_pct": c.get("edge"), "threshold_pct": round((c["wd"] / c["batch"] + PROFIT) * 100, 2),
            "net_hedged": round(hedged, 2), "net_unhedged": round(c["pnl"] - c["wdsum"] + fx, 2),
            "hedged_per_day": round(hedged / days, 2), "days": round(days, 1), "last": c["log"][-3:]}

def summary():
    now = time.time()
    rows = {k: row(k, c, now) for k, c in S.get("c", {}).items()}
    rows = dict(sorted(rows.items(), key=lambda kv: -kv[1]["hedged_per_day"]))
    by_dir = {}
    for r in rows.values():
        d = by_dir.setdefault(r["route"], [0, 0.0])
        d[0] += r["trades"]
        d[1] += r["net_hedged"]
    return {"routes": len(rows), "by_direction": {k: {"trades": v[0], "net_hedged": round(v[1], 2)}
                                                   for k, v in sorted(by_dir.items(), key=lambda kv: -kv[1][1])},
            "coins": rows}

def text():
    s = summary()
    if not s["routes"]:
        return "🌐 Бумага на всех биржах: маршруты подбираются"
    top = [f"{k} {v['hedged_per_day']:+.2f}$/сут ({v['trades']})" for k, v in list(s["coins"].items())[:4]]
    dirs = [f"{k} {v['net_hedged']:+.2f}$" for k, v in list(s["by_direction"].items())[:4]]
    return f"🌐 БУМАГА ВСЕ БИРЖИ ({s['routes']} маршрутов): лидеры {', '.join(top)}\n  по направлениям: {', '.join(dirs)}"

def loop():
    time.sleep(360)
    load()
    n = 0
    while True:
        now = time.time()
        BK.clear()
        try:
            if n % 10 == 0:
                discover(now)
                prune(now)
            for key, c in list(S["c"].items()):
                step(key, c, now)
            if n % 5 == 0:
                save()
            n += 1
        except Exception:
            log.exception("xinvx")
        time.sleep(60)

@mm.app.route("/invx")
def invx_page():
    if not (mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN):
        return "forbidden", 403
    return jsonify(summary())

threading.Thread(target=loop, daemon=True).start()
