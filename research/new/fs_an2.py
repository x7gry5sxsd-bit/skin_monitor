"""Дрейф после расчёта фандинга: группы по знаку/величине/интервалу, плацебо (то же событие, окно без расчёта), месяцы, концентрация по монетам."""
import os as _os
HERE = _os.path.dirname(_os.path.abspath(__file__))
import os, json, glob, math, time, statistics as st, collections, sys
exec(open(HERE + "/fs_an.py").read().split("COST =")[0])
COST = 0.002
ev = []
for c, rows in fr.items():
    for i, (t, x) in enumerate(rows):
        if t < T0 or i == 0: continue
        gap = t - rows[i - 1][0]; nxt = rows[i + 1][0] - t if i + 1 < len(rows) else None
        if abs(x) >= 0.003: ev.append((c, t, x, gap, nxt))
print("событий |f|≥0.3%:", len(ev))
def grp(x, gap):
    return ("f<0" if x < 0 else "f>0") + (" ≥1%" if abs(x) >= 0.01 else " 0.3–1%") + (" инт.≥4ч" if gap >= 14000 else " инт.1–2ч")
WIN = [("после +1→+15м", 60, 900), ("после +1→+30м", 60, 1800), ("после +1→+60м", 60, 3600), ("после +5→+60м", 300, 3600),
       ("плацебо −120→−61м", -7200, -3660), ("плацебо +61→+120м", 3660, 7200)]
res = collections.defaultdict(list)
for c, t, x, gap, nxt in ev:
    sg = 1 if x > 0 else -1
    for name, a, b in WIN:
        if name.startswith("плацебо") and gap < 14000: continue        # при часовом интервале в окне был бы другой расчёт
        if name.startswith("плацебо +") and (nxt or 0) < 14000: continue
        r = ret(c, t + a, t + b); m = ret("BTC", t + a, t + b)
        if r is None: continue
        res[(grp(x, gap), name)].append((t, c, sg * (r - (m or 0))))
for g in sorted({k[0] for k in res}):
    print("\n==", g)
    for name, a, b in WIN:
        v = sorted(res.get((g, name), []))
        if len(v) < 30: continue
        p = [z for _, _, z in v]; n = len(p); mu = st.mean(p); se = st.pstdev(p) / math.sqrt(n); h = n // 2
        coins = collections.Counter(c for _, c, _ in v)
        print(f"  {name:18} n={n:5} монет {len(coins):3} | {mu*100:+.3f}% (t={mu/se:+.1f}) медиана {st.median(p)*100:+.3f}% | после {COST*100:.1f}%: {(mu-COST)*100:+.3f}% "
              f"в плюсе {sum(z>COST for z in p)/n:.0%} | половины {st.mean(p[:h])*100:+.2f}/{st.mean(p[h:])*100:+.2f}%")
best = [("f<0", "после +1→+60м")]
for sgn, name in best:
    v = sorted(z for k, vv in res.items() if k[0].startswith(sgn) and " ≥1%" in k[0] and k[1] == name for z in vv)
    bym = collections.defaultdict(list)
    for t, c, z in v: bym[time.strftime("%y-%m", time.gmtime(t))].append(z - COST)
    print(f"\nпо месяцам {sgn} ≥1% {name}: " + " ".join(f"{m}:{st.mean(z)*100:+.2f}%(n{len(z)})" for m, z in sorted(bym.items())))
    cc = collections.defaultdict(list)
    for t, c, z in v: cc[c].append(z)
    top = sorted(cc.items(), key=lambda kv: -len(kv[1]))[:12]
    print("частые монеты:", " ".join(f"{c}:{len(z)}/{st.mean(z)*100:+.1f}%" for c, z in top))
    wo = [z for t, c, z in v if c not in dict(top[:3])]
    if wo: print(f"без 3 самых частых монет: n={len(wo)} {st.mean(wo)*100:+.3f}% (t={st.mean(wo)/(st.pstdev(wo)/math.sqrt(len(wo))):+.1f})")
