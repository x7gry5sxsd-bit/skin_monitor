"""Объявления Binance (публичный список статей): новые листинги/запуски фьючерсов (каталог 48) и делистинги (каталог 161)."""
import os as _os
HERE = _os.path.dirname(_os.path.abspath(__file__))
import json, time, os, requests
D = HERE + "/bn"; os.makedirs(D, exist_ok=True)
S = requests.Session(); S.headers["User-Agent"] = "Mozilla/5.0"
def fetch(cat, pages=60):
    out = []
    for pg in range(1, pages + 1):
        d = S.get("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query",
                  params={"type": 1, "catalogId": cat, "pageNo": pg, "pageSize": 50}, timeout=30).json()
        c = (d.get("data") or {}).get("catalogs") or []
        arts = c[0]["articles"] if c else []
        if not arts: break
        out += arts; time.sleep(0.3)
    return out
a = fetch(48); json.dump(a, open(f"{D}/announcements.json", "w")); print("листинги/запуски:", len(a))
b = fetch(161); json.dump(b, open(f"{D}/delist.json", "w")); print("делистинги:", len(b))
