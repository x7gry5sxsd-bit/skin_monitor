"""Дрейф цены вокруг расчёта фандинга Binance UM (минутки): до расчёта / после / сквозь расчёт. Знак: «как выгодно при таком фандинге»."""
import os as _os
HERE = _os.path.dirname(_os.path.abspath(__file__))
import os, json, glob, math, time, statistics as st, collections, sys
D = HERE + "/bfr"; W = HERE + "/fsw"
T0 = 1761955200
fr = {os.path.basename(f)[:-5]: json.load(open(f)) for f in glob.glob(f"{D}/*.json")}
fr = {c: r for c, r in fr.items() if r}
PX = {}
def series(c, day):
    k = (c, day)
    if k not in PX:
        f = f"{W}/{c}_{day}.json"
        try: rows = json.load(open(f)) if os.path.exists(f) else None
        except Exception: rows = None
        PX[k] = {r[0]: r[4] for r in rows} if rows else {}
    return PX[k]
def px(c, x):                                            # цена в момент x = close минутки, открытой в x-60
    o = x - 60
    return series(c, time.strftime("%Y-%m-%d", time.gmtime(o))).get(o)
def ret(c, a, b):
    pa, pb = px(c, a), px(c, b)
    return pb / pa - 1 if pa and pb else None
COST = float(sys.argv[1]) if len(sys.argv) > 1 else 0.002   # круг: тейкер 0.05%×2 + проскальзывание
ev = []
for c, rows in fr.items():
    for i, (t, x) in enumerate(rows):
        if t < T0 or i == 0: continue
        prev = rows[i - 1][1]; gap = t - rows[i - 1][0]
        if abs(x) >= 0.001 or abs(prev) >= 0.001: kind = "ext"
        elif abs(x) <= 0.0002: kind = "ctl"
        else: continue
        ev.append((c, t, x, prev, gap, kind))
print("событий", len(ev), collections.Counter(e[5] for e in ev))
WIN = {"до: -60→-1м": (-3600, -60, "pre"), "до: -30→-1м": (-1800, -60, "pre"), "до: -10→-1м": (-600, -60, "pre"),
       "после: +1→+10м": (60, 600, "post"), "после: +1→+30м": (60, 1800, "post"), "после: +1→+60м": (60, 3600, "post"),
       "сквозь: -5→+5м": (-300, 300, "thru")}
res = collections.defaultdict(list)
for c, t, x, prev, gap, kind in ev:
    if kind == "ctl":
        if not (px(c, t - 3600) and px(c, t + 3600)): continue
        sg = 1 if (c, t) and hash((c, t)) % 2 else -1          # контроль: знак случайный
        bucket = "контроль |f|≤0.02%"
    else:
        ref = x if abs(x) >= 0.001 else prev
        sg = 1 if ref > 0 else -1
        a = abs(ref); bucket = ("f>0 " if sg > 0 else "f<0 ") + ("0.1–0.3%" if a < 0.003 else "0.3–1%" if a < 0.01 else "≥1%")
    for name, (a_, b_, typ) in WIN.items():
        r = ret(c, t + a_, t + b_); m = ret("BTC", t + a_, t + b_)
        if r is None: continue
        m = m or 0.0
        if typ == "pre":  pnl = -sg * (r - m)                # при f>0 — шорт до расчёта
        elif typ == "post": pnl = sg * (r - m)               # при f>0 — лонг после расчёта
        else: pnl = -sg * (r - m) + (abs(x) if kind == "ext" else 0)   # шорт сквозь расчёт + получаем фандинг
        res[(bucket, name)].append((t, pnl))
for b in sorted({k[0] for k in res}):
    print("\n==", b)
    for name in WIN:
        v = sorted(res.get((b, name), []))
        if len(v) < 30: continue
        p = [x for _, x in v]; n = len(p); mu = st.mean(p); se = st.pstdev(p) / math.sqrt(n); h = n // 2
        net = [x - COST for x in p]
        print(f"  {name:16} n={n:5} до издержек {mu*100:+.3f}% (t={mu/se:+.1f}) медиана {st.median(p)*100:+.3f}% | после {COST*100:.2f}%: {st.mean(net)*100:+.3f}% "
              f"в плюсе {sum(x>0 for x in net)/n:.0%} | половины {st.mean(p[:h])*100:+.2f}/{st.mean(p[h:])*100:+.2f}%")
