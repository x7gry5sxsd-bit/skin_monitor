"""Загрузка часовых свечей фьючерсов Gate с кэшем на диске."""
import os as _os
HERE = _os.path.dirname(_os.path.abspath(__file__))
import os, json, time, requests
S = requests.Session(); S.headers["User-Agent"] = "Mozilla/5.0"
G = "https://api.gateio.ws/api/v4/futures/usdt"
CD = HERE + "/cache"; os.makedirs(CD, exist_ok=True)

def get(path, **p):
    for i in range(4):
        r = S.get(G + path, params=p, timeout=30)
        if r.status_code == 429:
            time.sleep(1 + i); continue
        r.raise_for_status(); return r.json()
    r.raise_for_status()

def contracts():
    f = f"{CD}/contracts.json"
    if os.path.exists(f) and time.time() - os.path.getmtime(f) < 6 * 3600:
        return json.load(open(f))
    d = get("/contracts"); json.dump(d, open(f, "w")); return d

def candles(contract, t0, t1, iv="1h"):
    """{время_начала_часа: (open, high, low, close, объём_usdt)}"""
    f = f"{CD}/{contract}_{iv}_{t0}_{t1}.json"
    if os.path.exists(f):
        return {int(k): tuple(v) for k, v in json.load(open(f)).items()}
    out, step = {}, {"1h": 3600, "5m": 300}[iv]
    a = t0
    while a < t1:
        b = min(t1, a + 1900 * step)
        try:
            d = get("/candlesticks", contract=contract, interval=iv, **{"from": a, "to": b})
        except requests.HTTPError:
            d = []
        for c in d:
            out[int(c["t"])] = (float(c["o"]), float(c["h"]), float(c["l"]), float(c["c"]), float(c.get("sum") or 0))
        a = b + step
        time.sleep(0.05)
    json.dump(out, open(f, "w"))
    return out
