# xpscout.py — бумага «Маятника» по ВСЕМ монетам: какие монеты реально качаются HTX ⇄ KuCoin / Gate.
#   Ничего не торгует. Каждый скан xarb (раз в минуту, лучшие цены 9 бирж) → по каждой паре «монета:партнёр» оба
#   направления чистыми после комиссий: A = продать на HTX / купить у партнёра, B = продать у партнёра / купить
#   на HTX. Окно засчитывается, только если держится 2 скана подряд (отсекает «окна» от разницы во времени опроса
#   бирж). По правилам xpend v2 ведётся бумажный маятник: запас XPS_UNITS сделок с каждой стороны, порог
#   XP_MIN_EDGE, выравнивание XP_REBAL_EDGE. Только монеты с фьючерсом MEXC (хедж).
#   Фандинг MEXC: плюс — шорт хеджа ПОЛУЧАЕТ, минус — платит (≈ ставка × 3 в сутки).
#   /pscout?t=... (&h=24 — за сколько часов, &coin=GALA — одна монета, &n=25 — сколько строк)
import os, sys, time, json, threading, logging
from flask import jsonify, request
import mm_scanner as mm
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

TRADE = float(env("XP_TRADE_USD", "15"))
MIN_EDGE = float(env("XP_MIN_EDGE", "0.004"))
REBAL = float(env("XP_REBAL_EDGE", "0.0005"))
UNITS = int(env("XPS_UNITS", "3"))               # запас: по 3 сделки монет и 3 сделки USDT на каждой стороне
MAX_DEV = float(env("XP_MAX_DEV", "0.05"))       # разошлись >5% — другой токен с тем же тикером, пропускаем
HOURS = 48
PARTNERS = ("kucoin", "gate")
log = logging.getLogger("xpscout")
S = {"p": {}, "fund": {}, "t0": time.time()}
RT = {"by": None, "t": 0, "dt": 60.0, "fund_t": 0, "save_t": time.time()}

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'pscout_v1'", fetch=True)
    if r:
        S.update(json.loads(r[0][0]))
    for c in S["p"].values():
        c["prev"] = [None, None]                       # после перезапуска «2 скана подряд» считаем заново

def save():
    now = time.time()
    for k in [k for k, c in S["p"].items() if now - c.get("seen", now) > 6 * 3600]:
        S["p"].pop(k)                                  # пара пропала из сканера на 6 ч — забываем
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('pscout_v1', %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(S),))

def funding():
    """Ставки фандинга MEXC по всем фьючерсам (раз в час)."""
    try:
        d = xpaper.S.get("https://contract.mexc.com/api/v1/contract/ticker", timeout=20).json().get("data") or []
        S["fund"] = {x["symbol"][:-5]: float(x.get("fundingRate") or 0) for x in d
                     if str(x.get("symbol", "")).endswith("_USDT")}
    except Exception as e:
        log.warning("funding: %s", e)

def edge_pair(qh, qp, v):
    """(A, B, премия HTX) по лучшим ценам. qh/qp — (bid, ask, bidQty, askQty, vol) из xarb."""
    fh, fp = xpaper.fee("htx"), xpaper.fee(v)
    hb, ha, hbq, haq = qh[0], qh[1], qh[2], qh[3]
    pb, pa = qp[0], qp[1]
    if min(hb, ha, pb, pa) <= 0:
        return None
    prem = hb / pa - 1                                 # исполнимо: продать на HTX покупателю, купить у партнёра
    if abs(prem) > MAX_DEV:
        return None
    a = hb * (1 - fh) / (pa * (1 + fp)) - 1            # продать на HTX, купить у партнёра
    b = pb * (1 - fp) / (ha * (1 + fh)) - 1            # продать у партнёра, купить на HTX
    if hbq and hbq * hb < TRADE:                       # у HTX объём лучшей цены известен: $15 не влезает
        a = None
    if haq and haq * ha < TRADE:
        b = None
    return a, b, prem

def thr(x):
    """Пороги как в xpend v2. x — чистое число сделок A (монеты ушли с HTX к партнёру, USDT — наоборот)."""
    ta = tb = MIN_EDGE
    if x > 0 or UNITS - x < 2:                         # на HTX монет меньше половины / у партнёра мало USDT
        tb = REBAL
    if x < 0 and UNITS + x < 2:                        # на HTX монет больше половины и мало USDT
        ta = REBAL
    return ta, tb

def process(by, now):
    X = sys.modules.get("xhedge")
    try:
        perp = set(X.perps()["mexc"]) if X else None
    except Exception:
        perp = None
    hour = int(now // 3600)
    for coin, q in by.items():
        qh = q.get("htx")
        if not qh or (perp is not None and coin not in perp):
            continue
        for v in PARTNERS:
            qp = q.get(v)
            e = edge_pair(qh, qp, v) if qp else None
            if not e:
                continue
            a, b, prem = e
            c = S["p"].setdefault(f"{coin}:{v}", {"x": 0, "prev": [None, None], "h": []})
            pa, pb = c["prev"]
            c["prev"], c["seen"] = [a, b], now
            ca = min(a, pa) if a is not None and pa is not None else None    # держится 2 скана подряд
            cb = min(b, pb) if b is not None and pb is not None else None
            if not c["h"] or c["h"][-1][0] != hour:
                c["h"] = (c["h"] + [[hour, 0, 0, 0, 0.0, 0, 0.0]])[-HOURS:]
            r = c["h"][-1]                             # [час, сканов, окон A, окон B, $ бумаги, сделок, Σпремии]
            r[1] += 1
            r[6] = round(r[6] + prem, 5)
            r[2] += int(ca is not None and ca >= MIN_EDGE)
            r[3] += int(cb is not None and cb >= MIN_EDGE)
            ta, tb = thr(c["x"])
            if ca is not None and ca >= ta and c["x"] < UNITS:
                c["x"] += 1
                r[4], r[5] = round(r[4] + ca * TRADE, 4), r[5] + 1
            elif cb is not None and cb >= tb and c["x"] > -UNITS:
                c["x"] -= 1
                r[4], r[5] = round(r[4] + cb * TRADE, 4), r[5] + 1

def summary(key, hours=24):
    c = S["p"].get(key)
    if not c:
        return None
    lo = int(time.time() // 3600) - hours
    rows = [r for r in c["h"] if r[0] > lo]
    n = sum(r[1] for r in rows)
    if n < 30:
        return None
    days = max(n * RT["dt"] / 86400, 1 / 24)
    f = S["fund"].get(key.split(":")[0])
    return {"pair": key, "hours": round(days * 24, 1), "A_windows": sum(r[2] for r in rows),
            "B_windows": sum(r[3] for r in rows), "trades": sum(r[5] for r in rows),
            "usd_day": round(sum(r[4] for r in rows) / days, 2),
            "htx_premium_pct": round(sum(r[6] for r in rows) / n * 100, 2),
            "funding_day_pct": round(f * 3 * 100, 3) if f is not None else None, "x_now": c["x"]}

def rate(coin, hours=24):
    """$ в сутки на бумаге «Маятника» у лучшего партнёра (для аллокатора)."""
    out = [summary(f"{coin}:{v}", hours) for v in PARTNERS]
    return max([s["usd_day"] for s in out if s] or [0.0])

def loop():
    time.sleep(150)
    load()
    while True:
        now = time.time()
        try:
            XA = sys.modules.get("xarb")
            by = XA.ST.get("by") if XA else None
            if by is not None and by is not RT["by"]:  # пришёл новый скан xarb
                if RT["t"]:
                    RT["dt"] = 0.8 * RT["dt"] + 0.2 * min(now - RT["t"], 600)
                RT["by"], RT["t"] = by, now
                process(by, now)
            if now - RT["fund_t"] > 3600:
                RT["fund_t"] = now
                funding()
            if now - RT["save_t"] > 1800:
                RT["save_t"] = now
                save()
        except Exception:
            log.exception("xpscout")
        time.sleep(15)

@mm.app.route("/pscout")
def pscout_page():
    if not (mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN):
        return "forbidden", 403
    try:
        h, n = int(request.args.get("h", "24")), int(request.args.get("n", "25"))
    except ValueError:
        h, n = 24, 25
    coin = request.args.get("coin", "").upper()
    rows = [s for s in (summary(k, h) for k in list(S["p"]) if not coin or k.split(":")[0] == coin) if s]
    mp = request.args.get("min_prem")                  # магазину нужны монеты с постоянной премией HTX, как NES
    if mp:
        rows = [s for s in rows if s["htx_premium_pct"] >= float(mp)]
    if request.args.get("sort") == "prem":
        rows.sort(key=lambda s: -s["htx_premium_pct"])
    else:
        rows.sort(key=lambda s: -s["usd_day"])
    return jsonify({"hours": h, "pairs": len(S["p"]), "scan_sec": round(RT["dt"]), "min_edge_pct": MIN_EDGE * 100,
                    "rebal_pct": REBAL * 100, "units": UNITS, "trade_usd": TRADE, "top": rows[:n]})

threading.Thread(target=loop, daemon=True).start()
