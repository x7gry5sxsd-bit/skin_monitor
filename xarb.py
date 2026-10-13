# xarb.py — v4.1: межбиржевые окна (9 бирж) + проверка переводов + радар + «идеальная бумага по живым стаканам»
import os, time, json, threading, logging, re, statistics
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
ALERTS = env("XA_ALERTS", "0") == "1"            # радар в Telegram (включить: XA_ALERTS=1)
ALERT_MIN = float(env("XA_ALERT_MIN", "3"))      # радар: только открытые окна от 3% после вывода
IDEAL_MIN = float(env("XA_IDEAL_MIN", "1"))      # идеальная бумага: от 1% после всех комиссий
IDEAL_SIZES = (50.0, 20.0)                       # пробуем $50, если стакан мал — $20
IDEAL_COOL = float(env("XA_IDEAL_COOL_MIN", "30"))  # один путь — не чаще раза в 30 минут
IDEAL_ID = "ideal_v2"
REAL_K = float(env("XA_REAL_K", "0.25"))         # из сделок v4: до финиша доходит ~25% окна
RUB = float(env("XA_RUB", "87"))
log = logging.getLogger("xarb")
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0"
BAD = re.compile(r"(\d+[LS]|UP|DOWN|BULL|BEAR)$")
ST = {"rows": [], "src": {}, "compared": 0, "scans": 0, "hit_scans": 0, "sent": {}, "chronic": {},
      "states": {}, "tot_states": {}, "last": 0, "report_ts": time.time()}
IDEAL = {}

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

def ideal_load():
    try:
        r = xpaper.q("SELECT js FROM paper_xarb WHERE id = %s", (IDEAL_ID,), fetch=True)
        data = json.loads(r[0][0]) if r else None
    except Exception:
        data = None
    IDEAL.update(data or {"trades": 0, "pnl": 0.0, "t0": time.time(), "cool": {}, "coins": {},
                          "log": [], "book_fail": 0, "thin": 0})

def ideal_take(r, rt, now):
    """Засчитываем сделку, только если окно проходит по ЖИВЫМ стаканам обеих бирж."""
    key = f"{r['coin']}:{r['buy']}>{r['sell']}"
    if rt["state"] != "open" or now < IDEAL["cool"].get(key, 0):
        return False
    wd = rt["fee_usd"] if rt["fee_usd"] is not None else 1.0
    if r["net_pct"] - wd / IDEAL_SIZES[0] * 100 < IDEAL_MIN:      # быстрый отсев без запросов
        return False
    ba, bb = xpaper.book(r["buy"], r["coin"]), xpaper.book(r["sell"], r["coin"])
    if not ba or not bb:
        return False
    for usd in IDEAL_SIZES:
        qty = xpaper.buy_qty(ba[1], usd)
        arrive = qty - wd / r["ask"] if qty else 0
        got = xpaper.sell_usdt(bb[0], arrive) if arrive > 0 else None
        if not got:
            continue                                               # стакана не хватило — пробуем меньше
        pct = (got / usd - 1) * 100
        if pct < IDEAL_MIN:
            IDEAL["book_fail"] += 1                                # на табло было, по стакану нет
            return False
        gain = got - usd
        IDEAL["cool"][key] = now + IDEAL_COOL * 60
        IDEAL["trades"] += 1
        IDEAL["pnl"] += gain
        IDEAL["coins"][r["coin"]] = round(IDEAL["coins"].get(r["coin"], 0) + gain, 3)
        IDEAL["log"] = (IDEAL["log"] + [[int(now), r["coin"], f"{r['buy']}>{r['sell']}",
                                         usd, round(pct, 2), round(gain, 3)]])[-50:]
        return True
    IDEAL["thin"] += 1                                             # стакан тоньше $20
    return False

def ideal_summary():
    if not IDEAL:
        return {}
    days = max((time.time() - IDEAL["t0"]) / 86400, 0.01)
    day = IDEAL["pnl"] / days
    return {"trades": IDEAL["trades"], "pnl_usd": round(IDEAL["pnl"], 2), "days": round(days, 2),
            "ceiling_per_day_usd": round(day, 2), "ceiling_per_day_rub": round(day * RUB),
            "realistic_per_day_usd": round(day * REAL_K, 2), "realistic_per_day_rub": round(day * REAL_K * RUB),
            "book_fail": IDEAL["book_fail"], "thin_book": IDEAL["thin"],
            "top_coins": sorted(IDEAL["coins"].items(), key=lambda kv: -kv[1])[:8],
            "last": IDEAL["log"][-8:]}

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
    ST["by"] = by                                   # котировки всех бирж — для xroute
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
    states, ideal_changed = {}, False
    IDEAL["cool"] = {k: t for k, t in IDEAL["cool"].items() if t > now}
    for r in rows[:CHECK_N]:
        rt = xfer.route(r["buy"], r["sell"], r["coin"], r["ask"])
        ref = min(r["size_usd"] or REF, REF)
        after = r["net_pct"] - ((rt["fee_usd"] or 0) / ref * 100 if ref else 0)
        r.update(route=rt["text"], state=rt["state"], after_wd_pct=round(after, 2), wd_usd=rt["fee_usd"])
        states[rt["state"]] = states.get(rt["state"], 0) + 1
        ST["tot_states"][rt["state"]] = ST["tot_states"].get(rt["state"], 0) + 1
        ST["chronic"][r["coin"]] = ST["chronic"].get(r["coin"], 0) + 1
        try:
            ideal_changed |= ideal_take(r, rt, now)
        except Exception:
            log.exception("ideal")
        key = f"{r['coin']}:{r['buy']}>{r['sell']}"
        ok_size = r["size_usd"] is None or r["size_usd"] >= MIN_SIZE
        if (ALERTS and rt["state"] == "open" and after >= ALERT_MIN and ok_size
                and now - ST["sent"].get(key, 0) > 3600):
            ST["sent"][key] = now
            sz = f"~${r['size_usd']:.0f} в стакане" if r["size_usd"] else "объём стакана проверить"
            mm.tg(f"🎯 Радар: {r['coin']} +{after:.1f}% после всех комиссий\n"
                  f"Купить {r['buy']} по {r['ask']:.6g} → продать {r['sell']} по {r['bid']:.6g}\n"
                  f"{rt['text']} | {sz}")
    if ideal_changed:
        xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES (%s, %s)
                    ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (IDEAL_ID, json.dumps(IDEAL)))
    try:
        xpaper.step(rows[:CHECK_N], by)
    except Exception:
        log.exception("xpaper")
    ST.update(rows=rows[:60], compared=compared, last=int(now), states=states)
    ST["scans"] += 1
    ST["hit_scans"] += states.get("open", 0) > 0

def loop():
    time.sleep(150)
    ideal_load()
    mm.tg("🟢 Межбиржевой сканер v4.1: идеальная бумага теперь проверяет живые стаканы")
    while True:
        t0 = time.time()
        try:
            scan()
            if time.time() - ST["report_ts"] >= 86400:
                ST["report_ts"] = time.time()
                i = ideal_summary()
                mm.tg(f"📊 Межбиржевые окна за сутки: {ST['scans']} сканов | статусы {ST['tot_states']}\n"
                      f"🏆 Потолок по живым стаканам: ${i.get('ceiling_per_day_usd')}/день "
                      f"≈ {i.get('ceiling_per_day_rub')}₽ ({i.get('trades')} сделок)\n"
                      f"🎯 Реалистично (~¼): ${i.get('realistic_per_day_usd')}/день "
                      f"≈ {i.get('realistic_per_day_rub')}₽\n"
                      f"Отсеял стакан: {i.get('book_fail')} | тонкий стакан: {i.get('thin_book')}\n"
                      f"Топ монет: {i.get('top_coins')}")
                ST["chronic"], ST["tot_states"] = {}, {}
        except Exception:
            log.exception("xarb")
        time.sleep(max(10, SCAN_SEC - (time.time() - t0)))

@mm.app.route("/xarb")
def xarb_page():
    if not mm.STATS_TOKEN or request.args.get("t") != mm.STATS_TOKEN:
        return "forbidden", 403
    return jsonify({"ideal": ideal_summary(), "paper": xpaper.summary(), "last": ST["last"],
                    "src": ST["src"], "scans": ST["scans"], "hit_scans": ST["hit_scans"],
                    "states": ST["states"], "states_today": ST["tot_states"], "windows": ST["rows"][:30]})

threading.Thread(target=loop, daemon=True).start()