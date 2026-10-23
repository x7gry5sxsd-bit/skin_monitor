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
#   • (v2.3) АЛЛОКАТОР: отдельная группа — монеты, которые отобрал аллокатор (кандидаты и заходы), шорт в момент
#     отбора, не чаще раза в сутки на монету. И ИСТОРИЯ: по каждому прошлому заходу аллокатора бот берёт у MEXC
#     минутные свечи и считает тот же шорт задним числом (тейк/ликвидация по минутам, ход за 1/4/24/48 ч и за
#     время, пока монету держали) + 3 случайные монеты в ту же минуту для сравнения. /shortp → «история_аллокатора».
import os, sys, time, json, random, statistics, threading, logging
from flask import jsonify, request
import mm_scanner as mm
import xpaper
import xexec_api as api

def env(k, d=""):
    return os.getenv(k, d).strip()

VERSION = "xshortp v2.3 · 23.10"
ON = env("XSP_ON", "1") == "1"
COOL = float(env("XSP_COOL_H", "2")) * 3600        # одна монета — не чаще раза в 2 ч
HOR = [1, 4, 24]                                   # горизонты, ч
LEVS = [2, 3, 5, 10, 20]
STOP = float(env("XSP_STOP", "0.03"))              # стоп-лосс для варианта «плечо 3 + стоп»
STOP2 = float(env("XSP_STOP2", "0.15"))            # аварийный стоп для варианта «плечо 2 + стоп»
FEE = 0.0004                                       # комиссия фьючерса MEXC туда-обратно (от объёма)
MXF = "https://contract.mexc.com"
CTRL = "контроль"
SIGK = ("окно", "всплеск")                         # «все сигналы» — только окна и всплески
AKIND = "аллокатор"                                # монеты, отобранные аллокатором (с этого дня)
RKIND, RCTRL = "аллокатор_история", "контроль_история"   # прошлые заходы аллокатора и случайные монеты к ним
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
    AL = sys.modules.get("xalloc")                     # 1б) монеты, которые отобрал аллокатор
    if AL:
        ac = P.setdefault("acool", {})
        new = [c[0] for c in (AL.A.get("cands") or []) if c]
        new += [x[2] for x in (AL.A.get("log") or []) if len(x) > 2 and x[1] == "заход" and now - x[0] < 900]
        for coin in dict.fromkeys(new):
            if coin in tk and now - ac.get(coin, 0) >= 24 * 3600 and len(P["open"]) < 400:
                ac[coin] = now
                P["open"].append(event(coin, AKIND, now, tk, lvl))
                cs = pool(tk, {coin})
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

# ---------- история: прошлые заходы аллокатора по свечам MEXC ----------
def kl(coin, start, end, iv):
    """Свечи фьючерса MEXC: [(время, open, high, low, close)]."""
    d = api.S.get(f"{MXF}/api/v1/contract/kline/{coin}_USDT",
                  params={"interval": iv, "start": int(start), "end": int(end)}, timeout=20).json()
    d = d.get("data") if isinstance(d, dict) else None
    if isinstance(d, dict) and d.get("time"):
        return [(int(t), float(o), float(h), float(l), float(c))
                for t, o, h, l, c in zip(d["time"], d["open"], d["high"], d["low"], d["close"])]
    return []

def retro_event(coin, t0, kind, t_exit=None, now=None):
    """Шорт задним числом с минуты t0: тейк/ликвидация по минутным свечам, ход за 1/4/24/48 ч и за удержание."""
    now = now or time.time()
    m, st = kl(coin, t0, t0 + 24 * 3600, "Min1"), 60
    if len(m) < 100:                                   # минутных нет — пятиминутные (касания грубее)
        m, st = kl(coin, t0, t0 + 24 * 3600, "Min5"), 300
    m = [x for x in m if x[0] >= t0 - 1]
    if not m:
        raise ValueError("MEXC не дал свечей")
    px = m[0][1]
    e = {"coin": coin, "kind": kind, "t": int(t0), "px": px, "fund": 0.0, "hi": px, "lo": px,
         "r": {}, "adv": {}, "tp": {}, "sl": {}, "v": 22, "свеча_с": st}
    hi = px
    for t, o, h, l, c in m:
        mins = max(1, round((t + st - t0) / 60))
        hi = max(hi, h)
        for x in TPS:
            if l <= px * (1 - x) and str(x) not in e["tp"]:
                e["tp"][str(x)] = mins
        for x in SLS + list(LIQS.values()):
            if h >= px * (1 + x) and str(x) not in e["sl"]:
                e["sl"][str(x)] = mins                 # тейк и рост в одной свече — считаем рост (осторожно)
        for hh in HOR:
            if str(hh) not in e["r"] and t + st >= t0 + hh * 3600:
                e["r"][str(hh)] = round((px - c) / px, 5)
                e["adv"][str(hh)] = round(hi / px - 1, 5)
    H = [x for x in kl(coin, t0, t0 + 73 * 3600, "Min60") if x[0] >= t0 - 3599]
    def close_at(ts):
        c = [x[4] for x in H if x[0] <= ts - 3600]
        return c[-1] if c else None
    if t0 + 48 * 3600 <= now and close_at(t0 + 48 * 3600):
        e["r48"] = round((px - close_at(t0 + 48 * 3600)) / px, 5)
        e["adv48"] = round(max([x[2] for x in H if x[0] < t0 + 48 * 3600] + [px]) / px - 1, 5)
    end = min(t_exit or now, now)
    cl = close_at(end) or (m[-1][4] if m else None)
    if cl and end - t0 >= 3600:
        e["hold_h"], e["r_hold"], e["held_now"] = round((end - t0) / 3600, 1), round((px - cl) / px, 5), not t_exit
    return e

def retro_list():
    """Прошлые заходы аллокатора: [(время, монета, время выхода или None)]."""
    AL = sys.modules.get("xalloc")
    log_ = (AL.A.get("log") or []) if AL else []
    out = []
    for i, x in enumerate(log_):
        if len(x) > 2 and x[1] == "заход":
            ex = next((y[0] for y in log_[i + 1:] if len(y) > 2 and y[2] == x[2] and y[1] in ("выход", "освобождён")),
                      None)
            out.append((int(x[0]), x[2], ex))
    return out

def retro_step(now, tk):
    """По одному прошлому заходу за минуту: сам заход + 3 случайные монеты в ту же минуту."""
    R = P.setdefault("retro", {})
    for t0, coin, ex in retro_list():
        key = f"{coin}:{t0}"
        old = R.get(key)
        if now - t0 < 24 * 3600 + 600 or (old and old.get("final")):
            continue                                   # сутки ещё не прошли / посчитано окончательно
        if old and now - old.get("at", 0) < (3600 if old.get("err") else 6 * 3600):
            continue                                   # ошибка — повтор через час; ещё держим — раз в 6 ч
        try:
            ev = [retro_event(coin, t0, RKIND, ex, now)]
            rnd = random.Random(t0)
            for c in rnd.sample(pool(tk, {coin}), min(3, len(pool(tk, {coin})))):
                try:
                    ev.append(retro_event(c, t0, RCTRL, ex, now))
                except Exception:
                    pass
            R[key] = {"ev": ev, "at": now, "final": bool(ex) and now - t0 > 50 * 3600}
        except Exception as e:
            R[key] = {"err": str(e)[:120], "at": now}
        return

def retro_page():
    R = P.get("retro") or {}
    src = [e for v in R.values() for e in v.get("ev", [])]
    rows = []
    for key, v in sorted(R.items(), key=lambda kv: int(kv[0].split(":")[1])):
        coin, t0 = key.split(":")[0], int(key.split(":")[1])
        if v.get("err"):
            rows.append({"монета": coin, "заход": time.strftime("%d.%m %H:%M", time.gmtime(t0 + 8 * 3600)),
                         "ошибка": v["err"]})
            continue
        e = v["ev"][0]
        f = lambda x: None if x is None else round(x * 100, 2)
        ns = nostop(e, 0.005, 10)
        rows.append({"монета": coin, "заход": time.strftime("%d.%m %H:%M", time.gmtime(t0 + 8 * 3600)),
                     "держали_ч": e.get("hold_h"), "ещё_держим": e.get("held_now"),
                     "шорт_за_удержание_%": f(e.get("r_hold")),
                     "шорт_1ч_%": f(e["r"].get("1")), "шорт_4ч_%": f(e["r"].get("4")),
                     "шорт_24ч_%": f(e["r"].get("24")), "шорт_48ч_%": f(e.get("r48")),
                     "макс_рост_против_24ч_%": f(e["adv"].get("24")),
                     "тейк_0,5%_через_мин": e["tp"].get("0.005"), "рост_9,5%_через_мин": e["sl"].get("0.095"),
                     "$50_х10_тейк_0,5%_без_стопа": None if ns is None else round(ns * 50, 2),
                     "случайные_монеты_шорт_24ч_%": [f(x["r"].get("24")) for x in v["ev"][1:]]})
    hold = [e for e in src if e["kind"] == RKIND and e.get("r_hold") is not None]
    return {"заходов": len(retro_list()), "посчитано": len(R),
            "монеты": rows,
            "шорт_за_удержание_средний_%": round(sum(e["r_hold"] for e in hold) / len(hold) * 100, 2) if hold else None,
            "итог": summary(RKIND, src), "итог_случайные": summary(RCTRL, src),
            "без_стопа": nostop_table(RKIND, src), "без_стопа_случайные": nostop_table(RCTRL, src),
            "тейк_стоп": tpsl_table(RKIND, src), "тейк_стоп_случайные": tpsl_table(RCTRL, src)}
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

def _src(src=None):
    return src if src is not None else P.get("done", []) + P.get("open", [])

def _is(e, kind):
    return e["kind"] == kind if kind else e["kind"] in SIGK

def tpsl_table(kind=None, src=None):
    """Таблица «тейк/стоп»: только новые события (с замером касаний), законченные."""
    es = [e for e in _src(src) if "tp" in e and _is(e, kind)]
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

def nostop_table(kind=None, src=None):
    """Таблица «тейк без стопа» (убыток — ликвидация) для х5 и х10."""
    es = [e for e in _src(src) if e.get("v", 0) >= 22 and _is(e, kind)]      # ликвидацию видят с v2.2
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

def summary(kind=None, src=None):
    """kind=None — окна и всплески; kind="контроль" — случайные монеты; иначе — одна группа."""
    out = {}
    for h in HOR:
        es = [e for e in _src(src) if str(h) in e["r"] and _is(e, kind)]
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
    rp = retro_page()
    x, y = (rp["без_стопа"].get("тейк 0.5% / без стопа") or {}), (rp["без_стопа_случайные"].get("тейк 0.5% / без стопа") or {})
    if x.get("х10: сделок"):
        L.append(f"История аллокатора ({x['х10: сделок']} заходов): шорт за время удержания в среднем "
                 f"{rp['шорт_за_удержание_средний_%'] if rp['шорт_за_удержание_средний_%'] is not None else '—'}% | "
                 f"тейк 0.5% без стопа, х10, $50: тейков {x['х10: тейков %']}%, ликвидаций {x['х10: ликвидаций']}, "
                 f"итог {x['х10: $ при $50 маржи']:+.2f}$" +
                 (f" | случайные: тейков {y['х10: тейков %']}%, ликвидаций {y['х10: ликвидаций']}, итог "
                  f"{y['х10: $ при $50 маржи']:+.2f}$" if y.get("х10: сделок") else ""))
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
            tk = tickers()
            step(now, tk)
            try:
                retro_step(now, tk)
            except Exception:
                log.exception("xshortp retro")
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
                    "аллокатор_с_сегодня": {"итог": summary(AKIND), "без_стопа": nostop_table(AKIND),
                                            "тейк_стоп": tpsl_table(AKIND)},
                    "история_аллокатора": retro_page(),
                    "последние": [e for e in _src() if e["kind"] != CTRL][-15:]})

if ON:
    threading.Thread(target=loop, daemon=True).start()
