"""Gate: монеты с 8-часовым фандингом на Gate и сильно отрицательной ставкой. Цена Gate в «чужие» 4-часовые слоты (04/12/20 UTC — расчёт Binance, у Gate расчёта нет)."""
import os as _os
HERE = _os.path.dirname(_os.path.abspath(__file__))
import sys, json, math, time, statistics as st, collections, os
sys.path.insert(0, HERE)
import gate
D = HERE + "/gfs"
NOW = int(time.time()) // 300 * 300; A5 = NOW - 34 * 86400
iv = {c["name"]: int(c.get("funding_interval") or 0) for c in gate.contracts()}
btc = gate.candles("BTC_USDT", A5 - 3 * 3600, NOW, "5m")
def p5(k, x):
    v = k.get(x - 300); return v[3] if v else None
res = collections.defaultdict(list)
for f in os.listdir(D):
    if not f.startswith("fr_"): continue
    c = f[3:-5]; rows = json.load(open(f"{D}/{f}"))
    if iv.get(c) != 28800 or len(rows) < 5: continue
    worst = min(r for t, r in rows if t >= A5) if any(t >= A5 for t, r in rows) else 0
    if worst > -0.003: continue
    k = gate.candles(c, A5 - 3 * 3600, NOW, "5m")
    for T in range(A5 // 14400 * 14400 + 14400, NOW - 7200, 14400):
        last = [r for t, r in rows if t < T]
        if not last: continue
        lr = last[-1]                                         # последняя известная ставка Gate (до момента T)
        g = "ставка ≤ −0.3%" if lr <= -0.003 else "ставка −0.1…−0.3%" if lr <= -0.001 else "ставка > −0.1%"
        slot = "слот Binance без расчёта Gate (04/12/20)" if (T // 3600) % 8 == 4 else "общий расчёт (00/08/16)"
        for name, a, b in (("шорт −5→+5м", -300, 300), ("шорт −5→+15м", -300, 900), ("шорт −5→+30м", -300, 1800), ("шорт +5→+30м", 300, 1800)):
            pa, pb, ba, bb = p5(k, T + a), p5(k, T + b), p5(btc, T + a), p5(btc, T + b)
            if not (pa and pb and ba and bb): continue
            pnl = -((pb / pa - 1) - (bb / ba - 1))
            if slot.startswith("общий") and a < 0 < b: pnl -= abs(lr)   # при общем расчёте шорт платит фандинг Gate
            res[(slot, g, name)].append((T, c, pnl))
for key in sorted(res):
    v = sorted(res[key]); p = [z for _, _, z in v]; n = len(p)
    if n < 20: continue
    se = (st.pstdev(p) or 1e-9) / math.sqrt(n); h = n // 2
    print(f"{key[0]:38} {key[1]:18} {key[2]:14} n={n:5} монет {len({c for _, c, _ in v}):3} | {st.mean(p)*100:+.3f}% (t={st.mean(p)/se:+.1f}) медиана {st.median(p)*100:+.3f}% | −0.2%: {(st.mean(p)-0.002)*100:+.3f}% | половины {st.mean(p[:h])*100:+.2f}/{st.mean(p[h:])*100:+.2f}%")
