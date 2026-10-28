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
#   v2 (28.10): СКАН РОСТА — зеркало шортового скана, тоже только бумага. Раз в 5 мин по всем фьючерсам Gate:
#     • рост — монета растёт прямо сейчас: за час +1,2%+, у максимума часа (не дальше 0,4%), за сутки −10…+15%;
#     • памп_дальше — памп продолжается: за сутки +15%+ (до +100%) и за час ещё +1,2%+ у максимума часа.
#     Оборот от $300 тыс., фандинг не выше +0,2% (лонг платит), не больше 2 монет каждого вида за раз и 24 в сутки,
#     раз в сутки на монету, к каждой — случайная монета в ту же минуту. Касания уже и 10% вверх и вниз:
#     у пампов движения огромные, тесный стоп выбивает раньше роста. Тейк 10% / стоп 10% считается при х5
#     (при х10 стоп 10% — это ликвидация).
#     РЫНОК в момент входа у всех событий: за сутки и за час, доля монет, выросших за час на 1%+, BTC за час.
#     ВАРИАНТЫ — как у шортов: фильтр «что было до входа» × тейк/стоп, $XLP_VAR_MARGIN (20) × плечо, отдельно
#     обратные окна, рост, памп_дальше и случайные с тем же фильтром. /longp/var?t=... — таблица текстом.
import os, sys, time, json, random, statistics, threading, logging
from collections import deque
from contextlib import nullcontext
from flask import jsonify, request
import mm_scanner as mm
import xpaper
import xexec_api as api

def env(k, d=""):
    return os.getenv(k, d).strip()

VERSION = "xlongp v2 · 28.10"
ON = env("XLP_ON", "1") == "1"
DAILY = env("XLP_DAILY", "1") == "1"
COOL = float(env("XLP_COOL_H", "2")) * 3600            # окно/всплеск по монете — не чаще раза в 2 ч
CAND_RATE = float(env("XLP_CAND_RATE", "0.5"))         # обр_кандидат: обратное направление ≥ $0,5 в сутки
MARGIN = float(env("XLP_MARGIN", "10"))
LEV = int(float(env("XLP_LEV", "10")))
FEE = float(env("XLP_FEE", "0.001"))                   # комиссия Gate туда-обратно от объёма
UPS, DNS, HOR = [0.01, 0.02, 0.03, 0.05, 0.1], [0.01, 0.02, 0.03, 0.05, 0.1], [1, 4, 24]   # v2: и 10%
PAIRS = [(0.01, 0.05), (0.02, 0.05), (0.02, 0.03), (0.03, 0.05), (0.05, 0.05), (0.1, 0.1)]   # тейк / стоп
KINDS = ("обр_окно", "обр_всплеск", "обр_кандидат")
SKINDS = ("рост", "памп_дальше")                       # (v2) скан роста
CTRL = "контроль"
SCAN = env("XLP_SCAN", "1") == "1"
SCAN_EVERY = float(env("XLP_SCAN_MIN", "5")) * 60
UP_CH1 = float(env("XLP_UP_CH1", "0.012"))             # за час выросла хотя бы на 1,2%
UP_DH1 = float(env("XLP_UP_DH1", "0.004"))             # и не дальше 0,4% от максимума часа — рост продолжается
UP_LO24 = float(env("XLP_UP_LO24", "-0.10"))           # «рост»: за сутки от −10%
PUMP24 = float(env("XLP_PUMP24", "0.15"))              # «рост» — до +15%; «памп_дальше» — от +15%
PUMP_MAX24 = float(env("XLP_PUMP_MAX24", "1.0"))       # «памп_дальше» — не больше +100% за сутки
SCAN_VOL = float(env("XLP_SCAN_VOL", "300000"))        # оборот за сутки на Gate, $
SCAN_FUND = float(env("XLP_SCAN_FUND", "0.002"))       # фандинг не выше +0,2% за 8 ч (лонг платит)
SCAN_MAX = int(env("XLP_SCAN_MAX", "2"))               # монет одного вида за скан
SCAN_DAY = int(env("XLP_SCAN_DAY", "24"))              # монет одного вида за сутки
VM = float(env("XLP_VAR_MARGIN", "20"))                # варианты: маржа $20 (как у живого шорта)
VMIN = int(env("XLP_VAR_MIN", "8"))                    # в «лучших» — только варианты с 8+ сделками
DONE_MAX = int(env("XLP_DONE_MAX", "1500"))
GTF = "https://api.gateio.ws/api/v4/futures/usdt"
log = logging.getLogger("xlongp")
P = {}
R = {"prev": {}, "fb": {}, "diag": {}}                 # в памяти: прошлые счётчики пар, десятиминутки, диагностика
LOCK = threading.RLock()
H = {}                                                 # (v2) монета → deque[(время, цена)] за 70 мин — в памяти
CTX = {}                                               # (v2) рынок сейчас: mk1, brup, btc1

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
            try:
                vol = float(x.get("volume_24h_quote") or x.get("volume_24h_settle") or 0)
            except (TypeError, ValueError):
                vol = 0.0
            out[c[:-5]] = {"last": float(x["last"]), "fund": float(x.get("funding_rate") or 0), "chg": chg,
                           "vol": vol}
    return out

# ---------- (v2) минутная история, рынок сейчас, скан роста ----------
def hist_step(now, tk):
    for c, q in tk.items():
        h = H.setdefault(c, deque())
        h.append((now, q["last"]))
        while h and now - h[0][0] > 4200:
            h.popleft()
    for c in [c for c in H if c not in tk]:
        H.pop(c, None)

def hour_of(c, now):
    """(изменение за час, ниже максимума часа) по минутной истории; None — истории меньше 55 мин."""
    h = H.get(c)
    if not h or now - h[0][0] < 3300:
        return None
    pts = [p for t, p in h if now - t <= 3660]
    if not pts or pts[0] <= 0:
        return None
    return h[-1][1] / pts[0] - 1, h[-1][1] / max(pts) - 1

def ctx_step(now):
    hrs = [x[0] for x in (hour_of(c, now) for c in list(H)) if x]
    b = hour_of("BTC", now)
    CTX.clear()
    CTX.update({"mk1": round(statistics.median(hrs), 4) if len(hrs) >= 50 else None,
                "brup": round(sum(1 for x in hrs if x >= 0.01) / len(hrs), 3) if len(hrs) >= 50 else None,
                "btc1": round(b[0], 4) if b else None, "t": int(now)})

def scan_kind(c, q, hr):
    """Вид скана для монеты или None: «рост» или «памп_дальше» (растёт сейчас, у максимума часа)."""
    if not hr or q.get("chg") is None or hr[0] < UP_CH1 or hr[1] < -UP_DH1:
        return None
    if UP_LO24 <= q["chg"] < PUMP24:
        return "рост"
    if PUMP24 <= q["chg"] <= PUMP_MAX24:
        return "памп_дальше"
    return None

def scan_step(now, tk, mk):
    if not SCAN or now - R.get("scan_t", 0) < SCAN_EVERY:
        return
    R["scan_t"] = now
    d = lt(now, "%Y-%m-%d")
    day = P.setdefault("scan_day", {})
    if day.get("d") != d:
        day.clear()
        day["d"] = d
    sc = P.setdefault("scool", {})
    busy = {e["coin"] for e in P.get("open", []) if e["kind"] != CTRL}
    vol_seen = any(q.get("vol") for q in tk.values())  # биржа не отдала оборот — фильтр оборота не применяю
    found = {k: [] for k in SKINDS}
    for c, q in tk.items():
        k = scan_kind(c, q, hour_of(c, now))
        if not k or (vol_seen and q.get("vol", 0) < SCAN_VOL) or q["fund"] > SCAN_FUND:
            continue
        if now - sc.get(c, 0) < 24 * 3600 or c in busy:
            continue
        found[k].append(c)
    R["diag"]["скан_прошли"] = {k: len(v) for k, v in found.items()}
    for k, cs in found.items():
        random.shuffle(cs)
        for c in cs[:max(0, min(SCAN_MAX, SCAN_DAY - day.get(k, 0)))]:
            if len(P["open"]) >= 400:
                return
            sc[c], day[k] = now, day.get(k, 0) + 1
            e = event(c, k, now, tk, mk)
            SP = sys.modules.get("xshortp")            # признаки «что было до входа» — как у шортов (памп за 3 дня)
            if SP and hasattr(SP, "pre_feats"):
                try:
                    e["pre"] = SP.pre_feats(c, e["t"], e["px"])
                except Exception as x:
                    e["pre"] = {"err": str(x)[:80]}
            P["open"].append(e)
            control(c, now, tk, mk)

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
         "ch24": q.get("chg"), "hi": q["last"], "lo": q["last"], "up": {}, "dn": {}, "r": {}, "v": 2}
    hr = hour_of(coin, now)                            # (v2) за час и рынок в момент входа — у всех событий
    if hr:
        e["ch1"], e["dh1"] = round(hr[0], 4), round(hr[1], 4)
    for k in ("mk1", "brup", "btc1"):
        if CTX.get(k) is not None and now - CTX.get("t", 0) < 300:
            e[k] = CTX[k]
    if q.get("vol"):
        e["vol"] = round(q["vol"])
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
    hist_step(now, tk)                                 # (v2) история за час и рынок сейчас
    ctx_step(now)
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
    try:                                               # 2б) (v2) скан роста
        scan_step(now, tk, mk)
    except Exception:
        log.exception("xlongp scan")
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
    P["done"] = P["done"][-DONE_MAX:]

# ---------- итоги ----------
def res(e, tp, sl):
    """(доход лонга, минут) с тейком tp и стопом sl; по времени — ход за 24 ч; None — ещё идёт.
    Касания 10% пишутся с v2 — у старых событий такие пары не считаем."""
    if max(tp, sl) >= 0.1 and e.get("v", 1) < 2:
        return None
    a, b = e["up"].get(str(tp)), e["dn"].get(str(sl))
    if a is not None and (b is None or a < b):
        return tp, a
    if b is not None:
        return -sl, b
    if "24" in e["r"]:
        return max(e["r"]["24"], -sl), 1440
    return None

def lev_of(sl):
    """Плечо пары: стоп 10% — при х5 (при х10 это уже ликвидация), остальное — XLP_LEV."""
    return min(LEV, 5) if sl >= 0.09 else LEV

def usd(ret, mins, fund, margin=None, lev=None):
    """$ на сделку: маржа × плечо × (ход − комиссия − фандинг за время удержания; лонг платит положительный)."""
    return (MARGIN if margin is None else margin) * (LEV if lev is None else lev) * (ret - FEE - (fund or 0) * mins / 480)

def _all():
    return P.get("done", []) + P.get("open", [])

def pick(kind=None, reg=None, src=None):
    es = _all() if src is None else src
    if kind == "сигналы":                              # «сигналы» — обратные окна (скан роста — отдельно)
        es = [e for e in es if e["kind"] in KINDS]
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
        out[f"тейк {tp * 100:g}% / стоп {sl * 100:g}%" + (f" (х{lev_of(sl)})" if lev_of(sl) != LEV else "")] = {
            "сделок": n, "тейков %": round(win / n * 100), "стопов %": round(loss / n * 100),
            "нужно тейков для нуля %": round((sl + FEE) / (tp + sl) * 100),
            f"$ на ${MARGIN:g} × х{LEV}": round(sum(usd(r[0], r[1], e.get("fund"), lev=lev_of(sl)) for r, e in rs), 2)}
    return out

def horizons(es):
    out = {}
    for h in HOR:
        rs = [e["r"][str(h)] for e in es if str(h) in e["r"]]
        if rs:
            out[f"{h}ч"] = {"событий": len(rs), "средний ход лонга %": round(sum(rs) / len(rs) * 100, 2),
                           "в плюсе %": round(sum(1 for r in rs if r > 0) / len(rs) * 100)}
    return out

def line10(es):
    t = table(es).get(pair_name(0.1, 0.1))
    if not t:
        return "итогов пока нет"
    return f"{t['сделок']} сд., тейков {t['тейков %']}%, {t[f'$ на ${MARGIN:g} × х{LEV}']:+.2f}$ на ${MARGIN:g}"

def line(es, tp, sl):
    t = table(es).get(f"тейк {tp * 100:g}% / стоп {sl * 100:g}%")
    if not t:
        return "итогов пока нет"
    return (f"{t['сделок']} сд., тейков {t['тейков %']}% (нужно {t['нужно тейков для нуля %']}%), "
            f"{t[f'$ на ${MARGIN:g} × х{LEV}']:+.2f}$")
# ---------- (v2) варианты: какой фильтр «что было до входа» даёт плюс лонгу ----------
def _c(x, f):
    return None if x is None else bool(f(x))

def _all2(*xs):
    if any(x is False for x in xs):
        return False
    return None if any(x is None for x in xs) else True

def up72(e):
    return (e.get("pre") or {}).get("up72")

LVARS = [
    ("все", "все события", lambda e: True),
    ("час_рост_1", "за час +1%+", lambda e: _c(e.get("ch1"), lambda x: x >= 0.01)),
    ("час_рост_2", "за час +2%+", lambda e: _c(e.get("ch1"), lambda x: x >= 0.02)),
    ("у_максимума_часа", "не дальше 0,5% от максимума часа", lambda e: _c(e.get("dh1"), lambda x: x >= -0.005)),
    ("откат_от_максимума", "ниже максимума часа на 1%+", lambda e: _c(e.get("dh1"), lambda x: x <= -0.01)),
    ("сутки_падала", "за сутки ниже нуля (отскок)", lambda e: _c(e.get("ch24"), lambda x: x < 0)),
    ("сутки_0_15", "за сутки 0…+15%", lambda e: _c(e.get("ch24"), lambda x: 0 <= x < 0.15)),
    ("памп", "за сутки +15% и больше", lambda e: _c(e.get("ch24"), lambda x: x >= 0.15)),
    ("рынок_растёт", "рынок за сутки +1% и выше", lambda e: _c(e.get("mkt"), lambda x: x >= 0.01)),
    ("рынок_ровный", "рынок за сутки −1…+1%", lambda e: _c(e.get("mkt"), lambda x: -0.01 < x < 0.01)),
    ("рынок_падает", "рынок за сутки −1% и ниже", lambda e: _c(e.get("mkt"), lambda x: x <= -0.01)),
    ("рынок_час_вверх", "рынок за час +0,3% и выше", lambda e: _c(e.get("mk1"), lambda x: x >= 0.003)),
    ("btc_час_вверх", "BTC за час +0,3% и выше", lambda e: _c(e.get("btc1"), lambda x: x >= 0.003)),
    ("много_растут", "за час выросли на 1%+ четверть монет и больше", lambda e: _c(e.get("brup"), lambda x: x >= 0.25)),
    ("фандинг_минус", "фандинг ≤0 — лонг получает", lambda e: (e.get("fund") or 0) <= 0),
    ("без_пампа_3дня", "за 3 дня не было роста 25%+", lambda e: _c(up72(e), lambda x: x < 0.25)),
    ("рост+рынок", "за час +1%+ и рынок за сутки не падает (≥0)",
     lambda e: _all2(_c(e.get("ch1"), lambda x: x >= 0.01), _c(e.get("mkt"), lambda x: x >= 0))),
    ("памп+рынок_растёт", "памп и рынок растёт",
     lambda e: _all2(_c(e.get("ch24"), lambda x: x >= 0.15), _c(e.get("mkt"), lambda x: x >= 0.01))),
]
LGROUPS = [("обр_окна", lambda e: e["kind"] in KINDS), ("рост", lambda e: e["kind"] == "рост"),
           ("памп_дальше", lambda e: e["kind"] == "памп_дальше"), ("случайные", lambda e: e["kind"] == CTRL)]

def pair_name(tp, sl):
    return f"тейк {tp * 100:g}% / стоп {sl * 100:g}%" + (f" (х{lev_of(sl)})" if lev_of(sl) != LEV else "")

def var_stats(es, tp, sl):
    rs = [(r, e) for r, e in ((res(e, tp, sl), e) for e in es) if r]
    if not rs:
        return None
    n, lv = len(rs), lev_of(sl)
    total = sum(usd(r[0], r[1], e.get("fund"), VM, lv) for r, e in rs)
    return {"сделок": n, "тейков %": round(sum(1 for r, _ in rs if r[0] == tp) / n * 100),
            "стопов %": round(sum(1 for r, _ in rs if r[0] == -sl) / n * 100),
            "нужно %": round((sl + FEE) / (tp + sl) * 100, 1), "$": round(total, 2), "$ на сделку": round(total / n, 2)}

def var_table():
    """{вариант: {"что": описание, группа: {пара: итог}}} — $VM × плечо пары."""
    src, out = _all(), {}
    for code, desc, f in LVARS:
        row = {"что": desc}
        for gname, gf in LGROUPS:
            es = []
            for e in src:
                if not gf(e):
                    continue
                try:
                    ok_ = f(e)
                except Exception:
                    ok_ = None
                if ok_ is True:
                    es.append(e)
            g = {}
            for tp, sl in PAIRS:
                st = var_stats(es, tp, sl)
                if st:
                    g[pair_name(tp, sl)] = st
            if g:
                row[gname] = g
        out[code] = row
    return out

def var_rows(T, label, group, nmin=None, base=False):
    nmin = VMIN if nmin is None else nmin
    rows = []
    for code, row in T.items():
        s = (row.get(group) or {}).get(label)
        if s and s["сделок"] >= nmin and (base or code != "все"):
            rows.append((s["$ на сделку"], code, s, (row.get("случайные") or {}).get(label)))
    rows.sort(key=lambda x: -x[0])
    return rows

def var_fmt(code, s, c=None):
    t = (f"{code}: {s['сделок']} сд., тейков {s['тейков %']}% (нужно {s['нужно %']:g}%), стопов {s['стопов %']}%, "
         f"{s['$']:+.0f}$ ({s['$ на сделку']:+.2f}/сд.)")
    if c:
        t += f" | случайные: {c['сделок']} сд., {c['тейков %']}%, {c['$ на сделку']:+.2f}/сд."
    return t

def var_lines(T=None):
    """Для brief: по скану роста и пампам — лучший вариант при тейке 2% / стопе 5% и при 10% / 10%."""
    T = T or var_table()
    out = []
    for group in ("рост", "памп_дальше"):
        for tp, sl in ((0.02, 0.05), (0.1, 0.1)):
            label = pair_name(tp, sl)
            rows = var_rows(T, label, group)
            if rows:
                out.append(f"  {group}, {label}: лучший — " + var_fmt(rows[0][1], rows[0][2], rows[0][3]))
    return ([f"Варианты лонга (${VM:g}, стоп как в паре), лучшие:"] + out) if out else []

def var_text():
    T = var_table()
    L = [f"📊 {VERSION} — варианты лонга: ${VM:g} × х{LEV} (стоп 10% — при х5), комиссия {FEE * 100:g}%, фандинг учтён",
         "Сделка: что раньше — тейк или стоп (по минутам; прострелы внутри минуты не видны — живой хуже). Ни того ни",
         "другого за сутки — закрытие через 24 ч. Сравнивай с «случайные»: если там так же — это рынок, а не сигнал.", ""]
    for group in ("рост", "памп_дальше", "обр_окна"):
        L.append(f"■■ {group}")
        for tp, sl in PAIRS:
            label = pair_name(tp, sl)
            rows = var_rows(T, label, group, nmin=1, base=True)
            if not rows:
                continue
            L.append(f"■ {label} (для нуля нужно {(sl + FEE) / (tp + sl) * 100:.1f}% тейков)")
            L += ["  " + var_fmt(code, s, c) for _, code, s, c in rows]
        L.append("")
    L.append("Что значит каждый вариант:")
    L += [f"  {k} — {d}" for k, d, _ in LVARS]
    return "\n".join(L)

def brief():
    mk = R["diag"].get("mkt")
    out = [f"📈 {VERSION} — бумага «лонг по обратным окнам» (на HTX дешевле), {lt(None, '%d.%m %H:%M')}",
           f"Ничего не торгует. Сделка на бумаге: ${MARGIN:g} × х{LEV}, комиссия {FEE * 100:g}%, фандинг учтён"]
    if not P.get("loaded"):
        return "\n".join(out + ["Ещё загружается (первые ~4 мин после запуска)"])
    if mk is not None:
        f = lambda x: "—" if x is None else f"{x * 100:+.2f}%"
        out.append(f"Рынок Gate за сутки: {mk * 100:+.1f}% ({regime(mk)}) · за час {f(CTX.get('mk1'))}, BTC за час "
                   f"{f(CTX.get('btc1'))}, за час выросли на 1%+ "
                   + ("—" if CTX.get("brup") is None else f"{CTX['brup'] * 100:.0f}% монет"))
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
    if SCAN:                                           # (v2) скан роста
        day, sp = P.get("scan_day") or {}, R["diag"].get("скан_прошли") or {}
        out.append(f"Скан роста: рост {len(pick('рост'))} (сегодня {day.get('рост', 0)}), памп_дальше "
                   f"{len(pick('памп_дальше'))} (сегодня {day.get('памп_дальше', 0)})"
                   + (f" · сейчас под фильтр: рост {sp.get('рост', 0)}, памп {sp.get('памп_дальше', 0)}" if sp else
                      " · копит историю цен (первый час после запуска)"))
        for k in SKINDS:
            if pick(k):
                out.append(f"  {k}: тейк 2% / стоп 5% — {line(pick(k), 0.02, 0.05)} · тейк 10% / стоп 10% (х5) — "
                           f"{line10(pick(k))}")
        try:
            out += var_lines()
        except Exception as x:
            log.exception("xlongp var_lines")
            out.append(f"Варианты: ошибка {str(x)[:80]}")
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
            "скан": {"включён": SCAN, "сегодня": P.get("scan_day"), "прошли_фильтр": R["diag"].get("скан_прошли"),
                     **{k: {"итог": table(pick(k)), "ход": horizons(pick(k))} for k in SKINDS}},
            "рынок_сейчас": dict(CTX), "варианты": var_table(),
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

@mm.app.route("/longp/var")
def longp_var():
    """(v2) Варианты лонга текстом: /longp/var?t=..."""
    if not ok():
        return "forbidden", 403
    with LOCK:
        txt = var_text()
    return txt, 200, {"Content-Type": "text/plain; charset=utf-8"}

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
