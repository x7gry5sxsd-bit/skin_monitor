# xhealth.py — самопроверка всей системы: модули живы, настройки в разумных пределах, ключи бирж отвечают,
#   денег хватает на сделку, пул адресов, зависшие сделки. /health?t=... (&tg=1 — в Telegram).
#   Раз в 30 мин проверяет сам и пишет в Telegram только о НОВЫХ проблемах.
import os, sys, time, threading, logging
from flask import jsonify, request
import mm_scanner as mm

def env(k, d=""):
    return os.getenv(k, d).strip()

LOOPS = ["mm_scanner", "tm_bot", "xarb", "xexec", "xshop", "xinv", "xroute", "xmiss", "xdaily", "xrotate", "xinvx", "xpredict", "xalloc", "xahedge", "xhunt"]
EVERY = float(env("XHL_EVERY_MIN", "30")) * 60    # свой ключ: XH_EVERY_MIN — это автохедж (2 мин)
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
    X, SH, A = mod("xexec"), mod("xshop"), mod("xaddr")
    # 2) настройки
    if X:
        info["схема 1"] = f"{'бумага' if X.DRY else 'ЖИВАЯ'}, {','.join(X.COINS)}, вход от {X.MIN_EDGE*100:.1f}%, " \
                          f"${X.TRADE:.0f}, подтверждений {X.CONFIRM}, Gate {'бумага' if X.GATE_DRY else 'живой'}"
        if X.MIN_EDGE < 0.012:
            warn.append(f"XE_MIN_EDGE = {X.MIN_EDGE} — для схемы 1 мало (монета едет минуты), нужно 0.015")
        if X.CONFIRM < 2:
            warn.append("XE_CONFIRM < 2 — схема 1 будет входить в ложные короткие окна")
        if X.TRADE > 30:
            warn.append(f"XE_TRADE_USD = {X.TRADE} — стакан HTX по BLUE держит только $15–30")
        if not X.GATE_DRY and not env("XE_GATE_USDT_ADDR"):
            warn.append("Gate живой, но XE_GATE_USDT_ADDR пуст — USDT с HTX на Gate возвращаться не будет")
        for c in X.S.get("cycles", []):
            if X.live(c) and time.time() - c["t0"] > X.TIMEOUT_MIN * 60:
                bad.append(f"сделка {c['coin']} в пути уже {int((time.time() - c['t0']) / 60)} мин — проверь перевод")
            if c.get("canceled") or c.get("stage") == "stuck":
                bad.append(f"сделка {c['coin']} застряла ({c.get('err') or 'вывод отменён'}) — /exec/clear после разбора")
    if SH:
        info["магазин"] = f"{'бумага' if SH.DRY else 'ЖИВОЙ'}, {','.join(SH.COINS)}, докупка {','.join(SH.VENUES)}, " \
                          f"вход от {SH.MIN_EDGE*100:.1f}% чистыми"
        if SH.MIN_EDGE < 0.004:
            warn.append(f"XS_MIN_EDGE = {SH.MIN_EDGE} — магазин может торговать почти в ноль")
        if SH.S.get("paused"):
            warn.append("магазин на паузе (/shop/start — включить)")
        for coin, c in SH.S.get("c", {}).items():
            for v, w in (c.get("wids") or {}).items():
                if w and time.time() - (w[3] if len(w) > 3 else w[1]) > 45 * 60:
                    warn.append(f"пачка {coin} с {v.upper()} в пути больше 45 мин")
    if X and SH:
        both = set(X.COINS) & set(SH.COINS)
        if both:
            bad.append(f"монеты {','.join(both)} одновременно в схеме 1 и в магазине — схема 1 продаст запас магазина!")
    # 3) ключи и деньги
    api = getattr(X, "api", None) if X else None
    if api:
        bal = {}
        for name, fn, has in (("mexc", lambda: api.mx_free("USDT"), True), ("htx", lambda: api.hx_free("USDT"), True),
                              ("gate", lambda: api.gt_free("USDT"), api.GT_KEY),
                              ("kucoin", lambda: api.kc_free("USDT", "trade"), getattr(api, "KC_KEY", ""))):
            if not has:
                continue
            try:
                bal[name] = round(fn(), 2)
            except Exception as e:
                bad.append(f"ключ {name.upper()} не отвечает: {str(e)[:80]}")
        info["USDT"] = bal
        need = (SH.TRADE if SH else 15) * 1.03
        used = set()                                   # биржи, где сейчас реально докупают (магазин + «Маятник»)
        try:
            PD = mod("xpend")
            pend = set(PD.pend_coins()) if PD and PD.P.get("loaded") else set()
            for c in (SH.COINS if SH else []):
                if c not in pend and not SH.exiting(c):
                    used |= {v for v in SH.venues_for(c) if v != "mexc" or (A and A.status(c)["good"])}
            if pend:
                used.add("kucoin")
        except Exception:
            pass
        for v in sorted(used):
            if v in bal and bal[v] < need:
                warn.append(f"на {v.upper()} {bal[v]} USDT — докупать нечем (одна сделка ≈ ${need:.0f})")
    # 4) пул адресов HTX
    if A and X:
        for coin in {c for c in X.COINS + (SH.COINS if SH else [])}:
            try:
                st = A.status(coin)
                if st["pool"] > 1 and st["good"] <= 2:
                    warn.append(f"пул адресов для {coin}: рабочих {st['good']} из {st['pool']} — добавь новые")
            except Exception:
                pass
    if not env("XE_BASELINE"):
        warn.append("XE_BASELINE не задан — строка «от старта» в итоге дня считается неверно")
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

# ---------- /config: что сервер реально видит в настройках (секреты скрыты) + сверка с рекомендуемыми ----------
RECOMMENDED = {"XP_COINS": "GALA", "XA_ON": "1", "XA_AUTO": "1", "XA_SLOTS": "2", "XA_SLOT_USD": "100",
               "XA_RESERVE": "40", "XA_HUNT": "1", "XA_HUNT_USD": "50", "XA_KUCOIN": "*", "XA_GATE": "*",
               "XA_MAX_FEE": "0.02", "XE_HTX_KEEP": "40", "XE_RET_VIA": "mexc", "XE_RETURN_AT": "50",
               "XS_DRY": "0", "XS_VENUES": "mexc", "XS_PENALTY_MEXC": "0.003", "XS_MIN_EDGE": "0.005",
               "XE_DRY": "0", "XE_MIN_EDGE": "0.99", "XE_VENUES": "mexc,gate", "XE_GATE_DRY": "0", "XH_AUTO": "1"}
MUST_EXIST = ["XE_KADDR", "XE_KUCOIN_USDT_ADDR", "XE_MEXC_USDT_ADDR", "XE_GATE_USDT_ADDR", "GATE_KEY", "GATE_SECRET",
              "MEXC_KEY", "MEXC_SECRET", "HTX_KEY", "HTX_SECRET", "KUCOIN_KEY", "KUCOIN_SECRET", "KUCOIN_PASSPHRASE",
              "TG_BOT_TOKEN", "DATABASE_URL"]
SECRET = ("KEY", "SECRET", "TOKEN", "PASS", "DATABASE")

def masked(k, v):
    return "*** (задан)" if any(s in k for s in SECRET) else v

@mm.app.route("/config")
def config_page():
    if not (mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN):
        return "forbidden", 403
    check = {}
    for k, want in RECOMMENDED.items():
        have = os.environ.get(k)
        if have is None:
            check[k] = f"❌ НЕТ (нужно {want})"
        elif have.strip() != want:
            check[k] = f"⚠️ {have.strip()} (рекомендую {want})"
        else:
            check[k] = f"✅ {want}"
    for k in MUST_EXIST:
        check[k] = "✅ задан" if os.environ.get(k, "").strip() else "❌ НЕТ"
    allv = {k: masked(k, v) for k, v in sorted(os.environ.items())
            if k[:2] in ("XA", "XS", "XE", "XP", "XH", "XX", "XI", "XR", "XM", "XW") or k in MUST_EXIST}
    return jsonify({"проверка": check, "все_настройки_X": allv})
