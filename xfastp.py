# xfastp.py — БУМАГА «БЫСТРАЯ СХЕМА». Ничего не торгует и ничего не меняет у других модулей — только считает.
#   Гипотеза записана здесь ДО подсчёта и не меняется:
#     шорт монеты, которая за час до входа уже упала на 1% и больше; тейк 1%, стоп 3%, не дольше 24 ч.
#   Сигналы — все события шорт-бумаги xshortp (кандидаты, окна, всплески, скан, аллокатор), в т. ч. те,
#   что живой не берёт. Свои замеры не нужны: xshortp уже пишет минуту первого касания −1% и +3% и ход через 4/24 ч.
#   ЧЕСТНАЯ ПРОВЕРКА (по разбору 29.10):
#   • в зачёт идут только события ПОСЛЕ фиксации (время первого запуска модуля, хранится в базе);
#     то, что было раньше, — справка, в зачёт не идёт;
#   • сравнение со случайными монетами в ту же минуту: (а) тоже уже падающими на 1%+ за час — главное сравнение,
#     (б) любыми — для справки;
#   • расходы: комиссия Gate 0,05% × 2 + проскальзывание 0,1% = 0,2% от позиции; фандинг учтён;
#   • «доказано» — только когда сделок 200+, средний $ на сделку в плюсе и выше, чем у случайных падающих,
#     больше чем на две погрешности (±), и при падающем, и при непадающем рынке схема не в минусе;
#   • скорость: за сколько минут половина сделок закрылась и какая доля — в первый час;
#   • рядом — та же схема с выходом через 4 ч и нынешняя схема живого (тейк 1%, стоп 5%) на тех же событиях.
#   /fastp?t=... — всё таблицей, /fastp/brief?t=... — коротко (&tg=1 — в Telegram); раз в день в 9:05 — в Telegram.
#   XF_ON=0 — выключить, XF_DAILY=0 — не слать, XF_MARGIN (10) — $ маржи для расчёта, плечо XF_LEV (10).
import os, sys, time, json, math, threading, logging, statistics
from flask import jsonify, request
import mm_scanner as mm
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

VERSION = "xfastp v1 · 29.10"
ON = env("XF_ON", "1") == "1"
DAILY = env("XF_DAILY", "1") == "1"
HOUR = int(env("XF_HOUR", "9"))
MARGIN = float(env("XF_MARGIN", "10"))
LEV = float(env("XF_LEV", "10"))
COST = 0.001 + float(env("XF_SLIP", "0.001"))           # комиссия туда-обратно + проскальзывание
FALL = 0.01                                            # «уже падает»: за час до входа −1% и больше
TP, SL, HOLD = 0.01, 0.03, 1440                        # схема: тейк 1%, стоп 3%, до 24 ч
N_PROOF = 200
SIGNALS = ("кандидат", "окно", "всплеск", "скан", "аллокатор")
log = logging.getLogger("xfastp")
S = {"loaded": 0}

def lt(t=None, f="%d.%m %H:%M"):
    return time.strftime(f, time.gmtime((time.time() if t is None else t) + 8 * 3600))

def sp():
    return sys.modules.get("xshortp")

def events():
    SP = sp()
    if not SP or not getattr(SP, "P", None):
        return []
    return list(SP.P.get("done", [])) + list(SP.P.get("open", []))

def falling(e):
    ch1 = (e.get("pre") or {}).get("ch1")
    return None if ch1 is None else ch1 <= -FALL

def market(e):
    SP = sp()
    m = SP.mkt(e) if SP and hasattr(SP, "mkt") else e.get("mk")
    return None if m is None else ("падает" if m <= -0.01 else "не падает")

def outcome(e, tp, sl, hold):
    """(ход шорта без плеча, "tp"/"sl"/"time", минут) или None — ещё не закончилась. Тейк и стоп в одну минуту —
    считаем стоп. Ни того ни другого за hold — закрытие по ходу цены через 4 или 24 ч."""
    if "tp" not in e or "sl" not in e:
        return None
    a, b = e["tp"].get(f"{tp:g}"), e["sl"].get(f"{sl:g}")
    a = a if a is not None and a <= hold else None
    b = b if b is not None and b <= hold else None
    if a is not None and (b is None or a < b):
        return tp, "tp", a
    if b is not None:
        return -sl, "sl", b
    r = (e.get("r") or {}).get("24" if hold >= 1440 else "4")
    return None if r is None else (r, "time", hold)

def usd(e, o):
    pos = MARGIN * LEV
    return pos * (o[0] - COST + (e.get("fund") or 0) * o[2] / 480)

def stats(es, tp=TP, sl=SL, hold=HOLD):
    rows = [(e, o) for e, o in ((e, outcome(e, tp, sl, hold)) for e in es) if o is not None]
    n = len(rows)
    if not n:
        return {"n": 0}
    v = [usd(e, o) for e, o in rows]
    m = sum(v) / n
    se = statistics.stdev(v) / math.sqrt(n) if n > 1 else None
    mins = sorted(o[2] for _, o in rows)
    return {"n": n, "тейков_%": round(100 * sum(1 for _, o in rows if o[1] == "tp") / n),
            "стопов_%": round(100 * sum(1 for _, o in rows if o[1] == "sl") / n),
            "usd": round(m, 2), "pm": None if se is None else round(se, 2), "сумма": round(sum(v), 2),
            "медиана_мин": round(statistics.median(mins)), "за_час_%": round(100 * sum(1 for x in mins if x <= 60) / n)}

def verdict(s, c, by_mkt):
    """Доказано / нет и почему — простыми словами."""
    if s["n"] < N_PROOF:
        return f"пока не доказано: сделок {s['n']} из {N_PROOF} нужных"
    if c.get("n", 0) < 30 or c.get("pm") is None or s.get("pm") is None:
        return "пока не доказано: мало случайных падающих монет для сравнения (нужно 30+)"
    gap, pm = s["usd"] - c["usd"], math.hypot(s["pm"], c["pm"])
    if s["usd"] <= 0:
        return f"не работает: в среднем {s['usd']:+.2f}$ на сделку"
    if gap < 2 * pm:
        return f"не доказано: лучше случайных на {gap:+.2f}$, а погрешность ±{pm:.2f}$ — разница может быть случайной"
    bad = [k for k, x in by_mkt.items() if x.get("n", 0) >= 30 and x["usd"] < 0]
    if bad:
        return f"не доказано: при рынке «{bad[0]}» схема в минусе"
    if any(x.get("n", 0) < 30 for x in by_mkt.values()):
        return "почти: лучше случайных, но нужно 30+ сделок и при падающем, и при непадающем рынке"
    return f"ДОКАЗАНО: лучше случайных на {gap:+.2f}$ на сделку (погрешность ±{pm:.2f}$)"

def table():
    fx = S.get("fixed_t")
    ev = [e for e in events() if falling(e) is not None]
    new = [e for e in ev if fx and e["t"] >= fx]
    old = [e for e in ev if not fx or e["t"] < fx]
    sig = lambda es: [e for e in es if e["kind"] in SIGNALS and falling(e)]
    ctl_fall = lambda es: [e for e in es if e["kind"] == "контроль" and falling(e)]
    ctl_all = lambda es: [e for e in es if e["kind"] == "контроль"]
    s, c, ca = stats(sig(new)), stats(ctl_fall(new)), stats(ctl_all(new))
    by_mkt = {k: stats([e for e in sig(new) if market(e) == k]) for k in ("падает", "не падает")}
    return {"версия": VERSION, "гипотеза": "шорт монеты, которая за час до входа уже упала на 1%+; тейк 1%, стоп 3%, до 24 ч",
            "зафиксирована": lt(fx) if fx else None, "ставка_$": MARGIN, "плечо": LEV, "расходы_%": round(COST * 100, 2),
            "схема": s, "случайные_падающие": c, "случайные_любые": ca, "по_рынку": by_mkt,
            "по_источникам": {k: stats([e for e in sig(new) if e["kind"] in ks])
                              for k, ks in (("скан", ("скан",)), ("окна", ("окно", "всплеск")),
                                            ("кандидаты", ("кандидат",)), ("аллокатор", ("аллокатор",)))},
            "выход_через_4ч": {"схема": stats(sig(new), hold=240), "случайные_падающие": stats(ctl_fall(new), hold=240)},
            "нынешняя_схема_живого_1_5": stats(sig(new), TP, 0.05, HOLD),
            "до_фиксации_справка": {"схема": stats(sig(old)), "случайные_падающие": stats(ctl_fall(old))},
            "вывод": verdict(s, c, by_mkt)}

def line(name, x):
    if not x or not x.get("n"):
        return f"  {name}: сделок пока нет"
    pm = f" ±{x['pm']:.2f}" if x.get("pm") is not None else ""
    return (f"  {name}: {x['n']} сд., тейков {x['тейков_%']}%, в среднем {x['usd']:+.2f}${pm}, "
            f"половина закрылась за {x['медиана_мин']} мин, за час — {x['за_час_%']}%")

def text():
    T = table()
    L = [f"⚡ {VERSION} — быстрая схема на бумаге, {lt()}",
         f"Правило: {T['гипотеза']}. Ставка ${MARGIN:g} × {LEV:g}, расходы {T['расходы_%']}% учтены.",
         f"Проверка идёт с {T['зафиксирована'] or '—'}; в зачёт только сделки после этого момента."]
    L.append(line("схема", T["схема"]))
    L.append(line("случайные монеты, тоже падающие", T["случайные_падающие"]))
    L.append(line("случайные монеты, любые", T["случайные_любые"]))
    for k, x in T["по_рынку"].items():
        L.append(line(f"рынок {k}", x))
    src = [f"{k} {x['n']} сд. {x['usd']:+.2f}$" for k, x in T["по_источникам"].items() if x.get("n")]
    if src:
        L.append("  по источникам: " + " · ".join(src))
    L.append(line("с выходом через 4 ч", T["выход_через_4ч"]["схема"]))
    L.append(line("нынешняя схема живого (тейк 1%, стоп 5%)", T["нынешняя_схема_живого_1_5"]))
    b = T["до_фиксации_справка"]["схема"]
    if b.get("n"):
        L.append(f"  до фиксации (справка, не в зачёт): {b['n']} сд., тейков {b['тейков_%']}%, {b['usd']:+.2f}$/сд.")
    L.append("Вывод: " + T["вывод"])
    return "\n".join(L)

# ---------- запуск, база, Telegram ----------
def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'fastp_v1'", fetch=True)
    S.update(json.loads(r[0][0]) if r else {})
    if not S.get("fixed_t"):                           # первый запуск — с этой минуты идёт честный зачёт
        S["fixed_t"] = int(time.time())
        save()
    S["loaded"] = 1

def save():
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('fastp_v1', %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""",
             (json.dumps({k: v for k, v in S.items() if k != "loaded"}),))

def send(txt):
    part = ""
    for ln in txt.split("\n"):
        if part and len(part) + len(ln) + 1 > 3800:
            mm.tg(part)
            part = ""
        part = (part + "\n" + ln) if part else ln[:3800]
    if part:
        mm.tg(part)

def tick(now):
    if not DAILY or not S.get("loaded"):
        return
    d, hm = lt(now, "%Y-%m-%d"), lt(now, "%H:%M")
    if hm >= f"{HOUR:02d}:05" and S.get("sent") != d:
        S["sent"] = d
        save()
        send(text())

def loop():
    time.sleep(300)
    while not S.get("loaded"):
        try:
            load()
        except Exception:
            log.exception("xfastp load")
            time.sleep(60)
    while True:
        try:
            tick(time.time())
        except Exception:
            log.exception("xfastp")
        time.sleep(60)

def ok():
    return mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN

@mm.app.route("/fastp")
def fastp_page():
    if not ok():
        return "forbidden", 403
    return jsonify(table())

@mm.app.route("/fastp/brief")
def fastp_brief():
    if not ok():
        return "forbidden", 403
    txt = text()
    if request.args.get("tg") == "1":
        send(txt)
    return txt, 200, {"Content-Type": "text/plain; charset=utf-8"}

if ON:
    threading.Thread(target=loop, daemon=True).start()
