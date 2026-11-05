# xskins.py — БУМАГА «СКИНЫ CS2: Lis-skins → заявки Market.csgo». Ничего не покупает и не продаёт.
#   Идея (проверено на ценах 05.11.2026): у части ходовых скинов готовая заявка на покупку на Market.csgo после его
#   комиссии (5%) выше самой дешёвой цены на Lis-skins на 3–16%. Купить на Lis-skins → через 7 дней (Steam не даёт
#   передать купленный скин раньше) продать в заявку Market.csgo. На 05.11: 18 предметов, 692 шт. на $1813 → +$91 (5%).
#   Главный вопрос, на который отвечает бумага: остаются ли заявки на месте через 7 дней и сколько остаётся после всех
#   комиссий (пополнение Lis-skins XSK_FEE_IN — 1% через СБП; вывод с Market.csgo XSK_FEE_OUT — укажи, когда узнаешь).
#   Раз в XSK_EVERY_MIN (60) мин: цены Lis-skins (только без блокировки обмена), заявки и минимальные цены Market.csgo,
#   раз в сутки — реальные продажи Skinport за неделю (ходовость). Сигнал: (заявка × (1 − 5%) × (1 − вывод)) /
#   (цена Lis × (1 + пополнение)) − 1 ≥ XSK_MIN_EDGE (3%), цена от $0.5, ≥10 продаж в неделю. Бумажная «покупка» —
#   1 шт. на предмет, не чаще раза в сутки на предмет. Через 7 дней: итог при продаже в текущую заявку и по минимальной цене.
#   Страницы (?t=STATS_TOKEN): /skins — всё (JSON), /skins/brief — коротко (&tg=1 — в Telegram). Итог — раз в день в
#   9:00 (Иркутск) в Telegram. XSK_ON=0 — выключить.
import os, time, json, threading, logging, statistics
import requests
from flask import jsonify, request
import mm_scanner as mm
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

VERSION = "xskins v1 · 05.11"
ON = env("XSK_ON", "1") == "1"
EVERY = float(env("XSK_EVERY_MIN", "60")) * 60
MIN_EDGE = float(env("XSK_MIN_EDGE", "0.03"))
FEE_MC = float(env("XSK_FEE_MC", "0.05"))               # комиссия Market.csgo с продажи
FEE_IN = float(env("XSK_FEE_IN", "0.01"))               # пополнение Lis-skins (СБП 1%)
FEE_OUT = float(env("XSK_FEE_OUT", "0"))                # вывод с Market.csgo на карту
MIN_PRICE = float(env("XSK_MIN_PRICE", "0.5"))
MIN_SALES7 = int(env("XSK_MIN_SALES7", "10"))
HOLD_D = float(env("XSK_HOLD_D", "7"))
LIS = "https://lis-skins.com/market_export_json/csgo.json"
MC_ASK = "https://market.csgo.com/api/v2/prices/USD.json"
MC_BID = "https://market.csgo.com/api/v2/prices/orders/USD.json"
SP_HIST = "https://api.skinport.com/v1/sales/history"
log = logging.getLogger("xskins")
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 uspex-skins", "Accept-Encoding": "br, gzip"})
L = {}
LOCK = threading.RLock()

def lt(t=None, f="%d.%m %H:%M"):
    return time.strftime(f, time.gmtime((time.time() if t is None else t) + 8 * 3600))

def say(text):
    mm.tg("🧪🔫 Скины [бумага]: " + text)

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'xskins_v1'", fetch=True)
    L.update(json.loads(r[0][0]) if r else {})
    for k, v in (("buys", []), ("last", {}), ("scan", {}), ("sp", {}), ("sp_t", 0), ("day", "")):
        L.setdefault(k, v)
    L["loaded"] = 1

def save():
    if not L.get("loaded"):
        return
    try:
        xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('xskins_v1', %s)
                    ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(L),))
    except Exception:
        log.exception("xskins save")

# ---------- данные ----------
def lis_prices():
    """{предмет: (самая дешёвая цена без блокировки обмена, лотов)} — сводка Lis-skins (~5 МБ)."""
    out = {}
    for x in S.get(LIS, timeout=120).json() or []:
        p = float(x.get("unlocked_price") or 0)
        if p > 0:
            out[x["name"]] = (p, int(x.get("count") or 0))
    return out

def mc(url):
    d = S.get(url, timeout=120).json()
    return {x["market_hash_name"]: (float(x["price"]), int(float(x.get("volume") or 0))) for x in d.get("items") or []
            if float(x.get("price") or 0) > 0}

def sp_sales(now):
    """Раз в сутки: продажи Skinport за 7 дней {предмет: [продаж, медиана]} — ходовость предмета."""
    if L["sp"] and now - L.get("sp_t", 0) < 86400:
        return L["sp"]
    try:
        r = S.get(SP_HIST, params={"app_id": 730, "currency": "USD"}, timeout=120)
        if r.status_code == 200:
            L["sp"] = {x["market_hash_name"]: [int((x.get("last_7_days") or {}).get("volume") or 0),
                                               float((x.get("last_7_days") or {}).get("median") or 0)]
                       for x in r.json() or [] if (x.get("last_7_days") or {}).get("volume")}
            L["sp_t"] = now
        else:
            log.warning("skinport %s", r.status_code)
    except Exception:
        log.exception("skinport")                      # оставляем прошлые данные, повтор на следующем скане
    return L["sp"]

def net(bid, lis):
    return bid * (1 - FEE_MC) * (1 - FEE_OUT) / (lis * (1 + FEE_IN)) - 1

# ---------- шаг ----------
def step(now):
    lis = lis_prices()
    asks, bids = mc(MC_ASK), mc(MC_BID)
    sp = sp_sales(now)
    cands = []
    for nm, (bid, bv) in bids.items():
        if nm not in lis or bv < 1:
            continue
        p, cnt = lis[nm]
        if p < MIN_PRICE or (sp.get(nm) or [0])[0] < MIN_SALES7:
            continue
        e = net(bid, p)
        if e >= MIN_EDGE:
            cands.append({"name": nm, "lis": p, "lis_n": cnt, "bid": bid, "bid_n": bv,
                          "ask": (asks.get(nm) or (0, 0))[0], "edge": round(e * 100, 2), "cap": min(bv, cnt),
                          "sales7": sp[nm][0]})
    cands.sort(key=lambda c: -c["edge"])
    new = 0
    for c in cands:
        if now - L["last"].get(c["name"], 0) < 86400:
            continue
        L["last"][c["name"]] = int(now)
        L["buys"].append(dict(c, t=int(now), r={}))
        new += 1
    L["buys"] = L["buys"][-800:]
    L["last"] = {k: v for k, v in L["last"].items() if now - v < 2 * 86400}
    for b in L["buys"]:                               # итог через 7 дней: продать в текущую заявку / по минимальной цене
        if "7д" in b["r"] or now - b["t"] < HOLD_D * 86400:
            continue
        bid_now = (bids.get(b["name"]) or (0, 0))[0]
        ask_now = (asks.get(b["name"]) or (0, 0))[0]
        cost = b["lis"] * (1 + FEE_IN)
        b["r"]["7д"] = round((bid_now * (1 - FEE_MC) * (1 - FEE_OUT) - cost) if bid_now else -cost, 3)
        b["r"]["по_мин_цене"] = round(((ask_now - 0.01) * (1 - FEE_MC) * (1 - FEE_OUT) - cost) if ask_now else -cost, 3)
        b["r"]["заявка_тогда_сейчас"] = [b["bid"], bid_now]
    L["scan"] = {"t": int(now), "lis": len(lis), "bids": len(bids), "asks": len(asks), "sp": len(sp),
                 "cands": len(cands), "new": new, "cap_units": sum(c["cap"] for c in cands),
                 "cap_usd": round(sum(c["cap"] * c["lis"] for c in cands), 2),
                 "cap_profit": round(sum(c["cap"] * c["lis"] * c["edge"] / 100 for c in cands), 2),
                 "top": cands[:10]}
    d = lt(now, "%Y-%m-%d")
    if int(lt(now, "%H")) >= 9 and L.get("day") != d:
        L["day"] = d
        say(summary())
    save()

def summary():
    sc = L.get("scan") or {}
    done = [b for b in L.get("buys") or [] if "7д" in b["r"]]
    head = (f"сейчас {sc.get('cands', 0)} предметов с разницей ≥{MIN_EDGE * 100:g}% (после комиссий: продажа 5%, "
            f"пополнение {FEE_IN * 100:g}%, вывод {FEE_OUT * 100:g}%): можно взять {sc.get('cap_units', 0)} шт. на "
            f"${sc.get('cap_usd', 0):.0f} → ${sc.get('cap_profit', 0):.0f} | бумажных покупок {len(L.get('buys') or [])}")
    if not done:
        return head + ", итогов через 7 дней пока нет"
    r = [b["r"]["7д"] for b in done]; ra = [b["r"]["по_мин_цене"] for b in done]
    inv = sum(b["lis"] * (1 + FEE_IN) for b in done)
    gone = sum(1 for b in done if not b["r"]["заявка_тогда_сейчас"][1])
    lower = sum(1 for b in done if 0 < b["r"]["заявка_тогда_сейчас"][1] < b["bid"] * 0.99)
    return (head + f" | через 7 дней закрыто {len(done)}: продажа в заявку {sum(r):+.2f}$ на ${inv:.0f} "
            f"({sum(r) / inv * 100:+.1f}%), в плюсе {sum(1 for x in r if x > 0) * 100 // len(r)}%; по минимальной цене "
            f"{sum(ra):+.2f}$ | заявка пропала {gone}, стала ниже {lower} из {len(done)}")

def brief():
    sc = L.get("scan") or {}
    out = [f"🔫 {VERSION} — бумага, {lt(None)}. Последний скан {lt(sc.get('t'))}: Lis {sc.get('lis', 0)}, "
           f"заявок MC {sc.get('bids', 0)}, ходовых по Skinport {sc.get('sp', 0)}", summary()]
    for c in sc.get("top") or []:
        out.append(f"  {c['edge']:+.1f}% {c['name']} — Lis ${c['lis']:.2f} → заявка ${c['bid']:.2f} "
                   f"(заявок {c['bid_n']}, лотов {c['lis_n']}, продаж/нед {c['sales7']})")
    return "\n".join(out)

def loop():
    time.sleep(240)
    while not L.get("loaded"):
        try:
            load()
        except Exception:
            log.exception("xskins load")
            time.sleep(60)
    first = not L.get("scan")
    if first and int(lt(None, "%H")) >= 9:
        L["day"] = lt(None, "%Y-%m-%d")                # первый запуск: одно сообщение «запущен», без второго итога
    while True:
        try:
            with LOCK:
                step(time.time())
                if first:
                    first = False
                    say(f"запущен ({VERSION}), ничего не покупает. {summary()}")
        except Exception:
            log.exception("xskins")
        time.sleep(EVERY)

def ok():
    return mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN

@mm.app.route("/skins")
def skins_page():
    if not ok():
        return "forbidden", 403
    with LOCK:
        return jsonify({"версия": VERSION, "итог": summary(), "скан": L.get("scan"),
                        "настройки": {"мин_разница_%": MIN_EDGE * 100, "комиссия_MC_%": FEE_MC * 100,
                                      "пополнение_%": FEE_IN * 100, "вывод_%": FEE_OUT * 100, "держим_дней": HOLD_D},
                        "покупки": (L.get("buys") or [])[-200:]})

@mm.app.route("/skins/brief")
def skins_brief():
    if not ok():
        return "forbidden", 403
    with LOCK:
        txt = brief()
    if request.args.get("tg") == "1":
        mm.tg(txt)
    return txt, 200, {"Content-Type": "text/plain; charset=utf-8"}

if ON:
    threading.Thread(target=loop, daemon=True).start()
