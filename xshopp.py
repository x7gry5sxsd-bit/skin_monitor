# xshopp.py — «реальная бумага» магазина: считает сделки так же, как живой бот, по всем монетам с фьючерсом MEXC.
#   Окно — по полному стакану HTX и партнёра (как window() в xshop): тонкий стакан даёт хуже цену, а не «ноль окон».
#   Порог, размер сделки и пауза — как у живого магазина. Пачка стоит столько, сколько берёт открытый путь
#   партнёр → HTX (xfer). Запас монеты на HTX кончается: пока пачка в пути (~40 мин), продавать нечего.
#   Фандинг шорта MEXC учитывается. Ничего не торгует.
import os, sys, time, json, threading, logging
from flask import jsonify, request
import mm_scanner as mm
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

TRADE = float(env("XS_TRADE_USD", "15"))
MIN_EDGE = float(env("XS_MIN_EDGE", "0.007"))
COOL = float(env("XS_COOL_SEC", "90"))
SLOT = float(env("XA_SLOT_USD", "100"))
BATCH = SLOT * 0.9
MIN_FILL = float(env("XSP_MIN_FILL", "5"))                 # меньше $5 запаса — уже не сделка
TRANSIT = float(env("XSP_TRANSIT_MIN", "40")) * 60         # пачка партнёр → HTX в пути
MAX_BOOKS = int(env("XSP_MAX_BOOKS", "25"))                 # стаканов за скан — только у лучших по тикерам
MAX_DEV, HOURS, FEE_TTL = 0.05, 48, 6 * 3600
PARTNERS = ("kucoin", "gate")
log = logging.getLogger("xshopp")
S = {"p": {}, "t0": time.time()}
RT = {"by": None, "save_t": time.time()}
FEE = {}                                                   # (монета, биржа) → (время, состояние пути, комиссия $)

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'shopp_v1'", fetch=True)
    if r:
        S.update(json.loads(r[0][0]))

def save():
    now = time.time()
    for k in [k for k, c in S["p"].items() if now - c.get("seen", now) > 6 * 3600]:
        S["p"].pop(k)
    xpaper.q("""INSERT INTO paper_xarb (id, js) VALUES ('shopp_v1', %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(S),))

def route_fee(coin, v, px):
    """Путь партнёр → HTX и комиссия пачки в $ (кэш 6 ч)."""
    c = FEE.get((coin, v))
    if c and time.time() - c[0] < FEE_TTL:
        return c[1], c[2]
    xf = sys.modules.get("xfer")
    try:
        r = xf.route(v, "htx", coin, px) if xf else {}
    except Exception:
        r = {}
    FEE[(coin, v)] = (time.time(), r.get("state", "unknown"), r.get("fee_usd"))
    return FEE[(coin, v)][1], FEE[(coin, v)][2]

def buy_cost(asks, qty, ex):
    left, usd = qty, 0.0
    for pr, a in asks:
        take = min(left, a)
        usd, left = usd + take * pr, left - take
        if left <= 1e-12:
            return usd * (1 + xpaper.fee(ex))
    return None

def row(c, hour, fund):
    if not c["h"] or c["h"][-1][0] != hour:                # [час, сканов, окон, сделок, вал $, пачки $, объём $, Σпремии, без запаса, фандинг $]
        c["h"] = (c["h"] + [[hour, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0, round(fund * 3 * SLOT / 24, 5)]])[-HOURS:]
    return c["h"][-1]

def process(by, now):
    X, PS = sys.modules.get("xhedge"), sys.modules.get("xpscout")
    try:
        perp = set(X.perps()["mexc"]) if X else None
    except Exception:
        perp = None
    fund = (PS.S.get("fund") or {}) if PS else {}
    hour, fh, cand = int(now // 3600), xpaper.fee("htx"), []
    for coin, q in by.items():
        qh = q.get("htx")
        if not qh or (perp is not None and coin not in perp) or min(qh[0], qh[1]) <= 0:
            continue
        for v in PARTNERS:
            qp = q.get(v)
            if not qp or min(qp[0], qp[1]) <= 0:
                continue
            prem = (qh[0] + qh[1]) / (qp[0] + qp[1]) - 1
            if abs(prem) > MAX_DEV:                        # другой токен с тем же тикером
                continue
            c = S["p"].setdefault(f"{coin}:{v}", {"inv": SLOT, "part": 0.0, "tr": [], "cool": 0, "h": []})
            c["seen"] = now
            if any(t[0] <= now for t in c["tr"]):          # пачка дошла до HTX
                c["inv"] += sum(t[1] for t in c["tr"] if t[0] <= now)
                c["tr"] = [t for t in c["tr"] if t[0] > now]
            r = row(c, hour, fund.get(coin) or 0)
            r[1] += 1
            r[7] = round(r[7] + prem, 5)
            if c["inv"] < MIN_FILL:
                r[8] += 1
            top = qh[0] * (1 - fh) / (qp[1] * (1 + xpaper.fee(v))) - 1
            if top >= MIN_EDGE and now >= c["cool"] and c["inv"] >= MIN_FILL:
                cand.append((top, coin, v))
    cand.sort(reverse=True)
    books = {}
    for top, coin, v in cand[:MAX_BOOKS]:                  # как живой бот: окно по полному стакану
        c = S["p"][f"{coin}:{v}"]
        bh = books.get(("htx", coin)) or books.setdefault(("htx", coin), xpaper.book("htx", coin))
        bv = xpaper.book(v, coin)
        if not bh or not bv or not bh[0]:
            continue
        price = bh[0][0][0]
        qty = min(TRADE, c["inv"]) / price
        got, cost = xpaper.sell_usdt(bh[0], qty, "htx"), buy_cost(bv[1], qty, v)
        if not got or not cost:
            continue
        st, wd = route_fee(coin, v, bv[1][0][0])
        if st in ("closed", "mismatch"):
            continue
        wd = wd if wd is not None else 1.0
        c["wd"], c["st"] = round(wd, 3), st
        edge = got / cost - 1 - wd / BATCH
        r = c["h"][-1]
        if edge < MIN_EDGE:
            continue
        r[2] += 1
        c["inv"] -= qty * price
        c["part"] += cost
        c["cool"] = now + COOL
        r[3] += 1
        r[4] = round(r[4] + got - cost, 4)
        r[5] = round(r[5] + wd * cost / BATCH, 4)          # доля пачки — как у живого бота
        r[6] = round(r[6] + cost, 2)
        if c["part"] >= BATCH:
            c["tr"].append([now + TRANSIT, round(c["part"], 2)])
            c["part"] = 0.0

def summary(key, hours=24):
    c = S["p"].get(key)
    if not c:
        return None
    lo = int(time.time() // 3600) - hours
    rows = [r for r in c["h"] if r[0] > lo]
    n = sum(r[1] for r in rows)
    if n < 30:
        return None
    days = max(len(rows) / 24, 1 / 24)
    tr, gross, wdf, vol, fund = (sum(r[i] for r in rows) for i in (3, 4, 5, 6, 9))
    return {"pair": key, "hours": len(rows), "net_usd_day": round((gross - wdf + fund) / days, 2),
            "trades_day": round(tr / days, 1), "gross_usd_day": round(gross / days, 2),
            "batch_usd_day": round(wdf / days, 2), "funding_usd_day": round(fund / days, 2),
            "avg_edge_pct": round(gross / vol * 100, 2) if vol else None,
            "windows": sum(r[2] for r in rows), "no_stock_pct": round(sum(r[8] for r in rows) / n * 100),
            "htx_premium_pct": round(sum(r[7] for r in rows) / n * 100, 2),
            "batch_fee_usd": c.get("wd"), "route": c.get("st")}

def rate(coin, hours=24):
    """$ в сутки чистыми у лучшего партнёра — оценка для аллокатора."""
    out = [summary(f"{coin}:{v}", hours) for v in PARTNERS]
    return max([s["net_usd_day"] for s in out if s] or [0.0])

def coins():
    return {k.split(":")[0] for k in S["p"] if (summary(k) or {}).get("net_usd_day", 0) > 0}

def ready():
    return time.time() - S.get("t0", time.time()) > 6 * 3600

def loop():
    time.sleep(170)
    load()
    while True:
        now = time.time()
        try:
            XA = sys.modules.get("xarb")
            by = XA.ST.get("by") if XA else None
            if by is not None and by is not RT["by"]:      # пришёл новый скан xarb
                RT["by"] = by
                process(by, now)
            if now - RT["save_t"] > 1800:
                RT["save_t"] = now
                save()
        except Exception:
            log.exception("xshopp")
        time.sleep(15)

@mm.app.route("/shopp")
def shopp_page():
    """Реальная бумага магазина: /shopp?t=...&h=24&n=30 (&coin=NES). Сверху — живое против бумаги по нашим монетам."""
    if not (mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN):
        return "forbidden", 403
    try:
        h, n = int(request.args.get("h", "24")), int(request.args.get("n", "30"))
    except ValueError:
        h, n = 24, 30
    coin = request.args.get("coin", "").upper()
    rows = [s for s in (summary(k, h) for k in list(S["p"]) if not coin or k.split(":")[0] == coin) if s]
    rows.sort(key=lambda s: -s["net_usd_day"])
    AL, live = sys.modules.get("xalloc"), {}
    for c in (AL.A.get("coins", {}) if AL else {}):
        k, pnl = AL.live_trades(c, h)
        live[c] = {"живых_сделок": k, "живой_итог_$": pnl, "бумага_$_сутки": rate(c, h)}
    return jsonify({"hours": h, "pairs": len(S["p"]), "ready": ready(), "trade_usd": TRADE,
                    "min_edge_pct": MIN_EDGE * 100, "transit_min": TRANSIT / 60, "live_vs_paper": live, "top": rows[:n]})

threading.Thread(target=loop, daemon=True).start()
