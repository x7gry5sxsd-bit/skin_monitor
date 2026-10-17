# xshop.py v6 — «магазин»: запас монет на HTX, USDT на MEXC, Gate и KuCoin. Окно → продажа из запаса на HTX + докупка
#   на бирже, где сейчас дешевле, в ту же секунду. Купленное везём на HTX пачкой с той же биржи.
#   v6: список монет и их настройки ведёт аллокатор (xalloc) — монеты появляются и уходят без правок в Render.
#   Монета «на выходе»: только продаём из запаса в окна (без докупки), через XA_EXIT_H ч — остаток порциями.
#   XS_COINS — стартовый список; XS_VENUES / XS_VENUES_<МОНЕТА> — где докупать. Хедж — автохедж (xahedge).
import os, sys, time, json, math, threading, logging
from datetime import datetime, timezone, timedelta
from flask import jsonify, request
import mm_scanner as mm
import xpaper
import xaddr
import xfer
import xtime
import xexec_api as api

def env(k, d=""):
    return os.getenv(k, d).strip()

DRY = env("XS_DRY", "1") == "1"
BASE = [c.strip().upper() for c in env("XS_COINS", env("XS_COIN", "US")).split(",") if c.strip()]
COINS = list(BASE)                                # живой список: его обновляет аллокатор
VENUES = [v.strip() for v in env("XS_VENUES", "mexc").split(",") if v.strip() in ("mexc", "gate", "kucoin")]
TRADE = float(env("XS_TRADE_USD", "15"))
MIN_EDGE = float(env("XS_MIN_EDGE", "0.008"))     # чистыми, уже за вычетом доли комиссии пачки
COOL = float(env("XS_COOL_SEC", "60"))
DAILY_STOP = float(env("XS_DAILY_STOP", "3"))
STEP = float(env("XS_STEP_SEC", "10"))
TZ = timezone(timedelta(hours=float(env("TZ_OFFSET", "8"))))
WD = {"mexc": {"US": 0.12, "ETHW": 0.03, "SXT": 0.21, "MAGIC": 0.15, "BLUE": 0.03, "DOS": 0.22, "M": 0.17},
      "gate": {"US": 0.06, "BLUE": 0.05, "MAGIC": 0.05}, "kucoin": {"US": 0.26}}
GNET = {"US": "SUI", "BLUE": "SUI", "MAGIC": "ARBEVM"}
KNET = {"US": "sui", "BLUE": "sui", "MAGIC": "arbitrum"}
FEE = xpaper.FEE
log = logging.getLogger("xshop")
S = {}

def acfg(coin, key):
    X = sys.modules.get("xalloc")
    return X.cfg(coin, key) if X and X.A else None

def exiting(coin):
    return acfg(coin, "mode") == "exit"

def sync_coins():
    """Список монет — от аллокатора (если он включён), иначе из XS_COINS."""
    X = sys.modules.get("xalloc")
    cur = X.coins() if X and X.ON and X.A.get("coins") else BASE
    COINS[:] = cur
    for c in cur:
        S["c"].setdefault(c, blank())
        for k, v in blank().items():
            S["c"][c].setdefault(k, v)

def batch_usd(coin):
    return float(acfg(coin, "batch") or env(f"XS_BATCH_USD_{coin}", env("XS_BATCH_USD", "30")))

def venues_for(coin):
    own = acfg(coin, "venues") or [v.strip() for v in env(f"XS_VENUES_{coin}").split(",")
                                   if v.strip() in ("mexc", "gate", "kucoin")]
    return own or VENUES

def wd_usd(coin, v):
    if v == "mexc" and acfg(coin, "wd") is not None:
        return float(acfg(coin, "wd"))
    if v != "mexc" and acfg(coin, f"wd_{v}") is not None:                # комиссию KuCoin/Gate узнал аллокатор
        return float(acfg(coin, f"wd_{v}"))
    key = {"mexc": "XS_WD_USD_", "gate": "XS_GWD_USD_", "kucoin": "XS_KWD_USD_"}[v] + coin
    return float(env(key, str(WD[v].get(coin, 0.3))))

def v_free(v, cur):
    if v == "kucoin":
        return api.kc_free(cur, "trade")
    return api.mx_free(cur) if v == "mexc" else api.gt_free(cur)

def v_buy(v, coin, usd):
    if v == "kucoin":
        return api.kc_buy_quote(coin, usd)
    return api.mx_buy_quote(f"{coin}USDT", usd) if v == "mexc" else api.gt_buy_quote(coin, usd)

def v_withdraw(v, coin, amt):
    """Вывод пачки на HTX; возвращает (ответ биржи, адрес)."""
    if v == "mexc":
        addr = xaddr.get(coin)                                              # пул адресов по кругу
        net = env(f"XE_NET_{coin}") or acfg(coin, "net")
        return api.mx_withdraw(coin, net, addr, f"{amt:.2f}"), addr
    if v == "kucoin":                                                      # KuCoin: выводит только с основного счёта
        addr = env(f"XE_KADDR_{coin}") or env("XE_KADDR") or xaddr.get(coin)   # XE_KADDR — один адрес для всех монет
        move = math.floor((amt - api.kc_free(coin, "main")) * 100) / 100     # переносим только недостающее
        if move > 0:                                                       # (после неудачного вывода монеты уже там)
            api.kc_transfer(coin, f"{move:.2f}", "trade", "main")
            time.sleep(1)
        return api.kc("POST", "/api/v1/withdrawals", body={
            "currency": coin, "address": addr, "amount": f"{amt:.2f}",
            "chain": env(f"XE_KNET_{coin}") or acfg(coin, "knet") or KNET.get(coin, ""),
            "feeDeductType": "INTERNAL"}), addr                               # комиссия — из суммы вывода
    addr = env(f"XE_GADDR_{coin}", env(f"XE_ADDR_{coin}"))                # Gate — свой доверенный адрес
    return api.gt_withdraw(coin, env(f"XE_GNET_{coin}", GNET.get(coin, "")), addr, f"{amt:.2f}"), addr

def path_ok(coin, v):
    """Открыт ли путь v → HTX для пачки. Если узнать не удалось — считаем открытым."""
    try:
        return xfer.route(v, "htx", coin, 1.0).get("state") != "closed"
    except Exception:
        return True

def htx_credited(coin, sent, amt):
    """Когда пачка зачислилась на HTX: пополнение после отправки на сумму не меньше 90% пачки (время или None)."""
    try:
        d = api.hx("GET", "/v1/query/deposit-withdraw", {"currency": coin.lower(), "type": "deposit", "size": 10})
    except Exception:
        return None
    for x in d.get("data") or []:
        if x.get("state") in ("confirmed", "safe") and float(x.get("amount") or 0) >= 0.9 * amt and \
                (x.get("created-at") or 0) / 1000 >= sent - 120:
            return min(time.time(), (x.get("updated-at") or 0) / 1000 or time.time())
    return None

def v_wstatus(v, coin, wid):
    if v == "kucoin":
        st = api.kc_withdraw_status(coin, wid)
        return "done" if st == "SUCCESS" else ("cancel" if st == "FAILURE" else None)
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

def buy_cost(asks, qty, ex=None):
    left, usd = qty, 0.0
    for pr, a in asks:
        take = min(left, a)
        usd += take * pr
        left -= take
        if left <= 1e-12:
            return usd * (1 + xpaper.fee(ex))
    return None

def window(coin):
    bh = xpaper.book("htx", coin)
    if not bh:
        return None
    price = bh[0][0][0]
    q = TRADE / price
    got = xpaper.sell_usdt(bh[0], q, "htx")
    rbuy = buy_cost(bh[1], q, "htx")                   # обратное: купить на HTX
    opts, rev = [], None
    for v in venues_for(coin):
        if v == "mexc" and not xaddr.status(coin)["good"]:   # с MEXC эту монету не отвезти — там не докупаем
            continue
        bv = xpaper.book(v, coin)
        if not bv:
            continue
        cost = buy_cost(bv[1], q, v)
        if got and cost:
            pen = float(env("XS_PENALTY_MEXC", "0.003")) if v == "mexc" and "kucoin" in venues_for(coin) else 0
            opts.append({"edge": got / cost - 1 - wd_usd(coin, v) / batch_usd(coin) - pen, "venue": v, "cost": cost})
        rs = xpaper.sell_usdt(bv[0], q, v)             # обратное: продать на MEXC/Gate
        if rs and rbuy:
            r = rs / rbuy - 1
            rev = r if rev is None else max(rev, r)
    if not opts:
        return None
    opts.sort(key=lambda o: -o["edge"])
    b = opts[0]
    return {"q": q, "got": got, "cost": b["cost"], "edge": b["edge"], "venue": b["venue"], "price": price,
            "opts": opts, "rev": rev, "sig": [bh[0][0][0], b["cost"]]}

def trade(coin, w, now, sell_only=False):
    c, q, v = S["c"][coin], w["q"], w["venue"]
    if DRY:
        got, cost = w["got"], w["cost"]
    elif sell_only:                                    # выход из монеты: только продаём из запаса, без докупки
        have = math.floor(api.hx_free(coin) * 100) / 100
        q = min(q, have)
        if q * w["price"] < 2:
            return
        floor_px = w["cost"] / w["q"] * 0.985                       # не ниже цены MEXC −1,5%: в пустой стакан не сливаем
        oid = api.hx_sell_ioc(f"{coin.lower()}usdt", q, floor_px).get("data")
        time.sleep(1.5)
        od = api.hx_order(oid).get("data") or {}
        filled = float(od.get("field-amount") or 0)
        if filled <= 0:
            return
        got = float(od.get("field-cash-amount") or 0) - float(od.get("field-fees") or 0)
        cost = w["cost"] * filled / w["q"]
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
            warn_once(f"usdt{coin}", f"Мало USDT для докупки {coin} на {', '.join(venues_for(coin)).upper()}")
            return
        v, w = pick["venue"], {**w, "cost": pick["cost"], "edge": pick["edge"], "venue": pick["venue"]}
        # 1) продаём на HTX лимитом-IOC не дешевле безубытка: если в стакане «мираж», сделка просто не исполнится
        need = 1 + MIN_EDGE + wd_usd(coin, v) / batch_usd(coin)
        floor_px = w["cost"] / q * need / (1 - xpaper.fee("htx"))
        oid = api.hx_sell_ioc(f"{coin.lower()}usdt", q, floor_px).get("data")
        time.sleep(1.5)
        od = api.hx_order(oid).get("data") or {}
        filled = float(od.get("field-amount") or 0)
        if filled <= 0:
            warn_once(f"mirage{coin}", f"{coin}: окно {w['edge']*100:.1f}% оказалось «миражом» — по нужной цене на HTX "
                                       f"никто не купил, сделка не исполнена (денег не потеряли)", 1800)
            return
        got = float(od.get("field-cash-amount") or 0) - float(od.get("field-fees") or 0)
        cost = round(w["cost"] * filled / q * 1.001, 2)                     # докупаем ровно проданное
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
    if sell_only:
        say(f"{coin} (выход): продали из запаса на HTX {got:.2f}$, против MEXC это {pnl:+.2f}$ "
            f"(окно {w['edge']*100:.2f}%)")
    else:
        say(f"{coin}: продали на HTX {got:.2f}$, докупили на {v.upper()} {cost:.2f}$ → {pnl:+.2f}$ "
            f"(окно {w['edge']*100:.2f}%) | за день {S['pnl_day']:+.2f}$")

def batch(coin, price):
    if DRY:
        return
    c = S["c"][coin]
    for v in venues_for(coin):
        if c["wids"].get(v):
            continue                                   # предыдущая пачка с этой биржи ещё в пути
        have = v_free(v, coin) + (api.kc_free(coin, "main") if v == "kucoin" else 0)   # KuCoin: торговый + основной
        amt = math.floor(have * 100) / 100
        low = api.hx_free(coin) * price < 2 * TRADE        # на HTX осталось меньше двух сделок — везём раньше
        need = batch_usd(coin) * (float(env("XS_MEXC_BATCH_X", "2")) if v == "mexc" and "kucoin" in venues_for(coin) else 1)
        if amt * price < need and not (low and amt * price >= 20):
            continue
        if v == "mexc" and not xaddr.status(coin)["good"]:               # все адреса пула MEXC отклонил
            X = sys.modules.get("xalloc")
            vs = acfg(coin, "venues") or []
            if X and "kucoin" in vs:                                     # есть KuCoin — MEXC для монеты выключаем
                X.A["coins"][coin]["venues"] = [x for x in vs if x != "mexc"]
                X.save()
                say(f"🔁 {coin}: MEXC не может отправить на HTX — докупка {coin} теперь только через KuCoin. "
                    f"Остаток {amt:.2f} {coin} на MEXC выведи вручную на HTX")
                continue
            warn_once(f"noaddr{coin}", f"⛔ {coin}: MEXC отклонил все адреса HTX — пачка ждёт. Добавь новый адрес HTX "
                                       f"в белый список MEXC для {coin} и в XE_ADDRS_{xaddr._key(coin)}", 6 * 3600)
            continue
        if not path_ok(coin, v):                                          # ввод на HTX или вывод закрыт
            warn_once(f"path{coin}{v}", f"🔒 {coin}: путь {v.upper()} → HTX закрыт — пачка ждёт открытия")
            continue
        if time.time() < c.setdefault("wd_block", {}).get(v, 0):
            continue                                                       # вывод недавно не прошёл — ждём
        try:
            r, addr = v_withdraw(v, coin, amt)
        except Exception as e:
            c["wd_block"][v] = time.time() + 1800
            say(f"❗ Пачка {coin} с {v.upper()} не ушла: {str(e)[:150]}. Повтор через 30 мин — проверь адрес "
                f"HTX в белом списке {v.upper()} для {coin}")
            continue
        c["batches"] += 1
        c["wd_usd"] += wd_usd(coin, v)
        wid = (r.get("id") or r.get("withdrawalId")) if isinstance(r, dict) else None
        c["wids"][v] = [wid, time.time(), addr, time.time(), amt]
        say(f"📦 Пачка {amt:.2f} {coin} (~${amt*price:.0f}) едет с {v.upper()} на HTX")

def check_batch(coin):
    c = S["c"][coin]
    for v, w in list(c["wids"].items()):
        wid, t, addr = w[0], w[1], (w[2] if len(w) > 2 else None)
        sent, amt, onchain = (w[3] if len(w) > 3 else t), (w[4] if len(w) > 4 else 0), len(w) > 5
        if DRY or not wid or time.time() - t < 60:
            if not wid:
                c["wids"].pop(v, None)
            continue
        c["wids"][v] = [wid, time.time(), addr, sent, amt] + (["chain"] if onchain else [])
        if onchain:                                                      # биржа отправила — ждём зачисления на HTX
            done_at = htx_credited(coin, sent, amt)
            if done_at or time.time() - sent > 7200:
                c["wids"].pop(v, None)
                if done_at:
                    net = (env(f"XE_NET_{coin}") or acfg(coin, "net")) if v == "mexc" else \
                        env(f"XE_GNET_{coin}", GNET.get(coin, ""))
                    try:
                        xtime.record(v, "htx", coin, net, (done_at - sent) / 60, "магазин")
                    except Exception:
                        pass
            continue
        st = v_wstatus(v, coin, wid)
        if st == "cancel":
            c["wids"].pop(v, None)
            c["batches"] -= 1                                            # отменённый вывод — комиссию не платили
            c["wd_usd"] -= wd_usd(coin, v)
            X = sys.modules.get("xalloc")
            vs = acfg(coin, "venues") or []
            if v == "mexc" and "kucoin" in vs and X:                     # MEXC не выпускает эту монету — без MEXC
                X.A["coins"][coin]["venues"] = [x for x in vs if x != "mexc"]
                X.save()
                try:
                    left = api.mx_free(coin)
                    if left > 0:
                        api.mx_sell_market(f"{coin}USDT", left)              # остаток на MEXC — продаём там же
                except Exception as e:
                    log.warning("mexc sell %s: %s", coin, e)
                say(f"🔁 MEXC не выпускает {coin} на HTX — докупка {coin} теперь только через KuCoin. Остаток {coin} "
                    f"на MEXC продан, автохедж подстроит шорт")
            elif v == "mexc" and xaddr.bad(coin, addr):                  # следующая пачка уйдёт на запасной
                say(f"🔁 MEXC отклонил адрес HTX для {coin} — пачка уйдёт заново на запасной адрес")
            else:
                say(f"❗ {v.upper()} отменил вывод пачки {coin}, запасных адресов нет — выведи вручную на HTX")
        elif st == "done":
            c["wids"][v] = [wid, time.time(), addr, sent, amt, "chain"]   # дальше — ждём зачисления на HTX

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

def kucoin_sweep():
    """USDT, пришедший на KuCoin (основной счёт), переносим на торговый — чтобы магазин мог докупать."""
    if DRY or not any("kucoin" in venues_for(c) for c in COINS):
        return
    u = math.floor(api.kc_free("USDT", "main") * 100) / 100
    if u >= 1:
        api.kc_transfer("USDT", f"{u:.2f}", "main", "trade")
        say(f"💱 KuCoin: {u:.2f} USDT перенесены на торговый счёт")

def loop():
    time.sleep(90)
    load()
    say("Магазин v6 запущен: " + ", ".join(f"{c} ({'/'.join(v.upper() for v in venues_for(c))})" for c in COINS)
        + f" | ${TRADE:.0f}/сделка, вход от {MIN_EDGE*100:.1f}% чистыми")
    n = 0
    while True:
        now = time.time()
        n += 1
        if n % 30 == 0:
            try:
                kucoin_sweep()
            except Exception as e:
                warn_once("kcsweep", f"❗ KuCoin: не перенёс USDT на торговый счёт — {str(e)[:120]}")
        try:
            d = datetime.now(TZ).strftime("%Y-%m-%d")
            if S["day"] != d:
                S["day"], S["pnl_day"] = d, 0.0
                if S.get("stop_day") and S["paused"]:          # пауза от дневного стопа — новый день, торгуем дальше
                    S["paused"], S["stop_day"] = False, None
                    say("▶️ Новый день — магазин снова торгует после дневного стопа")
            if S["pnl_day"] <= -DAILY_STOP and not S["paused"]:
                S["paused"], S["stop_day"] = True, d
                say(f"⛔ Дневной стоп магазина {S['pnl_day']:.2f}$ — пауза до завтра (или /shop/start)")
            sync_coins()
            for coin in COINS:
                try:
                    c, w = S["c"][coin], window(coin)
                    if w:
                        c["edge_now"], c["venue_now"] = round(w["edge"] * 100, 2), w["venue"]
                        if w["rev"] is not None:
                            c["rev_now"] = round(w["rev"] * 100, 2)
                            c["rev_hits"] += w["rev"] >= MIN_EDGE
                        hedge_watch(coin, w["price"])
                        if exiting(coin):                  # мягкий выход: в окна, потом порциями
                            X = sys.modules.get("xalloc")
                            eh = acfg(coin, "exit_h")
                            late = X and now - (acfg(coin, "exit_t") or now) >= (X.EXIT_H if eh is None else eh) * 3600
                            raw = w["got"] / w["cost"] - 1 if w["cost"] else -1     # без доли пачки: докупки нет
                            if not S["paused"] and now >= c["cool"] and (
                                    (w["edge"] >= MIN_EDGE and c["sig"] != w["sig"]) or (late and raw >= -0.01)):
                                c["cool"], c["sig"] = now + COOL, w["sig"]
                                trade(coin, w, now, sell_only=True)
                        elif not S["paused"] and w["edge"] >= MIN_EDGE and now >= c["cool"] and c["sig"] != w["sig"]:
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
    return jsonify({"mode": "бумага" if DRY else "ЖИВОЙ", "venues": {c: venues_for(c) for c in COINS},
                    "paused": S.get("paused"),
                    "pnl_day": round(S.get("pnl_day", 0), 2),
                    "net_total": round(sum(v["net"] for v in coins.values()), 2), "coins": coins})

@mm.app.route("/shop/fix")
def shop_fix():
    """Разово обнулить учёт пачек монеты (после отменённых выводов): /shop/fix?t=...&coin=NES"""
    if not ok():
        return "forbidden", 403
    c = S["c"].get(request.args.get("coin", "").upper())
    if not c:
        return "нет такой монеты"
    c["batches"], c["wd_usd"] = 0, 0.0
    save()
    return "учёт пачек обнулён"

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
