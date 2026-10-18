# xshortp.py — БУМАГА, ничего не торгует. Проверка гипотезы «шорт после окна»: если в момент окна HTX (бумага
#   сделала сделку по монете с продажей на HTX) открыть шорт на фьючерсе MEXC — цена потом падает?
#   На каждое событие: цена входа (фьючерс MEXC), дальше раз в минуту цена (максимум — для ликвидации),
#   через 1 / 4 / 24 ч — итог. Модель: плечо 2/3/5/10/20 с ликвидацией, плечо 3 со стопом 3%, комиссия, фандинг.
#   Сигналы: «окно» (сделка бумаги за последние 10 мин) и «всплеск» (3+ сделок за 20 мин в 2+ отрезках).
#   Одна монета — не чаще раза в XSP_COOL_H ч. /shortp?t=..., итог раз в сутки в Telegram. XSP_ON=0 — выкл.
import os, sys, time, json, threading, logging
from flask import jsonify, request
import mm_scanner as mm
import xpaper
import xexec_api as api

def env(k, d=""):
    return os.getenv(k, d).strip()

ON = env("XSP_ON", "1") == "1"
COOL = float(env("XSP_COOL_H", "2")) * 3600        # одна монета — не чаще раза в 2 ч
HOR = [1, 4, 24]                                   # горизонты, ч
LEVS = [2, 3, 5, 10, 20]
STOP = float(env("XSP_STOP", "0.03"))              # стоп-лосс для варианта «плечо 3 + стоп»
FEE = 0.0004                                       # комиссия фьючерса MEXC туда-обратно (от объёма)
MXF = "https://contract.mexc.com"
log = logging.getLogger("xshortp")
P = {}

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'shortp_v1'", fetch=True)
    P.update(json.loads(r[0][0]) if r else {})
    P.setdefault("open", [])
    P.setdefault("done", [])
    P.setdefault("cool", {})
    P.setdefault("rep_t", time.time())

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

def step(now, tk):
    for coin, kind in signals(now):                    # 1) новые события
        if coin not in tk or now - P["cool"].get(coin, 0) < COOL or len(P["open"]) >= 200:
            continue
        P["cool"][coin] = now
        px, fr = tk[coin]
        P["open"].append({"coin": coin, "kind": kind, "t": int(now), "px": px, "fund": fr, "hi": px, "lo": px,
                          "r": {}, "adv": {}})
    keep = []
    for e in P["open"]:                                # 2) ведём открытые
        if e["coin"] in tk:
            px = tk[e["coin"]][0]
            e["hi"], e["lo"] = max(e["hi"], px), min(e["lo"], px)
            for h in HOR:
                if str(h) not in e["r"] and now - e["t"] >= h * 3600:
                    e["r"][str(h)] = round((e["px"] - px) / e["px"], 5)          # доход шорта без плеча
                    e["adv"][str(h)] = round((e["hi"] - e["px"]) / e["px"], 5)   # худший рост против шорта
        if len(e["r"]) == len(HOR) or now - e["t"] > 26 * 3600:
            P["done"] = (P["done"] + [e])[-600:]
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

def summary(kind=None):
    out = {}
    for h in HOR:
        es = [e for e in P.get("done", []) + P.get("open", []) if str(h) in e["r"] and (not kind or e["kind"] == kind)]
        if not es:
            out[f"{h}ч"] = {"событий": 0}
            continue
        rs = [e["r"][str(h)] for e in es]
        row = {"событий": len(es), "средний ход шорта %": round(sum(rs) / len(rs) * 100, 2),
               "в плюсе %": round(sum(1 for r in rs if r > 0) / len(rs) * 100)}
        for L in LEVS:
            ps = [pnl(e, h, L) for e in es]
            row[f"х{L}: $ на $10 маржи"] = round(sum(ps) * 10, 2)
            row[f"х{L}: ликвидаций"] = sum(1 for p in ps if p == -1.0)
        row[f"х3 стоп {STOP*100:.0f}%: $ на $10"] = round(sum(pnl(e, h, 3, STOP) for e in es) * 10, 2)
        out[f"{h}ч"] = row
    return out

def report():
    s = summary()
    L = ["📉 Бумага «шорт после окна» — итог (ничего не торгуется)"]
    for h, row in s.items():
        if not row.get("событий"):
            L.append(f"{h}: событий пока нет")
            continue
        L.append(f"{h}: событий {row['событий']}, шорт в плюсе {row['в плюсе %']}%, средний ход "
                 f"{row['средний ход шорта %']:+.2f}% | на $10 маржи: х3 {row['х3: $ на $10 маржи']:+.2f}$, "
                 f"х10 {row['х10: $ на $10 маржи']:+.2f}$ (ликв. {row['х10: ликвидаций']}), "
                 f"х20 {row['х20: $ на $10 маржи']:+.2f}$ (ликв. {row['х20: ликвидаций']}), "
                 f"х3+стоп {row[f'х3 стоп {STOP*100:.0f}%: $ на $10']:+.2f}$")
    L.append(f"Открыто сейчас: {len(P.get('open', []))}. Вывод делать после 50+ событий на горизонте")
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
    return jsonify({"все": summary(), "только_всплески": summary("всплеск"), "открыто": len(P.get("open", [])),
                    "последние": (P.get("done", []) + P.get("open", []))[-15:]})

if ON:
    threading.Thread(target=loop, daemon=True).start()
