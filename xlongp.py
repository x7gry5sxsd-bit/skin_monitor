# xlongp.py — БУМАГА «ЛОНГ ПО ОБРАТНЫМ ОКНАМ». Ничего не торгует: только записывает, что было бы.
#   Зеркало шортов. Шорт: на HTX дороже — монету уже продают на других биржах, HTX отстаёт. Лонг: на HTX ДЕШЕВЛЕ —
#   монету уже покупают на других биржах (KuCoin, Gate), HTX отстаёт → цена должна подтянуться вверх.
#   Сигналы (из бумаги магазина xshopp, пары «МОНЕТА:биржа:rev» — окна по полным стаканам):
#     • обр_окно — бумажная сделка «купить на HTX, продать у партнёра» прямо сейчас;
#     • обр_всплеск — таких сделок 3+ за 20 мин в 2+ десятиминутках;
#     • обр_кандидат — обратное направление монеты за сутки даёт ≥ XLP_CAND_RATE $/сут (раз в 24 ч на монету).
#   На каждый сигнал — случайная монета в ту же минуту (контроль: лонг «наугад»).
#   Цены — фьючерсы Gate (там торговал бы лонг). Для каждого события: первые касания +1/2/3/5% и −1/2/3/5% (минуты),
#   ход через 1/4/24 ч, рынок Gate за сутки в момент сигнала (растёт ≥ +1%, падает ≤ −1%, иначе ровный).
#   Итоги — сделки «тейк / стоп» на $XLP_MARGIN × хXLP_LEV с комиссией и фандингом, по типам сигнала и по рынку.
#   Страницы (?t=STATS_TOKEN): /longp — всё (JSON), /longp/brief — коротко (&tg=1 — в Telegram).
#   XLP_ON=0 — выключить. Раз в сутки — короткий итог в Telegram (XLP_DAILY=0 — не слать).
import os, sys, time, json, random, statistics, threading, logging
from contextlib import nullcontext
from flask import jsonify, request
import mm_scanner as mm
import xpaper
import xexec_api as api

def env(k, d=""):
    return os.getenv(k, d).strip()

VERSION = "xlongp v1 · 27.10"
ON = env("XLP_ON", "1") == "1"
DAILY = env("XLP_DAILY", "1") == "1"
COOL = float(env("XLP_COOL_H", "2")) * 3600            # окно/всплеск по монете — не чаще раза в 2 ч
CAND_RATE = float(env("XLP_CAND_RATE", "0.5"))         # обр_кандидат: обратное направление ≥ $0,5 в сутки
MARGIN = float(env("XLP_MARGIN", "10"))
LEV = int(float(env("XLP_LEV", "10")))
FEE = float(env("XLP_FEE", "0.001"))                   # комиссия Gate туда-обратно от объёма
UPS, DNS, HOR = [0.01, 0.02, 0.03, 0.05], [0.01, 0.02, 0.03, 0.05], [1, 4, 24]
PAIRS = [(0.01, 0.05), (0.02, 0.05), (0.02, 0.03), (0.03, 0.05)]   # тейк / стоп
KINDS = ("обр_окно", "обр_всплеск", "обр_кандидат")
CTRL = "контроль"
GTF = "https://api.gateio.ws/api/v4/futures/usdt"
log = logging.getLogger("xlongp")
P = {}
R = {"prev": {}, "fb": {}, "diag": {}}                 # в памяти: прошлые счётчики пар, десятиминутки, диагностика
LOCK = threading.RLock()

def lt(t=None, f="%H:%M"):
    return time.strftime(f, time.gmtime((time.time() if t is None else t) + 8 * 3600))

# ---------- состояние ----------
def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'longp_v1'", fetch=True)
    P.update(json.loads(r[0][0]) if r else {})
    for k, v in (("open", []), ("done", []), ("cool", {}), ("ccool", {}), ("rep_t", time.time()),
                 ("since", time.time())):
        P.setdefault(k, v)
    P["loaded"] = 1

def save():
    if not P.get("loaded"):
        return
    try:
        xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('longp_v1', %s)
                    ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(P),))
    except Exception:
        log.exception("xlongp save")

# ---------- цены Gate ----------
def tickers():
    """Фьючерсы Gate: {МОНЕТА: {last, fund, chg}}."""
    out = {}
    for x in api.S.get(f"{GTF}/tickers", timeout=20).json() or []:
        c = x.get("contract", "")
        if c.endswith("_USDT") and float(x.get("last") or 0) > 0:
            try:
                chg = float(x.get("change_percentage")) / 100
            except (TypeError, ValueError):
                chg = None
            out[c[:-5]] = {"last": float(x["last"]), "fund": float(x.get("funding_rate") or 0), "chg": chg}
    return out

def market(tk):
    ch = [q["chg"] for q in tk.values() if q.get("chg") is not None and abs(q["chg"]) < 5]
    return statistics.median(ch) if len(ch) >= 20 else None

def regime(m):
    return "?" if m is None else "растёт" if m >= 0.01 else "падает" if m <= -0.01 else "ровный"

# ---------- сигналы из бумаги магазина ----------
def rev_pairs():
    """Пары обратного направления из xshopp: [(ключ, последние строки по часам [час, проверок, ?, сделок])]."""
    SPm = sys.modules.get("xshopp")
    if not SPm or not getattr(SPm, "S", None):
        return None, []
    with (getattr(SPm, "LOCK", None) or nullcontext()):
        items = [(k, [list(r[:4]) for r in (c.get("h") or [])[-3:]])
                 for k, c in list((SPm.S.get("p") or {}).items()) if k.endswith(":rev")]
    return SPm, items

def rev_new(now):
    """Новые бумажные сделки «купить на HTX, продать у партнёра» с прошлой проверки: {монета: [сделок, партнёр]}."""
    SPm, items = rev_pairs()
    out = {}
    for k, rows in items:
        if not rows:
            continue
        last = R["prev"].get(k)
        R["prev"][k] = (rows[-1][0], rows[-1][3])
        if not last:                                   # первая проверка после запуска — только запомнить
            continue
        new = sum(r[3] for r in rows if r[0] > last[0]) + sum(r[3] - last[1] for r in rows if r[0] == last[0])
        if new > 0:
            coin, v = k.split(":")[0], k.split(":")[1]
            x = out.setdefault(coin, [0, v])
            x[0] += new
    R["diag"].update(rev_pairs=len(items), xshopp=bool(SPm))
    b = str(int(now // 600) * 600)                     # десятиминутки для «всплеска»
    for coin, (n, _) in out.items():
        R["fb"].setdefault(coin, {})[b] = R["fb"].get(coin, {}).get(b, 0) + n
    for coin in list(R["fb"]):
        R["fb"][coin] = {k: v for k, v in R["fb"][coin].items() if float(k) >= now - 3600}
        if not R["fb"][coin]:
            R["fb"].pop(coin)
    return out

def burst(coin, now):
    """(сделок за 20 мин, сколько десятиминуток со сделками)."""
    bs = [v for k, v in R["fb"].get(coin, {}).items() if float(k) + 600 > now - 20 * 60]
    return sum(bs), len(bs)

def rev_cands(now):
    """Монеты, у которых обратное направление за сутки даёт ≥ CAND_RATE $/сут (бумага магазина)."""
    SPm, items = rev_pairs()
    if not SPm or not hasattr(SPm, "summary"):
        return []
    out = {}
    with (getattr(SPm, "LOCK", None) or nullcontext()):
        for k, _ in items:
            try:
                s = SPm.summary(k, 24)
            except Exception:
                s = None
            if s and s.get("hours", 0) >= 6 and (s.get("net_usd_day") or 0) >= CAND_RATE:
                coin = k.split(":")[0]
                out[coin] = max(out.get(coin, 0), s["net_usd_day"])
    return sorted(out.items(), key=lambda kv: -kv[1])

# ---------- события ----------
def event(coin, kind, now, tk, mk, extra=None):
    q = tk[coin]
    e = {"coin": coin, "kind": kind, "t": int(now), "px": q["last"], "fund": q["fund"], "mkt": mk,
         "ch24": q.get("chg"), "hi": q["last"], "lo": q["last"], "up": {}, "dn": {}, "r": {}, "v": 1}
    if extra:
        e.update(extra)
    return e

def control(coin, now, tk, mk):
    """Случайная монета из того же списка (пары бумаги магазина), что есть на фьючерсах Gate."""
    SPm, items = rev_pairs()
    pool = sorted({k.split(":")[0] for k, _ in items} & set(tk) - {coin}) or sorted(set(tk) - {coin})
    if pool:
        P["open"].append(event(random.choice(pool), CTRL, now, tk, mk, {"для": coin}))

def step(now, tk):
    mk = market(tk)
    R["diag"].update(mkt=mk, t=now, gate=len(tk))
    new = rev_new(now)
    for coin, (n, v) in new.items():                   # 1) обратные окна и всплески
        if coin not in tk:
            R["diag"]["no_gate"] = R["diag"].get("no_gate", 0) + 1
            continue
        if now - P["cool"].get(coin, 0) < COOL or len(P["open"]) >= 300:
            continue
        t20, k = burst(coin, now)
        P["cool"][coin] = now
        P["open"].append(event(coin, "обр_всплеск" if t20 >= 3 and k >= 2 else "обр_окно", now, tk, mk,
                               {"партнёр": v, "сделок": n}))
        control(coin, now, tk, mk)
    if now - R.get("cand_t", 0) >= 300:                # 2) обратные кандидаты — раз в 5 мин
        R["cand_t"] = now
        for coin, rate in rev_cands(now)[:5]:          # не больше 5 за раз (после запуска их может быть много)
            if coin in tk and now - P["ccool"].get(coin, 0) >= 24 * 3600 and len(P["open"]) < 300:
                P["ccool"][coin] = now
                P["open"].append(event(coin, "обр_кандидат", now, tk, mk, {"бумага_$_сут": rate}))
                control(coin, now, tk, mk)
    still = []                                         # 3) ведём открытые
    for e in P["open"]:
        q = tk.get(e["coin"])
        age = now - e["t"]
        if q:
            px, mins = q["last"], max(1, round(age / 60))
            e["hi"], e["lo"] = max(e["hi"], px), min(e["lo"], px)
            for x in UPS:
                if px >= e["px"] * (1 + x) and str(x) not in e["up"]:
                    e["up"][str(x)] = mins
            for x in DNS:
                if px <= e["px"] * (1 - x) and str(x) not in e["dn"]:
                    e["dn"][str(x)] = mins
            for h in HOR:
                if age >= h * 3600 and str(h) not in e["r"]:
                    e["r"][str(h)] = round(px / e["px"] - 1, 5)
        if "24" in e["r"] or age > 26 * 3600:
            P["done"].append(e)
        else:
            still.append(e)
    P["open"] = still
    P["done"] = P["done"][-800:]
# ---------- итоги ----------
def res(e, tp, sl):
    """(доход лонга, минут) с тейком tp и стопом sl; по времени — ход за 24 ч; None — ещё идёт."""
    a, b = e["up"].get(str(tp)), e["dn"].get(str(sl))
    if a is not None and (b is None or a < b):
        return tp, a
    if b is not None:
        return -sl, b
    if "24" in e["r"]:
        return max(e["r"]["24"], -sl), 1440
    return None

def usd(ret, mins, fund):
    """$ на сделку: маржа × плечо × (ход − комиссия − фандинг за время удержания; лонг платит положительный)."""
    return MARGIN * LEV * (ret - FEE - (fund or 0) * mins / 480)

def _all():
    return P.get("done", []) + P.get("open", [])

def pick(kind=None, reg=None, src=None):
    es = _all() if src is None else src
    if kind == "сигналы":
        es = [e for e in es if e["kind"] != CTRL]
    elif kind:
        es = [e for e in es if e["kind"] == kind]
    if reg:
        es = [e for e in es if regime(e.get("mkt")) == reg]
    return es

def table(es):
    """Итог по парам тейк/стоп: сделок, тейков %, стопов %, нужно тейков %, $ всего."""
    out = {}
    for tp, sl in PAIRS:
        rs = [(res(e, tp, sl), e) for e in es]
        rs = [(r, e) for r, e in rs if r]
        if not rs:
            continue
        n = len(rs)
        win = sum(1 for r, _ in rs if r[0] == tp)
        loss = sum(1 for r, _ in rs if r[0] == -sl)
        out[f"тейк {tp * 100:g}% / стоп {sl * 100:g}%"] = {
            "сделок": n, "тейков %": round(win / n * 100), "стопов %": round(loss / n * 100),
            "нужно тейков для нуля %": round((sl + FEE) / (tp + sl) * 100),
            f"$ на ${MARGIN:g} × х{LEV}": round(sum(usd(r[0], r[1], e.get("fund")) for r, e in rs), 2)}
    return out

def horizons(es):
    out = {}
    for h in HOR:
        rs = [e["r"][str(h)] for e in es if str(h) in e["r"]]
        if rs:
            out[f"{h}ч"] = {"событий": len(rs), "средний ход лонга %": round(sum(rs) / len(rs) * 100, 2),
                           "в плюсе %": round(sum(1 for r in rs if r > 0) / len(rs) * 100)}
    return out

def line(es, tp, sl):
    t = table(es).get(f"тейк {tp * 100:g}% / стоп {sl * 100:g}%")
    if not t:
        return "итогов пока нет"
    return (f"{t['сделок']} сд., тейков {t['тейков %']}% (нужно {t['нужно тейков для нуля %']}%), "
            f"{t[f'$ на ${MARGIN:g} × х{LEV}']:+.2f}$")

def brief():
    mk = R["diag"].get("mkt")
    out = [f"📈 {VERSION} — бумага «лонг по обратным окнам» (на HTX дешевле), {lt(None, '%d.%m %H:%M')}",
           f"Ничего не торгует. Сделка на бумаге: ${MARGIN:g} × х{LEV}, комиссия {FEE * 100:g}%, фандинг учтён"]
    if not P.get("loaded"):
        return "\n".join(out + ["Ещё загружается (первые ~4 мин после запуска)"])
    if mk is not None:
        out.append(f"Рынок Gate за сутки: {mk * 100:+.1f}% ({regime(mk)})")
    sig, ctl = pick("сигналы"), pick(CTRL)
    by = {k: len(pick(k)) for k in KINDS}
    out.append(f"С {lt(P.get('since'), '%d.%m %H:%M')}: сигналов {len(sig)} (окна {by['обр_окно']}, всплески "
               f"{by['обр_всплеск']}, кандидаты {by['обр_кандидат']}), случайных {len(ctl)} · открыто "
               f"{len(P.get('open', []))}")
    if not sig:
        d = R["diag"]
        out.append("Сигналов пока нет: " + ("бумага магазина (xshopp) не загружена" if not d.get("xshopp") else
                                            f"обратных окон ещё не было (пар под наблюдением: {d.get('rev_pairs', 0)})"))
    hs, hc = horizons(sig), horizons(ctl)
    if hs:
        out.append("Ход лонга, сигналы / случайные: " + " · ".join(
            f"{h} {v['средний ход лонга %']:+.2f}% / " + (f"{hc[h]['средний ход лонга %']:+.2f}%" if h in hc else "—")
            for h, v in hs.items()))
    for tp, sl in ((0.01, 0.05), (0.02, 0.05)):
        out.append(f"Тейк {tp * 100:g}% / стоп {sl * 100:g}%: сигналы — {line(sig, tp, sl)} | случайные — "
                   f"{line(ctl, tp, sl)}")
    regs = [f"{rg} — {line(pick('сигналы', rg), 0.02, 0.05)}" for rg in ("растёт", "ровный", "падает")
            if pick("сигналы", rg)]
    if regs:
        out.append("По рынку (тейк 2% / стоп 5%, сигналы): " + "; ".join(regs))
    last = [e for e in _all() if e["kind"] != CTRL][-5:]
    if last:
        out.append("Последние: " + "; ".join(
            f"{e['coin']} {lt(e['t'])} {e['kind'].replace('обр_', '')} (рынок {regime(e.get('mkt'))})" for e in last))
    out.append("Вывод делать после 30+ закрытых сигналов, и только если сигналы заметно лучше случайных монет")
    return "\n".join(out)

def page():
    sig, ctl = pick("сигналы"), pick(CTRL)
    return {"версия": VERSION, "с": lt(P.get("since"), "%d.%m %H:%M"), "рынок_за_сутки_%":
            None if R["diag"].get("mkt") is None else round(R["diag"]["mkt"] * 100, 2),
            "настройки": {"маржа_$": MARGIN, "плечо": LEV, "комиссия_%": FEE * 100, "пауза_по_монете_ч": COOL / 3600,
                          "кандидат_от_$_сут": CAND_RATE},
            "сигналы": {"итог": table(sig), "ход": horizons(sig)},
            "случайные": {"итог": table(ctl), "ход": horizons(ctl)},
            "по_типам": {k: {"итог": table(pick(k)), "ход": horizons(pick(k))} for k in KINDS},
            "по_рынку": {rg: {"сигналы": table(pick("сигналы", rg)), "случайные": table(pick(CTRL, rg))}
                         for rg in ("растёт", "ровный", "падает")},
            "открыто": len(P.get("open", [])), "закрыто": len(P.get("done", [])),
            "последние": [e for e in _all() if e["kind"] != CTRL][-15:],
            "диагностика": {k: v for k, v in R["diag"].items() if k != "t"}}

# ---------- цикл ----------
def loop():
    time.sleep(200)                                    # после бумаги магазина (xshopp стартует через ~3 мин)
    while True:
        try:
            with LOCK:
                load()
            break
        except Exception:
            log.exception("xlongp load")
            time.sleep(60)
    while True:
        try:
            tick(time.time())
        except Exception:
            log.exception("xlongp")
        time.sleep(60)

def tick(now):
    """Один проход: цены Gate → сигналы и ведение событий; раз в 5 мин — сохранить, раз в сутки — итог в Telegram."""
    tk = tickers()
    with LOCK:
        step(now, tk)
        R["n"] = R.get("n", 0) + 1
        if R["n"] % 5 == 0:
            save()
        if DAILY and now - P["rep_t"] >= 24 * 3600:
            P["rep_t"] = now
            mm.tg(brief())

def ok():
    return mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN

@mm.app.route("/longp")
def longp_page():
    if not ok():
        return "forbidden", 403
    with LOCK:
        return jsonify(page())

@mm.app.route("/longp/brief")
def longp_brief():
    if not ok():
        return "forbidden", 403
    with LOCK:
        txt = brief()
    if request.args.get("tg") == "1":
        mm.tg(txt)
    return txt, 200, {"Content-Type": "text/plain; charset=utf-8"}

if ON:
    threading.Thread(target=loop, daemon=True).start()
