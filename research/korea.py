"""Корейские события бота против рынка Gate: шорт-доход монеты минус шорт-доход «средней монеты» за то же окно."""
import os
HERE = os.path.dirname(os.path.abspath(__file__))

import sys, json, math, statistics as st, time
sys.path.insert(0, HERE)
import gate
D = HERE + "/data"  # сюда — JSON страниц бота (/upbit, /upbit/prem, /upbit/pump, /kwallet)
T0, T1 = 1793300000 - 1793300000 % 3600, int(time.time()) - int(time.time()) % 3600
tick = gate.get("/tickers")
vol = {t["contract"]: float(t.get("volume_24h_quote") or 0) for t in tick}
basket = [c for c, v in sorted(vol.items(), key=lambda kv: -kv[1]) if c.endswith("_USDT")][:80]
C = {}
def cd(c):
    if c not in C:
        C[c] = gate.candles(c, T0, T1)
    return C[c]
def px(c, t):
    """Цена закрытия часа, в который попало t (вход — по закрытию этого часа)."""
    h = int(t) - int(t) % 3600
    k = cd(c).get(h)
    return k[3] if k else None
def ret(c, t, hrs):
    a, b = px(c, t), px(c, t + hrs * 3600)
    return (b / a - 1) if a and b else None
for c in basket: cd(c)
def mkt(t, hrs):
    rs = [ret(c, t, hrs) for c in basket]
    rs = [r for r in rs if r is not None]
    return st.median(rs) if len(rs) > 20 else None

def evaluate(name, rows, hrs_list=(24, 72), fee=0.004):
    print(f"\n=== {name}: событий {len(rows)}")
    for hrs in hrs_list:
        out = []
        for coin, t in rows:
            c = f"{coin}_USDT"
            if c not in vol:
                continue
            r, m = ret(c, t, hrs), mkt(t, hrs)
            if r is None or m is None:
                continue
            out.append((t, -r - fee, -(r - m) - fee, -m))
        if len(out) < 5:
            print(f"  {hrs} ч: мало данных ({len(out)})"); continue
        out.sort()
        raw = [o[1] for o in out]; exc = [o[2] for o in out]; mk = [o[3] for o in out]
        se = st.pstdev(exc) / math.sqrt(len(exc)) if len(exc) > 1 else 1
        h = len(out) // 2
        print(f"  {hrs:2} ч: n={len(out):3} | шорт {st.mean(raw)*100:+6.2f}% (в плюсе {sum(x>0 for x in raw)/len(raw):.0%}) | "
              f"рынок дал бы шорту {st.mean(mk)*100:+5.2f}% | СВЕРХ РЫНКА {st.mean(exc)*100:+6.2f}% (t={st.mean(exc)/se:4.1f}, "
              f"медиана {st.median(exc)*100:+.2f}%) | половины {st.mean(exc[:h])*100:+.1f}% / {st.mean(exc[h:])*100:+.1f}%")

prem = json.load(open(f"{D}/upbit_prem.json"))["сигналы"]
evaluate("Корейская переплата ≥2% (бумага)", [(s["coin"], s["t"]) for s in prem], (4, 24, 72))
pump = json.load(open(f"{D}/upbit_pump.json"))
flags = pump["метки_бирж_события"]
by = {}
for f in flags:
    by.setdefault(f["flag"], []).append((f["coin"], f["t"]))
for k, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
    evaluate(f"Метка Upbit «{k}»", v)
evaluate("Корейский памп (бумага)", [(s["coin"], s.get("enter_at") or s["t"]) for s in pump["сигналы"]])
kw = json.load(open(f"{D}/kwallet.json"))["события"]
byk = {}
for e in kw:
    byk.setdefault(e["what"], []).append((e["coin"], e["t"]))
for k, v in byk.items():
    evaluate(f"Bithumb: {k}", v)
up = json.load(open(f"{D}/upbit.json"))["события"]
evaluate("Листинг Upbit/Bithumb", [(e["coin"], e["t_start"]) for e in up])
