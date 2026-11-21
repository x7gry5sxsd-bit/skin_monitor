import os
HERE = os.path.dirname(os.path.abspath(__file__))
import sys, json, statistics as st, time, collections, os, math
sys.path.insert(0, HERE)
from kr_fund import *
def settles(coin, t0, t1):
    rows = sorted(json.load(open(f"{FD}/{coin}.json")))
    return rows, [(t, r) for t, r in rows if t0 < t <= t1]
def sim(thr, cost, hold=72, sl=0.20):
    out = []
    for h, src, coin, ur1, gr1, mult in sigs:
        tr = trade(h, coin, hold=hold, sl=sl)
        if not tr: continue
        t0, t1, pnl = tr
        rows, ss = settles(coin, t0, t1)
        fu, dodges = 0.0, 0
        for t, r in ss:
            prev = [x for tt, x in rows if tt < t]
            p = prev[-1] if prev else 0.0
            if thr is not None and p <= thr:
                dodges += 1                                   # закрыли до расчёта, открыли после: фандинг не платим
            else:
                fu += r
        out.append((t0, pnl + fu - dodges * cost, dodges))
    return out
for thr in (None, -0.005, -0.003, -0.0025, -0.002, -0.0015, -0.001):
    for cost in (0.0025, 0.004):
        o = sim(thr, cost)
        net = [x[1] for x in o]; h = len(net) // 2
        se = st.pstdev(net) / math.sqrt(len(net))
        bym = collections.defaultdict(float)
        for x in o: bym[time.strftime("%m", time.gmtime(x[0]))] += x[1] * 200
        print(f"уходим от расчёта при ставке ≤ {thr if thr is not None else '—':>7}, цена ухода {cost*100:.2f}%: "
              f"ИТОГ {st.mean(net)*100:+.2f}% (t={st.mean(net)/se:.1f}) в плюсе {sum(n>0 for n in net)/len(net):.0%} "
              f"| уходов на сделку {st.mean(x[2] for x in o):.1f} | половины {st.mean(net[:h])*100:+.1f}/{st.mean(net[h:])*100:+.1f}% "
              f"| мес.$: " + " ".join(f"{m}:{v:+.0f}" for m, v in sorted(bym.items())))
