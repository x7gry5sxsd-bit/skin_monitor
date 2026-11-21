"""Часовые свечи Upbit/Bithumb (KRW) и Gate (USDT-фьючерсы) с 01.07.2026 — в кэш на диск."""
import os
HERE = os.path.dirname(os.path.abspath(__file__))

import sys, os, json, time, requests, datetime as dt
sys.path.insert(0, HERE)
import gate
S = requests.Session(); S.headers["User-Agent"] = "Mozilla/5.0"
CD = HERE + "/kr"; os.makedirs(CD, exist_ok=True)
START = int(dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc).timestamp())
END = int(time.time()) // 3600 * 3600
BASE = {"upbit": "https://api.upbit.com/v1/candles/minutes/60", "bithumb": "https://api.bithumb.com/v1/candles/minutes/60"}

def kr(src, coin):
    f = f"{CD}/{src}_{coin}.json"
    if os.path.exists(f):
        return
    out, to = {}, None
    while True:
        p = {"market": f"KRW-{coin}", "count": 200}
        if to:
            p["to"] = to
        for i in range(5):
            try:
                r = S.get(BASE[src], params=p, timeout=30)
                if r.status_code == 429:
                    time.sleep(1 + i); continue
                d = r.json() if r.status_code == 200 else []
                break
            except Exception:
                time.sleep(2); d = []
        if not isinstance(d, list) or not d:
            break
        for c in d:
            t = int(dt.datetime.fromisoformat(c["candle_date_time_utc"]).replace(tzinfo=dt.timezone.utc).timestamp())
            out[t] = (c["opening_price"], c["high_price"], c["low_price"], c["trade_price"], c["candle_acc_trade_price"])
        first = min(out)
        if first <= START or len(d) < 200:
            break
        to = dt.datetime.fromtimestamp(first, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        time.sleep(0.12)
    json.dump(out, open(f, "w"))

gc = {c["name"][:-5] for c in gate.contracts() if c["name"].endswith("_USDT") and not c.get("in_delisting")}
up = {m["market"][4:] for m in S.get("https://api.upbit.com/v1/market/all", timeout=30).json() if m["market"].startswith("KRW-")}
bt = {m["market"][4:] for m in S.get("https://api.bithumb.com/v1/market/all", timeout=30).json() if m["market"].startswith("KRW-")}
todo = [("upbit", c) for c in sorted(up & gc)] + [("bithumb", c) for c in sorted(bt & gc)] + [("upbit", "USDT"), ("bithumb", "USDT")]
json.dump({"upbit": sorted(up & gc), "bithumb": sorted(bt & gc)}, open(f"{CD}/lists.json", "w"))
print("todo", len(todo), flush=True)
from concurrent.futures import ThreadPoolExecutor
def job(a):
    i, (src, c) = a
    kr(src, c)
    if i % 25 == 0:
        print(i, src, c, flush=True)
with ThreadPoolExecutor(8) as ex:
    list(ex.map(job, enumerate(todo)))
print("korea done", flush=True)
coins = sorted((up | bt) & gc)
def gjob(a):
    i, c = a
    gate.candles(f"{c}_USDT", START - 8 * 86400, END)
    if i % 50 == 0:
        print("gate", i, c, flush=True)
with ThreadPoolExecutor(4) as ex:
    list(ex.map(gjob, enumerate(coins + ["BTC", "ETH"])))
print("DONE", flush=True)
