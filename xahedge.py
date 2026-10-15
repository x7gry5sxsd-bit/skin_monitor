# xahedge.py — автохедж магазина: шорт по каждой монете держится равным ВСЕМУ запасу (HTX + MEXC + Gate + пачки
#   в пути). Расхождение больше допуска → бот сам добавляет или уменьшает шорт. Следит за ценой ликвидации.
#   XH_AUTO=0 (по умолчанию) — только пишет, что сделал бы. XH_AUTO=1 — торгует сам. /hedge/auto?t=...
import os, sys, time, threading, logging
from flask import jsonify, request
import mm_scanner as mm
import xpaper
import xhedge as hx
import xexec_api as api

def env(k, d=""):
    return os.getenv(k, d).strip()

AUTO = env("XH_AUTO", "0") == "1"
VENUE = env("XH_VENUE", "mexc")                   # где держать шорт по умолчанию; XH_VENUE_<МОНЕТА> — для монеты
TOL = float(env("XH_TOL", "0.10"))                # допуск расхождения запаса и шорта (10%)
EVERY = float(env("XH_EVERY_MIN", "5")) * 60
LEV = int(env("XH_LEV", "2"))
MAX_PER_H = int(env("XH_MAX_PER_HOUR", "6"))      # не больше 6 поправок в час — защита от «дёрганья»
LIQ_WARN = float(env("XH_LIQ_WARN", "0.85"))      # предупреждать, когда цена дошла до 85% цены ликвидации
log = logging.getLogger("xahedge")
A = {"acts": [], "last": {}, "warned": {}}

def venue_for(coin):
    return env(f"XH_VENUE_{coin}", VENUE)

def inventory(coin, SH):
    """Весь запас монеты: HTX + MEXC + Gate + пачки, которые сейчас едут."""
    inv = api.hx_free(coin) + api.mx_free(coin) + (api.gt_free(coin) if api.GT_KEY else 0)
    for w in (SH.S.get("c", {}).get(coin, {}).get("wids") or {}).values():
        if w and len(w) > 4 and w[4]:
            inv += float(w[4])
    return inv

def margin(v):
    """Свободная маржа на фьючерсах биржи (USDT) или None, если не узнать."""
    b = hx.balances().get(v)
    try:
        return float(b[0][2]) if v == "mexc" else float(b.get("available"))
    except Exception:
        return None

def say(text):
    mm.tg(("🛡 " if AUTO else "🧪🛡 [бумага] ") + text)

def liq_check():
    pos = []
    try:
        for x in hx.mxf("GET", "/api/v1/private/position/open_positions") or []:
            if x.get("positionType") == 2:
                pos.append(("mexc", x.get("symbol", "")[:-5], float(x.get("liquidatePrice") or 0)))
    except Exception:
        pass
    try:
        for x in api.gt("GET", "/futures/usdt/positions") or []:
            if float(x.get("size") or 0) < 0:
                pos.append(("gate", x.get("contract", "").split("_")[0], float(x.get("liq_price") or 0)))
    except Exception:
        pass
    for v, coin, liq in pos:
        b = xpaper.book("mexc", coin) or xpaper.book("htx", coin)
        if not b or not liq:
            continue
        px = b[1][0][0]
        A["last"].setdefault(coin, {})["liq_left_pct"] = round((liq / px - 1) * 100, 1)
        if px >= liq * LIQ_WARN and time.time() - A["warned"].get(f"liq{coin}", 0) > 3600:
            A["warned"][f"liq{coin}"] = time.time()
            mm.tg(f"⚠️ Шорт {coin} на {v.upper()}: цена {px:.6f}, ликвидация {liq:.6f} — до неё {(liq/px-1)*100:.0f}%. "
                  f"Добавь маржу на фьючерсах!")

def run_once():
    SH = sys.modules.get("xshop")
    if not SH or SH.DRY:
        return
    sh, err = hx.shorts()
    if err:                                            # не видим позиции — не трогаем, чтобы не наделать лишнего
        A["err"] = err
        return
    for coin in SH.COINS:
        b = xpaper.book("mexc", coin) or xpaper.book("htx", coin)
        if not b:
            continue
        px, inv = b[0][0][0], inventory(coin, SH)
        short = sum(q for _, q, _ in sh.get(coin, []))
        diff = inv - short
        A["last"][coin] = {**A["last"].get(coin, {}), "inventory": round(inv), "short": round(short),
                           "gap_pct": round(diff / inv * 100, 1) if inv else None, "t": int(time.time())}
        v = venue_for(coin)
        mult = hx.perps()[v].get(coin)
        if not inv or abs(diff) / inv <= TOL or not mult or abs(diff) < mult:
            continue
        if not AUTO and time.time() - A["warned"].get(f"paper{coin}", 0) < 3600:
            continue
        if len([a for a in A["acts"] if time.time() - a[0] < 3600]) >= MAX_PER_H:
            if time.time() - A["warned"].get("limit", 0) > 3600:
                A["warned"]["limit"] = time.time()
                say(f"Хедж {coin}: поправок за час уже {MAX_PER_H} — жду, проверь вручную")
            continue
        if diff > 0:                                   # запаса больше шорта → добавить шорт
            need, avail = diff * px / LEV * 1.1, margin(v)
            if avail is not None and avail < need:
                if time.time() - A["warned"].get(f"m{v}", 0) > 3600:
                    A["warned"][f"m{v}"] = time.time()
                    mm.tg(f"⚠️ Хедж {coin}: на фьючерсах {v.upper()} свободно ${avail:.2f}, нужно ~${need:.2f}. "
                          f"Переведи USDT на фьючерсный счёт")
                continue
            kind, qty = "добавил", diff
        else:                                          # шорта больше запаса → уменьшить там, где он есть
            where = [x[0] for x in sh.get(coin, [])]
            v = v if v in where else (where[0] if where else v)
            kind, qty = "уменьшил", -diff
        if AUTO:
            r = hx.open_short(v, coin, qty, LEV) if kind == "добавил" else hx.close_short(v, coin, qty)
            qty = r.get("coins", 0)
        else:
            A["warned"][f"paper{coin}"] = time.time()
        A["acts"] = (A["acts"] + [[int(time.time()), coin, v, kind, round(qty), AUTO]])[-50:]
        say(f"Хедж {coin}: запас {inv:,.0f}, шорт {short:,.0f} → {kind} шорт на {qty:,.0f} на {v.upper()}")

def loop():
    time.sleep(300)
    say(f"Автохедж запущен: биржа {VENUE.upper()}, допуск {TOL*100:.0f}%, плечо {LEV}×, проверка раз в {EVERY/60:.0f} мин")
    while True:
        try:
            run_once()
            liq_check()
        except Exception as e:
            log.exception("xahedge")
            if time.time() - A["warned"].get("err", 0) > 3600:
                A["warned"]["err"] = time.time()
                mm.tg(f"❗ Автохедж: {str(e)[:200]}")
        time.sleep(EVERY)

@mm.app.route("/hedge/auto")
def hedge_auto():
    if not (mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN):
        return "forbidden", 403
    return jsonify({"mode": "ТОРГУЕТ" if AUTO else "бумага (только пишет)", "venue": VENUE, "tolerance_pct": TOL * 100,
                    "coins": A["last"], "actions": A["acts"][-10:], "errors": A.get("err")})

threading.Thread(target=loop, daemon=True).start()
