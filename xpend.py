# xpend.py v2 — «Маятник»: двусторонняя торговля HTX ⇄ KuCoin и HTX ⇄ Gate. Запас монеты лежит на HTX и на
#   биржах-партнёрах, шорт = общему запасу (держит автохедж). HTX дороже → продаём на HTX, покупаем у партнёра;
#   партнёр дороже → продаём у партнёра, покупаем на HTX. Первая нога — лимит-IOC (миражи не исполнятся).
#   ГЛАВНОЕ: перекос запаса и денег выравнивается ЧЕРЕЗ РЫНОК, а не переводами: если на HTX мало монет или у партнёра
#   мало USDT, порог обратного направления опускается до XP_REBAL_EDGE (чуть выше безубытка) — сделки сами
#   возвращают запас. Перевод по блокчейну — крайний случай: на HTX < XP_SIDE_MIN запаса дольше XP_SHIP_AFTER_H ч.
#   XP_COINS=GALA — список; XP_COINS=* — все монеты аллокатора с KuCoin или Gate. XP_PARTNERS=kucoin,gate. /pend?t=...
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
MIN_EDGE = float(env("XP_MIN_EDGE", "0.004"))     # обычный порог, чистыми после комиссий обеих ног
REBAL = float(env("XP_REBAL_EDGE", "0.0005"))     # порог «выравнивающего» направления — чуть выше безубытка
SIDE_MIN = float(env("XP_SIDE_MIN", "0.10"))      # на HTX меньше 10% запаса …
SHIP_H = float(env("XP_SHIP_AFTER_H", "6"))       # … дольше 6 ч (рынок не выровнял) → пачка партнёр → HTX
PARTNERS = [v.strip() for v in env("XP_PARTNERS", "kucoin,gate").split(",") if v.strip() in ("kucoin", "gate")]
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
    """Монеты «Маятника»: явный список или (XP_COINS=*) все активные монеты аллокатора с KuCoin или Gate."""
    X = sys.modules.get("xalloc")
    if RAW == "*":
        if not X or not X.A:
            return []
        return [c for c, v in X.A.get("coins", {}).items() if v.get("mode") == "active" and not v.get("hunt")
                and set(PARTNERS) & set(v.get("venues") or [])]
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

def has_key(v):
    return bool(getattr(api, "KC_KEY" if v == "kucoin" else "GT_KEY", ""))

def p_coin(v, coin):
    return api.kc_free(coin, "trade") if v == "kucoin" else api.gt_free(coin)

def p_usdt(v):
    return api.kc_free("USDT", "trade") if v == "kucoin" else api.gt_free("USDT")

def p_buy(v, coin, usd):
    return api.kc_buy_quote(coin, usd) if v == "kucoin" else api.gt_buy_quote(coin, usd)

def p_sell_ioc(v, coin, q, floor_px):
    """Продажа у партнёра лимитом-IOC: (продано монет, получено USDT за вычетом комиссии)."""
    if v == "gate":
        return api.gt_sell_ioc(coin, q, floor_px)
    r = api.kc_sell_ioc(coin, q, floor_px) or {}
    if not r.get("orderId"):
        return 0.0, 0.0
    time.sleep(1.2)
    od = api.kc_order(r["orderId"])
    return float(od.get("dealSize") or 0), float(od.get("dealFunds") or 0) - float(od.get("fee") or 0)

def sides(coin, px):
    """Запас: (на HTX, {партнёр: монет}, всего в $)."""
    h, part = api.hx_free(coin), {}
    for v in PARTNERS:
        if has_key(v):
            try:
                part[v] = p_coin(v, coin) + (api.kc_free(coin, "main") if v == "kucoin" else 0)
            except Exception:
                part[v] = 0.0
    return h, part, (h + sum(part.values())) * px

def edges(coin, v):
    bh, bk = xpaper.book("htx", coin), xpaper.book(v, coin)
    if not bh or not bk or not bh[0] or not bk[0] or not bh[1] or not bk[1]:
        return None
    px = (bh[0][0][0] + bk[0][0][0]) / 2
    q = TRADE / px
    ga, ca = xpaper.sell_usdt(bh[0], q, "htx"), buy_cost(bk[1], q, v)       # A: HTX дороже
    gb, cb = xpaper.sell_usdt(bk[0], q, v), buy_cost(bh[1], q, "htx")       # B: партнёр дороже
    return {"v": v, "q": q, "px": px, "ask_htx": bh[1][0][0],
            "a": (ga / ca - 1) if ga and ca else None, "ca": ca,
            "b": (gb / cb - 1) if gb and cb else None, "cb": cb}

def thresholds(e, h, tot):
    """Пороги направлений. Перекос выравниваем рынком: «нужное» направление — чуть выше безубытка."""
    share_h = h * e["px"] / tot if tot else 0.5
    ta = tb = MIN_EDGE
    if share_h < 0.5 or p_usdt(e["v"]) < 2 * TRADE:    # на HTX мало монет или у партнёра мало USDT → тянем «B»
        tb = REBAL
    if share_h > 0.5 and api.hx_free("USDT") < 2 * TRADE:   # на HTX мало USDT, а монет много → тянем «A»
        ta = REBAL
    if share_h < SIDE_MIN:
        ta = MIN_EDGE + 0.004                          # HTX почти пуст — дальше его не опустошаем без жирного окна
    return ta, tb

def record(coin, d, v, got, cost, e):
    c = P["c"][coin]
    pnl = got - cost
    c["trades"] += 1
    c["pnl"] += pnl
    c[d] += 1
    by = c.setdefault("by", {})
    by[v] = by.get(v, 0) + 1
    P["pnl_day"] += pnl
    P["acts"] = (P["acts"] + [time.time()])[-100:]
    c["done"] = (c["done"] + [[int(time.time()), d, round(e * 100, 2), round(got, 3), round(cost, 3),
                               round(pnl, 3), v]])[-20:]
    sell, buy = ("HTX", v.upper()) if d == "a" else (v.upper(), "HTX")
    say(f"Маятник {coin}: продал на {sell} {got:.2f}$, купил на {buy} {cost:.2f}$ → {pnl:+.2f}$ "
        f"(окно {e*100:.2f}%) | за день {P['pnl_day']:+.2f}$")

def trade_a(coin, e, thr):
    """HTX дороже: продаём на HTX (IOC не дешевле безубытка), сразу покупаем столько же у партнёра."""
    v = e["v"]
    q = min(e["q"], math.floor(api.hx_free(coin) * 100) / 100)
    if q * e["px"] < 5:
        return False
    ca = e["ca"] * q / e["q"]
    if p_usdt(v) < ca * 1.02:
        return False
    floor_px = ca / q * (1 + thr) / (1 - xpaper.fee("htx"))
    oid = api.hx_sell_ioc(f"{coin.lower()}usdt", q, floor_px).get("data")
    time.sleep(1.2)
    od = api.hx_order(oid).get("data") or {}
    f = float(od.get("field-amount") or 0)
    if f <= 0:
        return False                                   # мираж — ничего не продано, ничего не покупаем
    got = float(od.get("field-cash-amount") or 0) - float(od.get("field-fees") or 0)
    cost = round(ca * f / q * 1.001, 2)
    p_buy(v, coin, cost)
    record(coin, "a", v, got, cost * (1 + xpaper.fee(v)), e["a"])   # комиссия покупки удерживается монетами
    return True

def trade_b(coin, e, thr):
    """Партнёр дороже: продаём у партнёра (IOC не дешевле безубытка), сразу покупаем столько же на HTX."""
    v = e["v"]
    q = min(e["q"], math.floor(p_coin(v, coin) * 100) / 100)
    if q * e["px"] < 5:
        return False
    cb = e["cb"] * q / e["q"]
    if api.hx_free("USDT") < cb * 1.02:
        return False
    floor_px = cb / q * (1 + thr) / (1 - xpaper.fee(v))
    f, got = p_sell_ioc(v, coin, q, floor_px)
    if f <= 0:
        return False                                   # мираж у партнёра — ничего не продано
    oid = api.hx_buy_ioc(f"{coin.lower()}usdt", f, e["ask_htx"] * 1.01).get("data")
    time.sleep(1.2)
    hd = api.hx_order(oid).get("data") or {}
    cost = float(hd.get("field-cash-amount") or 0)
    cost += float(hd.get("field-fees") or 0) * e["ask_htx"]   # HTX берёт комиссию покупки монетами
    record(coin, "b", v, got, cost, e["b"])
    return True

def ship(coin, px, part):
    """Крайний случай: рынок не выровнял запас за XP_SHIP_AFTER_H ч — одна пачка с партнёра, где монет больше всего."""
    c = P["c"][coin]
    if c.get("reb") and time.time() - c["reb"] < 3 * 3600:
        return
    v = max(part, key=lambda x: part[x]) if part else None
    if not v or part[v] * px < 30:
        return
    h = api.hx_free(coin)
    amt = math.floor((part[v] - (h + part[v]) / 2) * 100) / 100   # выравниваем до половины
    if amt * px < 20:
        return
    X = sys.modules.get("xalloc")
    cfg = (lambda k: X.cfg(coin, k)) if X and X.A else (lambda k: None)
    if v == "kucoin":
        chain = cfg("knet") or env(f"XE_KNET_{coin}", "eth")
        addr = env(f"XE_KADDR_{coin}") or env("XE_KADDR")
        move = math.floor((amt - api.kc_free(coin, "main")) * 100) / 100
        if move > 0:
            api.kc_transfer(coin, f"{move:.2f}", "trade", "main")
            time.sleep(1)
        api.kc("POST", "/api/v1/withdrawals", body={"currency": coin, "address": addr, "amount": f"{amt:.2f}",
                                                     "chain": chain, "feeDeductType": "INTERNAL"})
    else:
        chain = env(f"XE_GNET_{coin}") or cfg("gnet") or "ETH"
        addr = env(f"XE_GADDR_{coin}") or env(f"XE_GADDR_{chain}") or env("XE_GADDR") or env("XE_KADDR")
        api.gt_withdraw(coin, chain, addr, f"{amt:.2f}")
    c["reb"] = time.time()
    say(f"Маятник {coin}: рынок {SHIP_H:.0f} ч не выровнял запас — пачка {amt:.2f} {coin} (~${amt*px:.0f}) едет "
        f"с {v.upper()} на HTX")
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
        c = P["c"].setdefault(coin, {"trades": 0, "pnl": 0.0, "a": 0, "b": 0, "done": [], "cool": 0, "reb": None})
        bad = P.setdefault("bad", {})                  # «партнёр+направление», отключённые после оборванной сделки
        es = [e for e in (edges(coin, v) for v in PARTNERS if has_key(v)) if e]
        if not es:
            continue
        h, part, tot = sides(coin, es[0]["px"])
        c["now"] = {e["v"]: {"a": round(e["a"] * 100, 2) if e["a"] is not None else None,
                             "b": round(e["b"] * 100, 2) if e["b"] is not None else None} for e in es}
        if tot and h * es[0]["px"] / tot < SIDE_MIN:   # HTX почти пуст: сколько уже так
            c.setdefault("low_since", now)
            if now - c["low_since"] >= SHIP_H * 3600:
                try:
                    ship(coin, es[0]["px"], part)
                except Exception as ex:
                    c["reb"] = time.time()
                    say(f"❗ Маятник {coin}: пачка на HTX не ушла — {str(ex)[:150]}. Повтор через 3 ч")
        else:
            c.pop("low_since", None)
        if now < c["cool"]:
            continue
        best = []                                      # возможности по всем партнёрам: (запас над порогом, …)
        for e in es:
            ta, tb = thresholds(e, h, tot)
            if e["a"] is not None and e["a"] >= ta and bad.get(e["v"] + "a", 0) < now:
                best.append((e["a"] - ta, "a", e, ta))
            if e["b"] is not None and e["b"] >= tb and bad.get(e["v"] + "b", 0) < now:
                best.append((e["b"] - tb, "b", e, tb))
        for _, kind, e, thr in sorted(best, key=lambda x: -x[0]):
            key = e["v"] + kind                        # «партнёр+направление»: сломана одна сторона — вторая работает
            try:
                done = (trade_a if kind == "a" else trade_b)(coin, e, thr)
            except Exception as ex:                    # вторая нога не прошла: НЕ повторяем каждые 5 с, а отключаем сторону
                fails = P.setdefault("fails", {})
                fails[key] = fails.get(key, 0) + 1
                hours = (1, 6, 24)[min(fails[key], 3) - 1]   # 1-й сбой — 1 ч, 2-й — 6 ч, 3-й и дальше — 24 ч
                bad[key] = now + hours * 3600
                c["cool"] = now + 900
                way = f"HTX → {e['v'].upper()}" if kind == "a" else f"{e['v'].upper()} → HTX"
                say(f"❗ Маятник {coin}: сделка {way} оборвалась на второй ноге: {str(ex)[:140]}. Первая нога уже "
                    f"исполнена (хедж подстроится сам). Это направление отключено на {hours} ч — проверь ключ биржи "
                    f"(права на торговлю)")
                break
            if done:
                P.setdefault("fails", {})[key] = 0     # чистая сделка этой же стороны — счётчик сбоев обнуляем
                c["cool"] = now + COOL
                break

def loop():
    time.sleep(120)
    load()
    if not RAW:
        return
    who = "все монеты аллокатора" if RAW == "*" else ", ".join(pend_coins())
    say(f"Маятник v2 запущен: {who} | HTX ⇄ {' / '.join(v.upper() for v in PARTNERS if has_key(v))}, вход от "
        f"{MIN_EDGE*100:.1f}%, выравнивание рынком от {REBAL*100:.2f}%, ${TRADE:.0f}/сделка")
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
            e = next(x for x in (edges(coin, v) for v in PARTNERS if has_key(v)) if x)
            h, part, tot = sides(coin, e["px"])
        except Exception:
            h = part = None
        out[coin] = {"trades": c["trades"], "htx_to_partner": c["a"], "partner_to_htx": c["b"], "by": c.get("by"),
                     "pnl": round(c["pnl"], 2), "edge_now_pct": c.get("now"), "on_htx": h, "on_partners": part,
                     "last": c["done"][-5:]}
    return jsonify({"on": ON, "paused": P.get("paused"), "pnl_day": round(P.get("pnl_day", 0), 2),
                    "min_edge_pct": MIN_EDGE * 100, "rebal_edge_pct": REBAL * 100, "partners": PARTNERS, "coins": out})

threading.Thread(target=loop, daemon=True).start()
