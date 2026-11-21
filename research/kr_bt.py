"""Проверка «корейского пампа» на истории (как в xupbit) — против рынка, с хеджем и без, по месяцам."""
import os
HERE = os.path.dirname(os.path.abspath(__file__))

import sys, os, json, math, statistics as st, time, collections, datetime as dt
sys.path.insert(0, HERE)
import gate
CD = HERE + "/kr"
L = json.load(open(f"{CD}/lists.json"))
START = int(dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc).timestamp())
END = int(time.time()) // 3600 * 3600
def load_kr(src, c):
    f = f"{CD}/{src}_{c}.json"
    return {int(k): v for k, v in json.load(open(f)).items()} if os.path.exists(f) else {}
GC = {}
def g(c):
    if c not in GC:
        GC[c] = gate.candles(f"{c}_USDT", START - 8 * 86400, END)
    return GC[c]
tick = {t["contract"][:-5]: float(t.get("volume_24h_quote") or 0) for t in gate.get("/tickers") if t["contract"].endswith("_USDT")}
BASKET = [c for c, v in sorted(tick.items(), key=lambda kv: -kv[1])[:60]]
def gret(c, t0, t1):
    a, b = g(c).get(t0), g(c).get(t1)
    return b[3] / a[3] - 1 if a and b else None
MK = {}
def mret(t0, t1):
    k = (t0, t1)
    if k not in MK:
        rs = [r for r in (gret(c, t0, t1) for c in BASKET) if r is not None]
        MK[k] = st.median(rs) if len(rs) > 20 else None
    return MK[k]

def signals(MULT=5, UP=0.03, GAP=0.02, MINV=3e5):
    out = []
    for src in ("upbit", "bithumb"):
        usd = load_kr(src, "USDT")
        for coin in L[src]:
            k = load_kr(src, coin)
            if not k:
                continue
            gc = g(coin)
            hs = sorted(k)
            vols = {h: k[h][4] for h in hs}
            first_k, first_g = hs[0], (min(gc) if gc else None)
            cool = 0
            for h in hs:
                if h < START + 8 * 86400 or h - 3600 not in k:
                    continue
                c1, c0, v1 = k[h][3], k[h - 3600][3], k[h][4]
                ur1 = c1 / c0 - 1 if c0 else 0
                if ur1 < UP:
                    continue
                u = (usd.get(h) or [0, 0, 0, 0])[3]
                if not u or v1 / u < MINV:
                    continue
                prev = [vols[t] for t in range(h - 168 * 3600, h, 3600) if t in vols]
                avg = sum(prev) / 168 if prev else 0
                if not avg or v1 < MULT * avg:
                    continue
                g1, g0 = gc.get(h), gc.get(h - 3600)
                if not g1 or not g0:
                    continue
                gr1 = g1[3] / g0[3] - 1
                if ur1 - gr1 < GAP:
                    continue
                if not 0.8 <= (c1 / u) / g1[3] <= 1.25:
                    continue
                if h - first_k < 14 * 86400 or first_g is None or h - first_g < 14 * 86400:
                    continue
                gv = sum((gc.get(t) or (0, 0, 0, 0, 0))[4] for t in range(h - 23 * 3600, h + 3600, 3600))
                if gv < 1e6:
                    continue
                if h - cool < 24 * 3600:
                    continue
                cool = h
                out.append((h, src, coin, ur1, gr1, v1 / avg))
    return sorted(out)

def trade(h, coin, hold=72, sl=0.20, fee=0.004, delay=1):
    """Вход — закрытие часа h+delay на Gate; выход — через hold ч или стоп по high часа."""
    gc = g(coin)
    e = gc.get(h + delay * 3600)
    if not e:
        return None
    p0, t0 = e[3], h + delay * 3600
    for i in range(1, hold + 1):
        k = gc.get(t0 + i * 3600)
        if not k:
            return None
        if sl and k[1] / p0 - 1 >= sl:
            return t0, t0 + i * 3600, -(sl + 0.01) - fee            # стоп с проскальзыванием 1%
    return t0, t0 + hold * 3600, -(gc[t0 + hold * 3600][3] / p0 - 1) - fee

def run(name, sigs, **kw):
    rows = []
    for h, src, coin, ur1, gr1, mult in sigs:
        r = trade(h, coin, **kw)
        if not r:
            continue
        t0, t1, pnl = r
        m, b = mret(t0, t1), gret("BTC", t0, t1)
        if m is None or b is None:
            continue
        rows.append((t0, src, coin, pnl, pnl + m - 0.001, pnl + b - 0.001))   # хедж: лонг рынка/BTC на ту же сумму
    if not rows:
        print(name, "нет сделок"); return rows
    def s(ix):
        xs = [r[ix] for r in rows]; return st.mean(xs), sum(x > 0 for x in xs) / len(xs), st.mean(xs) / (st.pstdev(xs) / math.sqrt(len(xs)))
    a, b, c = s(3), s(4), s(5)
    print(f"\n{name}: сделок {len(rows)} ({len(rows) / ((END - START) / 86400):.1f}/сут)")
    print(f"  без хеджа:   {a[0]*100:+.2f}% на сделку, в плюсе {a[1]:.0%}, t={a[2]:.1f}")
    print(f"  хедж рынком: {b[0]*100:+.2f}%, в плюсе {b[1]:.0%}, t={b[2]:.1f}   | хедж BTC: {c[0]*100:+.2f}%, в плюсе {c[1]:.0%}, t={c[2]:.1f}")
    bym = collections.defaultdict(list)
    for r in rows:
        bym[time.strftime("%Y-%m", time.gmtime(r[0]))].append(r)
    print("  по месяцам (без хеджа / с хеджем BTC, $ на $200):", ", ".join(
        f"{m}: {sum(x[3] for x in v)*200:+.0f}/{sum(x[5] for x in v)*200:+.0f} ({len(v)})" for m, v in sorted(bym.items())))
    return rows

if __name__ == "__main__":
    sigs = signals()
    print("сигналов:", len(sigs), collections.Counter(s[1] for s in sigs))
    for hold, sl in ((72, 0.20), (72, None), (48, 0.20), (24, 0.20), (96, 0.20), (72, 0.30)):
        run(f"памп: держим {hold} ч, стоп {sl}", sigs, hold=hold, sl=sl)
