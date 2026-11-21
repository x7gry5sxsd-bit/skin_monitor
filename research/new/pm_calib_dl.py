"""Рассчитанные рынки «кто победит» Polymarket (спорт и киберспорт) + цена за 2 ч и 30 мин до начала матча."""
import os as _os
HERE = _os.path.dirname(_os.path.abspath(__file__))
import os, json, time, requests, datetime as dt
from concurrent.futures import ThreadPoolExecutor
S = requests.Session(); S.headers["User-Agent"] = "Mozilla/5.0"
OUT = HERE + "/pm"; os.makedirs(OUT, exist_ok=True)
def ts(s):
    try:
        s = str(s).replace("Z", "+00:00").replace(" ", "T", 1)
        if len(s) >= 3 and s[-3] in "+-" and ":" not in s[-3:]: s += ":00"
        return dt.datetime.fromisoformat(s).timestamp()
    except Exception: return None
rows, a = [], int(dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc).timestamp())
end = time.time() - 6 * 3600
while a < end:
    b = a + 3 * 86400
    for off in range(0, 3000, 100):
        p = [("closed", "true"), ("sports_market_types", "moneyline"), ("limit", "100"), ("offset", str(off)),
             ("end_date_min", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(a))),
             ("end_date_max", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(b)))]
        try:
            d = S.get("https://gamma-api.polymarket.com/markets", params=p, timeout=60).json()
        except Exception:
            d = []
        if not isinstance(d, list):
            break
        for m in d:
            op = json.loads(m.get("outcomePrices") or "[]"); toks = json.loads(m.get("clobTokenIds") or "[]")
            gs = ts(m.get("gameStartTime"))
            if len(op) != 2 or len(toks) != 2 or not gs or float(op[0]) not in (0.0, 1.0) or gs < a - 7 * 86400 or gs > b + 86400:
                continue
            ev = (m.get("events") or [{}])[0]
            rows.append({"id": m["id"], "q": m.get("question"), "outs": json.loads(m.get("outcomes") or "[]"),
                         "y": float(op[0]), "tok": toks[0], "gs": gs, "series": ev.get("seriesSlug"), "title": ev.get("title"),
                         "fee": (m.get("feeSchedule") or {}).get("rate"), "neg": ev.get("negRisk"), "vol": m.get("volumeNum")})
        if len(d) < 100: break
    a = b
print("рынков", len(rows), flush=True)
def hist(r):
    try:
        h = S.get("https://clob.polymarket.com/prices-history", timeout=30,
                  params={"market": r["tok"], "startTs": int(r["gs"] - 6 * 3600), "endTs": int(r["gs"]), "fidelity": 10}).json()
        pts = [(int(x["t"]), float(x["p"])) for x in h.get("history") or []]
        for lab, dtm in (("p2h", 7200), ("p30", 1800), ("p5", 300)):
            pr = [p for t, p in pts if t <= r["gs"] - dtm]
            r[lab] = pr[-1] if pr else None
    except Exception:
        pass
    return r
with ThreadPoolExecutor(8) as ex:
    rows = list(ex.map(hist, rows))
json.dump(rows, open(f"{OUT}/moneyline.json", "w"))
print("DONE", sum(1 for r in rows if r.get("p30") is not None), flush=True)
