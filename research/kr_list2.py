import os
HERE = os.path.dirname(os.path.abspath(__file__))
import sys, json, math, statistics as st, time, collections
sys.path.insert(0, HERE)
from kr_bt import *
from kr_fund import funding
src_rows = []
for src in ("upbit", "bithumb"):
    for coin in L[src]:
        k = load_kr(src, coin)
        if not k: continue
        first = min(k)
        if first < START + 3 * 86400: continue
        gc = g(coin)
        if not gc or min(gc) > first - 3600: continue
        src_rows.append((first, src, coin))
src_rows.sort(); seen = {}; EV = []
for t, s, c in src_rows:
    if c in seen and t - seen[c] < 5 * 86400: continue
    seen[c] = t; EV.append((t, s, c))
def one(t, coin, hold, sl, add=0.0, add_h=24):
    gc = g(coin); h0 = t - t % 3600; e = gc.get(h0)
    if not e: return None
    p0 = e[3]; q = 1.0; avg = p0; added = False
    for i in range(1, hold + 1):
        kk = gc.get(h0 + i * 3600)
        if not kk: return None
        if add and not added and i == add_h and kk[3] >= p0:
            avg = (avg * q + kk[3] * add) / (q + add); q += add; added = True
        if kk[1] / avg - 1 >= sl:
            px1 = avg * (1 + sl + 0.01); t1 = h0 + i * 3600; break
    else:
        px1 = gc[h0 + hold * 3600][3]; t1 = h0 + hold * 3600
    fu = funding(coin, h0, t1)
    return (-(px1 / avg - 1) + fu - 0.004) * q, q        # доход в долях базовой суммы
res = {}
for hold in (120, 168, 240, 336):
    for sl in (0.2, 0.28, 0.35, 0.5):
        for add in (0.0, 0.5):
            o = [one(t, c, hold, sl, add) for t, s, c in EV]
            o = [x[0] for x in o if x]
            if len(o) < 20: continue
            h = len(o) // 2; se = st.pstdev(o) / math.sqrt(len(o))
            res[(hold, sl, add)] = (st.mean(o), st.mean(o) / se, st.mean(o[:h]), st.mean(o[h:]), min(o), len(o))
for k, v in sorted(res.items(), key=lambda kv: -kv[1][1])[:14]:
    print(f"держим {k[0]:3} ч, стоп {k[1]*100:.0f}%, добор {'да (+50%)' if k[2] else 'нет'}: {v[0]*100:+.1f}% на базовую сумму, t={v[1]:.1f}, "
          f"половины {v[2]*100:+.1f}/{v[3]*100:+.1f}%, худшая {v[4]*100:+.0f}%, n={v[5]}")
cur = res.get((168, 0.28, 0.5)); print("\nкак сейчас в боте (168 ч, 28%, добор $100 к $200):", cur and f"{cur[0]*100:+.1f}%, t={cur[1]:.1f}")
