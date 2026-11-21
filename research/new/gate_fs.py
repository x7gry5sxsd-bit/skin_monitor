"""Проверка на Gate: что происходит с ценой бессрочника Gate вокруг расчёта фандинга Gate при экстремальной ставке (последние ~35 дней, 5-минутки; 7 дней, минутки)."""
import os as _os
HERE = _os.path.dirname(_os.path.abspath(__file__))
import os, sys, json, math, time, statistics as st, collections
sys.path.insert(0, HERE)
import gate
from concurrent.futures import ThreadPoolExecutor
D = HERE + "/gfs"
NOW = int(time.time()) // 300 * 300
A5 = NOW - 34 * 86400; A1 = NOW - 6 * 86400 - 3600
cs = [c for c in gate.contracts() if c["name"].endswith("_USDT") and not c.get("in_delisting")]
print("контрактов", len(cs), flush=True)
def fund(c):
    f = f"{D}/fr_{c}.json"
    if os.path.exists(f) and time.time() - os.path.getmtime(f) < 6 * 3600: return c, json.load(open(f))
    try: d = gate.get("/funding_rate", contract=c, limit=1000, **{"from": A5 - 86400, "to": NOW})
    except Exception: d = []
    rows = sorted((int(x["t"]), float(x["r"])) for x in d); json.dump(rows, open(f, "w")); return c, rows
with ThreadPoolExecutor(6) as ex: FR = dict(ex.map(fund, [c["name"] for c in cs]))
ev = []
for c, rows in FR.items():
    for i, (t, r) in enumerate(rows):
        if t < A5 + 7200 or t > NOW - 7200 or i == 0: continue
        if abs(r) >= 0.003 or (abs(r) <= 0.0001 and hash((c, t)) % 40 == 0):
            ev.append((c, t, r, t - rows[i - 1][0], rows[i - 1][1]))
print("событий", len(ev), collections.Counter(("ext−" if r <= -0.003 else "ext+" if r >= 0.003 else "ctl") for c, t, r, g, p in ev), flush=True)
need5 = sorted({c for c, t, r, g, p in ev})
def c5(c): return c, gate.candles(c, A5 - 3 * 3600, NOW, "5m")
with ThreadPoolExecutor(4) as ex: K5 = dict(ex.map(c5, need5))
btc = gate.candles("BTC_USDT", A5 - 3 * 3600, NOW, "5m")
def p5(k, x):                                 # цена в момент x (кратно 5 мин) = close свечи, открытой в x-300
    v = k.get(x - 300); return v[3] if v else None
W = [("шорт −5→+5м (платим фандинг)", -300, 300, 1), ("шорт −5→+15м (платим)", -300, 900, 1), ("шорт −5→+60м (платим)", -300, 3600, 1),
     ("шорт +5→+60м (без фандинга)", 300, 3600, 0), ("шорт +5→+30м (без фандинга)", 300, 1800, 0), ("плацебо −65→−5м", -3900, -300, 0)]
res = collections.defaultdict(list)
for c, t, r, gap, prev in ev:
    k = K5.get(c) or {}
    g = "ctl" if abs(r) <= 0.0001 else ("f≤−1%" if r <= -0.01 else "f −0.3…−1%" if r < 0 else "f≥+1%" if r >= 0.01 else "f +0.3…+1%")
    s = 1 if r > 0 else -1 if r < 0 else (1 if hash((c, t)) % 2 else -1)       # шорт при r<0 означает: мы на стороне, которая платит
    for name, a, b, payf in W:
        pa, pb, ba, bb = p5(k, t + a), p5(k, t + b), p5(btc, t + a), p5(btc, t + b)
        if not (pa and pb and ba and bb): continue
        rr = (pb / pa - 1) - (bb / ba - 1)
        pnl = s * rr                          # при r<0 «шорт» = -rr; при r>0 зеркально (лонг)
        pnl = -rr if r < 0 else rr if r > 0 else s * rr
        if payf: pnl -= abs(r)                # стоим в позиции, которая платит фандинг в момент расчёта
        res[(g, name)].append((t, c, pnl))
for g in ("f≤−1%", "f −0.3…−1%", "f +0.3…+1%", "f≥+1%", "ctl"):
    print("\n==", g, "(для r<0 — шорт; для r>0 — лонг, т.е. всегда сторона, которая ПЛАТИТ фандинг)")
    for name, a, b, payf in W:
        v = sorted(res.get((g, name), []))
        if len(v) < 15: continue
        p = [z for _, _, z in v]; n = len(p); mu = st.mean(p); se = (st.pstdev(p) or 1e-9) / math.sqrt(n); h = n // 2
        print(f"  {name:30} n={n:5} монет {len({c for _, c, _ in v}):3} | {mu*100:+.3f}% (t={mu/se:+.1f}) медиана {st.median(p)*100:+.3f}% | −0.2%: {(mu-0.002)*100:+.3f}% | половины {st.mean(p[:h])*100:+.2f}/{st.mean(p[h:])*100:+.2f}%")
json.dump([list(e) for e in ev], open(f"{D}/events.json", "w"))
