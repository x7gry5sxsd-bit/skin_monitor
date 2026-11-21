"""«Выкуп обвала»: после часа с падением ≥X% против рынка — лонг на закрытии этого часа. Сравнение со случайным контролем."""
import os as _os
HERE = _os.path.dirname(_os.path.abspath(__file__))
import sys, json, math, time, random, statistics as st, collections
sys.path.insert(0, HERE)
import gate
U = json.load(open(HERE + "/flush_universe.json")); T0, NOW = U["T0"], U["NOW"]
K = {c: gate.candles(c, T0, NOW) for c in U["coins"]}
hours = sorted({t for k in K.values() for t in k})
MK = {}
for t in hours:
    rs = [k[t][3] / k[t - 3600][3] - 1 for k in K.values() if t in k and t - 3600 in k]
    if len(rs) >= 30: MK[t] = st.median(rs)
def mret(a, b):                                   # рыночная доходность от закрытия часа a до закрытия часа b
    s = 0.0
    for t in range(a + 3600, b + 3600, 3600): s += MK.get(t, 0.0)
    return s
COST = 0.002
def trade(k, t, hold, sl, side=1):
    p0 = k[t][3]
    for i in range(1, hold + 1):
        x = k.get(t + i * 3600)
        if not x: return None
        adverse = (x[2] / p0 - 1) if side > 0 else (x[1] / p0 - 1)   # лонг — смотрим минимум, шорт — максимум
        if sl and side * adverse <= -sl:
            return -sl - 0.005 - COST, t + i * 3600
    return side * (k[t + hold * 3600][3] / p0 - 1) - COST, t + hold * 3600
random.seed(3)
for X in (0.08, 0.12, 0.20):
    ev, ctl = [], []
    for c, k in K.items():
        last = -10**12
        for t in sorted(k):
            if t - 3600 not in k or t not in MK: continue
            r = k[t][3] / k[t - 3600][3] - 1 - MK[t]
            vol24 = [k[t - j * 3600][4] for j in range(1, 25) if t - j * 3600 in k]
            if r <= -X and t - last >= 24 * 3600 and vol24 and k[t][4] >= 2 * st.median(vol24):
                wick = (k[t][3] - k[t][2]) / max(k[t - 3600][3] - k[t][2], 1e-12)     # доля отскока от минимума часа
                ev.append((t, c, r, wick)); last = t
            elif random.random() < 0.0015: ctl.append((t, c, r, 0))
    print(f"\n### падение за час ≥ {X:.0%} против рынка + объём ≥2× медианы: событий {len(ev)}, монет {len({c for _, c, _, _ in ev})}")
    for hold, sl in ((1, None), (4, None), (12, None), (24, None), (24, 0.10), (72, 0.15)):
        for name, E in (("событие", ev), ("контроль", ctl)):
            rs = []
            for t, c, r, w in E:
                z = trade(K[c], t, hold, sl)
                if z: rs.append((t, z[0] + mret(t, z[1]) * -1, z[0]))     # сверх рынка
            if len(rs) < 20: continue
            rs.sort(); p = [x[2] for x in rs]; pe = [x[1] for x in rs]; n = len(p); h = n // 2
            se = st.pstdev(p) / math.sqrt(n)
            print(f"  {name:8} лонг {hold:2}ч стоп {sl or '—':4}: n={n:5} {st.mean(p)*100:+.2f}% (t={st.mean(p)/se:+.1f}) медиана {st.median(p)*100:+.2f}% в плюсе {sum(x>0 for x in p)/n:.0%} "
                  f"| сверх рынка {st.mean(pe)*100:+.2f}% | половины {st.mean(p[:h])*100:+.2f}/{st.mean(p[h:])*100:+.2f}% | худшая {min(p)*100:+.0f}%")
    if X == 0.12:
        for lo, hi, nm in ((0, 0.25, "закрылся у минимума"), (0.25, 0.6, "отскок 25–60%"), (0.6, 9, "отскок >60%")):
            rs = [trade(K[c], t, 24, None) for t, c, r, w in ev if lo <= w < hi]
            rs = [z[0] for z in rs if z]
            if len(rs) >= 15: print(f"    {nm:22} лонг 24ч: n={len(rs)} {st.mean(rs)*100:+.2f}% медиана {st.median(rs)*100:+.2f}%")
