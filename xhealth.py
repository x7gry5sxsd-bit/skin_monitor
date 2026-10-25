# xhealth.py — самопроверка всей системы: модули живы, настройки в разумных пределах, ключи бирж отвечают,
#   денег хватает на сделку, пул адресов, зависшие сделки. /health?t=... (&tg=1 — в Telegram).
#   Раз в 30 мин проверяет сам и пишет в Telegram только о НОВЫХ проблемах.
#   v2 (22.10): версии модулей (видно, что деплой встал), монеты без полного хеджа, бан аллокатора; /config сверяет
#   с настройками после пакета 22.10 (без «Маятника» и KuCoin).
#   v3 (25.10): MEXC закрыт для аккаунта — предупреждения про USDT и фьючерсы MEXC убраны (MEXC не пополнять).
#   Магазин на паузе — это норма, а не предупреждение; пул адресов проверяется, только пока магазин работает.
#   АВТОШОРТ: цикл жив, режим, итог, открытые позиции; на фьючерсах Gate хватает денег на XSL_MAX_POS позиций.
#   /config: XA_AUTO и XH_AUTO теперь рекомендуются 0 (магазин свёрнут).
import os, sys, time, threading, logging
VERSION = "xhealth v3 · 25.10"
from flask import jsonify, request
import mm_scanner as mm

def env(k, d=""):
    return os.getenv(k, d).strip()

LOOPS = (["mm_scanner"] if os.getenv("MM_SCAN", "1").strip() == "1" else []) + \
        (["tm_bot"] if os.getenv("TM_ON", "1").strip() == "1" else []) + \
        ["xarb", "xexec", "xshop", "xdaily", "xalloc", "xahedge", "xhunt", "xshortp", "xpscout", "xshopp", "xradar",
         "xshortlive"] + \
        (["xpend"] if os.getenv("XP_COINS", "").strip() else [])   # «Маятник» выключен — его цикл и не должен жить
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
            info["магазин"] += " — НА ПАУЗЕ (это нормально: магазин свёрнут)"
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
        for name, fn, has in (("htx", lambda: api.hx_free("USDT"), True),   # MEXC закрыт для аккаунта — не проверяем
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
                used |= set(getattr(PD, "PARTNERS", ["kucoin"]))   # партнёры «Маятника»: KuCoin и/или Gate
        except Exception:
            pass
        used.discard("mexc")                           # MEXC закрыт — пополнять нельзя, предупреждать не о чем
        if SH and SH.S.get("paused"):
            used = set()                               # магазин на паузе — докупок нет
        low = {"gate": float(env("XHL_LOW_GATE", "40")), "kucoin": float(env("XHL_LOW_KUCOIN", "30"))}
        tip = {"kucoin": "USDT на KuCoin НЕ переводить — KuCoin замораживает пополнения",
               "gate": "бот пополнит сам с HTX; если не пополнилось за 2 ч — переведи $40 USDT на Gate (сеть BSC)"}
        for v in sorted(used):                         # заранее, а не когда уже пусто; текст без цифр — придёт один раз
            lim = max(need, low.get(v, need))
            if v in bal and bal[v] < lim:
                warn.append(f"на {v.upper()} меньше ${lim:.0f} USDT для докупок — {tip.get(v, 'пополни')}")
    # 3а) автошорт на Gate
    SL = mod("xshortlive")
    if SL and getattr(SL, "L", None) is not None and SL.L.get("loaded"):
        try:
            t = (SL.L.get("tot") or {}).get("live" if SL.LIVE else "paper") or {}
            pos = ", ".join(f"{c} ({p['mode']})" for c, p in (SL.L.get("pos") or {}).items()) or "нет"
            info["автошорт"] = (f"{'ЖИВОЙ' if SL.LIVE else SL.MODE}, маржа ${SL.MARGIN:g}×{SL.LEV}, тейк {SL.TP * 100:g}%, "
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
    # 3б) хедж и аллокатор: монета без полного шорта — главный риск
    HG, AL = mod("xahedge"), mod("xalloc")
    if HG:
        for coin, x in (HG.A.get("last") or {}).items():
            inv, short, gap = x.get("inventory") or 0, x.get("short") or 0, x.get("gap_pct")
            fresh = time.time() - (x.get("t") or 0) < 1800
            if fresh and inv > 0 and gap is not None and gap > 20 and (not SH or coin in SH.COINS):
                bad.append(f"{coin} без полного хеджа: запас {inv}, шорт {short} — смотри /hedge/auto")
            elif fresh and inv == 0 and short > 0:
                warn.append(f"шорт {coin} {short} без монеты — автохедж закроет его сам за ~5 мин; не закрылся — /hedge/auto")
    if AL and AL.A:
        bans = {c: b[1] for c, b in (AL.A.get("ban") or {}).items() if b[0] > time.time()}
        if bans:
            info["бан аллокатора"] = "; ".join(f"{c}: {w[:50]}" for c, w in bans.items())
        info["аллокатор"] = (f"{'сам' if AL.AUTO else 'предлагает'}{'' if AL.ON else ' (ВЫКЛ)'}, монеты "
                             f"{', '.join(AL.coins()) or 'нет'}, K={AL.A.get('k', getattr(AL, 'PAPER_K', '?'))} | "
                             f"{AL.A.get('why') or '—'}")
    vers = {m: getattr(mod(m), "VERSION", "старая") for m in ("xalloc", "xahedge", "xhedge", "xhealth", "xshortp",
                                                               "xshortlive") if mod(m)}
    info["версии"] = ", ".join(vers.values())
    # 4) пул адресов HTX
    if A and X:
        shop_on = SH and not SH.S.get("paused")        # магазин на паузе — его адреса не нужны
        for coin in {c for c in X.COINS + (SH.COINS if shop_on else [])}:
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
RECOMMENDED = {"XA_ON": "1", "XA_AUTO": "0", "XA_SLOTS": "2", "XA_SLOT_USD": "100", "XA_RESERVE": "40",
               "XA_EXIT_H": "24", "XA_MAX_FEE": "0.02", "XA_GATE": "*", "XA_HUNT": "0",
               "XSP_PARTNERS": "gate", "XSP_MAX_DEV": "0.08", "XS_DRY": "0", "XS_MIN_EDGE": "0.007",
               "XH_AUTO": "0", "XH_EVERY_MIN": "2", "XH_FUT_SKIP": "kucoin,gate",
               "XE_DRY": "0", "XE_MIN_EDGE": "0.99", "XE_GATE_DRY": "0", "XE_RETURN_AT": "45", "XE_HTX_KEEP": "5",
               "XE_RET_SKIP": "kucoin,mexc", "XE_USDT_CHAIN": "bep20usdt", "XE_FWD_AT": "99999"}
CODE_DEFAULT = {"XA_HOLD_H": "48", "XA_SWAP_EVERY_H": "24", "XA_SWAP_GAP": "1", "XA_MIN_RATE": "0.5",
                "XE_VENUES": "mexc,gate"}          # не заданы в Render — работает значение из кода, это нормально
MUST_EMPTY = ["XP_COINS", "XA_KUCOIN"]                 # «Маятник» и KuCoin убраны: эти ключи должны быть пустыми
MUST_EXIST = ["XE_KADDR", "XE_MEXC_USDT_ADDR", "XE_GATE_USDT_ADDR", "GATE_KEY", "GATE_SECRET",   # XE_KADDR — адрес HTX
              "MEXC_KEY", "MEXC_SECRET", "HTX_KEY", "HTX_SECRET", "TG_BOT_TOKEN", "DATABASE_URL", "STATS_TOKEN"]   # для пачек с Gate
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
    for k, want in CODE_DEFAULT.items():
        have = (os.environ.get(k) or "").strip()
        check[k] = (f"✅ по умолчанию в коде ({want})" if not have else
                    f"✅ {want}" if have == want else f"⚠️ {have} (в коде по умолчанию {want} — ключ можно удалить)")
    for k in MUST_EXIST:
        check[k] = "✅ задан" if os.environ.get(k, "").strip() else "❌ НЕТ"
    for k in MUST_EMPTY:
        check[k] = "✅ пусто" if not os.environ.get(k, "").strip() else f"⚠️ {os.environ[k].strip()} (должно быть пусто)"
    fr = (os.environ.get("XB_FROZEN") or "").strip()
    check["XB_FROZEN"] = ("✅ нет" if fr in ("", "0", "0.0") else
                          f"⚠️ {fr} — деньги KuCoin уже в капитале, поставь 0, иначе итог дня посчитает их дважды")
    allv = {k: masked(k, v) for k, v in sorted(os.environ.items())
            if k[:2] in ("XA", "XS", "XE", "XP", "XH", "XX", "XI", "XR", "XM", "XW", "XB") or k in MUST_EXIST
            or k in ("TZ_OFFSET", "DAILY_HOUR", "PUBLIC_URL")}
    return jsonify({"проверка": check, "все_настройки_X": allv})
