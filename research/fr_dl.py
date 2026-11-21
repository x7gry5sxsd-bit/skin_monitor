import os
HERE = os.path.dirname(os.path.abspath(__file__))
import sys, json, os, time
sys.path.insert(0, HERE)
import gate
from concurrent.futures import ThreadPoolExecutor
FD = HERE + "/fund"; os.makedirs(FD, exist_ok=True)
import datetime as dt
START = int(dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc).timestamp()); END = int(time.time()) // 3600 * 3600
cs = [c["name"] for c in gate.contracts() if c["name"].endswith("_USDT") and not c.get("in_delisting")]
tick = {t["contract"]: float(t.get("volume_24h_quote") or 0) for t in gate.get("/tickers")}
cs = [c for c in cs if tick.get(c, 0) >= 200000]
print("contracts", len(cs), flush=True)
def job(c):
    coin = c[:-5]; f = f"{FD}/{coin}.json"
    if not os.path.exists(f):
        rows, a = [], START
        while a < END:
            try:
                d = gate.get("/funding_rate", contract=c, limit=1000, **{"from": a, "to": min(END, a + 40 * 86400)})
            except Exception:
                d = []
            rows += [(int(x["t"]), float(x["r"])) for x in d]
            a += 40 * 86400
        json.dump(rows, open(f, "w"))
    gate.candles(c, START - 8 * 86400, END)
with ThreadPoolExecutor(4) as ex:
    for i, _ in enumerate(ex.map(job, cs)):
        if i % 50 == 0: print(i, flush=True)
json.dump(cs, open(HERE + "/fr_list.json", "w"))
print("DONE", flush=True)
