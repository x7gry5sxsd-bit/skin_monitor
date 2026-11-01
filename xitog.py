# xitog.py — ИТОГ СИСТЕМЫ: одна страница про всё и раз в день — то же в Telegram. Ничего не торгует.
#   Читает живой автошорт (xshortlive) и обе бумаги: шорт (xshortp) и лонг (xlongp). Каждая «стратегия» =
#   сторона (шорт/лонг) × источник сигнала (кандидат, окно, скан, аллокатор, всплеск; лонг: рост, памп_дальше,
#   обратные окна) × фильтр × тейк/стоп. Шорт считается «как живой»: те же проверки, что делает xshortlive перед
#   входом (памп за сутки, фандинг, XSL_RULE, растущий рынок, XSL_MKT_MAX_BY), плюс вариант («как живой + …»).
#   Для каждой — сделок, тейков %, $ на сделку как у живого ($XIT_MARGIN × плечо), то же у случайных
#   монет с тем же фильтром и в ту же минуту, и устойчивость: $ на сделку в старой и новой половине сделок.
#   ⚙️ — как живой настроен сейчас (тип, вариант из XSL_VAR_BY, тейк из XSL_TP_BY) и что это дало на бумаге.
#     ✅ проверено — 30+ сделок, плюс, лучше случайных хотя бы на $0,20 за сделку и плюс в обеих половинах;
#     🟡 перспективно — в плюсе и лучше случайных, но сделок меньше или половины расходятся;
#     ❌ не работает — 15+ сделок и минус.
#   «При таком рынке» — те же стратегии только по событиям при похожем рынке (падает / ровный / растёт).
#   МНОГОХОДОВКИ — фильтры из нескольких шагов истории монеты (добавляются в варианты обеих бумаг, их видно и на
#   /shortp/var, /longp/var; шортовые можно включить живому через XSL_VAR_BY):
#     шорт: м_выдох_пампа (памп за 3 дня → уже 10%+ ниже вершины → падает в этот час), м_падает_везде (сутки, 4 ч и
#     час вниз), м_слабая_на_росте (рынок растёт, монета за сутки −3%+ и падает), м_разворот_у_вершины (за день
#     +5…15%, за час откат от вершины 1,5%+), м_обвал_рынка (за час падает четверть рынка), м_после_роста_вниз
#     (за сутки до этого монета была в лонг-скане); м_без_отскока (v1.1) — монета за час до входа упала не больше
#     чем на 2% и рынок за час не растёт (+0,3%): не входим, когда монета уже обвалилась или рынок разворачивается;
#     (v1.2) м_не_глубоко — монета не ниже максимума за 3 дня больше чем на 14,5% (сильно упавшие чаще отскакивают);
#     м_не_обвал — за час от максимума часа не больше −3% (не входим сразу после обвала). Варианты можно
#     складывать через «&»: XSL_VAR_BY=скан:м_без_отскока&м_не_глубоко — итог считает и такие (⚙️ и советы);
#     лонг: м_отскок (за сутки −5%+, в этот час рост), м_тренд_вверх (4 ч и час вверх, рынок не падает),
#     м_памп_с_рынком, м_сильная_на_слабом, м_разворот_рынка_вверх, м_после_падения_вверх (была в шорт-скане).
#   Сверка: живые сделки автошорта с бумажными двойниками (та же монета, та же минута) — совпал ли исход.
#   «Что сделать» — только текст: другая настройка советуется, только если она ✅ и на той же истории дала бы больше $
#   хотя бы на $0,20 за сделку, чем текущая; с точной строкой переменной. Сам ничего не включает.
#   /itog?t=... — текст, /itog/json?t=... — всё таблицей, &tg=1 — отправить в Telegram.
#   Раз в сутки в XIT_HOUR (9:00 по Иркутску) — итог в Telegram (XIT_DAILY=0 — не слать).
import os, sys, time, json, threading, logging
from flask import jsonify, request
import mm_scanner as mm
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

VERSION = "xitog v1.2 · 01.11"
DAILY = env("XIT_DAILY", "1") == "1"
HOUR = int(env("XIT_HOUR", "9"))                       # час отправки по Иркутску
VM = float(env("XIT_MARGIN", "20"))                    # $ маржи — как у живого автошорта
N_OK, N_BAD, N_MIN = 30, 15, 8                         # сделок: «проверено», «не работает», «показывать»
EDGE = 0.20                                            # лучше случайных хотя бы на $0,20 за сделку
log = logging.getLogger("xitog")
S = {"loaded": 0}
REG = {}
IXC = {"t": 0, "ix": {}}

def lt(t=None, f="%d.%m %H:%M"):
    return time.strftime(f, time.gmtime((time.time() if t is None else t) + 8 * 3600))

def mods():
    return sys.modules.get("xshortp"), sys.modules.get("xlongp"), sys.modules.get("xshortlive")

# ---------- помощники условий ----------
def _c(x, f):
    return None if x is None else bool(f(x))

def _all(*xs):
    if any(x is False for x in xs):
        return False
    return None if any(x is None for x in xs) else True

def sp(e, k):
    """Признак шорт-события: из «что было до входа» или с самого события."""
    v = (e.get("pre") or {}).get(k)
    return e.get(k) if v is None else v

def s_mkt(e):
    SP = sys.modules.get("xshortp")
    return SP.mkt(e) if SP and hasattr(SP, "mkt") else e.get("mk")

# ---------- история монеты в обеих бумагах (для многоходовок «после роста / после падения») ----------
def coin_index():
    """{монета: [(время, сторона, вид)]} по обеим бумагам — кэш 60 с."""
    if time.time() - IXC["t"] < 60:
        return IXC["ix"]
    SP, LP, _ = mods()
    ix = {}
    for e in (SP._src() if SP and hasattr(SP, "_src") else []):
        ix.setdefault(e["coin"], []).append((e["t"], "шорт", e["kind"]))
    for e in ((LP.P.get("done", []) + LP.P.get("open", [])) if LP and getattr(LP, "P", None) else []):
        ix.setdefault(e["coin"], []).append((e["t"], "лонг", e["kind"]))
    IXC.update(t=time.time(), ix=ix)
    return ix

def prior(e, side, kinds, hours=24):
    """Была ли у монеты за hours ч до события запись этой стороны и вида (True/False; None — бумаги нет)."""
    SP, LP, _ = mods()
    if (side == "лонг" and not LP) or (side == "шорт" and not SP):
        return None
    return any(e["t"] - hours * 3600 <= t < e["t"] and s == side and k in kinds
               for t, s, k in coin_index().get(e["coin"], []))

S_PAT = [
    ("м_выдох_пампа", "памп за 3 дня (25%+) → уже 10%+ ниже его вершины → падает в этот час (1%+)",
     lambda e: _all(_c(sp(e, "up72"), lambda x: x >= 0.25), _c(sp(e, "dh72"), lambda x: x <= -0.10),
                    _c(sp(e, "ch1"), lambda x: x <= -0.01))),
    ("м_падает_везде", "за сутки −5%+, за 4 ч −2%+ и за час −1%+",
     lambda e: _all(_c(sp(e, "ch24"), lambda x: x <= -0.05), _c(sp(e, "ch4"), lambda x: x <= -0.02),
                    _c(sp(e, "ch1"), lambda x: x <= -0.01))),
    ("м_слабая_на_росте", "рынок за сутки +1%+, а монета −3%+ и падает в этот час",
     lambda e: _all(_c(s_mkt(e), lambda x: x >= 0.01), _c(sp(e, "ch24"), lambda x: x <= -0.03),
                    _c(sp(e, "ch1"), lambda x: x <= -0.005))),
    ("м_разворот_у_вершины", "за день +5…15%, а за час откат от вершины 1,5%+",
     lambda e: _all(_c(sp(e, "ch24"), lambda x: 0.05 <= x < 0.15), _c(sp(e, "dh1"), lambda x: x <= -0.015),
                    _c(sp(e, "ch1"), lambda x: x <= 0))),
    ("м_обвал_рынка", "за час падает четверть рынка (1%+) и монета тоже",
     lambda e: _all(_c(e.get("br1"), lambda x: x >= 0.25), _c(sp(e, "ch1"), lambda x: x <= -0.01))),
    ("м_после_роста_вниз", "за сутки до этого монета была в лонг-скане (рост или памп), теперь сигнал вниз",
     lambda e: prior(e, "лонг", ("рост", "памп_дальше"))),
    ("м_без_отскока", "монета за час упала не больше чем на 2% и рынок за час не растёт (+0,3%) — без входа перед отскоком",
     lambda e: _all(_c(sp(e, "ch1"), lambda x: x > -0.02), _c(e.get("mk1"), lambda x: x < 0.003))),
    ("м_не_глубоко", "монета не ниже своего максимума за 3 дня больше чем на 14,5% — сильно упавшие чаще отскакивают",
     lambda e: _c(sp(e, "dh72"), lambda x: x > -0.145)),
    ("м_не_обвал", "за час монета ниже максимума часа не больше чем на 3% — не входим сразу после обвала",
     lambda e: _c(sp(e, "dh1"), lambda x: x > -0.03)),
]
COMBOS = ["м_без_отскока&м_не_глубоко", "м_не_обвал&м_не_глубоко"]   # (v1.2) связки — считаем всегда

def code_fn(c, vm):
    """(v1.2) Условие варианта, в т. ч. связки «a&b» (все части сразу); None — такого варианта нет."""
    spat = {x: f for x, _, f in S_PAT}
    fs = [vm.get(p) or spat.get(p) for p in str(c or "").split("&") if p.strip()]
    if not fs or any(f is None for f in fs):
        return None
    return lambda e, fs=fs: _all(*[f(e) for f in fs])

def lp(e, k):
    v = e.get(k)
    return (e.get("pre") or {}).get(k) if v is None else v

L_PAT = [
    ("м_отскок", "за сутки −5%+, а в этот час рост 1,2%+ (отскок)",
     lambda e: _all(_c(lp(e, "ch24"), lambda x: x <= -0.05), _c(lp(e, "ch1"), lambda x: x >= 0.012))),
    ("м_тренд_вверх", "за 4 ч +2%+, за час +1%+, рынок за сутки не падает",
     lambda e: _all(_c(lp(e, "ch4"), lambda x: x >= 0.02), _c(lp(e, "ch1"), lambda x: x >= 0.01),
                    _c(e.get("mkt"), lambda x: x >= 0))),
    ("м_памп_с_рынком", "памп (сутки +15%+) продолжается, и рынок за час растёт",
     lambda e: _all(_c(lp(e, "ch24"), lambda x: x >= 0.15), _c(lp(e, "ch1"), lambda x: x >= 0.012),
                    _c(e.get("mk1"), lambda x: x >= 0))),
    ("м_сильная_на_слабом", "рынок за сутки не растёт, а монета +3%+ и растёт в этот час",
     lambda e: _all(_c(e.get("mkt"), lambda x: x <= 0), _c(lp(e, "ch24"), lambda x: x >= 0.03),
                    _c(lp(e, "ch1"), lambda x: x >= 0.01))),
    ("м_разворот_рынка_вверх", "за час растёт четверть рынка (1%+) и BTC +0,3%+",
     lambda e: _all(_c(e.get("brup"), lambda x: x >= 0.25), _c(e.get("btc1"), lambda x: x >= 0.003))),
    ("м_после_падения_вверх", "за сутки до этого монета была в шорт-скане, теперь сигнал вверх",
     lambda e: prior(e, "шорт", ("скан",))),
]

def register():
    """Многоходовки — в варианты обеих бумаг (их видно на /shortp/var и /longp/var, шортовые — для XSL_VAR_BY)."""
    SP, LP, _ = mods()
    if SP and hasattr(SP, "VARS") and hasattr(SP, "VMAP") and not REG.get("sp"):
        for code, desc, f in S_PAT:
            if code not in SP.VMAP:
                SP.VARS.append((code, desc, f))
                SP.VMAP[code] = f
                if hasattr(SP, "VDESC"):
                    SP.VDESC[code] = desc
        REG["sp"] = 1
    if LP and hasattr(LP, "LVARS") and not REG.get("lp"):
        have = {c for c, _, _ in LP.LVARS}
        for code, desc, f in L_PAT:
            if code not in have:
                LP.LVARS.append((code, desc, f))
        REG["lp"] = 1
# ---------- стратегии ----------
def regime(m):
    return None if m is None else "растёт" if m >= 0.01 else "падает" if m <= -0.01 else "ровный"

S_KINDS = ("кандидат", "окно", "аллокатор", "скан", "всплеск")   # как в XSL_KINDS

def live_ok(e, kind):
    """Взял бы это событие живой автошорт с его настройками (без XSL_VAR_BY): True / False / None — не проверить.
    Те же проверки, что в xshortlive.check: памп за сутки, фандинг, XSL_RULE, растущий рынок, XSL_MKT_MAX_BY."""
    SP, _, XL = mods()
    g = lambda k, d: getattr(XL, k, d) if XL else d
    ch24 = sp(e, "ch24")
    if ch24 is None:
        return None
    if ch24 >= g("MAX24", 0.15):
        return False
    f = e.get("fund")
    if f is not None and f < g("MIN_FUND", -0.002):
        return False
    if g("RULE", "full") != "pump":
        r = SP.rule_ok(e) if SP and hasattr(SP, "rule_ok") else None
        if r is not True:
            return False if r is False else None
    mk = s_mkt(e)
    if g("MKT_FILTER", True) and mk is not None and mk >= g("MKT_UP", 0.01) and ch24 >= 0:
        return False
    mmax = (g("MKT_MAX_BY", {}) or {}).get(kind)
    if mmax is not None and (mk is None or mk >= mmax):
        return False
    return True

def live_name(code):
    return "как живой" + (f" + {code}" if code else "")

def short_space(SP):
    """Шорт: источник = тип сигнала (как в XSL_KINDS); фильтр = «все» (без проверок) или «как живой» (проверки
    живого) и «как живой + вариант»; тейк 1% / 2%, стоп 5% и 24 ч — как у живого."""
    _, _, XL = mods()
    src = [(k, lambda e, k=k: e["kind"] == k) for k in S_KINDS]
    vm = getattr(SP, "VMAP", {})
    codes = [c for c in ("правило", "рынок_падает", "рынок_ровный", "рынок_растёт", "памп_3дня", "без_пампа_3дня",
                         "импульс", "сутки_−5_−15") if c in vm] + [c for c, _, _ in S_PAT]
    extra = list((getattr(XL, "VAR_BY", {}) or {}).values()) if XL else []
    codes += [c for c in dict.fromkeys(extra + COMBOS) if c not in codes and code_fn(c, vm)]   # (v1.2) и связки «a&b»
    flt = [("все", None, lambda e, k: True), (live_name(""), "", live_ok)]
    flt += [(live_name(c), c, lambda e, k, f=code_fn(c, vm): _all(live_ok(e, k), f(e)))
            for c in codes if code_fn(c, vm)]
    return src, flt, [(0.01, 0.05), (0.02, 0.05)]

def long_space(LP):
    src = [("обр_окна", lambda e: e["kind"] in getattr(LP, "KINDS", ())), ("рост", lambda e: e["kind"] == "рост"),
           ("памп_дальше", lambda e: e["kind"] == "памп_дальше")]
    flt = [("все", None, lambda e, k: True)]
    lv = {c: f for c, _, f in getattr(LP, "LVARS", [])}
    for code in ("рынок_растёт", "рынок_ровный", "рынок_падает", "час_рост_2", "без_пампа_3дня", "фандинг_минус"):
        if code in lv:
            flt.append((code, code, lambda e, k, f=lv[code]: f(e)))
    flt += [(c, c, lambda e, k, f=f: f(e)) for c, _, f in L_PAT]
    return src, flt, [(0.01, 0.05), (0.02, 0.05), (0.03, 0.05), (0.05, 0.05), (0.1, 0.1)]

def s_deal(SP, e, tp, sl):
    o = SP.outcome(e, tp, 1440)
    return None if o is None else (SP.usd_of(e, o) * VM / SP.VM, o[1] == "tp", o[1] == "sl")

def l_deal(LP, e, tp, sl):
    r = LP.res(e, tp, sl)
    return None if r is None else (LP.usd(r[0], r[1], e.get("fund"), VM, LP.lev_of(sl)), r[0] == tp, r[0] == -sl)

def stats(rows):
    """rows: [(время, $, тейк?, стоп?)] → итог с половинами."""
    if not rows:
        return None
    rows = sorted(rows)
    n = len(rows)
    usd = [r[1] for r in rows]
    h = n // 2
    return {"n": n, "tp": round(sum(1 for r in rows if r[2]) / n * 100), "sl": round(sum(1 for r in rows if r[3]) / n * 100),
            "usd": round(sum(usd) / n, 2), "sum": round(sum(usd), 2),
            "old": round(sum(usd[:h]) / h, 2) if h >= 4 else None,
            "new": round(sum(usd[h:]) / (n - h), 2) if h >= 4 else None}

def status(s, r):
    if not s or s["n"] < N_MIN:
        return "мало"
    better = r is not None and r["n"] >= 10 and s["usd"] - r["usd"] >= EDGE
    if s["n"] >= N_OK and s["usd"] > 0 and better and (s["old"] or 0) > 0 and (s["new"] or 0) > 0:
        return "✅"
    if s["n"] >= N_BAD and s["usd"] < 0:
        return "❌"
    if s["usd"] > 0 and (r is None or s["usd"] > r["usd"]):
        return "🟡"
    return "—"

def table(reg=None):
    """Все стратегии обеих сторон. reg — только события при таком рынке («падает» / «ровный» / «растёт»)."""
    SP, LP, _ = mods()
    out = []
    sides = []
    if SP and hasattr(SP, "outcome") and hasattr(SP, "_src"):
        if hasattr(SP, "ensure_mkx"):
            SP.ensure_mkx()
        ev = SP._src()
        sides.append(("шорт", ev, [e for e in ev if e["kind"] == "контроль"], short_space(SP),
                      lambda e, tp, sl: s_deal(SP, e, tp, sl), s_mkt))
    if LP and getattr(LP, "P", None) and hasattr(LP, "res"):
        ev = LP.P.get("done", []) + LP.P.get("open", [])
        sides.append(("лонг", ev, [e for e in ev if e["kind"] == "контроль"], long_space(LP),
                      lambda e, tp, sl: l_deal(LP, e, tp, sl), lambda e: e.get("mkt")))
    for side, ev, ctrl, (srcs, flts, pairs), deal, mk in sides:
        if reg:
            ev = [e for e in ev if regime(mk(e)) == reg]
            ctrl = [e for e in ctrl if regime(mk(e)) == reg]
        for sname, sf in srcs:
            es0 = [e for e in ev if e["kind"] != "контроль" and sf(e)]
            if not es0:
                continue
            for fname, code, ff in flts:
                def ok(e):
                    try:
                        return ff(e, sname) is True
                    except Exception:
                        return False
                es = [e for e in es0 if ok(e)]
                if not es:
                    continue
                fctl = [e for e in ctrl if ok(e)]
                for tp, sl in pairs:
                    s = stats([(e["t"],) + d for e in es for d in [deal(e, tp, sl)] if d])
                    if not s:
                        continue
                    r = stats([(e["t"],) + d for e in fctl for d in [deal(e, tp, sl)] if d])
                    out.append({"сторона": side, "источник": sname, "фильтр": fname, "код": code, "тейк": tp,
                                "стоп": sl, "нужно": round((sl + 0.001) / (tp + sl) * 100, 1), "итог": s,
                                "случайные": r, "статус": status(s, r)})
    return out

def fmt(x):
    s, r = x["итог"], x["случайные"]
    t = (f"[{x['сторона']}] {x['источник']} · {x['фильтр']} · тейк {x['тейк'] * 100:g}% / стоп {x['стоп'] * 100:g}%: "
         f"{s['n']} сд., тейков {s['tp']}% (нужно {x['нужно']:g}%), {s['usd']:+.2f}$/сд.")
    if r:
        t += f" | случайные {r['usd']:+.2f} ({r['n']})"
    if s["old"] is not None:
        t += f" | половины {s['old']:+.2f}/{s['new']:+.2f}"
    return t

# ---------- живой автошорт и сверка с бумагой ----------
OUT = {"tp": "тейк", "sl": "стоп", "time": "по времени"}
def live_lines():
    SP, _, XL = mods()
    if not XL or not getattr(XL, "L", None) or not XL.L.get("loaded"):
        return ["Живой автошорт: не загружен"]
    with XL.LOCK:
        mode = "live" if XL.LIVE else "paper"
        d, t = dict(XL.dst(mode)), dict(XL.tot(mode))
        pos = [XL.pos_view(p) for p in XL.L.get("pos", {}).values()]
        done = list(XL.L.get("done", []))
        pr = XL.pause_reason()
        vb = getattr(XL, "VAR_BY", {})
    out = [f"Живой автошорт ({'ЖИВОЙ' if mode == 'live' else 'бумага'}): маржа ${XL.MARGIN:g}, сигналы "
           f"{', '.join(sorted(XL.KINDS))}" + (", варианты " + ", ".join(f"{k}:{v}" for k, v in sorted(vb.items())) if vb else "") + f" · {pr or 'работает'}",
           f"  сегодня: {d.get('n', 0)} сд., тейков {d.get('tp', 0)}, стопов {d.get('sl', 0)}, {d.get('pnl', 0):+.2f}$ · "
           f"всего: {t.get('n', 0)} сд., тейков {t.get('tp', 0)}, стопов {t.get('sl', 0)}, {t.get('pnl', 0):+.2f}$"]
    if pos:
        out.append("  открыто: " + "; ".join(f"{p['монета']} {p['шорт_%']:+.2f}%" if p.get("шорт_%") is not None
                                               else p["монета"] for p in pos))
    if SP and hasattr(SP, "_src") and hasattr(SP, "outcome"):   # сверка с бумажными двойниками
        ev = [e for e in SP._src() if e["kind"] in ("кандидат", "аллокатор", "окно", "всплеск", "скан")]
        same, diff = 0, []
        for x in [x for x in done if x.get("mode") == "live"]:
            tw = [e for e in ev if e["coin"] == x["coin"] and abs(e["t"] - x["t"]) < 900]
            tpk = XL.tp_of(x.get("kind")) if hasattr(XL, "tp_of") else 0.01
            o = SP.outcome(tw[0], tpk, 1440) if tw else None
            if not o:
                continue
            lo = x.get("outcome", "")
            lk = "tp" if lo.startswith("тейк") else "sl" if (lo.startswith("стоп") or lo == "ликвидация") else "other"
            if (lk == "tp" and o[1] == "tp") or (lk == "sl" and o[1] == "sl"):
                same += 1
            else:
                pn = x.get("pnl")
                diff.append(f"{x['coin']} (живой: {lo}" + (f" {pn:+.2f}$" if pn is not None else "")
                            + f", бумага: {OUT.get(o[1], o[1])})")
        if same or diff:
            out.append(f"  сверка с бумагой: совпало {same} из {same + len(diff)}"
                       + (" · расхождения: " + "; ".join(diff[-4:]) if diff else ""))
    return out
# ---------- итог ----------
def market_line():
    SP, LP, _ = mods()
    c = dict(getattr(SP, "CTX", {}) or {}) if SP else {}
    lc = dict(getattr(LP, "CTX", {}) or {}) if LP else {}
    mk = c.get("mk")
    f = lambda x, d=1: "—" if x is None else f"{x * 100:+.{d}f}%"
    share = lambda x: "—" if x is None else f"{x * 100:.0f}%"
    return (f"Рынок Gate: сутки {f(mk)} ({regime(mk) or '?'}), час {f(c.get('mk1'), 2)}, BTC за час "
            f"{f(c.get('btc1'), 2)}, за час падают {share(c.get('br1'))} / растут {share(lc.get('brup'))} монет",
            regime(mk))

def kv_str(d):
    return ",".join(f"{k}:{v:g}" if isinstance(v, float) else f"{k}:{v}" for k, v in sorted(d.items()))

def live_now(T):
    """Как сейчас настроен живой — по каждому типу из XSL_KINDS та же настройка на бумаге."""
    _, _, XL = mods()
    if not XL:
        return []
    vb = dict(getattr(XL, "VAR_BY", {}) or {})
    out = []
    for k in [k for k in S_KINDS if k in XL.KINDS]:
        tp = XL.tp_of(k) if hasattr(XL, "tp_of") else 0.01
        x = next((x for x in T if x["сторона"] == "шорт" and x["источник"] == k and x["фильтр"] == live_name(vb.get(k))
                  and abs(x["тейк"] - tp) < 1e-9), None)
        out.append((k, tp, vb.get(k), x))
    return out

def recs(T):
    """Что можно поменять переменными (только подсказка). Сравниваем с тем, как живой настроен сейчас:
    советуем, только если на той же истории сумма $ выше хотя бы на $0,20 за сделку и новая настройка ✅."""
    _, _, XL = mods()
    if not XL:
        return ["живой автошорт не загружен"]
    kinds = set(XL.KINDS)
    vb = dict(getattr(XL, "VAR_BY", {}) or {})
    tpb = dict(getattr(XL, "TP_BY", {}) or {})
    sh = [x for x in T if x["сторона"] == "шорт" and x["код"] is not None]       # только «как живой …»
    out = []
    for k in S_KINDS:
        cand = [x for x in sh if x["источник"] == k and x["статус"] == "✅"]
        tp_cur = XL.tp_of(k) if hasattr(XL, "tp_of") else 0.01
        cur = next((x for x in sh if x["источник"] == k and x["код"] == (vb.get(k) or "")
                    and abs(x["тейк"] - tp_cur) < 1e-9), None) if k in kinds else None
        base = cur["итог"]["sum"] if cur else 0.0
        nn = lambda x: max(x["итог"]["n"], cur["итог"]["n"] if cur else 0)
        best = max([x for x in cand if x["итог"]["sum"] - base >= EDGE * nn(x)], key=lambda x: x["итог"]["sum"],
                   default=None)
        if k in kinds and cur and cur["статус"] == "❌":
            out.append(f"⚠️ «{k}» как сейчас — в минусе на бумаге ({cur['итог']['n']} сд., {cur['итог']['usd']:+.2f}$/сд.)"
                       + ("" if best else ": подумать выключить (убрать из XSL_KINDS)"))
        if not best:
            continue
        ch = []
        if k not in kinds:
            ch.append("XSL_KINDS = " + ",".join(sorted(kinds | {k})))
        if (vb.get(k) or "") != best["код"]:
            v2 = dict(vb)
            if best["код"]:
                v2[k] = best["код"]
            else:
                v2.pop(k, None)
            ch.append(("XSL_VAR_BY = " + kv_str(v2)) if v2 else "удалить XSL_VAR_BY")
        if abs(tp_cur - best["тейк"]) > 1e-9:
            t2 = dict(tpb)
            t2[k] = best["тейк"]
            ch.append("XSL_TP_BY = " + kv_str(t2))
        was = (f"сейчас {cur['итог']['n']} сд. {cur['итог']['sum']:+.0f}$" if cur else
               "сейчас не берём" if k not in kinds else "сейчас нет данных")
        out.append(f"«{k}» → {best['фильтр']}, тейк {best['тейк'] * 100:g}%: на той же истории {best['итог']['n']} сд. "
                   f"{best['итог']['sum']:+.0f}$ ({was}) — " + "; ".join(ch))
    for x in [x for x in T if x["сторона"] == "лонг" and x["статус"] == "✅"][:1]:
        out.append(f"лонг «{x['источник']}» · {x['фильтр']} · тейк {x['тейк'] * 100:g}% / стоп {x['стоп'] * 100:g}% "
                   f"проверен на бумаге — живого лонга пока нет, "
                   f"скажи — сделаю модуль (с маленькой маржой)")
    return out or ["ничего не меняем — лучше текущих настроек проверенного пока нет, копим данные"]

def uniq(xs, top):
    """Без повторов: одинаковые сделки под разными фильтрами — одна строка (с самым коротким фильтром);
    шорт «все» (без проверок живого) — только в /itog/json."""
    seen, out = set(), []
    for x in sorted(xs, key=lambda x: (-x["итог"]["usd"], len(x["фильтр"]))):
        key = (x["сторона"], x["источник"], x["тейк"], x["итог"]["n"], x["итог"]["sum"])
        if (x["сторона"] == "шорт" and x["код"] is None) or key in seen:
            continue
        seen.add(key)
        out.append(x)
    return out[:top]

def text():
    SP, LP, XL = mods()
    register()
    ml, reg = market_line()
    T = table()
    L = [f"🧭 {VERSION} — итог системы, {lt()}", ml]
    L += live_lines()
    for k, tp, code, x in live_now(T):
        L.append(f"  ⚙️ «{k}» как настроен ({live_name(code)}, тейк {tp * 100:g}%) на бумаге: "
                 + (f"{x['итог']['n']} сд., тейков {x['итог']['tp']}% (нужно {x['нужно']:g}%), "
                    f"{x['итог']['usd']:+.2f}$/сд. {x['статус']}" if x else "сделок пока нет"))
    sc = (getattr(SP, "P", {}) or {}).get("scan_day") or {} if SP else {}
    lsc = (getattr(LP, "P", {}) or {}).get("scan_day") or {} if LP else {}
    L.append(f"Сканы сегодня: шорт (падают) {sc.get('n', 0)} · лонг: рост {lsc.get('рост', 0)}, памп {lsc.get('памп_дальше', 0)}")
    L.append(f"✅ Проверено ({N_OK}+ сделок, плюс, лучше случайных на ${EDGE:g}+, плюс в обеих половинах):")
    L += ["  " + fmt(x) for x in uniq([x for x in T if x["статус"] == "✅"], 5)] or ["  пока нет"]
    L.append("🟡 Перспективно (в плюсе и лучше случайных, но рано):")
    L += ["  " + fmt(x) for x in uniq([x for x in T if x["статус"] == "🟡"], 6)] or ["  пока нет"]
    if reg:
        R = [x for x in table(reg) if x["итог"]["n"] >= 5 and x["итог"]["usd"] > 0
             and (x["случайные"] is None or x["итог"]["usd"] > x["случайные"]["usd"])]
        L.append(f"🌡 При таком рынке ({reg}) лучше всего было:")
        L += ["  " + fmt(x) for x in uniq(R, 4)] or ["  данных при таком рынке пока мало"]
    bad = sorted([x for x in T if x["статус"] == "❌" and not (x["сторона"] == "шорт" and x["код"] is None)],
                 key=lambda x: x["итог"]["usd"])
    if bad:
        L.append("❌ Не работает:")
        L += ["  " + fmt(x) for x in bad[:3]]
    L.append("Что сделать:")
    L += ["  " + r for r in recs(T)]
    L.append("Бумага смотрит цену раз в минуту, $ — при $" + f"{VM:g} маржи; выводы — только по ✅")
    return "\n".join(L)

# ---------- цикл и страницы ----------
def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'itog_v1'", fetch=True)
    S.update(json.loads(r[0][0]) if r else {})
    S["loaded"] = 1

def save():
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('itog_v1', %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps({k: v for k, v in S.items()
                                                                                  if k != "loaded"}),))

def send(txt):
    """Telegram берёт до 4096 знаков — длинный итог шлём частями по строкам."""
    part = ""
    for line in txt.split("\n"):
        if part and len(part) + len(line) + 1 > 3800:
            mm.tg(part)
            part = ""
        part = (part + "\n" + line) if part else line[:3800]
    if part:
        mm.tg(part)

def tick(now):
    register()
    if not DAILY or not S.get("loaded"):
        return
    d, h = lt(now, "%Y-%m-%d"), int(lt(now, "%H"))
    if h >= HOUR and S.get("sent") != d:
        S["sent"] = d
        save()
        send(text())

def loop():
    time.sleep(300)                                    # после бумаг (xshortp ~4 мин, xlongp ~3 мин)
    while not S.get("loaded"):
        try:
            load()
        except Exception:
            log.exception("xitog load")
            time.sleep(60)
    while True:
        try:
            tick(time.time())
        except Exception:
            log.exception("xitog")
        time.sleep(60)

def ok():
    return mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN

@mm.app.route("/itog")
def itog_page():
    if not ok():
        return "forbidden", 403
    txt = text()
    if request.args.get("tg") == "1":
        send(txt)
    return txt, 200, {"Content-Type": "text/plain; charset=utf-8"}

@mm.app.route("/itog/json")
def itog_json():
    if not ok():
        return "forbidden", 403
    register()
    ml, reg = market_line()
    return jsonify({"версия": VERSION, "рынок": ml, "все_стратегии": table(),
                    "при_таком_рынке": {"рынок": reg, "стратегии": table(reg) if reg else []}})

register()
threading.Thread(target=loop, daemon=True).start()
