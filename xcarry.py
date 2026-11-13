# xcarry.py — БУМАГА «ПОСТОЯННО В СДЕЛКЕ: ФАНДИНГ БЕЗ РИСКА ЦЕНЫ». Ничего не торгует, ключи бирж не трогает.
#   ИДЕЯ. Купить монету на споте Gate и на ту же сумму открыть шорт на фьючерсе Gate. Цена монеты больше не важна:
#   что теряет одна нога, зарабатывает другая. Пока ставка фандинга положительная, шорт каждые 1–8 часов получает
#   выплату. Позиции держатся неделями — бот в сделке постоянно.
#   ПРАВИЛО (записано до запуска и не меняется):
#   • вход: средний фандинг монеты за 7 суток ≥ XCY_IN (0,05% в сутки), оборот фьючерса ≥ $200 тыс. в сутки,
#     монета торгуется на споте Gate, фьючерс не в делистинге; фьючерс не дешевле спота больше чем на 0,5%;
#     круг (вход + выход) по стаканам с комиссиями ≤ XCY_MAX_RT (1,2%);
#   • до XCY_N (5) монет по XCY_USD ($100) — первыми идут монеты с самым высоким фандингом за 7 суток;
#   • выход: средний фандинг за 7 суток упал ниже XCY_OUT (0,02% в сутки) и позиции ≥ 3 суток; или монету
#     снимают с торгов.
#   ПРОВЕРКА НА ИСТОРИИ (Gate, 16.05–11.11.2026, 176 дней, часовые цены спота и фьючерса, настоящие выплаты):
#   около +20% годовых на вложенные деньги (при круге 0,8%), обе половины истории одинаковые (+0,054% в сутки),
#   недель в плюсе 88–96%, худшая неделя −0,8%, все месяцы в плюсе. Это около +1,6% в месяц: на $850 — $13–14.
#   ЧЕСТНАЯ БУМАГА: вход и выход — по настоящим стаканам спота и фьючерса на нужную сумму; комиссии спота 0,1% и
#   фьючерса 0,05% с каждой стороны; фандинг — из истории выплат биржи (перерыв в работе бота его не искажает).
#   Деньги на позицию: спот + залог шорта = сумма × (1 + 1/XCY_LEV), плечо шорта 3.
#   /carry?t=... — таблица, /carry/brief?t=... — коротко (&tg=1 — в Telegram). XCY_ON=0 — выключить.
import os, time, json, threading, logging
import requests
from flask import jsonify, request
import mm_scanner as mm
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

VERSION = "xcarry v1 · 13.11"
ON = env("XCY_ON", "1") == "1"
N = int(env("XCY_N", "5"))
USD = float(env("XCY_USD", "100"))
LEV = float(env("XCY_LEV", "3"))
R_IN = float(env("XCY_IN", "0.0005"))                  # средний фандинг за 7 суток, доля в сутки
R_OUT = float(env("XCY_OUT", "0.0002"))
MIN_HOLD = float(env("XCY_MIN_HOLD_D", "3")) * 86400
MAX_RT = float(env("XCY_MAX_RT", "0.012"))             # круг по стаканам вместе с комиссиями
BASIS_MIN = float(env("XCY_BASIS_MIN", "-0.005"))      # фьючерс / спот − 1 на входе
MIN_VOL = float(env("XCY_MIN_VOL", "200000"))
FEE_S, FEE_F = float(env("XCY_FEE_S", "0.001")), float(env("XCY_FEE_F", "0.0005"))
TOP = int(env("XCY_TOP", "25"))                        # сколько монет с самой высокой текущей ставкой проверять
SCAN_EVERY, STEP_EVERY = 4 * 3600, 1800
GT = "https://api.gateio.ws/api/v4"
log = logging.getLogger("xcarry")
S = {"loaded": 0, "pos": {}, "done": []}
M = {"err": "", "last": 0, "scan": 0, "skip": []}
LOCK = threading.Lock()

def lt(t=None, f="%d.%m %H:%M"):
    return time.strftime(f, time.gmtime((t or time.time()) + 8 * 3600))

def get(path, **params):
    r = requests.get(GT + path, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def fnum(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d

# ---------- стаканы ----------
def fill_usd(levels, usd):
    """Сколько монет дают за usd по уровням [(цена, монет)] → (монет, средняя цена) или None, если стакан мелкий."""
    got = spent = 0.0
    for p, q in levels:
        v = min(usd - spent, p * q)
        spent += v
        got += v / p
        if spent >= usd - 1e-9:
            return got, spent / got
    return None

def fill_qty(levels, qty):
    """Средняя цена на qty монет по уровням или None."""
    left, cost = qty, 0.0
    for p, q in levels:
        v = min(left, q)
        cost += v * p
        left -= v
        if left <= qty * 1e-9:
            return cost / qty
    return None

def books(coin, mult):
    sb = get("/spot/order_book", currency_pair=coin, limit=50)
    fb = get("/futures/usdt/order_book", contract=coin, limit=50)
    lv = lambda rows: [(fnum(p), fnum(q)) for p, q in rows if fnum(p) > 0]
    fl = lambda rows: [(fnum(x.get("p")), fnum(x.get("s")) * mult) for x in rows if fnum(x.get("p")) > 0]
    return {"sa": lv(sb.get("asks") or []), "sb": lv(sb.get("bids") or []),
            "fa": fl(fb.get("asks") or []), "fb": fl(fb.get("bids") or [])}

# ---------- фандинг ----------
def fund_hist(coin):
    """История выплат: [(время, ставка)] от старых к новым (до 200 последних)."""
    rows = get("/futures/usdt/funding_rate", contract=coin, limit=200)
    return sorted((int(x["t"]), fnum(x["r"])) for x in rows)

def per_day(hist, now, days=7):
    """Средний фандинг в сутки за days суток; None — истории меньше, чем days − 0,5 суток."""
    if not hist or hist[0][0] > now - (days - 0.5) * 86400:
        return None
    return sum(r for t, r in hist if t > now - days * 86400) / days

# ---------- позиции ----------
def value(p, bk):
    """Что получим, закрыв позицию сейчас: (доход $, цена продажи спота, цена откупа фьючерса) или None."""
    so, fo = fill_qty(bk["sb"], p["qty"]), fill_qty(bk["fa"], p["qty"])
    if not so or not fo:
        return None
    fee_out = p["qty"] * (so * FEE_S + fo * FEE_F)
    return (so - p["s_in"]) * p["qty"] + (p["f_in"] - fo) * p["qty"] + p["fund"] - p["fee_in"] - fee_out, so, fo

def accrue(p, hist, px):
    """Начислить выплаты после входа, которых ещё не видели. Шорт получает ставку × стоимость позиции."""
    for t, r in hist:
        if t > p["f_seen"]:
            if t > p["t"]:
                p["fund"] += r * p["qty"] * px
                p["n_pay"] = p.get("n_pay", 0) + 1
            p["f_seen"] = t

def close(coin, p, now, why, v):
    pnl, so, fo = v
    d = dict(p, coin=coin, t1=int(now), why=why, pnl=round(pnl, 4), s_out=so, f_out=fo)
    with LOCK:
        S["pos"].pop(coin, None)
        S["done"] = (S["done"] + [d])[-300:]
    mm.tg(f"💤 Фандинг (бумага): закрыл {coin[:-5]} — {why}. Держал {(now - p['t']) / 86400:.1f} сут, "
          f"фандинг {p['fund']:+.2f}$, итог {pnl:+.2f}$")

def manage(now, ctr, spot):
    for coin, p in list(S["pos"].items()):
        try:
            c = ctr.get(coin) or {}
            bk = books(coin, p["mult"])
            hist = fund_hist(coin)
            fo = fill_qty(bk["fa"], p["qty"]) or p.get("f_last") or p["f_in"]
            accrue(p, hist, fo)
            v = value(p, bk)
            if v:
                p["now"], p["f_last"] = round(v[0], 4), v[2]
            p["f7"] = per_day(hist, now)
            gone = coin not in spot or not c or c.get("in_delisting")
            weak = p["f7"] is not None and p["f7"] < R_OUT and now - p["t"] >= MIN_HOLD
            if (gone or weak) and v:
                close(coin, p, now, "монету снимают с торгов" if gone else
                      f"фандинг за 7 суток упал до {p['f7'] * 100:+.3f}% в сутки", v)
        except Exception as e:
            log.warning("xcarry %s: %s", coin, e)

def scan(now, tick, ctr, spot):
    free = N - len(S["pos"])
    M["skip"] = []
    if free <= 0:
        return
    cand = []
    for coin, q in tick.items():
        c = ctr.get(coin)
        if not c or coin in S["pos"] or coin not in spot or c.get("in_delisting"):
            continue
        iv = fnum(c.get("funding_interval"), 28800) or 28800
        day = fnum(q.get("funding_rate")) * 86400 / iv
        if day >= R_IN and fnum(q.get("volume_24h_quote")) >= MIN_VOL:
            cand.append((day, coin))
    rated = []
    for _, coin in sorted(cand, reverse=True)[:TOP]:
        try:
            f7 = per_day(fund_hist(coin), now)
        except Exception:
            continue
        if f7 is not None and f7 >= R_IN:
            rated.append((f7, coin))
    for f7, coin in sorted(rated, reverse=True):
        if free <= 0:
            break
        try:
            mult = fnum(ctr[coin].get("quanto_multiplier"))
            bk = books(coin, mult)
            buy = fill_usd(bk["sa"], USD)
            if not buy or not mult:
                M["skip"].append(f"{coin[:-5]}: мелкий стакан спота")
                continue
            qty, s_in = buy
            f_in, s_out, f_out = fill_qty(bk["fb"], qty), fill_qty(bk["sb"], qty), fill_qty(bk["fa"], qty)
            if not (f_in and s_out and f_out):
                M["skip"].append(f"{coin[:-5]}: мелкий стакан")
                continue
            basis = f_in / s_in - 1
            rt = (s_in / s_out - 1) + (f_out / f_in - 1) + 2 * (FEE_S + FEE_F)
            if basis < BASIS_MIN or basis > 0.03:
                M["skip"].append(f"{coin[:-5]}: фьючерс к споту {basis * 100:+.2f}%")
                continue
            if rt > MAX_RT:
                M["skip"].append(f"{coin[:-5]}: круг {rt * 100:.2f}% дороже {MAX_RT * 100:g}%")
                continue
            p = {"t": int(now), "qty": qty, "mult": mult, "s_in": s_in, "f_in": f_in, "usd": USD, "f7": f7, "f7_in": f7,
                 "fee_in": qty * (s_in * FEE_S + f_in * FEE_F), "fund": 0.0, "f_seen": int(now), "rt": rt, "n_pay": 0}
            with LOCK:
                S["pos"][coin] = p
            free -= 1
            mm.tg(f"💤 Фандинг (бумага): вошёл в {coin[:-5]} на ${USD:g} — спот {s_in:.6g} + шорт {f_in:.6g}. "
                  f"Фандинг за 7 суток {f7 * 100:+.3f}% в сутки, круг {rt * 100:.2f}%")
        except Exception as e:
            log.warning("xcarry вход %s: %s", coin, e)

def step(now):
    tick = {x["contract"]: x for x in get("/futures/usdt/tickers")}
    ctr = {x["name"]: x for x in get("/futures/usdt/contracts")}
    spot = {x["id"] for x in get("/spot/currency_pairs") if x.get("trade_status") == "tradable"}
    manage(now, ctr, spot)
    if now - M["scan"] >= SCAN_EVERY:
        scan(now, tick, ctr, spot)
        M["scan"] = now
    M["last"] = now
    save()

# ---------- отчёт ----------
def totals():
    now = time.time()
    with LOCK:
        pos = json.loads(json.dumps(S["pos"]))
        done = list(S["done"])
    cap = N * USD * (1 + 1 / LEV)
    closed = sum(d["pnl"] for d in done)
    open_now = sum(p.get("now", -p["fee_in"]) for p in pos.values())
    fund = sum(p["fund"] for p in pos.values()) + sum(d["fund"] for d in done)
    days = max((now - S.get("fixed_t", now)) / 86400, 1e-9)
    tot = closed + open_now
    return {"pos": pos, "done": done, "cap": cap, "closed": closed, "open": open_now, "fund": fund, "days": days,
            "tot": tot, "year": tot / cap / days * 365 if days >= 1 else None}

def text():
    T = totals()
    L = [f"💤 {VERSION} — БУМАГА «постоянно в сделке»: спот + шорт, доход — фандинг. {lt()}",
         f"До {N} монет по ${USD:g}; деньги на всё ${T['cap']:.0f}. Вход: фандинг за 7 суток от {R_IN * 100:g}% в сутки; "
         f"выход: ниже {R_OUT * 100:g}%",
         f"Итог за {T['days']:.1f} сут: {T['tot']:+.2f}$ (получено фандинга {T['fund']:+.2f}$, закрытые {T['closed']:+.2f}$, "
         f"открытые сейчас {T['open']:+.2f}$)"
         + (f" — это {T['year'] * 100:+.0f}% годовых" if T["year"] is not None else " — годовые посчитаю через сутки")]
    if not T["pos"]:
        L.append("Позиций нет — жду монеты с высоким фандингом.")
    for coin, p in sorted(T["pos"].items(), key=lambda kv: kv[1]["t"]):
        f7 = p.get("f7")
        L.append(f"  {coin[:-5]}: {(time.time() - p['t']) / 86400:.1f} сут, выплат {p.get('n_pay', 0)}, фандинг {p['fund']:+.2f}$, "
                 f"если закрыть сейчас {p.get('now', -p['fee_in']):+.2f}$"
                 + (f", ставка за 7 суток {f7 * 100:+.3f}% в сутки" if f7 is not None else ""))
    for d in T["done"][-5:]:
        L.append(f"  закрыта {d['coin'][:-5]} {lt(d['t1'])}: {d['pnl']:+.2f}$ за {(d['t1'] - d['t']) / 86400:.1f} сут ({d['why']})")
    if M["skip"]:
        L.append("Не взял: " + "; ".join(M["skip"][:6]))
    if M["err"]:
        L.append("⚠️ " + M["err"])
    return "\n".join(L)

def table():
    T = totals()
    return {"версия": VERSION, "деньги": round(T["cap"], 2), "итог": round(T["tot"], 2), "фандинг": round(T["fund"], 2),
            "суток": round(T["days"], 2), "позиции": T["pos"], "закрытые": T["done"], "не взял": M["skip"], "ошибка": M["err"]}

# ---------- запуск, база ----------
def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'carry_v1'", fetch=True)
    with LOCK:
        S.update(json.loads(r[0][0]) if r else {})
        S.setdefault("pos", {})
        S.setdefault("done", [])
        if not S.get("fixed_t"):
            S["fixed_t"] = int(time.time())
    S["loaded"] = 1

def save():
    with LOCK:
        js = json.dumps({k: v for k, v in S.items() if k != "loaded"})
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('carry_v1', %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (js,))

def loop():
    time.sleep(300)                                    # после остальных модулей
    while not S.get("loaded"):
        try:
            load()
        except Exception:
            log.exception("xcarry load")
            time.sleep(60)
    while True:
        t0 = time.time()
        try:
            step(t0)
            M["err"] = ""
        except Exception as e:
            M["err"] = f"Gate: {str(e)[:120]}"
            log.exception("xcarry step")
        time.sleep(max(60, STEP_EVERY - (time.time() - t0)))

def ok():
    return mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN

@mm.app.route("/carry")
def carry_page():
    if not ok():
        return "forbidden", 403
    return jsonify(table())

@mm.app.route("/carry/brief")
def carry_brief():
    if not ok():
        return "forbidden", 403
    txt = text()
    if request.args.get("tg") == "1":
        mm.tg(txt)
    return txt, 200, {"Content-Type": "text/plain; charset=utf-8"}

if ON:
    threading.Thread(target=loop, daemon=True).start()
