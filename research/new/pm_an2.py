import os as _os
HERE = _os.path.dirname(_os.path.abspath(__file__))
import json, math, statistics as st, collections, sys
P = open(HERE + "/pm_an.py").read()
exec(P.split("rows = []")[0])
exec("B = " + P.split("B = ")[1].split("\n")[0])
exec("def line" + P.split("def line")[1].split("for lab in")[0])
VMIN = float(sys.argv[2]) if len(sys.argv) > 2 else 5000
def cat2(r):
    s = (r.get("series") or "").lower()
    if s.startswith("setka") or s in ("wtt",) or "tabletennis" in s: return "наст. теннис"
    if s in ("pga-tour", "lpga-tour", "dp-world-tour", "liv", "korn-ferry"): return "гольф"
    return cat(r)
rows = []
for r in R:
    if (r.get("vol") or 0) < VMIN: continue
    for lab in ("p2h", "p30", "p5"):
        p = r.get(lab)
        if p is None or not (0.02 <= p <= 0.98): continue
        for side, pp, y in ((0, p, r["y"]), (1, 1 - p, 1 - r["y"])):
            cost = pp + HALF + fee(pp)
            rows.append({"lab": lab, "cat": cat2(r), "p": pp, "y": y, "roi": (y - cost) / (pp + HALF), "gs": r["gs"], "draw": "draw" in (r.get("q") or "").lower(),
                         "mv": (r["p30"] - r["p2h"]) * (1 if side == 0 else -1) if r.get("p30") is not None and r.get("p2h") is not None else None})
print(f"объём ≥ ${VMIN:.0f}: рынков", sum(1 for r in R if (r.get("vol") or 0) >= VMIN and r.get("p30") is not None))
print(collections.Counter(cat2(r) for r in R if (r.get("vol") or 0) >= VMIN and r.get("p30") is not None).most_common())
for lab in ("p30",):
    print(f"\n### {lab} все")
    for a, b in B:
        line([x for x in rows if x["lab"] == lab and a <= x["p"] < b and not x["draw"]], f"{a:.2f}–{b:.2f}")
    line([x for x in rows if x["lab"] == lab and x["draw"]], "ничья (да/нет)")
print("\n### p30 по видам: аутсайдер <0.5 / фаворит ≥0.5 / сильный фаворит ≥0.8")
for c in sorted({x["cat"] for x in rows}):
    v = [x for x in rows if x["lab"] == "p30" and x["cat"] == c and not x["draw"]]
    for a, b in ((0.02, 0.5), (0.5, 0.8), (0.8, 0.98)):
        line([x for x in v if a <= x["p"] < b], f"{c} {a:.2f}–{b:.2f}")
print("\n### движение -2ч→-30м")
for a, b, t in ((0.03, 1, "выросла ≥3 п."), (0.01, 0.03, "выросла 1–3 п."), (-0.03, -0.01, "упала 1–3 п."), (-1, -0.03, "упала ≥3 п.")):
    line([x for x in rows if x["lab"] == "p30" and x["mv"] is not None and a <= x["mv"] < b and not x["draw"] and 0.1 <= x["p"] <= 0.9], t)
