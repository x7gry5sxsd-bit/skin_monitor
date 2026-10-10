# xarb.py — межбиржевые окна: 9 бирж, все монеты к USDT (публичные данные, без ключей и ccxt)
import os, time, threading, logging, re, statistics
import requests
from flask import jsonify, request
import mm_scanner as mm

def env(k, d=""):
    return os.getenv(k, d).strip()

FEE = float(env("XA_FEE", "0.001"))              # комиссия тейкера на каждой бирже
MIN_NET = float(env("XA_MIN_NET", "0.005"))      # окно от 0.5% после комиссий
MAX_NET = float(env("XA_MAX_NET", "0.3"))        # больше 30% — почти наверняка разные токены
MIN_VOL = float(env("XA_MIN_VOL", "50000"))      # мин. объём пары за сутки, $
MIN_SIZE = float(env("XA_MIN_SIZE", "50"))       # алерт, если окно вмещает от $50
MAX_DEV = float(env("XA_MAX_DEV", "0.2"))        # отклонение от медианы бирж
SCAN_SEC = float(env("XA_SCAN_SEC", "60"))
log = logging.getLogger("xarb")
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0"
BAD = re.compile(r"(\d+[LS]|UP|DOWN|BULL|BEAR)$")
ST = {"rows": [], "src": {}, "compared": 0, "scans": 0, "hit_scans": 0, "seen": {}, "sent": {},
      "chronic": {}, "last": 0, "report_ts": time.time()}

def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0

def get(url, **params):
    return S.get(url, params=params, timeout=20).json()

def binance_like(url):                                  # Binance и MEXC: одинаковый формат
    out = {}
    for x in get(url):
        s = x.get("symbol", "")
        if s.endswith("USDT"):
            out[s[:-4]] = (f(x.get("bidPrice")), f(x.get("askPrice")), f(x.get("bidQty")),
                           f(x.get("askQty")), f(x.get("quoteVolume")))
    return out

def okx():
    out = {}
    for x in get("https://www.okx.com/api/v5/market/tickers", instType="SPOT").get("data", []):
        i = x.get("instId", "")
        if i.endswith("-USDT"):
            out[i[:-5]] = (f(x.get("bidPx")), f(x.get("askPx")), f(x.get("bidSz")),
                           f(x.get("askSz")), f(x.get("volCcy24h")))
    return out

def bybit():
    out = {}
    for x in get("https://api.bybit.com/v5/market/tickers", category="spot").get("result", {}).get("list", []):
        s = x.get("symbol", "")
        if s.endswith("USDT"):
            out[s[:-4]] = (f(x.get("bid1Price")), f(x.get("ask1Price")), f(x.get("bid1Size")),
                           f(x.get("ask1Size")), f(x.get("turnover24h")))
    return out

def bingx():
    out = {}
    d = get("https://open-api.bingx.com/openApi/spot/v1/ticker/24hr", timestamp=int(time.time() * 1000))
    for x in d.get("data") or []:
        s = x.get("symbol", "")
        if s.endswith("-USDT"):
            out[s[:-5]] = (f(x.get("bidPrice")), f(x.get("askPrice")), f(x.get("bidQty")),
                           f(x.get("askQty")), f(x.get("quoteVolume")))
    return out

def gate():
    out = {}
    for x in get("https://api.gateio.ws/api/v4/spot/tickers"):
        s = x.get("currency_pair", "")
        if s.endswith("_USDT"):
            out[s[:-5]] = (f(x.get("highest_bid")), f(x.get("lowest_ask")), 0.0, 0.0,
                           f(x.get("quote_volume")))
    return out

def kucoin():
    out = {}
    for x in get("https://api.kucoin.com/api/v1/market/allTickers").get("data", {}).get("ticker", []):
        s = x.get("symbol", "")
        if s.endswith("-USDT"):
            out[s[:-5]] = (f(x.get("buy")), f(x.get("sell")), 0.0, 0.0, f(x.get("volValue")))
    return out

def htx():
    out = {}
    for x in get("https://api.huobi.pro/market/tickers").get("data", []):
        s = (x.get("symbol") or "").upper()
        if s.endswith("USDT"):
            out[s[:-4]] = (f(x.get("bid")), f(x.get("ask")), f(x.get("bidSize")),
                           f(x.get("askSize")), f(x.get("vol")))
    return out

def bitget():
    out = {}
    for x in get("https://api.bitget.com/api/v2/spot/market/tickers").get("data", []):
        s = x.get("symbol", "")
        if s.endswith("USDT"):
            out[s[:-4]] = (f(x.get("bidPr")), f(x.get("askPr")), f(x.get("bidSz")), f(x.get("askSz")),
                           f(x.get("usdtVolume") or x.get("quoteVolume")))
    return out

EXCH = {"binance": lambda: binance_like("https://api.binance.com/api/v3/ticker/24hr"),
        "mexc": lambda: binance_like("https://api.mexc.com/api/v3/ticker/24hr"),
        "okx": okx, "bybit": bybit, "bingx": bingx, "gate": gate, "kucoin": kucoin,
        "htx": htx, "bitget": bitget}
ENABLED = [e.strip() for e in env("XA_EXCH", ",".join(EXCH)).split(",") if e.strip() in EXCH]

def scan():
    by = {}
    for name in ENABLED:
        try:
            d = {c: v for c, v in EXCH[name]().items()
                 if v[0] > 0 and v[1] >= v[0] and v[4] >= MIN_VOL and not BAD.search(c)}
            ST["src"][name] = f"ok {len(d)}"
            for c, v in d.items():
                by.setdefault(c, {})[name] = v
        except Exception as e:
            ST["src"][name] = f"ошибка {type(e).__name__}"
    now, rows, compared = time.time(), [], 0
    for c, q in by.items():
        if len(q) < 2:
            continue
        compared += 1
        med = statistics.median((v[0] + v[1]) / 2 for v in q.values())
        q = {e: v for e, v in q.items() if abs((v[0] + v[1]) / 2 / med - 1) <= MAX_DEV}
        if len(q) < 2:
            continue
        buy = min(q, key=lambda e: q[e][1])
        sell = max(q, key=lambda e: q[e][0])
        if buy == sell:
            continue
        ask, bid = q[buy][1], q[sell][0]
        net = bid * (1 - FEE) / (ask * (1 + FEE)) - 1
        if net < MIN_NET:
            continue
        aq, bq = q[buy][3], q[sell][2]
        size = round(min(aq * ask, bq * bid), 2) if aq > 0 and bq > 0 else None
        key = f"{c}:{buy}>{sell}"
        first = ST["seen"].get(key, now)
        rows.append({"coin": c, "buy": buy, "ask": ask, "sell": sell, "bid": bid,
                     "net_pct": round(net * 100, 2), "size_usd": size, "key": key,
                     "alive_min": round((now - first) / 60, 1), "first": first,
                     "suspect": net > MAX_NET, "exchanges": len(q)})
    rows.sort(key=lambda r: -r["net_pct"])
    ST["seen"] = {r["key"]: r["first"] for r in rows}
    for r in rows:
        ST["chronic"][r["coin"]] = ST["chronic"].get(r["coin"], 0) + 1
        ok_size = r["size_usd"] is None or r["size_usd"] >= MIN_SIZE
        if (not r["suspect"] and ok_size and r["alive_min"] * 60 >= 2 * SCAN_SEC
                and now - ST["sent"].get(r["key"], 0) > 3600):
            ST["sent"][r["key"]] = now
            sz = f"~${r['size_usd']:.0f}" if r["size_usd"] else "объём не известен"
            mm.tg(f"🔀 {r['coin']}: купить {r['buy']} {r['ask']:.6g} → продать {r['sell']} "
                  f"{r['bid']:.6g}\n+{r['net_pct']}% после комиссий | {sz} | живёт {r['alive_min']} мин\n"
                  f"⚠️ Проверить: открыт ли вывод на {r['buy']} и ввод на {r['sell']}, "
                  f"совпадает ли контракт токена")
    ST.update(rows=rows[:60], compared=compared, last=int(now))
    ST["scans"] += 1
    ST["hit_scans"] += any(not r["suspect"] for r in rows)

def loop():
    time.sleep(150)
    mm.tg(f"🟢 Межбиржевой сканер запущен: {', '.join(ENABLED)} (все монеты к USDT)")
    while True:
        t0 = time.time()
        try:
            scan()
            if time.time() - ST["report_ts"] >= 86400:
                ST["report_ts"] = time.time()
                top = sorted(ST["chronic"].items(), key=lambda kv: -kv[1])[:10]
                mm.tg(f"📊 Межбиржевые окна за сутки: {ST['scans']} сканов, монет сравнивается "
                      f"{ST['compared']}\nСканов с окном ≥{MIN_NET*100:.1f}%: {ST['hit_scans']}\n"
                      f"Чаще всего (хронические): {top}\nБиржи: {ST['src']}")
                ST["chronic"] = {}
        except Exception:
            log.exception("xarb")
        time.sleep(max(10, SCAN_SEC - (time.time() - t0)))

@mm.app.route("/xarb")
def xarb_page():
    if not mm.STATS_TOKEN or request.args.get("t") != mm.STATS_TOKEN:
        return "forbidden", 403
    top = sorted(ST["chronic"].items(), key=lambda kv: -kv[1])[:15]
    rows = [{k: v for k, v in r.items() if k not in ("key", "first")} for r in ST["rows"][:30]]
    return jsonify({"last": ST["last"], "src": ST["src"], "compared": ST["compared"],
                    "scans": ST["scans"], "hit_scans": ST["hit_scans"], "chronic": top,
                    "windows": rows})

threading.Thread(target=loop, daemon=True).start()