"""Калибровка цен Polymarket «кто победит» (спорт, киберспорт) перед стартом: есть ли систематическая ошибка после комиссии."""
import os as _os
HERE = _os.path.dirname(_os.path.abspath(__file__))
import json, math, statistics as st, collections, time, sys
R = json.load(open(HERE + "/pm/moneyline.json"))
HALF = float(sys.argv[1]) if len(sys.argv) > 1 else 0.005          # половина спреда (покупаем по аску)
def cat(r):
    s = (r.get("series") or "").lower(); q = (r.get("q") or "").lower()
    for k, v in (("nba", "NBA"), ("wnba", "WNBA"), ("nfl", "NFL"), ("nhl", "NHL"), ("mlb", "MLB"), ("cfb", "NCAA футбол"), ("cbb", "NCAA баскет"),
                 ("ncaab", "NCAA баскет"), ("atp", "теннис"), ("wta", "теннис"), ("itf", "теннис"), ("tennis", "теннис"), ("ufc", "ММА"), ("mma", "ММА"),
                 ("counter-strike", "CS2"), ("cs2", "CS2"), ("league-of-legends", "LoL"), ("lol", "LoL"), ("dota", "Dota2"), ("valorant", "Valorant"),
                 ("cricket", "крикет"), ("ipl", "крикет"), ("boxing", "бокс"), ("kbo", "бейсбол прочий"), ("npb", "бейсбол прочий")):
        if k in s: return v
    if "draw" in q or "win on 20" in q or any(k in s for k in ("premier", "liga", "serie", "bundesliga", "ligue", "eredivisie", "ere-", "mls", "uefa", "ucl", "uel", "soccer", "fifa", "copa", "championship", "efl", "allsvenskan", "eliteserien", "tur-", "bel1", "j1", "k-league", "brazil", "argentina", "mexico", "saudi", "scottish", "a-league", "super-lig", "primeira")):
        return "футбол"
    return "прочее"
def fee(p): return 0.05 * p * (1 - p)
rows = []
for r in R:
    for lab in ("p2h", "p30", "p5"):
        p = r.get(lab)
        if p is None or not (0.02 <= p <= 0.98): continue
        for side, pp, y in ((0, p, r["y"]), (1, 1 - p, 1 - r["y"])):
            cost = pp + HALF + fee(pp)
            rows.append({"lab": lab, "cat": cat(r), "p": pp, "y": y, "roi": (y - cost) / (pp + HALF), "gs": r["gs"], "id": r["id"], "side": side,
                         "draw": "draw" in (r.get("q") or "").lower(), "mv": (r.get("p30") - r.get("p2h")) * (1 if side == 0 else -1) if r.get("p30") is not None and r.get("p2h") is not None else None})
print("рынков", len(R), "с ценой за 30 мин:", sum(1 for r in R if r.get("p30") is not None), "| строк", len(rows))
print("по видам:", collections.Counter(cat(r) for r in R if r.get("p30") is not None).most_common())
B = [(0.02, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 0.98)]
def line(v, title):
    if len(v) < 40: return
    n = len(v); pr = st.mean(x["p"] for x in v); wr = st.mean(x["y"] for x in v)
    se = math.sqrt(max(wr * (1 - wr), 1e-6) / n); roi = st.mean(x["roi"] for x in v); h = sorted(v, key=lambda x: x["gs"]); k = n // 2
    r1 = st.mean(x["roi"] for x in h[:k]); r2 = st.mean(x["roi"] for x in h[k:])
    print(f"  {title:22} n={n:6} цена {pr:.3f} выигрыш {wr:.3f} ({(wr-pr)/se:+.1f}σ) | доход на $ после комиссии и спреда {roi*100:+.1f}% | половины {r1*100:+.1f}/{r2*100:+.1f}%")
for lab in ("p30", "p2h", "p5"):
    print(f"\n######## цена {lab}: все виды")
    for a, b in B:
        line([x for x in rows if x["lab"] == lab and a <= x["p"] < b and not x["draw"]], f"{a:.2f}–{b:.2f}")
    line([x for x in rows if x["lab"] == lab and x["draw"]], "ничья (да/нет)")
print("\n######## p30 по видам спорта: фавориты 0.5–0.98 и аутсайдеры 0.02–0.5")
for c in sorted({x["cat"] for x in rows}):
    v = [x for x in rows if x["lab"] == "p30" and x["cat"] == c and not x["draw"]]
    for a, b in ((0.02, 0.2), (0.2, 0.5), (0.5, 0.8), (0.8, 0.98)):
        line([x for x in v if a <= x["p"] < b], f"{c} {a:.2f}–{b:.2f}")
print("\n######## движение цены от -2ч до -30м (сторона, на которую пошли деньги), цена p30")
for a, b, t in ((0.03, 1, "выросла ≥3 п."), (0.01, 0.03, "выросла 1–3 п."), (-0.01, 0.01, "стоит"), (-0.03, -0.01, "упала 1–3 п."), (-1, -0.03, "упала ≥3 п.")):
    line([x for x in rows if x["lab"] == "p30" and x["mv"] is not None and a <= x["mv"] < b and not x["draw"] and 0.1 <= x["p"] <= 0.9], t)
