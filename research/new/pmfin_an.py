"""Ежедневные рынки Polymarket «вверх/вниз» (акции, индексы, золото, серебро) против «справедливой» вероятности по цене бессрочника Gate (торгуется 24/7)."""
import os as _os
HERE = _os.path.dirname(_os.path.abspath(__file__))
import sys, json, math, time, statistics as st, collections, bisect
sys.path.insert(0, HERE)
import gate
D = HERE + "/pmfin"
R = json.load(open(f"{D}/markets.json"))
NOW = int(time.time()) // 3600 * 3600
cre = {c["name"]: int(c.get("create_time") or 0) for c in gate.contracts()}
K1, K5 = {}, {}
for con in {r["con"] for r in R}:
    a = max(cre.get(con, 0) // 3600 * 3600, NOW - 9800 * 3600)
    K1[con] = gate.candles(con, a, NOW, "1h"); K5[con] = gate.candles(con, NOW - 34 * 86400, NOW, "5m")
def perp(con, t):                           # цена бессрочника в момент t: close последней завершённой свечи (5м, иначе 1ч)
    k5 = K5[con]; x = (int(t) // 300) * 300 - 300
    for j in range(3):
        if x - j * 300 in k5: return k5[x - j * 300][3]
    k1 = K1[con]; x = (int(t) // 3600) * 3600 - 3600
    for j in range(2):
        if x - j * 3600 in k1: return k1[x - j * 3600][3]
    return None
def ncdf(z): return 0.5 * (1 + math.erf(z / math.sqrt(2)))
HALF = float(sys.argv[1]) if len(sys.argv) > 1 else 0.01
THR = float(sys.argv[2]) if len(sys.argv) > 2 else 0.08
def us_open(end):                           # открытие NYSE в день рынка: закрытие в 20:00 UTC (лето) → 13:30; 21:00 UTC (зима) → 14:30
    return end - 6.5 * 3600
by = collections.defaultdict(list)
for r in R: by[r["slug"]].append(r)
rows = []
for slug, L in by.items():
    L.sort(key=lambda r: r["end"])
    metal = slug.startswith(("gold", "silver"))
    for i in range(1, len(L)):
        r, pr = L[i], L[i - 1]
        if r["end"] - pr["end"] > 5 * 86400: continue
        con = r["con"]; C = perp(con, pr["end"])
        if not C or len(r["h"]) < 5: continue
        # дневная волатильность по закрытиям прошлых 30 рынков этой серии (без заглядывания вперёд)
        past = [perp(con, x["end"]) for x in L[max(0, i - 31):i]]
        past = [p for p in past if p]
        if len(past) < 8: continue
        sd = st.pstdev([math.log(b / a) for a, b in zip(past, past[1:])]) or 0.01
        open_mk = slug.startswith("spx-open")
        T = us_open(r["end"]) if open_mk else r["end"]                    # момент, когда исход определён
        o = us_open(r["end"])
        ts_ = [t for t, p in r["h"]]
        for t in range(int(r["start"]) // 1800 * 1800 + 3600, int(T) - 600, 1800):
            j = bisect.bisect_right(ts_, t) - 1
            if j < 0 or t - ts_[j] > 3600: continue
            p = r["h"][j][1]
            if not (0.03 <= p <= 0.97): continue
            S = perp(con, t)
            if not S: continue
            if metal: frac = max(T - t, 0) / (23 * 3600)
            elif open_mk: frac = 0.2 * max(T - t, 0) / max(T - pr["end"], 1)
            else: frac = (0.2 * max(o - t, 0) / max(o - pr["end"], 1)) + 0.8 * min(max(T - max(t, o), 0) / (6.5 * 3600), 1)
            frac = min(max(frac, 0.0005), 1.2)
            fair = ncdf(math.log(S / C) / (sd * math.sqrt(frac)))
            rows.append({"slug": slug, "id": r["tok"], "t": t, "end": r["end"], "tau": (T - t) / 3600, "p": p, "fair": fair, "y": r["y"], "vol": r.get("vol") or 0, "m": math.log(S / C) / sd})
print("точек", len(rows), "рынков", len({x["id"] for x in rows}))
def brier(v, key): return st.mean((x[key] - x["y"]) ** 2 for x in v)
for lo, hi in ((0, 2), (2, 6), (6, 12), (12, 40)):
    v = [x for x in rows if lo <= x["tau"] < hi]
    if len(v) < 50: continue
    print(f"до исхода {lo:2}–{hi:2} ч: n={len(v):5} Брайер: рынок {brier(v,'p'):.4f} | модель по Gate {brier(v,'fair'):.4f} | средн.|модель−рынок| {st.mean(abs(x['fair']-x['p']) for x in v):.3f}")
def fee(q): return 0.05 * q * (1 - q)
def sim(thr, tmin=0.25, tmax=40, label=""):
    trades = []
    seen = set()
    for x in sorted(rows, key=lambda x: (x["id"], x["t"])):
        if x["id"] in seen or not (tmin <= x["tau"] < tmax): continue
        up_q = x["p"] + HALF; dn_q = (1 - x["p"]) + HALF
        if x["fair"] - up_q - fee(up_q) > thr and up_q < 0.97:
            q, win = up_q, x["y"]
        elif (1 - x["fair"]) - dn_q - fee(dn_q) > thr and dn_q < 0.97:
            q, win = dn_q, 1 - x["y"]
        else: continue
        seen.add(x["id"]); trades.append((x["t"], x["slug"], (win - q - fee(q)) / q, q, win, x["tau"]))
    if len(trades) < 10: print(f"  {label} порог {thr:.2f}: сделок {len(trades)}"); return trades
    roi = [z[2] for z in trades]; n = len(roi); h = n // 2; trades.sort()
    se = st.pstdev(roi) / math.sqrt(n)
    print(f"  {label} порог {thr:.2f}: сделок {n:4} | доход на $ {st.mean(roi)*100:+.1f}% (t={st.mean(roi)/se:+.1f}) | угадано {st.mean(z[4] for z in trades):.0%} при средней цене {st.mean(z[3] for z in trades):.2f} "
          f"| половины {st.mean(z[2] for z in trades[:h])*100:+.1f}/{st.mean(z[2] for z in trades[h:])*100:+.1f}%")
    return trades
print(f"\nСделка: покупаем сторону, если модель − (цена + {HALF:.2f} спред + комиссия) > порога; одна сделка на рынок")
for thr in (0.05, 0.08, 0.12, 0.2):
    tr = sim(thr, label="все серии  ")
tr = sim(THR, label="все серии  ")
c = collections.defaultdict(list)
for t, s, roi, q, w, tau in tr: c[s].append(roi)
for s, v in sorted(c.items(), key=lambda kv: -len(kv[1])):
    print(f"     {s:28} n={len(v):3} доход на $ {st.mean(v)*100:+.1f}%")
for lo, hi in ((0.25, 2), (2, 6), (6, 40)):
    sim(THR, lo, hi, label=f"за {lo}–{hi} ч")
