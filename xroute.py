# xroute.py — поиск многоходовок между биржами по живым котировкам сканера xarb.
#   Нога X>Y: купить монету на X, перевести (открытая общая сеть, комиссия вывода), продать на Y.
#   Круг X⇄Y: прямая нога + обратная нога другой монетой вместо возврата USDT. Треугольник X>Y>Z>X.
import os, time, json, threading, logging, statistics, itertools
from flask import jsonify, request
import mm_scanner as mm
import xarb, xfer, xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

EXS = [e.strip() for e in env("XR_EXCH", "mexc,htx,gate,kucoin").split(",") if e.strip()]
FEE = xpaper.FEE
REF = float(env("XR_REF_USD", "50"))        # размер ноги: комиссия вывода делится на эту сумму
MIN_LEG = float(env("XR_MIN_LEG", "0.003"))  # нога считается рабочей от +0.3% после всех комиссий
GOOD = float(env("XR_GOOD", "0.005"))       # круг засчитывается от +0.5%
TOPN = int(env("XR_TOPN", "6"))             # маршрут перевода проверяем у 6 лучших монет направления
STEP = float(env("XR_STEP_SEC", "120"))
USDT_BACK = float(env("XR_USDT_BACK", "0.3"))   # возврат USDT напрямую, $ (с HTX по BEP20 — 0.01)
log = logging.getLogger("xroute")
R = {}

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'route_v1'", fetch=True)
    if r:
        R.update(json.loads(r[0][0]))
    for k in ("legs", "cycles"):
        R.setdefault(k, {})
    R.setdefault("t0", time.time())
    R.setdefault("scans", 0)
    R.setdefault("last_rep", time.time())

def save():
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('route_v1', %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(R),))

def legs_now(by):
    """Лучшие исполнимые ноги по каждому направлению X>Y (по котировкам + маршрут перевода)."""
    out = {}
    for x, y in itertools.permutations(EXS, 2):
        cand = []
        for c, q in by.items():
            if x not in q or y not in q:
                continue
            mids = [(v[0] + v[1]) / 2 for v in q.values() if v[0] > 0 and v[1] > 0]
            med = statistics.median(mids) if mids else 0
            ask, bid = q[x][1], q[y][0]
            if not med or ask <= 0 or bid <= 0 or abs(ask / med - 1) > 0.2 or abs(bid / med - 1) > 0.2:
                continue
            net = bid * (1 - FEE) / (ask * (1 + FEE)) - 1
            if net > 0.002:
                cand.append((net, c, ask))
        legs = []
        for net, c, ask in sorted(cand, reverse=True)[:TOPN]:
            try:
                rt = xfer.route(x, y, c, ask)
            except Exception:
                continue
            if rt.get("state") != "open" or rt.get("fee_usd") is None:
                continue
            after = net - rt["fee_usd"] / REF
            if after >= MIN_LEG:
                legs.append({"coin": c, "pct": round(after * 100, 2), "text": rt.get("text", "")[:60]})
        out[f"{x}>{y}"] = sorted(legs, key=lambda l: -l["pct"])
    return out

def scan_once():
    by = xarb.ST.get("by")
    if not by:
        return
    now = time.time()
    L = legs_now(by)
    R["scans"] += 1
    R["now"] = {k: v[:3] for k, v in L.items() if v}
    for k, v in L.items():                                   # статистика направлений
        s = R["legs"].setdefault(k, {"hits": 0, "sum": 0.0, "coins": {}})
        if v:
            s["hits"] += 1
            s["sum"] += v[0]["pct"]
            s["coins"][v[0]["coin"]] = s["coins"].get(v[0]["coin"], 0) + 1
    best = lambda k: (L.get(k) or [None])[0]
    for x, y in itertools.combinations(EXS, 2):              # круг X⇄Y: обратная нога вместо возврата USDT
        a, b = best(f"{x}>{y}"), best(f"{y}>{x}")
        if a and b:
            val = (1 + a["pct"] / 100) * (1 + b["pct"] / 100) - 1
            usdt = max(a["pct"], b["pct"]) / 100 - USDT_BACK / REF   # одна нога + возврат просто USDT
            key = f"{x}⇄{y}"
            s = R["cycles"].setdefault(key, {"hits": 0, "best": 0.0, "gain_vs_usdt": 0.0, "last": []})
            if val >= GOOD:
                s["hits"] += 1
                s["best"] = max(s["best"], round(val * 100, 2))
                s["gain_vs_usdt"] = round(s["gain_vs_usdt"] + (val - usdt) * 100, 2)
                s["last"] = (s["last"] + [[int(now), a["coin"], b["coin"], round(val * 100, 2)]])[-5:]
    for x, y, z in itertools.permutations(EXS, 3):           # треугольник X>Y>Z>X (без повторов поворотом)
        if x != min(x, y, z):
            continue
        a, b, c = best(f"{x}>{y}"), best(f"{y}>{z}"), best(f"{z}>{x}")
        if a and b and c:
            val = (1 + a["pct"] / 100) * (1 + b["pct"] / 100) * (1 + c["pct"] / 100) - 1
            key = f"{x}>{y}>{z}>{x}"
            s = R["cycles"].setdefault(key, {"hits": 0, "best": 0.0, "gain_vs_usdt": 0.0, "last": []})
            if val >= GOOD:
                s["hits"] += 1
                s["best"] = max(s["best"], round(val * 100, 2))
                s["last"] = (s["last"] + [[int(now), a["coin"], b["coin"], c["coin"], round(val * 100, 2)]])[-5:]

def summary():
    n = max(R.get("scans", 0), 1)
    legs = {k: {"open_share_pct": round(s["hits"] / n * 100, 1),
                "avg_best_pct": round(s["sum"] / s["hits"], 2) if s["hits"] else None,
                "top_coins": sorted(s["coins"].items(), key=lambda kv: -kv[1])[:6]}
            for k, s in R.get("legs", {}).items()}
    cyc = {k: {"share_pct": round(s["hits"] / n * 100, 1), "best_pct": s["best"],
               "extra_vs_usdt_pct_sum": s["gain_vs_usdt"], "last": s["last"]}
           for k, s in sorted(R.get("cycles", {}).items(), key=lambda kv: -kv[1]["hits"])}
    return {"hours": round((time.time() - R.get("t0", time.time())) / 3600, 1), "scans": n,
            "legs": dict(sorted(legs.items(), key=lambda kv: -kv[1]["open_share_pct"])),
            "cycles": cyc, "now": R.get("now", {})}

def loop():
    time.sleep(180)
    load()
    while True:
        try:
            scan_once()
            if time.time() - R["last_rep"] > 12 * 3600:
                R["last_rep"] = time.time()
                s = summary()
                top = [f"{k}: {v['share_pct']}% времени, лучший {v['best_pct']}%"
                       for k, v in list(s["cycles"].items())[:5] if v["share_pct"] > 0]
                mm.tg("🔁 [бумага] Многоходовки за " + str(s["hours"]) + " ч:\n" + ("\n".join(top) or "кругов нет"))
            save()
        except Exception:
            log.exception("xroute")
        time.sleep(STEP)

@mm.app.route("/route")
def route_page():
    if not (mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN):
        return "forbidden", 403
    return jsonify(summary())

threading.Thread(target=loop, daemon=True).start()