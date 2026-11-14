# xrcarry.py — БУМАГА «ОБРАТНЫЙ КЭРРИ»: фандинг без риска цены, когда шорты переплачивают. Деньгами не торгует.
#   ИДЕЯ. Когда у фьючерса сильно отрицательный фандинг (шорты платят лонгам 0,5%+ за расчёт), можно получать эти
#   выплаты без ставки на цену: занять монету на Gate (маржа), продать её на споте и на ту же сумму открыть ЛОНГ
#   фьючерса. Цена монеты больше не важна — что теряет одна нога, зарабатывает другая. Доход = выплаты лонгу −
#   процент по займу − комиссии ± изменение разницы фьючерс/спот (фьючерс в такие часы дешевле спота, разница
#   обычно сходится — это ещё плюс).
#   ПОЧЕМУ ЭТО НЕ РАЗОБРАНО: занять такую монету обычно нельзя — в 72–76% часов с сильным отрицательным фандингом
#   ставка займа на Gate стоит в потолке (весь пул уже занят). Окно открывается на часы — его и ловим. Малой суммой
#   ($100–300) в такое окно попасть проще, чем крупному игроку.
#   ПРАВИЛО (записано до запуска):
#   • сигнал: расчёт фандинга со ставкой ≤ XRC_IN (−0,5%);
#   • вход через 1 ч после расчёта (окно — ещё 2 ч), если: займ монеты на Gate включён и ставка займа ниже потолка
#     (пул не выбран), сумма ставок фандинга за 24 ч ≤ XRC_F24_IN (−0,3%), фьючерс и спот отличаются меньше чем на 5%,
#     проскальзывание входа по стаканам ≤ XRC_MAX_COST (0,6%);
#   • выход обеих ног: сумма ставок за 24 ч стала выше XRC_F24_OUT (−0,3%), или цена ушла на ±XRC_STOP (30%) от входа
#     (чтобы не выбило одну ногу), или прошло XRC_MAX_DAYS (14) суток, или займ / фьючерс закрывают.
#   ПРОВЕРКА НА ИСТОРИИ (Gate, 13.07–11.11.2026: часовые ставки займа, цены спота и фьючерса, все выплаты фандинга;
#   круг с комиссиями и спредами 1%): 59 сделок за 121 день по 42 монетам, +3,27% на сделку (медиана +1,04%), в плюсе
#   59%, худшая сделка −6%; первая половина +5,18%, вторая +1,43%; без 5 лучших монет +0,89%. Фандинг +6,4%, займ −2,9%,
#   разница фьючерс/спот +0,8%. В среднем 1,7 позиции одновременно, максимум 5. При $200 на ногу — около $96 в месяц.
#   ЧТО ПРОВЕРЯЕТ ЭТА БУМАГА СВЕРХ ИСТОРИИ: можно ли НА САМОМ ДЕЛЕ занять монету в момент входа. Бот спрашивает
#   биржу по ключу, сколько этой монеты можно занять (только спрашивает, ничего не занимает); если меньше нужного —
#   сделка не открывается, и это видно в сводке.
#   ЧЕСТНАЯ БУМАГА: вход и выход — по настоящим стаканам на нужное количество; комиссии спот 0,1% и фьючерс 0,05% за
#   каждую сторону; выплаты фандинга — из истории биржи, процент по займу — из часовой истории ставок биржи.
#   /rcarry?t=... — таблица, /rcarry/brief?t=... — коротко (&tg=1 — в Telegram). XRC_ON=0 — выключить.
import os, time, json, threading, logging
import requests
from flask import jsonify, request
import mm_scanner as mm
import xpaper
import xexec_api as api

def env(k, d=""):
    return os.getenv(k, d).strip()

VERSION = "xrcarry v1 · 14.11"
ON = env("XRC_ON", "1") == "1"
USD = float(env("XRC_USD", "200"))                     # сумма каждой ноги, $
MAXP = int(env("XRC_MAX", "3"))
R_IN = float(env("XRC_IN", "-0.005"))
F24_IN = float(env("XRC_F24_IN", "-0.003"))
F24_OUT = float(env("XRC_F24_OUT", "-0.003"))
CAP_K = float(env("XRC_CAP", "0.98"))
STOP = float(env("XRC_STOP", "0.30"))
MAX_DAYS = float(env("XRC_MAX_DAYS", "14"))
MAX_COST = float(env("XRC_MAX_COST", "0.006"))
FEE_S, FEE_F = 0.001, 0.0005
DELAY, WIN = 3600, 2 * 3600                            # вход через 1 ч после расчёта, окно входа ещё 2 ч
PRE = -0.001                                           # монеты, у которых текущая ставка ≤ −0,1%, смотрим подробно
STEP, SCAN, META = 300, 900, 6 * 3600
GT = "https://api.gateio.ws/api/v4"
log = logging.getLogger("xrcarry")
S = {"loaded": 0, "pos": {}, "done": [], "sig": {}, "log": []}
M = {"err": "", "last": 0, "scan": 0, "meta_t": 0, "meta": {}, "probe": ""}
LOCK = threading.Lock()

def lt(t=None, f="%d.%m %H:%M"):
    return time.strftime(f, time.gmtime((t if t is not None else time.time()) + 8 * 3600))

def get(path, **params):
    r = requests.get(GT + path, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def fnum(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d

# ---------- справочники: потолок ставки займа, займ включён, пара маржи ----------
def meta(now):
    if M["meta"] and now - M["meta_t"] < META:
        return M["meta"]
    cap = {x["currency"]: fnum(x.get("max_rate")) for x in get("/earn/uni/currencies")}
    loan = {x["name"]: x.get("loan_status") == "enable" for x in get("/unified/currencies")}
    mpair = {x["currency_pair"]: x.get("status") == "enabled" for x in get("/margin/uni/currency_pairs")}
    spot = {x["id"] for x in get("/spot/currency_pairs") if x.get("trade_status") == "tradable"}
    M["meta"] = {"cap": cap, "loan": loan, "mpair": mpair, "spot": spot}
    M["meta_t"] = now
    return M["meta"]

def fund_hist(c):
    """[(время расчёта, ставка)] от старых к новым."""
    return sorted((int(x["t"]), fnum(x["r"])) for x in get("/futures/usdt/funding_rate", contract=f"{c}_USDT", limit=60))

def loan_hist(c, n=30):
    """[(час, ставка займа за час)] от старых к новым."""
    r = get("/unified/history_loan_rate", currency=c, limit=n).get("rates") or []
    return sorted((int(x["time"]) // 1000, fnum(x["rate"])) for x in r)

def f24(hist, now):
    return sum(r for t, r in hist if now - 86400 < t <= now)

# ---------- стаканы ----------
def fill_usd(levels, usd):
    """За usd по уровням [(цена, монет)] → (монет, средняя цена) или None."""
    got = spent = 0.0
    for p, q in levels:
        v = min(usd - spent, p * q)
        spent += v
        got += v / p
        if spent >= usd - 1e-9:
            return got, spent / got
    return None

def fill_qty(levels, qty):
    left, cost = qty, 0.0
    for p, q in levels:
        v = min(left, q)
        cost += v * p
        left -= v
        if left <= qty * 1e-9:
            return cost / qty
    return None

def books(c, mult):
    sb = get("/spot/order_book", currency_pair=f"{c}_USDT", limit=50)
    fb = get("/futures/usdt/order_book", contract=f"{c}_USDT", limit=50)
    lv = lambda rows: [(fnum(p), fnum(q)) for p, q in rows if fnum(p) > 0]
    fl = lambda rows: [(fnum(x.get("p")), fnum(x.get("s")) * mult) for x in rows if fnum(x.get("p")) > 0]
    return {"sa": lv(sb.get("asks") or []), "sb": lv(sb.get("bids") or []), "fa": fl(fb.get("asks") or []), "fb": fl(fb.get("bids") or [])}

def mid(levels_bid, levels_ask):
    return (levels_bid[0][0] + levels_ask[0][0]) / 2 if levels_bid and levels_ask else None

# ---------- проверка займа по ключу (только вопрос к бирже, ничего не занимает) ----------
def borrowable(c):
    """(сколько монет можно занять или None, откуда/ошибка)."""
    if not api.GT_KEY:
        return None, "нет ключа Gate"
    errs = []
    for path, q, key in (("/margin/uni/borrowable", {"currency": c, "currency_pair": f"{c}_USDT"}, "borrowable"),
                         ("/unified/borrowable", {"currency": c}, "amount")):
        try:
            d = api.gt("GET", path, q) or {}
            return fnum(d.get(key)), path.split("/")[1]
        except Exception as e:
            errs.append(str(e)[:90])
    return None, "; ".join(errs)

# ---------- позиции ----------
def accrue(p, fh, lh, px_f, px_s):
    for t, r in fh:                                    # лонг получает −ставка × стоимость
        if t > p["f_seen"]:
            if t > p["t"]:
                p["fund"] += -r * p["qty"] * px_f
                p["n_pay"] = p.get("n_pay", 0) + 1
            p["f_seen"] = t
    h0 = int(p["t"]) // 3600 * 3600
    for h, r in lh:                                    # займ: ставка за каждый час, начиная с часа входа
        if h >= h0 and h > p["i_seen"]:
            p["intr"] += r * p["qty"] * px_s
            p["i_seen"] = h

def value(p, bk):
    """Закрыть сейчас: выкупить монету на споте (asks), продать фьючерс (bids) → (итог $, цена спота, цена фьючерса)."""
    so, fo = fill_qty(bk["sa"], p["qty"]), fill_qty(bk["fb"], p["qty"])
    if not so or not fo:
        return None
    fee_out = p["qty"] * (so * FEE_S + fo * FEE_F)
    pnl = (p["s_in"] - so) * p["qty"] + (fo - p["f_in"]) * p["qty"] + p["fund"] - p["intr"] - p["fee_in"] - fee_out
    return pnl, so, fo

def close(c, p, now, why, v):
    pnl, so, fo = v
    d = dict(p, coin=c, t1=int(now), why=why, pnl=round(pnl, 4), s_out=so, f_out=fo)
    with LOCK:
        S["pos"].pop(c, None)
        S["done"] = (S["done"] + [d])[-300:]
    mm.tg(f"🔄 Обратный кэрри (бумага): закрыл {c} — {why}. {(now - p['t']) / 3600:.0f} ч, выплат {p.get('n_pay', 0)}: "
          f"фандинг {p['fund']:+.2f}$, займ −{p['intr']:.2f}$, итог {pnl:+.2f}$ на ${p['usd']:.0f}")

def manage(now, ctr, md):
    for c, p in list(S["pos"].items()):
        try:
            k = ctr.get(f"{c}_USDT") or {}
            bk = books(c, p["mult"])
            fh, lh = fund_hist(c), loan_hist(c, 100)
            ms, mf = mid(bk["sb"], bk["sa"]), mid(bk["fb"], bk["fa"])
            accrue(p, fh, lh, mf or p["f_in"], ms or p["s_in"])
            v = value(p, bk)
            if v:
                p["now"] = round(v[0], 4)
            p["f24"] = f24(fh, now)
            move = (ms / p["s_in"] - 1) if ms else 0.0
            why = None
            if k.get("in_delisting") or not md["loan"].get(c, True):
                why = "фьючерс или займ закрывают"
            elif abs(move) >= STOP:
                why = f"цена ушла на {move * 100:+.0f}% — закрываю обе ноги"
            elif p["f24"] > F24_OUT:
                why = f"фандинг за сутки стал {p['f24'] * 100:+.2f}%"
            elif now - p["t"] >= MAX_DAYS * 86400:
                why = f"прошло {MAX_DAYS:g} суток"
            if why and v:
                close(c, p, now, why, v)
        except Exception as e:
            log.warning("xrcarry %s: %s", c, e)

def note(c, text, now):
    S["log"] = (S["log"] + [{"t": int(now), "c": c, "x": text}])[-200:]

def scan(now, tick, ctr, md):
    """Сигналы: расчёт со ставкой ≤ R_IN. Вход — через час после расчёта, если займ свободен."""
    for n, q in tick.items():
        c = n[:-5]
        if fnum(q.get("funding_rate")) > PRE and c not in S["sig"]:
            continue
        try:
            fh = fund_hist(c)
        except Exception:
            continue
        hit = [(t, r) for t, r in fh if r <= R_IN and now - t <= DELAY + WIN]
        if hit and c not in S["pos"]:
            t, r = hit[-1]
            if S["sig"].get(c, {}).get("t") != t:
                S["sig"][c] = {"t": t, "r": r, "done": False}
        sg = S["sig"].get(c)
        if not sg or sg["done"] or c in S["pos"] or now < sg["t"] + DELAY:
            continue
        if now > sg["t"] + DELAY + WIN:
            sg["done"] = True
            continue
        sg["done"] = True                               # одно решение на сигнал
        why = entry(c, sg, fh, now, ctr, md)
        note(c, f"сигнал {lt(sg['t'], '%H:%M')} ставка {sg['r'] * 100:+.2f}% → {why}", now)
    for c in [c for c, s in S["sig"].items() if now - s["t"] > DELAY + WIN + 3600]:
        S["sig"].pop(c, None)

def entry(c, sg, fh, now, ctr, md):
    k = ctr.get(f"{c}_USDT") or {}
    if len(S["pos"]) >= MAXP:
        return f"пропуск: уже {MAXP} позиций"
    if k.get("in_delisting") or f"{c}_USDT" not in md["spot"]:
        return "пропуск: нет спота или фьючерс в делистинге"
    if not md["loan"].get(c) or not md["mpair"].get(f"{c}_USDT"):
        return "пропуск: займ этой монеты на Gate выключен"
    lh = loan_hist(c, 3)
    cap = md["cap"].get(c) or 0.0
    rate = lh[-1][1] if lh else None
    if rate is None or not cap or rate >= CAP_K * cap:
        return f"пропуск: займ выбран (ставка {(rate or 0) * 2400:.2f}% в сутки = потолок)"
    s24 = f24(fh, now)
    if s24 > F24_IN:
        return f"пропуск: фандинг за сутки уже {s24 * 100:+.2f}%"
    mult = fnum(k.get("quanto_multiplier"))
    bk = books(c, mult)
    sell = fill_usd(bk["sb"], USD)
    if not sell or not mult:
        return "пропуск: мелкий стакан спота"
    qty, s_in = sell
    f_in = fill_qty(bk["fa"], qty)
    ms, mf = mid(bk["sb"], bk["sa"]), mid(bk["fb"], bk["fa"])
    if not f_in or not ms or not mf:
        return "пропуск: мелкий стакан фьючерса"
    if abs(mf / ms - 1) > 0.05:
        return f"пропуск: фьючерс и спот разошлись на {(mf / ms - 1) * 100:+.1f}%"
    slip = (ms - s_in) / ms + (f_in - mf) / mf
    if slip > MAX_COST:
        return f"пропуск: проскальзывание входа {slip * 100:.2f}% > {MAX_COST * 100:g}%"
    can, src = borrowable(c)
    M["probe"] = src if can is not None else f"не удалось: {src}"
    if can is not None and can < qty:
        return f"пропуск: биржа даёт занять только {can:.6g} {c} из {qty:.6g} нужных"
    p = {"t": int(now), "sig_t": sg["t"], "rate_sig": sg["r"], "qty": qty, "mult": mult, "s_in": s_in, "f_in": f_in, "usd": USD,
         "fee_in": qty * (s_in * FEE_S + f_in * FEE_F), "fund": 0.0, "intr": 0.0, "f_seen": int(now), "i_seen": 0, "n_pay": 0,
         "loan_rate": rate, "cap": cap, "f24_in": s24, "slip": slip,
         "probe": (f"биржа даёт занять {can:.6g} (нужно {qty:.6g})" if can is not None else f"займ не проверен ({src[:60]})")}
    with LOCK:
        S["pos"][c] = p
    mm.tg(f"🔄 Обратный кэрри (бумага): вход {c} на ${USD:.0f} — продал на споте {s_in:.6g} в долг + лонг фьючерса {f_in:.6g}. "
          f"Ставка расчёта {sg['r'] * 100:+.2f}%, за сутки {s24 * 100:+.2f}%; займ {rate * 2400:.2f}% в сутки (потолок "
          f"{cap * 2400:.2f}%); проскальзывание {slip * 100:.2f}%. {p['probe']}")
    return "ВХОД"

def step(now):
    md = meta(now)
    ctr = {x["name"]: x for x in get("/futures/usdt/contracts")}
    manage(now, ctr, md)
    if now - M["scan"] >= SCAN or any(now >= s["t"] + DELAY and not s["done"] for s in S["sig"].values()):
        tick = {x["contract"]: x for x in get("/futures/usdt/tickers") if x.get("contract", "").endswith("_USDT")}
        scan(now, tick, ctr, md)
        M["scan"] = now
    M["last"] = now
    save()

# ---------- отчёт ----------
def totals():
    with LOCK:
        pos = json.loads(json.dumps(S["pos"]))
        done = list(S["done"])
    closed = sum(d["pnl"] for d in done)
    opened = sum(p.get("now", -p["fee_in"]) for p in pos.values())
    days = max((time.time() - S.get("fixed_t", time.time())) / 86400, 1e-9)
    return {"pos": pos, "done": done, "closed": closed, "open": opened, "tot": closed + opened, "days": days,
            "fund": sum(p["fund"] for p in pos.values()) + sum(d["fund"] for d in done),
            "intr": sum(p["intr"] for p in pos.values()) + sum(d["intr"] for d in done)}

def text():
    T = totals()
    lg = S["log"][-50:]
    n_sig = sum(1 for x in S["log"] if time.time() - x["t"] < 7 * 86400)
    n_cap = sum(1 for x in S["log"] if time.time() - x["t"] < 7 * 86400 and "займ выбран" in x["x"])
    L = [f"🔄 {VERSION} — БУМАГА «обратный кэрри»: монету в долг и на спот + лонг фьючерса, доход — отрицательный фандинг. {lt()}",
         f"Нога ${USD:.0f}, позиций ≤{MAXP}. Вход через 1 ч после расчёта ≤ {R_IN * 100:g}%, если займ свободен; выход — фандинг за "
         f"сутки выше {F24_OUT * 100:g}% или цена ±{STOP * 100:g}%",
         f"Итог за {T['days']:.1f} сут: {T['tot']:+.2f}$ (фандинг {T['fund']:+.2f}$, займ −{T['intr']:.2f}$; закрытые {T['closed']:+.2f}$ "
         f"в {len(T['done'])} сделках, открытые сейчас {T['open']:+.2f}$)",
         f"Сигналов за 7 дней: {n_sig}, из них займ был выбран: {n_cap}"
         + (f" · проверка займа по ключу: {M['probe']}" if M["probe"] else " · проверки займа по ключу ещё не было")]
    if not T["pos"]:
        L.append("Позиций нет.")
    for c, p in sorted(T["pos"].items(), key=lambda kv: kv[1]["t"]):
        L.append(f"  {c}: {(time.time() - p['t']) / 3600:.0f} ч, выплат {p.get('n_pay', 0)}, фандинг {p['fund']:+.2f}$, займ −{p['intr']:.2f}$, "
                 f"если закрыть сейчас {p.get('now', -p['fee_in']):+.2f}$ · фандинг за сутки {p.get('f24', p['f24_in']) * 100:+.2f}% · {p['probe']}")
    for d in T["done"][-5:]:
        L.append(f"  закрыта {d['coin']} {lt(d['t1'])}: {d['pnl']:+.2f}$ за {(d['t1'] - d['t']) / 3600:.0f} ч ({d['why']})")
    for x in lg[-6:]:
        L.append(f"  {lt(x['t'])} {x['c']}: {x['x']}")
    if M["err"]:
        L.append("⚠️ " + M["err"])
    return "\n".join(L)

def table():
    T = totals()
    return {"версия": VERSION, "итог": round(T["tot"], 2), "фандинг": round(T["fund"], 2), "займ": round(T["intr"], 2), "суток": round(T["days"], 2),
            "позиции": T["pos"], "закрытые": T["done"], "сигналы": S["sig"], "журнал": S["log"][-100:], "проверка_займа": M["probe"], "ошибка": M["err"]}

# ---------- база и цикл ----------
def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'rcarry_v1'", fetch=True)
    with LOCK:
        S.update(json.loads(r[0][0]) if r else {})
        for k, d in (("pos", {}), ("done", []), ("sig", {}), ("log", [])):
            S.setdefault(k, d)
        if not S.get("fixed_t"):
            S["fixed_t"] = int(time.time())
    S["loaded"] = 1

def save():
    with LOCK:
        js = json.dumps({k: v for k, v in S.items() if k != "loaded"})
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('rcarry_v1', %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (js,))

def loop():
    time.sleep(240)
    while not S.get("loaded"):
        try:
            load()
        except Exception:
            log.exception("xrcarry load")
            time.sleep(60)
    while True:
        t0 = time.time()
        try:
            step(t0)
            M["err"] = ""
        except Exception as e:
            M["err"] = f"Gate: {str(e)[:120]}"
            log.exception("xrcarry step")
        time.sleep(max(30, STEP - (time.time() - t0)))

def ok():
    return mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN

@mm.app.route("/rcarry")
def rcarry_page():
    if not ok():
        return "forbidden", 403
    return jsonify(table())

@mm.app.route("/rcarry/brief")
def rcarry_brief():
    if not ok():
        return "forbidden", 403
    txt = text()
    if request.args.get("tg") == "1":
        mm.tg(txt)
    return txt, 200, {"Content-Type": "text/plain; charset=utf-8"}

if ON:
    threading.Thread(target=loop, daemon=True).start()
