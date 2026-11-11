# xcrowd.py — БУМАГА «толпа против крупных». Ничего не торгует.
#   Идея: Gate по каждому фьючерсу отдаёт, сколько счетов стоит в лонге и в шорте (толпа) и как стоят крупные
#   трейдеры (объём лонгов к шортам). Монеты, где толпа в лонге, а крупные в шорте, потом отстают; обратные —
#   опережают. Проверка 11.11 на 442 фьючерсах за 180 дней (отбор монет по данным на тот момент, расходы учтены):
#   15 лонгов + 15 шортов, пересборка раз в сутки — +0,78% корзины в день на первых 60% истории и +0,64% на
#   отложенных 40%, просадка до 26% корзины, по месяцам +14 +29 +37 +1 +44 −4 +6%. Истории всего полгода —
#   поэтому сначала бумага.
#   Раз в сутки в XCR_HOUR_UTC (0 = 08:00 Иркутск):
#   1) монеты — крипто-фьючерсы Gate с открытым интересом ≥ XCR_MIN_OI ($1 млн) и ≥ XCR_MIN_USERS (50) счетов;
#   2) сигнал = место по доле счетов в лонге минус место по объёму крупных в лонге;
#   3) чтобы не путать с обычным импульсом, монеты делятся на три группы по росту за 72 ч; в каждой группе
#      XCR_PER (5) монет с самым низким сигналом — в лонг, 5 с самым высоким — в шорт. Итого 15 + 15, равные доли;
#   4) вчерашняя корзина закрывается по текущим ценам: итог дня = средний ход лонгов минус средний ход шортов
#      минус расходы XCR_COST (0,1%) с каждой стороны оборота.
#   $ считаются при XCR_USD ($30) на позицию. Стопов на отдельные позиции в бумаге нет — худшая позиция дня пишется.
#   Итог в Telegram после пересборки. Страницы (?t=STATS_TOKEN): /crowd, /crowd/brief. XCR_ON=0 — выкл.
import os, time, json, threading, logging
import requests
from flask import jsonify, request
import mm_scanner as mm
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

VERSION = "xcrowd v1 · 11.11"
ON = env("XCR_ON", "1") == "1"
HOUR_UTC = int(env("XCR_HOUR_UTC", "0"))
PER = max(1, int(env("XCR_PER", "5")))                   # монет в лонг и в шорт из каждой трети
MIN_OI = float(env("XCR_MIN_OI", "1000000"))
MIN_USERS = int(env("XCR_MIN_USERS", "50"))
USD = float(env("XCR_USD", "30"))                        # $ на позицию — только для пересчёта в деньги
COST = float(env("XCR_COST", "0.001"))                   # расходы с оборота одной стороны сделки
GATE = "https://api.gateio.ws/api/v4/futures/usdt"
log = logging.getLogger("xcrowd")
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0"})
L = {}
LOCK = threading.RLock()

def lt(t=None, f="%d.%m %H:%M"):
    return time.strftime(f, time.gmtime((time.time() if t is None else t) + 8 * 3600))

def say(text):
    mm.tg("🧪 Толпа против крупных [бумага]: " + text)

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'xcrowd_v1'", fetch=True)
    L.update(json.loads(r[0][0]) if r else {})
    for k, v in (("port", None), ("days", []), ("day", ""), ("last_err", "")):
        L.setdefault(k, v)
    L["loaded"] = 1

def save():
    if not L.get("loaded"):
        return
    try:
        xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('xcrowd_v1', %s)
                    ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(L),))
    except Exception:
        log.exception("xcrowd save")

# ---------- данные ----------
def prices():
    """{монета: последняя цена} по всем фьючерсам Gate — один запрос."""
    return {x["contract"][:-5]: float(x["last"]) for x in S.get(f"{GATE}/tickers", timeout=30).json()
            if str(x.get("contract", "")).endswith("_USDT") and float(x.get("last") or 0) > 0}

def candidates():
    """Крипто-фьючерсы с открытым интересом около порога и выше (по тикерам — чтобы не опрашивать все)."""
    mult = {c["name"]: float(c.get("quanto_multiplier") or 0) for c in S.get(f"{GATE}/contracts", timeout=60).json()
            if not c.get("contract_type") and not c.get("in_delisting") and str(c.get("name", "")).endswith("_USDT")}
    out = []
    for x in S.get(f"{GATE}/tickers", timeout=30).json():
        n = x.get("contract", "")
        if n in mult:
            oi = float(x.get("total_size") or 0) * mult[n] * float(x.get("mark_price") or x.get("last") or 0)
            if oi >= MIN_OI * 0.7:
                out.append(n[:-5])
    return out

def stats(coin):
    """Последняя строка статистики позиций и цена 72 ч назад; None — данных нет."""
    for k in range(3):
        try:
            r = S.get(f"{GATE}/contract_stats", params={"contract": f"{coin}_USDT", "interval": "1h", "limit": 74}, timeout=20)
            if r.status_code == 200:
                d = r.json()
                break
            if r.status_code == 400:
                return None
        except Exception:
            pass
        time.sleep(1 + k)
    else:
        return None
    if not isinstance(d, list) or len(d) < 73:
        return None
    a, b = d[-1], d[-73]
    try:
        crowd, whale = float(a["lsr_account"]), float(a["top_lsr_size"])
        users = float(a.get("long_users") or 0) + float(a.get("short_users") or 0)
        oi, p1, p0 = float(a.get("open_interest_usd") or 0), float(a["mark_price"]), float(b["mark_price"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (crowd > 0 and whale > 0 and p0 > 0 and p1 > 0):
        return None
    return {"crowd": crowd, "whale": whale, "users": users, "oi": oi, "r72": p1 / p0 - 1}

def pct_rank(vals):
    """Место каждого значения в долях (0…1], равные делят место поровну."""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    out, i = [0.0] * len(vals), 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = ((i + j) / 2 + 1) / len(vals)
        i = j + 1
    return out

def pick(rows):
    """rows: {монета: stats}. Вернёт (лонги, шорты, сигнал по монетам)."""
    coins = sorted(rows)
    ra, rb = pct_rank([rows[c]["crowd"] for c in coins]), pct_rank([rows[c]["whale"] for c in coins])
    sig = {c: ra[i] - rb[i] for i, c in enumerate(coins)}
    by_mom = sorted(coins, key=lambda c: rows[c]["r72"])
    n = len(by_mom) // 3
    longs, shorts = [], []
    for g in (by_mom[:n], by_mom[n:2 * n], by_mom[2 * n:]):
        g = sorted(g, key=lambda c: (sig[c], c))
        longs += g[:PER]
        shorts += g[-PER:]
    return longs, shorts, sig

# ---------- сутки ----------
def close_day(port, px, now):
    """Итог закрываемой корзины, доли корзины."""
    lr = [px.get(c, p0) / p0 - 1 for c, p0 in port["long"].items()]
    sr = [-(px.get(c, p0) / p0 - 1) for c, p0 in port["short"].items()]
    uni = [px[c] / p0 - 1 for c, p0 in (port.get("uni") or {}).items() if c in px]
    pos = [(c, px.get(c, p0) / p0 - 1) for c, p0 in port["long"].items()] + \
          [(c, -(px.get(c, p0) / p0 - 1)) for c, p0 in port["short"].items()]
    worst = min(pos, key=lambda x: x[1])
    gross = sum(lr) / len(lr) + sum(sr) / len(sr)
    return {"d": lt(now, "%Y-%m-%d"), "t": int(now), "hours": round((now - port["t"]) / 3600, 1),
            "long": round(sum(lr) / len(lr), 5), "short": round(sum(sr) / len(sr), 5), "gross": round(gross, 5),
            "cost": round(port.get("cost", 0), 5), "pnl": round(gross - port.get("cost", 0), 5),
            "mkt": round(sum(uni) / len(uni), 5) if uni else None, "worst": [worst[0], round(worst[1], 4)],
            "n": port.get("n", 0)}

def rebalance(now):
    rows = {}
    for c in candidates():
        s = stats(c)
        if s and s["oi"] >= MIN_OI and s["users"] >= MIN_USERS:
            rows[c] = s
        time.sleep(0.12)
    if len(rows) < 3 * (2 * PER + 2):
        raise RuntimeError(f"подходящих монет {len(rows)} — мало для корзины {3 * PER} + {3 * PER}")
    px = prices()
    rows = {c: v for c, v in rows.items() if c in px}
    longs, shorts, sig = pick(rows)
    old = L.get("port")
    day = close_day(old, px, now) if old else None
    w_old = {}
    if old:
        w_old.update({c: 1 / len(old["long"]) for c in old["long"]})
        w_old.update({c: -1 / len(old["short"]) for c in old["short"]})
    w_new = {c: 1 / len(longs) for c in longs}
    w_new.update({c: -1 / len(shorts) for c in shorts})
    turn = sum(abs(w_new.get(c, 0) - w_old.get(c, 0)) for c in set(w_new) | set(w_old))
    L["port"] = {"t": int(now), "long": {c: px[c] for c in longs}, "short": {c: px[c] for c in shorts},
                 "uni": {c: px[c] for c in rows}, "n": len(rows), "cost": round(turn * COST, 5), "turn": round(turn, 3),
                 "sig": {c: round(sig[c], 3) for c in longs + shorts}}
    if day:
        L["days"] = (L["days"] + [day])[-400:]
    return day

def totals():
    ds = L.get("days") or []
    cum, peak, dd = 0.0, 0.0, 0.0
    for d in ds:
        cum += d["pnl"]
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    return {"дней": len(ds), "итог": cum, "просадка": dd, "в_плюсе": sum(1 for d in ds if d["pnl"] > 0),
            "лучший": max((d["pnl"] for d in ds), default=0), "худший": min((d["pnl"] for d in ds), default=0)}

def usd(x):
    return x * USD * 3 * PER                               # доля корзины → $ (корзина = позиций в одной стороне × $)

def summary():
    t = totals()
    if not t["дней"]:
        return "закрытых суток пока нет"
    return (f"суток {t['дней']}, в плюсе {t['в_плюсе']} | всего {t['итог'] * 100:+.2f}% корзины = {usd(t['итог']):+.2f}$ "
            f"(при ${USD:.0f} на позицию, корзина ${USD * 3 * PER:.0f}) | просадка {t['просадка'] * 100:.1f}% = "
            f"{usd(t['просадка']):.2f}$ | лучшие сутки {t['лучший'] * 100:+.2f}%, худшие {t['худший'] * 100:+.2f}%")

def brief():
    out = [f"🧪 {VERSION} — бумага, {lt(None)}. {3 * PER} лонгов + {3 * PER} шортов, пересборка раз в сутки в "
           f"{(HOUR_UTC + 8) % 24:02d}:00 (Иркутск)", summary()]
    p = L.get("port")
    if p:
        try:
            px = prices()
            lr = [px.get(c, p0) / p0 - 1 for c, p0 in p["long"].items()]
            sr = [-(px.get(c, p0) / p0 - 1) for c, p0 in p["short"].items()]
            cur = sum(lr) / len(lr) + sum(sr) / len(sr) - p.get("cost", 0)
            out.append(f"Текущая корзина с {lt(p['t'])} (монет в отборе {p.get('n')}): пока {cur * 100:+.2f}% = {usd(cur):+.2f}$ "
                       f"(лонги {sum(lr) / len(lr) * 100:+.2f}%, шорты {sum(sr) / len(sr) * 100:+.2f}%)")
        except Exception:
            out.append(f"Текущая корзина с {lt(p['t'])}: цены сейчас не получил")
        out.append("  лонг: " + ", ".join(p["long"]))
        out.append("  шорт: " + ", ".join(p["short"]))
    for d in (L.get("days") or [])[-7:]:
        out.append(f"  {d['d']}: {d['pnl'] * 100:+.2f}% = {usd(d['pnl']):+.2f}$ (лонги {d['long'] * 100:+.2f}%, шорты "
                   f"{d['short'] * 100:+.2f}%, расходы {d['cost'] * 100:.2f}%"
                   + (f", средняя монета {d['mkt'] * 100:+.2f}%" if d.get("mkt") is not None else "")
                   + f"; худшая позиция {d['worst'][0]} {d['worst'][1] * 100:+.1f}%)")
    if L.get("last_err"):
        out.append(f"последняя ошибка: {L['last_err']}")
    return "\n".join(out)

def step(now):
    d = time.strftime("%Y-%m-%d", time.gmtime(now))
    if L.get("day") == d or time.gmtime(now).tm_hour < HOUR_UTC:
        return
    try:
        day = rebalance(now)
    except Exception as x:
        log.exception("xcrowd rebalance")
        L["last_err"] = f"{lt(now)}: {str(x)[:150]}"
        if now - L.get("err_t", 0) > 6 * 3600:
            L["err_t"] = now
            say(f"не собрал корзину ({str(x)[:120]}) — попробую снова через 30 мин")
        L["retry_t"] = now
        return
    L["day"], L["last_err"] = d, ""
    p = L["port"]
    head = (f"сутки закрыты: {day['pnl'] * 100:+.2f}% корзины = {usd(day['pnl']):+.2f}$ (лонги {day['long'] * 100:+.2f}%, "
            f"шорты {day['short'] * 100:+.2f}%; худшая позиция {day['worst'][0]} {day['worst'][1] * 100:+.1f}%). "
            f"Всего: {summary()}\n" if day else f"запущен ({VERSION}), ничего не торгует. Первая корзина собрана.\n")
    say(head + f"Новая корзина (отбор из {p['n']} монет): лонг — {', '.join(p['long'])}; шорт — {', '.join(p['short'])}")
    save()

def loop():
    time.sleep(150)
    while not L.get("loaded"):
        try:
            load()
        except Exception:
            log.exception("xcrowd load")
            time.sleep(60)
    while True:
        try:
            now = time.time()
            if now - L.get("retry_t", 0) >= 1800:
                with LOCK:
                    step(now)
        except Exception:
            log.exception("xcrowd")
        time.sleep(300)

def ok():
    return mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN

@mm.app.route("/crowd")
def crowd_page():
    if not ok():
        return "forbidden", 403
    with LOCK:
        return jsonify({"версия": VERSION, "итог": summary(), "корзина": L.get("port"), "сутки": (L.get("days") or [])[-120:],
                        "настройки": {"монет_в_стороне": 3 * PER, "открытый_интерес_от_$": MIN_OI, "счетов_от": MIN_USERS,
                                      "$_на_позицию": USD, "расходы_с_оборота_стороны_%": COST * 100,
                                      "час_пересборки_utc": HOUR_UTC}})

@mm.app.route("/crowd/brief")
def crowd_brief():
    if not ok():
        return "forbidden", 403
    with LOCK:
        txt = brief()
    if request.args.get("tg") == "1":
        mm.tg(txt)
    return txt, 200, {"Content-Type": "text/plain; charset=utf-8"}

if ON:
    threading.Thread(target=loop, daemon=True).start()
