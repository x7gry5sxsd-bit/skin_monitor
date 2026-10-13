# xexec.py v2 — исполнитель {MEXC, Gate} → HTX: покупка там, где дешевле → вывод → продажа на HTX → возврат USDT.
#   XE_DRY=1 — всё на бумаге. XE_GATE_DRY=1 — Gate на бумаге, MEXC живьём (по умолчанию).
import os, time, json, math, threading, logging
from datetime import datetime, timezone, timedelta
from flask import jsonify, request
import mm_scanner as mm
import xpaper
import xexec_api as api

def env(k, d=""):
    return os.getenv(k, d).strip()

ENABLED = env("XE_ENABLED", "1") == "1"
DRY = env("XE_DRY", "1") == "1"
GATE_DRY = env("XE_GATE_DRY", "1") == "1"
VENUES = [v.strip() for v in env("XE_VENUES", "mexc,gate").split(",") if v.strip() in ("mexc", "gate")]
COINS = [c.strip().upper() for c in env("XE_COINS", "BLUE,MAGIC").split(",") if c.strip()]
TRADE = float(env("XE_TRADE_USD", "15"))
MIN_EDGE = float(env("XE_MIN_EDGE", "0.025"))
CONFIRM = int(env("XE_CONFIRM", "3"))
MAX_ACTIVE = int(env("XE_MAX_ACTIVE", "2"))
DAILY_STOP = float(env("XE_DAILY_STOP", "3"))
RETURN_AT = float(env("XE_RETURN_AT", "20"))
STEP_SEC = float(env("XE_STEP_SEC", "10"))
TIMEOUT_MIN = float(env("XE_TIMEOUT_MIN", "30"))
TZ = timezone(timedelta(hours=float(env("TZ_OFFSET", "8"))))
WDFEE = {"mexc": {"BLUE": 3.0, "MAGIC": 1.0, "US": 10.0}, "gate": {"BLUE": 4.68, "MAGIC": 0.413, "US": 5.15}}
GNET = {"BLUE": "SUI", "US": "SUI", "MAGIC": "ARBEVM"}
log = logging.getLogger("xexec")
S = {}

def cfg(coin, key, d=""):
    return env(f"XE_{key}_{coin}", d)

def wdfee(coin, v="mexc"):
    return float(cfg(coin, "WDFEE" if v == "mexc" else "GWDFEE", str(WDFEE[v].get(coin, 1.0))))

def is_paper(v):
    return DRY or (v == "gate" and GATE_DRY)

def v_free(v, cur):
    return api.mx_free(cur) if v == "mexc" else api.gt_free(cur)

def v_buy(v, coin, usdt):
    return api.mx_buy_quote(f"{coin}USDT", usdt) if v == "mexc" else api.gt_buy_quote(coin, usdt)

def v_withdraw(v, coin, amt):
    addr = cfg(coin, "ADDR")
    if v == "mexc":
        return api.mx_withdraw(coin, cfg(coin, "NET"), addr, f"{amt:.2f}")
    return api.gt_withdraw(coin, cfg(coin, "GNET", GNET.get(coin, "")), addr, f"{amt:.2f}")

def v_canceled(v, coin, wid):
    if v == "mexc":
        return api.mx_withdraw_status(coin, wid) in (8, 9)
    return api.gt_withdraw_status(coin, wid) in ("CANCEL", "FAIL", "INVALID")

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = %s", ("exec_dry" if DRY else "exec_live",), fetch=True)
    S.update(json.loads(r[0][0]) if r else {"cycles": [], "done": [], "pnl": 0.0, "day": "", "pnl_day": 0.0,
                                            "paused": False, "trades": 0, "wins": 0})
    for k in ("cool", "last", "rev", "streak"):
        S.setdefault(k, {})
    S.setdefault("gp", {"trades": 0, "wins": 0, "pnl": 0.0, "done": []})

def save():
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES (%s, %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""",
             ("exec_dry" if DRY else "exec_live", json.dumps(S)))

def say(text, paper=False):
    mm.tg(("🧪 [бумага] " if paper else "💰 ") + text)

def check(coin):
    """Окна по каждой бирже покупки: {venue: {arrive, edge}}; плюс обратное окно MEXC (наблюдение)."""
    bh = xpaper.book("htx", coin)
    if not bh:
        return {}, None
    res, rev = {}, None
    for v in VENUES:
        bv = xpaper.book(v, coin)
        if not bv:
            continue
        qty = xpaper.buy_qty(bv[1], TRADE)
        arrive = qty - wdfee(coin, v) if qty else 0
        got = xpaper.sell_usdt(bh[0], arrive) if arrive > 0 else None
        if got:
            res[v] = {"arrive": arrive, "edge": got / TRADE - 1}
        if v == "mexc":
            rq = xpaper.buy_qty(bh[1], TRADE)
            r_got = xpaper.sell_usdt(bv[0], rq - wdfee(coin)) if rq and rq > wdfee(coin) else None
            rev = round((r_got / TRADE - 1) * 100, 2) if r_got else None
    return res, rev

def start(coin, v, c, now):
    paper = is_paper(v)
    cyc = {"coin": coin, "venue": v, "paper": paper, "t0": now, "stage": "transfer", "spent": TRADE,
           "exp_edge": round(c["edge"] * 100, 2), "arrive": c["arrive"], "ready": now + 60}
    if not paper:
        if v_free(v, "USDT") < TRADE:
            S["cool"][f"{coin}@{v}"] = now + 600
            say(f"Мало USDT на {v} для {coin} — пауза 10 мин")
            return
        cyc["htx_before"] = api.hx_free(coin)
        v_buy(v, coin, TRADE)
        time.sleep(2)
        amt = math.floor(v_free(v, coin) * 100) / 100
        try:
            r = v_withdraw(v, coin, amt)
            cyc["wid"] = r.get("id") if isinstance(r, dict) else None
        except Exception as e:
            S["cool"][f"{coin}@{v}"] = now + 3600
            say(f"❗ {coin} куплен на {v}, но вывод не прошёл: {str(e)[:200]}. Выведи вручную")
            return
        cyc["arrive"] = amt - wdfee(coin, v)
    S["cycles"].append(cyc)
    say(f"Вход {coin}: {v.upper()} → HTX на ${TRADE:.0f}, ожидание +{cyc['exp_edge']}%", paper)

def finish(cyc, got):
    pnl = got - cyc["spent"]
    row = [int(time.time()), cyc["coin"], cyc.get("venue", "mexc"), round(got, 3), round(pnl, 3),
           round(pnl / cyc["spent"] * 100, 2), cyc["exp_edge"]]
    if cyc.get("paper") and not DRY:                   # бумажная нога (Gate) при живом MEXC — отдельный учёт
        g = S["gp"]
        g["trades"] += 1
        g["wins"] += pnl > 0
        g["pnl"] += pnl
        g["done"] = (g["done"] + [row])[-20:]
        say(f"Итог {cyc['coin']} через {cyc['venue']}: {pnl:+.2f}$ ({pnl / cyc['spent'] * 100:+.1f}%) | "
            f"бумага Gate всего {g['pnl']:+.2f}$", True)
        return True
    S["pnl"] += pnl
    S["pnl_day"] += pnl
    S["trades"] += 1
    S["wins"] += pnl > 0
    S["done"] = (S["done"] + [row])[-50:]
    say(f"Итог {cyc['coin']} ({cyc.get('venue', 'mexc')}): {pnl:+.2f}$ ({pnl / cyc['spent'] * 100:+.1f}%, "
        f"ждали +{cyc['exp_edge']}%) | за день {S['pnl_day']:+.2f}$", DRY)
    return True

def progress(cyc, now):
    coin, v = cyc["coin"], cyc.get("venue", "mexc")
    if cyc.get("paper"):
        if now < cyc["ready"]:
            return False
        bh = xpaper.book("htx", coin)
        got = xpaper.sell_usdt(bh[0], cyc["arrive"]) if bh else None
        return finish(cyc, got) if got else False
    if cyc.get("wid") and not cyc.get("canceled") and now - cyc.get("wchk", 0) > 30:
        cyc["wchk"] = now
        if v_canceled(v, coin, cyc["wid"]):
            cyc["canceled"] = True
            say(f"❗ {coin}: {v} отменил вывод — монеты вернулись на {v}. Выведи их вручную на HTX — "
                f"бот сам продаст. Или продай на {v} и открой /exec/clear")
    bal = api.hx_free(coin)
    if bal < cyc["htx_before"] + 0.9 * cyc["arrive"]:
        if now - cyc["t0"] > TIMEOUT_MIN * 60 and not cyc.get("late"):
            cyc["late"] = True
            say(f"⚠️ {coin}: перевод с {v} идёт дольше {TIMEOUT_MIN:.0f} мин — проверь вручную")
        return False
    oid = api.hx_sell_market(f"{coin.lower()}usdt", bal).get("data")
    time.sleep(2)
    od = api.hx_order(oid).get("data") or {}
    return finish(cyc, float(od.get("field-cash-amount") or 0) - float(od.get("field-fees") or 0))

def live(c):
    return c["stage"] == "transfer" and not (c.get("paper") and not DRY)

def return_usdt():
    if DRY or any(live(c) for c in S["cycles"]) or time.time() < S.get("ret_cool", 0):
        return
    u = api.hx_free("USDT")
    if u < RETURN_AT:
        return
    dest, addr = "mexc", env("XE_MEXC_USDT_ADDR")
    if "gate" in VENUES and not GATE_DRY and env("XE_GATE_USDT_ADDR"):
        try:
            if api.gt_free("USDT") < api.mx_free("USDT"):
                dest, addr = "gate", env("XE_GATE_USDT_ADDR")
        except Exception:
            pass
    fee = float(env("XE_USDT_FEE", "0.01"))
    amt = math.floor((u - fee - 0.01) * 100) / 100
    try:
        api.hx_withdraw_usdt(addr, amt)
        say(f"↩️ Возврат {amt:.2f} USDT с HTX на {dest.upper()} (сеть {env('XE_USDT_CHAIN', 'trc20usdt')})")
    except Exception as e:
        S["ret_cool"] = time.time() + 1800
        say(f"❗ Возврат USDT на {dest} не прошёл: {str(e)[:150]}. Повтор через 30 мин")

def day_roll():
    d = datetime.now(TZ).strftime("%Y-%m-%d")
    if S["day"] != d:
        S["day"], S["pnl_day"] = d, 0.0
    if S["pnl_day"] <= -DAILY_STOP and not S["paused"]:
        S["paused"] = True
        say(f"⛔ Дневной стоп {S['pnl_day']:.2f}$ — пауза до ручного старта")

def entries(now):
    for coin in COINS:
        res, rev = check(coin)
        S["last"][coin] = {v: round(x["edge"] * 100, 2) for v, x in res.items()}
        S["rev"][coin] = rev
        for v, x in res.items():
            key = f"{coin}@{v}"
            S["streak"][key] = S["streak"].get(key, 0) + 1 if x["edge"] >= MIN_EDGE else 0
        for paper_pass in (False, True):               # сначала живые биржи, потом бумажные
            cands = [(x["edge"], v) for v, x in res.items()
                     if is_paper(v) == paper_pass and S["streak"].get(f"{coin}@{v}", 0) >= CONFIRM
                     and now >= S["cool"].get(f"{coin}@{v}", 0)]
            if not cands or any(c["coin"] == coin and bool(c.get("paper")) == paper_pass for c in S["cycles"]):
                continue
            if not paper_pass and len([c for c in S["cycles"] if live(c)]) >= MAX_ACTIVE:
                continue
            edge, v = max(cands)
            S["streak"][f"{coin}@{v}"] = 0
            try:
                start(coin, v, res[v], now)
            except Exception as e:
                S["cool"][f"{coin}@{v}"] = now + 900
                say(f"❗ {coin} на {v}: ошибка на входе — {str(e)[:200]}. Пауза 15 мин")

def loop():
    time.sleep(60)
    load()
    say(f"Исполнитель v2 запущен: {', '.join(COINS)} | биржи покупки: "
        + ", ".join(f"{v}{' (бумага)' if is_paper(v) else ''}" for v in VENUES)
        + f" | ${TRADE:.0f}/сделка, вход от {MIN_EDGE*100:.1f}%", DRY)
    while True:
        now = time.time()
        try:
            day_roll()
            for cyc in list(S["cycles"]):
                if cyc["stage"] != "transfer":
                    continue
                try:
                    if progress(cyc, now):
                        S["cycles"].remove(cyc)
                except Exception as e:
                    cyc["stage"], cyc["err"] = "stuck", str(e)[:200]
                    say(f"❗ {cyc['coin']}: ошибка на продаже — {cyc['err']}. Нужна ручная проверка")
            if ENABLED and not S["paused"]:
                entries(now)
                try:
                    return_usdt()
                except Exception as e:
                    say(f"❗ Возврат USDT: {str(e)[:200]}")
            save()
        except Exception:
            log.exception("xexec")
        time.sleep(STEP_SEC)
def ok():
    return mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN

@mm.app.route("/exec")
def exec_page():
    if not ok():
        return "forbidden", 403
    g = S.get("gp", {})
    return jsonify({"mode": "бумага" if DRY else "ЖИВОЙ", "venues": {v: "бумага" if is_paper(v) else "живой"
                                                                  for v in VENUES},
                    "enabled": ENABLED, "paused": S.get("paused"),
                    "pnl": round(S.get("pnl", 0), 2), "pnl_day": round(S.get("pnl_day", 0), 2),
                    "trades": S.get("trades"), "wins": S.get("wins"),
                    "edge_now_pct": S.get("last"), "edge_reverse_pct": S.get("rev"),
                    "gate_paper": {"trades": g.get("trades"), "wins": g.get("wins"),
                                   "pnl": round(g.get("pnl", 0), 2), "last": g.get("done", [])[-5:]},
                    "cycles": S.get("cycles"), "last_trades": S.get("done", [])[-10:]})

@mm.app.route("/exec/stop")
def exec_stop():
    if not ok():
        return "forbidden", 403
    S["paused"] = True
    save()
    say("⏸ Остановлен вручную")
    return "остановлен"

@mm.app.route("/exec/start")
def exec_start():
    if not ok():
        return "forbidden", 403
    S["paused"] = False
    save()
    say("▶️ Запущен вручную")
    return "запущен"

@mm.app.route("/exec/clear")
def exec_clear():
    if not ok():
        return "forbidden", 403
    S["cycles"] = [c for c in S["cycles"] if c["stage"] != "stuck" and not c.get("canceled")]
    save()
    return "застрявшие и отменённые сделки убраны"

@mm.app.route("/exec/check")
def exec_check():
    """Проверка ключей без сделок: балансы и сети вывода на всех биржах."""
    if not ok():
        return "forbidden", 403
    out = {}
    for name, fn, has in (("mexc_usdt", lambda: api.mx_free("USDT"), True),
                          ("htx_usdt", lambda: api.hx_free("USDT"), True),
                          ("gate_usdt", lambda: api.gt_free("USDT"), api.GT_KEY),
                          ("kucoin_usdt_main", lambda: api.kc_free("USDT", "main"), api.KC_KEY),
                          ("kucoin_usdt_trade", lambda: api.kc_free("USDT", "trade"), api.KC_KEY)):
        try:
            out[name] = fn() if has else "ключа нет"
        except Exception as e:
            out[name] = f"ошибка: {str(e)[:200]}"
    for c in COINS:
        for name, fn in ((f"mexc_networks_{c}", lambda c=c: api.mx_networks(c)),
                         (f"gate_networks_{c}", lambda c=c: api.gt_networks(c)),
                         (f"kucoin_networks_{c}", lambda c=c: api.kc_networks(c))):
            try:
                out[name] = fn()
            except Exception as e:
                out[name] = f"ошибка: {str(e)[:200]}"
    try:
        d = api.S.get("https://api.huobi.pro/v2/reference/currencies", params={"currency": "usdt"},
                      timeout=15).json()
        out["htx_usdt_chains"] = [(c.get("chain"), c.get("displayName"), c.get("withdrawStatus"),
                                   c.get("transactFeeWithdraw"))
                                  for c in ((d.get("data") or [{}])[0].get("chains") or [])]
    except Exception as e:
        out["htx_usdt_chains"] = f"ошибка: {str(e)[:200]}"
    return jsonify(out)

threading.Thread(target=loop, daemon=True).start()