# xpaper.py — облегчённый: стаканы бирж и база для xarb, xexec и xinv (бумажная стратегия v3 отключена)
import os, time, logging
import requests
import psycopg2
import mm_scanner as mm

FEE = float(os.getenv("XA_FEE", "0.001").strip())
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0"
log = logging.getLogger("xpaper")

def q(sql, params=(), fetch=False):
    conn = psycopg2.connect(mm.DB_URL, connect_timeout=20)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall() if fetch else None
    finally:
        conn.close()

q("CREATE TABLE IF NOT EXISTS paper_xarb(id TEXT PRIMARY KEY, js TEXT)")

def _lv(rows):
    return [(float(r[0]), float(r[1])) for r in rows or []]

def book(ex, c):
    try:
        if ex == "gate":
            d = S.get("https://api.gateio.ws/api/v4/spot/order_book",
                      params={"currency_pair": f"{c}_USDT", "limit": 20}, timeout=10).json()
        elif ex == "kucoin":
            d = S.get("https://api.kucoin.com/api/v1/market/orderbook/level2_20",
                      params={"symbol": f"{c}-USDT"}, timeout=10).json().get("data") or {}
        elif ex == "htx":
            d = S.get("https://api.huobi.pro/market/depth", timeout=10,
                      params={"symbol": f"{c.lower()}usdt", "type": "step0", "depth": 20}).json().get("tick") or {}
        elif ex == "mexc":
            d = S.get("https://api.mexc.com/api/v3/depth", params={"symbol": f"{c}USDT", "limit": 20},
                      timeout=10).json()
        elif ex == "bingx":
            d = S.get("https://open-api.bingx.com/openApi/spot/v1/market/depth", timeout=10,
                      params={"symbol": f"{c}-USDT", "limit": 20,
                              "timestamp": int(time.time() * 1000)}).json().get("data") or {}
        elif ex == "bitget":
            d = S.get("https://api.bitget.com/api/v2/spot/market/orderbook", timeout=10,
                      params={"symbol": f"{c}USDT", "type": "step0", "limit": 20}).json().get("data") or {}
        elif ex == "okx":
            d = (S.get("https://www.okx.com/api/v5/market/books", params={"instId": f"{c}-USDT", "sz": 20},
                       timeout=10).json().get("data") or [{}])[0]
        else:
            return None
        bids, asks = _lv(d.get("bids")), _lv(d.get("asks"))
        return (bids, asks) if bids and asks else None
    except Exception:
        return None

def buy_qty(asks, usdt):                     # сколько монет купим на usdt (проход по стакану)
    left, qty = usdt / (1 + FEE), 0.0
    for p, a in asks:
        take = min(left, p * a)
        qty += take / p
        left -= take
        if left <= 1e-9:
            return qty
    return None

def sell_usdt(bids, qty):                    # сколько USDT получим за qty монет
    left, usd = qty, 0.0
    for p, a in bids:
        take = min(left, a)
        usd += take * p
        left -= take
        if left <= 1e-12:
            return usd * (1 - FEE)
    return None

def step(rows, by):                          # бумажная стратегия v3 отключена (вызывается из xarb)
    return None

def summary():                               # пустой раздел «paper» в /xarb
    return {}
