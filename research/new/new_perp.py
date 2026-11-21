"""НОВОЕ: шорт новых фьючерсов Gate в первые часы/дни после запуска. Цена + фандинг − комиссия, против рынка."""
import os as _os
HERE = _os.path.dirname(_os.path.abspath(__file__))
import os, sys, json, math, statistics as st, time, collections, datetime as dt
sys.path.insert(0, HERE)
import gate
from concurrent.futures import ThreadPoolExecutor
START = int(dt.datetime(2026, 5, 1, tzinfo=dt.timezone.utc).timestamp()); END = int(time.time()) // 3600 * 3600
cs = gate.contracts()
new = [c for c in cs if c["name"].endswith("_USDT") and START <= float(c.get("create_time") or c.get("launch_time") or 0) <= END - 8 * 86400]
print("новых фьючерсов с 01.05:", len(new))
FD = HERE + "/fund"; os.makedirs(FD, exist_ok=True)
def fund(coin):
    f = f"{FD}/{coin}.json"
    if os.path.exists(f): return sorted(json.load(open(f)))
    rows, a = [], START
    while a < END:
        try: d = gate.get("/funding_rate", contract=f"{coin}_USDT", limit=1000, **{"from": a, "to": min(END, a + 40 * 86400)})
        except Exception: d = []
        rows += [(int(x["t"]), float(x["r"])) for x in d]; a += 40 * 86400
    json.dump(rows, open(f, "w")); return sorted(rows)
def load(c):
    t0 = int(float(c.get("create_time") or c.get("launch_time")))
    h0 = t0 - t0 % 3600
    return c["name"][:-5], h0, gate.candles(c["name"], h0, min(END, h0 + 40 * 86400)), fund(c["name"][:-5])
with ThreadPoolExecutor(6) as ex:
    D = list(ex.map(load, new))
tick = {t["contract"][:-5]: float(t.get("volume_24h_quote") or 0) for t in gate.get("/tickers") if t["contract"].endswith("_USDT")}
BASKET = [c for c, v in sorted(tick.items(), key=lambda kv: -kv[1])[:60]]
BC = {c: gate.candles(f"{c}_USDT", START - 86400, END) for c in BASKET}
def mret(a, b):
    rs = [BC[c][b][3] / BC[c][a][3] - 1 for c in BASKET if a in BC[c] and b in BC[c]]
    return st.median(rs) if len(rs) > 20 else 0.0
def test(delay, hold, sl, minv=2e5):
    out = []
    for coin, h0, k, fr in D:
        e = k.get(h0 + delay * 3600)
        if not e: continue
        vol = sum((k.get(h0 + i * 3600) or (0,) * 5)[4] for i in range(max(0, delay - 23), delay + 1))
        if vol < minv: continue
        p0, t0 = e[3], h0 + delay * 3600; t1 = px1 = None
        for i in range(1, hold + 1):
            kk = k.get(t0 + i * 3600)
            if not kk: break
            if sl and kk[1] / p0 - 1 >= sl: t1, px1 = t0 + i * 3600, p0 * (1 + sl + 0.01); break
        if t1 is None:
            kk = k.get(t0 + hold * 3600)
            if not kk: continue
            t1, px1 = t0 + hold * 3600, kk[3]
        fu = sum(r for t, r in fr if t0 < t <= t1)
        net = -(px1 / p0 - 1) + fu - 0.004
        out.append((t0, coin, net, net + mret(t0, t1), fu))
    return sorted(out)
def show(name, o):
    if len(o) < 10: print(name, "мало", len(o)); return
    n = [x[2] for x in o]; e = [x[3] for x in o]; h = len(o) // 2; se = st.pstdev(n) / math.sqrt(len(n))
    bym = collections.defaultdict(float)
    for x in o: bym[time.strftime("%m", time.gmtime(x[0]))] += x[2] * 100
    print(f"{name}: n={len(o)} | ИТОГ {st.mean(n)*100:+.2f}% (t={st.mean(n)/se:.1f}) медиана {st.median(n)*100:+.1f}% в плюсе {sum(x>0 for x in n)/len(n):.0%} "
          f"| сверх рынка {st.mean(e)*100:+.2f}% | фандинг {st.mean(x[4] for x in o)*100:+.2f}% | половины {st.mean(n[:h])*100:+.1f}/{st.mean(n[h:])*100:+.1f}% "
          f"| $ на $100 по мес.: " + " ".join(f"{m}:{v:+.0f}" for m, v in sorted(bym.items())))
for delay in (1, 6, 24, 72):
    for hold, sl in ((24, 0.3), (72, 0.3), (168, 0.4), (336, 0.5)):
        show(f"вход через {delay:2} ч после запуска, держим {hold:3} ч, стоп {sl*100:.0f}%", test(delay, hold, sl))
