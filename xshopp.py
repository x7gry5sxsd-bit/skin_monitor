# xshopp.py v2 — «реальная бумага» магазина: считает сделки так же, как живой бот, по всем монетам с фьючерсом MEXC.
#   Окно — по полному стакану HTX и партнёра (как window() в xshop). Порог, сделка и пауза — как у живого магазина.
#   Пачка стоит столько, сколько берёт открытый путь партнёр → HTX (xfer), и едет столько, сколько её сеть.
#   Запас на HTX кончается — пока пачка в пути, продавать нечего. Фандинг шорта MEXC учитывается. Ничего не торгует.
#   v2: воронка по каждой паре (почему нет сделок), «почти-окна», время пачки по сети, ошибки не рвут скан.
import os, re, sys, time, json, threading, logging
from flask import jsonify, request
import mm_scanner as mm
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

TRADE = float(env("XS_TRADE_USD", "15"))
MIN_EDGE = float(env("XS_MIN_EDGE", "0.007"))
NEAR = MIN_EDGE / 2                                        # «почти-окно»: больше половины порога
COOL = float(env("XS_COOL_SEC", "90"))
SLOT = float(env("XA_SLOT_USD", "100"))
BATCH = SLOT * 0.9
MIN_FILL = float(env("XSP_MIN_FILL", "5"))                 # меньше $5 запаса — уже не сделка
TRANSIT = float(env("XSP_TRANSIT_MIN", "40"))              # пачка в пути, если сеть не узнали (мин)
MAX_BOOKS = int(env("XSP_MAX_BOOKS", "25"))                 # стаканов за скан — только у лучших по тикерам
MAX_DEV = float(env("XSP_MAX_DEV", "0.25"))               # у живого магазина защиты нет — двойник отличается в разы
HOURS, FEE_TTL, MIN_HOURS = 48, 6 * 3600, 6
PARTNERS = ("kucoin", "gate")
NET_MIN = {"BSC": 8, "BEP20": 8, "TRX": 5, "TRC20": 5, "SOL": 3, "SUI": 3, "APT": 3, "TON": 5, "AVAXC": 5,
           "ARB": 10, "ARBITRUM": 10, "OP": 10, "OPTIMISM": 10, "BASE": 10, "MATIC": 10, "POLYGON": 10,
           "ETH": 35, "ERC20": 35}                         # + ~5 мин на зачисление HTX
log = logging.getLogger("xshopp")
S = {"p": {}, "t0": time.time()}
RT = {"by": None, "save_t": time.time()}
DIAG = {"scans": 0, "last": 0, "cand": 0, "books": 0, "err": None, "err_t": 0}
FEE = {}                                                   # (монета, биржа) → (время, путь, комиссия $, минут в пути)
# строка часа: 0 час, 1 сканов, 2 окон по стакану, 3 сделок, 4 вал $, 5 пачки $, 6 объём $, 7 Σпремии, 8 без запаса,
# 9 фандинг $/ч, 10 окон по лучшей цене, 11 почти-окон, 12 лучшее окно %, 13 стакан не держит, 14 путь закрыт, 15 ниже порога,
# 16 нет запаса или пауза, 17 не дошла очередь, 18 ошибка
W = 19

def g(r, i):
    return r[i] if len(r) > i else 0

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
    """Путь партнёр → HTX: (состояние, комиссия пачки $, минут в пути). Кэш 6 ч."""
    c = FEE.get((coin, v))
    if c and time.time() - c[0] < FEE_TTL:
        return c[1], c[2], c[3]
    xf, r = sys.modules.get("xfer"), {}
    try:
        r = xf.route(v, "htx", coin, px) if xf else {}
    except Exception:
        pass
    m = re.search(r"сеть ([A-Za-z0-9_\-]+)", r.get("text") or "")
    net = m.group(1).upper() if m else ""
    mins = NET_MIN.get(net, TRANSIT) + 5
    FEE[(coin, v)] = (time.time(), r.get("state", "unknown"), r.get("fee_usd"), mins)
    return FEE[(coin, v)][1:]

def buy_cost(asks, qty, ex):
    left, usd = qty, 0.0
    for pr, a in asks:
        take = min(left, a)
        usd, left = usd + take * pr, left - take
        if left <= 1e-12:
            return usd * (1 + xpaper.fee(ex))
    return None

def row(c, hour, fund):
    if not c["h"] or c["h"][-1][0] != hour:
        c["h"] = (c["h"] + [[hour, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0, round(fund * 3 * SLOT / 24, 5)] + [0] * (W - 10)])[-HOURS:]
    r = c["h"][-1]
    if len(r) < W:                                         # строка из v1 — дополняем полями воронки
        r.extend([0] * (W - len(r)))
    return r

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
            prem = qh[0] / qp[1] - 1                       # как торгует бот: продать на HTX по bid, купить у партнёра по ask
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
            r[12] = max(r[12], round(top * 100, 3))
            if top >= NEAR:
                r[11] += 1
            if top >= MIN_EDGE:
                r[10] += 1
                if now >= c["cool"] and c["inv"] >= MIN_FILL:
                    cand.append((top, coin, v))
                else:
                    r[16] += 1                             # запас уехал пачкой или пауза после сделки
    cand.sort(reverse=True)
    DIAG.update(scans=DIAG["scans"] + 1, last=now, cand=len(cand), books=0)
    books = {}
    for top, coin, v in cand[MAX_BOOKS:]:                  # стаканов за скан ограничено — остальные ждут
        S["p"][f"{coin}:{v}"]["h"][-1][17] += 1
    for top, coin, v in cand[:MAX_BOOKS]:                  # как живой бот: окно по полному стакану
        c = S["p"][f"{coin}:{v}"]
        r = c["h"][-1]
        try:
            if ("htx", coin) not in books:
                books[("htx", coin)] = xpaper.book("htx", coin)
            bh, bv = books[("htx", coin)], xpaper.book(v, coin)
            DIAG["books"] += 1
            if not bh or not bv or not bh[0] or not bv[1]:
                r[13] += 1
                continue
            price = bh[0][0][0]
            qty = min(TRADE, c["inv"]) / price
            got, cost = xpaper.sell_usdt(bh[0], qty, "htx"), buy_cost(bv[1], qty, v)
            if not got or not cost:                        # стакан не держит сделку
                r[13] += 1
                continue
            st, wd, mins = route_fee(coin, v, bv[1][0][0])
            if st in ("closed", "mismatch"):
                r[14] += 1
                continue
            wd = wd if wd is not None else 1.0
            c["wd"], c["st"], c["mins"] = round(wd, 3), st, mins
            edge = got / cost - 1 - wd / BATCH
            if edge < MIN_EDGE:
                r[15] += 1
                continue
            r[2] += 1
            c["inv"] -= qty * price
            c["part"] += cost
            c["cool"] = now + COOL
            r[3] += 1
            r[4] = round(r[4] + got - cost, 4)
            r[5] = round(r[5] + wd * cost / BATCH, 4)      # доля пачки — как у живого бота
            r[6] = round(r[6] + cost, 2)
            cum = S.setdefault("cum", {}).setdefault(f"{coin}:{v}", [0, 0.0])   # для xhunt: окна и всплески
            cum[0], cum[1] = cum[0] + 1, round(cum[1] + got - cost - wd * cost / BATCH, 4)
            if c["part"] >= BATCH:
                c["tr"].append([now + mins * 60, round(c["part"], 2)])
                c["part"] = 0.0
        except Exception as e:                             # одна монета не обрывает весь скан
            r[18] += 1
            DIAG.update(err=f"{coin}:{v} {type(e).__name__}: {str(e)[:120]}", err_t=now)

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
    tr, gross, wdf, vol, fund = (sum(g(r, i) for r in rows) for i in (3, 4, 5, 6, 9))
    return {"pair": key, "hours": len(rows), "net_usd_day": round((gross - wdf + fund) / days, 2),
            "trades_day": round(tr / days, 1), "gross_usd_day": round(gross / days, 2),
            "batch_usd_day": round(wdf / days, 2), "funding_usd_day": round(fund / days, 2),
            "avg_edge_pct": round(gross / vol * 100, 2) if vol else None,
            "best_edge_pct": max([g(r, 12) for r in rows] or [0]),
            "funnel": {"почти": sum(g(r, 11) for r in rows), "окно_по_лучшей_цене": sum(g(r, 10) for r in rows),
                       "стакан_не_держит": sum(g(r, 13) for r in rows), "путь_закрыт": sum(g(r, 14) for r in rows),
                       "ниже_порога_по_стакану": sum(g(r, 15) for r in rows),
                       "нет_запаса_или_пауза": sum(g(r, 16) for r in rows), "не_дошла_очередь": sum(g(r, 17) for r in rows),
                       "ошибка": sum(g(r, 18) for r in rows), "сделок": tr},
            "no_stock_pct": round(sum(r[8] for r in rows) / n * 100),
            "htx_premium_pct": round(sum(r[7] for r in rows) / n * 100, 2),
            "batch_fee_usd": c.get("wd"), "route": c.get("st"), "transit_min": c.get("mins")}

def rate(coin, hours=24):
    """$ в сутки чистыми у лучшего партнёра — оценка для аллокатора (мало данных — 0, путь не подтверждён — вдвое ниже)."""
    best = 0.0
    for v in PARTNERS:
        s = summary(f"{coin}:{v}", hours)
        if not s or s["hours"] < MIN_HOURS:
            continue
        best = max(best, s["net_usd_day"] * (1 if s["route"] == "open" else 0.5))
    return round(best, 2)

def trades(coin, hours):
    lo = int(time.time() // 3600) - hours
    return sum(r[3] for v in PARTNERS for r in (S["p"].get(f"{coin}:{v}") or {}).get("h", []) if r[0] > lo)

def coins():
    return {k.split(":")[0] for k in S["p"] if rate(k.split(":")[0]) > 0}

def ready():
    return time.time() - S.get("t0", time.time()) > MIN_HOURS * 3600

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
        except Exception as e:
            DIAG.update(err=f"скан: {type(e).__name__}: {str(e)[:120]}", err_t=now)
            log.exception("xshopp")
        time.sleep(15)

@mm.app.route("/shopp")
def shopp_page():
    """Реальная бумага: /shopp?t=...&h=24&n=30 (&coin=NES) (&sort=near|best). Сверху — живое против бумаги и диагностика."""
    if not (mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN):
        return "forbidden", 403
    try:
        h, n = int(request.args.get("h", "24")), int(request.args.get("n", "30"))
    except ValueError:
        h, n = 24, 30
    coin, mode = request.args.get("coin", "").upper(), request.args.get("sort", "")
    rows = [s for s in (summary(k, h) for k in list(S["p"]) if not coin or k.split(":")[0] == coin) if s]
    key = {"near": lambda s: -s["funnel"]["почти"], "best": lambda s: -s["best_edge_pct"]}.get(mode, lambda s: -s["net_usd_day"])
    rows.sort(key=key)
    AL, live = sys.modules.get("xalloc"), {}
    for c in (AL.A.get("coins", {}) if AL else {}):
        k, pnl = AL.live_trades(c, h)
        live[c] = {"живых_сделок": k, "бумажных_сделок": trades(c, h), "живой_итог_$": pnl, "бумага_$_сутки": rate(c, h)}
    now = time.time()
    diag = {"сканов": DIAG["scans"], "последний_скан_мин_назад": round((now - DIAG["last"]) / 60, 1) if DIAG["last"] else None,
            "кандидатов_в_скане": DIAG["cand"], "стаканов_в_скане": DIAG["books"],
            "ошибка": DIAG["err"], "ошибка_мин_назад": round((now - DIAG["err_t"]) / 60) if DIAG["err_t"] else None}
    return jsonify({"hours": h, "pairs": len(S["p"]), "ready": ready(), "trade_usd": TRADE, "min_edge_pct": MIN_EDGE * 100,
                    "диагностика": diag, "live_vs_paper": live, "top": rows[:n]})

threading.Thread(target=loop, daemon=True).start()
