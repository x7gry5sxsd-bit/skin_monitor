"""Binance UM: история фандинга (data.binance.vision, помесячно) по всем монетам, у которых есть бессрочный фьючерс на Gate."""
import os as _os
HERE = _os.path.dirname(_os.path.abspath(__file__))
import os, io, json, zipfile, requests, time, sys
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, HERE)
import gate
S = requests.Session(); S.headers["User-Agent"] = "Mozilla/5.0"
D = HERE + "/bfr"
MONTHS = ["2025-11", "2025-12"] + [f"2026-{m:02d}" for m in range(1, 11)]
coins = sorted({c["name"][:-5] for c in gate.contracts() if c["name"].endswith("_USDT")})
print("монет на Gate", len(coins), flush=True)
def get(sym, mon):
    url = f"https://data.binance.vision/data/futures/um/monthly/fundingRate/{sym}USDT/{sym}USDT-fundingRate-{mon}.zip"
    for k in range(3):
        try:
            r = S.get(url, timeout=40)
            if r.status_code == 404: return None
            if r.status_code == 200: break
        except Exception: pass
        time.sleep(2)
    else:
        return None
    z = zipfile.ZipFile(io.BytesIO(r.content)); out = []
    for line in z.read(z.namelist()[0]).decode().splitlines():
        p = line.split(",")
        if p[0].isdigit(): out.append((int(p[0]) // 1000, float(p[2])))
    return out
def job(c):
    f = f"{D}/{c}.json"
    if os.path.exists(f): return c, json.load(open(f))
    rows = get(c, "2026-10")
    if rows is None:
        json.dump([], open(f, "w")); return c, []
    for m in MONTHS[:-1]:
        rows += get(c, m) or []
    rows = sorted(set(rows)); json.dump(rows, open(f, "w")); return c, rows
with ThreadPoolExecutor(12) as ex:
    res = list(ex.map(job, coins))
have = [(c, r) for c, r in res if r]
print("есть на Binance", len(have), "строк", sum(len(r) for c, r in have))
ext = sum(1 for c, r in have for t, x in r if abs(x) >= 0.001)
print("расчётов с |фандинг| ≥ 0.1%:", ext)
