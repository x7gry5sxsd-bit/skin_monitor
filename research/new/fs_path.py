"""Профиль цены вокруг расчёта (накопленное отклонение от BTC) по 10-минутным шагам: где именно идёт движение."""
import os as _os
HERE = _os.path.dirname(_os.path.abspath(__file__))
import os, json, glob, math, time, statistics as st, collections, sys
exec(open(HERE + "/fs_an.py").read().split("COST =")[0])
SEL = sys.argv[1] if len(sys.argv) > 1 else "x"           # x — по фактической ставке, prev — по ставке прошлого расчёта (известна заранее)
G = collections.defaultdict(list)
for c, rows in fr.items():
    for i, (t, x) in enumerate(rows):
        if t < T0 or i == 0: continue
        gap = t - rows[i - 1][0]; prev = rows[i - 1][1]
        ref = x if SEL == "x" else prev
        if ref > -0.003 or gap < 14000: continue
        g = ("≥1%" if ref <= -0.01 else "0.3–1%") + (" инт.4ч" if gap < 20000 else " инт.8ч")
        G[g].append((c, t))
steps = list(range(-240, 181, 10))
for g, E in sorted(G.items()):
    acc = {s: [] for s in steps}
    for c, t in E:
        base = px(c, t - 14400); bb = px("BTC", t - 14400)
        if not base or not bb: continue
        for s in steps:
            p = px(c, t + s * 60); b = px("BTC", t + s * 60)
            if p and b: acc[s].append((p / base - 1) - (b / bb - 1))
    print(f"\n== f<0 {g} (отбор по {'факт. ставке' if SEL=='x' else 'прошлой ставке'}) n={len(acc[0])}: накопл. откл. от BTC с T−240м, %")
    out = []
    for s in steps:
        v = acc[s]
        if len(v) > 30: out.append(f"{s:+d}:{st.mean(v)*100:+.2f}")
    for k in range(0, len(out), 11): print("   " + "  ".join(out[k:k + 11]))
