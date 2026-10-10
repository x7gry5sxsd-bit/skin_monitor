# xarb.py — v3: межбиржевые окна, 9 бирж + проверка переводов + бумажная торговля (без ключей)
import os, time, threading, logging, re, statistics
import requests
from flask import jsonify, request
import mm_scanner as mm
import xfer
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

FEE = float(env("XA_FEE", "0.001"))
MIN_NET = float(env("XA_MIN_NET", "0.005"))
MAX_NET = float(env("XA_MAX_NET", "0.3"))
MIN_VOL = float(env("XA_MIN_VOL", "50000"))
MIN_SIZE = float(env("XA_MIN_SIZE", "50"))
MAX_DEV = float(env("XA_MAX_DEV", "0.2"))
SCAN_SEC = float(env("XA_SCAN_SEC", "60"))
TRADE = [e.strip() for e in env("XA_TRADE", "bingx,mexc,htx,gate,kucoin,bitget").split(",") if e.strip()]
CHECK_N = int(env("XA_CHECK_N", "25"))
REF = float(env("XA_REF_USD", "100"))
ALERTS = env("XA_ALERTS", "0") == "1"            # алерты по окнам: по умолчанию выключены
log = logging.getLogger("xarb")
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0"
BAD = re.compile(r"(\d+[LS]|UP|DOWN|BULL|BEAR)$")
ST = {"rows": [], "src": {}, "compared": 0, "scans": 0, "hit_scans": 0, "sent": {}, "chronic": {},
      "states": {}, "tot_states": {}, "last": 0, "report_ts": time.time()}

def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0

def get(url, **params):
    return S.get(url, params=params, timeout=20).json()

def binance_like(url):
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
    for c, q_all in by.items():
        if len(q_all) < 2:
            continue
        med = statistics.median((v[0] + v[1]) / 2 for v in q_all.values())
        q = {e: v for e, v in q_all.items()
             if e in TRADE and abs((v[0] + v[1]) / 2 / med - 1) <= MAX_DEV}
        if len(q) < 2:
            continue
        compared += 1
        buy, sell = min(q, key=lambda e: q[e][1]), max(q, key=lambda e: q[e][0])
        if buy == sell:
            continue
        ask, bid = q[buy][1], q[sell][0]
        net = bid * (1 - FEE) / (ask * (1 + FEE)) - 1
        if net < MIN_NET or net > MAX_NET:
            continue
        aq, bq = q[buy][3], q[sell][2]
        size = round(min(aq * ask, bq * bid), 2) if aq > 0 and bq > 0 else None
        rows.append({"coin": c, "buy": buy, "ask": ask, "sell": sell, "bid": bid,
                     "net_pct": round(net * 100, 2), "size_usd": size, "exchanges": len(q_all)})
    rows.sort(key=lambda r: -r["net_pct"])
    states = {}
    for r in rows[:CHECK_N]:
        rt = xfer.route(r["buy"], r["sell"], r["coin"], r["ask"])
        ref = min(r["size_usd"] or REF, REF)
        after = r["net_pct"] - ((rt["fee_usd"] or 0) / ref * 100 if ref else 0)
        r.update(route=rt["text"], state=rt["state"], after_wd_pct=round(after, 2), wd_usd=rt["fee_usd"])
        states[rt["state"]] = states.get(rt["state"], 0) + 1
        ST["tot_states"][rt["state"]] = ST["tot_states"].get(rt["state"], 0) + 1
        ST["chronic"][r["coin"]] = ST["chronic"].get(r["coin"], 0) + 1
        key = f"{r['coin']}:{r['buy']}>{r['sell']}"
        ok_size = r["size_usd"] is None or r["size_usd"] >= MIN_SIZE
        if (ALERTS and rt["state"] in ("open", "half") and after >= MIN_NET * 100 and ok_size
                and now - ST["sent"].get(key, 0) > 1800):
            ST["sent"][key] = now
            mm.tg(f"🔀 {r['coin']}: купить {r['buy']} → продать {r['sell']} | +{after:.2f}% "
                  f"после вывода\n{rt['text']}")
    try:
        xpaper.step(rows[:CHECK_N], by)                          # бумажная торговля
    except Exception:
        log.exception("xpaper")
    ST.update(rows=rows[:60], compared=compared, last=int(now), states=states)
    ST["scans"] += 1
    ST["hit_scans"] += states.get("open", 0) > 0

def loop():
    time.sleep(150)
    mm.tg("🟢 Межбиржевой сканер v3: статистика + бумажная торговля $100 (алерты по окнам выключены)")
    while True:
        t0 = time.time()
        try:
            scan()
            if time.time() - ST["report_ts"] >= 86400:
                ST["report_ts"] = time.time()
                top = sorted(ST["chronic"].items(), key=lambda kv: -kv[1])[:10]
                p = xpaper.summary()
                paper = "\n".join(f"  {k}: капитал ${v['equity']} | сделок {v['trades']} "
                                  f"(в плюс {v['wins']}) | ${v['per_day_usd']}/день" for k, v in p.items())
                mm.tg(f"📊 Межбиржевые окна за сутки: {ST['scans']} сканов\nСтатусы окон: "
                      f"{ST['tot_states']}\nСканов с открытым путём: {ST['hit_scans']}\n"
                      f"Чаще всего: {top}\n📄 Бумага ($100):\n{paper}")
                ST["chronic"], ST["tot_states"] = {}, {}
        except Exception:
            log.exception("xarb")
        time.sleep(max(10, SCAN_SEC - (time.time() - t0)))

@mm.app.route("/xarb")
def xarb_page():
    if not mm.STATS_TOKEN or request.args.get("t") != mm.STATS_TOKEN:
        return "forbidden", 403
    return jsonify({"paper": xpaper.summary(), "last": ST["last"], "src": ST["src"],
                    "scans": ST["scans"], "hit_scans": ST["hit_scans"], "states": ST["states"],
                    "states_today": ST["tot_states"], "windows": ST["rows"][:30]})

threading.Thread(target=loop, daemon=True).start()