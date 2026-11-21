import os as _os
HERE = _os.path.dirname(_os.path.abspath(__file__))
import sys, math, statistics as st, collections
sys.argv = ["x", "0.01", "0.08"]
src = open(HERE + "/pmfin_an.py").read()
exec(src.split("def brier")[0])
# калибровка модели и рынка в последние 0.25–6 ч; подгонка наклона по первой половине времени, проверка на второй
v = sorted([x for x in rows if 0.25 <= x["tau"] < 6], key=lambda x: x["t"])
h = len(v) // 2; A, B = v[:h], v[h:]
def ll(xs, k):
    def f(x):
        z = max(min(x["m"] * k / max(math.sqrt(x.get("frac", 1)), 1e-3), 30), -30)
        return z
    return xs
import math
def logit(p): p = min(max(p, 1e-4), 1 - 1e-4); return math.log(p / (1 - p))
def fitk(xs):
    best = None
    for k in [i / 20 for i in range(4, 41)]:
        loss = 0
        for x in xs:
            q = 1 / (1 + math.exp(-k * logit(x["fair"])))
            loss += (q - x["y"]) ** 2
        if best is None or loss < best[0]: best = (loss / len(xs), k)
    return best
b = fitk(A); k = b[1]
print(f"наклон по 1-й половине: k={k:.2f} (1 = модель как есть; <1 — модель самоуверенна)")
def br(xs, f): return st.mean((f(x) - x["y"]) ** 2 for x in xs)
for name, xs in (("1-я половина", A), ("2-я половина", B)):
    print(f"  {name}: n={len(xs)} Брайер рынок {br(xs, lambda x: x['p']):.4f} | модель {br(xs, lambda x: x['fair']):.4f} | модель с наклоном {br(xs, lambda x: 1/(1+math.exp(-k*logit(x['fair'])))):.4f} "
          f"| среднее рынка и модели {br(xs, lambda x: 0.5*x['p'] + 0.5/(1+math.exp(-k*logit(x['fair'])))):.4f}")
print("калибровка модели (2-я половина):")
for lo, hi in ((0, .1), (.1, .3), (.3, .5), (.5, .7), (.7, .9), (.9, 1.01)):
    w = [x for x in B if lo <= x["fair"] < hi]
    if len(w) > 30: print(f"   модель {lo:.1f}–{hi:.1f}: n={len(w):4} средн. модель {st.mean(x['fair'] for x in w):.2f} | факт {st.mean(x['y'] for x in w):.2f} | рынок {st.mean(x['p'] for x in w):.2f}")
