# xshortlive.py — АВТОШОРТ по ✅-кандидатам на фьючерсах GATE. По умолчанию БУМАГА: только пишет, что сделал бы.
#   v2 (24.10): MEXC закрыл для аккаунта открытие позиций — торговля перенесена на фьючерсы Gate (USDT, одиночный
#   режим позиций, изолированная маржа). Сигналы — по-прежнему из бумаги xshortp (публичные цены MEXC).
#   Сигнал: в xshortp появился кандидат («аллокатор» — монету отобрал аллокатор, или «кандидат» — бумага магазина
#   ≥ $0,5/сут), и он ✅ по правилу «уже падает»: цена ниже максимума часа хотя бы на 0,5% и за сутки выросла
#   меньше чем на 15%. Памп (рост за сутки ≥ XSL_MAX24) — никогда. XSL_RULE=pump — брать и ❌, кроме пампов.
#   Что делает: шорт на фьючерсе Gate (маржа XSL_MARGIN $, плечо XSL_LEV) и сразу НА БИРЖЕ два условных ордера на
#   закрытие всей позиции: тейк −XSL_TP (1%) по последней цене и стоп +XSL_SL (5%) по справедливой (mark) цене.
#   Сработал один — второй бот снимает сам. Через XSL_MAX_HOLD_H (24 ч) — закрывает по рынку.
#   Страховка: каждые 10 с бот сам смотрит цену; тейк/стоп на бирже не встал или не сработал — закрывает сам.
#   Ограничения: позиций не больше XSL_MAX_POS (2); за день XSL_DAY_STOPS (2) стопа или убыток XSL_DAY_LOSS $ —
#   пауза до завтра (день — по Иркутску); общий убыток модуля XSL_MAX_LOSS $ — пауза до /shortlive/resume;
#   капитал (xbal) ниже XSL_STOPLINE $ — не заходит. Монеты магазина и аллокатора и монеты, где на Gate уже есть
#   позиция, не трогает. Цена ушла от сигнала больше XSL_MAX_SLIP (1%) — не заходит.
#   XSL_MODE: off — новых шортов нет, paper — бумага (по умолчанию), live — торгует.
#   Страницы (?t=STATS_TOKEN): /shortlive — всё (JSON), /shortlive/brief — коротко (&tg=1 — в Telegram),
#   /shortlive/pause, /shortlive/resume, /shortlive/close?coin=AXS — закрыть сейчас,
#   /shortlive/test?coin=DOGE — план проверки API Gate; &go=1 — живая проверка на 1 контракте: открыть шорт,
#   поставить далёкие тейк/стоп, найти их на бирже, снять, закрыть шорт.
#   v2.1 (25.10): • ПОСЛЕ ВЫХОДА — бот сутки от входа следит за монетой: насколько ушла вниз и вверх и сработал бы
#   тейк 2/3/5% раньше +5% против (видно, сколько оставляем на столе). • РАСТУЩИЙ РЫНОК — если средняя монета на Gate
#   за сутки выросла на XSL_MKT_UP (1%) и больше, бот берёт только монеты, которые за сутки упали (XSL_MKT_FILTER=0 —
#   выключить). • В пропусках — ✅/❌ по правилу «уже падает» (раньше всегда ✅).
#   v2.2 (26.10): ЛИКВИДАЦИЯ РАНЬШЕ СТОПА (NES: у мелких монет Gate берёт большую поддерживающую маржу, при х10
#   ликвидация была на +1,9%, а стоп на +5% — стоп не мог сработать). • До входа: плечо снижается так, чтобы
#   ликвидация была дальше стопа минимум на XSL_LIQ_GAP (2%); маржа та же, позиция меньше. • После входа: Gate
#   показал ликвидацию ближе чем стоп + XSL_LIQ_MIN (1%) — закрываю сразу. • Страховка: цена прошла XSL_LIQ_GUARD
#   (85%) пути до ликвидации — закрываю сам, не жду биржу.
#   v2.3 (26.10): ОКНА И ТЕЙК ПО ТИПАМ. • Тейк по типу сигнала XSL_TP_BY (аллокатор 2%, кандидат 1%, окно 2%), маржа
#   XSL_MARGIN_BY (окно $10), позиций одного типа XSL_POS_BY (окно ≤2). Стоп у всех +XSL_SL (5%). • Окна берутся
#   только на падающем рынке (XSL_MKT_MAX_BY: средняя монета Gate за сутки ниже 0%). • У окон свои дневные стопы
#   (XSL_OWN_DAY): 2 стопа окон — окна до завтра на паузе, аллокатор и кандидаты работают. • Тип потерял
#   XSL_KIND_LOSS_BY $ (окна $15) — тип выключается до /shortlive/resume?kind=окно. • Пропуски окон — без Telegram
#   (XSL_QUIET), в brief — счёт. • Статистика по типам сигналов в brief и /shortlive. Окна включаются в XSL_KINDS.
#   v2.4 (28.10): MEXC больше не нужен. Сигналы xshortp v2.8 — уже в ценах фьючерсов Gate, поэтому «цена ушла от
#   сигнала» сверяется с Gate, а сверка Gate ↔ MEXC (XSL_MAX_BASIS) убрана.
#   v2.5 (28.10): ВАРИАНТЫ ИЗ БУМАГИ. XSL_VAR_BY=кандидат:правило — тип сигнала берётся, только если событие подходит
#   под вариант бумаги xshortp v2.9 (те же условия, что в таблице /shortp/var; «правило&без_пампа_3дня» — оба
#   сразу). Признака нет — не заходит. По умолчанию пусто — как v2.4. • В сообщении о входе — прогноз: как
#   закончились похожие ситуации раньше (из бумаги). Тип «скан» (падают прямо сейчас) — только если добавить его в
#   XSL_KINDS; сначала смотрим бумагу.
import os, sys, time, json, threading, logging, statistics
from decimal import Decimal
from flask import jsonify, request
import mm_scanner as mm
import xpaper
import xexec_api as api

def env(k, d=""):
    return os.getenv(k, d).strip()

VERSION = "xshortlive v2.5 · 28.10 · Gate"
MODE = env("XSL_MODE", "paper").lower()                # off | paper | live
LIVE = MODE == "live"
MARGIN = float(env("XSL_MARGIN", "10"))                # маржа на сделку, $
LEV = int(float(env("XSL_LEV", "10")))                 # плечо
TP = float(env("XSL_TP", "0.01"))                      # тейк: цена ниже входа на 1%
SL = float(env("XSL_SL", "0.05"))                      # стоп: цена выше входа на 5%
MAX_POS = int(env("XSL_MAX_POS", "2"))                 # открытых позиций одновременно
DAY_STOPS = int(env("XSL_DAY_STOPS", "2"))             # стопов за день → пауза до завтра
DAY_LOSS = float(env("XSL_DAY_LOSS", "10"))            # убыток за день, $ → пауза до завтра
MAX_LOSS = float(env("XSL_MAX_LOSS", "30"))            # общий убыток модуля, $ → пауза до /shortlive/resume
MAX24 = float(env("XSL_MAX24", "0.15"))                # рост за сутки от 15% — памп, не шортим
HOLD_H = float(env("XSL_MAX_HOLD_H", "24"))            # дольше суток не держим
MIN_FUND = float(env("XSL_MIN_FUND", "-0.002"))        # фандинг хуже −0,2% за 8 ч — шорт дорогой
STOPLINE = float(env("XSL_STOPLINE", "604"))           # капитал ниже — новых шортов нет (0 — не проверять)
KINDS = {x.strip() for x in env("XSL_KINDS", "аллокатор,кандидат").split(",") if x.strip()}
RULE = env("XSL_RULE", "full")                         # full — только ✅; pump — всё, кроме пампов
MAX_AGE = float(env("XSL_MAX_AGE_S", "180"))           # сигнал старше 3 мин — не берём
MAX_SLIP = float(env("XSL_MAX_SLIP", "0.01"))          # цена ушла от сигнала больше чем на 1% — не берём
COOL_H = float(env("XSL_COOL_H", "12"))                # одна монета — не чаще раза в 12 ч
SL_TREND = int(env("XSL_SL_TREND", "2"))               # стоп по цене: 1 — последней, 2 — справедливой (mark)
FEE = float(env("XSL_FEE", "0.001"))                   # комиссия Gate туда-обратно от объёма (оценка для бумаги)
MKT_FILTER = env("XSL_MKT_FILTER", "1") == "1"         # растущий рынок → только монеты, упавшие за сутки
MKT_UP = float(env("XSL_MKT_UP", "0.01"))              # «растущий» — средняя монета Gate за сутки +1% и больше
LIQ_GAP = float(env("XSL_LIQ_GAP", "0.02"))            # до входа: ликвидация дальше стопа минимум на 2%
LIQ_MIN = float(env("XSL_LIQ_MIN", "0.01"))            # после входа: ближе чем стоп + 1% — закрыть сразу
LIQ_GUARD = float(env("XSL_LIQ_GUARD", "0.85"))        # цена прошла 85% пути до ликвидации — закрыть самому
MIN_LEV = int(env("XSL_MIN_LEV", "2"))                 # безопасное плечо меньше — монету не берём

def kv(s, cast=float):
    """«окно:0.02,кандидат:0.01» → {"окно": 0.02, "кандидат": 0.01}."""
    out = {}
    for part in s.split(","):
        if ":" in part:
            k, v = part.split(":", 1)
            try:
                out[k.strip()] = cast(v.strip())
            except ValueError:
                pass
    return out

def words(s):
    return {x.strip() for x in s.split(",") if x.strip()}

TP_BY = kv(env("XSL_TP_BY", "аллокатор:0.02,кандидат:0.01,окно:0.02"))   # тейк по типу сигнала (нет в списке — XSL_TP)
MARGIN_BY = kv(env("XSL_MARGIN_BY", "окно:10"))        # маржа по типу: окна по $10, пока не наберут 15–20 сделок
POS_BY = kv(env("XSL_POS_BY", "окно:2"), int)          # позиций одного типа одновременно
MKT_MAX_BY = kv(env("XSL_MKT_MAX_BY", "окно:0"))       # тип берём, только если рынок за сутки ниже (окна — падает)
OWN_DAY = words(env("XSL_OWN_DAY", "окно"))            # у этих типов свои дневные стопы — не тормозят остальных
KIND_LOSS = kv(env("XSL_KIND_LOSS_BY", "окно:15"))     # тип потерял столько $ — выключить тип до resume?kind=
QUIET = words(env("XSL_QUIET", "окно"))                # пропуски этих типов — без Telegram (их много)
VAR_BY = kv(env("XSL_VAR_BY", ""), str)                # (v2.5) тип: вариант бумаги xshortp («кандидат:правило»)
MAIN = "основные"                                      # группа дневных стопов для аллокатора и кандидатов

def tp_of(kind):
    return TP_BY.get(kind, TP)

def margin_of(kind):
    return MARGIN_BY.get(kind, MARGIN)

def grp(kind):
    """Группа дневных стопов: окна — своя, остальные — общая."""
    return kind if kind in OWN_DAY else MAIN
DN, UP = [0.01, 0.02, 0.03, 0.05], [0.03, 0.05]        # после входа: первое касание падения и роста (минуты)
EVERY = 10
GTF = "https://api.gateio.ws/api/v4/futures/usdt"     # публичные данные фьючерсов Gate
TXT = "t-xsl"                                          # метка наших ордеров на Gate
log = logging.getLogger("xshortlive")
L = {}
LOCK = threading.RLock()
CACHE = {}

class Skip(Exception):
    pass

def lt(t=None, f="%H:%M"):
    """Время по Иркутску."""
    return time.strftime(f, time.gmtime((time.time() if t is None else t) + 8 * 3600))

def today():
    return lt(None, "%Y-%m-%d")

def say(text, live=None):
    live = LIVE if live is None else live
    mm.tg(("🤖 Автошорт Gate: " if live else "🧪🤖 Автошорт [бумага]: ") + text)

# ---------- состояние ----------
def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'shortlive_v2'", fetch=True)
    L.update(json.loads(r[0][0]) if r else {})
    if "dg" not in L and L.get("day") == today() and (L.get("dstat") or {}).get(MODE):
        s = L["dstat"][MODE]                           # до v2.3 стопы дня были общие — переносим в «основные»
        L["dg"] = {MAIN: {"n": s.get("n", 0), "sl": s.get("sl", 0), "pnl": s.get("pnl", 0.0)}}
    for k, v in (("pos", {}), ("done", []), ("seen", {}), ("skips", []), ("cool", {}), ("warned", {}),
                 ("tot", {}), ("dstat", {}), ("day", today()), ("paused", ""), ("fails", 0), ("trk", {}),
                 ("dg", {}), ("kon", {}), ("koff", {})):
        L.setdefault(k, v)
    for k in KINDS:                                    # с какого момента считать убыток типа
        L["kon"].setdefault(k, time.time())
    L["loaded"] = 1

def save():
    if not L.get("loaded"):
        return
    try:
        xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('shortlive_v2', %s)
                    ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(L),))
    except Exception:
        log.exception("xshortlive save")

def dst(mode):
    return L.setdefault("dstat", {}).setdefault(mode, {"n": 0, "tp": 0, "sl": 0, "pnl": 0.0})

def tot(mode):
    return L.setdefault("tot", {}).setdefault(mode, {"n": 0, "tp": 0, "sl": 0, "time": 0, "other": 0, "pnl": 0.0})

def day_roll():
    d = today()
    if L.get("day") == d:
        return
    for m, s in (L.get("dstat") or {}).items():
        if s.get("n"):
            say(f"итог дня {L.get('day')}: сделок {s['n']}, тейков {s.get('tp', 0)}, стопов {s.get('sl', 0)}, "
                f"{s.get('pnl', 0):+.2f}$", live=m == "live")
    L["day"], L["dstat"], L["dg"] = d, {}, {}
    L["seen"] = {k: v for k, v in L["seen"].items() if time.time() - v < 2 * 86400}

def dgs(g):
    """Счётчик дня по группе: «основные» (аллокатор, кандидаты) или свой у окон."""
    return L.setdefault("dg", {}).setdefault(g, {"n": 0, "sl": 0, "pnl": 0.0})

def pause_reason(kind=None):
    """Почему не заходим. kind — тип сигнала (у окон свои дневные стопы и своё выключение)."""
    if L.get("paused"):
        return f"пауза: {L['paused']}"
    if kind and kind in (L.get("koff") or {}):
        return f"«{kind}» выключены: {L['koff'][kind]}"
    g = grp(kind) if kind else MAIN                    # дневные лимиты — по группе: окна не тормозят остальных
    d, who = dgs(g), "" if g == MAIN else f" для «{g}»"
    if d["sl"] >= DAY_STOPS:
        return f"пауза до завтра{who}: за день уже {d['sl']} стопа"
    if d["pnl"] <= -DAY_LOSS:
        return f"пауза до завтра{who}: убыток дня {d['pnl']:+.2f}$"
    return None
# ---------- цены и контракты (публичные) ----------
def tickers():
    """Фьючерсы Gate: {МОНЕТА: {last, fair (mark), fund}} — один запрос на все контракты."""
    out = {}
    for x in api.S.get(f"{GTF}/tickers", timeout=20).json() or []:
        c = x.get("contract", "")
        if c.endswith("_USDT") and float(x.get("last") or 0) > 0:
            last = float(x["last"])
            try:
                chg = float(x.get("change_percentage")) / 100
            except (TypeError, ValueError):
                chg = None
            out[c[:-5]] = {"last": last, "fair": float(x.get("mark_price") or 0) or last,
                           "fund": float(x.get("funding_rate") or 0), "chg": chg}
    return out

def market(tk):
    """Рынок за сутки: медиана изменения цены всех фьючерсов Gate (−0.02 = средняя монета −2%)."""
    ch = [q["chg"] for q in tk.values() if q.get("chg") is not None and abs(q["chg"]) < 5]
    return statistics.median(ch) if len(ch) >= 20 else None

def detail(coin):
    """Контракт Gate (монет в контракте, шаг цены, плечо, минимум) — кэш 6 ч."""
    c = CACHE.get("d" + coin)
    if c and time.time() - c[0] < 6 * 3600:
        return c[1]
    r = api.S.get(f"{GTF}/contracts/{coin}_USDT", timeout=20)
    d = r.json() if r.status_code == 200 else None
    if not isinstance(d, dict) or not d.get("quanto_multiplier"):
        raise Skip("на Gate нет такого фьючерса")
    CACHE["d" + coin] = (time.time(), d)
    return d

def rnd(x, tick):
    """Цена с шагом контракта, без хвостов вида 0.30000000000000004."""
    try:
        t = Decimal(str(tick))
    except Exception:
        t = Decimal(0)
    if t <= 0:
        return float(f"{x:.8g}")
    return float((Decimal(str(x)) / t).quantize(Decimal(1)) * t)

def ps(x):
    """Цена строкой для Gate: 0.0773, а не 7.73e-02."""
    return format(Decimal(str(x)).normalize(), "f")

def mmr_of(d):
    """Поддерживающая маржа контракта Gate (доля): у мелких монет бывает 5–10% вместо обычных 0,5%."""
    try:
        return max(0.0, float(d.get("maintenance_rate") or 0.005))
    except (TypeError, ValueError):
        return 0.005

def safe_lev(d, px, fair=None):
    """Плечо, при котором ликвидация дальше стопа на LIQ_GAP: 1/плечо ≥ стоп + запас + поддерж. маржа + комиссия
    + насколько справедливая цена уже выше последней (ликвидация считается по справедливой)."""
    gap = max(0.0, fair / px - 1) if fair else 0.0
    return int(1 / (SL + LIQ_GAP + mmr_of(d) + 0.001 + gap))

def liq_est(px, lev, mmr):
    """Оценка цены ликвидации шорта: вход × (1 + 1/плечо − поддерж. маржа)."""
    return px * (1 + 1 / lev - mmr)

def size(coin, px, fair=None, margin=None):
    """(контрактов, монет в контракте, плечо, шаг цены, поддерж. маржа): маржа × плечо / цена контракта.
    Плечо — не больше безопасного: ликвидация должна быть дальше стопа."""
    mg = MARGIN if margin is None else margin
    d = detail(coin)
    if d.get("in_delisting"):
        raise Skip("фьючерс Gate снимается с торгов")
    cs, mmr = float(d["quanto_multiplier"]), mmr_of(d)
    lev = max(1, min(LEV, int(float(d.get("leverage_max") or LEV)), safe_lev(d, px, fair)))
    if lev < MIN_LEV:
        raise Skip(f"у монеты на Gate поддерживающая маржа {mmr * 100:.1f}% — ликвидация была бы раньше стопа "
                   f"+{SL * 100:g}% даже при плече х{MIN_LEV}")
    minv = max(1, int(float(d.get("order_size_min") or 1)))
    n = int(mg * lev / (px * cs))
    if n < minv:
        raise Skip(f"1 контракт ≈${px * cs * minv:.0f} — больше позиции ${mg * lev:.0f}")
    if d.get("order_size_max"):
        n = min(n, int(float(d["order_size_max"])))
    return n, cs, lev, d.get("order_price_round"), mmr

# ---------- биржа Gate (по ключу) ----------
def gt_mode_ok():
    """Одиночный режим позиций — иначе наши ордера на закрытие не подходят. Кэш 1 ч."""
    c = CACHE.get("mode")
    if c and time.time() - c[0] < 3600:
        return c[1]
    a = api.gt("GET", "/futures/usdt/accounts") or {}
    ok_ = not a.get("in_dual_mode") and a.get("position_mode", "single") == "single"
    CACHE["mode"] = (time.time(), ok_)
    return ok_

def gt_free():
    try:
        return float((api.gt("GET", "/futures/usdt/accounts") or {}).get("available"))
    except Exception:
        return None

def gt_shorts():
    """Открытые позиции Gate: {МОНЕТА: позиция} (шорт — размер меньше нуля). Ошибка — исключение."""
    out = {}
    for x in api.gt("GET", "/futures/usdt/positions") or []:
        if float(x.get("size") or 0) != 0:
            out[x.get("contract", "")[:-5]] = x
    return out

def gt_open(coin, n, lev):
    """Плечо (изолированная маржа) и шорт по рынку на n контрактов."""
    api.gt("POST", f"/futures/usdt/positions/{coin}_USDT/leverage", {"leverage": str(lev)})
    return api.gt("POST", "/futures/usdt/orders", body={"contract": f"{coin}_USDT", "size": -int(n), "price": "0",
                                                         "tif": "ioc", "text": TXT})

def gt_close(coin):
    """Закрыть всю позицию по рынку (одиночный режим: size 0 + close)."""
    return api.gt("POST", "/futures/usdt/orders", body={"contract": f"{coin}_USDT", "size": 0, "price": "0",
                                                         "close": True, "tif": "ioc", "text": TXT})

def tpsl_find(coin):
    """Наши действующие тейк/стоп на бирже: {"tp": id, "sl": id} (что нашлось)."""
    out = {}
    for x in api.gt("GET", "/futures/usdt/price_orders", {"status": "open", "contract": f"{coin}_USDT"}) or []:
        tr = x.get("trigger") or {}
        out["tp" if int(tr.get("rule") or 0) == 2 else "sl"] = x.get("id")
    return out

def tpsl_cancel(coin):
    """Снять ВСЕ условные ордера по этому контракту (только он — у позиции своя монета)."""
    if coin:
        api.gt("DELETE", "/futures/usdt/price_orders", {"contract": f"{coin}_USDT"})

def tpsl_place(coin, tp, sl, tick):
    """Тейк (цена ≤ tp, по последней) и стоп (цена ≥ sl, по справедливой) — закрыть всю позицию по рынку.
    Вернёт найденные на бирже {"tp": id, "sl": id} (если чего-то нет — значит не встало)."""
    try:
        tpsl_cancel(coin)                              # старые условные ордера монеты (от прошлой сделки) — снять
    except Exception:
        pass
    for rule, px, ptype in ((2, tp, 0), (1, sl, 1 if SL_TREND == 2 else 0)):
        api.gt("POST", "/futures/usdt/price_orders", body={
            "initial": {"contract": f"{coin}_USDT", "size": 0, "price": "0", "close": True, "tif": "ioc",
                        "reduce_only": True, "text": TXT},
            "trigger": {"strategy_type": 0, "price_type": ptype, "price": ps(rnd(px, tick)), "rule": rule,
                        "expiration": 2 * 86400},
            "order_type": "close-short-position"})
    time.sleep(1)
    return tpsl_find(coin)

def closed_info(coin, t0):
    """Итог закрытой позиции из истории Gate: (цена закрытия, прибыль $, пометка биржи) или None."""
    for x in api.gt("GET", "/futures/usdt/position_close", {"contract": f"{coin}_USDT", "limit": 10}) or []:
        if x.get("side") == "short" and float(x.get("time") or 0) >= t0 - 5:
            return (float(x.get("long_price") or 0) or None,
                    float(x["pnl"]) if x.get("pnl") is not None else None, str(x.get("text") or ""))
    return None

def capital():
    """(капитал $, каких бирж не видно) по xbal — кэш 15 мин."""
    c = CACHE.get("cap")
    if c and time.time() - c[0] < 900:
        return c[1]
    XB = sys.modules.get("xbal")
    if not XB:
        return None
    s = XB.snapshot()
    v = (s["total"], ", ".join(s.get("errors") or {}))
    CACHE["cap"] = (time.time(), v)
    return v

def busy_coins():
    """Монеты магазина и аллокатора — там свой хедж-шорт, их не трогаем."""
    out = set()
    SH, AL = sys.modules.get("xshop"), sys.modules.get("xalloc")
    if SH:
        out |= set(getattr(SH, "COINS", []) or [])
    if AL and getattr(AL, "A", None):
        out |= set((AL.A.get("coins") or {}).keys())
    return out

def hedge_quiet(coin, on):
    """Автохедж не пишет «добавь маржу» про шорты автошорта: при х10 ликвидация всегда близко, у нас свой стоп."""
    HA = sys.modules.get("xahedge")
    if HA and hasattr(HA, "A"):
        w = HA.A.setdefault("warned", {})
        if on:
            w["liq" + coin] = time.time() + 10 ** 9
        else:
            w.pop("liq" + coin, None)

# ---------- вход ----------
def scan(now, SP):
    """Новые кандидаты бумаги xshortp, по которым ещё не решали."""
    out = []
    for e in list(SP.P.get("open") or []):
        if e.get("kind") not in KINDS or not e.get("coin"):
            continue
        key = f"{e['coin']}:{e['t']}"
        if key in L["seen"]:
            continue
        if now - e["t"] > MAX_AGE:                     # старое (например, после перезапуска) — молча
            L["seen"][key] = now
            continue
        if "pre" not in e:                             # признаки «что было до сигнала» ещё считаются
            continue
        L["seen"][key] = now
        out.append(e)
    return out
def check(e, now, SP, tk):
    """None — заходим; иначе (причина, сообщать ли в Telegram)."""
    pr, coin = e.get("pre") or {}, e["coin"]
    ch24 = pr.get("ch24")
    if ch24 is None:
        return "нет свечей до сигнала — правило не проверить", False
    if ch24 >= MAX24:
        return f"памп: за сутки {ch24 * 100:+.1f}%", False
    rule = getattr(SP, "rule_ok", None)
    if rule is None:
        return "xshortp старой версии — нет правила «уже падает»", False
    if RULE != "pump" and rule(e) is not True:
        return "❌ не по правилу «уже падает» (цена у максимума часа)", False
    code = VAR_BY.get(e["kind"])                       # (v2.5) вариант из бумаги для этого типа
    if code:
        vf = getattr(SP, "var_ok", None)
        ok_v = vf(code, e) if vf else None
        if ok_v is not True:
            return (f"не подходит под вариант «{code}»" if ok_v is False else
                    f"вариант «{code}» не проверить (нет признаков или xshortp старше v2.9)"), False
    kind = e["kind"]
    r = pause_reason(kind)
    if r:
        return r, True
    if coin in L["pos"]:
        return "по монете уже открыт автошорт", False
    if now - L["cool"].get(coin, 0) < COOL_H * 3600:
        return f"по монете был шорт меньше {COOL_H:.0f} ч назад", True
    if len(L["pos"]) >= MAX_POS:
        return f"уже {len(L['pos'])} позиции (лимит {MAX_POS})", True
    cap = POS_BY.get(kind)
    if cap is not None and sum(1 for p in L["pos"].values() if p.get("kind") == kind) >= cap:
        return f"«{kind}» уже {cap} поз. (лимит для типа {cap})", True
    if coin not in tk:
        return "на Gate нет фьючерса этой монеты", True
    if tk[coin]["fund"] < MIN_FUND:
        return f"фандинг {tk[coin]['fund'] * 100:.2f}% за 8 ч — шорт платит много", True
    if coin in busy_coins():
        return "монета магазина/аллокатора — там свой хедж-шорт", True
    mk = CACHE.get("mkt")
    if MKT_FILTER and mk is not None and mk >= MKT_UP and ch24 >= 0:
        return (f"рынок растёт (средняя монета {mk * 100:+.1f}% за сутки) — беру только упавшие за сутки, "
                f"а эта {ch24 * 100:+.1f}%"), True
    mmax = MKT_MAX_BY.get(kind)
    if mmax is not None and (mk is None or mk >= mmax):
        now_s = "не посчитан" if mk is None else f"{mk * 100:+.1f}% за сутки"
        need = "падающем рынке" if mmax == 0 else f"рынке ниже {mmax * 100:+g}% за сутки"
        return f"«{kind}» беру только на {need}, а рынок Gate {now_s}", True
    mv = tk[coin]["last"] / e["px"] - 1                # v2.4: сигнал в ценах Gate — сверяю с Gate, MEXC не нужен
    if abs(mv) > MAX_SLIP:
        return f"цена уже ушла на {mv * 100:+.1f}% от сигнала", True
    return None

def skip(e, why, loud, SP=None):
    L["skips"] = (L["skips"] + [[int(time.time()), e["coin"], e["kind"], why]])[-150:]
    if loud and e["kind"] not in QUIET:
        r = getattr(SP, "rule_ok", lambda x: None)(e) if SP else None
        say(f"пропустил {e['coin']}{' ✅' if r is True else ' ❌' if r is False else ''} — {why}")

def open_text(p):
    pr = p.get("pre") or {}
    nt = p["n"] * p["cs"] * p["px"]
    if p["mode"] != "live":
        on = "бумага — на бирже ничего не открыто"
    elif p.get("tpsl"):
        on = "тейк и стоп стоят на Gate ✅"
    else:
        on = f"⚠️ тейк/стоп на Gate НЕ встали ({p.get('tpsl_err', '?')}) — слежу сам и закрою по рынку"
    lq, tpk = p.get("liq"), p.get("tp_pct", TP)
    lv = ""
    if p["lev"] < LEV:
        lv = (f" — плечо снижено с х{LEV}, чтобы ликвидация была дальше стопа (поддерживающая маржа "
              f"{(p.get('mmr') or 0) * 100:.1f}%)")
    return (f"шорт {p['coin']} [{p['kind']}] по {p['px']:.6g} — {p['n']} контр. ≈ ${nt:.0f} (маржа ≈ ${nt / p['lev']:.2f}, "
            f"х{p['lev']}){lv}\n"
            f"Тейк {p['tp']:.6g} (−{tpk * 100:g}%) ≈ +${nt * tpk:.2f} · стоп {p['sl']:.6g} (+{SL * 100:g}%) ≈ −${nt * SL:.2f}"
            + (f" · ликвидация {lq:.6g} (+{(lq / p['px'] - 1) * 100:.1f}%{'' if p['mode'] == 'live' else ', оценка'})"
               if lq else "")
            + f" · не дольше {HOLD_H:g} ч\n"
            f"Сигнал: {p['kind']}, за сутки {(pr.get('ch24') or 0) * 100:+.1f}%, ниже максимума часа на "
            f"{abs(pr.get('dh1') or 0) * 100:.1f}% · {on}"
            + (f"\nПрогноз (бумага): {p['fc']}" if p.get("fc") else ""))

def set_tpsl(p):
    try:
        f = tpsl_place(p["coin"], p["tp"], p["sl"], p["tick"])
        p["tpsl"] = f if ("tp" in f and "sl" in f) else None
        if not p["tpsl"]:
            p["tpsl_err"] = "биржа приняла, но в списке условных ордеров " + ("нет стопа" if "tp" in f else
                                                                                "нет тейка" if "sl" in f else "их нет")
    except Exception as x:
        p["tpsl"], p["tpsl_err"] = None, str(x)[:150]

def adopt(p, x):
    """Позиция появилась на бирже: точная цена входа, тейк/стоп от неё — и сразу на биржу."""
    p["seen"] = 1
    p["px"] = float(x.get("entry_price") or p["px"])
    p["n"] = abs(int(float(x.get("size") or -p["n"])))
    p["liq"] = float(x.get("liq_price") or 0) or p.get("liq")
    p["tp"], p["sl"] = rnd(p["px"] * (1 - p.get("tp_pct", TP)), p["tick"]), rnd(p["px"] * (1 + SL), p["tick"])
    p["tpsl_try"], p["tpsl_t"] = 1, time.time()
    L["fails"] = 0
    if p.get("liq") and p["liq"] < p["px"] * (1 + SL + LIQ_MIN):   # стоп не успеет — ликвидация раньше
        p["liq_bad"] = 1
        say(f"⚠️ {p['coin']}: Gate поставил ликвидацию {p['liq']:.6g} (+{(p['liq'] / p['px'] - 1) * 100:.1f}%) — "
            f"раньше стопа {p['sl']:.6g} (+{SL * 100:g}%) или вплотную к нему. Закрываю сразу, потеря — только "
            f"комиссия", live=True)
        L["cool"][p["coin"]] = time.time() + 7 * 86400                # монету неделю не трогаем
        close_now(p, "закрыта сразу: ликвидация раньше стопа", time.time())
        save()
        return
    set_tpsl(p)
    trk_start(p)
    hedge_quiet(p["coin"], True)
    say(open_text(p), live=True)
    save()

def open_pos(e, now, tk):
    coin, px = e["coin"], tk[e["coin"]]["last"]
    tpk, mg = tp_of(e["kind"]), margin_of(e["kind"])
    n, cs, lev, tick, mmr = size(coin, px, tk[coin].get("fair"), mg)
    p = {"coin": coin, "kind": e["kind"], "sig_t": e["t"], "sig_px": e["px"], "pre": e.get("pre"), "t": int(now),
         "n": n, "cs": cs, "lev": lev, "tick": tick, "mode": "live" if LIVE else "paper", "px": px,
         "tp": rnd(px * (1 - tpk), tick), "sl": rnd(px * (1 + SL), tick), "hi": px, "lo": px, "bk": 0,
         "mmr": mmr, "liq": liq_est(px, lev, mmr), "tp_pct": tpk, "margin": mg}
    try:                                               # (v2.5) прогноз по похожим ситуациям из бумаги
        fc = getattr(sys.modules.get("xshortp"), "fc_text", None)
        p["fc"] = fc(e) if fc else ""
    except Exception:
        p["fc"] = ""
    if p["tp"] > px * (1 - tpk / 2) or not px * (1 + SL / 2) < p["sl"] < px * (1 + SL * 1.5):
        raise Skip(f"шаг цены контракта {tick} слишком крупный для тейка {tpk * 100:g}% и стопа {SL * 100:g}%")
    if not LIVE:
        L["pos"][coin], L["cool"][coin] = p, now
        trk_start(p)
        say(open_text(p), live=False)
        save()
        return
    if not gt_mode_ok():
        raise Skip("на Gate включён двусторонний режим позиций — переключи фьючерсы на одиночный (One-way)")
    if coin in gt_shorts():
        raise Skip("на Gate по монете уже есть позиция — не смешиваю")
    try:
        cap = capital()
    except Exception:                                  # капитал не посчитать — не мешает; общий лимит убытка свой
        log.exception("xshortlive capital")
        cap = None
    if cap and STOPLINE > 0 and cap[0] < STOPLINE:
        raise Skip(f"капитал ${cap[0]:.0f} ниже стоп-линии ${STOPLINE:.0f}" + (f" (не видно: {cap[1]})" if cap[1] else ""))
    need, avail = n * cs * px / lev * 1.05, gt_free()
    if avail is not None and avail < need:
        raise Skip(f"на фьючерсах Gate свободно ${avail:.2f}, нужно ~${need:.2f}")
    p["seen"] = 0                                      # запись до ордера: при сбое связи бот сам найдёт позицию
    L["pos"][coin], L["cool"][coin] = p, now
    save()
    try:
        gt_open(coin, n, lev)
        L["fails"] = 0
    except Exception as x:
        p["open_err"] = f"ошибка Gate: {str(x)[:150]}"
        L["fails"] = L.get("fails", 0) + 1
        if L["fails"] >= 3 and not L.get("paused"):
            L["paused"] = "Gate 3 раза подряд не открыл шорт"
            say(f"⛔ {L['paused']} — новые шорты остановлены. Проверь /shortlive/test, потом /shortlive/resume")
    for _ in range(5):
        time.sleep(1.5)
        try:
            sh = gt_shorts()
        except Exception:
            continue
        if coin in sh and float(sh[coin].get("size") or 0) < 0:
            adopt(p, sh[coin])
            return
    say(f"{coin}: {p.get('open_err') or 'ордер отправлен'} — позиции пока не вижу, проверяю ещё 2 мин", live=True)
# ---------- ведение позиций ----------
def finish(p, outcome, ex, pnl, now, note=""):
    coin = p["coin"]
    L["pos"].pop(coin, None)
    hedge_quiet(coin, False)
    nt = p["n"] * p["cs"] * p["px"]
    if pnl is None:
        pnl = nt * (p["px"] - ex) / p["px"] - nt * FEE
    rec = {"coin": coin, "kind": p["kind"], "mode": p["mode"], "t": p["t"], "t_close": int(now), "px": p["px"],
           "exit": ex, "pnl": round(pnl, 2), "outcome": outcome, "worst_pct": round((p["hi"] / p["px"] - 1) * 100, 2),
           "pre": p.get("pre"), "note": note, "key": p.get("key")}
    L["done"] = (L["done"] + [rec])[-300:]
    k = ("tp" if outcome.startswith("тейк") else "sl" if outcome.startswith("стоп") or outcome == "ликвидация"
         else "time" if outcome == "время" else "other")
    for s in (dst(p["mode"]), tot(p["mode"])):
        s["n"] += 1
        s[k] = s.get(k, 0) + 1
        s["pnl"] = round(s["pnl"] + pnl, 2)
    cur = p["mode"] == ("live" if LIVE else "paper")
    if cur:                                            # счётчик дня своей группы (у окон — свой)
        g = dgs(grp(p["kind"]))
        g["n"] += 1
        g["sl"] += 1 if k == "sl" else 0
        g["pnl"] = round(g["pnl"] + pnl, 2)
    d = dst(p["mode"])
    say(f"{'✅' if pnl > 0 else '❌'} {coin} [{p['kind']}]: {outcome} через {round((now - p['t']) / 60)} мин, "
        f"выход {ex:.6g}, итог {pnl:+.2f}$ (хуже всего по пути +{rec['worst_pct']:.1f}%){note}\n"
        f"Сегодня: сделок {d['n']}, тейков {d['tp']}, стопов {d['sl']}, {d['pnl']:+.2f}$", live=p["mode"] == "live")
    if cur:
        t = tot(p["mode"])
        if t["pnl"] <= -MAX_LOSS and not L.get("paused"):
            L["paused"] = f"общий убыток {t['pnl']:+.2f}$ — больше лимита ${MAX_LOSS:.0f}"
            say(f"⛔ {L['paused']}. Новые шорты остановлены. Включить снова: /shortlive/resume")
        r, wk = pause_reason(p["kind"]), "dpause" + grp(p["kind"])
        if r and r.startswith("пауза до завтра") and L["warned"].get(wk) != today():
            L["warned"][wk] = today()
            say(f"⏸ {r}" + ("" if grp(p["kind"]) == MAIN or "для «" not in r else
                            " — аллокатор и кандидаты работают дальше"))
        lim = KIND_LOSS.get(p["kind"])
        if lim and p["kind"] not in L.setdefault("koff", {}):
            t0 = L.setdefault("kon", {}).setdefault(p["kind"], p["t"])
            kp = round(sum(x["pnl"] for x in L["done"] if x.get("kind") == p["kind"] and x.get("mode") == p["mode"]
                           and x.get("t_close", 0) >= t0), 2)
            if kp <= -lim:
                L["koff"][p["kind"]] = f"потеряли {kp:+.2f}$ с {lt(t0, '%d.%m %H:%M')} (лимит ${lim:g})"
                say(f"⛔ «{p['kind']}» выключены: {L['koff'][p['kind']]}. Остальные сигналы работают. "
                    f"Включить снова: /shortlive/resume?kind={p['kind']}")
    save()

def classify(p, ex, pnl, text=""):
    nt = p["n"] * p["cs"] * p["px"]
    if "liq" in text.lower() or (pnl is not None and pnl <= -0.8 * nt / p["lev"]):
        return "ликвидация"
    if ex <= p["tp"] * 1.003:
        return "тейк"
    if ex >= p["sl"] * 0.997:
        return "стоп"
    return "закрыта на бирже (вручную?)"

def close_now(p, outcome, now, note=""):
    """Закрыть по рынку (живую — на Gate, бумажную — по последней цене)."""
    coin, q = p["coin"], (CACHE.get("tk") or {}).get(p["coin"])
    if p["mode"] != "live":
        finish(p, outcome, q["last"] if q else p["px"], None, now, note)
        return True
    try:
        gt_close(coin)
    except Exception as x:
        if now - L["warned"].get("cl" + coin, 0) > 600:
            L["warned"]["cl" + coin] = now
            say(f"❗ {coin}: не смог закрыть шорт ({str(x)[:120]}) — пробую снова каждые 10 с. Не выйдет — закрой "
                f"вручную на Gate", live=True)
        return False
    time.sleep(2)
    try:
        tpsl_cancel(coin)
    except Exception:
        pass
    info = None
    for _ in range(3):
        try:
            info = closed_info(coin, p["t"])
        except Exception:
            info = None
        if info:
            break
        time.sleep(2)
    ex = (info[0] if info else None) or (q["last"] if q else p["px"])
    finish(p, outcome, ex, info[1] if info else None, now, note)
    return True

def paper_step(p, q, now):
    if not q:
        return
    slpx = q["fair"] if SL_TREND == 2 else q["last"]
    if q["last"] <= p["tp"]:
        finish(p, "тейк", p["tp"], None, now)
    elif slpx >= p["sl"]:
        finish(p, "стоп", max(p["sl"], q["last"]), None, now)
    elif now - p["t"] >= HOLD_H * 3600:
        finish(p, "время", q["last"], None, now)

def live_step(p, q, sh, now):
    coin = p["coin"]
    x = sh.get(coin)
    short = x is not None and float(x.get("size") or 0) < 0
    if not p.get("seen"):                              # ордер ушёл, позиции ещё не видели
        if short:
            adopt(p, x)
        elif now - p["t"] > 120:
            L["pos"].pop(coin, None)
            say(f"{coin}: шорт так и не появился на Gate — запись снята. {p.get('open_err', '')}", live=True)
            save()
        return
    if not short:                                      # закрыта на бирже: тейк, стоп, вручную или ликвидация
        p.setdefault("gone_t", now)
        try:
            info = closed_info(coin, p["t"])
        except Exception:
            info = None
        if not info and now - p["gone_t"] < 60:        # история Gate может появиться с задержкой
            return
        ex = (info[0] if info else None) or (q["last"] if q else p["px"])
        pnl = info[1] if info else None
        try:
            tpsl_cancel(coin)                          # второй условный ордер (тейк или стоп) больше не нужен
        except Exception:
            pass
        finish(p, classify(p, ex, pnl, info[2] if info else ""), ex, pnl, now)
        return
    p.pop("gone_t", None)
    if float(x.get("liq_price") or 0) > 0:             # цена ликвидации на бирже может меняться — держим свежую
        p["liq"] = float(x["liq_price"])
    if p.get("liq_bad"):                               # не закрылась сразу после входа — пробую снова
        close_now(p, "закрыта сразу: ликвидация раньше стопа", now)
        return
    if not q:
        return
    if now - p["t"] >= HOLD_H * 3600:
        close_now(p, "время", now)
        return
    if not p.get("tpsl") and p.get("tpsl_try", 0) < 3 and now - p.get("tpsl_t", 0) > 60:
        p["tpsl_try"], p["tpsl_t"] = p.get("tpsl_try", 0) + 1, now   # не встал — ещё раз в минуту, до 3 раз
        set_tpsl(p)
        if p.get("tpsl"):
            say(f"{coin}: тейк и стоп на Gate встали с {p['tpsl_try']}-й попытки ✅", live=True)
    slpx = q["fair"] if SL_TREND == 2 else q["last"]
    lq = p.get("liq")
    if lq and lq > p["px"] and max(q["fair"], q["last"]) >= p["px"] + (lq - p["px"]) * LIQ_GUARD:
        close_now(p, "стоп (до ликвидации)", now,             # стоп не сработал, а ликвидация уже близко
                  note=f" — цена прошла {LIQ_GUARD * 100:.0f}% пути до ликвидации {lq:.6g}")
        return
    if p.get("tpsl"):                                  # страховка: биржа должна была сработать, но позиция жива
        hit = ("тейк (закрыл сам)" if q["last"] <= p["tp"] * 0.997 else
               "стоп (закрыл сам)" if slpx >= p["sl"] * 1.005 else None)
        need = 2
    else:                                              # тейка/стопа на бирже нет — веду сам
        hit = "тейк (закрыл сам)" if q["last"] <= p["tp"] else "стоп (закрыл сам)" if slpx >= p["sl"] else None
        need = 1
    p["bk"] = p.get("bk", 0) + 1 if hit else 0
    if hit and p["bk"] >= need:
        close_now(p, hit, now, note=" — биржевой тейк/стоп не сработал" if p.get("tpsl") else "")

# ---------- после выхода: сутки от входа ----------
def trk_start(p):
    """Следить за монетой сутки от входа (и после тейка/стопа): как далеко ушла вниз и вверх."""
    key = f"{p['coin']}:{p['t']}"
    p["key"] = key
    L["trk"][key] = {"coin": p["coin"], "t": p["t"], "px": p["px"], "lo": p["px"], "hi": p["px"], "dn": {}, "up": {},
                     "mode": p["mode"]}

def trk_sum(r):
    return {"ниже_входа_макс_%": round((r["lo"] / r["px"] - 1) * 100, 2),
            "выше_входа_макс_%": round((r["hi"] / r["px"] - 1) * 100, 2), "dn": r["dn"], "up": r["up"]}

def trk_step(now, tk):
    for key, r in list(L["trk"].items()):
        q = tk.get(r["coin"])
        if q and now - r["t"] <= 24 * 3600:
            px, mins = q["last"], max(1, round((now - r["t"]) / 60))
            r["lo"], r["hi"] = min(r["lo"], px), max(r["hi"], px)
            for x in DN:
                if px <= r["px"] * (1 - x) and str(x) not in r["dn"]:
                    r["dn"][str(x)] = mins
            for x in UP:
                if px >= r["px"] * (1 + x) and str(x) not in r["up"]:
                    r["up"][str(x)] = mins
        if now - r["t"] > 24 * 3600 and r["coin"] not in L["pos"]:
            for d in reversed(L["done"]):
                if d.get("key") == key:
                    d["after"] = trk_sum(r)
                    break
            L["trk"].pop(key, None)

def after_hit(a, x):
    """Тейк x сработал бы раньше, чем цена выросла на 5% против (сутки от входа)."""
    t, s = (a.get("dn") or {}).get(str(x)), (a.get("up") or {}).get("0.05")
    return t is not None and (s is None or t < s)
def after_lines():
    fin = [d for d in L.get("done", []) if d.get("after")][-30:]
    out = []
    if fin:
        n = len(fin)
        lo = sum(d["after"]["ниже_входа_макс_%"] for d in fin) / n
        out.append(f"После входа за сутки ({n} сд.): в среднем вниз до {lo:+.1f}% · тейк 2% был бы в "
                   f"{sum(after_hit(d['after'], 0.02) for d in fin)}, 3% — в {sum(after_hit(d['after'], 0.03) for d in fin)}, "
                   f"5% — в {sum(after_hit(d['after'], 0.05) for d in fin)} (раньше +5% против)")
        out.append("  " + "; ".join(f"{d['coin']} до {d['after']['ниже_входа_макс_%']:+.1f}% / "
                                    f"вверх {d['after']['выше_входа_макс_%']:+.1f}%" for d in fin[-5:]))
    live = [r for r in L.get("trk", {}).values()]
    if live:
        out.append("Слежу после входа: " + "; ".join(f"{r['coin']} пока до {(r['lo'] / r['px'] - 1) * 100:+.1f}%"
                                                    for r in live[-6:]))
    return out

def monitor(now, tk):
    sh = None
    if any(p["mode"] == "live" for p in L["pos"].values()):
        try:
            sh = gt_shorts()
            L.pop("pos_err_t", None)
        except Exception as x:
            L.setdefault("pos_err_t", now)
            if now - L["pos_err_t"] > 300 and now - L["warned"].get("poserr", 0) > 3600:
                L["warned"]["poserr"] = now
                say(f"⚠️ 5+ мин не вижу позиций Gate ({str(x)[:100]}). Тейк/стоп на бирже стоят, но проверь вручную",
                    live=True)
    for coin, p in list(L["pos"].items()):
        q = tk.get(coin)
        if q:
            p["hi"], p["lo"] = max(p["hi"], q["last"]), min(p["lo"], q["last"])
        if p["mode"] != "live":
            paper_step(p, q, now)
        elif sh is not None:
            live_step(p, q, sh, now)

def step(now):
    day_roll()
    SP = sys.modules.get("xshortp")
    new = scan(now, SP) if SP and MODE in ("paper", "live") else []
    if not new and not L["pos"] and not L["trk"] and now - CACHE.get("mkt_t", 0) < 600:
        return
    tk = tickers()
    CACHE["tk"], CACHE["mkt"], CACHE["mkt_t"] = tk, market(tk), now
    for e in new:
        why = check(e, now, SP, tk)
        if why:
            skip(e, why[0], why[1], SP)
            continue
        try:
            open_pos(e, now, tk)
        except Skip as x:
            skip(e, str(x), True, SP)
        except Exception as x:
            log.exception("xshortlive open %s", e.get("coin"))
            skip(e, f"ошибка: {str(x)[:150]}", True, SP)
    monitor(now, tk)
    trk_step(now, tk)

def loop():
    time.sleep(90)
    while not L.get("loaded"):
        try:
            load()
        except Exception:
            log.exception("xshortlive load")
            time.sleep(60)
    for p in L["pos"].values():
        if p["mode"] == "live":
            hedge_quiet(p["coin"], True)
    if MODE != "off" or L["pos"]:
        say(f"запущен — {'ЖИВОЙ, Gate' if LIVE else 'бумага по ценам Gate' if MODE == 'paper' else 'ВЫКЛ (только веду открытые)'}: "
            f"сигналы {', '.join(sorted(KINDS))} · {setup_line()} (рост за сутки <{MAX24 * 100:g}%)"
            + (f" | открыто: {', '.join(L['pos'])}" if L["pos"] else "")
            + (f" | {pause_reason()}" if pause_reason() else ""))
    n = 0
    while True:
        try:
            with LOCK:
                step(time.time())
        except Exception:
            log.exception("xshortlive")
        n += 1
        if n % 6 == 0:
            with LOCK:
                save()
        time.sleep(EVERY)

# ---------- страницы ----------
def ok():
    return mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN

def pos_view(p):
    q = (CACHE.get("tk") or {}).get(p["coin"])
    nt = p["n"] * p["cs"] * p["px"]
    mv = (p["px"] - q["last"]) / p["px"] if q else None
    return {"монета": p["coin"], "режим": p["mode"], "с": lt(p["t"], "%d.%m %H:%M"), "вход": p["px"],
            "сейчас": q["last"] if q else None, "шорт_%": round(mv * 100, 2) if mv is not None else None,
            "прибыль_$": round(nt * mv, 2) if mv is not None else None, "тейк": p["tp"], "стоп": p["sl"],
            "контрактов": p["n"], "объём_$": round(nt, 2), "плечо": p["lev"], "ликвидация": p.get("liq"),
            "тейк_стоп_на_бирже": p.get("tpsl") if p["mode"] == "live" else "бумага",
            "ошибка_тейк_стоп": p.get("tpsl_err"), "хуже_всего_%": round((p["hi"] / p["px"] - 1) * 100, 2)}

def kind_stats(mode, day=None):
    """Итог по типам сигналов (аллокатор, кандидат, окно): {тип: {n, tp, sl, pnl}}; day — только этот день."""
    out = {}
    for d in L.get("done", []):
        if d.get("mode") != mode or (day and lt(d["t_close"], "%Y-%m-%d") != day):
            continue
        s = out.setdefault(d.get("kind") or "?", {"n": 0, "tp": 0, "sl": 0, "pnl": 0.0})
        o = d.get("outcome", "")
        s["n"] += 1
        s["tp"] += 1 if o.startswith("тейк") else 0
        s["sl"] += 1 if (o.startswith("стоп") or o == "ликвидация") else 0
        s["pnl"] = round(s["pnl"] + d["pnl"], 2)
    return out

def kinds_line(st):
    return "; ".join(f"{k} {s['n']} сд., тейков {s['tp']}, стопов {s['sl']}, {s['pnl']:+.2f}$"
                     for k, s in sorted(st.items()))

def setup_line():
    tps = ", ".join(f"{k} {tp_of(k) * 100:g}%" for k in sorted(KINDS))
    mgs = ", ".join(f"{k} ${margin_of(k):g}" for k in sorted(KINDS) if margin_of(k) != MARGIN)
    vrs = ", ".join(f"{k} — «{v}»" for k, v in sorted(VAR_BY.items()))
    return (f"Маржа ${MARGIN:g}{' (' + mgs + ')' if mgs else ''} × х{LEV} · тейк: {tps} · стоп +{SL * 100:g}% · "
            f"≤{HOLD_H:g} ч · ≤{MAX_POS} поз. · {'только ✅' if RULE != 'pump' else 'всё, кроме пампов'}"
            + (f" · варианты: {vrs}" if vrs else ""))

def brief():
    md = {"live": "ЖИВОЙ (Gate)", "paper": "БУМАГА (цены Gate)", "off": "ВЫКЛ"}.get(MODE, MODE)
    out = [f"🤖 {VERSION} — {md}, {lt(None, '%d.%m %H:%M')}", setup_line()]
    if not L.get("loaded"):
        return "\n".join(out + ["Ещё загружается (первые ~2 мин после запуска)"])
    s = dst("live" if LIVE else "paper")
    out.append(f"Сегодня: сделок {s['n']}, тейков {s['tp']}, стопов {s['sl']}, {s['pnl']:+.2f}$ · "
               f"{pause_reason() or 'работает'}")
    for k in sorted(KINDS & (OWN_DAY | set(L.get("koff") or {}))):   # отдельный статус окон
        r = pause_reason(k)
        mmax, mk = MKT_MAX_BY.get(k), CACHE.get("mkt")
        if not r and mmax is not None and (mk is None or mk >= mmax):
            r = "рынок не падает — сейчас не беру"
        out.append(f"«{k}»: {r or 'работают'}")
    ks = kind_stats("live" if LIVE else "paper", today())
    if ks:
        out.append("Сегодня по типам: " + kinds_line(ks))
    ka = kind_stats("live" if LIVE else "paper")
    if ka:
        out.append("Всего по типам: " + kinds_line(ka))
    for m in ("live", "paper"):
        t = (L.get("tot") or {}).get(m)
        if t and t["n"]:
            out.append(f"Всего ({'живые' if m == 'live' else 'бумага'}): сделок {t['n']}, тейков {t['tp']} "
                       f"({t['tp'] / t['n'] * 100:.0f}%), стопов {t['sl']}, по времени {t.get('time', 0)}, "
                       f"{t['pnl']:+.2f}$")
    for p in L["pos"].values():
        v = pos_view(p)
        out.append(f"Открыт: {p['coin']} с {lt(p['t'])} по {p['px']:.6g}, сейчас "
                   + ("—" if v["шорт_%"] is None else f"{v['шорт_%']:+.2f}% ({v['прибыль_$']:+.2f}$)")
                   + f" · тейк {p['tp']:.6g} / стоп {p['sl']:.6g}"
                   + (f" / ликв. {p['liq']:.6g} (+{(p['liq'] / p['px'] - 1) * 100:.1f}%)" if p.get("liq") else "")
                   + (f" · х{p['lev']}" if p["lev"] != LEV else "")
                   + ("" if p["mode"] != "live" else " · на бирже ✅" if p.get("tpsl") else " · ⚠️ веду сам"))
    last = L["done"][-5:]
    if last:
        out.append("Последние: " + "; ".join(f"{d['coin']} {lt(d['t_close'])} {d['outcome']} {d['pnl']:+.2f}$"
                                             for d in last))
    sk = [x for x in L["skips"] if x[2] not in QUIET][-5:]
    if sk:
        out.append("Пропуски: " + "; ".join(f"{x[1]} {lt(x[0])} — {x[3]}" for x in sk))
    qs = [x for x in L["skips"] if x[2] in QUIET and lt(x[0], "%Y-%m-%d") == today()]
    if qs:
        out.append(f"Пропущено сегодня «{'», «'.join(sorted({x[2] for x in qs}))}»: {len(qs)}, последний — "
                   f"{qs[-1][1]} {lt(qs[-1][0])}: {qs[-1][3]}")
    out += after_lines()
    mk = CACHE.get("mkt")
    if mk is not None:
        out.append(f"Рынок Gate за сутки: средняя монета {mk * 100:+.1f}%"
                   + (" — растёт, беру только упавшие монеты" if MKT_FILTER and mk >= MKT_UP else ""))
    return "\n".join(out)
@mm.app.route("/shortlive")
def shortlive_page():
    if not ok():
        return "forbidden", 403
    with LOCK:
        return jsonify({"версия": VERSION, "режим": MODE, "пауза": pause_reason(),
                        "настройки": {"биржа": "Gate", "маржа_$": MARGIN, "плечо": LEV, "тейк_%": TP * 100,
                                      "стоп_%": SL * 100,
                                      "стоп_по_цене": "справедливой" if SL_TREND == 2 else "последней",
                                      "держим_ч": HOLD_H, "позиций": MAX_POS, "стопов_в_день": DAY_STOPS,
                                      "убыток_дня_$": DAY_LOSS, "общий_убыток_$": MAX_LOSS,
                                      "памп_сутки_%": MAX24 * 100, "стоп_линия_$": STOPLINE,
                                      "сигналы": sorted(KINDS), "правило": RULE, "комиссия_%": FEE * 100,
                                      "фильтр_растущего_рынка": f"от {MKT_UP * 100:g}%" if MKT_FILTER else "выкл",
                                      "фандинг_не_хуже_%": MIN_FUND * 100,
                                      "ликвидация_дальше_стопа_до_входа_%": LIQ_GAP * 100,
                                      "ликвидация_дальше_стопа_после_входа_мин_%": LIQ_MIN * 100,
                                      "страховка_пути_до_ликвидации_%": LIQ_GUARD * 100,
                                      "тейк_по_типам_%": {k: tp_of(k) * 100 for k in sorted(KINDS)},
                                      "маржа_по_типам_$": {k: margin_of(k) for k in sorted(KINDS)},
                                      "позиций_по_типам": POS_BY, "тип_только_при_рынке_ниже_%":
                                          {k: v * 100 for k, v in MKT_MAX_BY.items()},
                                      "свои_дневные_стопы": sorted(OWN_DAY), "убыток_типа_выключить_$": KIND_LOSS,
                                      "вариант_по_типам": VAR_BY},
                        "выключенные_типы": L.get("koff"), "день_по_группам": L.get("dg"),
                        "по_типам": {"сегодня": kind_stats(MODE, today()), "всего": kind_stats(MODE)},
                        "сегодня": L.get("dstat"), "всего": L.get("tot"),
                        "открыто": [pos_view(p) for p in (L.get("pos") or {}).values()],
                        "сделки": (L.get("done") or [])[-20:],
                        "слежу_после_входа": [dict(trk_sum(r), монета=r["coin"], с=lt(r["t"], "%d.%m %H:%M"))
                                              for r in (L.get("trk") or {}).values()],
                        "рынок_за_сутки_%": None if CACHE.get("mkt") is None else round(CACHE["mkt"] * 100, 2),
                        "пропуски": [[lt(x[0], "%d.%m %H:%M")] + x[1:] for x in (L.get("skips") or [])[-60:]]})

@mm.app.route("/shortlive/brief")
def shortlive_brief():
    if not ok():
        return "forbidden", 403
    with LOCK:
        txt = brief()
    if request.args.get("tg") == "1":
        mm.tg(txt)
    return txt, 200, {"Content-Type": "text/plain; charset=utf-8"}

@mm.app.route("/shortlive/pause")
def shortlive_pause():
    if not ok():
        return "forbidden", 403
    with LOCK:
        L["paused"] = "вручную"
        save()
    return "пауза: новых шортов нет, открытые бот ведёт дальше. Снять: /shortlive/resume"

@mm.app.route("/shortlive/resume")
def shortlive_resume():
    """Снять паузу. &day=1 — ещё и обнулить счётчики сегодняшнего дня (снять паузу «до завтра»).
    &kind=окно — включить обратно выключенный тип (счёт его убытка начнётся заново)."""
    if not ok():
        return "forbidden", 403
    kind = request.args.get("kind", "").strip()
    if kind:
        with LOCK:
            was = L.setdefault("koff", {}).pop(kind, None)
            L.setdefault("kon", {})[kind] = time.time()
            if request.args.get("day") == "1":
                L.setdefault("dg", {}).pop(grp(kind), None)
            save()
            r = pause_reason(kind)
        return (f"«{kind}» " + ("включены снова" if was else "и не были выключены") + " — убыток типа считаю заново"
                + ("" if kind in KINDS else f". ⚠️ «{kind}» нет в XSL_KINDS — бот их не берёт")
                + (f". Но действует: {r}" if r else ""))
    with LOCK:
        L["paused"], L["fails"] = "", 0
        if request.args.get("day") == "1":
            L.setdefault("dstat", {}).pop("live" if LIVE else "paper", None)
            L.setdefault("dg", {}).pop(MAIN, None)
        save()
        r = pause_reason()
    return "работает" if not r else f"ручная пауза снята, но действует: {r} (снять: &day=1)"

@mm.app.route("/shortlive/close")
def shortlive_close():
    if not ok():
        return "forbidden", 403
    coin = request.args.get("coin", "").upper()
    with LOCK:
        p = (L.get("pos") or {}).get(coin)
        if not p:
            return f"по {coin} открытого автошорта нет"
        if p["mode"] == "live":
            try:
                x = gt_shorts().get(coin)
            except Exception as e:
                return f"не вижу позиций Gate: {str(e)[:150]}"
            if not x:
                return f"на Gate позиции {coin} уже нет — бот сам запишет итог за минуту"
        try:
            CACHE["tk"] = tickers()
        except Exception:
            pass
        done = close_now(p, "закрыта вручную", time.time())
    return f"{coin}: " + ("закрыт — итог в Telegram" if done else "закрыть не вышло — причина в Telegram")

@mm.app.route("/shortlive/test")
def shortlive_test():
    """Проверка API Gate на 1 контракте. Без &go=1 — только план."""
    if not ok():
        return "forbidden", 403
    coin = request.args.get("coin", "DOGE").upper()
    out = {"монета": coin, "биржа": "Gate", "шаги": []}

    def st(name, fn):
        try:
            r = fn()
            out["шаги"].append([name, "ок", r])
            return r
        except Exception as x:
            out["шаги"].append([name, "ОШИБКА", str(x)[:300]])
            raise

    try:
        px = tickers()[coin]["last"]
        d = detail(coin)
    except Exception as x:
        out["ошибка"] = f"нет цены или контракта на Gate: {str(x)[:200]}"
        return jsonify(out)
    cs, tick = float(d["quanto_multiplier"]), d.get("order_price_round")
    minv = max(1, int(float(d.get("order_size_min") or 1)))
    lev = max(1, min(LEV, int(float(d.get("leverage_max") or LEV))))
    one = px * cs * minv
    out["план"] = {"цена": px, "монет_в_контракте": cs, "контрактов": minv, "объём_$": round(one, 2), "плечо": lev,
                   "маржа_$": round(one / lev, 2), "шаг_цены": tick,
                   "тейк_стоп_для_проверки": [rnd(px * 0.8, tick), rnd(px * 1.2, tick)]}
    try:
        n, _, blev, _, mmr = size(coin, px)
        out["в_боевой_сделке_было_бы"] = (f"{n} контр. ≈ ${n * px * cs:.0f}, плечо х{blev}, поддерживающая маржа "
                                          f"{mmr * 100:.2f}%, ликвидация ≈ +{(1 / blev - mmr) * 100:.1f}% "
                                          f"(стоп +{SL * 100:g}%)")
    except Skip as x:
        out["в_боевой_сделке_было_бы"] = f"не зашёл бы: {x}"
    try:
        out["режим_позиций_одиночный"] = gt_mode_ok()
        out["свободно_на_фьючерсах_$"] = gt_free()
    except Exception as x:
        out["ключ"] = f"ключ Gate не видит фьючерсы: {str(x)[:200]}"
    if request.args.get("go") != "1":
        out["дальше"] = ("добавь &go=1 — бот откроет 1 контракт шорта, поставит далёкие тейк/стоп (−20%/+20%), "
                         "найдёт их, снимет и закроет шорт")
        return jsonify(out)
    if one > 30:
        out["ошибка"] = "1 контракт дороже $30 — возьми монету дешевле"
        return jsonify(out)
    if coin in (L.get("pos") or {}) or coin in busy_coins():
        out["ошибка"] = "монета в работе (автошорт, магазин или аллокатор) — возьми другую"
        return jsonify(out)
    if out.get("режим_позиций_одиночный") is False:
        out["ошибка"] = "на Gate двусторонний режим позиций — переключи фьючерсы на одиночный (One-way)"
        return jsonify(out)
    opened = False
    with LOCK:
        try:
            if coin in st("позиции до проверки", lambda: sorted(gt_shorts())):
                raise Skip("по монете уже есть позиция — проверка смешала бы их")
            try:
                st("плечо и шорт 1 контракт", lambda: {k: v for k, v in (gt_open(coin, minv, lev) or {}).items()
                                                       if k in ("id", "size", "fill_price", "status", "finish_as",
                                                                "left", "tif")})
            finally:                                   # даже если ответ биржи потерялся — смотрим, открылся ли шорт
                time.sleep(2)
                opened = True                          # не узнали — значит, попробовать закрыть и предупредить
                x = st("позиция на бирже", lambda: {k: v for k, v in (gt_shorts().get(coin) or {}).items()
                                                    if k in ("size", "entry_price", "liq_price", "leverage",
                                                             "mode", "margin")})
                opened = bool(x)
            if not x:
                raise Skip("позиция не появилась")
            st("поставить тейк/стоп", lambda: tpsl_place(coin, px * 0.8, px * 1.2, tick))
            st("тейк/стоп в списке биржи (нужны tp и sl)", lambda: tpsl_find(coin))
            st("снять тейк/стоп", lambda: tpsl_cancel(coin))
            time.sleep(1)
            st("после снятия (должно быть пусто)", lambda: tpsl_find(coin))
        except Exception:
            pass
        if opened:
            try:
                tpsl_cancel(coin)
            except Exception:
                pass
            t0 = time.time()
            try:
                st("закрыть шорт", lambda: {k: v for k, v in (gt_close(coin) or {}).items()
                                            if k in ("id", "size", "fill_price", "status", "finish_as")})
                time.sleep(2)
                st("позиция после закрытия (должно быть пусто)", lambda: gt_shorts().get(coin))
                time.sleep(1)
                st("итог в истории Gate (цена, $, пометка)", lambda: closed_info(coin, t0 - 60))
            except Exception:
                pass
    bad = [s[0] for s in out["шаги"] if s[1] != "ок"]
    if any(s[0].startswith("тейк/стоп в списке") and s[1] == "ок" and not ("tp" in (s[2] or {}) and "sl" in (s[2] or {}))
           for s in out["шаги"]):
        bad.append("тейк/стоп не нашлись на бирже")
    if opened and ("закрыть шорт" in bad or not any(s[0] == "закрыть шорт" for s in out["шаги"])):
        out["ВНИМАНИЕ"] = f"шорт {coin} мог остаться открытым — проверь Gate и закрой вручную"
    elif opened and any(s[0].startswith("позиция после закрытия") and s[2] for s in out["шаги"]):
        out["ВНИМАНИЕ"] = f"позиция {coin} всё ещё видна после закрытия — проверь Gate и закрой вручную"
    out["вывод"] = ("всё работает ✅" if not bad else "не прошло: " + ", ".join(bad)
                    + " — пришли мне эту страницу целиком")
    return jsonify(out)

threading.Thread(target=loop, daemon=True).start()
