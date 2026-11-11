# xhealth.py — самопроверка всей системы: модули живы, ключи бирж отвечают, автошорт и деньги на фьючерсах Gate.
#   /health?t=... (&tg=1 — в Telegram). Раз в 30 мин проверяет сам и пишет в Telegram только о НОВЫХ проблемах.
#   v4 (02.11): после уборки. Схема 1, магазин, автохедж, аллокатор, «Маятник», быстрая схема и лонг-бумага
#   удалены — их проверки убраны. USDT на HTX / Gate / KuCoin смотрим напрямую через xexec_api.
#   Добавлена строка «капитал» (из xbal, кэш 15 мин) — раньше она была в итоге дня xdaily.
#   /config: показывает настройки и список ключей X…, которые больше ни один файл не читает — их можно удалить
#   в Render (ничего не изменится).
#   v5 (03.11): Upbit-шорт (xupbit): цикл, версия и строка «upbit-шорт» — что открыто и итог.
#   v6 (11.11): фандинг-скальп (xfscalp): цикл, версия и строка «фандинг-скальп».
#   v6.1 (11.11): строка «автошорт» — маржа, тейк и лимит позиций по каждому включённому типу (было: общие значения).
import os, re, sys, time, threading, logging
VERSION = "xhealth v6.1 · 11.11"
from flask import jsonify, request
import mm_scanner as mm

def env(k, d=""):
    return os.getenv(k, d).strip()

LOOPS = (["mm_scanner"] if env("MM_SCAN", "0") == "1" else []) + \
        (["tm_bot"] if env("TM_ON", "0") == "1" else []) + \
        ["xarb", "xhunt", "xshopp", "xradar", "xshortlive", "xitog", "xupbit", "xfscalp"] + \
        (["xshortp"] if env("XSP_ON", "1") == "1" else []) + \
        (["xprem"] if env("XPR_ON", "1") == "1" else [])
EVERY = float(env("XHL_EVERY_MIN", "30")) * 60
log = logging.getLogger("xhealth")
H = {"seen": set()}

def mod(n):
    if n == "tm_bot":                                  # tm_bot запущен как главный файл → модуль __main__
        return sys.modules.get("tm_bot") or sys.modules.get("__main__")
    return sys.modules.get(n)

def alive_loops():
    out = set()
    for t in threading.enumerate():
        f = getattr(t, "_target", None)
        if f is not None and t.is_alive():
            m = getattr(f, "__module__", "")
            out.add("tm_bot" if m == "__main__" else m)
    return out

def check():
    bad, warn, info = [], [], {}
    # 1) модули и их циклы
    alive = alive_loops()
    for m in LOOPS:
        if not mod(m):
            bad.append(f"модуль {m} не загрузился — смотри Render → Logs")
        elif m not in alive:
            bad.append(f"цикл {m} остановился")
    # 2) ключи бирж: USDT на споте (только смотрим — бот сам туда ничего не переводит)
    try:
        import xexec_api as api
    except Exception as e:
        api = None
        bad.append(f"xexec_api не загрузился: {str(e)[:80]}")
    if api:
        bal = {}
        for name, fn, has in (("htx", lambda: api.hx_free("USDT"), api.HX_KEY),
                              ("gate", lambda: api.gt_free("USDT"), api.GT_KEY),
                              ("kucoin", lambda: api.kc_free("USDT", "trade"), api.KC_KEY)):
            if not has:
                continue
            try:
                bal[name] = round(fn(), 2)
            except Exception as e:
                bad.append(f"ключ {name.upper()} не отвечает: {str(e)[:80]}")
        info["USDT на споте"] = bal
    # 3) автошорт на Gate
    SL = mod("xshortlive")
    if SL and getattr(SL, "L", None) is not None and SL.L.get("loaded"):
        try:
            t = (SL.L.get("tot") or {}).get("live" if SL.LIVE else "paper") or {}
            pos = ", ".join(f"{c} ({p['mode']})" for c, p in (SL.L.get("pos") or {}).items()) or "нет"
            kinds = sorted(SL.KINDS) or ["все"]
            cfg = "; ".join(f"{k}: маржа ${SL.margin_of(k):g}×{SL.LEV}, тейк {SL.tp_of(k) * 100:g}%"
                            + (f", позиций ≤{SL.POS_BY[k]}" if k in SL.POS_BY else "") for k in kinds)
            info["автошорт"] = (f"{'ЖИВОЙ' if SL.LIVE else SL.MODE}, {cfg}, "
                                f"стоп {SL.SL * 100:g}% | всего {t.get('n', 0)} сд., тейков {t.get('tp', 0)}, "
                                f"стопов {t.get('sl', 0)}, {t.get('pnl', 0):+.2f}$ | открыто: {pos}"
                                + (f" | {SL.pause_reason()}" if SL.pause_reason() else ""))
            if SL.LIVE:
                free, need = SL.gt_free(), SL.MAX_POS * SL.MARGIN * 1.05
                if free is not None and free + sum(p["n"] * p["cs"] * p["px"] / p["lev"]
                                                   for p in (SL.L.get("pos") or {}).values()) < need:
                    warn.append(f"на фьючерсах Gate мало денег для автошорта: свободно ${free:.0f}, на {SL.MAX_POS} "
                                f"позиции по ${SL.MARGIN:g} нужно ~${need:.0f} — переведи со спота Gate (Перевод → Фьючерсы)")
        except Exception as e:
            warn.append(f"автошорт: не получилось проверить ({str(e)[:80]})")
        try:
            cap = SL.capital()
            if cap:
                info["капитал"] = f"${cap[0]:.2f}" + (f" (не видно: {cap[1]})" if cap[1] else "") + " — подробно /balance"
        except Exception:
            pass
    UB = mod("xupbit")                                 # шорт после листинга на Upbit
    if UB and getattr(UB, "L", None) is not None and UB.L.get("loaded"):
        t = UB.L.get("tot") or {}
        pos = ", ".join(f"{c} ({p['mode']})" for c, p in (UB.L.get("pos") or {}).items()) or "нет"
        info["upbit-шорт"] = (f"{'ЖИВОЙ' if UB.LIVE else UB.MODE}, ${UB.USD:.0f} ×{UB.LEV}, стоп +{UB.SL * 100:g}%, "
                              f"держу {UB.HOLD_H:g} ч | сделок {t.get('n', 0)}, {t.get('pnl', 0):+.2f}$ | открыто: {pos}"
                              + (f" | ⛔ {UB.L['paused']}" if UB.L.get("paused") else ""))
    FS = mod("xfscalp")
    if FS and FS.L.get("loaded"):
        t = FS.L.get("tot" if FS.LIVE else "tot_p") or {}
        info["фандинг-скальп"] = (f"{'ЖИВОЙ' if FS.LIVE else FS.MODE}, ${FS.size_now():.0f} ×{FS.LEV}, ставка от −{FS.MIN_RATE * 100:g}%, "
                                  f"держу {FS.HOLD:g} с | сделок {t.get('n', 0)}, {t.get('pnl', 0):+.2f}$"
                                  + (f" | проба {FS.L.get('probe', 0)} из {FS.PROBE_N}" if FS.LIVE and FS.L.get("probe", 0) < FS.PROBE_N else "")
                                  + (f" | ⛔ {FS.L['paused']}" if FS.L.get("paused") else ""))
    vers = {m: getattr(mod(m), "VERSION", "старая") for m in ("xhealth", "xhedge", "xshortp", "xshortlive",
                                                               "xitog", "xprem", "xupbit", "xfscalp") if mod(m)}
    info["версии"] = ", ".join(vers.values())
    return {"ok": not bad, "problems": bad, "warnings": warn, "info": info, "loops_alive": sorted(alive & set(LOOPS))}
def text(r):
    head = "🩺 Система в порядке" if r["ok"] and not r["warnings"] else \
        ("🩺 ❗ Есть проблемы" if not r["ok"] else "🩺 ⚠️ Есть предупреждения")
    L = [head]
    L += [f"❗ {p}" for p in r["problems"]] + [f"⚠️ {w}" for w in r["warnings"]]
    L += [f"• {k}: {v}" for k, v in r["info"].items()]
    return "\n".join(L)

def loop():
    time.sleep(600)
    while True:
        try:
            r = check()
            new = [x for x in r["problems"] + r["warnings"] if x not in H["seen"]]
            H["seen"] = set(r["problems"] + r["warnings"])
            if new:
                mm.tg("🩺 Самопроверка — новое:\n" + "\n".join(("❗ " if x in r["problems"] else "⚠️ ") + x for x in new))
        except Exception:
            log.exception("xhealth")
        time.sleep(EVERY)

@mm.app.route("/health")
def health_page():
    if not (mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN):
        return "forbidden", 403
    r = check()
    if request.args.get("tg") == "1":
        mm.tg(text(r))
    return text(r), 200, {"Content-Type": "text/plain; charset=utf-8"}

threading.Thread(target=loop, daemon=True).start()

# ---------- /config: что сервер реально видит в настройках (секреты скрыты) + какие ключи уже лишние ----------
MUST_EXIST = ["GATE_KEY", "GATE_SECRET", "TG_BOT_TOKEN", "DATABASE_URL", "STATS_TOKEN"]
SECRET = ("KEY", "SECRET", "TOKEN", "PASS", "DATABASE")
DYNAMIC = ("XE_MEMO_", "XE_GADDR_")                    # такие ключи xfer собирает из имени монеты

def ours(k):
    """Ключ наших модулей: X + до 3 букв + «_» (XSL_TP, XA_HUNT…). Системные XDG_… не трогаем."""
    return bool(re.match(r"X[A-Z]{0,3}_[A-Z0-9_]+$", k)) and not k.startswith("XDG_")

def masked(k, v):
    return "*** (задан)" if any(s in k for s in SECRET) else v

def used_keys():
    """Все ключи вида X…, которые упоминаются в файлах, загруженных сейчас (наша папка)."""
    here, keys = os.path.dirname(os.path.abspath(__file__)), set()
    for m in list(sys.modules.values()):
        f = getattr(m, "__file__", None) or ""
        if f.endswith(".py") and os.path.dirname(os.path.abspath(f)) == here:
            try:
                with open(f, encoding="utf-8") as fh:
                    keys |= set(re.findall(r"[\"'](X[A-Z0-9_]+)[\"']", fh.read()))
            except Exception:
                pass
    return keys

@mm.app.route("/config")
def config_page():
    if not (mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN):
        return "forbidden", 403
    check_ = {k: ("✅ задан" if env(k) else "❌ НЕТ") for k in MUST_EXIST}
    fr = env("XB_FROZEN")
    check_["XB_FROZEN"] = ("✅ нет" if fr in ("", "0", "0.0") else
                           f"⚠️ {fr} — деньги KuCoin уже в капитале, поставь 0, иначе капитал посчитается дважды")
    used = used_keys()
    extra = sorted(k for k in os.environ if ours(k) and k not in used and not k.startswith(DYNAMIC))
    allv = {k: masked(k, v) for k, v in sorted(os.environ.items())
            if ours(k) or k in MUST_EXIST or k in ("TZ_OFFSET", "PUBLIC_URL")}
    return jsonify({"версия": VERSION, "проверка": check_,
                    "можно_удалить_в_Render": extra or "лишних ключей нет",
                    "все_настройки_X": allv})
