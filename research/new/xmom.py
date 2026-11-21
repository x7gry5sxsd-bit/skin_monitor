"""Кросс-секционный моментум на бессрочниках Gate: точечный по времени набор монет (ликвидность на момент ребаланса), издержки по обороту, фандинг (где есть история)."""
import os as _os
HERE = _os.path.dirname(_os.path.abspath(__file__))
import sys, json, math, statistics as st, collections, time, os, bisect
sys.path.insert(0, HERE)
import gate
U = json.load(open(HERE + "/flush_universe.json")); T0, NOW = U["T0"], U["NOW"]
K = {c: gate.candles(c, T0, NOW) for c in U["coins"] if c != "BTC_USDT"}
FR = {}
for c in K:
    f = fHERE + "/gfr/{c}.json"
    FR[c] = json.load(open(f)) if os.path.exists(f) else []
FR_T0 = 1785945600
def fsum(c, a, b):
    r = FR.get(c) or []
    ts = [t for t, _ in r]; i = bisect.bisect_right(ts, a); j = bisect.bisect_right(ts, b)
    return sum(x for _, x in r[i:j])
def px(c, t):
    k = K[c]
    for j in range(3):
        if t - j * 3600 in k: return k[t - j * 3600][3]
    return None
def liq(c, t):                                   # медианный часовой оборот за 7 дней до t
    k = K[c]; v = [k[t - i * 3600][4] for i in range(1, 169) if t - i * 3600 in k]
    return st.median(v) if len(v) > 140 else 0
COST = 0.002
def run(look_h, hold_h=24, frac=0.2, vmin=20000, fund=False, start=None, name="", ret=False):
    t = (start or (T0 + 30 * 86400)) // 86400 * 86400; out = []; prevL, prevS = set(), set()
    while t + hold_h * 3600 <= NOW:
        sc = []
        for c in K:
            a, b, e = px(c, t - look_h * 3600), px(c, t), px(c, t + hold_h * 3600)
            if not (a and b and e) or liq(c, t) < vmin: continue
            r = e / b - 1
            fl = fsum(c, t, t + hold_h * 3600) if fund else 0.0
            sc.append((b / a - 1, c, r, fl))
        if len(sc) < 40: t += hold_h * 3600; continue
        sc.sort(); n = max(int(len(sc) * frac), 3)
        lo, hi = sc[:n], sc[-n:]
        L = {x[1] for x in hi}; S_ = {x[1] for x in lo}
        turn = (len(L - prevL) / n + len(S_ - prevS) / n) / 2
        g = st.mean(x[2] for x in hi) - st.mean(x[2] for x in lo)
        f = -st.mean(x[3] for x in hi) + st.mean(x[3] for x in lo)      # лонг платит фандинг, шорт получает
        out.append((t, g + f - 2 * COST * turn, f, turn)); prevL, prevS = L, S_
        t += hold_h * 3600
    v = [x[1] for x in out]; n = len(v)
    if n < 15: return
    h = n // 2; se = st.pstdev(v) / math.sqrt(n)
    eq, peak, dd = 1.0, 1.0, 0.0
    for r in v: eq *= 1 + r / 2; peak = max(peak, eq); dd = min(dd, eq / peak - 1)   # капитал = 1 на обе ноги (по 0.5)
    q = collections.defaultdict(list)
    for t, r, f, tu in out: q[time.strftime("%y-%m", time.gmtime(t))].append(r)
    print(f"{name:40} дней {n:3} | в день {st.mean(v)*100:+.3f}% на ногу (t={st.mean(v)/se:+.1f}) | фандинг {st.mean(x[2] for x in out)*100:+.3f}% | оборот {st.mean(x[3] for x in out):.0%} "
          f"| на капитал: итого {(eq-1)*100:+.0f}%, просадка {dd*100:.0f}% | половины {st.mean(v[:h])*100:+.3f}/{st.mean(v[h:])*100:+.3f}%")
    if ret: print("      по месяцам (% в день): " + " ".join(f"{m}:{st.mean(x)*100:+.2f}" for m, x in sorted(q.items())))
    return out
print("== весь период (фандинг не учтён — истории у Gate нет раньше 05.08.2026)")
for look in (168, 336, 672):
    for vmin in (10000, 40000):
        run(look, vmin=vmin, name=f"моментум {look//24}д, оборот ≥${vmin//1000}k/ч", ret=(look == 672 and vmin == 10000))
print("\n== с 05.08.2026, С фандингом")
for look in (168, 336, 672):
    run(look, fund=True, start=FR_T0 + 86400, name=f"моментум {look//24}д + фандинг")
    run(look, fund=False, start=FR_T0 + 86400, name=f"моментум {look//24}д без фандинга (тот же период)")
