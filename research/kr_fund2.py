import os
HERE = os.path.dirname(os.path.abspath(__file__))
import sys, json, statistics as st, time, collections, os
sys.path.insert(0, HERE)
from kr_fund import *
def rate_at(coin, t):
    rows = json.load(open(f"{FD}/{coin}.json"))
    prev = [r for tt, r in rows if tt <= t]
    return prev[-1] if prev else 0.0
def interval_h(coin):
    rows = json.load(open(f"{FD}/{coin}.json"))
    ts = sorted(t for t, _ in rows)
    d = [b - a for a, b in zip(ts, ts[1:]) if b > a]
    return round(st.median(d) / 3600) if d else 8
rows = []
for h, src, coin, ur1, gr1, mult in sigs:
    r = trade(h, coin, hold=72, sl=0.20)
    if not r: continue
    t0, t1, pnl = r
    fu = funding(coin, t0, t1)
    rows.append(dict(t0=t0, coin=coin, src=src, pnl=pnl, fu=fu, r0=rate_at(coin, t0), iv=interval_h(coin)))
fs = sorted(x["fu"] for x in rows)
print("фандинг на сделку: медиана %+.2f%%, 10%% худших до %+.2f%%, доля сделок хуже -2%%: %.0f%%" % (st.median(fs)*100, fs[len(fs)//10]*100, sum(f < -0.02 for f in fs)/len(fs)*100))
print("интервал фандинга (ч):", collections.Counter(x["iv"] for x in rows))
for lim in (None, -0.0005, -0.001, -0.002, -0.005):
    sel = [x for x in rows if lim is None or x["r0"] >= lim]
    if not sel: continue
    net = [x["pnl"] + x["fu"] for x in sel]
    h = len(sel) // 2
    print(f"вход, только если ставка сейчас ≥ {lim if lim is not None else '—'}: сделок {len(sel):3} | без фандинга {st.mean(x['pnl'] for x in sel)*100:+.2f}% "
          f"| фандинг {st.mean(x['fu'] for x in sel)*100:+.2f}% | ИТОГ {st.mean(net)*100:+.2f}% в плюсе {sum(n>0 for n in net)/len(net):.0%} | половины {st.mean(net[:h])*100:+.1f}/{st.mean(net[h:])*100:+.1f}%")
for iv in sorted({x["iv"] for x in rows}):
    sel = [x for x in rows if x["iv"] == iv]
    print(f"  интервал {iv} ч: сделок {len(sel)}, итог с фандингом {st.mean(x['pnl']+x['fu'] for x in sel)*100:+.2f}%, фандинг {st.mean(x['fu'] for x in sel)*100:+.2f}%")
