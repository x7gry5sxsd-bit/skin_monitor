# xexec.py — исполнитель MEXC → HTX (BLUE, MAGIC, US): покупка на MEXC → вывод → продажа на HTX → возврат USDT.
#   По умолчанию БУМАГА (XE_DRY=1). Живой режим: XE_DRY=0.
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
COINS = [c.strip().upper() for c in env("XE_COINS", "BLUE,MAGIC").split(",") if c.strip()]
TRADE = float(env("XE_TRADE_USD", "15"))           # одна сделка, $
MIN_EDGE = float(env("XE_MIN_EDGE", "0.025"))      # порог входа по живым стаканам после всех комиссий
CONFIRM = int(env("XE_CONFIRM", "3"))              # окно должно держаться 3 проверки подряд (~30 с)
MAX_ACTIVE = int(env("XE_MAX_ACTIVE", "2"))
DAILY_STOP = float(env("XE_DAILY_STOP", "3"))      # дневной стоп, $
RETURN_AT = float(env("XE_RETURN_AT", "20"))       # вернуть USDT с HTX, когда накопится
STEP_SEC = float(env("XE_STEP_SEC", "10"))
TIMEOUT_MIN = float(env("XE_TIMEOUT_MIN", "30"))
TZ = timezone(timedelta(hours=float(env("TZ_OFFSET", "8"))))
WDFEE = {"BLUE": 3.0, "MAGIC": 1.0, "US": 10.0}     # комиссия вывода с MEXC, монет
log = logging.getLogger("xexec")
S = {}

def cfg(coin, key, d=""):
    return env(f"XE_{key}_{coin}", d)

def wdfee(coin):
    return float(cfg(coin, "WDFEE", str(WDFEE.get(coin, 1.0))))

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = %s", ("exec_dry" if DRY else "exec_live",), fetch=True)
    S.update(json.loads(r[0][0]) if r else {"cycles": [], "done": [], "pnl": 0.0, "day": "", "pnl_day": 0.0,
                                            "paused": False, "cool": {}, "trades": 0, "wins": 0, "last": {}})
    for k in ("cool", "last", "rev", "streak"):
        S.setdefault(k, {})

def save():
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES (%s, %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""",
             ("exec_dry" if DRY else "exec_live", json.dumps(S)))

def say(text):
    mm.tg(("🧪 [бумага] " if DRY else "💰 ") + text)

def check(coin):
    """Прямое окно: купить на MEXC → продать на HTX; плюс обратное (только наблюдение)."""
    bm, bh = xpaper.book("mexc", coin), xpaper.book("htx", coin)
    if not bm or not bh:
        return None, None
    qty = xpaper.buy_qty(bm[1], TRADE)
    arrive = qty - wdfee(coin) if qty else 0
    got = xpaper.sell_usdt(bh[0], arrive) if arrive > 0 else None
    fwd = {"arrive": arrive, "got": got, "edge": got / TRADE - 1} if got else None
    rq = xpaper.buy_qty(bh[1], TRADE)
    r_arr = rq - float(cfg(coin, "RWDFEE", str(wdfee(coin)))) if rq else 0
    r_got = xpaper.sell_usdt(bm[0], r_arr) if r_arr > 0 else None
    rev = round((r_got / TRADE - 1) * 100, 2) if r_got else None
    return fwd, rev

def start(coin, c, now):
    cyc = {"coin": coin, "t0": now, "stage": "transfer", "spent": TRADE,
           "exp_edge": round(c["edge"] * 100, 2), "arrive": c["arrive"], "ready": now + 60}
    if not DRY:
        if api.mx_free("USDT") < TRADE:
            S["cool"][coin] = now + 600
            say(f"Мало USDT на MEXC для {coin} — пауза 10 мин")
            return
        cyc["htx_before"] = api.hx_free(coin)
        api.mx_buy_quote(f"{coin}USDT", TRADE)
        time.sleep(2)
        amt = math.floor(api.mx_free(coin) * 100) / 100
        try:
            r = api.mx_withdraw(coin, cfg(coin, "NET"), cfg(coin, "ADDR"), f"{amt:.2f}")
            cyc["wid"] = r.get("id") if isinstance(r, dict) else None
        except Exception as e:
            S["cool"][coin] = now + 3600
            say(f"❗ {coin} куплен на MEXC, но вывод не прошёл: {str(e)[:200]}. Выведи вручную")
            return
        cyc["arrive"] = amt - wdfee(coin)
    S["cycles"].append(cyc)
    say(f"Вход {coin}: MEXC → HTX на ${TRADE:.0f}, ожидание +{cyc['exp_edge']}%")

def finish(cyc, got):
    pnl = got - cyc["spent"]
    S["pnl"] += pnl
    S["pnl_day"] += pnl
    S["trades"] += 1
    S["wins"] += pnl > 0
    S["done"] = (S["done"] + [[int(time.time()), cyc["coin"], round(got, 3), round(pnl, 3),
                               round(pnl / cyc["spent"] * 100, 2), cyc["exp_edge"]]])[-50:]
    say(f"Итог {cyc['coin']}: {pnl:+.2f}$ ({pnl / cyc['spent'] * 100:+.1f}%, ждали +{cyc['exp_edge']}%) | "
        f"за день {S['pnl_day']:+.2f}$")
    return True

def progress(cyc, now):
    coin = cyc["coin"]
    if DRY:
        if now < cyc["ready"]:
            return False
        bh = xpaper.book("htx", coin)
        got = xpaper.sell_usdt(bh[0], cyc["arrive"]) if bh else None
        return finish(cyc, got) if got else False
    if cyc.get("wid") and not cyc.get("canceled") and now - cyc.get("wchk", 0) > 30:
        cyc["wchk"] = now
        st = api.mx_withdraw_status(coin, cyc["wid"])
        if st in (8, 9):
            cyc["canceled"] = True
            say(f"❗ {coin}: MEXC отменил вывод — монеты вернулись на MEXC. Выведи их вручную на HTX "
                f"(сеть {cfg(coin, 'NET')}) — бот сам продаст. Или продай на MEXC и открой /exec/clear")
    bal = api.hx_free(coin)
    if bal < cyc["htx_before"] + 0.9 * cyc["arrive"]:
        if now - cyc["t0"] > TIMEOUT_MIN * 60 and not cyc.get("late"):
            cyc["late"] = True
            say(f"⚠️ {coin}: перевод идёт дольше {TIMEOUT_MIN:.0f} мин — проверь вручную")
        return False
    oid = api.hx_sell_market(f"{coin.lower()}usdt", bal).get("data")
    time.sleep(2)
    od = api.hx_order(oid).get("data") or {}
    return finish(cyc, float(od.get("field-cash-amount") or 0) - float(od.get("field-fees") or 0))

def return_usdt():
    if DRY or S["cycles"]:
        return
    u = api.hx_free("USDT")
    if u >= RETURN_AT:
        amt = math.floor(u * 100) / 100
        api.hx_withdraw_usdt(env("XE_MEXC_USDT_ADDR"), amt)
        say(f"↩️ Возврат {amt:.2f} USDT с HTX на MEXC (сеть {env('XE_USDT_CHAIN', 'trc20usdt')})")

def day_roll():
    d = datetime.now(TZ).strftime("%Y-%m-%d")
    if S["day"] != d:
        S["day"], S["pnl_day"] = d, 0.0
    if S["pnl_day"] <= -DAILY_STOP and not S["paused"]:
        S["paused"] = True
        say(f"⛔ Дневной стоп {S['pnl_day']:.2f}$ — пауза до ручного старта")

def loop():
    time.sleep(240)
    load()
    say(f"Исполнитель MEXC→HTX запущен: {', '.join(COINS)}, ${TRADE:.0f}/сделка, вход от {MIN_EDGE*100:.1f}%, "
        f"окно должно держаться {CONFIRM} проверки")
    if not DRY:
        for c in COINS:
            try:
                say(f"Сети вывода MEXC для {c}: {api.mx_networks(c)}")
            except Exception as e:
                say(f"Проверка сетей {c}: {str(e)[:200]}")
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
                for coin in COINS:
                    if len([c for c in S["cycles"] if c["stage"] == "transfer"]) >= MAX_ACTIVE:
                        break
                    if any(c["coin"] == coin for c in S["cycles"]) or now < S["cool"].get(coin, 0):
                        continue
                    c, rev = check(coin)
                    S["last"][coin] = round(c["edge"] * 100, 2) if c else None
                    S["rev"][coin] = rev
                    S["streak"][coin] = S["streak"].get(coin, 0) + 1 if c and c["edge"] >= MIN_EDGE else 0
                    if S["streak"][coin] >= CONFIRM:
                        S["streak"][coin] = 0
                        try:
                            start(coin, c, now)
                        except Exception as e:
                            S["cool"][coin] = now + 900
                            say(f"❗ {coin}: ошибка на входе — {str(e)[:200]}. Пауза 15 мин")
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
    return jsonify({"mode": "бумага" if DRY else "ЖИВОЙ", "enabled": ENABLED, "paused": S.get("paused"),
                    "pnl": round(S.get("pnl", 0), 2), "pnl_day": round(S.get("pnl_day", 0), 2),
                    "trades": S.get("trades"), "wins": S.get("wins"),
                    "edge_now_pct": S.get("last"), "edge_reverse_pct": S.get("rev"),
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
    """Проверка ключей без сделок: балансы, сети вывода, сети возврата USDT."""
    if not ok():
        return "forbidden", 403
    out = {}
    for name, fn in (("mexc_usdt", lambda: api.mx_free("USDT")), ("htx_usdt", lambda: api.hx_free("USDT")),
                     ("htx_account", api.hx_account)):
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = f"ошибка: {str(e)[:200]}"
    for c in COINS:
        try:
            out[f"mexc_networks_{c}"] = api.mx_networks(c)
        except Exception as e:
            out[f"mexc_networks_{c}"] = f"ошибка: {str(e)[:200]}"
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
