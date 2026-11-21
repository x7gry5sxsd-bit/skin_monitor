import os
HERE = os.path.dirname(os.path.abspath(__file__))
import sys, json, math, statistics as st, time, collections
sys.path.insert(0, HERE)
from kr_bt import *
import gate, os
FD = HERE + "/fund"; os.makedirs(FD, exist_ok=True)
def funding(coin, t0, t1):
    """Сумма ставок фандинга, которую ПОЛУЧАЕТ шорт за [t0, t1] (ставка > 0 — лонги платят шортам)."""
    f = f"{FD}/{coin}.json"
    if os.path.exists(f):
        rows = json.load(open(f))
    else:
        rows = []
        a = START
        while a < END:
            try:
                d = gate.get("/funding_rate", contract=f"{coin}_USDT", limit=1000, **{"from": a, "to": min(END, a + 40 * 86400)})
            except Exception:
                d = []
            rows += [(int(x["t"]), float(x["r"])) for x in d]
            a += 40 * 86400
        json.dump(rows, open(f, "w"))
    return sum(r for t, r in rows if t0 < t <= t1)
sigs = signals()
def portfolio(hold=72, sl=0.20, maxpos=5, usd=200, hedge=False):
    open_, out = [], []
    for h, src, coin, ur1, gr1, mult in sigs:
        r = trade(h, coin, hold=hold, sl=sl)
        if not r:
            continue
        t0, t1, pnl = r
        open_ = [x for x in open_ if x[1] > t0]
        if len(open_) >= maxpos or any(x[0] == coin for x in open_):
            continue
        fu = funding(coin, t0, t1)
        b = gret("BTC", t0, t1) if hedge else 0.0
        out.append((t0, coin, pnl, fu, (pnl + fu + (b - 0.001 if hedge else 0)) * usd))
        open_.append((coin, t1))
    return out
for hedge in (False, True):
    o = portfolio(hedge=hedge)
    tot = sum(x[4] for x in o); fu = st.mean(x[3] for x in o)
    bym = collections.defaultdict(float)
    for x in o: bym[time.strftime("%Y-%m", time.gmtime(x[0]))] += x[4]
    days = (END - START) / 86400
    eq, peak, dd = 0, 0, 0
    for x in sorted(o, key=lambda x: x[0]):
        eq += x[4]; peak = max(peak, eq); dd = min(dd, eq - peak)
    print(f"\n{'С ХЕДЖЕМ BTC' if hedge else 'БЕЗ ХЕДЖА'}: сделок {len(o)} при лимите 5 позиций, $200 на сделку")
    print(f"  фандинг в среднем {fu*100:+.2f}% на сделку (плюс — шорт получает)")
    print(f"  итог +${tot:.0f} за {days:.0f} дн. ≈ ${tot / days * 30:.0f} в месяц | худшая просадка ${dd:.0f} | в плюсе {sum(x[4]>0 for x in o)/len(o):.0%}")
    print("  по месяцам:", ", ".join(f"{m}: {v:+.0f}$" for m, v in sorted(bym.items())))
