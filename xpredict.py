# xpredict.py v2 — «прогнозист окон» (бумага). Раз в 5 мин по котировкам сканера (xarb, 9 бирж) для монет, которые
#   торгуются хотя бы на 2 из наших бирж (MEXC/HTX/Gate/KuCoin), считает 5 признаков: привычка к окнам (72 ч),
#   скорость цены (15/60 мин), всплеск объёма, тонкий стакан на HTX, «почти окно» сейчас.
#   v2: топ отбирает только ТОРГУЕМЫЕ монеты (путь перевода открыт, фьючерс для хеджа, стакан ≥ $15), сам заводит
#   их в бумажный магазин на всех биржах (xinvx) и считает точность ПО ПРИБЫЛИ: сколько бумага заработала на
#   монетах из прогноза за 3 ч против остальных. /predict?t=...
import os, sys, time, json, statistics, threading, logging
from flask import jsonify, request
import mm_scanner as mm
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

OURS = ["mexc", "htx", "gate", "kucoin"]
WIN = float(env("XP_WIN", "0.015"))               # «окно» = разница между нашими биржами ≥1.5% (до комиссий)
TOP = int(env("XP_TOP", "20"))
DEPTH = float(env("XP_MIN_DEPTH", "15"))          # стакан по лучшей цене хотя бы на $15
AHEAD = 3 * 3600                                  # прогноз на 3 часа вперёд
STEP = 300
log = logging.getLogger("xpredict")
P = {"ring": {}, "evals": [], "score": {}, "trade": {}, "rc": {}}

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'predict_v2'", fetch=True)
    if r:
        d = json.loads(r[0][0])
        P.update({k: d[k] for k in ("hours", "stats", "evals", "t0") if k in d})
    else:
        r1 = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'predict_v1'", fetch=True)   # привычку не теряем
        if r1:
            P["hours"] = json.loads(r1[0][0]).get("hours", {})
    P.setdefault("hours", {})
    P.setdefault("stats", {})
    for k in ("checked", "top_hit", "base_hit", "p_checked", "p_pred", "p_base"):
        P["stats"].setdefault(k, 0.0)
    P.setdefault("t0", time.time())

def save():
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('predict_v2', %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""",
             (json.dumps({k: P[k] for k in ("hours", "stats", "evals", "t0")}),))

def disp(q):
    """Разница между нашими биржами: (лучшая разница, медиана, где купить, где продать)."""
    ours = {e: v for e, v in q.items() if e in OURS and v[0] > 0 and v[1] > 0}
    if len(ours) < 2:
        return None, None, None, None
    med = statistics.median((v[0] + v[1]) / 2 for v in ours.values())
    ours = {e: v for e, v in ours.items() if abs((v[0] + v[1]) / 2 / med - 1) <= 0.2}
    best, pair = None, (None, None)
    for a, va in ours.items():
        for b, vb in ours.items():
            if a != b and (best is None or vb[0] / va[1] - 1 > best):
                best, pair = vb[0] / va[1] - 1, (a, b)
    return best, med, pair[0], pair[1]

def snap(now):
    X = sys.modules.get("xarb")
    by = (X.ST.get("by") or {}) if X else {}
    hb = str(int(now // 3600) * 3600)
    for coin, q in by.items():
        d, mid, buy, sell = disp(q)
        if d is None:
            continue
        depth = None
        if buy and sell:
            ab, bb = q[buy], q[sell]
            if ab[3] and bb[2]:                     # объём по лучшим ценам (если биржа его отдаёт)
                depth = min(ab[1] * ab[3], bb[0] * bb[2])
        ring = P["ring"].setdefault(coin, [])
        ring.append([now, mid, d, {e: v[4] for e, v in q.items() if e in OURS},
                     (q.get("htx") or (0, 0, 0, 0))[:4], buy, sell, depth])
        P["ring"][coin] = ring[-13:]
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
    t, mid, d, vols, hq, buy, sell, depth = ring[-1]
    m15 = next((r[1] for r in reversed(ring) if now - r[0] >= 900), ring[0][1])
    m60 = ring[0][1]
    spike = 0.0
    for e, v in vols.items():
        v0 = ring[0][3].get(e)
        if v and v0:
            spike = max(spike, (v - v0) / (v / 24))
    bid, ask, bq, aq = hq
    hdepth = min(bid * bq, ask * aq) if bid and ask and bq and aq else None
    habit = len(P["hours"].get(coin, {})) / 72
    return {"habit": round(habit, 3), "move15": round(abs(mid / m15 - 1), 4), "move60": round(abs(mid / m60 - 1), 4),
            "vol_spike": round(spike, 2), "htx_depth_usd": round(hdepth) if hdepth else None, "near": round(d, 4),
            "leg": f"{buy}>{sell}" if buy and sell else None, "pair_depth_usd": round(depth) if depth else None}

def score(f):
    thin = 1.0 if f["htx_depth_usd"] is not None and f["htx_depth_usd"] < 300 else (
        0.5 if f["htx_depth_usd"] is not None and f["htx_depth_usd"] < 1500 else 0.0)
    return round(100 * (0.35 * min(f["habit"] / 0.3, 1) + 0.2 * min(f["move60"] / 0.05, 1)
                        + 0.1 * min(f["move15"] / 0.02, 1) + 0.15 * min(max(f["vol_spike"], 0) / 3, 1)
                        + 0.1 * thin + 0.1 * min(f["near"] / WIN, 1)), 1)

def hedgeable():
    X = sys.modules.get("xhedge")
    try:
        pp = X.perps() if X else {}
        return set().union(*[set(v) for v in pp.values()]) if pp else None
    except Exception:
        return None

def tradeable(coin, f, now, hg):
    """Можно ли на этом заработать: фьючерс для хеджа, стакан ≥ $15, путь перевода открыт (кэш 30 мин)."""
    if hg is not None and coin not in hg:
        return False, "нет фьючерса"
    if f["pair_depth_usd"] is not None and f["pair_depth_usd"] < DEPTH:
        return False, "пустой стакан"
    if not f["leg"]:
        return False, "нет пары"
    c = P["rc"].get(f"{coin}:{f['leg']}")
    if c and now - c[0] < 1800:
        return c[1], c[2]
    xf, ok, why = sys.modules.get("xfer"), False, "путь закрыт"
    try:
        buy, sell = f["leg"].split(">")
        rt = xf.route(buy, sell, coin, 1.0) if xf else {}
        ok = rt.get("state") == "open"
    except Exception:
        why = "не проверить путь"
    P["rc"][f"{coin}:{f['leg']}"] = (now, ok, "" if ok else why)
    return ok, "" if ok else why

def rank(now):
    out = {}
    for coin in P["ring"]:
        f = features(coin, now)
        if f:
            out[coin] = {**f, "score": score(f)}
    P["score"] = dict(sorted(out.items(), key=lambda kv: -kv[1]["score"]))
    hg, trade = hedgeable(), {}
    for coin, f in list(P["score"].items())[:80]:   # путь проверяем только у лучших 80 — бережём запросы
        ok, why = tradeable(coin, f, now, hg)
        f["tradeable"] = ok if ok else why
        if ok and len(trade) < TOP:
            trade[coin] = f
    P["trade"] = trade
    return P["score"]

def netmap():
    """Накопленная чистая прибыль бумаги по монетам (xinv + xinvx), $."""
    out = {}
    I, X = sys.modules.get("xinv"), sys.modules.get("xinvx")
    for c, v in (I.S.get("c", {}).items() if I else []):
        k = c.split("@")[0]
        out[k] = out.get(k, 0.0) + v["pnl"] - v["wd"]
    for key, v in (X.S.get("c", {}).items() if X else []):
        k = key.split(":")[0]
        out[k] = out.get(k, 0.0) + v["pnl"] - v["wdsum"]
    return out

def had_window(coin, t0, t1):
    """Были ли окна в часах, начавшихся ПОСЛЕ прогноза (без подглядывания в прошлое)."""
    return any(t0 <= float(k) < t1 for k in P["hours"].get(coin, {}))

def evaluate(now):
    keep, universe, nm = [], list(P["ring"]), netmap()
    st = P["stats"]
    for e in P["evals"]:
        t, top = e[0], e[1]
        if now - t < AHEAD:
            keep.append(e)
            continue
        st["checked"] += 1
        st["top_hit"] += sum(1 for c in top if had_window(c, t, t + AHEAD)) / max(len(top), 1)
        st["base_hit"] += sum(1 for c in universe if had_window(c, t, t + AHEAD)) / max(len(universe), 1)
        if len(e) > 3:                              # прибыль бумаги за 3 ч: прогноз против остальных
            pred, snap0 = e[2], e[3]
            both = [c for c in nm if c in snap0 or c in pred]
            d = {c: nm[c] - snap0.get(c, 0.0) for c in both}
            p = [d[c] for c in pred if c in d]
            rest = [v for c, v in d.items() if c not in pred]
            if p and rest:
                st["p_checked"] += 1
                st["p_pred"] += sum(p) / len(p)
                st["p_base"] += sum(rest) / len(rest)
    P["evals"] = keep[-60:]

def text():
    st = P.get("stats", {})
    top = ", ".join(list(P.get("trade", {}))[:6]) or "копится"
    s = f"🔮 ПРОГНОЗ (торгуемые): {top}"
    if st.get("checked"):
        p, b = st["top_hit"] / st["checked"], st["base_hit"] / st["checked"]
        s += f" | разница цен: топ {p*100:.0f}% vs случайно {b*100:.0f}%"
    if st.get("p_checked"):
        pp, pb = st["p_pred"] / st["p_checked"], st["p_base"] / st["p_checked"]
        s += f" | ПРИБЫЛЬ бумаги за 3 ч на монету: прогноз {pp:+.3f}$ vs остальные {pb:+.3f}$"
    else:
        s += " | точность по прибыли — через 3 ч"
    return s

def loop():
    time.sleep(420)
    load()
    n = 0
    while True:
        now = time.time()
        try:
            snap(now)
            sc = rank(now)
            X = sys.modules.get("xinvx")
            for coin, f in P["trade"].items():      # торгуемые монеты из прогноза — в бумагу, проверять прибылью
                if X and f["leg"]:
                    X.add_predicted(coin, f["leg"], now)
            if len(sc) >= TOP:
                P["evals"].append([now, list(sc)[:TOP], list(P["trade"]), netmap()])
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
    acc = {"checks": st.get("checked", 0), "profit_checks": st.get("p_checked", 0)}
    if st.get("checked"):
        acc.update({"top_hit_pct": round(st["top_hit"] / st["checked"] * 100, 1),
                    "random_hit_pct": round(st["base_hit"] / st["checked"] * 100, 1)})
    if st.get("p_checked"):
        acc.update({"pred_profit_3h": round(st["p_pred"] / st["p_checked"], 3),
                    "other_profit_3h": round(st["p_base"] / st["p_checked"], 3)})
    raw = list(P.get("score", {}).items())[:TOP]
    return jsonify({"text": text(), "accuracy": acc, "coins_tracked": len(P.get("ring", {})),
                    "top_tradeable": P.get("trade", {}),
                    "top_raw": {c: {"score": f["score"], "tradeable": f.get("tradeable", "—")} for c, f in raw}})

threading.Thread(target=loop, daemon=True).start()
