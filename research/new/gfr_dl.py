"""История фандинга Gate (≈13 мес.) по 239 ликвидным монетам."""
import os as _os
HERE = _os.path.dirname(_os.path.abspath(__file__))
import sys, os, json, time
sys.path.insert(0, HERE)
import gate
from concurrent.futures import ThreadPoolExecutor
U = json.load(open(HERE + "/flush_universe.json")); T0, NOW = U["T0"], U["NOW"]
def job(c):
    f = fHERE + "/gfr/{c}.json"
    if os.path.exists(f): return 1
    rows, a = {}, T0
    while a < NOW:
        b = min(NOW, a + 300 * 86400)
        try: d = gate.get("/funding_rate", contract=c, limit=1000, **{"from": a, "to": b})
        except Exception: d = []
        for x in d: rows[int(x["t"])] = float(x["r"])
        a = b
    json.dump(sorted(rows.items()), open(f, "w")); return 1
with ThreadPoolExecutor(4) as ex: print("DONE", sum(ex.map(job, U["coins"])))
