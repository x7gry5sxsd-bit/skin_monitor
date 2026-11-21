"""Шорт на Gate после листинга монеты на Upbit/Bithumb (июль–ноябрь): цена + фандинг − комиссия, против рынка."""
import os
HERE = os.path.dirname(os.path.abspath(__file__))

import sys, json, os, math, statistics as st, time, collections
sys.path.insert(0, HERE)
from kr_bt import *
from kr_fund import funding
ev = []
for src in ("upbit", "bithumb"):
    for coin in L[src]:
        k = load_kr(src, coin)
        if not k: continue
        first = min(k)
        if first < START + 3 * 86400:          # торговалась уже в начале периода — не новый листинг
            continue
        gc = g(coin)
        if not gc or min(gc) > first - 3600:   # на Gate фьючерса ещё не было
            continue
        ev.append((first, src, coin))
ev.sort()
seen, rows = {}, []
for t, src, coin in ev:
    if coin in seen and t - seen[coin] < 5 * 86400:      # тот же листинг на второй бирже
        continue
    seen[coin] = t
    for hold, sl in ((72, 0.20), (168, 0.28)):
        gc = g(coin); h0 = t - t % 3600
        e = gc.get(h0)
        if not e: continue
        p0, out = e[3], None
        for i in range(1, hold + 1):
            kk = gc.get(h0 + i * 3600)
            if not kk: break
            if kk[1] / p0 - 1 >= sl:
                out = (h0 + i * 3600, -(sl + 0.01)); break
        if out is None:
            kk = gc.get(h0 + hold * 3600)
            if not kk: continue
            out = (h0 + hold * 3600, -(kk[3] / p0 - 1))
        t1, pr = out
        fu = funding(coin, h0, t1)
        m = mret(h0, t1) or 0.0
        rows.append(dict(t=t, coin=coin, src=src, hold=hold, pr=pr, fu=fu, net=pr + fu - 0.004, exc=pr + fu - 0.004 + m))
for hold in (72, 168):
    r = [x for x in rows if x["hold"] == hold]
    if not r: continue
    net = [x["net"] for x in r]; se = st.pstdev(net) / math.sqrt(len(net)); h = len(r) // 2
    print(f"\nЛистинг, держим {hold} ч: событий {len(r)} | цена {st.mean(x['pr'] for x in r)*100:+.2f}% | фандинг {st.mean(x['fu'] for x in r)*100:+.2f}% "
          f"| ИТОГ {st.mean(net)*100:+.2f}% (t={st.mean(net)/se:.1f}) в плюсе {sum(n>0 for n in net)/len(net):.0%} | сверх рынка {st.mean(x['exc'] for x in r)*100:+.2f}% "
          f"| половины {st.mean(net[:h])*100:+.1f}/{st.mean(net[h:])*100:+.1f}% | худшая {min(net)*100:+.0f}%")
    bym = collections.defaultdict(list)
    for x in r: bym[time.strftime("%Y-%m", time.gmtime(x["t"]))].append(x["net"])
    print("  по месяцам ($ на $200):", ", ".join(f"{m}: {sum(v)*200:+.0f} ({len(v)})" for m, v in sorted(bym.items())))
    print("  ", ", ".join(f"{x['coin']}({x['src'][0]}) {x['net']*100:+.0f}%" for x in r))

print("\n=== Портфель: листинги, 168 ч, стоп 28%, позиций ≤5, комиссия 0,4%, фандинг учтён ===")
r = sorted([x for x in rows if x["hold"] == 168], key=lambda x: x["t"])
for usd in (200, 300, 400):
    for hedge in (False, True):
        eq = peak = dd = 0; openp = []; n = 0; bym = collections.defaultdict(float)
        for x in r:
            openp = [e for e in openp if e > x["t"]]
            if len(openp) >= 5: continue
            v = (x["exc"] if hedge else x["net"]) * usd - (usd * 0.001 if hedge else 0)
            eq += v; peak = max(peak, eq); dd = min(dd, eq - peak); n += 1
            bym[time.strftime("%Y-%m", time.gmtime(x["t"]))] += v
            openp.append(x["t"] + 168 * 3600)
        days = (END - START) / 86400
        print(f"  ${usd} на листинг{' + хедж BTC/рынок' if hedge else ''}: сделок {n}, итог ${eq:+.0f} ≈ ${eq / days * 30:+.0f}/мес, "
              f"худшая просадка ${dd:.0f}, маржа до ${usd / 3 * 5:.0f} | " + " ".join(f"{m[5:]}:{v:+.0f}" for m, v in sorted(bym.items())))
