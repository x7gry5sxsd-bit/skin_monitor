"""Часовые свечи Gate за ~13 мес. по всем ликвидным бессрочникам (для проверки «выкупа обвала»)."""
import os as _os
HERE = _os.path.dirname(_os.path.abspath(__file__))
import sys, time, json
sys.path.insert(0, HERE)
import gate
from concurrent.futures import ThreadPoolExecutor
NOW = int(time.time()) // 3600 * 3600; T0 = NOW - 9800 * 3600
tk = gate.get("/tickers")
liq = sorted([x["contract"] for x in tk if x["contract"].endswith("_USDT") and float(x.get("volume_24h_quote") or 0) >= 300_000])
print("контрактов с оборотом ≥ $300k:", len(liq), flush=True)
def job(c):
    try: return c, len(gate.candles(c, T0, NOW))
    except Exception as e: return c, 0
with ThreadPoolExecutor(3) as ex: res = list(ex.map(job, liq))
json.dump({"T0": T0, "NOW": NOW, "coins": [c for c, n in res if n > 500]}, open(HERE + "/flush_universe.json", "w"))
print("DONE", sum(1 for c, n in res if n > 500), flush=True)
