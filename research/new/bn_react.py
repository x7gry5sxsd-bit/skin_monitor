import os as _os
HERE = _os.path.dirname(_os.path.abspath(__file__))
import json, math, statistics as st, time, collections, os
D = HERE + "/bn"
ev = json.load(open(f"{D}/events.json"))
def rows(e, market):
    out = []
    for k in (0, 1):
        day = time.strftime("%Y-%m-%d", time.gmtime(e["t"] + k * 86400))
        f = f"{D}/{market}_{e['sym']}_{day}.json"
        if os.path.exists(f):
            r = json.load(open(f))
            if r: out += r
    return {x[0]: x for x in out}
res = collections.defaultdict(list)
seen = set()
for e in ev:
    m = "um" if "um" in e["mk"] else ("spot" if "spot" in e["mk"] else None)
    if not m or (e["sym"], int(e["t"]) // 3600) in seen: continue
    seen.add((e["sym"], int(e["t"]) // 3600))
    k = rows(e, m)
    t0 = int(e["t"]) // 60 * 60
    def px(sec, field=4):
        x = k.get((t0 + sec) // 60 * 60)
        return x[field] if x else None
    pre = px(-3600); p_ann = px(-60)
    for lag in (60, 180, 600):
        p_in = px(lag, 1)                                     # вход — открытие минуты через lag
        if not p_in or not p_ann: continue
        r = {"lag": lag, "jump_to_entry": p_in / p_ann - 1, "pre1h": (p_ann / pre - 1) if pre else None}
        for lab, sec in (("5м", 300), ("15м", 900), ("30м", 1800), ("1ч", 3600), ("4ч", 14400), ("24ч", 86400)):
            p = px(lag + sec)
            if p: r[lab] = p / p_in - 1 - 0.004
        mx = [k[t][2] for t in range(t0 + lag, t0 + lag + 3600, 60) if t in k]
        r["max1ч"] = max(mx) / p_in - 1 if mx else None
        res[(e["kind"], lag)].append(r)
for (kind, lag), rs in sorted(res.items()):
    print(f"\n{('ЛИСТИНГ НА СПОТЕ (лонг фьючерса)' if kind == 'spot_list' else 'ЗАПУСК ФЬЮЧЕРСА (лонг спота)')}, вход через {lag} с: событий {len(rs)}")
    j = [r["jump_to_entry"] for r in rs]
    print(f"  скачок от объявления до входа: медиана {st.median(j)*100:+.1f}% | за час ДО объявления: медиана {st.median([r['pre1h'] for r in rs if r['pre1h'] is not None])*100:+.1f}%")
    for lab in ("5м", "15м", "30м", "1ч", "4ч", "24ч"):
        xs = [r[lab] for r in rs if lab in r]
        if len(xs) < 5: continue
        se = st.pstdev(xs) / math.sqrt(len(xs)); h = len(xs) // 2
        print(f"  держим {lab:3}: n={len(xs)} среднее {st.mean(xs)*100:+5.2f}% медиана {st.median(xs)*100:+5.2f}% (t={st.mean(xs)/se:4.1f}) в плюсе {sum(x>0 for x in xs)/len(xs):.0%} | половины {st.mean(xs[:h])*100:+.1f}/{st.mean(xs[h:])*100:+.1f}%")
    mx = [r["max1ч"] for r in rs if r["max1ч"] is not None]
    print(f"  максимум за час после входа: медиана {st.median(mx)*100:+.1f}%")
