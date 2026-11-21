import os
HERE = os.path.dirname(os.path.abspath(__file__))
import sys, json, math, statistics as st, time, collections
sys.path.insert(0, HERE)
import gate
exec(open(HERE + "/korea.py").read().split("def evaluate")[0])   # загрузка свечей, ret(), mkt()
prem = json.load(open(f"{D}/upbit_prem.json"))["сигналы"]
pump = json.load(open(f"{D}/upbit_pump.json"))["сигналы"]
up = json.load(open(f"{D}/upbit.json"))["события"]
print("переплата: монет", len({s['coin'] for s in prem}), collections.Counter(s['coin'] for s in prem).most_common(8))
print("порог/поля:", {k: prem[0][k] for k in prem[0] if k not in ('r',)})
def path(c, t, hrs):
    """часовые (high, low, close) после входа"""
    h0 = int(t) - int(t) % 3600
    k = cd(c)
    return [k.get(h0 + i * 3600) for i in range(1, hrs + 1)]
def sim(rows, hrs=72, sl=None, fee=0.004, hedge="mkt", cool=72):
    last, out = {}, []
    for coin, t in sorted(rows, key=lambda r: r[1]):
        c = f"{coin}_USDT"
        if c not in vol or (coin in last and t - last[coin] < cool * 3600):
            continue
        p0 = px(c, t)
        pp = path(c, t, hrs)
        if not p0 or None in pp:
            continue
        exit_r, held = pp[-1][3] / p0 - 1, hrs
        if sl:
            for i, k in enumerate(pp):
                if k[1] / p0 - 1 >= sl:                       # high достиг стопа (шорт)
                    exit_r, held = sl + 0.005, i + 1; break
        m = mkt(t, held) if hedge == "mkt" else (ret("BTC_USDT", t, held) if hedge == "btc" else 0.0)
        if m is None:
            continue
        last[coin] = t
        out.append((t, coin, -exit_r - fee, -(exit_r - m) - fee - (0.002 if hedge else 0)))
    return out
def show(name, out):
    if len(out) < 5:
        print(name, "мало"); return
    raw = [o[2] for o in out]; hed = [o[3] for o in out]; h = len(out) // 2
    se = st.pstdev(hed) / math.sqrt(len(hed))
    days = (out[-1][0] - out[0][0]) / 86400 or 1
    print(f"{name:42} n={len(out):3} ({len(out)/days:.1f}/сут) | без хеджа {st.mean(raw)*100:+5.2f}% в плюсе {sum(x>0 for x in raw)/len(raw):.0%}"
          f" | С ХЕДЖЕМ {st.mean(hed)*100:+5.2f}% (t={st.mean(hed)/se:.1f}) в плюсе {sum(x>0 for x in hed)/len(hed):.0%}"
          f" | половины {st.mean(hed[:h])*100:+.1f}/{st.mean(hed[h:])*100:+.1f}% | худшая {min(hed)*100:+.0f}%")
P = [(s["coin"], s["t"]) for s in prem]
for hrs in (24, 48, 72):
    for sl in (None, 0.15, 0.25):
        for hg in ("mkt", "btc"):
            show(f"переплата {hrs}ч стоп {sl or '—'} хедж {hg}", sim(P, hrs, sl, hedge=hg))
PU = [(s["coin"], s.get("enter_at") or s["t"]) for s in pump]
LI = [(e["coin"], e["t_start"]) for e in up]
for hrs in (24, 72):
    show(f"памп {hrs}ч стоп 20% хедж mkt", sim(PU, hrs, 0.20))
    show(f"листинг {hrs}ч стоп 28% хедж mkt", sim(LI, hrs, 0.28))
