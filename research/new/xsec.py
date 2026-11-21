"""Рыночно-нейтральные портфели на бессрочниках Gate (239 ликвидных монет, ~13 мес., часовые свечи): лонг одних монет / шорт других, ребаланс раз в неделю или сутки."""
import os as _os
HERE = _os.path.dirname(_os.path.abspath(__file__))
import sys, json, math, statistics as st, collections, time
sys.path.insert(0, HERE)
import gate
U = json.load(open(HERE + "/flush_universe.json")); T0, NOW = U["T0"], U["NOW"]
K = {c: gate.candles(c, T0, NOW) for c in U["coins"]}
K = {c: k for c, k in K.items() if c not in ("BTC_USDT",) and len(k) > 2000}
def px(c, t):
    k = K[c]
    for j in range(3):
        if t - j * 3600 in k: return k[t - j * 3600][3]
    return None
COST = 0.002                                   # круг на одну ногу при полной замене позиции (тейкер 0.05%×2 + проскальзывание)
def run(signal, hold_h, look_h, frac=0.2, name=""):
    t = T0 + max(look_h, 24 * 7) * 3600 + 3600; out = []
    prevL, prevS = set(), set()
    while t + hold_h * 3600 <= NOW:
        sc = []
        for c in K:
            s = signal(c, t, look_h)
            p0, p1 = px(c, t), px(c, t + hold_h * 3600)
            if s is not None and p0 and p1 and (t - 24 * 7 * 3600) in K[c]: sc.append((s, c, p1 / p0 - 1))
        if len(sc) < 60: t += hold_h * 3600; continue
        sc.sort(); n = int(len(sc) * frac)
        low, high = sc[:n], sc[-n:]
        L = {c for _, c, _ in high}; S_ = {c for _, c, _ in low}          # лонг — высокий сигнал, шорт — низкий
        turn = (len(L - prevL) + len(S_ - prevS)) / (2 * n)
        r = st.mean(x[2] for x in high) - st.mean(x[2] for x in low) - COST * turn * 2 / 2
        out.append((t, r)); prevL, prevS = L, S_
        t += hold_h * 3600
    if len(out) < 10: return
    v = [r for _, r in out]; n = len(v); h = n // 2
    per_year = 8760 / hold_h
    sharpe = st.mean(v) / (st.pstdev(v) or 1e-9) * math.sqrt(per_year)
    eq, peak, dd = 1.0, 1.0, 0.0
    for r in v: eq *= 1 + r; peak = max(peak, eq); dd = min(dd, eq / peak - 1)
    print(f"{name:44} периодов {n:3} | за период {st.mean(v)*100:+.2f}% (t={st.mean(v)/(st.pstdev(v)/math.sqrt(n)):+.1f}) | Шарп/год {sharpe:+.2f} | итого {(eq-1)*100:+.0f}% | макс. просадка {dd*100:.0f}% | половины {st.mean(v[:h])*100:+.2f}/{st.mean(v[h:])*100:+.2f}%")
def mom(c, t, L):
    a, b = px(c, t - L * 3600), px(c, t)
    return b / a - 1 if a and b else None
def rev(c, t, L):
    m = mom(c, t, L); return -m if m is not None else None
def lowvol(c, t, L):
    rs = []
    for i in range(1, L):
        a, b = px(c, t - i * 3600), px(c, t - (i - 1) * 3600)
        if a and b: rs.append(b / a - 1)
    return -st.pstdev(rs) if len(rs) > L * 0.8 else None
def lowvol_long_only_check(c, t, L): return lowvol(c, t, L)
print("лонг — монеты с ВЫСОКИМ сигналом, шорт — с НИЗКИМ; 20% / 20%; доходность — разница корзин минус издержки (фандинг не учтён)")
for hold in (24, 168):
    run(mom, hold, 168, name=f"моментум 7д, ребаланс {hold}ч")
    run(mom, hold, 672, name=f"моментум 28д, ребаланс {hold}ч")
    run(rev, hold, 24, name=f"разворот 1д, ребаланс {hold}ч")
    run(lowvol, hold, 168, name=f"низкая волатильность 7д, ребаланс {hold}ч")
