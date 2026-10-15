# xpredict.py — «прогнозист окон» (бумага). Раз в 5 мин по котировкам сканера (xarb, 9 бирж) для монет, которые
#   торгуются хотя бы на 2 из наших бирж (MEXC/HTX/Gate/KuCoin), считает 5 признаков: привычка к окнам (72 ч),
#   скорость цены (15/60 мин), всплеск объёма на одной бирже, тонкий стакан на HTX, «почти окно» сейчас.
#   Топ-20 — где окна вероятнее всего в ближайшие 3 ч. Сам проверяет себя: доля топ-20 с окнами против всех монет.
import os, sys, time, json, statistics, threading, logging
from flask import jsonify, request
import mm_scanner as mm
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

OURS = ["mexc", "htx", "gate", "kucoin"]
WIN = float(env("XP_WIN", "0.015"))               # «окно» = разница между нашими биржами ≥1.5% (до комиссий)
TOP = int(env("XP_TOP", "20"))
AHEAD = 3 * 3600                                  # прогноз на 3 часа вперёд
STEP = 300
log = logging.getLogger("xpredict")
P = {"ring": {}, "evals": [], "score": {}}

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'predict_v1'", fetch=True)
    if r:
        d = json.loads(r[0][0])
        P.update({k: d[k] for k in ("hours", "stats", "evals", "t0") if k in d})
    P.setdefault("hours", {})                     # монета -> {час: окон}
    P.setdefault("stats", {"checked": 0, "top_hit": 0, "base_hit": 0.0})
    P.setdefault("t0", time.time())

def save():
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('predict_v1', %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""",
             (json.dumps({k: P[k] for k in ("hours", "stats", "evals", "t0")}),))

def disp(q):
    """Разница между нашими биржами: max(bid)/min(ask) − 1 по разным биржам (без явно чужих цен)."""
    ours = {e: v for e, v in q.items() if e in OURS and v[0] > 0 and v[1] > 0}
    if len(ours) < 2:
        return None, None
    med = statistics.median((v[0] + v[1]) / 2 for v in ours.values())
    ours = {e: v for e, v in ours.items() if abs((v[0] + v[1]) / 2 / med - 1) <= 0.2}
    if len(ours) < 2:
        return None, med
    best = 0.0
    for a, va in ours.items():
        for b, vb in ours.items():
            if a != b:
                best = max(best, vb[0] / va[1] - 1)
    return best, med

def snap(now):
    X = sys.modules.get("xarb")
    by = (X.ST.get("by") or {}) if X else {}
    hb = str(int(now // 3600) * 3600)
    for coin, q in by.items():
        d, mid = disp(q)
        if d is None:
            continue
        ring = P["ring"].setdefault(coin, [])
        ring.append([now, mid, d, {e: v[4] for e, v in q.items() if e in OURS},
                     (q.get("htx") or (0, 0, 0, 0))[:4]])
        P["ring"][coin] = ring[-13:]                # час истории по 5 минут
        if d >= WIN:
            h = P["hours"].setdefault(coin, {})
            h[hb] = h.get(hb, 0) + 1
    old = now - 72 * 3600
    for coin in list(P["hours"]):
        P["hours"][coin] = {k: v for k, v in P["hours"][coin].items() if float(k) >= old}
        if not P["hours"][coin]:
            P["hours"].pop(coin)

def features(coin, now):
    ring = P["ring"].get(coin) or []
    if len(ring) < 4:
        return None
    t, mid, d, vols, hq = ring[-1]
    m15 = next((r[1] for r in reversed(ring) if now - r[0] >= 900), ring[0][1])
    m60 = ring[0][1]
    spike = 0.0
    for e, v in vols.items():                       # всплеск: прирост суточного объёма за час против среднего часа
        v0 = ring[0][3].get(e)
        if v and v0:
            spike = max(spike, (v - v0) / (v / 24))
    bid, ask, bq, aq = hq
    depth = min(bid * bq, ask * aq) if bid and ask and bq and aq else None
    habit = len(P["hours"].get(coin, {})) / 72
    return {"habit": round(habit, 3), "move15": round(abs(mid / m15 - 1), 4), "move60": round(abs(mid / m60 - 1), 4),
            "vol_spike": round(spike, 2), "htx_depth_usd": round(depth) if depth else None, "near": round(d, 4)}

def score(f):
    thin = 1.0 if f["htx_depth_usd"] is not None and f["htx_depth_usd"] < 300 else (
        0.5 if f["htx_depth_usd"] is not None and f["htx_depth_usd"] < 1500 else 0.0)
    return round(100 * (0.35 * min(f["habit"] / 0.3, 1) + 0.2 * min(f["move60"] / 0.05, 1)
                        + 0.1 * min(f["move15"] / 0.02, 1) + 0.15 * min(max(f["vol_spike"], 0) / 3, 1)
                        + 0.1 * thin + 0.1 * min(f["near"] / WIN, 1)), 1)

def rank(now):
    out = {}
    for coin in P["ring"]:
        f = features(coin, now)
        if f:
            out[coin] = {**f, "score": score(f)}
    P["score"] = dict(sorted(out.items(), key=lambda kv: -kv[1]["score"]))
    return P["score"]

def had_window(coin, t0, t1):
    """Были ли окна в часах, начавшихся ПОСЛЕ прогноза (без подглядывания в прошлое)."""
    return any(t0 <= float(k) < t1 for k in P["hours"].get(coin, {}))

def evaluate(now):
    """Через 3 ч: какая доля топа дала окна, и какая доля всех монет — для сравнения."""
    keep = []
    universe = list(P["ring"])
    for t, top in P["evals"]:
        if now - t < AHEAD:
            keep.append([t, top])
            continue
        hit = sum(1 for c in top if had_window(c, t, t + AHEAD))
        base = sum(1 for c in universe if had_window(c, t, t + AHEAD)) / max(len(universe), 1)
        st = P["stats"]
        st["checked"] += 1
        st["top_hit"] += hit / max(len(top), 1)
        st["base_hit"] += base
    P["evals"] = keep[-60:]

def text():
    st = P.get("stats", {})
    top = ", ".join(list(P.get("score", {}))[:6]) or "копится"
    if not st.get("checked"):
        return f"🔮 ПРОГНОЗ: сейчас вероятнее всего окна у {top} | точность — через 3 ч после старта"
    p, b = st["top_hit"] / st["checked"], st["base_hit"] / st["checked"]
    return (f"🔮 ПРОГНОЗ: сейчас вероятнее всего окна у {top} | точность: топ-{TOP} попадает в {p*100:.0f}%, "
            f"случайная монета — {b*100:.0f}% (×{p / b:.1f})" if b else f"🔮 ПРОГНОЗ: {top}")

def loop():
    time.sleep(420)
    load()
    n = 0
    while True:
        now = time.time()
        try:
            snap(now)
            sc = rank(now)
            if len(sc) >= TOP:
                P["evals"].append([now, list(sc)[:TOP]])
            evaluate(now)
            n += 1
            if n % 3 == 0:
                save()
        except Exception:
            log.exception("xpredict")
        time.sleep(STEP)

@mm.app.route("/predict")
def predict_page():
    if not (mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN):
        return "forbidden", 403
    st = P.get("stats", {})
    acc = {"checks": st.get("checked", 0)}
    if st.get("checked"):
        acc.update({"top_hit_pct": round(st["top_hit"] / st["checked"] * 100, 1),
                    "random_hit_pct": round(st["base_hit"] / st["checked"] * 100, 1)})
    return jsonify({"text": text(), "accuracy": acc, "coins_tracked": len(P.get("ring", {})),
                    "top": dict(list(P.get("score", {}).items())[:TOP])})

threading.Thread(target=loop, daemon=True).start()
