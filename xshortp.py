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
#   • (v2.4) КАНДИДАТЫ ПО-СТАРОМУ: монеты, у которых бумага магазина (xshopp) ≥ XSP_CAND_RATE $/сутки — как отбирал
#     прежний аллокатор, без фильтров v4 (путь, окупаемость, K): для шорта они не нужны. Раз в сутки на монету.
#     УВЕДОМЛЕНИЯ (XSP_ALERT=1): на каждого кандидата — цена и уровни; потом — что случилось раньше: тейк 1% или
#     +9,5% против (ликвидация при х10), или итог за сутки. Это бумага, не сигнал к сделке.
#   • (v2.5) ЧТО БЫЛО ДО СИГНАЛА: у каждого события (и у прошлых заходов аллокатора) — по свечам MEXC за сутки до
#     входа: рост за 24 ч и за 1 ч, насколько цена ниже максимума часа и суток. Таблица «разбор_по_признакам»:
#     итог шорта по корзинам этих признаков — при каком росте и каком откате от пика шорт работает.
#   • (v2.6) ПРАВИЛО «уже падает»: цена ниже максимума последнего часа хотя бы на XSP_RULE_DH1 (0,5%) и за сутки
#     выросла меньше XSP_RULE_MAX24 (15%). Итоги «по правилу / не по правилу» для каждой группы; в уведомлении —
#     подходит ли кандидат под правило. КОРОТКО: /shortp/brief?t=... — сводка на 15–20 строк (&tg=1 — в Telegram).
#   • (v2.7) ЛОНГ ПОСЛЕ ПАМПА — бумага: события, где монета за сутки до сигнала выросла на 15%+ (их шорт не берёт).
#     Что случилось раньше: рост на +2/+3/+5% (тейк лонга) или падение на −2/−3/−5% (стоп). Таблица в /shortp →
#     «лонг_после_пампа», строка в /shortp/brief. Для этого у новых событий пишется первое касание падения на 3% и 5%.
#     База не ответила при запуске — бумага повторяет загрузку раз в минуту (раньше молча останавливалась).
#   • (v2.8, 28.10) ЦЕНЫ GATE. MEXC для нас закрыт, торгуем на фьючерсах Gate — и бумага теперь считает там же:
#     цены и фандинг новых событий, касания тейка/стопа, ход через 1/4/24 ч, индекс рынка, свечи «что было до
#     сигнала» — всё с фьючерсов Gate (свечей на Gate нет — берутся MEXC). Комиссия — как у Gate (XSP_FEE, 0,1%).
#     Сигналы — только по монетам с фьючерсом Gate. События до перехода остаются в итогах (у них src не «gate»).
#   • (v2.9, 28.10) ВАРИАНТЫ, СКАН, ПРОГНОЗ. Всё — бумага, ничего не торгует.
#     ВАРИАНТЫ: каждый фильтр «что было до входа» (правило, падение за час/сутки, рынок, фандинг, памп за 3 дня…)
#     считается как живой автошорт: $XSP_VAR_MARGIN (20) × х10, тейк 1% или 2%, стоп +5%, держим до 24 ч (и до 4 ч).
#     Отдельно сигналы, кандидаты, окна, скан и случайные монеты с тем же фильтром — видно, где фильтр сам по себе
#     предсказывает падение, а где просто падал рынок. /shortp/var?t=... — таблица текстом. Вариант можно потом
#     включить живому автошорту без нового кода (xshortlive v2.5, XSL_VAR_BY=кандидат:правило).
#     СКАН (XSP_SCAN=1): раз в 5 мин по ВСЕМ фьючерсам Gate — монеты, которые падают прямо сейчас: за час −1,2%+,
#     ниже максимума часа на 1,2%+, за сутки от −30% до +5%, оборот от $300 тыс. Не больше 2 за раз и 24 в сутки,
#     раз в сутки на монету, к каждой — случайная монета для сравнения. Это кандидат в новый источник сделок.
#     ПРОГНОЗ: у каждого кандидата — как закончились похожие ситуации в прошлом (та же корзина: рост за сутки,
#     откат от максимума часа, рынок) — только те, что закончились ДО входа (без подглядывания в будущее).
#     НОВЫЕ ПРИЗНАКИ у новых событий: рынок за сутки (медиана Gate) и за час, доля монет, упавших за час на 1%+,
#     BTC за час, оборот монеты, рост за 4 ч, самый большой рост за 3 дня (памп), статистика фьючерса Gate
#     (лонги/шорты, открытый интерес, ликвидации — что отдаст биржа). У старых событий рынок за сутки — по индексу.
#     /shortp/raw?t=... — все события целиком (для разбора).
#   • (v2.10, 09.11) ПРОГНОЗ ПО ЖИВЫМ УРОВНЯМ. Новые события пишут ещё и касание +7% (стоп живых окон). Прогноз в
#     сообщении о живой сделке — по её тейку и стопу (окна: 2% / 7%); пока событий с отметкой +7% меньше
#     XSP_FC_MIN — по ближайшему (стоп 5%), и это написано прямо в строке. Рынок в корзине — словами:
#     «рынок падает» (средняя монета за сутки −1% и ниже), «рынок растёт» (+1% и выше), «рынок ровный» (между).
import os, sys, time, json, random, statistics, threading, logging, bisect
from collections import deque
from flask import jsonify, request
import mm_scanner as mm
import xpaper
import xexec_api as api

def env(k, d=""):
    return os.getenv(k, d).strip()

VERSION = "xshortp v2.10 · 09.11 · Gate"
ON = env("XSP_ON", "1") == "1"
COOL = float(env("XSP_COOL_H", "2")) * 3600        # одна монета — не чаще раза в 2 ч
HOR = [1, 4, 24]                                   # горизонты, ч
LEVS = [2, 3, 5, 10, 20]
STOP = float(env("XSP_STOP", "0.03"))              # стоп-лосс для варианта «плечо 3 + стоп»
STOP2 = float(env("XSP_STOP2", "0.15"))            # аварийный стоп для варианта «плечо 2 + стоп»
FEE = float(env("XSP_FEE", "0.001"))               # комиссия фьючерса Gate туда-обратно (v2.8; у MEXC была 0,04%)
MXF = "https://contract.mexc.com"                  # запасной источник свечей
GTF = "https://api.gateio.ws/api/v4/futures/usdt"  # v2.8: цены, фандинг и свечи — фьючерсы Gate
GIV = {"Min1": "1m", "Min5": "5m", "Min60": "1h"}  # интервалы свечей: как у MEXC → как у Gate
CTRL = "контроль"
SIGK = ("окно", "всплеск")                         # «все сигналы» — только окна и всплески
AKIND = "аллокатор"                                # монеты, отобранные аллокатором (с этого дня)
RKIND, RCTRL = "аллокатор_история", "контроль_история"   # прошлые заходы аллокатора и случайные монеты к ним
CKIND = "кандидат"                                 # бумага магазина ≥ XSP_CAND_RATE (отбор прежнего аллокатора)
CAND_RATE = float(env("XSP_CAND_RATE", "0.5"))     # $/сутки по бумаге — порог кандидата
ALERT = env("XSP_ALERT", "1") == "1"               # уведомления в Telegram по кандидатам
RULE_DH1 = float(env("XSP_RULE_DH1", "0.005"))     # правило: цена ниже максимума часа хотя бы на 0,5%
RULE_MAX24 = float(env("XSP_RULE_MAX24", "0.15"))  # правило: за сутки выросла меньше чем на 15%
TPS = [0.005, 0.01, 0.02]                          # тейк: цена упала на 0,5 / 1 / 2%
SLS = [0.01, 0.02, 0.03, 0.05]                     # стоп: цена выросла на 1 / 2 / 3 / 5%
SLX = [0.07]                                       # (v2.10) ещё касание +7% — стоп живых окон; только у новых событий (v7)
LIQS = {5: 0.195, 10: 0.095}                       # ликвидация: рост цены, при котором сгорает маржа
DNS = [0.03, 0.05]                                 # (v2.7) падение на 3 / 5% — для стопа лонга после пампа
LTP, LSL = [0.02, 0.03, 0.05], [0.02, 0.03, 0.05]  # лонг после пампа: тейк (рост) и стоп (падение)
LFEE = 0.001                                       # комиссия Gate туда-обратно — для лонга после пампа
SKIND = "скан"                                     # (v2.9) монеты Gate, которые падают прямо сейчас
SCAN = env("XSP_SCAN", "1") == "1"
SCAN_EVERY = float(env("XSP_SCAN_MIN", "5")) * 60
SCAN_CH1 = float(env("XSP_SCAN_CH1", "0.012"))     # за час упала хотя бы на 1,2%
SCAN_DH1 = float(env("XSP_SCAN_DH1", "0.012"))     # и ниже максимума часа хотя бы на 1,2%
SCAN_LO24 = float(env("XSP_SCAN_LO24", "-0.30"))   # за сутки не ниже −30%
SCAN_HI24 = float(env("XSP_SCAN_HI24", "0.05"))    # и не выше +5%
SCAN_VOL = float(env("XSP_SCAN_VOL", "300000"))    # оборот за сутки на Gate, $
SCAN_FUND = float(env("XSP_SCAN_FUND", "-0.002"))  # фандинг не хуже −0,2% (как у живого)
SCAN_MAX = int(env("XSP_SCAN_MAX", "2"))           # монет за один скан
SCAN_DAY = int(env("XSP_SCAN_DAY", "24"))          # монет за сутки
VM = float(env("XSP_VAR_MARGIN", "20"))            # варианты считаются как живой автошорт: маржа $20
VL = int(env("XSP_VAR_LEV", "10"))                 # × плечо 10
VSL = 0.05                                         # стоп +5% — как у живого
VMIN = int(env("XSP_VAR_MIN", "8"))                # в «лучших» — только варианты с 8+ сделками
FC_MIN = int(env("XSP_FC_MIN", "8"))               # прогноз — если похожих ситуаций 8+
DONE_MAX = int(env("XSP_DONE_MAX", "1500"))        # сколько законченных событий хранить
log = logging.getLogger("xshortp")
P = {}
EXTRA = {}                                         # (v2.9) монета → (изменение за сутки, оборот $) — из тикеров Gate
H = {}                                             # монета → deque[(время, цена)] за 70 мин — в памяти
CTX = {}                                           # рынок сейчас: mk (сутки), mk1 (час), br1 (доля упавших), btc1

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'shortp_v1'", fetch=True)
    P.update(json.loads(r[0][0]) if r else {})
    P.setdefault("open", [])
    P.setdefault("done", [])
    P.setdefault("cool", {})
    P.setdefault("rep_t", time.time())
    P.setdefault("ix", {"lvl": 1.0, "last": {}, "since": int(time.time())})
    if P.get("src") != "gate":                         # v2.8: переход на цены Gate — индекс продолжаем с новых цен
        P["ix"]["last"], P["src"], P["gate_since"] = {}, "gate", int(time.time())

def save():
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('shortp_v1', %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(P),))

def tickers():
    """{МОНЕТА: (цена фьючерса, фандинг)} — один запрос на все контракты Gate (v2.8; раньше MEXC)."""
    out, ex = {}, {}
    for x in api.S.get(f"{GTF}/tickers", timeout=20).json() or []:
        c = x.get("contract", "")
        if c.endswith("_USDT") and float(x.get("last") or 0) > 0:
            out[c[:-5]] = (float(x["last"]), float(x.get("funding_rate") or 0))
            try:                                       # (v2.9) рост за сутки и оборот — для скана и рынка
                chg = float(x.get("change_percentage")) / 100
            except (TypeError, ValueError):
                chg = None
            try:
                vol = float(x.get("volume_24h_quote") or x.get("volume_24h_settle") or 0)
            except (TypeError, ValueError):
                vol = 0.0
            ex[c[:-5]] = (chg, vol)
    if ex:
        EXTRA.clear()
        EXTRA.update(ex)
    return out

# ---------- (v2.9) рынок сейчас и скан падающих монет ----------
def hist_step(now, tk):
    """Минутная история цен всех фьючерсов за 70 мин (для «за час» без лишних запросов свечей)."""
    for c, v in tk.items():
        h = H.setdefault(c, deque())
        h.append((now, v[0]))
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
    px = h[-1][1]
    return px / pts[0] - 1, px / max(pts) - 1

def ctx_step(now):
    """Рынок сейчас: медиана изменения за сутки (как у живого), за час, доля монет −1%+ за час, BTC за час."""
    ch = [v[0] for v in EXTRA.values() if v[0] is not None and abs(v[0]) < 5]
    hrs = [x[0] for x in (hour_of(c, now) for c in list(H)) if x]
    b = hour_of("BTC", now)
    CTX.clear()
    CTX.update({"mk": round(statistics.median(ch), 4) if len(ch) >= 20 else None,
                "mk1": round(statistics.median(hrs), 4) if len(hrs) >= 50 else None,
                "br1": round(sum(1 for x in hrs if x <= -0.01) / len(hrs), 3) if len(hrs) >= 50 else None,
                "btc1": round(b[0], 4) if b else None, "t": int(now)})
def scan_step(now, tk, lvl):
    """Скан: монеты Gate, которые падают прямо сейчас. Случайные из прошедших фильтр — не больше SCAN_MAX за раз."""
    if not SCAN or now - P.get("scan_t", 0) < SCAN_EVERY:
        return
    P["scan_t"] = now
    day = P.setdefault("scan_day", {"d": "", "n": 0})
    d = time.strftime("%Y-%m-%d", time.gmtime(now + 8 * 3600))
    if day["d"] != d:
        day.update(d=d, n=0)
    sc = P.setdefault("scool", {})
    busy = {e["coin"] for e in P.get("open", []) if e["kind"] != CTRL}
    vol_seen = any(v[1] > 0 for v in EXTRA.values())  # биржа не отдала оборот — фильтр оборота не применяю
    ok = []
    for c in tk:
        hr, ex = hour_of(c, now), EXTRA.get(c)
        if not hr or not ex or ex[0] is None:
            continue
        if hr[0] > -SCAN_CH1 or hr[1] > -SCAN_DH1 or not SCAN_LO24 <= ex[0] <= SCAN_HI24:
            continue
        if (vol_seen and ex[1] < SCAN_VOL) or tk[c][1] < SCAN_FUND:
            continue
        if now - sc.get(c, 0) < 24 * 3600 or c in busy:
            continue
        ok.append(c)
    P["scan_last"] = [int(now), len(ok)]
    random.shuffle(ok)
    for c in ok[:max(0, min(SCAN_MAX, SCAN_DAY - day["n"]))]:
        if len(P["open"]) >= 400:
            break
        sc[c], day["n"] = now, day["n"] + 1
        e = event(c, SKIND, now, tk, lvl)
        P["open"].append(e)
        pre_now(e)
        cs = pool(tk, {c})
        if cs:
            P["open"].append(event(random.choice(cs), CTRL, now, tk, lvl))

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
    """Монеты для контроля: тот же список, что видит бумага магазина (монеты HTX), с фьючерсом Gate (v2.8)."""
    SP = sys.modules.get("xshopp")
    cs = {k.split(":")[0] for k in (SP.S.get("p") or {})} if SP else set()
    cs = [c for c in (cs or set(tk)) if c in tk and c not in skip]
    return sorted(cs)

def event(coin, kind, now, tk, lvl):
    px, fr = tk[coin]
    e = {"coin": coin, "kind": kind, "t": int(now), "px": px, "fund": fr, "hi": px, "lo": px,
         "r": {}, "adv": {}, "m0": lvl, "mr": {}, "tp": {}, "sl": {}, "dn": {}, "v": 25, "src": "gate", "v7": 1}
    for k in ("mk", "mk1", "br1", "btc1"):            # (v2.9) рынок в момент входа
        if CTX.get(k) is not None and now - CTX.get("t", 0) < 300:
            e[k] = CTX[k]
    ex = EXTRA.get(coin)
    if ex and ex[1]:
        e["vol"] = round(ex[1])
    return e

def step(now, tk):
    lvl = index_step(tk)
    hist_step(now, tk)                                 # (v2.9) история за час и рынок сейчас
    ctx_step(now)
    if now - P.get("ixh_t", 0) >= 600:                 # почасовой след индекса — рынок за сутки для событий
        P["ixh_t"] = now
        P["ixh"] = (P.get("ixh", []) + [[int(now), round(lvl, 6)]])[-500:]
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
                pre_now(P["open"][-1])
                alert_open(P["open"][-1])
                cs = pool(tk, {coin})
                if cs:
                    P["open"].append(event(random.choice(cs), CTRL, now, tk, lvl))
    SP = sys.modules.get("xshopp")                     # 1в) кандидаты по-старому: бумага ≥ порога
    if SP and hasattr(SP, "rate") and now - P.get("cand_t", 0) >= 300:
        P["cand_t"] = now
        cc = P.setdefault("ccool", {})
        lock = getattr(SP, "LOCK", None)
        try:
            if lock:
                lock.acquire()
            keys = [k for k in list(SP.S.get("p") or {}) if not k.endswith(":rev")]
            rates = {}
            for coin in sorted({k.split(":")[0] for k in keys}):
                if coin in tk and now - cc.get(coin, 0) >= 24 * 3600:
                    try:
                        rates[coin] = SP.rate(coin)
                    except Exception:
                        pass
        finally:
            if lock:
                lock.release()
        for coin, rt in sorted(rates.items(), key=lambda kv: -kv[1]):
            if rt < CAND_RATE or len(P["open"]) >= 400:
                continue
            cc[coin] = now
            e = event(coin, CKIND, now, tk, lvl)
            e["paper"] = rt
            P["open"].append(e)
            pre_now(e)
            alert_open(e)
            cs = pool(tk, {coin})
            if cs:
                P["open"].append(event(random.choice(cs), CTRL, now, tk, lvl))
    try:                                               # 1г) (v2.9) скан: монеты Gate, которые падают сейчас
        scan_step(now, tk, lvl)
    except Exception:
        log.exception("xshortp scan")
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
                for x in SLS + list(LIQS.values()) + (SLX if e.get("v7") else []):
                    if mv >= x and str(x) not in e["sl"]:
                        e["sl"][str(x)] = mins
                if "dn" in e:                          # (v2.7) глубокое падение — только у новых событий
                    for x in DNS:
                        if mv <= -x and str(x) not in e["dn"]:
                            e["dn"][str(x)] = mins
            for h in HOR:
                if str(h) not in e["r"] and now - e["t"] >= h * 3600:
                    e["r"][str(h)] = round((e["px"] - px) / e["px"], 5)          # доход шорта без плеча
                    e["adv"][str(h)] = round((e["hi"] - e["px"]) / e["px"], 5)   # худший рост против шорта
                    if e.get("m0"):
                        e.setdefault("mr", {})[str(h)] = round(1 - lvl / e["m0"], 5)   # шорт «всего рынка»
            alert_close(e, now, px)
        if len(e["r"]) == len(HOR) or now - e["t"] > 26 * 3600:
            P["done"] = (P["done"] + [e])[-DONE_MAX:]
        else:
            keep.append(e)
    P["open"] = keep

# ---------- что было до сигнала ----------
FEATS = {"рост_24ч": ("ch24", [(-9, 0, "упала"), (0, 0.05, "0–5%"), (0.05, 0.15, "5–15%"), (0.15, 99, "больше 15%")]),
         "рост_1ч": ("ch1", [(-9, -0.01, "упала >1%"), (-0.01, 0.01, "±1%"), (0.01, 0.03, "1–3%"), (0.03, 99, "больше 3%")]),
         "ниже_максимума_часа": ("dh1", [(-0.005, 9, "у пика (<0,5%)"), (-0.02, -0.005, "0,5–2% ниже"),
                                         (-9, -0.02, "больше 2% ниже")]),
         "ниже_максимума_суток": ("dh24", [(-0.02, 9, "у пика (<2%)"), (-0.06, -0.02, "2–6% ниже"),
                                           (-9, -0.06, "больше 6% ниже")])}

PRE_RU = {"ch24": "рост_24ч_%", "ch1": "рост_1ч_%", "dh1": "ниже_максимума_часа_%", "dh24": "ниже_максимума_суток_%"}
def pre_feats(coin, t0, px):
    """По свечам (Gate, запасной — MEXC) за сутки до входа: рост за 24 ч и за 1 ч, ниже максимума часа и суток."""
    m = [x for x in kl(coin, t0 - 24 * 3600, t0, "Min5") if x[0] < t0 - 1]
    if len(m) < 12:
        return {"err": "мало свечей до входа"}
    h1 = [x for x in m if x[0] >= t0 - 3600]
    o1 = h1[0][1] if h1 else None
    hi1 = max([x[2] for x in h1] + [px])
    hi24 = max([x[2] for x in m] + [px])
    out = {"ch24": round(px / m[0][1] - 1, 4), "ch1": round(px / o1 - 1, 4) if o1 else None,
           "dh1": round(px / hi1 - 1, 4), "dh24": round(px / hi24 - 1, 4)}
    h4 = [x for x in m if x[0] >= t0 - 4 * 3600]       # (v2.9) рост за 4 ч
    if h4 and h4[0][1] > 0:
        out["ch4"] = round(px / h4[0][1] - 1, 4)
    try:                                               # (v2.9) памп за 3 дня: от минимума до максимума после него
        hh = [x for x in kl(coin, t0 - 72 * 3600, t0, "Min60") if x[0] < t0 - 1]
    except Exception:
        hh = []
    if len(hh) >= 24:
        i = max(range(len(hh)), key=lambda k: hh[k][2])
        lo = min(x[3] for x in hh[:i + 1])
        if lo > 0:
            out["up72"] = round(hh[i][2] / lo - 1, 4)
            out["dh72"] = round(px / max(hh[i][2], px) - 1, 4)
    try:
        cs = gate_stats(coin, t0)
        if cs:
            out["cs"] = cs
    except Exception:
        pass
    return out

CS_PRI = ("lsr", "liq", "open_interest", "top_", "long", "short")

def gate_stats(coin, t0):
    """(v2.9) Статистика фьючерса Gate за час до входа (лонги/шорты, открытый интерес, ликвидации): числовые поля
    последней записи (до 12, сначала самые полезные) и изменение открытого интереса за час. Только для разбора."""
    r = api.S.get(f"{GTF}/contract_stats", params={"contract": f"{coin}_USDT", "from": int(t0 - 3600),
                                                    "interval": "5m", "limit": 13}, timeout=15)
    if getattr(r, "status_code", 200) != 200:
        return {}
    d = r.json()
    if not isinstance(d, list):
        return {}
    d = [x for x in d if isinstance(x, dict) and float(x.get("time") or 0) <= t0 + 60]
    if not d:
        return {}
    first, last = d[0], d[-1]
    keys = sorted((k for k in last if k != "time"),
                  key=lambda k: next((i for i, s in enumerate(CS_PRI) if s in k), len(CS_PRI)))
    out = {}
    for k in keys:
        try:
            out[k] = float(f"{float(last[k]):.4g}")
        except (TypeError, ValueError):
            continue
        if len(out) >= 12:
            break
    for k in ("open_interest_usd", "open_interest"):
        try:
            a, b = float(first.get(k) or 0), float(last.get(k) or 0)
        except (TypeError, ValueError):
            continue
        if a > 0:
            out["oi_ch1"] = round(b / a - 1, 4)
            break
    return out

def pre_now(e):
    """Признаки сразу (для уведомления о кандидате)."""
    try:
        e["pre"] = pre_feats(e["coin"], e["t"], e["px"])
    except Exception as x:
        e["pre"] = {"err": str(x)[:80]}

def rule_ok(e):
    """Правило «уже падает»: цена ниже максимума часа на RULE_DH1+ и за сутки рост меньше RULE_MAX24.
    None — признаков ещё нет."""
    pr = e.get("pre") or {}
    if pr.get("dh1") is None or pr.get("ch24") is None:
        return None
    return pr["dh1"] <= -RULE_DH1 and pr["ch24"] < RULE_MAX24

def stat(es):
    """Короткий итог группы: событий, ход шорта через 1 и 24 ч, тейк 1% без стопа при х10 и $50, ликвидации."""
    r1 = [e["r"]["1"] for e in es if "1" in e["r"]]
    r24 = [e["r"]["24"] for e in es if "24" in e["r"]]
    ns = [p for p in (nostop(e, 0.01, 10) for e in es if "tp" in e) if p is not None]
    return {"n": len(es), "r1": sum(r1) / len(r1) * 100 if r1 else None,
            "r24": sum(r24) / len(r24) * 100 if r24 else None,
            "p24": sum(1 for r in r24 if r > 0) / len(r24) * 100 if r24 else None,
            "tp": sum(ns) * 50 if ns else None, "ntp": len(ns), "liq": sum(1 for p in ns if p == -1.0),
            "worst": max([e["hi"] / e["px"] - 1 for e in es] or [0]) * 100}

def stat_line(name, st):
    if not st["n"]:
        return f"{name}: нет"
    f = lambda x, d="": "—" if x is None else f"{x:+.1f}%{d}"
    s = f"{name}: {st['n']} | 1ч {f(st['r1'])} | 24ч {f(st['r24'])}"
    if st["p24"] is not None:
        s += f" ({st['p24']:.0f}% в плюсе)"
    if st["ntp"]:
        s += f" | тейк 1% х10 $50: {st['tp']:+.0f}$ ({st['ntp']} сд., ликв. {st['liq']})"
    return s + f" | хуже всего +{st['worst']:.0f}%"

def rule_split(kind=None, src=None):
    es = [e for e in _src(src) if _is(e, kind)]
    return ({"по правилу": stat([e for e in es if rule_ok(e) is True]),
             "не по правилу": stat([e for e in es if rule_ok(e) is False])})

def brief():
    """Короткая сводка для копирования: 15–20 строк."""
    hist = [e for v in (P.get("retro") or {}).values() for e in v.get("ev", [])]
    L = [f"📉 {VERSION}, {time.strftime('%d.%m %H:%M', time.gmtime(time.time() + 8 * 3600))} (бумага)",
         f"Правило «уже падает»: ниже максимума часа ≥{RULE_DH1*100:g}%, рост за сутки <{RULE_MAX24*100:g}%"]
    if P.get("gate_since"):                            # v2.8: с какого момента цены Gate
        ng = sum(1 for e in _src() if e.get("src") == "gate" and e["kind"] != CTRL)
        L.append(f"Цены: фьючерсы Gate с {time.strftime('%d.%m %H:%M', time.gmtime(P['gate_since'] + 8 * 3600))} "
                 f"(событий по Gate: {ng}), до этого — MEXC; итоги общие, комиссия {FEE * 100:g}%")
    for title, kind, src in (("История аллокатора", RKIND, hist), ("  случайные к ней", RCTRL, hist),
                             ("Кандидаты", CKIND, None), ("Окна и всплески", None, None), ("Случайные", CTRL, None),
                             ("Скан (падают сейчас)", SKIND, None)):
        es = [e for e in _src(src) if _is(e, kind)]
        if kind == SKIND and not es:
            continue
        L.append(stat_line(title, stat(es)))
        if kind == SKIND:                              # скан почти весь «по правилу» — разбивка не нужна
            continue
        rs = rule_split(kind, src)
        if rs["по правилу"]["n"] or rs["не по правилу"]["n"]:
            L.append("  " + stat_line("по правилу", rs["по правилу"]))
            L.append("  " + stat_line("не по правилу", rs["не по правилу"]))
    if CTX.get("mk") is not None or CTX.get("mk1") is not None:   # (v2.9) рынок сейчас
        f = lambda x, d=1: "—" if x is None else f"{x * 100:+.{d}f}%"
        sl = P.get("scan_last") or [0, 0]
        L.append(f"Рынок Gate сейчас: сутки {f(CTX.get('mk'))}, час {f(CTX.get('mk1'), 2)}, BTC за час "
                 f"{f(CTX.get('btc1'), 2)}, за час упали на 1%+ "
                 + ("—" if CTX.get("br1") is None else f"{CTX['br1'] * 100:.0f}% монет")
                 + (f" · под фильтр скана: {sl[1]} монет" if SCAN and time.time() - sl[0] < 900 else ""))
    try:
        L += var_lines()
    except Exception as x:
        log.exception("xshortp var_lines")
        L.append(f"Варианты: ошибка {str(x)[:80]}")
    ix = fc_index()
    now_c = [f"{e['coin']} {time.strftime('%H:%M', time.gmtime(e['t'] + 8 * 3600))}"
             + (" ✅" if rule_ok(e) else " ❌" if rule_ok(e) is False else "")
             + (lambda f: f" [{f['тейков %']}% тейков из {f['похожих']}]" if f and f["похожих"] >= FC_MIN else "")(
                 forecast(e, ix))
             for e in P.get("open", []) if e["kind"] in (CKIND, AKIND, SKIND)]
    L.append("Кандидаты и скан сейчас: " + (", ".join(now_c[-8:]) if now_c else "нет")
             + (f" (в скобках — прогноз: у скольких похожих тейк 1% был раньше стопа 5%; для нуля нужно "
                f"{(VSL + FEE) / (0.01 + VSL) * 100:.0f}%)" if any("[" in x for x in now_c) else ""))
    L.append(long_line())
    return "\n".join(L)

# ---------- (v2.7) лонг после пампа — бумага ----------
def long_events():
    """События, где монета за сутки до сигнала выросла на RULE_MAX24 (15%) и больше — шорт их не берёт."""
    return [e for e in _src() if e["kind"] != CTRL and "tp" in e
            and (e.get("pre") or {}).get("ch24") is not None and e["pre"]["ch24"] >= RULE_MAX24]

def long_res(e, tp, sl):
    """Лонг: "tp" — раньше был рост на tp; "sl" — раньше падение на sl (в одну минуту — считаем стоп);
    число — ни того ни другого за сутки (доход лонга); None — ещё неизвестно или нет данных."""
    up = e["sl"].get(str(tp))
    if sl in TPS:
        dn = e["tp"].get(str(sl))
    elif "dn" in e:
        dn = e["dn"].get(str(sl))
    else:
        return None                                    # старое событие: падение на 3/5% не записывалось
    if up is not None and (dn is None or up < dn):
        return "tp"
    if dn is not None:
        return "sl"
    if "24" in e["r"]:
        return -e["r"]["24"]                           # r — доход шорта, у лонга наоборот
    return None

def long_table():
    """Лонг после пампа при $10 маржи и х10 ($100), комиссия Gate: тейк +2/3/5% × стоп −2/3/5%."""
    es, out = long_events(), {}
    for tp in LTP:
        for sl in LSL:
            rs = [x for x in (long_res(e, tp, sl) for e in es) if x is not None]
            if not rs:
                continue
            n = len(rs)
            usd = sum(100 * tp if x == "tp" else -100 * sl if x == "sl" else 100 * x for x in rs) - 100 * LFEE * n
            out[f"+{tp * 100:g}% / −{sl * 100:g}%"] = {
                "сделок": n, "тейков %": round(sum(1 for x in rs if x == "tp") / n * 100),
                "стопов %": round(sum(1 for x in rs if x == "sl") / n * 100), "$ на $10 х10": round(usd, 2),
                "нужно тейков для нуля %": round((sl + LFEE) / (tp + sl) * 100, 1)}
    return out

def long_page():
    es = long_events()
    ev = [{"монета": e["coin"], "вид": e["kind"], "время": time.strftime("%d.%m %H:%M", time.gmtime(e["t"] + 8 * 3600)),
           "рост_24ч_до_%": round(e["pre"]["ch24"] * 100, 1),
           "вверх_мин": {k: e["sl"].get(k) for k in ("0.02", "0.03", "0.05")},
           "вниз_мин": {"0.02": e["tp"].get("0.02"), **{k: (e.get("dn") or {}).get(k) for k in ("0.03", "0.05")}},
           "макс_вверх_%": round((e["hi"] / e["px"] - 1) * 100, 1), "макс_вниз_%": round((e["lo"] / e["px"] - 1) * 100, 1)}
          for e in es[-12:]]
    return {"сигналов": len(es), "таблица_тейк_вверх_/_стоп_вниз": long_table(), "последние": ev}
def long_line():
    t = long_table()
    parts = [f"{k}: {v['тейков %']}% тейков, {v['$ на $10 х10']:+.1f}$ ({v['сделок']} сд.)"
             for k, v in t.items() if k in ("+2% / −2%", "+3% / −3%", "+5% / −3%")]
    return (f"Лонг после пампа (рост за сутки ≥{RULE_MAX24 * 100:g}%, $10 х10): {len(long_events())} сигн."
            + (" | " + " | ".join(parts) if parts else " — итогов пока нет"))

def pre_step(now):
    """До 6 событий за минуту: признаки «что было до сигнала» (свечи до входа не меняются — можно позже).
    Старые события (до 7 дней) тоже: у них нет тейков, но есть ход через 1/4/24 ч."""
    n = 0
    for e in reversed(P.get("open", []) + P.get("done", [])[-300:]):
        if "pre" in e or now - e["t"] > 7 * 86400:
            continue
        try:
            e["pre"] = pre_feats(e["coin"], e["t"], e["px"])
        except Exception as x:
            e["pre"] = {"err": str(x)[:80]}
        n += 1
        if n >= 6:
            return

def split(kind=None, src=None):
    """Итог шорта по корзинам признаков: событий, ход через 1 ч и 24 ч, тейк 1% без стопа при х10 и $50."""
    es = [e for e in _src(src) if _is(e, kind) and (e.get("pre") or {}).get("ch24") is not None]
    out = {}
    for name, (key, bks) in FEATS.items():
        rows = {}
        for lo, hi, label in bks:
            g = [e for e in es if e["pre"].get(key) is not None and lo <= e["pre"][key] < hi]
            if not g:
                continue
            r1 = [e["r"]["1"] for e in g if "1" in e["r"]]
            r24 = [e["r"]["24"] for e in g if "24" in e["r"]]
            ns = [p for p in (nostop(e, 0.01, 10) for e in g if "tp" in e) if p is not None]
            rows[label] = {"событий": len(g),
                           "шорт_1ч_%": round(sum(r1) / len(r1) * 100, 2) if r1 else None,
                           "шорт_24ч_%": round(sum(r24) / len(r24) * 100, 2) if r24 else None,
                           "в_плюсе_24ч_%": round(sum(1 for r in r24 if r > 0) / len(r24) * 100) if r24 else None,
                           "тейк_1%_х10_$50": round(sum(ns) * 50, 2) if ns else None,
                           "ликвидаций_х10": sum(1 for p in ns if p == -1.0),
                           "хуже_всего_против_%": round(max(e["hi"] / e["px"] - 1 for e in g) * 100, 1)}
        out[name] = rows
    return out

# ---------- уведомления по кандидатам ----------
def alert_open(e):
    if not ALERT or e["kind"] not in (AKIND, CKIND):
        return
    px = e["px"]
    who = "аллокатор отобрал" if e["kind"] == AKIND else "бумага ${:.2f}/сут".format(e.get("paper", 0))
    pr, ok = e.get("pre") or {}, rule_ok(e)
    ctx = ""
    if pr.get("ch24") is not None:
        ctx = "За сутки {:+.1f}%, за час {:+.1f}%, ниже максимума часа на {:.1f}% — {}\n".format(
            pr["ch24"] * 100, (pr.get("ch1") or 0) * 100, abs(pr["dh1"]) * 100,
            "✅ по правилу «уже падает»" if ok else "❌ не по правилу (ещё у пика или сильный рост)")
    fc = fc_text(e)                                    # (v2.9) как закончились похожие ситуации
    mm.tg("📉 Кандидат на шорт: {} ({}), цена Gate {:.6g}\n{}"
          "Тейк 1%: {:.6g} · тейк 2%: {:.6g} · ликвидация при х10: {:.6g} (+9,5%)\n{}"
          "Бумажная проверка, не сигнал к сделке".format(e["coin"], who, px, ctx, px * 0.99, px * 0.98, px * 1.095,
                                                        f"Прогноз: {fc}\n" if fc else ""))

def alert_close(e, now, px):
    """Что случилось раньше: тейк 1% или +9,5% против; ни того ни другого за сутки — итог шорта."""
    if not ALERT or e["kind"] not in (AKIND, CKIND) or e.get("al"):
        return
    a, b, c = e["tp"].get("0.01"), e["sl"].get("0.095"), e["coin"]
    if a is not None and (b is None or a < b):
        e["al"] = 1
        mm.tg("✅ {}: тейк 1% через {} мин — при $50 и х10 ≈ +$4,80 (бумага). Хуже всего по пути: +{:.1f}% против"
              .format(c, a, (e["hi"] / e["px"] - 1) * 100))
    elif b is not None:
        e["al"] = 1
        mm.tg("❌ {}: цена +9,5% против шорта через {} мин — при х10 это ликвидация, −$50 (бумага)".format(c, b))
    elif now - e["t"] >= 24 * 3600:
        e["al"] = 1
        mm.tg("⏱ {}: за сутки ни тейка 1%, ни ликвидации — шорт {:+.1f}% (бумага)"
              .format(c, (e["px"] - px) / e["px"] * 100))

# ---------- история: прошлые заходы аллокатора по свечам MEXC ----------
def kl(coin, start, end, iv):
    """Свечи фьючерса: [(время, open, high, low, close)]. v2.8: сначала Gate (там торгуем), не дал — MEXC."""
    try:
        out = kl_gate(coin, start, end, iv)
        if out:
            return out
    except Exception:
        pass
    return kl_mexc(coin, start, end, iv)

def kl_gate(coin, start, end, iv):
    """Свечи фьючерса Gate: [(время, open, high, low, close)] — время начала свечи, секунды."""
    r = api.S.get(f"{GTF}/candlesticks", params={"contract": f"{coin}_USDT", "interval": GIV.get(iv, "5m"),
                                                 "from": int(start), "to": int(end)}, timeout=20)
    if getattr(r, "status_code", 200) != 200:
        return []
    out = []
    for x in r.json() or []:
        if isinstance(x, dict):
            out.append((int(float(x["t"])), float(x["o"]), float(x["h"]), float(x["l"]), float(x["c"])))
        elif isinstance(x, (list, tuple)) and len(x) >= 6:   # вид как у спота: [время, объём, close, high, low, open]
            out.append((int(float(x[0])), float(x[5]), float(x[3]), float(x[4]), float(x[2])))
    return sorted(out)

def kl_mexc(coin, start, end, iv):
    """Свечи фьючерса MEXC (запасной источник): [(время, open, high, low, close)]."""
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
        raise ValueError("нет свечей ни на Gate, ни на MEXC")
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
    e["hi"] = hi
    try:
        e["pre"] = pre_feats(coin, t0, px)
    except Exception as x:
        e["pre"] = {"err": str(x)[:80]}
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
        if now - t0 < 24 * 3600 + 600 or (old and old.get("final") and old.get("fv") == 25):
            continue                                   # сутки ещё не прошли / посчитано окончательно (v2.5)
        if old and old.get("fv") == 25 and now - old.get("at", 0) < (3600 if old.get("err") else 6 * 3600):
            continue                                   # ошибка — повтор через час; ещё держим — раз в 6 ч
        try:
            ev = [retro_event(coin, t0, RKIND, ex, now)]
            rnd = random.Random(t0)
            for c in rnd.sample(pool(tk, {coin}), min(3, len(pool(tk, {coin})))):
                try:
                    ev.append(retro_event(c, t0, RCTRL, ex, now))
                except Exception:
                    pass
            R[key] = {"ev": ev, "at": now, "final": bool(ex) and now - t0 > 50 * 3600, "fv": 25}
        except Exception as e:
            R[key] = {"err": str(e)[:120], "at": now, "fv": 25}
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
                     "до_входа": {PRE_RU.get(k, k): f(x) for k, x in (e.get("pre") or {}).items() if k != "err"}
                     or e.get("pre"),
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

# ---------- (v2.9) варианты: какой фильтр «что было до входа» даёт плюс у живого автошорта ----------
def pf(e, k):
    return (e.get("pre") or {}).get(k)

def _c(x, f):
    """Условие по признаку: None — признака нет (старое событие или свечи не пришли)."""
    return None if x is None else bool(f(x))

def _all(*xs):
    """«И» с учётом «нет признака»: хоть одно ложно — False; чего-то нет — None; иначе True."""
    if any(x is False for x in xs):
        return False
    return None if any(x is None for x in xs) else True

def mkt(e):
    """Рынок за сутки при входе: медиана Gate (новые события) или по индексу бумаги (старые)."""
    return e["mk"] if e.get("mk") is not None else e.get("mkx")

def ensure_mkx():
    """Старым событиям — рынок за сутки по индексу бумаги: уровень при входе / уровень сутки назад − 1."""
    pts = sorted([(e["t"], e["m0"]) for e in _src() if e.get("m0")] +
                 [(int(t), l) for t, l in P.get("ixh", []) if l])
    if len(pts) < 10:
        return
    ts = [p[0] for p in pts]
    for e in _src():
        if e.get("mk") is not None or e.get("mkx") is not None or not e.get("m0"):
            continue
        want = e["t"] - 86400
        i = bisect.bisect_left(ts, want)
        near = [pts[j] for j in (i - 1, i) if 0 <= j < len(pts)]
        if not near:
            continue
        p = min(near, key=lambda q: abs(q[0] - want))
        if abs(p[0] - want) <= 2700 and p[1] > 0:
            e["mkx"] = round(e["m0"] / p[1] - 1, 4)

def v_imp(e):
    return _all(_c(pf(e, "ch1"), lambda x: x <= -0.01), _c(pf(e, "dh1"), lambda x: x <= -0.01),
                _c(pf(e, "ch24"), lambda x: -0.20 <= x < 0))

def v_rule(e):
    return rule_ok(e)

FCX = {"ix": None}                                     # индекс прогноза на время одного расчёта таблицы
def v_fc(e):
    """Прогноз «за»: похожих 8+, и тейк 1% раньше стопа 5% у них не реже, чем нужно для нуля."""
    f = forecast(e, FCX.get("ix"))
    if not f or f["похожих"] < FC_MIN:
        return None
    return f["тейков %"] >= f["нужно %"]

VARS = [
    ("все", "все события", lambda e: True),
    ("правило", "правило «уже падает»: ниже максимума часа на 0,5%+, за сутки рост <15%", v_rule),
    ("не_правило", "НЕ по правилу: у максимума часа или памп", lambda e: None if v_rule(e) is None else not v_rule(e)),
    ("без_пампа", "за сутки рост меньше 15%", lambda e: _c(pf(e, "ch24"), lambda x: x < 0.15)),
    ("час_вниз", "за час упала на 1%+", lambda e: _c(pf(e, "ch1"), lambda x: x <= -0.01)),
    ("час_ровно", "за час от −1% до +1%", lambda e: _c(pf(e, "ch1"), lambda x: -0.01 < x < 0.01)),
    ("откат_2", "ниже максимума часа на 2%+", lambda e: _c(pf(e, "dh1"), lambda x: x <= -0.02)),
    ("сутки_0_−5", "за сутки от 0 до −5%", lambda e: _c(pf(e, "ch24"), lambda x: -0.05 < x <= 0)),
    ("сутки_−5_−15", "за сутки −5…−15%", lambda e: _c(pf(e, "ch24"), lambda x: -0.15 < x <= -0.05)),
    ("сутки_ниже_−15", "за сутки −15% и ниже (риск отскока)", lambda e: _c(pf(e, "ch24"), lambda x: x <= -0.15)),
    ("сутки_рост", "за сутки рост 0…15%", lambda e: _c(pf(e, "ch24"), lambda x: 0 < x < 0.15)),
    ("импульс", "за час −1%+, ниже максимума часа на 1%+, за сутки 0…−20%", v_imp),
    ("рынок_падает", "рынок за сутки −1% и ниже", lambda e: _c(mkt(e), lambda x: x <= -0.01)),
    ("рынок_ровный", "рынок за сутки −1…+1%", lambda e: _c(mkt(e), lambda x: -0.01 < x < 0.01)),
    ("рынок_растёт", "рынок за сутки +1% и выше", lambda e: _c(mkt(e), lambda x: x >= 0.01)),
    ("правило+рынок", "правило и рынок не растёт (<+1%)", lambda e: _all(v_rule(e), _c(mkt(e), lambda x: x < 0.01))),
    ("импульс+рынок", "импульс и рынок не растёт (<+1%)", lambda e: _all(v_imp(e), _c(mkt(e), lambda x: x < 0.01))),
    ("фандинг_плюс", "фандинг ≥0 — шорт получает", lambda e: e.get("fund", 0) >= 0),
    ("без_пампа_3дня", "за 3 дня не было роста 25%+ (признак с 28.10)", lambda e: _c(pf(e, "up72"), lambda x: x < 0.25)),
    ("памп_3дня", "за 3 дня был рост 25%+ (признак с 28.10)", lambda e: _c(pf(e, "up72"), lambda x: x >= 0.25)),
    ("рынок_час_вниз", "рынок за час −0,3% и ниже (с 28.10)", lambda e: _c(e.get("mk1"), lambda x: x <= -0.003)),
    ("btc_час_вниз", "BTC за час −0,3% и ниже (с 28.10)", lambda e: _c(e.get("btc1"), lambda x: x <= -0.003)),
    ("много_падают", "за час упали на 1%+ четверть монет и больше (с 28.10)",
     lambda e: _c(e.get("br1"), lambda x: x >= 0.25)),
    ("прогноз_за", "похожие ситуации раньше дали тейк 1% не реже, чем нужно (8+ похожих)", v_fc),
]
VMAP = {k: f for k, _, f in VARS}
VDESC = {k: d for k, d, _ in VARS}
VGROUPS = [("сигналы", lambda e: e["kind"] not in (CTRL, RKIND, RCTRL)), ("кандидаты", lambda e: e["kind"] == CKIND),
           ("окна", lambda e: e["kind"] in SIGK), ("скан", lambda e: e["kind"] == SKIND),
           ("случайные", lambda e: e["kind"] == CTRL)]
VSET = [("тейк 1% / стоп 5% / до 24 ч", 0.01, 1440), ("тейк 2% / стоп 5% / до 24 ч", 0.02, 1440),
        ("тейк 1% / стоп 5% / до 4 ч", 0.01, 240)]

def var_ok(code, e):
    """Для живого автошорта: подходит ли событие под вариант. «правило&без_пампа_3дня» — оба сразу.
    True / False; None — признаков нет или варианта такого нет (живой тогда не заходит)."""
    parts = [x.strip() for x in str(code).split("&") if x.strip()]
    if not parts or any(x not in VMAP for x in parts):
        return None
    if "прогноз_за" in parts:
        FCX["ix"] = fc_index()
    try:
        return _all(*[VMAP[x](e) for x in parts])
    except Exception:
        return None

def outcome(e, tp, hold, sl=None):
    """Сделка «тейк tp / стоп sl (по умолчанию 5%)», держим до hold мин: (доход шорта без плеча, "tp"/"sl"/"time",
    минут) или None — ещё не закончилась или нет замеров. Тейк и стоп в одну минуту — считаем стоп (осторожно)."""
    if "tp" not in e or "sl" not in e:
        return None
    sl = sl or VSL
    if sl in SLX and not e.get("v7"):                  # +7% пишут только новые события
        return None
    a, b = e["tp"].get(str(tp)), e["sl"].get(str(sl))
    a = a if a is not None and a <= hold else None
    b = b if b is not None and b <= hold else None
    if a is not None and (b is None or a < b):
        return tp, "tp", a
    if b is not None:
        return -sl, "sl", b
    h = "24" if hold >= 1440 else "4" if hold >= 240 else "1"
    if h in e["r"]:
        return e["r"][h], "time", hold
    return None

def usd_of(e, o):
    """$ живого автошорта ($VM × хVL): ход шорта минус комиссия плюс фандинг за время сделки."""
    return VM * VL * (o[0] - FEE + e.get("fund", 0) * o[2] / 480)

def var_stats(es, tp, hold):
    rs = [(e, o) for e, o in ((e, outcome(e, tp, hold)) for e in es) if o is not None]
    if not rs:
        return None
    n, usd = len(rs), sum(usd_of(e, o) for e, o in rs)
    tm = [o[2] for _, o in rs if o[1] == "tp"]
    return {"сделок": n, "тейков %": round(len(tm) / n * 100), "стопов %": round(sum(1 for _, o in rs if o[1] == "sl") / n * 100),
            "нужно %": round((VSL + FEE) / (tp + VSL) * 100, 1), "$": round(usd, 2), "$ на сделку": round(usd / n, 2),
            "тейк_через_мин_медиана": int(statistics.median(tm)) if tm else None}

def var_table():
    """{вариант: {"что": описание, группа: {настройка: итог}}} — как живой: $VM × хVL, стоп 5%."""
    ensure_mkx()
    FCX["ix"] = fc_index()
    src = _src()
    out = {}
    for code, desc, f in VARS:
        row = {"что": desc}
        for gname, gf in VGROUPS:
            es = []
            for e in src:
                if not gf(e):
                    continue
                try:
                    ok = f(e)
                except Exception:
                    ok = None
                if ok is True:
                    es.append(e)
            g = {}
            for label, tp, hold in VSET:
                s = var_stats(es, tp, hold)
                if s:
                    g[label] = s
            if g:
                row[gname] = g
        out[code] = row
    return out

def var_rows(T, label, group="сигналы", nmin=None, base=False):
    """[(доход на сделку, вариант, итог, итог у случайных)] — для вариантов с nmin+ (VMIN) сделок."""
    rows, nmin = [], VMIN if nmin is None else nmin
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

def var_lines(T=None, top=2):
    """Для brief: лучшие варианты сигналов при тейке 1% и 2% и худший."""
    T = T or var_table()
    out = [f"Варианты как живой (${VM:g} × х{VL}, стоп 5%), сигналы — лучшие по $ на сделку:"]
    for label, _, _ in VSET[:2]:
        rows = var_rows(T, label)
        if not rows:
            continue
        nm = label.split(" / ")[0]
        out += [f"  {nm}: " + var_fmt(code, s, c) for _, code, s, c in rows[:top]]
        w = rows[-1]
        out.append(f"  {nm}, хуже всего: " + var_fmt(w[1], w[2], w[3]))
    base = (T.get("все") or {})
    for g in ("сигналы", "случайные"):
        s = (base.get(g) or {}).get(VSET[0][0])
        if s:
            out.append(f"  все {g} (тейк 1%): {s['сделок']} сд., тейков {s['тейков %']}%, {s['$ на сделку']:+.2f}/сд.")
    return out if len(out) > 1 else []

def var_text():
    """Страница /shortp/var: все варианты по каждой настройке — сигналы, кандидаты, скан и случайные."""
    T = var_table()
    L = [f"📊 {VERSION} — варианты как живой автошорт: ${VM:g} × х{VL}, стоп +5%, комиссия {FEE * 100:g}%, фандинг учтён",
         "Сделка: что раньше — тейк или стоп (по минутам; прострелы внутри минуты не видны — живой хуже).",
         "Ни того ни другого — закрытие через 24 ч (или 4 ч). Сравнивай с «случайные»: если там так же — это рынок.", ""]
    for label, tp, _ in VSET:
        L.append(f"■ {label} (для нуля нужно {(VSL + FEE) / (tp + VSL) * 100:.1f}% тейков)")
        for _, code, s, c in var_rows(T, label, nmin=1, base=True):
            k = (T[code].get("кандидаты") or {}).get(label)
            sc = (T[code].get("скан") or {}).get(label)
            L.append("  " + var_fmt(code, s, c)
                     + (f" | кандидаты: {k['сделок']} сд., {k['тейков %']}%, {k['$ на сделку']:+.2f}/сд." if k else "")
                     + (f" | скан: {sc['сделок']} сд., {sc['тейков %']}%, {sc['$ на сделку']:+.2f}/сд." if sc else ""))
        L.append("")
    L.append("Что значит каждый вариант:")
    L += [f"  {k} — {d}" for k, d, _ in VARS]
    return "\n".join(L)

# ---------- (v2.9) прогноз по похожим ситуациям ----------
def bucket(e):
    """Корзина ситуации: рост за сутки × откат от максимума часа × рынок за сутки."""
    ch, dh, mk = pf(e, "ch24"), pf(e, "dh1"), mkt(e)
    if ch is None or dh is None:
        return None
    a = ("памп" if ch >= 0.15 else "рост" if ch > 0 else "0…−5%" if ch > -0.05 else "−5…−15%" if ch > -0.15
         else "≤−15%")
    b = "у пика часа" if dh > -0.005 else "откат 0,5–2%" if dh > -0.02 else "падает 2%+"
    c = "рынок ?" if mk is None else "рынок падает" if mk <= -0.01 else "рынок растёт" if mk >= 0.01 else "рынок ровный"
    return a, b, c

def fc_index(tp=0.01, sl=None):
    """Корзина → [(когда сделка закончилась, исход)] по всем событиям, кроме истории аллокатора."""
    ensure_mkx()
    ix = {}
    for x in _src():
        if x["kind"] in (RKIND, RCTRL):
            continue
        k = bucket(x)
        o = outcome(x, tp, 1440, sl) if k else None
        if o:
            ix.setdefault(k, []).append((x["t"] + o[2] * 60, o[1]))
    for v in ix.values():
        v.sort()
    return ix

def forecast(e, ix=None, tp=0.01, sl=None):
    """Как закончились похожие ситуации, которые закончились ДО входа: похожих, тейков %, стопов %."""
    k = bucket(e)
    if not k:
        return None
    sl = sl or VSL
    rows = (ix if ix is not None else fc_index(tp, sl)).get(k, [])
    rows = [o for t, o in rows[:bisect.bisect_left(rows, (e["t"],))]]
    n = len(rows)
    return {"корзина": " · ".join(k), "похожих": n, "нужно %": round((sl + FEE) / (tp + sl) * 100, 1),
            "тейк": tp, "стоп": sl,
            "тейков %": round(sum(1 for o in rows if o == "tp") / n * 100) if n else None,
            "стопов %": round(sum(1 for o in rows if o == "sl") / n * 100) if n else None}
def fc_text(e, ix=None, tp=0.01, sl=None):
    """Строка прогноза для уведомлений и brief (пусто — признаков ещё нет).
    (v2.10) tp/sl — уровни живой сделки; по стопу +7% мало данных — берём ближайший 5% и пишем об этом."""
    note = ""
    try:
        f = forecast(e, ix if (tp == 0.01 and not sl) else None, tp, sl)
        if f and sl and sl != VSL and f["похожих"] < FC_MIN:
            n7 = f["похожих"]
            f = forecast(e, None, tp, VSL)
            note = f" — по стопу {sl * 100:g}% похожих пока {n7}, считаю по стопу {VSL * 100:g}%"
    except Exception:
        return ""
    if not f:
        return ""
    if f["похожих"] < FC_MIN:
        return f"похожих ситуаций {f['похожих']} — мало для прогноза ({f['корзина']})"
    return (f"похожих {f['похожих']} ({f['корзина']}): тейк {f['тейк'] * 100:g}% раньше стопа {f['стоп'] * 100:g}% — "
            f"{f['тейков %']}%, стоп — {f['стопов %']}% (для нуля нужно {f['нужно %']:g}%){note}")

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
    cn, nc2 = nostop_table(CKIND), nostop_table(CTRL)
    for k in ("тейк 1% / без стопа", "тейк 2% / без стопа"):
        x, y = cn.get(k) or {}, nc2.get(k) or {}
        if x.get("х10: сделок"):
            L.append(f"Кандидаты (бумага ≥ ${CAND_RATE:g}), {k}, х10, $50: сделок {x['х10: сделок']}, тейков "
                     f"{x['х10: тейков %']}%, ликвидаций {x['х10: ликвидаций']}, итог {x['х10: $ при $50 маржи']:+.2f}$" +
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
    while True:                                        # база не ответила — повтор через минуту, а не тишина до деплоя
        try:
            load()
            break
        except Exception:
            log.exception("xshortp load")
            time.sleep(60)
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
            try:
                pre_step(now)
            except Exception:
                log.exception("xshortp pre")
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
                    "кандидаты_по_старому": {"порог_$_сутки": CAND_RATE, "итог": summary(CKIND),
                                             "без_стопа": nostop_table(CKIND), "тейк_стоп": tpsl_table(CKIND),
                                             "сейчас": [[e["coin"], e.get("paper"), time.strftime(
                                                 "%d.%m %H:%M", time.gmtime(e["t"] + 8 * 3600))]
                                                 for e in P.get("open", []) if e["kind"] == CKIND]},
                    "история_аллокатора": retro_page(),
                    "разбор_по_признакам": {
                        "история_аллокатора": split(RKIND, [e for v in (P.get("retro") or {}).values()
                                                            for e in v.get("ev", [])]),
                        "история_случайные": split(RCTRL, [e for v in (P.get("retro") or {}).values()
                                                           for e in v.get("ev", [])]),
                        "кандидаты": split(CKIND), "окна_и_всплески": split(), "только_всплески": split("всплеск"),
                        "случайные": split(CTRL)},
                    "лонг_после_пампа": long_page(),
                    "рынок_сейчас": dict(CTX),
                    "скан": {"включён": SCAN, "событий": sum(1 for e in _src() if e["kind"] == SKIND),
                             "сегодня": (P.get("scan_day") or {}).get("n"),
                             "прошли_фильтр_в_последний_раз": P.get("scan_last"), "итог": summary(SKIND),
                             "тейк_стоп": tpsl_table(SKIND)},
                    "варианты": var_table(),
                    "последние": [e for e in _src() if e["kind"] != CTRL][-15:]})

@mm.app.route("/shortp/var")
def shortp_var():
    """(v2.9) Варианты фильтров как живой автошорт — текстом: /shortp/var?t=..."""
    if not (mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN):
        return "forbidden", 403
    return var_text(), 200, {"Content-Type": "text/plain; charset=utf-8"}

RAW_KEYS = ("coin", "kind", "t", "px", "fund", "pre", "tp", "sl", "dn", "r", "adv", "hi", "lo", "m0", "mk", "mkx",
            "mk1", "br1", "btc1", "vol", "paper", "src", "v")

@mm.app.route("/shortp/raw")
def shortp_raw():
    """(v2.9) Все события целиком за &days= (по умолчанию 14) — для разбора: /shortp/raw?t=..."""
    if not (mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN):
        return "forbidden", 403
    try:
        days = float(request.args.get("days", "14"))
    except ValueError:
        days = 14.0
    ensure_mkx()
    since = time.time() - days * 86400
    ev = [{k: e[k] for k in RAW_KEYS if k in e} for e in _src() if e["t"] >= since]
    return jsonify({"версия": VERSION, "событий": len(ev), "события": ev})

@mm.app.route("/shortp/brief")
def shortp_brief():
    """Короткая сводка: /shortp/brief?t=... (&tg=1 — отправить в Telegram)."""
    if not (mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN):
        return "forbidden", 403
    txt = brief()
    if request.args.get("tg") == "1":
        mm.tg(txt)
    return txt, 200, {"Content-Type": "text/plain; charset=utf-8"}

if ON:
    threading.Thread(target=loop, daemon=True).start()
