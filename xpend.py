# xpend.py — «Маятник»: двусторонний магазин HTX ⇄ KuCoin. Запас монеты лежит на ОБЕИХ биржах, шорт = общему запасу
#   (его держит автохедж). HTX дороже → продаём на HTX, покупаем на KuCoin (запас перетекает на KuCoin).
#   KuCoin дороже → продаём на KuCoin, покупаем на HTX (запас перетекает обратно). Переводов почти нет: только если на
#   HTX осталось меньше XP_SIDE_MIN запаса — одна пачка KuCoin → HTX. Первая нога — лимит-IOC (миражи не исполнятся),
#   вторая — сразу следом. XP_COINS=GALA — список монет; XP_COINS=* — ВСЕ монеты аллокатора с KuCoin (новые монеты
#   попадают в «Маятник» сами). Монету «на выходе» ведёт магазин. Монеты «Маятника» магазин не трогает. /pend?t=...
import os, sys, time, json, math, threading, logging
from datetime import datetime, timezone, timedelta
from flask import jsonify, request
import mm_scanner as mm
import xpaper
import xexec_api as api

def env(k, d=""):
    return os.getenv(k, d).strip()

ON = env("XP_ON", "1") == "1"
RAW = env("XP_COINS", "")
TRADE = float(env("XP_TRADE_USD", "15"))
MIN_EDGE = float(env("XP_MIN_EDGE", "0.004"))     # чистыми после комиссий обеих ног (пачек нет)
SIDE_MIN = float(env("XP_SIDE_MIN", "0.25"))      # на HTX меньше 25% запаса → пачка с KuCoin
MAX_PER_H = int(env("XP_MAX_PER_HOUR", "30"))
DAILY_STOP = float(env("XP_DAILY_STOP", "2"))
COOL = 20
TZ = timezone(timedelta(hours=float(env("TZ_OFFSET", "8"))))
log = logging.getLogger("xpend")
P = {}

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'pend_v1'", fetch=True)
    P.update(json.loads(r[0][0]) if r else {})
    for k, v in (("c", {}), ("day", ""), ("pnl_day", 0.0), ("paused", False), ("acts", [])):
        P.setdefault(k, v)
    P["loaded"] = True

def pend_coins():
    """Монеты «Маятника»: явный список или (XP_COINS=*) все активные монеты аллокатора с биржей KuCoin."""
    X = sys.modules.get("xalloc")
    if RAW == "*":
        if not X or not X.A:
            return []
        return [c for c, v in X.A.get("coins", {}).items()
                if v.get("mode") == "active" and "kucoin" in (v.get("venues") or [])]
    out = [c.strip().upper() for c in RAW.split(",") if c.strip()]
    return [c for c in out if not (X and X.A and X.cfg(c, "mode") == "exit")]   # на выходе — ведёт магазин

def save():
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('pend_v1', %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(P),))

def say(t):
    mm.tg("🔁 " + t)

def buy_cost(asks, qty, ex):
    left, usd = qty, 0.0
    for p, a in asks:
        take = min(left, a)
        usd += take * p
        left -= take
        if left <= 1e-12:
            return usd * (1 + xpaper.fee(ex))
    return None

def kc_coin(coin):
    return api.kc_free(coin, "trade")

def sides(coin, px):
    h, k = api.hx_free(coin), kc_coin(coin) + api.kc_free(coin, "main")
    return h, k, (h + k) * px

def edges(coin):
    bh, bk = xpaper.book("htx", coin), xpaper.book("kucoin", coin)
    if not bh or not bk or not bh[0] or not bk[0]:
        return None
    px = (bh[0][0][0] + bk[0][0][0]) / 2
    q = TRADE / px
    ga, ca = xpaper.sell_usdt(bh[0], q, "htx"), buy_cost(bk[1], q, "kucoin")    # A: HTX дороже
    gb, cb = xpaper.sell_usdt(bk[0], q, "kucoin"), buy_cost(bh[1], q, "htx")    # B: KuCoin дороже
    return {"q": q, "px": px, "ask_htx": bh[1][0][0],
            "a": (ga / ca - 1) if ga and ca else None, "ca": ca,
            "b": (gb / cb - 1) if gb and cb else None, "cb": cb}

def record(coin, d, got, cost, e):
    c = P["c"][coin]
    pnl = got - cost
    c["trades"] += 1
    c["pnl"] += pnl
    c[d] += 1
    P["pnl_day"] += pnl
    P["acts"] = (P["acts"] + [time.time()])[-100:]
    c["done"] = (c["done"] + [[int(time.time()), d, round(e * 100, 2), round(got, 3), round(cost, 3),
                               round(pnl, 3)]])[-20:]
    where = "HTX → KuCoin" if d == "a" else "KuCoin → HTX"
    say(f"Маятник {coin}: продал на {'HTX' if d == 'a' else 'KUCOIN'} {got:.2f}$, купил на "
        f"{'KUCOIN' if d == 'a' else 'HTX'} {cost:.2f}$ → {pnl:+.2f}$ (окно {e*100:.2f}%, {where}) | "
        f"за день {P['pnl_day']:+.2f}$")

def trade_a(coin, e):
    """HTX дороже: продаём на HTX (IOC не дешевле безубытка), сразу покупаем столько же на KuCoin."""
    q = min(e["q"], math.floor(api.hx_free(coin) * 100) / 100)      # запаса меньше сделки — продаём сколько есть
    if q * e["px"] < 5:
        return False
    ca = e["ca"] * q / e["q"]
    if api.kc_free("USDT", "trade") < ca * 1.02:
        return False
    floor_px = ca / q * (1 + MIN_EDGE) / (1 - xpaper.fee("htx"))
    oid = api.hx_sell_ioc(f"{coin.lower()}usdt", q, floor_px).get("data")
    time.sleep(1.2)
    od = api.hx_order(oid).get("data") or {}
    f = float(od.get("field-amount") or 0)
    if f <= 0:
        return False                                   # мираж — ничего не продано, ничего не покупаем
    got = float(od.get("field-cash-amount") or 0) - float(od.get("field-fees") or 0)
    cost = round(ca * f / q * 1.001, 2)
    api.kc_buy_quote(coin, cost)
    record(coin, "a", got, cost, e["a"])
    return True

def trade_b(coin, e):
    """KuCoin дороже: продаём на KuCoin (IOC не дешевле безубытка), сразу покупаем столько же на HTX."""
    q = min(e["q"], math.floor(kc_coin(coin) * 100) / 100)
    if q * e["px"] < 5:
        return False
    cb = e["cb"] * q / e["q"]
    if api.hx_free("USDT") < cb * 1.02:
        return False
    floor_px = cb / q * (1 + MIN_EDGE) / (1 - xpaper.fee("kucoin"))
    r = api.kc_sell_ioc(coin, q, floor_px) or {}
    if not r.get("orderId"):
        return False
    time.sleep(1.2)
    od = api.kc_order(r["orderId"])
    f = float(od.get("dealSize") or 0)
    if f <= 0:
        return False                                   # мираж на KuCoin — ничего не продано
    got = float(od.get("dealFunds") or 0) - float(od.get("fee") or 0)
    oid = api.hx_buy_ioc(f"{coin.lower()}usdt", f, e["ask_htx"] * 1.01).get("data")
    time.sleep(1.2)
    hd = api.hx_order(oid).get("data") or {}
    cost = float(hd.get("field-cash-amount") or 0)
    record(coin, "b", got, cost, e["b"])
    return True

def rebalance(coin, px):
    """На HTX мало запаса, а на KuCoin много — одна пачка KuCoin → HTX (на универсальный адрес XE_KADDR)."""
    c = P["c"][coin]
    if c.get("reb") and time.time() - c["reb"] < 3600:
        return                                         # прошлая пачка ещё в пути
    h, k, tot = sides(coin, px)
    if tot <= 0 or h * px >= SIDE_MIN * tot or k * px < 30:
        return
    amt = math.floor((k - (h + k) / 2) * 100) / 100   # выравниваем до половины
    if amt * px < 20:
        return
    X = sys.modules.get("xalloc")
    chain = (X.cfg(coin, "knet") if X and X.A else None) or env(f"XE_KNET_{coin}", "eth")
    addr = env(f"XE_KADDR_{coin}") or env("XE_KADDR")
    move = math.floor((amt - api.kc_free(coin, "main")) * 100) / 100
    if move > 0:
        api.kc_transfer(coin, f"{move:.2f}", "trade", "main")
        time.sleep(1)
    api.kc("POST", "/api/v1/withdrawals", body={"currency": coin, "address": addr, "amount": f"{amt:.2f}",
                                                 "chain": chain, "feeDeductType": "INTERNAL"})
    c["reb"] = time.time()
    say(f"Маятник {coin}: на HTX осталось мало запаса — пачка {amt:.2f} {coin} (~${amt*px:.0f}) едет с KuCoin на HTX")

def step(now):
    d = datetime.now(TZ).strftime("%Y-%m-%d")
    if P["day"] != d:
        P["day"], P["pnl_day"] = d, 0.0
        if P.get("stop_day"):
            P["paused"], P["stop_day"] = False, None
    if P["pnl_day"] <= -DAILY_STOP and not P["paused"]:
        P["paused"], P["stop_day"] = True, d
        say(f"⛔ Маятник: дневной стоп {P['pnl_day']:.2f}$ — пауза до завтра")
    if P["paused"] or len([t for t in P["acts"] if now - t < 3600]) >= MAX_PER_H:
        return
    for coin in pend_coins():
        c = P["c"].setdefault(coin, {"trades": 0, "pnl": 0.0, "a": 0, "b": 0, "done": [], "cool": 0, "reb": None,
                                     "fees": 0.0})
        e = edges(coin)
        if not e:
            continue
        c["now"] = {"a": round(e["a"] * 100, 2) if e["a"] is not None else None,
                    "b": round(e["b"] * 100, 2) if e["b"] is not None else None}
        try:
            rebalance(coin, e["px"])
        except Exception as ex:
            c["reb"] = time.time()
            say(f"❗ Маятник {coin}: пачка KuCoin → HTX не ушла — {str(ex)[:150]}. Повтор через час")
        if now < c["cool"]:
            continue
        if e["a"] is not None and e["a"] >= MIN_EDGE and trade_a(coin, e):
            c["cool"] = now + COOL
        elif e["b"] is not None and e["b"] >= MIN_EDGE and trade_b(coin, e):
            c["cool"] = now + COOL

def loop():
    time.sleep(120)
    load()
    if not RAW:
        return
    who = "все монеты с KuCoin" if RAW == "*" else ", ".join(pend_coins())
    say(f"Маятник запущен: {who} | HTX ⇄ KuCoin, вход от {MIN_EDGE*100:.1f}% чистыми, ${TRADE:.0f}/сделка")
    n = 0
    while True:
        now = time.time()
        try:
            if ON:
                step(now)
            n += 1
            if n % 6 == 0:
                save()
        except Exception as e:
            log.exception("xpend")
            if now - P.get("err_t", 0) > 900:
                P["err_t"] = now
                say(f"❗ Маятник: {str(e)[:200]}")
        time.sleep(5)

@mm.app.route("/pend")
def pend_page():
    if not (mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN):
        return "forbidden", 403
    out = {}
    for coin, c in P.get("c", {}).items():
        try:
            e = edges(coin)
            h, k, tot = sides(coin, e["px"]) if e else (None, None, None)
        except Exception:
            h = k = tot = None
        out[coin] = {"trades": c["trades"], "htx_to_kucoin": c["a"], "kucoin_to_htx": c["b"],
                     "pnl": round(c["pnl"], 2), "edge_now_pct": c.get("now"), "on_htx": h, "on_kucoin": k,
                     "last": c["done"][-5:]}
    return jsonify({"on": ON, "paused": P.get("paused"), "pnl_day": round(P.get("pnl_day", 0), 2),
                    "min_edge_pct": MIN_EDGE * 100, "coins": out})

threading.Thread(target=loop, daemon=True).start()
