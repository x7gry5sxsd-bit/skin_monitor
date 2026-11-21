"""Минутные свечи Binance UM вокруг расчётов фандинга (±2 ч): экстремальные расчёты + контрольная выборка обычных."""
import os as _os
HERE = _os.path.dirname(_os.path.abspath(__file__))
import os, io, json, zipfile, requests, time, random, collections, glob
from concurrent.futures import ThreadPoolExecutor
S = requests.Session(); S.headers["User-Agent"] = "Mozilla/5.0"
D = HERE + "/bfr"; OUT = HERE + "/fsw"; os.makedirs(OUT, exist_ok=True)
T0 = 1761955200                                         # 2025-11-01
fr = {os.path.basename(f)[:-5]: json.load(open(f)) for f in glob.glob(f"{D}/*.json")}
fr = {c: r for c, r in fr.items() if r}
need = collections.defaultdict(set)                      # (coin, day) -> settlement times
random.seed(7)
ext = ctl = 0
for c, rows in fr.items():
    for i, (t, x) in enumerate(rows):
        if t < T0: continue
        prev = rows[i - 1][1] if i else 0.0
        if abs(x) >= 0.001 or abs(prev) >= 0.001: ext += 1; pick = True
        elif abs(x) <= 0.0002 and random.random() < 0.004: ctl += 1; pick = True
        else: pick = False
        if pick:
            for dd in (-7200, 0, 7200):
                need[(c, time.strftime("%Y-%m-%d", time.gmtime(t + dd)))].add(t)
for t in range(T0, T0 + 366 * 86400, 86400):            # BTC на все дни — поправка на рынок
    need[("BTC", time.strftime("%Y-%m-%d", time.gmtime(t)))].add(0)
print("экстремальных", ext, "контрольных", ctl, "файлов", len(need), flush=True)
def job(k):
    c, day = k; f = f"{OUT}/{c}_{day}.json"
    if os.path.exists(f): return 1
    url = f"https://data.binance.vision/data/futures/um/daily/klines/{c}USDT/1m/{c}USDT-1m-{day}.zip"
    for a in range(3):
        try:
            r = S.get(url, timeout=60)
            if r.status_code in (200, 404): break
        except Exception: pass
        time.sleep(2)
    else:
        return 0
    if r.status_code != 200:
        json.dump(None, open(f, "w")); return 0
    z = zipfile.ZipFile(io.BytesIO(r.content)); rows = []
    for line in z.read(z.namelist()[0]).decode().splitlines():
        p = line.split(",")
        if not p[0].isdigit(): continue
        ot = int(p[0]); ot = ot // 1000 if ot > 1e14 else ot
        rows.append([ot // 1000, float(p[1]), float(p[2]), float(p[3]), float(p[4]), round(float(p[7]))])
    json.dump(rows, open(f, "w")); return 1
with ThreadPoolExecutor(16) as ex:
    ok = sum(ex.map(job, sorted(need)))
print("DONE", ok, flush=True)
