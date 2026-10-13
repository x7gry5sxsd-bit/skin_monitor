# xshop.py — «магазин»: запас монеты на HTX, USDT на MEXC. Окно → продажа из запаса на HTX + докупка на MEXC
#   в ту же секунду; купленное на MEXC везём на HTX пачкой. Хедж — шорт фьючерса на MEXC, открывается вручную.
#   XS_DRY=1 — только считает и пишет «была бы сделка». Монету магазина убрать из XE_COINS (схемы 1)!
import os, time, json, math, threading, logging
from datetime import datetime, timezone, timedelta
from flask import jsonify, request
import mm_scanner as mm
import xpaper
import xexec_api as api

def env(k, d=""):
    return os.getenv(k, d).strip()

DRY = env("XS_DRY", "1") == "1"
COIN = env("XS_COIN", "US").upper()
TRADE = float(env("XS_TRADE_USD", "15"))         # одна продажа из запаса, $
BATCH = float(env("XS_BATCH_USD", "30"))         # везти с MEXC на HTX, когда накопилось на $
WD_USD = float(env("XS_WD_USD", "0.12"))         # комиссия вывода пачки, $ (US с MEXC ≈ 0.12)
MIN_EDGE = float(env("XS_MIN_EDGE", "0.012"))    # вход от 1.2% чистыми (уже с долей комиссии пачки)
COOL = float(env("XS_COOL_SEC", "60"))
DAILY_STOP = float(env("XS_DAILY_STOP", "2"))
HEDGE_P0 = float(env("XS_HEDGE_PRICE", "0"))     # цена, по которой открыт шорт (для тревоги по марже)
STEP = float(env("XS_STEP_SEC", "10"))
TZ = timezone(timedelta(hours=float(env("TZ_OFFSET", "8"))))
FEE = xpaper.FEE
log = logging.getLogger("xshop")
S = {}

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = %s", ("shop_dry" if DRY else "shop_live",), fetch=True)
    S.update(json.loads(r[0][0]) if r else {})
    for k, v in (("trades", 0), ("wins", 0), ("pnl", 0.0), ("wd_usd", 0.0), ("batches", 0), ("done", []),
                 ("day", ""), ("pnl_day", 0.0), ("paused", False), ("cool", 0), ("sig", None),
                 ("warn", {}), ("hedge_step", 0)):
        S.setdefault(k, v)

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
    for p, a in asks:
        take = min(left, a)
        usd += take * p
        left -= take
        if left <= 1e-12:
            return usd * (1 + FEE)
    return None

def window():
    bh, bm = xpaper.book("htx", COIN), xpaper.book("mexc", COIN)
    if not bh or not bm:
        return None
    price = bh[0][0][0]
    q = TRADE / price
    got, cost = xpaper.sell_usdt(bh[0], q), buy_cost(bm[1], q)
    if not got or not cost:
        return None
    edge = got / cost - 1 - WD_USD / BATCH
    r_got, r_cost = xpaper.sell_usdt(bm[0], q), buy_cost(bh[1], q)      # обратное: купить на HTX, продать на MEXC
    rev = r_got / r_cost - 1 - WD_USD / BATCH if r_got and r_cost else None
    return {"q": q, "got": got, "cost": cost, "edge": edge, "price": price, "sig": [bh[0][0][0], bm[1][0][0]]}

def trade(w, now):
    q = w["q"]
    if DRY:
        got, cost = w["got"], w["cost"]
    else:
        if api.hx_free(COIN) < q:
            warn_once("inv", f"Запас {COIN} на HTX кончился — жду пачку с MEXC")
            return
        if api.mx_free("USDT") < w["cost"] * 1.02:
            warn_once("usdt", f"Мало USDT на MEXC для докупки {COIN}")
            return
        oid = api.hx_sell_market(f"{COIN.lower()}usdt", q).get("data")    # 1) продали из запаса на HTX
        time.sleep(1.5)
        od = api.hx_order(oid).get("data") or {}
        got = float(od.get("field-cash-amount") or 0) - float(od.get("field-fees") or 0)
        cost = round(w["cost"] * 1.001, 2)
        try:
            api.mx_buy_quote(f"{COIN}USDT", cost)                           # 2) докупили столько же на MEXC
        except Exception as e:
            S["paused"] = True
            say(f"❗ Продали {COIN} на HTX, но докупка на MEXC не прошла: {str(e)[:150]}. "
                f"Магазин на паузе — докупи {q:.0f} {COIN} вручную и открой /shop/start")
            return
    pnl = got - cost
    S["trades"] += 1
    S["wins"] += pnl > 0
    S["pnl"] += pnl
    S["pnl_day"] += pnl
    S["done"] = (S["done"] + [[int(now), round(w["edge"] * 100, 2), round(got, 3), round(cost, 3), round(pnl, 3)]])[-30:]
    say(f"{COIN}: продали на HTX {got:.2f}$, докупили на MEXC {cost:.2f}$ → {pnl:+.2f}$ "
        f"(окно {w['edge']*100:.2f}%) | за день {S['pnl_day']:+.2f}$")

def batch(price):
    if DRY:
        return
    amt = math.floor(api.mx_free(COIN) * 100) / 100
    if amt * price < BATCH:
        return
    r = api.mx_withdraw(COIN, env(f"XE_NET_{COIN}"), env(f"XE_ADDR_{COIN}"), f"{amt:.2f}")
    S["batches"] += 1
    S["wd_usd"] += WD_USD
    S["last_wid"] = r.get("id") if isinstance(r, dict) else None
    S["last_wchk"] = time.time()
    say(f"📦 Пачка {amt:.0f} {COIN} (~${amt*price:.0f}) едет с MEXC на HTX")

def check_batch():
    wid = S.get("last_wid")
    if DRY or not wid or time.time() - S.get("last_wchk", 0) < 60:
        return
    S["last_wchk"] = time.time()
    st = api.mx_withdraw_status(COIN, wid)
    if st in (8, 9):
        S["last_wid"] = None
        say(f"❗ MEXC отменил вывод пачки {COIN} — монеты остались на MEXC. Выведи вручную на HTX")
    elif st == 7:
        S["last_wid"] = None

def hedge_watch(price):
    if HEDGE_P0 <= 0:
        return
    up = price / HEDGE_P0 - 1
    step = int(up / 0.1) if up > 0.2 else 0
    if step > S["hedge_step"]:
        S["hedge_step"] = step
        say(f"⚠️ {COIN} вырос на {up*100:.0f}% от цены шорта — проверь маржу фьючерса на MEXC")

def loop():
    time.sleep(90)
    load()
    say(f"Магазин {COIN} запущен: продажа из запаса на HTX + докупка на MEXC, ${TRADE:.0f}/сделка, "
        f"вход от {MIN_EDGE*100:.1f}%, пачка ${BATCH:.0f}")
    while True:
        now = time.time()
        try:
            d = datetime.now(TZ).strftime("%Y-%m-%d")
            if S["day"] != d:
                S["day"], S["pnl_day"] = d, 0.0
            if S["pnl_day"] <= -DAILY_STOP and not S["paused"]:
                S["paused"] = True
                say(f"⛔ Дневной стоп магазина {S['pnl_day']:.2f}$ — пауза")
            w = window()
            if w:
                S["edge_now"] = round(w["edge"] * 100, 2)
                hedge_watch(w["price"])
                if (not S["paused"] and w["edge"] >= MIN_EDGE and now >= S["cool"] and S["sig"] != w["sig"]):
                    S["cool"], S["sig"] = now + COOL, w["sig"]
                    trade(w, now)
                batch(w["price"])
            check_batch()
            save()
        except Exception as e:
            log.exception("xshop")
            warn_once("err", f"❗ Ошибка магазина: {str(e)[:150]}", 900)
        time.sleep(STEP)

def ok():
    return mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN

@mm.app.route("/shop")
def shop_page():
    if not ok():
        return "forbidden", 403
    return jsonify({"mode": "бумага" if DRY else "ЖИВОЙ", "coin": COIN, "paused": S.get("paused"),
                    "edge_now_pct": S.get("edge_now"), "trades": S.get("trades"), "wins": S.get("wins"),
                    "pnl": round(S.get("pnl", 0), 2), "pnl_day": round(S.get("pnl_day", 0), 2),
                    "batches": S.get("batches"), "batch_fees": round(S.get("wd_usd", 0), 2),
                    "net": round(S.get("pnl", 0) - S.get("wd_usd", 0), 2), "last": S.get("done", [])[-10:]})

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

threading.Thread(target=loop, daemon=True).start()