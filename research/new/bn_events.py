"""Объявления Binance → минутные цены (архив data.binance.vision) вокруг публикации."""
import os as _os
HERE = _os.path.dirname(_os.path.abspath(__file__))
import os, re, io, json, time, zipfile, requests, datetime as dt
from concurrent.futures import ThreadPoolExecutor
S = requests.Session(); S.headers["User-Agent"] = "Mozilla/5.0"
D = HERE + "/bn"
arts = json.load(open(f"{D}/announcements.json"))
SKIP = {"USDT", "USDC", "BTC", "ETH", "BNB", "FDUSD", "EUR", "TRY", "BRL", "JPY", "USD", "BUSD", "TUSD", "AEUR", "XUSD", "USD1"}
ev = []
for a in arts:
    t = a["releaseDate"] / 1000
    if t < dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc).timestamp():
        continue
    ti = a["title"]
    if "Binance Will List" in ti or "HODLer Airdrop" in ti:
        kind = "spot_list"
    elif "Futures Will Launch" in ti and "TradFi" not in ti:
        kind = "fut_launch"
    else:
        continue
    syms = set(re.findall(r"\(([A-Z0-9]{2,12})\)", ti)) | {m[:-4] for m in re.findall(r"\b([A-Z0-9]{2,15}USDT)\b", ti)}
    for s in syms - SKIP:
        ev.append({"t": t, "kind": kind, "sym": s, "title": ti[:120]})
print("событий", len(ev))
def klines(market, sym, day):
    base = "futures/um" if market == "um" else "spot"
    url = f"https://data.binance.vision/data/{base}/daily/klines/{sym}USDT/1m/{sym}USDT-1m-{day}.zip"
    f = f"{D}/{market}_{sym}_{day}.json"
    if os.path.exists(f):
        return json.load(open(f))
    r = S.get(url, timeout=60)
    if r.status_code != 200:
        json.dump(None, open(f, "w")); return None
    z = zipfile.ZipFile(io.BytesIO(r.content))
    rows = []
    for line in z.read(z.namelist()[0]).decode().splitlines():
        p = line.split(",")
        if not p[0].isdigit(): continue
        ot = int(p[0]); ot = ot // 1000 if ot > 1e14 else ot           # микро- или миллисекунды
        rows.append([ot // 1000, float(p[1]), float(p[2]), float(p[3]), float(p[4]), float(p[7])])
    json.dump(rows, open(f, "w")); return rows
def job(e):
    out = {}
    for market in ("um", "spot"):
        rows = []
        for k in (0, 1):
            day = time.strftime("%Y-%m-%d", time.gmtime(e["t"] + k * 86400))
            r = klines(market, e["sym"], day)
            if r: rows += r
        if rows:
            first = min(x[0] for x in rows)
            if first < e["t"] - 600:                          # торговалась ДО объявления — можно мерить реакцию
                out[market] = rows
    e["mk"] = {m: len(v) for m, v in out.items()}
    return e
with ThreadPoolExecutor(8) as ex:
    ev = list(ex.map(job, ev))
json.dump(ev, open(f"{D}/events.json", "w"))
import collections
print(collections.Counter((e["kind"], tuple(sorted(e["mk"]))) for e in ev))
