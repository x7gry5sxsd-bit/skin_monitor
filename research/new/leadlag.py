"""Новая идея: Upbit/Bithumb резко выросли за час, Gate отстал → догонит ли Gate в следующие часы (лонг)?"""
import os as _os
HERE = _os.path.dirname(_os.path.abspath(__file__))
import sys, os, json, math, statistics as st, time, collections
sys.path.insert(0, HERE); sys.path.insert(0, _os.path.join(HERE, '..'))   # kr_bt.py, kr_fund.py — из research/ (ветка с корейскими листингами)
from kr_bt import *
from kr_fund import funding
def scan(UP=0.03, GAP=0.02, MULT=3, MINV=1e5):
    out = []
    for src in ("upbit", "bithumb"):
        usd = load_kr(src, "USDT")
        for coin in L[src]:
            k = load_kr(src, coin)
            if not k: continue
            gc = g(coin); hs = sorted(k); vols = {h: k[h][4] for h in hs}; cool = 0
            for h in hs:
                if h < START + 8 * 86400 or h - 3600 not in k: continue
                c1, c0, v1 = k[h][3], k[h - 3600][3], k[h][4]
                ur1 = c1 / c0 - 1 if c0 else 0
                if ur1 < UP: continue
                u = (usd.get(h) or [0] * 4)[3]
                if not u or v1 / u < MINV: continue
                prev = [vols[t] for t in range(h - 168 * 3600, h, 3600) if t in vols]
                avg = sum(prev) / 168 if prev else 0
                if not avg or v1 < MULT * avg: continue
                g1, g0 = gc.get(h), gc.get(h - 3600)
                if not g1 or not g0: continue
                gr1 = g1[3] / g0[3] - 1
                if ur1 - gr1 < GAP or h - cool < 6 * 3600: continue
                cool = h
                out.append((h, src, coin, ur1, gr1))
    return sorted(out)
sig = scan()
print("сигналов (Upbit/Bithumb +3% за час, Gate отстал на 2%+, оборот ×3):", len(sig))
for hrs in (1, 2, 4, 8, 24):
    rs, ex = [], []
    for h, src, coin, ur1, gr1 in sig:
        a, b = g(coin).get(h), g(coin).get(h + hrs * 3600)        # вход — закрытие часа-сигнала
        if not a or not b: continue
        r = b[3] / a[3] - 1 - 0.002
        m = mret(h, h + hrs * 3600)
        rs.append(r); ex.append(r - (m or 0))
    se = st.pstdev(rs) / math.sqrt(len(rs)); hh = len(rs) // 2
    print(f"  лонг Gate на {hrs:2} ч: n={len(rs)} в среднем {st.mean(rs)*100:+.2f}% (t={st.mean(rs)/se:.1f}) в плюсе {sum(x>0 for x in rs)/len(rs):.0%} "
          f"сверх рынка {st.mean(ex)*100:+.2f}% | половины {st.mean(rs[:hh])*100:+.2f}/{st.mean(rs[hh:])*100:+.2f}%")
# насколько Gate догоняет внутри следующего часа по high
up = []
for h, src, coin, ur1, gr1 in sig:
    a, b = g(coin).get(h), g(coin).get(h + 3600)
    if a and b: up.append(b[1] / a[3] - 1)
print("  максимум Gate в следующий час: медиана %+.2f%%, 75%%-квантиль %+.2f%%" % (st.median(up)*100, sorted(up)[len(up)*3//4]*100))
