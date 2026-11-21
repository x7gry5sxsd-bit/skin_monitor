"""Polymarket: дневные рынки «вверх/вниз» по акциям/индексам/золоту/серебру + история цены «Up» (10 мин) + свечи бессрочников Gate."""
import os as _os
HERE = _os.path.dirname(_os.path.abspath(__file__))
import sys, os, json, time, requests, datetime as dt
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, HERE)
import gate
S = requests.Session(); S.headers["User-Agent"] = "Mozilla/5.0"
D = HERE + "/pmfin"
SER = {"gold-daily-up-or-down": "XAU_USDT", "silver-daily-up-or-down": "XAG_USDT", "spx-daily-up-or-down": "SPY_USDT", "spx-open-daily-up-or-down": "SPY_USDT",
       "ndx-daily-up-or-down": "NAS100_USDT", "nvda-daily-up-down": "NVDA_USDT", "tsla-daily-up-down": "TSLA_USDT", "aapl-daily-up-down": "AAPL_USDT",
       "msft-daily-up-down": "MSFT_USDT", "amzn-daily-up-down": "AMZN_USDT", "googl-daily-up-down": "GOOGL_USDT", "meta-daily-up-down": "META_USDT",
       "coin-daily-up-or-down": "COIN_USDT", "hood-daily-up-or-down": "HOOD_USDT", "spy-daily-up-or-down": "SPY_USDT", "qqq-daily-up-or-down": "QQQ_USDT"}
cre = {c["name"]: int(c.get("create_time") or 0) for c in gate.contracts()}
def ts(s):
    try: return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception: return None
rows = []
for slug, con in SER.items():
    evs = []
    for off in range(0, 2000, 100):
        d = S.get("https://gamma-api.polymarket.com/events", params={"series_slug": slug, "closed": "true", "limit": 100, "offset": off}, timeout=60).json()
        if not isinstance(d, list) or not d: break
        evs += d
        if len(d) < 100: break
    n0 = 0
    for e in evs:
        for m in e.get("markets") or []:
            try:
                outs = json.loads(m.get("outcomes") or "[]"); op = json.loads(m.get("outcomePrices") or "[]"); tok = json.loads(m.get("clobTokenIds") or "[]")
            except Exception: continue
            if outs[:2] != ["Up", "Down"] or len(op) != 2 or len(tok) != 2: continue
            end = ts(m.get("endDate") or e.get("endDate")); st0 = ts(m.get("startDate") or e.get("startDate"))
            if not end or not st0 or end < cre.get(con, 1e12) + 3 * 86400 or float(op[0]) not in (0.0, 1.0): continue
            rows.append({"slug": slug, "con": con, "title": e.get("title"), "end": end, "start": st0, "y": float(op[0]), "tok": tok[0], "vol": m.get("volumeNum"),
                         "fee": (m.get("feeSchedule") or {}).get("rate")}); n0 += 1
    print(f"{slug:28} событий {len(evs):4} годных {n0}", flush=True)
def hist(r):
    f = f"{D}/h_{r['tok'][:20]}.json"
    if os.path.exists(f): r["h"] = json.load(open(f)); return r
    try:
        h = S.get("https://clob.polymarket.com/prices-history", timeout=40, params={"market": r["tok"], "startTs": int(r["start"]), "endTs": int(r["end"]), "fidelity": 10}).json()
        r["h"] = [(int(x["t"]), float(x["p"])) for x in h.get("history") or []]
    except Exception: r["h"] = []
    json.dump(r["h"], open(f, "w")); return r
with ThreadPoolExecutor(8) as ex: rows = list(ex.map(hist, rows))
json.dump(rows, open(f"{D}/markets.json", "w"))
print("рынков", len(rows), "с историей", sum(1 for r in rows if len(r["h"]) > 5), flush=True)
NOW = int(time.time()) // 3600 * 3600
for con in sorted({r["con"] for r in rows}):
    a = max(cre.get(con, 0) // 3600 * 3600, NOW - 9800 * 3600)
    k1 = gate.candles(con, a, NOW, "1h"); k5 = gate.candles(con, NOW - 34 * 86400, NOW, "5m")
    print(con, "1h", len(k1), "5m", len(k5), flush=True)
