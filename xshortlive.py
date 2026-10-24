# xshortlive.py — АВТОШОРТ по ✅-кандидатам. По умолчанию БУМАГА: только пишет в Telegram, что сделал бы.
#   Сигнал — из бумаги xshortp: появился кандидат («аллокатор» — монету отобрал аллокатор, или «кандидат» — бумага
#   магазина ≥ $0,5/сут), и он ✅ по правилу «уже падает»: цена ниже максимума часа хотя бы на 0,5% и за сутки
#   выросла меньше чем на 15%. Памп (рост за сутки ≥ XSL_MAX24) — никогда.
#   Что делает: шорт на фьючерсе MEXC (изолированная маржа XSL_MARGIN $, плечо XSL_LEV) и сразу НА БИРЖЕ тейк
#   −XSL_TP (1%) и стоп +XSL_SL (5%) на всю позицию. Через XSL_MAX_HOLD_H (24 ч) — закрывает по рынку.
#   Страховка: каждые 10 с бот сам смотрит цену; тейк/стоп на бирже не встал или не сработал — закрывает сам.
#   Ограничения: позиций не больше XSL_MAX_POS (2); за день XSL_DAY_STOPS (2) стопа или убыток XSL_DAY_LOSS $ —
#   пауза до завтра (день — по Иркутску); общий убыток модуля XSL_MAX_LOSS $ — пауза до /shortlive/resume;
#   капитал (xbal) ниже XSL_STOPLINE $ — не заходит. Монеты магазина и аллокатора и монеты, где на MEXC уже есть
#   шорт, не трогает. Автохедж и аллокатор живые шорты автошорта не трогают (защита внутри этого файла).
#   XSL_MODE: off — новых шортов нет, paper — бумага (по умолчанию), live — торгует.
#   Страницы (?t=STATS_TOKEN): /shortlive — всё (JSON), /shortlive/brief — коротко (&tg=1 — в Telegram),
#   /shortlive/pause, /shortlive/resume, /shortlive/close?coin=AXS — закрыть сейчас,
#   /shortlive/test?coin=DOGE — план проверки API; &go=1 — живая проверка на 1 контракте: открыть шорт, поставить
#   далёкие тейк/стоп, найти их на бирже, снять, закрыть шорт.
import os, sys, time, json, threading, logging
from decimal import Decimal
from flask import jsonify, request
import mm_scanner as mm
import xpaper
import xexec_api as api
import xhedge as hx

def env(k, d=""):
    return os.getenv(k, d).strip()

VERSION = "xshortlive v1 · 24.10"
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
RULE = env("XSL_RULE", "full")                         # full — только ✅; pump — только фильтр пампа
MAX_AGE = float(env("XSL_MAX_AGE_S", "180"))           # сигнал старше 3 мин — не берём
MAX_SLIP = float(env("XSL_MAX_SLIP", "0.01"))          # цена ушла от сигнала больше чем на 1% — не берём
COOL_H = float(env("XSL_COOL_H", "12"))                # одна монета — не чаще раза в 12 ч
SL_TREND = int(env("XSL_SL_TREND", "2"))               # стоп по цене: 1 — последней, 2 — справедливой (без прострелов)
EVERY = 10
FEE = 0.0004                                           # комиссия MEXC туда-обратно от объёма (оценка для бумаги)
MXF = "https://contract.mexc.com"
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
    mm.tg(("🤖 Автошорт: " if live else "🧪🤖 Автошорт [бумага]: ") + text)

# ---------- состояние ----------
def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'shortlive_v1'", fetch=True)
    L.update(json.loads(r[0][0]) if r else {})
    for k, v in (("pos", {}), ("done", []), ("seen", {}), ("skips", []), ("cool", {}), ("warned", {}),
                 ("tot", {}), ("dstat", {}), ("day", today()), ("paused", ""), ("fails", 0)):
        L.setdefault(k, v)
    L["loaded"] = 1

def save():
    if not L.get("loaded"):
        return
    try:
        xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('shortlive_v1', %s)
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
    L["day"], L["dstat"] = d, {}
    L["seen"] = {k: v for k, v in L["seen"].items() if time.time() - v < 2 * 86400}

def pause_reason():
    if L.get("paused"):
        return f"пауза: {L['paused']}"
    s = dst(MODE)
    if s["sl"] >= DAY_STOPS:
        return f"пауза до завтра: за день уже {s['sl']} стопа"
    if s["pnl"] <= -DAY_LOSS:
        return f"пауза до завтра: убыток дня {s['pnl']:+.2f}$"
    return None

# ---------- биржа ----------
def tickers():
    """{МОНЕТА: {last, fair, fund}} — один запрос на все фьючерсы MEXC."""
    out = {}
    for x in api.S.get(f"{MXF}/api/v1/contract/ticker", timeout=20).json().get("data") or []:
        s = x.get("symbol", "")
        if s.endswith("_USDT") and float(x.get("lastPrice") or 0) > 0:
            last = float(x["lastPrice"])
            out[s[:-5]] = {"last": last, "fair": float(x.get("fairPrice") or 0) or last,
                           "fund": float(x.get("fundingRate") or 0)}
    return out

def detail(coin):
    """Параметры контракта MEXC (монет в контракте, шаг цены, плечо, минимум) — кэш 6 ч."""
    c = CACHE.get("d" + coin)
    if c and time.time() - c[0] < 6 * 3600:
        return c[1]
    d = api.S.get(f"{MXF}/api/v1/contract/detail", params={"symbol": f"{coin}_USDT"}, timeout=20).json().get("data")
    if isinstance(d, list):
        d = next((x for x in d if x.get("symbol") == f"{coin}_USDT"), None)
    if not isinstance(d, dict) or not d.get("contractSize"):
        raise Skip("MEXC не отдал параметры контракта")
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

def num(v):
    v = float(v)
    return int(v) if v == int(v) else v

def size(coin, px):
    """(контрактов, монет в контракте, плечо, шаг цены): маржа × плечо / цена контракта."""
    d = detail(coin)
    if d.get("apiAllowed") is False:
        raise Skip("MEXC запретил торговлю этим фьючерсом через API")
    cs = float(d["contractSize"])
    lev = max(1, min(LEV, int(float(d.get("maxLeverage") or LEV))))
    minv = max(1, int(float(d.get("minVol") or 1)))
    n = int(MARGIN * lev / (px * cs))
    if n < minv:
        raise Skip(f"1 контракт ≈${px * cs * minv:.0f} — больше позиции ${MARGIN * lev:.0f}")
    if d.get("maxVol"):
        n = min(n, int(float(d["maxVol"])))
    return n, cs, lev, d.get("priceUnit")

def mx_shorts():
    """Открытые шорты MEXC: {МОНЕТА: позиция}. Ошибка — исключение (не видим позиции — не торгуем)."""
    out = {}
    for x in hx.mxf("GET", "/api/v1/private/position/open_positions") or []:
        if x.get("positionType") == 2 and float(x.get("holdVol") or 0) > 0:
            out[x.get("symbol", "")[:-5]] = x
    return out

def mx_open(coin, n, lev):
    return hx.mxf("POST", "/api/v1/private/order/submit", {"symbol": f"{coin}_USDT", "vol": n, "leverage": lev,
                                                           "side": 3, "type": 5, "openType": 1})

def mx_close(coin, n, lev):
    return hx.mxf("POST", "/api/v1/private/order/submit", {"symbol": f"{coin}_USDT", "vol": n, "leverage": lev,
                                                           "side": 2, "type": 5, "openType": 1})

def tpsl_find(coin, pid):
    """Наш тейк/стоп в списке действующих на бирже или None."""
    for x in hx.mxf("GET", "/api/v1/private/stoporder/open_orders", {"symbol": f"{coin}_USDT"}) or []:
        if str(x.get("positionId")) == str(pid) and int(x.get("state") or 0) == 1:
            return {"id": x.get("id"), "tp": x.get("takeProfitPrice"), "sl": x.get("stopLossPrice")}
    return None

def tpsl_place(coin, pid, vol, tp, sl):
    """Тейк и стоп на бирже на всю позицию (по рынку при срабатывании). Вернёт найденный план или None."""
    hx.mxf("POST", "/api/v1/private/stoporder/place",
           {"positionId": int(pid), "vol": num(vol), "volType": 2,
            "takeProfitPrice": tp, "profitTrend": 1, "takeProfitType": 0, "takeProfitOrderPrice": tp,
            "stopLossPrice": sl, "lossTrend": SL_TREND, "stopLossType": 0, "stopLossOrderPrice": sl})
    time.sleep(1)
    return tpsl_find(coin, pid)

def tpsl_cancel(pid):
    if pid:                                            # без positionId биржа сняла бы тейки/стопы по ВСЕМ контрактам
        hx.mxf("POST", "/api/v1/private/stoporder/cancel_all", {"positionId": int(pid)})

def closed_info(coin, pid):
    """Итог закрытой позиции из истории MEXC: (цена закрытия, прибыль $) или None."""
    d = hx.mxf("GET", "/api/v1/private/position/list/history_positions",
               {"symbol": f"{coin}_USDT", "page_num": 1, "page_size": 20})
    rows = d if isinstance(d, list) else ((d or {}).get("resultList") or (d or {}).get("list") or [])
    for x in rows:
        if str(x.get("positionId")) == str(pid):
            pnl = x.get("realised")
            if pnl is None:
                pnl = x.get("closeProfitLoss")
            return float(x.get("closeAvgPrice") or 0) or None, (float(pnl) if pnl is not None else None)
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

def mine(coin):
    """Живой шорт автошорта по монете (смотрим в модуль на момент вызова — переживает перезагрузку файла)."""
    M = sys.modules.get("xshortlive")
    pos = (getattr(M, "L", None) or L).get("pos") or {}
    return pos.get(coin, {}).get("mode") == "live"

def guards():
    """Автохедж не закрывает живые шорты автошорта как «голые», аллокатор не заходит в такую монету."""
    HA, AL = sys.modules.get("xahedge"), sys.modules.get("xalloc")
    if HA and hasattr(HA, "one") and not getattr(HA.one, "_xsl", False):
        orig = HA.one
        def one(coin, *a, **k):
            M = sys.modules.get("xshortlive")
            if M and M.mine(coin):
                return None
            return orig(coin, *a, **k)
        one._xsl = True
        HA.one = one
    if AL and hasattr(AL, "enter") and not getattr(AL.enter, "_xsl", False):
        orig_e = AL.enter
        def enter(coin, *a, **k):
            M = sys.modules.get("xshortlive")
            if M and M.mine(coin):
                mm.tg(f"🧠 {coin}: заход отменён — по монете открыт автошорт (шорты смешались бы)")
                return False
            return orig_e(coin, *a, **k)
        enter._xsl = True
        AL.enter = enter

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
    r = pause_reason()
    if r:
        return r, True
    if coin in L["pos"]:
        return "по монете уже открыт автошорт", False
    if now - L["cool"].get(coin, 0) < COOL_H * 3600:
        return f"по монете был шорт меньше {COOL_H:.0f} ч назад", True
    if len(L["pos"]) >= MAX_POS:
        return f"уже {len(L['pos'])} позиции (лимит {MAX_POS})", True
    if coin not in tk:
        return "нет цены фьючерса MEXC", True
    if tk[coin]["fund"] < MIN_FUND:
        return f"фандинг {tk[coin]['fund'] * 100:.2f}% за 8 ч — шорт платит много", True
    if not hx.api_ok(coin):
        return "MEXC не даёт торговать этим фьючерсом через API", True
    if coin in busy_coins():
        return "монета магазина/аллокатора — там свой хедж-шорт", True
    mv = tk[coin]["last"] / e["px"] - 1
    if abs(mv) > MAX_SLIP:
        return f"цена уже ушла на {mv * 100:+.1f}% от сигнала", True
    return None

def skip(e, why, loud):
    L["skips"] = (L["skips"] + [[int(time.time()), e["coin"], e["kind"], why]])[-40:]
    if loud:
        say(f"пропустил {e['coin']} ✅ — {why}")

def open_text(p):
    pr = p.get("pre") or {}
    nt = p["n"] * p["cs"] * p["px"]
    if p["mode"] != "live":
        on = "бумага — на бирже ничего не открыто"
    elif p.get("tpsl"):
        on = "тейк и стоп стоят на бирже ✅"
    else:
        on = f"⚠️ тейк/стоп на бирже НЕ встали ({p.get('tpsl_err', '?')}) — слежу сам и закрою по рынку"
    return (f"шорт {p['coin']} по {p['px']:.6g} — {p['n']} контр. ≈ ${nt:.0f} (маржа ≈ ${nt / p['lev']:.2f}, х{p['lev']})\n"
            f"Тейк {p['tp']:.6g} (−{TP * 100:g}%) ≈ +${nt * TP:.2f} · стоп {p['sl']:.6g} (+{SL * 100:g}%) ≈ −${nt * SL:.2f}"
            f" · не дольше {HOLD_H:g} ч\n"
            f"Сигнал: {p['kind']}, за сутки {(pr.get('ch24') or 0) * 100:+.1f}%, ниже максимума часа на "
            f"{abs(pr.get('dh1') or 0) * 100:.1f}% · {on}")

def adopt(p, x):
    """Позиция появилась на бирже: точная цена входа, тейк/стоп от неё — и сразу на биржу."""
    p["pid"] = x.get("positionId")
    p["px"] = float(x.get("holdAvgPrice") or x.get("openAvgPrice") or p["px"])
    p["n"] = num(x.get("holdVol") or p["n"])
    p["liq"] = float(x.get("liquidatePrice") or 0) or None
    p["tp"], p["sl"] = rnd(p["px"] * (1 - TP), p["tick"]), rnd(p["px"] * (1 + SL), p["tick"])
    p["tpsl_try"], p["tpsl_t"] = 1, time.time()
    L["fails"] = 0
    try:
        p["tpsl"] = tpsl_place(p["coin"], p["pid"], p["n"], p["tp"], p["sl"])
        if not p["tpsl"]:
            p["tpsl_err"] = "биржа приняла, но в списке тейков/стопов его нет"
    except Exception as x2:
        p["tpsl"], p["tpsl_err"] = None, str(x2)[:150]
    hedge_quiet(p["coin"], True)
    say(open_text(p), live=True)
    save()

def open_pos(e, now, tk):
    coin, px = e["coin"], tk[e["coin"]]["last"]
    n, cs, lev, tick = size(coin, px)
    p = {"coin": coin, "kind": e["kind"], "sig_t": e["t"], "sig_px": e["px"], "pre": e.get("pre"), "t": int(now),
         "n": n, "cs": cs, "lev": lev, "tick": tick, "mode": "live" if LIVE else "paper", "px": px,
         "tp": rnd(px * (1 - TP), tick), "sl": rnd(px * (1 + SL), tick), "hi": px, "lo": px, "bk": 0}
    if p["tp"] > px * (1 - TP / 2) or not px * (1 + SL / 2) < p["sl"] < px * (1 + SL * 1.5):
        raise Skip(f"шаг цены контракта {tick} слишком крупный для тейка {TP * 100:g}% и стопа {SL * 100:g}%")
    if not LIVE:
        L["pos"][coin], L["cool"][coin] = p, now
        say(open_text(p), live=False)
        save()
        return
    if coin in mx_shorts():
        raise Skip("на MEXC по монете уже есть шорт — не смешиваю")
    try:
        cap = capital()
    except Exception:                                  # капитал не посчитать — не мешает; общий лимит убытка свой
        log.exception("xshortlive capital")
        cap = None
    if cap and STOPLINE > 0 and cap[0] < STOPLINE:
        raise Skip(f"капитал ${cap[0]:.0f} ниже стоп-линии ${STOPLINE:.0f}" + (f" (не видно: {cap[1]})" if cap[1] else ""))
    need = n * cs * px / lev * 1.05
    try:
        avail = float(hx.balances()["mexc"][0][2])
    except Exception:
        avail = None
    if avail is not None and avail < need:
        raise Skip(f"на фьючерсах MEXC свободно ${avail:.2f}, нужно ~${need:.2f}")
    p["pid"] = None                                    # запись до ордера: при сбое связи бот сам найдёт позицию
    L["pos"][coin], L["cool"][coin] = p, now
    save()
    try:
        mx_open(coin, n, lev)
        L["fails"] = 0
    except Exception as x:
        p["open_err"] = f"ошибка MEXC: {str(x)[:150]}"
        L["fails"] = L.get("fails", 0) + 1
        if L["fails"] >= 3 and not L.get("paused"):
            L["paused"] = "MEXC 3 раза подряд не открыл шорт"
            say(f"⛔ {L['paused']} — новые шорты остановлены. Проверь /shortlive/test, потом /shortlive/resume")
    for _ in range(5):
        time.sleep(1.5)
        try:
            ps = mx_shorts()
        except Exception:
            continue
        if coin in ps:
            adopt(p, ps[coin])
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
           "pre": p.get("pre"), "note": note}
    L["done"] = (L["done"] + [rec])[-300:]
    k = ("tp" if outcome.startswith("тейк") else "sl" if outcome.startswith("стоп") or outcome == "ликвидация"
         else "time" if outcome == "время" else "other")
    for s in (dst(p["mode"]), tot(p["mode"])):
        s["n"] += 1
        s[k] = s.get(k, 0) + 1
        s["pnl"] = round(s["pnl"] + pnl, 2)
    d = dst(p["mode"])
    say(f"{'✅' if pnl > 0 else '❌'} {coin}: {outcome} через {round((now - p['t']) / 60)} мин, выход {ex:.6g}, "
        f"итог {pnl:+.2f}$ (хуже всего по пути +{rec['worst_pct']:.1f}%){note}\n"
        f"Сегодня: сделок {d['n']}, тейков {d['tp']}, стопов {d['sl']}, {d['pnl']:+.2f}$", live=p["mode"] == "live")
    if p["mode"] == ("live" if LIVE else "paper"):
        t = tot(p["mode"])
        if t["pnl"] <= -MAX_LOSS and not L.get("paused"):
            L["paused"] = f"общий убыток {t['pnl']:+.2f}$ — больше лимита ${MAX_LOSS:.0f}"
            say(f"⛔ {L['paused']}. Новые шорты остановлены. Включить снова: /shortlive/resume")
        r = pause_reason()
        if r and r.startswith("пауза до завтра") and L["warned"].get("dpause") != today():
            L["warned"]["dpause"] = today()
            say(f"⏸ {r}")
    save()

def classify(p, ex, pnl):
    nt = p["n"] * p["cs"] * p["px"]
    if pnl is not None and pnl <= -0.8 * nt / p["lev"]:
        return "ликвидация"
    if ex <= p["tp"] * 1.003:
        return "тейк"
    if ex >= p["sl"] * 0.997:
        return "стоп"
    return "закрыта на бирже (вручную?)"

def close_now(p, outcome, now, note="", vol=None):
    """Закрыть по рынку (живую — на MEXC, бумажную — по последней цене)."""
    coin, q = p["coin"], (CACHE.get("tk") or {}).get(p["coin"])
    if p["mode"] != "live":
        finish(p, outcome, q["last"] if q else p["px"], None, now, note)
        return True
    try:
        mx_close(coin, num(vol or p["n"]), p["lev"])
    except Exception as x:
        if now - L["warned"].get("cl" + coin, 0) > 600:
            L["warned"]["cl" + coin] = now
            say(f"❗ {coin}: не смог закрыть шорт ({str(x)[:120]}) — пробую снова каждые 10 с. Не выйдет — закрой "
                f"вручную на MEXC", live=True)
        return False
    time.sleep(2)
    try:
        tpsl_cancel(p.get("pid"))
    except Exception:
        pass
    info = None
    for _ in range(3):
        try:
            info = closed_info(coin, p.get("pid"))
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

def live_step(p, q, ps, now):
    coin = p["coin"]
    if p.get("pid") is None:                           # ордер ушёл, позиции ещё не видели
        if coin in ps:
            adopt(p, ps[coin])
        elif now - p["t"] > 120:
            L["pos"].pop(coin, None)
            say(f"{coin}: шорт так и не появился на MEXC — запись снята. {p.get('open_err', '')}", live=True)
            save()
        return
    if coin not in ps or str(ps[coin].get("positionId")) != str(p["pid"]):
        p.setdefault("gone_t", now)                    # закрыта на бирже: тейк, стоп, вручную или ликвидация
        try:
            info = closed_info(coin, p["pid"])
        except Exception:
            info = None
        if not info and now - p["gone_t"] < 60:        # история MEXC появляется с задержкой
            return
        ex = (info[0] if info else None) or (q["last"] if q else p["px"])
        pnl = info[1] if info else None
        try:
            tpsl_cancel(p["pid"])
        except Exception:
            pass
        finish(p, classify(p, ex, pnl), ex, pnl, now)
        return
    p.pop("gone_t", None)
    if not q:
        return
    vol = ps[coin].get("holdVol")
    if now - p["t"] >= HOLD_H * 3600:
        close_now(p, "время", now, vol=vol)
        return
    if not p.get("tpsl") and p.get("tpsl_try", 0) < 3 and now - p.get("tpsl_t", 0) > 60:
        p["tpsl_try"], p["tpsl_t"] = p.get("tpsl_try", 0) + 1, now
        try:                                           # тейк/стоп не встал — ещё попытка раз в минуту, до 3 раз
            p["tpsl"] = tpsl_place(coin, p["pid"], p["n"], p["tp"], p["sl"])
            if p["tpsl"]:
                say(f"{coin}: тейк и стоп на бирже встали с {p['tpsl_try']}-й попытки ✅", live=True)
        except Exception as x:
            p["tpsl_err"] = str(x)[:150]
    slpx = q["fair"] if SL_TREND == 2 else q["last"]
    if p.get("tpsl"):                                  # страховка: биржа должна была сработать, но позиция жива
        hit = ("тейк (закрыл сам)" if q["last"] <= p["tp"] * 0.997 else
               "стоп (закрыл сам)" if slpx >= p["sl"] * 1.005 else None)
        need = 2
    else:                                              # тейка/стопа на бирже нет — веду сам
        hit = "тейк (закрыл сам)" if q["last"] <= p["tp"] else "стоп (закрыл сам)" if slpx >= p["sl"] else None
        need = 1
    p["bk"] = p.get("bk", 0) + 1 if hit else 0
    if hit and p["bk"] >= need:
        close_now(p, hit, now, note=" — биржевой тейк/стоп не сработал" if p.get("tpsl") else "", vol=vol)

def monitor(now, tk):
    ps = None
    if any(p["mode"] == "live" for p in L["pos"].values()):
        try:
            ps = mx_shorts()
            L.pop("pos_err_t", None)
        except Exception as x:
            L.setdefault("pos_err_t", now)
            if now - L["pos_err_t"] > 300 and now - L["warned"].get("poserr", 0) > 3600:
                L["warned"]["poserr"] = now
                say(f"⚠️ 5+ мин не вижу позиций MEXC ({str(x)[:100]}). Тейк/стоп на бирже стоят, но проверь вручную",
                    live=True)
    for coin, p in list(L["pos"].items()):
        q = tk.get(coin)
        if q:
            p["hi"], p["lo"] = max(p["hi"], q["last"]), min(p["lo"], q["last"])
        if p["mode"] != "live":
            paper_step(p, q, now)
        elif ps is not None:
            live_step(p, q, ps, now)

def step(now):
    day_roll()
    SP = sys.modules.get("xshortp")
    new = scan(now, SP) if SP and MODE in ("paper", "live") else []
    if not new and not L["pos"]:
        return
    tk = tickers()
    CACHE["tk"] = tk
    for e in new:
        why = check(e, now, SP, tk)
        if why:
            skip(e, why[0], why[1])
            continue
        try:
            open_pos(e, now, tk)
        except Skip as x:
            skip(e, str(x), True)
        except Exception as x:
            log.exception("xshortlive open %s", e.get("coin"))
            skip(e, f"ошибка: {str(x)[:150]}", True)
    monitor(now, tk)

def loop():
    time.sleep(90)
    while not L.get("loaded"):
        try:
            load()
        except Exception:
            log.exception("xshortlive load")
            time.sleep(60)
    guards()
    for p in L["pos"].values():
        if p["mode"] == "live":
            hedge_quiet(p["coin"], True)
    if MODE != "off" or L["pos"]:
        say(f"запущен — {'ЖИВОЙ' if LIVE else 'бумага' if MODE == 'paper' else 'ВЫКЛ (только веду открытые)'}: "
            f"маржа ${MARGIN:g} × х{LEV}, тейк −{TP * 100:g}%, стоп +{SL * 100:g}%, не дольше {HOLD_H:g} ч, "
            f"позиций ≤{MAX_POS}; беру только ✅ (рост за сутки <{MAX24 * 100:g}%)"
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

def brief():
    md = {"live": "ЖИВОЙ", "paper": "БУМАГА", "off": "ВЫКЛ"}.get(MODE, MODE)
    nt = MARGIN * LEV
    out = [f"🤖 {VERSION} — {md}, {lt(None, '%d.%m %H:%M')}",
           f"Маржа ${MARGIN:g} × х{LEV} = ${nt:.0f} · тейк −{TP * 100:g}% (+${nt * TP:.2f}) · стоп +{SL * 100:g}% "
           f"(−${nt * SL:.2f}) · ≤{HOLD_H:g} ч · ≤{MAX_POS} поз. · только ✅"]
    if not L.get("loaded"):
        return "\n".join(out + ["Ещё загружается (первые ~2 мин после запуска)"])
    s = dst("live" if LIVE else "paper")
    out.append(f"Сегодня: сделок {s['n']}, тейков {s['tp']}, стопов {s['sl']}, {s['pnl']:+.2f}$ · "
               f"{pause_reason() or 'работает'}")
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
                   + ("" if p["mode"] != "live" else " · на бирже ✅" if p.get("tpsl") else " · ⚠️ веду сам"))
    last = L["done"][-5:]
    if last:
        out.append("Последние: " + "; ".join(f"{d['coin']} {lt(d['t_close'])} {d['outcome']} {d['pnl']:+.2f}$"
                                             for d in last))
    sk = L["skips"][-5:]
    if sk:
        out.append("Пропуски: " + "; ".join(f"{x[1]} {lt(x[0])} — {x[3]}" for x in sk))
    return "\n".join(out)

@mm.app.route("/shortlive")
def shortlive_page():
    if not ok():
        return "forbidden", 403
    with LOCK:
        return jsonify({"версия": VERSION, "режим": MODE, "пауза": pause_reason(),
                        "настройки": {"маржа_$": MARGIN, "плечо": LEV, "тейк_%": TP * 100, "стоп_%": SL * 100,
                                      "стоп_по_цене": "справедливой" if SL_TREND == 2 else "последней",
                                      "держим_ч": HOLD_H, "позиций": MAX_POS, "стопов_в_день": DAY_STOPS,
                                      "убыток_дня_$": DAY_LOSS, "общий_убыток_$": MAX_LOSS,
                                      "памп_сутки_%": MAX24 * 100, "стоп_линия_$": STOPLINE,
                                      "сигналы": sorted(KINDS), "правило": RULE},
                        "сегодня": L.get("dstat"), "всего": L.get("tot"),
                        "открыто": [pos_view(p) for p in (L.get("pos") or {}).values()],
                        "сделки": (L.get("done") or [])[-20:],
                        "пропуски": [[lt(x[0], "%d.%m %H:%M")] + x[1:] for x in (L.get("skips") or [])[-20:]]})

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
    """Снять паузу. &day=1 — ещё и обнулить счётчики сегодняшнего дня (снять паузу «до завтра»)."""
    if not ok():
        return "forbidden", 403
    with LOCK:
        L["paused"], L["fails"] = "", 0
        if request.args.get("day") == "1":
            L.setdefault("dstat", {}).pop("live" if LIVE else "paper", None)
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
        vol = None
        if p["mode"] == "live":
            try:
                x = mx_shorts().get(coin)
            except Exception as e:
                return f"не вижу позиций MEXC: {str(e)[:150]}"
            if not x:
                return f"на MEXC шорта {coin} уже нет — бот сам запишет итог за минуту"
            vol = x.get("holdVol")
        try:
            CACHE["tk"] = tickers()
        except Exception:
            pass
        done = close_now(p, "закрыта вручную", time.time(), vol=vol)
    return f"{coin}: " + ("закрыт — итог в Telegram" if done else "закрыть не вышло — причина в Telegram")

@mm.app.route("/shortlive/test")
def shortlive_test():
    """Проверка API на 1 контракте. Без &go=1 — только план."""
    if not ok():
        return "forbidden", 403
    coin = request.args.get("coin", "DOGE").upper()
    out = {"монета": coin, "шаги": []}

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
        out["ошибка"] = f"нет цены или контракта: {str(x)[:200]}"
        return jsonify(out)
    cs, tick = float(d["contractSize"]), d.get("priceUnit")
    minv = max(1, int(float(d.get("minVol") or 1)))
    lev = max(1, min(LEV, int(float(d.get("maxLeverage") or LEV))))
    one = px * cs * minv
    out["план"] = {"цена": px, "монет_в_контракте": cs, "контрактов": minv, "объём_$": round(one, 2), "плечо": lev,
                   "маржа_$": round(one / lev, 2), "шаг_цены": tick, "торговля_через_API": d.get("apiAllowed"),
                   "тейк_стоп_для_проверки": [rnd(px * 0.8, tick), rnd(px * 1.2, tick)]}
    n = int(MARGIN * lev / (px * cs))
    out["в_боевой_сделке_было_бы"] = (f"{n} контр. ≈ ${n * px * cs:.0f}" if n >= minv
                                      else f"не зашёл бы: 1 контракт ≈${one:.0f} больше позиции ${MARGIN * lev:.0f}")
    if request.args.get("go") != "1":
        out["дальше"] = "добавь &go=1 — бот откроет 1 контракт шорта, поставит далёкие тейк/стоп (−20%/+20%), найдёт их, снимет и закроет шорт"
        return jsonify(out)
    if one > 30:
        out["ошибка"] = "1 контракт дороже $30 — возьми монету дешевле"
        return jsonify(out)
    if coin in (L.get("pos") or {}) or coin in busy_coins():
        out["ошибка"] = "монета в работе (автошорт, магазин или аллокатор) — возьми другую"
        return jsonify(out)
    opened, pid, vol = False, None, minv
    with LOCK:
        try:
            if coin in st("шорты до проверки", lambda: sorted(mx_shorts())):
                raise Skip("по монете уже есть шорт — проверка смешала бы позиции")
            try:
                st("открыть шорт", lambda: mx_open(coin, minv, lev))
            finally:                                   # даже если ответ биржи потерялся — смотрим, открылся ли шорт
                time.sleep(2)
                opened = True                          # не узнали — значит, попробовать закрыть и предупредить
                x = st("позиция на бирже", lambda: {k: v for k, v in (mx_shorts().get(coin) or {}).items()
                                                    if k in ("positionId", "holdVol", "holdAvgPrice", "liquidatePrice",
                                                             "leverage", "openType")})
                opened = bool(x)
                if x:
                    pid, vol = x["positionId"], x.get("holdVol") or minv
            if not x:
                raise Skip("позиция не появилась")
            st("поставить тейк/стоп", lambda: tpsl_place(coin, pid, vol, rnd(px * 0.8, tick), rnd(px * 1.2, tick)))
            st("тейк/стоп в списке биржи", lambda: tpsl_find(coin, pid))
            st("снять тейк/стоп", lambda: tpsl_cancel(pid))
            time.sleep(1)
            st("после снятия (должно быть пусто)", lambda: tpsl_find(coin, pid))
        except Exception:
            pass
        if opened:
            if pid:
                try:
                    tpsl_cancel(pid)
                except Exception:
                    pass
            try:
                st("закрыть шорт", lambda: mx_close(coin, num(vol), lev))
                time.sleep(2)
                st("шорт после закрытия (должно быть пусто)", lambda: mx_shorts().get(coin))
                if pid:
                    time.sleep(1)
                    st("итог в истории MEXC (цена, $)", lambda: closed_info(coin, pid))
            except Exception:
                pass
    bad = [s[0] for s in out["шаги"] if s[1] != "ок"]
    if opened and ("закрыть шорт" in bad or not any(s[0] == "закрыть шорт" for s in out["шаги"])):
        out["ВНИМАНИЕ"] = f"шорт {coin} мог остаться открытым — проверь MEXC и закрой вручную"
    elif opened and any(s[0].startswith("шорт после закрытия") and s[2] for s in out["шаги"]):
        out["ВНИМАНИЕ"] = f"шорт {coin} всё ещё виден после закрытия — проверь MEXC и закрой вручную"
    out["вывод"] = ("всё работает ✅" if not bad else "не прошло: " + ", ".join(bad)
                    + " — пришли мне эту страницу целиком")
    return jsonify(out)

guards()                                               # сразу: автохедж и аллокатор уже загружены
threading.Thread(target=loop, daemon=True).start()
