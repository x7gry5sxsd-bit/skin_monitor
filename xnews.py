# xnews.py — БУМАГА «скальп на новостях Binance». Ничего не торгует.
#   Проверка на тиках Gate (05.11.2026): когда Binance объявляет листинг на споте монеты, которая уже есть на фьючерсах
#   Gate, цена на Gate за 1–2 секунды прыгает на +4…+40%. Вход через 1 с после публикации давал в среднем +13…+21%,
#   через 2 с — уже +3…+10% со средней около нуля. Всё решает скорость. Этот модуль меряет, успеваем ли мы.
#   Раз в XNEWS_POLL (1 с) читает список «New Cryptocurrency Listing» Binance. Новое объявление → замер «увидел через N с
#   после публикации» (для любого объявления). Листинг на споте (или новый фьючерс) монеты с фьючерсом на Gate →
#   бумажная покупка на XNEWS_USD ($100) по лучшей цене продажи (ask) в момент, когда увидели, и продажа по лучшей цене
#   покупки (bid) через 30 с, 1, 5 и 30 мин; комиссия 0,1% за круг учтена. Сообщение в Telegram — при объявлении и итог.
#   Страницы (?t=STATS_TOKEN): /news — всё (JSON), /news/brief — коротко (&tg=1 — в Telegram). XNEWS_ON=0 — выключить.
import os, time, json, re, threading, logging, statistics
import requests
from flask import jsonify, request
import mm_scanner as mm
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

VERSION = "xnews v1 · 05.11"
ON = env("XNEWS_ON", "1") == "1"
POLL = max(0.5, float(env("XNEWS_POLL", "1")))
USD = float(env("XNEWS_USD", "100"))
FEE = 0.001                                             # тейкер 0,05% на вход и на выход
CHECKS = (30, 60, 300, 1800)                            # через сколько секунд после входа считаем выход
CMS = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
GATE = "https://api.gateio.ws/api/v4/futures/usdt"
log = logging.getLogger("xnews")
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
L = {}
LOCK = threading.RLock()
C = {"contracts": set(), "ct": 0}

def lt(t=None, f="%d.%m %H:%M:%S"):
    return time.strftime(f, time.gmtime((time.time() if t is None else t) + 8 * 3600))

def say(text):
    mm.tg("📰 Новости [бумага]: " + text)

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'xnews_v1'", fetch=True)
    L.update(json.loads(r[0][0]) if r else {})
    for k, v in (("seen", []), ("events", []), ("lat", []), ("polls", 0), ("errors", 0), ("last_ok", 0), ("last_err", "")):
        L.setdefault(k, v)
    L["loaded"] = 1

def save():
    if not L.get("loaded"):
        return
    try:
        xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('xnews_v1', %s)
                    ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(L),))
    except Exception:
        log.exception("xnews save")

# ---------- данные ----------
def articles():
    """Последние объявления каталога «New Cryptocurrency Listing» (id 48): [(code, title, время публикации, с)]."""
    r = S.get(CMS, params={"type": 1, "catalogId": 48, "pageNo": 1, "pageSize": 10}, timeout=5)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    arts = r.json()["data"]["catalogs"][0]["articles"]
    return [(str(a.get("code") or a.get("id")), a.get("title") or "", float(a.get("releaseDate") or 0) / 1000) for a in arts]

def contracts(now):
    if now - C["ct"] > 1800 or not C["contracts"]:
        try:
            C["contracts"] = {x["name"].replace("_USDT", "") for x in S.get(f"{GATE}/contracts", timeout=20).json()
                              if not x.get("in_delisting")}
            C["ct"] = now
        except Exception:
            log.exception("xnews contracts")
    return C["contracts"]

def kind(title):
    tl = title.lower()
    if any(w in tl for w in ("delist", "remove", "postpone", "tradfi", "stock", "equity", "index", "bstocks", "tokenized")):
        return None
    if "futures will launch" in tl:
        return "фьючерс"
    if "will list" in tl and "futures" not in tl:
        return "спот"
    return None

def coins(title):
    s = title.upper().replace("USDⓈ", "")
    out = set(re.findall(r"\(([A-Z0-9]{2,15})\)", s)) | set(re.findall(r"\b(?:1000)?([A-Z0-9]{2,15})USDT\b", s))
    return sorted(out - {"USDT", "USDC", "USD", "BTC", "ETH", "BNB"})

def top(coin):
    d = S.get(f"{GATE}/order_book", params={"contract": coin + "_USDT", "limit": 1}, timeout=5).json()
    return float(d["asks"][0]["p"]), float(d["bids"][0]["p"])

def price_before(coin, t):
    d = S.get(f"{GATE}/trades", params={"contract": coin + "_USDT", "from": int(t) - 300, "to": int(t), "limit": 1}, timeout=5).json()
    return float(d[0]["price"]) if d else 0.0

# ---------- событие ----------
def follow(ev):
    """Бумажная сделка: вход по ask в момент, когда увидели; выход по bid через 30 с, 1, 5, 30 мин."""
    try:
        ask, bid = top(ev["coin"])
        ev["ask"] = ask
        try:
            ev["p0"] = price_before(ev["coin"], ev["t_pub"])
        except Exception:
            ev["p0"] = 0.0
        ev["moved"] = round((ask / ev["p0"] - 1) * 100, 2) if ev["p0"] else None
        say(f"Binance {ev['kind']}: {ev['coin']} — «{ev['title'][:70]}». Увидел через {ev['lag']:.1f} с после публикации. "
            f"Цена Gate до объявления {ev['p0']:g}, вход (ask) {ask:g}"
            + (f" — уже {ev['moved']:+.1f}%" if ev["moved"] is not None else "") + f". Бумажная покупка ${USD:.0f}.")
        t0 = time.time()
        for s in CHECKS:
            time.sleep(max(0, t0 + s - time.time()))
            try:
                b = top(ev["coin"])[1]
                ev["r"][str(s)] = round((b / ask - 1 - FEE) * 100, 2)
            except Exception:
                log.exception("xnews check")
        r = ev["r"]
        say(f"итог {ev['coin']}: продажа через 30 с {r.get('30', 0):+.1f}%, 1 мин {r.get('60', 0):+.1f}%, 5 мин {r.get('300', 0):+.1f}%, "
            f"30 мин {r.get('1800', 0):+.1f}% после комиссии (на ${USD:.0f} за 5 мин: {r.get('300', 0) * USD / 100:+.2f}$)")
    except Exception as e:
        ev["err"] = type(e).__name__
        log.exception("xnews follow")
    with LOCK:
        save()

def step(now):
    arts = articles()
    L["polls"] += 1; L["last_ok"] = int(now)
    first = not L["seen"]
    for code, title, t_pub in sorted(arts, key=lambda a: a[2]):     # от старых к новым
        if code in L["seen"]:
            continue
        L["seen"].append(code)
        if first:
            continue                                     # при первом запуске старое не считаем
        lag = now - t_pub
        L["lat"].append({"t": int(now), "lag": round(lag, 2), "title": title[:80]})
        k = kind(title)
        if not k:
            continue
        have = contracts(now)
        for c in coins(title):
            if c not in have:
                continue
            ev = {"t": int(now), "t_pub": t_pub, "lag": round(lag, 2), "kind": k, "coin": c, "title": title[:100], "r": {}}
            L["events"].append(ev)
            threading.Thread(target=follow, args=(ev,), daemon=True).start()
    L["seen"] = L["seen"][-300:]; L["lat"] = L["lat"][-300:]; L["events"] = L["events"][-200:]

def summary():
    lat = [x["lag"] for x in L.get("lat") or []]
    head = (f"опросов {L.get('polls', 0)}, ошибок {L.get('errors', 0)}, последний успешный {lt(L.get('last_ok')) if L.get('last_ok') else '—'}"
            + (f" | новых объявлений {len(lat)}, видим через: медиана {statistics.median(lat):.1f} с, лучшее {min(lat):.1f} с" if lat else
               " | новых объявлений пока не было"))
    done = [e for e in L.get("events") or [] if "1800" in e.get("r", {})]
    if not done:
        return head + f" | сделок по листингам {len(L.get('events') or [])}, итогов пока нет"
    m5 = [e["r"]["300"] for e in done]
    return head + (f" | сделок {len(done)}: через 5 мин в среднем {sum(m5) / len(m5):+.1f}%, в плюсе {sum(1 for x in m5 if x > 0)} из {len(m5)}")

def brief():
    out = [f"📰 {VERSION} — бумага, {lt()}. Опрос Binance раз в {POLL:g} с, сделка ${USD:.0f}", summary()]
    for e in (L.get("events") or [])[-10:]:
        r = e.get("r", {})
        out.append(f"  {lt(e['t'])} {e['kind']} {e['coin']}: увидел через {e['lag']:.1f} с, вход "
                   + (f"{e['moved']:+.1f}% от цены до" if e.get("moved") is not None else "—")
                   + f" | 30с {r.get('30', '—')} 1м {r.get('60', '—')} 5м {r.get('300', '—')} 30м {r.get('1800', '—')}")
    if L.get("last_err"):
        out.append(f"последняя ошибка: {L['last_err']}")
    return "\n".join(out)

def loop():
    time.sleep(60)
    while not L.get("loaded"):
        try:
            load()
        except Exception:
            log.exception("xnews load")
            time.sleep(60)
    say(f"запущен ({VERSION}), ничего не торгует. Слежу за листингами Binance раз в {POLL:g} с.")
    pause, warned, last_save = POLL, 0, 0
    while True:
        now = time.time()
        try:
            with LOCK:
                step(now)
            pause = POLL
        except Exception as e:
            with LOCK:
                L["errors"] = L.get("errors", 0) + 1; L["last_err"] = f"{lt(now)} {type(e).__name__}: {str(e)[:80]}"
            pause = min(30, pause * 2)
            if now - L.get("last_ok", now) > 900 and now - warned > 3600:
                warned = now
                say(f"⚠️ не могу прочитать объявления Binance больше 15 мин ({L['last_err']})")
        if now - last_save > 600:
            with LOCK:
                save()
            last_save = now
        time.sleep(max(0.05, pause - (time.time() - now)))

def ok():
    return mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN

@mm.app.route("/news")
def news_page():
    if not ok():
        return "forbidden", 403
    with LOCK:
        return jsonify({"версия": VERSION, "итог": summary(), "сделки": (L.get("events") or [])[-50:],
                        "задержки": (L.get("lat") or [])[-50:], "настройки": {"опрос_с": POLL, "сделка_$": USD, "комиссия_%": FEE * 100}})

@mm.app.route("/news/brief")
def news_brief():
    if not ok():
        return "forbidden", 403
    with LOCK:
        txt = brief()
    if request.args.get("tg") == "1":
        mm.tg(txt)
    return txt, 200, {"Content-Type": "text/plain; charset=utf-8"}

if ON:
    threading.Thread(target=loop, daemon=True).start()
