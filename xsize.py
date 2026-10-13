# xsize.py — замер «сколько денег выдерживает окно»: когда окно HTX дороже MEXC/Gate открыто на $15,
#   проверяем по живым стаканам, остаётся ли оно на $30 / $50 / $100 / $200. Только наблюдение, без сделок.
import os, time, json, threading, logging
from flask import jsonify, request
import mm_scanner as mm
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

COINS = sorted({c.strip().upper() for c in (env("XE_COINS", "BLUE,MAGIC") + "," + env("XS_COINS", env("XS_COIN", "US"))
                                             + "," + env("XZ_EXTRA", "")).split(",") if c.strip()})
VENUES = ["mexc", "gate"]
SIZES = [15, 30, 50, 100, 200]
OPEN = float(env("XZ_OPEN", "0.012"))        # окно «открыто», если на $15 разница ≥1.2% (до комиссии вывода)
STEP = float(env("XZ_STEP_SEC", "30"))
FEE = xpaper.FEE
log = logging.getLogger("xsize")
Z = {}

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'size_v1'", fetch=True)
    if r:
        Z.update(json.loads(r[0][0]))
    Z.setdefault("c", {})
    Z.setdefault("t0", time.time())

def save():
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('size_v1', %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(Z),))

def buy_cost(asks, qty):
    left, usd = qty, 0.0
    for pr, a in asks:
        take = min(left, a)
        usd += take * pr
        left -= take
        if left <= 1e-12:
            return usd * (1 + FEE)
    return None

def edges(bh, bv):
    price, out = bh[0][0][0], {}
    for s in SIZES:
        q = s / price
        got, cost = xpaper.sell_usdt(bh[0], q), buy_cost(bv[1], q)
        out[s] = (got / cost - 1) if got and cost else None   # None — стакана не хватает на такой объём
    return out

def scan():
    for coin in COINS:
        bh = xpaper.book("htx", coin)
        if not bh:
            continue
        for v in VENUES:
            bv = xpaper.book(v, coin)
            if not bv:
                continue
            e = edges(bh, bv)
            if e[SIZES[0]] is None or e[SIZES[0]] < OPEN:
                continue
            k = f"{coin}@{v}"
            s = Z["c"].setdefault(k, {"open": 0, "hold": {str(x): 0 for x in SIZES}, "sum": {str(x): 0.0 for x in SIZES}})
            s["open"] += 1
            for x in SIZES:
                if e[x] is not None:
                    s["sum"][str(x)] += e[x]
                    s["hold"][str(x)] += e[x] >= OPEN
            s["last"] = [int(time.time())] + [round(e[x] * 100, 2) if e[x] is not None else None for x in SIZES]

def summary():
    out = {}
    for k, s in Z.get("c", {}).items():
        n = max(s["open"], 1)
        out[k] = {"open_samples": s["open"],
                  "window_holds_pct": {f"${x}": round(s["hold"][str(x)] / n * 100) for x in SIZES},
                  "avg_edge_pct": {f"${x}": round(s["sum"][str(x)] / n * 100, 2) for x in SIZES},
                  "last": s.get("last")}
    return {"hours": round((time.time() - Z.get("t0", time.time())) / 3600, 1), "open_threshold_pct": OPEN * 100,
            "coins": out}

def loop():
    time.sleep(120)
    load()
    while True:
        try:
            scan()
            save()
        except Exception:
            log.exception("xsize")
        time.sleep(STEP)

@mm.app.route("/size")
def size_page():
    if not (mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN):
        return "forbidden", 403
    return jsonify(summary())

threading.Thread(target=loop, daemon=True).start()
