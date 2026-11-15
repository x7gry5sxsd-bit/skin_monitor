# xyoung.py — БУМАГА «ШОРТ МОЛОДЫХ ЛИСТИНГОВ». Ничего не торгует, ключи бирж не трогает.
#   ИДЕЯ. Новые монеты на фьючерсах Gate первые недели почти всегда дешевеют: продают получатели раздач и ранние
#   инвесторы, маркетмейкеры сбрасывают запас, интерес толпы уходит к следующему листингу. Если за первую неделю
#   монета уже упала, падение обычно продолжается ещё 2–4 недели. Этот эффект — общий для всех направлений рынка
#   (мемы, ИИ, DePIN, RWA, Perp DEX…): после 5–6 недель с листинга он пропадает.
#   ПРАВИЛО (записано до запуска и не меняется):
#   • берём только крипто-фьючерсы Gate (не акции и не металлы), не в делистинге;
#   • три захода по XYG_USD ($10): на 7-й, 14-й и 21-й день с листинга (XYG_DAYS), окно каждого — XYG_WIN_H (24 ч);
#   • заход открывается, только если цена ниже закрытия первых суток торгов хотя бы на XYG_DROP (10%);
#   • выход каждого захода: стоп — цена выросла на XYG_SL (100%) от входа; тейк — цена упала на XYG_TP (50%);
#     или прошло XYG_HOLD_D (30) суток; или монету снимают с торгов. Фандинг шорт получает или платит по истории биржи.
#   ПРОВЕРКА НА ИСТОРИИ (Gate, часовые цены 12.09.2025–02.11.2026, 113 молодых монет, 293 захода, издержки 0,3%
#   на круг, фандинг — по истории, где она есть): +16,2% на заход (медиана +36%), в плюсе 81%; первая половина
#   истории +20,6%, вторая +11,9%; по монетам +13,8% (t=3,3); 9 месяцев из 14 в плюсе, худший месяц −$38 при $10
#   на заход, наибольшая просадка −$74, одновременно до 56 заходов, держим в среднем 21 день.
#   Почему верим: эффект есть при входе на 2–28-й день (+3…+20%), слабеет к 35–45-му и исчезает к 60-му дню — это
#   свойство молодой монеты, а не подгонка под один день. Монет, снятых с торгов до ноября 2026, в данных нет; у
#   26 снятых позже шорт дал +31% против +7% у остальных — пропущенные скорее добавили бы, чем отняли.
#   Риск: редкие сквизы — монета удваивается (стоп −100% захода, таких 13%). Поэтому сумма захода маленькая.
#   ЧЕСТНАЯ БУМАГА: вход — по настоящему стакану на нужную сумму (если проскальзывание больше XYG_MAX_SLIP — пропуск);
#   стоп и тейк — по 5-минутным свечам биржи (стоп исполняется на 0,3% хуже); комиссия 0,05% за сторону.
#   /young?t=... — таблица, /young/brief?t=... — коротко (&tg=1 — в Telegram). XYG_ON=0 — выключить.
import os, time, json, threading, logging
import requests
from flask import jsonify, request
import mm_scanner as mm
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

VERSION = "xyoung v1 · 15.11"
ON = env("XYG_ON", "1") == "1"
USD = float(env("XYG_USD", "10"))                      # сумма одного захода, $
DAYS = tuple(int(x) for x in env("XYG_DAYS", "7,14,21").split(",") if x.strip())
WIN = float(env("XYG_WIN_H", "24")) * 3600             # окно захода после нужного возраста
DROP = float(env("XYG_DROP", "0.10"))                  # цена ниже закрытия первых суток хотя бы на 10%
SL = float(env("XYG_SL", "1.0"))                       # стоп: +100% к цене входа
TP = float(env("XYG_TP", "0.5"))                       # тейк: −50% к цене входа
HOLD = float(env("XYG_HOLD_D", "30")) * 86400
MAXP = int(env("XYG_MAX", "60"))                       # заходов одновременно
MAX_SLIP = float(env("XYG_MAX_SLIP", "0.02"))
FEE = float(env("XYG_FEE", "0.0005"))
STOP_SLIP = 0.003                                      # стоп — рыночная заявка, считаем на 0,3% хуже
STEP = 600
GT = "https://api.gateio.ws/api/v4"
log = logging.getLogger("xyoung")
S = {"loaded": 0, "coins": {}, "pos": {}, "done": [], "log": []}
M = {"err": "", "last": 0}
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

def crypto(x):
    """Крипто-фьючерс в USDT (не акции, не металлы, не индексы), торгуется, не в делистинге."""
    return (str(x.get("name", "")).endswith("_USDT") and not x.get("contract_type") and not x.get("in_delisting")
            and x.get("status", "trading") == "trading")

def launch(x):
    return int(fnum(x.get("launch_time")) or fnum(x.get("create_time")))

# ---------- цены ----------
def candles(name, t0, t1, iv):
    cs = get("/futures/usdt/candlesticks", contract=name, interval=iv, **{"from": int(t0), "to": int(t1)})
    return sorted(({"t": int(fnum(c.get("t"))), "h": fnum(c.get("h")), "l": fnum(c.get("l")), "c": fnum(c.get("c"))}
                   for c in cs or [] if fnum(c.get("c")) > 0), key=lambda c: c["t"])

def ref_price(name, t_launch):
    """Закрытие первых суток торгов = закрытие 24-й часовой свечи с листинга."""
    cs = candles(name, t_launch // 3600 * 3600, t_launch + 30 * 3600, "1h")
    return cs[23]["c"] if len(cs) >= 24 else None

def book(name, mult):
    b = get("/futures/usdt/order_book", contract=name, limit=50)
    lv = lambda rows: [(fnum(x.get("p")), fnum(x.get("s")) * mult) for x in rows or [] if fnum(x.get("p")) > 0]
    return lv(b.get("bids")), lv(b.get("asks"))

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

# ---------- заходы ----------
def note(c, text, now):
    S["log"] = (S["log"] + [{"t": int(now), "c": c, "x": text}])[-300:]

def decide(name, x, d, now, last):
    """Решение по заходу d-го дня. Возвращает текст для журнала."""
    k = S["coins"][name]
    if k.get("ref") is None:
        k["ref"] = ref_price(name, k["lt"])
    ref = k["ref"]
    if not ref:
        return "пропуск: нет свечей первых суток"
    if not last:
        return "пропуск: нет цены"
    drop = last / ref - 1
    if drop > -DROP:
        return f"пропуск: цена {drop * 100:+.0f}% к закрытию 1-х суток (нужно ≤ −{DROP * 100:g}%)"
    if len(S["pos"]) >= MAXP:
        return f"пропуск: уже {MAXP} заходов"
    mult = fnum(x.get("quanto_multiplier")) or 1.0
    bids, asks = book(name, mult)
    f = fill_usd(bids, USD)
    if not f or not asks:
        return "пропуск: мелкий стакан"
    qty, px = f
    mid = (bids[0][0] + asks[0][0]) / 2
    slip = (mid - px) / mid
    if slip > MAX_SLIP:
        return f"пропуск: проскальзывание {slip * 100:.2f}% > {MAX_SLIP * 100:g}%"
    key = f"{name}#{d}"
    p = {"c": name, "d": d, "t": int(now), "px": px, "qty": qty, "usd": USD, "ref": ref, "drop": drop, "mult": mult,
         "fee_in": qty * px * FEE, "fund": 0.0, "n_pay": 0, "f_seen": int(now), "f_chk": 0, "chk": int(now), "hi": px, "lo": px}
    with LOCK:
        S["pos"][key] = p
    mm.tg(f"🐣 Молодые листинги (бумага): шорт {name[:-5]} — заход {d}-го дня на ${USD:.0f} по {px:.6g}. Цена {drop * 100:+.0f}% "
          f"к закрытию первых суток. Стоп +{SL * 100:g}%, тейк −{TP * 100:g}%, держу до {HOLD / 86400:g} дней.")
    return f"ВХОД по {px:.6g} ({drop * 100:+.0f}% к 1-м суткам)"

def scan(now, ctr, tick):
    for name, x in ctr.items():
        if not crypto(x):
            continue
        t_l = launch(x)
        age = now - t_l
        if t_l <= 0 or age < min(DAYS) * 86400 or age > max(DAYS) * 86400 + WIN:
            continue
        k = S["coins"].setdefault(name, {"lt": t_l, "ref": None, "dec": {}})
        for d in DAYS:
            if str(d) in k["dec"] or not (d * 86400 <= age < d * 86400 + WIN):
                continue
            try:
                why = decide(name, x, d, now, fnum((tick.get(name) or {}).get("last")))
            except Exception as e:
                log.warning("xyoung %s: %s", name, e)
                continue                                # сеть подвела — попробуем на следующем шаге, окно ещё открыто
            k["dec"][str(d)] = why
            note(name[:-5], f"{d}-й день: {why}", now)
    old = [n for n, k in S["coins"].items() if now - k["lt"] > max(DAYS) * 86400 + WIN + 86400
           and not any(p["c"] == n for p in S["pos"].values())]
    for n in old:
        S["coins"].pop(n, None)

# ---------- позиции ----------
def pnl_at(p, px_out):
    return (p["px"] - px_out) * p["qty"] + p["fund"] - p["fee_in"] - p["qty"] * px_out * FEE

def close(key, p, now, why, px_out):
    pnl = pnl_at(p, px_out)
    d = dict(p, t1=int(now), why=why, px_out=px_out, pnl=round(pnl, 4))
    with LOCK:
        S["pos"].pop(key, None)
        S["done"] = (S["done"] + [d])[-1000:]
    mm.tg(f"🐣 Молодые листинги (бумага): закрыл {p['c'][:-5]} (заход {p['d']}-го дня) — {why}. "
          f"{pnl:+.2f}$ ({pnl / p['usd'] * 100:+.0f}%) за {(now - p['t']) / 86400:.0f} дн, фандинг {p['fund']:+.2f}$")

def accrue(p, now, last):
    """Фандинг: шорт получает ставку × стоимость позиции (при отрицательной ставке — платит)."""
    if now - p.get("f_chk", 0) < 3600:
        return
    fh = sorted((int(fnum(r.get("t"))), fnum(r.get("r"))) for r in get("/futures/usdt/funding_rate", contract=p["c"], limit=100))
    for t, r in fh:
        if t > p["f_seen"]:
            if t > p["t"]:
                p["fund"] += r * p["qty"] * (last or p["px"])
                p["n_pay"] += 1
            p["f_seen"] = t
    p["f_chk"] = int(now)

def touch(p, now):
    """Стоп и тейк по свечам с последней проверки. → (причина, цена выхода) или None."""
    gap = now - p["chk"]
    iv, step = ("5m", 300) if gap < 6 * 86400 else ("1h", 3600)
    for c in candles(p["c"], p["chk"] // step * step, now, iv):
        if c["t"] < p["t"] // step * step + (step if p["t"] % step else 0):
            continue                                    # свеча началась до входа — её максимум к нам не относится
        p["hi"], p["lo"] = max(p["hi"], c["h"]), min(p["lo"], c["l"])
        p["chk"] = c["t"]
        if c["h"] >= p["px"] * (1 + SL):
            return f"стоп: цена выросла на {SL * 100:g}%", p["px"] * (1 + SL) * (1 + STOP_SLIP)
        if c["l"] <= p["px"] * (1 - TP):
            return f"тейк: цена упала на {TP * 100:g}%", p["px"] * (1 - TP)
    return None

def manage(now, ctr, tick):
    full = len(ctr) > 100                               # список контрактов пришёл целиком
    for key, p in list(S["pos"].items()):
        try:
            x = ctr.get(p["c"])
            last = fnum((tick.get(p["c"]) or {}).get("last")) or p["px"]
            accrue(p, now, last)
            hit = touch(p, now)
            if hit:
                close(key, p, now, hit[0], hit[1])
                continue
            p["now"] = round(pnl_at(p, last), 4)
            gone = (x is None and full) or (x or {}).get("in_delisting")
            if gone or now - p["t"] >= HOLD:
                if p["c"] not in tick:                  # снятый контракт пропадает из общих списков — цену берём у него самого
                    d = get(f"/futures/usdt/contracts/{p['c']}") or {}
                    last = fnum(d.get("last_price")) or fnum(d.get("mark_price")) or last
                _, asks = book(p["c"], p["mult"]) if x else ([], [])
                px_out = fill_qty(asks, p["qty"]) or last
                close(key, p, now, "монету снимают с торгов" if gone else f"прошло {HOLD / 86400:g} дней", px_out)
        except Exception as e:
            log.warning("xyoung %s: %s", key, e)

def step(now):
    ctr = {x["name"]: x for x in get("/futures/usdt/contracts")}
    tick = {x["contract"]: x for x in get("/futures/usdt/tickers") if str(x.get("contract", "")).endswith("_USDT")}
    manage(now, ctr, tick)
    scan(now, ctr, tick)
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
    why = {}
    for d in done:
        k = d["why"].split(":")[0]
        why[k] = why.get(k, 0) + 1
    return {"pos": pos, "done": done, "closed": closed, "open": opened, "tot": closed + opened, "days": days,
            "win": sum(1 for d in done if d["pnl"] > 0), "why": why}

def text():
    T = totals()
    n7 = [x for x in S["log"] if time.time() - x["t"] < 7 * 86400]
    L = [f"🐣 {VERSION} — БУМАГА «шорт молодых листингов»: заходы на 7/14/21-й день, если цена ниже 1-х суток на {DROP * 100:g}%+. {lt()}",
         f"Заход ${USD:.0f}, стоп +{SL * 100:g}%, тейк −{TP * 100:g}%, до {HOLD / 86400:g} дней, заходов ≤{MAXP}",
         f"Итог за {T['days']:.1f} сут: {T['tot']:+.2f}$ (закрытые {T['closed']:+.2f}$ в {len(T['done'])} заходах, в плюсе {T['win']}; "
         f"открытые сейчас {T['open']:+.2f}$ в {len(T['pos'])})",
         "Выходы: " + (", ".join(f"{k} {v}" for k, v in T["why"].items()) if T["why"] else "ещё не было"),
         f"Решений за 7 дней: {len(n7)}, из них входов: {sum(1 for x in n7 if 'ВХОД' in x['x'])}"]
    for k, p in sorted(T["pos"].items(), key=lambda kv: kv[1].get("now", 0))[:12]:
        L.append(f"  {p['c'][:-5]} ({p['d']}-й день): {(time.time() - p['t']) / 86400:.0f} дн, сейчас {p.get('now', -p['fee_in']):+.2f}$, "
                 f"фандинг {p['fund']:+.2f}$")
    for d in T["done"][-5:]:
        L.append(f"  закрыт {d['c'][:-5]} ({d['d']}-й день) {lt(d['t1'])}: {d['pnl']:+.2f}$ — {d['why']}")
    for x in S["log"][-6:]:
        L.append(f"  {lt(x['t'])} {x['c']}: {x['x']}")
    if M["err"]:
        L.append("⚠️ " + M["err"])
    return "\n".join(L)

def table():
    T = totals()
    return {"версия": VERSION, "итог": round(T["tot"], 2), "закрытые": round(T["closed"], 2), "открытые": round(T["open"], 2),
            "суток": round(T["days"], 2), "выходы": T["why"], "позиции": T["pos"], "сделки": T["done"][-200:],
            "журнал": S["log"][-150:], "ошибка": M["err"]}

# ---------- база и цикл ----------
def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'young_v1'", fetch=True)
    with LOCK:
        S.update(json.loads(r[0][0]) if r else {})
        for k, d in (("coins", {}), ("pos", {}), ("done", []), ("log", [])):
            S.setdefault(k, d)
        if not S.get("fixed_t"):
            S["fixed_t"] = int(time.time())
    S["loaded"] = 1

def save():
    with LOCK:
        js = json.dumps({k: v for k, v in S.items() if k != "loaded"})
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('young_v1', %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (js,))

def loop():
    time.sleep(300)
    while not S.get("loaded"):
        try:
            load()
        except Exception:
            log.exception("xyoung load")
            time.sleep(60)
    while True:
        t0 = time.time()
        try:
            step(t0)
            M["err"] = ""
        except Exception as e:
            M["err"] = f"Gate: {str(e)[:120]}"
            log.exception("xyoung step")
        time.sleep(max(30, STEP - (time.time() - t0)))

def ok():
    return mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN

@mm.app.route("/young")
def young_page():
    if not ok():
        return "forbidden", 403
    return jsonify(table())

@mm.app.route("/young/brief")
def young_brief():
    if not ok():
        return "forbidden", 403
    txt = text()
    if request.args.get("tg") == "1":
        mm.tg(txt)
    return txt, 200, {"Content-Type": "text/plain; charset=utf-8"}

if ON:
    threading.Thread(target=loop, daemon=True).start()
