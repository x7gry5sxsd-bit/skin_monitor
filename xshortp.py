# xshortp.py — БУМАГА, ничего не торгует. Проверка гипотезы «шорт после окна»: если в момент окна HTX (бумага
#   сделала сделку по монете с продажей на HTX) открыть шорт на фьючерсе MEXC — цена потом падает?
#   На каждое событие: цена входа (фьючерс MEXC), дальше раз в минуту цена (максимум — для ликвидации),
#   через 1 / 4 / 24 ч — итог. Модель: плечо 2/3/5/10/20 с ликвидацией, плечо 3 со стопом 3%, комиссия, фандинг.
#   Сигналы: «окно» (сделка бумаги за последние 10 мин) и «всплеск» (3+ сделок за 20 мин в 2+ отрезках).
#   Одна монета — не чаще раза в XSP_COOL_H ч. /shortp?t=..., итог раз в сутки в Telegram. XSP_ON=0 — выкл.
#   v2 (23.10) — КОНТРОЛЬ: наш сигнал или просто падает весь рынок?
#   • РЫНОК: индекс всех фьючерсов MEXC (медиана изменения цен раз в минуту). У каждого события — ход рынка за
#     то же время и «сверх рынка» = ход шорта монеты минус ход шорта рынка.
#   • СЛУЧАЙНАЯ МОНЕТА: на каждое событие — такой же бумажный шорт случайной монеты из того же списка (монеты HTX
#     с фьючерсом MEXC), в ту же минуту. Группа «контроль» в общий итог не входит.
#   • ВАРИАНТ «х2 + аварийный стоп XSP_STOP2 (15%)» — без тесного стопа, но с защитой от сквиза.
#   • (v2.1) ТЕЙК И СТОП: у каждого события — минута, когда цена ПЕРВЫЙ раз упала на 0,5/1/2% (тейк) и выросла на
#     1/2/3/5% (стоп). Что случилось раньше — то и итог сделки; ни то ни другое за сутки — закрытие через 24 ч.
#     Для каждой пары «тейк/стоп»: доля выигрышей, сколько нужно для нуля с комиссией, $ при х5 и х10 — и то же
#     для случайных монет. Обе цены в одну минуту — считаем стоп (осторожно). Цена раз в минуту: короткие
#     прострелы внутри минуты бумага не видит — живой результат будет хуже.
#   • (v2.2) БЕЗ СТОПА: тейк 0,5/1/2%, а убыток — ликвидация (х10 — рост на 9,5%, х5 — на 19,5%, вся маржа).
#     $ и при $50 маржи. Отдельно — только всплески.
import os, sys, time, json, random, statistics, threading, logging
from flask import jsonify, request
import mm_scanner as mm
import xpaper
import xexec_api as api

def env(k, d=""):
    return os.getenv(k, d).strip()

VERSION = "xshortp v2.2 · 23.10"
ON = env("XSP_ON", "1") == "1"
COOL = float(env("XSP_COOL_H", "2")) * 3600        # одна монета — не чаще раза в 2 ч
HOR = [1, 4, 24]                                   # горизонты, ч
LEVS = [2, 3, 5, 10, 20]
STOP = float(env("XSP_STOP", "0.03"))              # стоп-лосс для варианта «плечо 3 + стоп»
STOP2 = float(env("XSP_STOP2", "0.15"))            # аварийный стоп для варианта «плечо 2 + стоп»
FEE = 0.0004                                       # комиссия фьючерса MEXC туда-обратно (от объёма)
MXF = "https://contract.mexc.com"
CTRL = "контроль"
TPS = [0.005, 0.01, 0.02]                          # тейк: цена упала на 0,5 / 1 / 2%
SLS = [0.01, 0.02, 0.03, 0.05]                     # стоп: цена выросла на 1 / 2 / 3 / 5%
LIQS = {5: 0.195, 10: 0.095}                       # ликвидация: рост цены, при котором сгорает маржа
log = logging.getLogger("xshortp")
P = {}

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'shortp_v1'", fetch=True)
    P.update(json.loads(r[0][0]) if r else {})
    P.setdefault("open", [])
    P.setdefault("done", [])
    P.setdefault("cool", {})
    P.setdefault("rep_t", time.time())
    P.setdefault("ix", {"lvl": 1.0, "last": {}, "since": int(time.time())})

def save():
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('shortp_v1', %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(P),))

def tickers():
    """{МОНЕТА: (цена фьючерса, фандинг за 8 ч)} — один запрос на все контракты MEXC."""
    out = {}
    for x in api.S.get(f"{MXF}/api/v1/contract/ticker", timeout=20).json().get("data") or []:
        s = x.get("symbol", "")
        if s.endswith("_USDT") and float(x.get("lastPrice") or 0) > 0:
            out[s[:-5]] = (float(x["lastPrice"]), float(x.get("fundingRate") or 0))
    return out

def index_step(tk):
    """Индекс рынка: раз в минуту умножаем на (1 + медиана изменения цен всех фьючерсов с прошлой минуты)."""
    ix = P.setdefault("ix", {"lvl": 1.0, "last": {}, "since": int(time.time())})
    last = ix.get("last") or {}
    rets = [min(0.5, max(-0.5, tk[c][0] / p - 1)) for c, p in last.items() if c in tk and p > 0]
    if len(rets) >= 20:
        ix["lvl"] = ix["lvl"] * (1 + statistics.median(rets))
    ix["last"] = {c: v[0] for c, v in tk.items()}
    return ix["lvl"]

def signals(now):
    """Монеты с окном прямо сейчас — из корзин охотника (бумага, продажа на HTX)."""
    HU = sys.modules.get("xhunt")
    if not HU or not HU.H.get("fb"):
        return []
    out = []
    for coin in HU.H["fb"]:
        t10, _, _ = HU.burst(coin, now, 10)
        if t10 <= 0:
            continue
        t20, _, k = HU.burst(coin, now, 20)
        out.append((coin, "всплеск" if t20 >= 3 and k >= 2 else "окно"))
    return out

def pool(tk, skip):
    """Монеты для контроля: тот же список, что видит бумага магазина (монеты HTX), с фьючерсом MEXC."""
    SP = sys.modules.get("xshopp")
    cs = {k.split(":")[0] for k in (SP.S.get("p") or {})} if SP else set()
    cs = [c for c in (cs or set(tk)) if c in tk and c not in skip]
    return sorted(cs)

def event(coin, kind, now, tk, lvl):
    px, fr = tk[coin]
    return {"coin": coin, "kind": kind, "t": int(now), "px": px, "fund": fr, "hi": px, "lo": px,
            "r": {}, "adv": {}, "m0": lvl, "mr": {}, "tp": {}, "sl": {}, "v": 22}

def step(now, tk):
    lvl = index_step(tk)
    sig = signals(now)
    for coin, kind in sig:                             # 1) новые события + контроль в ту же минуту
        if coin not in tk or now - P["cool"].get(coin, 0) < COOL or len(P["open"]) >= 400:
            continue
        P["cool"][coin] = now
        P["open"].append(event(coin, kind, now, tk, lvl))
        cs = pool(tk, {c for c, _ in sig})
        if cs:
            P["open"].append(event(random.choice(cs), CTRL, now, tk, lvl))
    keep = []
    for e in P["open"]:                                # 2) ведём открытые
        if e["coin"] in tk:
            px = tk[e["coin"]][0]
            e["hi"], e["lo"] = max(e["hi"], px), min(e["lo"], px)
            mv, mins = px / e["px"] - 1, max(1, round((now - e["t"]) / 60))
            if "tp" in e and now - e["t"] <= 24 * 3600:      # первое касание уровней (минута от входа)
                for x in TPS:
                    if mv <= -x and str(x) not in e["tp"]:
                        e["tp"][str(x)] = mins
                for x in SLS + list(LIQS.values()):
                    if mv >= x and str(x) not in e["sl"]:
                        e["sl"][str(x)] = mins
            for h in HOR:
                if str(h) not in e["r"] and now - e["t"] >= h * 3600:
                    e["r"][str(h)] = round((e["px"] - px) / e["px"], 5)          # доход шорта без плеча
                    e["adv"][str(h)] = round((e["hi"] - e["px"]) / e["px"], 5)   # худший рост против шорта
                    if e.get("m0"):
                        e.setdefault("mr", {})[str(h)] = round(1 - lvl / e["m0"], 5)   # шорт «всего рынка»
        if len(e["r"]) == len(HOR) or now - e["t"] > 26 * 3600:
            P["done"] = (P["done"] + [e])[-1200:]
        else:
            keep.append(e)
    P["open"] = keep

def pnl(e, h, lev, stop=None):
    """Итог сделки на марже (доля маржи): ликвидация = −100%, стоп = −стоп×плечо, плюс фандинг, минус комиссия."""
    r, adv = e["r"].get(str(h)), e["adv"].get(str(h))
    if r is None:
        return None
    if adv >= 1 / lev - 0.005:
        return -1.0                                    # ликвидация
    if stop and adv >= stop:
        return -stop * lev - FEE * lev
    return r * lev + e.get("fund", 0) * (h / 8) * lev - FEE * lev

def tpsl(e, tp, sl, lev):
    """Сделка «тейк tp / стоп sl» на марже (доля маржи) или None, если ещё не закончилась. Стопы до 5% —
    раньше ликвидации и при х5 (19,5%), и при х10 (9,5%)."""
    a, b = e["tp"].get(str(tp)), e["sl"].get(str(sl))
    if a is not None and (b is None or a < b):
        r, mins = tp, a
    elif b is not None:
        r, mins = -sl, b
    elif "24" in e["r"]:
        r, mins = e["r"]["24"], 1440
    else:
        return None
    return r * lev - FEE * lev + e.get("fund", 0) * (mins / 480) * lev

def tpsl_table(kind=None):
    """Таблица «тейк/стоп»: только новые события (с замером касаний), законченные."""
    es = [e for e in P.get("done", []) + P.get("open", []) if "tp" in e
          and (e["kind"] == kind if kind else e["kind"] != CTRL)]
    out = {}
    for tp in TPS:
        for sl in SLS:
            res = []
            for e in es:
                a, b = e["tp"].get(str(tp)), e["sl"].get(str(sl))
                if a is not None and (b is None or a < b):
                    res.append((1, e))
                elif b is not None:
                    res.append((0, e))
                elif "24" in e["r"]:
                    res.append((2, e))                 # ни тейка, ни стопа за сутки
            if not res:
                continue
            n = len(res)
            out[f"тейк {tp*100:g}% / стоп {sl*100:g}%"] = {
                "сделок": n, "тейков %": round(sum(1 for k, _ in res if k == 1) / n * 100),
                "стопов %": round(sum(1 for k, _ in res if k == 0) / n * 100),
                "нужно тейков для нуля %": round((sl + FEE) / (tp + sl) * 100, 1),
                "х5: $ на $10": round(sum(tpsl(e, tp, sl, 5) or 0 for _, e in res) * 10, 2),
                "х10: $ на $10": round(sum(tpsl(e, tp, sl, 10) or 0 for _, e in res) * 10, 2),
                "х10: $ при $50 маржи": round(sum(tpsl(e, tp, sl, 10) or 0 for _, e in res) * 50, 2)}
    return out

def nostop(e, tp, lev):
    """Сделка «тейк tp, без стопа»: тейк раньше ликвидации — +tp×плечо; ликвидация раньше — −вся маржа;
    ни того ни другого за сутки — закрытие через 24 ч. None — ещё не закончилась."""
    a, b = e["tp"].get(str(tp)), e["sl"].get(str(LIQS[lev]))
    if a is not None and (b is None or a < b):
        r, mins = tp, a
    elif b is not None:
        return -1.0
    elif "24" in e["r"]:
        r, mins = e["r"]["24"], 1440
    else:
        return None
    return r * lev - FEE * lev + e.get("fund", 0) * (mins / 480) * lev

def nostop_table(kind=None):
    """Таблица «тейк без стопа» (убыток — ликвидация) для х5 и х10."""
    es = [e for e in P.get("done", []) + P.get("open", []) if e.get("v", 0) >= 22      # ликвидацию видят с v2.2
          and (e["kind"] == kind if kind else e["kind"] != CTRL)]
    out = {}
    for tp in TPS:
        row = {}
        for L in (5, 10):
            ps = [p for p in (nostop(e, tp, L) for e in es) if p is not None]
            if not ps:
                continue
            win = tp * L - FEE * L
            row.update({f"х{L}: сделок": len(ps),
                        f"х{L}: тейков %": round(sum(1 for p in ps if p > 0) / len(ps) * 100, 1),
                        f"х{L}: ликвидаций": sum(1 for p in ps if p == -1.0),
                        f"х{L}: нужно тейков для нуля %": round(1 / (1 + win) * 100, 1),
                        f"х{L}: $ при $50 маржи": round(sum(ps) * 50, 2)})
        if row:
            out[f"тейк {tp*100:g}% / без стопа"] = row
    return out

def summary(kind=None):
    """kind=None — все сигналы (без контроля); kind="контроль" — случайные монеты; иначе — один вид сигнала."""
    out = {}
    for h in HOR:
        es = [e for e in P.get("done", []) + P.get("open", []) if str(h) in e["r"]
              and (e["kind"] == kind if kind else e["kind"] != CTRL)]
        if not es:
            out[f"{h}ч"] = {"событий": 0}
            continue
        rs = [e["r"][str(h)] for e in es]
        row = {"событий": len(es), "средний ход шорта %": round(sum(rs) / len(rs) * 100, 2),
               "в плюсе %": round(sum(1 for r in rs if r > 0) / len(rs) * 100)}
        ms = [(e["r"][str(h)], e["mr"][str(h)]) for e in es if str(h) in (e.get("mr") or {})]
        if ms:
            row["с замером рынка"] = len(ms)
            row["рынок за то же время, ход шорта %"] = round(sum(m for _, m in ms) / len(ms) * 100, 2)
            row["сверх рынка %"] = round(sum(r - m for r, m in ms) / len(ms) * 100, 2)
            row["сверх рынка в плюсе %"] = round(sum(1 for r, m in ms if r > m) / len(ms) * 100)
        for L in LEVS:
            ps = [pnl(e, h, L) for e in es]
            row[f"х{L}: $ на $10 маржи"] = round(sum(ps) * 10, 2)
            row[f"х{L}: ликвидаций"] = sum(1 for p in ps if p == -1.0)
        row[f"х3 стоп {STOP*100:.0f}%: $ на $10"] = round(sum(pnl(e, h, 3, STOP) for e in es) * 10, 2)
        row[f"х2 стоп {STOP2*100:.0f}%: $ на $10"] = round(sum(pnl(e, h, 2, STOP2) for e in es) * 10, 2)
        row[f"х2 стоп {STOP2*100:.0f}%: стопов"] = sum(1 for e in es if e["adv"][str(h)] >= STOP2)
        out[f"{h}ч"] = row
    return out

def report():
    s, c = summary(), summary(CTRL)
    L = ["📉 Бумага «шорт после окна» — итог (ничего не торгуется)"]
    for h, row in s.items():
        if not row.get("событий"):
            L.append(f"{h}: событий пока нет")
            continue
        L.append(f"{h}: событий {row['событий']}, шорт в плюсе {row['в плюсе %']}%, средний ход "
                 f"{row['средний ход шорта %']:+.2f}% | на $10 маржи: х2 {row['х2: $ на $10 маржи']:+.2f}$, "
                 f"х3 {row['х3: $ на $10 маржи']:+.2f}$, х10 {row['х10: $ на $10 маржи']:+.2f}$ "
                 f"(ликв. {row['х10: ликвидаций']}), х2+стоп {STOP2*100:.0f}% "
                 f"{row[f'х2 стоп {STOP2*100:.0f}%: $ на $10']:+.2f}$")
        if row.get("с замером рынка"):
            L.append(f"   рынок за то же время {row['рынок за то же время, ход шорта %']:+.2f}%, сверх рынка "
                     f"{row['сверх рынка %']:+.2f}% (в плюсе {row['сверх рынка в плюсе %']}%, событий "
                     f"{row['с замером рынка']})")
        cr = c.get(h) or {}
        if cr.get("событий"):
            L.append(f"   случайные монеты: событий {cr['событий']}, в плюсе {cr['в плюсе %']}%, средний ход "
                     f"{cr['средний ход шорта %']:+.2f}%")
    ns, nc, nb = nostop_table(), nostop_table(CTRL), nostop_table("всплеск")
    for k in ("тейк 0.5% / без стопа",):
        x = ns.get(k) or {}
        if x.get("х10: сделок"):
            y, z = nc.get(k) or {}, nb.get(k) or {}
            L.append(f"{k}, х10, $50: сделок {x['х10: сделок']}, тейков {x['х10: тейков %']}% (нужно "
                     f"{x['х10: нужно тейков для нуля %']}%), ликвидаций {x['х10: ликвидаций']}, итог "
                     f"{x['х10: $ при $50 маржи']:+.2f}$" +
                     (f" | всплески: тейков {z['х10: тейков %']}%, итог {z['х10: $ при $50 маржи']:+.2f}$"
                      if z.get("х10: сделок") else "") +
                     (f" | случайные: тейков {y['х10: тейков %']}%, итог {y['х10: $ при $50 маржи']:+.2f}$"
                      if y.get("х10: сделок") else ""))
    ts, tc = tpsl_table(), tpsl_table(CTRL)
    for k in ("тейк 0.5% / стоп 2%", "тейк 1% / стоп 3%"):
        if k in ts:
            x, y = ts[k], tc.get(k) or {}
            L.append(f"{k}: сделок {x['сделок']}, тейков {x['тейков %']}% (нужно {x['нужно тейков для нуля %']}%), "
                     f"х10 {x['х10: $ на $10']:+.2f}$ на $10" +
                     (f" | случайные: тейков {y['тейков %']}%, х10 {y['х10: $ на $10']:+.2f}$" if y else ""))
    L.append(f"Открыто сейчас: {len(P.get('open', []))}. Вывод делать после 50+ событий с замером рынка на горизонте")
    return "\n".join(L)

def loop():
    time.sleep(240)
    try:
        load()
    except Exception:
        log.exception("xshortp load")
        return
    n = 0
    while True:
        now = time.time()
        try:
            step(now, tickers())
            n += 1
            if n % 5 == 0:
                save()
            if now - P["rep_t"] >= 24 * 3600:
                P["rep_t"] = now
                mm.tg(report())
        except Exception:
            log.exception("xshortp")
        time.sleep(60)

@mm.app.route("/shortp")
def shortp_page():
    if not (mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN):
        return "forbidden", 403
    if request.args.get("tg") == "1":
        mm.tg(report())
    ix = P.get("ix") or {}
    return jsonify({"версия": VERSION, "все": summary(), "только_всплески": summary("всплеск"),
                    "контроль_случайные_монеты": summary(CTRL),
                    "без_стопа_сигналы": nostop_table(), "без_стопа_всплески": nostop_table("всплеск"),
                    "без_стопа_случайные": nostop_table(CTRL),
                    "тейк_стоп_сигналы": tpsl_table(), "тейк_стоп_всплески": tpsl_table("всплеск"),
                    "тейк_стоп_случайные": tpsl_table(CTRL),
                    "индекс_рынка": {"с": time.strftime("%d.%m %H:%M", time.gmtime(ix.get("since", 0) + 8 * 3600)),
                                     "уровень": round(ix.get("lvl", 1.0), 4)},
                    "открыто": len(P.get("open", [])),
                    "последние": [e for e in P.get("done", []) + P.get("open", []) if e["kind"] != CTRL][-15:]})

if ON:
    threading.Thread(target=loop, daemon=True).start()
