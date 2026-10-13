# xshop.py v3 — «магазин»: запас монет на HTX, USDT на MEXC и Gate. Окно → продажа из запаса на HTX + докупка
#   на бирже, где сейчас дешевле (MEXC или Gate), в ту же секунду. Купленное везём на HTX пачкой с той же биржи.
#   XS_COINS=US — монеты; XS_VENUES=mexc,gate — где докупать. Хедж — шорт, вручную. XS_DRY=1 — только считает.
import os, time, json, math, threading, logging
from datetime import datetime, timezone, timedelta
from flask import jsonify, request
import mm_scanner as mm
import xpaper
import xaddr
import xexec_api as api

def env(k, d=""):
    return os.getenv(k, d).strip()

DRY = env("XS_DRY", "1") == "1"
COINS = [c.strip().upper() for c in env("XS_COINS", env("XS_COIN", "US")).split(",") if c.strip()]
VENUES = [v.strip() for v in env("XS_VENUES", "mexc").split(",") if v.strip() in ("mexc", "gate")]
TRADE = float(env("XS_TRADE_USD", "15"))
MIN_EDGE = float(env("XS_MIN_EDGE", "0.008"))     # чистыми, уже за вычетом доли комиссии пачки
COOL = float(env("XS_COOL_SEC", "60"))
DAILY_STOP = float(env("XS_DAILY_STOP", "3"))
STEP = float(env("XS_STEP_SEC", "10"))
TZ = timezone(timedelta(hours=float(env("TZ_OFFSET", "8"))))
WD = {"mexc": {"US": 0.12, "ETHW": 0.03, "SXT": 0.21, "MAGIC": 0.15, "BLUE": 0.03, "DOS": 0.22, "M": 0.17},
      "gate": {"US": 0.06, "BLUE": 0.05, "MAGIC": 0.05}}
GNET = {"US": "SUI", "BLUE": "SUI", "MAGIC": "ARBEVM"}
FEE = xpaper.FEE
log = logging.getLogger("xshop")
S = {}

def batch_usd(coin):
    return float(env(f"XS_BATCH_USD_{coin}", env("XS_BATCH_USD", "30")))

def wd_usd(coin, v):
    key = f"XS_WD_USD_{coin}" if v == "mexc" else f"XS_GWD_USD_{coin}"
    return float(env(key, str(WD[v].get(coin, 0.3))))

def v_free(v, cur):
    return api.mx_free(cur) if v == "mexc" else api.gt_free(cur)

def v_buy(v, coin, usd):
    return api.mx_buy_quote(f"{coin}USDT", usd) if v == "mexc" else api.gt_buy_quote(coin, usd)

def v_withdraw(v, coin, amt):
    if v == "mexc":
        return api.mx_withdraw(coin, env(f"XE_NET_{coin}"), xaddr.get(coin), f"{amt:.2f}")
    addr = env(f"XE_GADDR_{coin}", env(f"XE_ADDR_{coin}"))                # Gate — свой доверенный адрес
    return api.gt_withdraw(coin, env(f"XE_GNET_{coin}", GNET.get(coin, "")), addr, f"{amt:.2f}")

def v_wstatus(v, coin, wid):
    if v == "mexc":
        st = api.mx_withdraw_status(coin, wid)
        return "done" if st == 7 else ("cancel" if st in (8, 9) else None)
    st = api.gt_withdraw_status(coin, wid)
    return "done" if st == "DONE" else ("cancel" if st in ("CANCEL", "FAIL", "INVALID") else None)

def blank():
    return {"trades": 0, "wins": 0, "pnl": 0.0, "wd_usd": 0.0, "batches": 0, "done": [], "cool": 0,
            "sig": None, "hedge_step": 0, "rev_hits": 0, "by_venue": {}, "wids": {}}

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = %s", ("shop_dry" if DRY else "shop_live",), fetch=True)
    S.update(json.loads(r[0][0]) if r else {})
    if "c" not in S:                                   # перенос учёта v1 (одна монета) в v3
        old = {k: S.pop(k) for k in list(S) if k in blank() or k == "rev_now"}
        S["c"] = {env("XS_COIN", "US").upper(): {**blank(), **old}} if old.get("trades") is not None else {}
    for k, v in (("day", ""), ("pnl_day", 0.0), ("paused", False), ("warn", {})):
        S.setdefault(k, v)
    for c in COINS:
        S["c"].setdefault(c, blank())
        for k, v in blank().items():
            S["c"][c].setdefault(k, v)

def save():
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES (%s, %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", ("shop_dry" if DRY else "shop_live", json.dumps(S)))

def say(t):
    mm.tg(("🧪🏪 [бумага] " if DRY else "🏪 ") + t)

def warn_once(key, text, every=3600):
    if time.time() - S["warn"].get(key, 0) > every:
        S["warn"][key] = time.time()
        say(text)

def buy_cost(asks, qty):
    left, usd = qty, 0.0
    for pr, a in asks:
        take = min(left, a)
        usd += take * pr
        left -= take
        if left <= 1e-12:
            return usd * (1 + FEE)
    return None

def window(coin):
    bh = xpaper.book("htx", coin)
    if not bh:
        return None
    price = bh[0][0][0]
    q = TRADE / price
    got = xpaper.sell_usdt(bh[0], q)
    rbuy = buy_cost(bh[1], q)                          # обратное: купить на HTX
    opts, rev = [], None
    for v in VENUES:
        bv = xpaper.book(v, coin)
        if not bv:
            continue
        cost = buy_cost(bv[1], q)
        if got and cost:
            opts.append({"edge": got / cost - 1 - wd_usd(coin, v) / batch_usd(coin), "venue": v, "cost": cost})
        rs = xpaper.sell_usdt(bv[0], q)                # обратное: продать на MEXC/Gate
        if rs and rbuy:
            r = rs / rbuy - 1
            rev = r if rev is None else max(rev, r)
    if not opts:
        return None
    opts.sort(key=lambda o: -o["edge"])
    b = opts[0]
    return {"q": q, "got": got, "cost": b["cost"], "edge": b["edge"], "venue": b["venue"], "price": price,
            "opts": opts, "rev": rev, "sig": [bh[0][0][0], b["cost"]]}

def trade(coin, w, now):
    c, q, v = S["c"][coin], w["q"], w["venue"]
    if DRY:
        got, cost = w["got"], w["cost"]
    else:
        if api.hx_free(coin) < q:
            warn_once(f"inv{coin}", f"Запас {coin} на HTX кончился — жду пачку")
            return
        pick = None                                    # лучшая биржа, где хватает USDT
        for o in w["opts"]:
            if o["edge"] < MIN_EDGE:
                break
            if v_free(o["venue"], "USDT") >= o["cost"] * 1.02:
                pick = o
                break
        if not pick:
            warn_once(f"usdt{coin}", f"Мало USDT для докупки {coin} на {', '.join(VENUES).upper()}")
            return
        v, w = pick["venue"], {**w, "cost": pick["cost"], "edge": pick["edge"], "venue": pick["venue"]}
        oid = api.hx_sell_market(f"{coin.lower()}usdt", q).get("data")    # 1) продали из запаса на HTX
        time.sleep(1.5)
        od = api.hx_order(oid).get("data") or {}
        got = float(od.get("field-cash-amount") or 0) - float(od.get("field-fees") or 0)
        cost = round(w["cost"] * 1.001, 2)
        try:
            v_buy(v, coin, cost)                                            # 2) докупили там, где дешевле
        except Exception as e:
            S["paused"] = True
            say(f"❗ Продали {coin} на HTX, но докупка на {v.upper()} не прошла: {str(e)[:150]}. "
                f"Магазин на паузе — докупи {q:.2f} {coin} вручную и открой /shop/start")
            return
    pnl = got - cost
    c["trades"] += 1
    c["wins"] += pnl > 0
    c["pnl"] += pnl
    c["by_venue"][v] = c["by_venue"].get(v, 0) + 1
    S["pnl_day"] += pnl
    c["done"] = (c["done"] + [[int(now), v, round(w["edge"] * 100, 2), round(got, 3), round(cost, 3),
                               round(pnl, 3)]])[-20:]
    say(f"{coin}: продали на HTX {got:.2f}$, докупили на {v.upper()} {cost:.2f}$ → {pnl:+.2f}$ "
        f"(окно {w['edge']*100:.2f}%) | за день {S['pnl_day']:+.2f}$")

def batch(coin, price):
    if DRY:
        return
    c = S["c"][coin]
    for v in VENUES:
        if c["wids"].get(v):
            continue                                   # предыдущая пачка с этой биржи ещё в пути
        amt = math.floor(v_free(v, coin) * 100) / 100
        if amt * price < batch_usd(coin):
            continue
        r = v_withdraw(v, coin, amt)
        c["batches"] += 1
        c["wd_usd"] += wd_usd(coin, v)
        c["wids"][v] = [(r.get("id") if isinstance(r, dict) else None), time.time()]
        say(f"📦 Пачка {amt:.2f} {coin} (~${amt*price:.0f}) едет с {v.upper()} на HTX")

def check_batch(coin):
    c = S["c"][coin]
    for v, (wid, t) in list(c["wids"].items()):
        if DRY or not wid or time.time() - t < 60:
            if not wid:
                c["wids"].pop(v, None)
            continue
        c["wids"][v] = [wid, time.time()]
        st = v_wstatus(v, coin, wid)
        if st == "cancel":
            c["wids"].pop(v, None)
            if v == "mexc" and xaddr.bad(coin):                          # следующая пачка уйдёт на запасной
                say(f"🔁 MEXC отклонил адрес HTX для {coin} — пачка уйдёт заново на запасной адрес")
            else:
                say(f"❗ {v.upper()} отменил вывод пачки {coin}, запасных адресов нет — выведи вручную на HTX")
        elif st == "done":
            c["wids"].pop(v, None)

def hedge_watch(coin, price):
    legacy = env("XS_HEDGE_PRICE", "0") if coin == env("XS_COIN", "US").upper() else "0"
    p0 = float(env(f"XS_HEDGE_PRICE_{coin}", legacy))
    if p0 <= 0:
        return
    up = price / p0 - 1
    step = int(up / 0.1) if up > 0.2 else 0
    if step > S["c"][coin]["hedge_step"]:
        S["c"][coin]["hedge_step"] = step
        say(f"⚠️ {coin} вырос на {up*100:.0f}% от цены шорта — проверь маржу фьючерса {coin}")

def loop():
    time.sleep(90)
    load()
    say(f"Магазин v3 запущен: {', '.join(COINS)} | докупка: {', '.join(v.upper() for v in VENUES)} | "
        f"${TRADE:.0f}/сделка, вход от {MIN_EDGE*100:.1f}% чистыми")
    while True:
        now = time.time()
        try:
            d = datetime.now(TZ).strftime("%Y-%m-%d")
            if S["day"] != d:
                S["day"], S["pnl_day"] = d, 0.0
            if S["pnl_day"] <= -DAILY_STOP and not S["paused"]:
                S["paused"] = True
                say(f"⛔ Дневной стоп магазина {S['pnl_day']:.2f}$ — пауза")
            for coin in COINS:
                try:
                    c, w = S["c"][coin], window(coin)
                    if w:
                        c["edge_now"], c["venue_now"] = round(w["edge"] * 100, 2), w["venue"]
                        if w["rev"] is not None:
                            c["rev_now"] = round(w["rev"] * 100, 2)
                            c["rev_hits"] += w["rev"] >= MIN_EDGE
                        hedge_watch(coin, w["price"])
                        if not S["paused"] and w["edge"] >= MIN_EDGE and now >= c["cool"] and c["sig"] != w["sig"]:
                            c["cool"], c["sig"] = now + COOL, w["sig"]
                            trade(coin, w, now)
                        batch(coin, w["price"])
                    check_batch(coin)
                except Exception as e:
                    log.exception("xshop %s", coin)
                    warn_once(f"err{coin}", f"❗ Ошибка магазина {coin}: {str(e)[:150]}", 900)
            save()
        except Exception:
            log.exception("xshop")
        time.sleep(STEP)
def ok():
    return mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN

@mm.app.route("/shop")
def shop_page():
    if not ok():
        return "forbidden", 403
    coins = {k: {"edge_now_pct": v.get("edge_now"), "best_venue_now": v.get("venue_now"),
                 "reverse_now_pct": v.get("rev_now"), "reverse_hits_10s": v.get("rev_hits", 0),
                 "trades": v["trades"], "by_venue": v.get("by_venue", {}), "wins": v["wins"],
                 "pnl": round(v["pnl"], 2), "batches": v["batches"], "batch_fees": round(v["wd_usd"], 2),
                 "net": round(v["pnl"] - v["wd_usd"], 2), "last": v["done"][-5:]} for k, v in S.get("c", {}).items()}
    return jsonify({"mode": "бумага" if DRY else "ЖИВОЙ", "venues": VENUES, "paused": S.get("paused"),
                    "pnl_day": round(S.get("pnl_day", 0), 2),
                    "net_total": round(sum(v["net"] for v in coins.values()), 2), "coins": coins})

@mm.app.route("/shop/stop")
def shop_stop():
    if not ok():
        return "forbidden", 403
    S["paused"] = True
    save()
    say("⏸ Магазин остановлен")
    return "остановлен"

@mm.app.route("/shop/start")
def shop_start():
    if not ok():
        return "forbidden", 403
    S["paused"] = False
    save()
    say("▶️ Магазин запущен")
    return "запущен"

@mm.app.route("/shop/gatetest")
def shop_gatetest():
    """Проверка Gate живыми деньгами: купить монету на $5 и вывести на HTX. Открывать ОДИН раз."""
    if not ok():
        return "forbidden", 403
    coin, usd = request.args.get("coin", "US").upper(), float(request.args.get("usd", "5"))
    out = {}
    try:
        out["buy"] = api.gt_buy_quote(coin, usd)
        time.sleep(3)
        amt = math.floor(api.gt_free(coin) * 100) / 100
        out["bought"] = amt
        out["withdraw"] = v_withdraw("gate", coin, amt)
    except Exception as e:
        out["error"] = str(e)[:300]
    return jsonify(out)

threading.Thread(target=loop, daemon=True).start()