# xkwallet.py — БУМАГА «ввод/вывод на Bithumb». Ничего не торгует.
#   Идея: когда корейская биржа закрывает ввод монеты, арбитражёры не могут её завезти — цена в Корее надувается
#   (переплата растёт). Когда ввод открывают, монету везут и продают — переплата сдувается, а цена на Gate может
#   пойти в любую сторону (арбитражёры покупают на мировых биржах, чтобы завезти). Направление заранее не знаем,
#   поэтому бумага пишет оба: шорт и лонг на Gate.
#   Раз в XKW_EVERY_MIN (5) мин: статус ввода/вывода Bithumb по всем монетам (открытый API), цены Bithumb в вонах,
#   курс USDT/KRW на Bithumb, цены фьючерсов Gate. Событие — у монеты с фьючерсом на Gate (оборот ≥ XKW_MIN_VOL)
#   закрылся или открылся ввод (или вывод). Пишем переплату и цену Gate, итог через 1, 4, 24 и 72 ч:
#   шорт на Gate (комиссия и проскальзывание 0,4% учтены), изменение переплаты.
#   Итог в Telegram раз в день в 9:00 (Иркутск). Страницы (?t=STATS_TOKEN): /kwallet, /kwallet/brief. XKW_ON=0 — выкл.
import os, time, json, threading, logging, statistics
import requests
from flask import jsonify, request
import mm_scanner as mm
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

VERSION = "xkwallet v1 · 07.11"
ON = env("XKW_ON", "1") == "1"
EVERY = max(1.0, float(env("XKW_EVERY_MIN", "5"))) * 60
MIN_VOL = float(env("XKW_MIN_VOL", "300000"))
COST = 0.004
CHECKS = (1, 4, 24, 72)                                  # часы после события
BH = "https://api.bithumb.com/public"
GATE = "https://api.gateio.ws/api/v4/futures/usdt"
log = logging.getLogger("xkwallet")
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0"})
L = {}
LOCK = threading.RLock()

def lt(t=None, f="%d.%m %H:%M"):
    return time.strftime(f, time.gmtime((time.time() if t is None else t) + 8 * 3600))

def say(text):
    mm.tg("🧪🇰🇷 Ввод/вывод Bithumb [бумага]: " + text)

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'xkw_v1'", fetch=True)
    L.update(json.loads(r[0][0]) if r else {})
    for k, v in (("st", {}), ("since", {}), ("events", []), ("day", ""), ("polls", 0), ("last_ok", 0)):
        L.setdefault(k, v)
    L["loaded"] = 1

def save():
    if not L.get("loaded"):
        return
    try:
        xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('xkw_v1', %s)
                    ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(L),))
    except Exception:
        log.exception("xkwallet save")

# ---------- данные ----------
def status():
    """{монета: [ввод 1/0, вывод 1/0]} по всем монетам Bithumb."""
    d = S.get(f"{BH}/assetsstatus/ALL", timeout=20).json()
    if str(d.get("status")) != "0000":
        raise RuntimeError(f"Bithumb assetsstatus {d.get('status')}")
    return {c: [int(v.get("deposit_status") or 0), int(v.get("withdrawal_status") or 0)] for c, v in d["data"].items()}

def krw():
    """Цены Bithumb в вонах {монета: цена} и курс USDT/KRW."""
    d = S.get(f"{BH}/ticker/ALL_KRW", timeout=20).json()["data"]
    px = {c: float(v["closing_price"]) for c, v in d.items() if isinstance(v, dict) and float(v.get("closing_price") or 0) > 0}
    return px, px.get("USDT", 0.0)

def gate():
    """{монета: (последняя цена, оборот за сутки $)} по фьючерсам Gate."""
    out = {}
    for x in S.get(f"{GATE}/tickers", timeout=30).json():
        c = x["contract"].replace("_USDT", "")
        out[c] = (float(x.get("last") or 0), float(x.get("volume_24h_quote") or 0))
    return out

def prem(c, px, usdt, g):
    if c in px and usdt > 0 and g.get(c, (0, 0))[0] > 0:
        return round((px[c] / usdt / g[c][0] - 1) * 100, 2)
    return None

# ---------- шаг ----------
def step(now):
    st = status(); px, usdt = krw(); g = gate()
    L["polls"] += 1; L["last_ok"] = int(now)
    first = not L["st"]
    for c, (dep, wd) in st.items():
        old = L["st"].get(c)
        L["st"][c] = [dep, wd]
        if first or old is None or old == [dep, wd]:
            continue
        for i, name in ((0, "ввод"), (1, "вывод")):
            if old[i] == [dep, wd][i]:
                continue
            key = f"{c}:{i}"
            opened = [dep, wd][i] == 1
            closed_h = round((now - L["since"].get(key, now)) / 3600, 1) if opened and key in L["since"] else None
            if opened:
                L["since"].pop(key, None)
            else:
                L["since"][key] = int(now)
            if c not in g or g[c][0] <= 0 or g[c][1] < MIN_VOL:
                continue                                   # нет фьючерса на Gate или мало оборота
            ev = {"t": int(now), "coin": c, "what": f"{name} {'открыт' if opened else 'закрыт'}", "closed_h": closed_h,
                  "p0": g[c][0], "prem0": prem(c, px, usdt, g), "vol": round(g[c][1]), "r": {}, "prem": {}}
            L["events"].append(ev)
            say(f"{c}: {ev['what']}" + (f" (был закрыт {closed_h} ч)" if closed_h is not None else "")
                + f". Переплата в Корее {ev['prem0'] if ev['prem0'] is not None else '—'}%, цена Gate {ev['p0']:g}.")
    for ev in L["events"]:                                 # итоги через 1/4/24/72 ч
        for h in CHECKS:
            k = str(h)
            if k in ev["r"] or now - ev["t"] < h * 3600:
                continue
            p = g.get(ev["coin"], (0, 0))[0]
            if p > 0:
                ev["r"][k] = round((1 - p / ev["p0"] - COST) * 100, 2)      # шорт на Gate, %
                ev["prem"][k] = prem(ev["coin"], px, usdt, g)
    L["events"] = L["events"][-500:]
    d = lt(now, "%Y-%m-%d")
    if int(lt(now, "%H")) >= 9 and L.get("day") != d:
        L["day"] = d
        say(summary())
    save()

def group(evs, h):
    r = [e["r"][str(h)] for e in evs if str(h) in e["r"]]
    if not r:
        return "—"
    return f"{len(r)} сд, шорт {sum(r) / len(r):+.2f}% (лонг {-sum(r) / len(r) - 2 * COST * 100:+.2f}%), шорт в плюсе {sum(1 for x in r if x > 0) * 100 // len(r)}%"

def summary():
    ev = L.get("events") or []
    closed = [e for e in L.get("st", {}).values() if e[0] == 0]
    head = f"следим за {len(L.get('st') or {})} монетами Bithumb, ввод закрыт у {len(closed)} | событий у монет с фьючерсом на Gate: {len(ev)}"
    if not ev:
        return head + ", итогов пока нет"
    out = [head]
    for what in ("ввод закрыт", "ввод открыт", "вывод закрыт", "вывод открыт"):
        sub = [e for e in ev if e["what"] == what]
        if sub:
            out.append(f"  {what} ({len(sub)}): 24 ч — {group(sub, 24)}; 72 ч — {group(sub, 72)}")
    hi = [e for e in ev if e["what"] == "ввод открыт" and (e.get("prem0") or 0) >= 3]
    if hi:
        out.append(f"  ввод открыт при переплате ≥3% ({len(hi)}): 24 ч — {group(hi, 24)}")
    return "\n".join(out)

def brief():
    out = [f"🇰🇷 {VERSION} — бумага, {lt()}. Опросов {L.get('polls', 0)}, последний {lt(L.get('last_ok')) if L.get('last_ok') else '—'}", summary()]
    for e in (L.get("events") or [])[-12:]:
        r = e.get("r", {})
        out.append(f"  {lt(e['t'])} {e['coin']} {e['what']}" + (f" (закрыт был {e['closed_h']} ч)" if e.get("closed_h") is not None else "")
                   + f" | переплата {e.get('prem0')}% → 24ч {e.get('prem', {}).get('24', '—')}% | шорт: 1ч {r.get('1', '—')} 4ч {r.get('4', '—')} "
                   f"24ч {r.get('24', '—')} 72ч {r.get('72', '—')}")
    return "\n".join(out)

def loop():
    time.sleep(120)
    while not L.get("loaded"):
        try:
            load()
        except Exception:
            log.exception("xkwallet load")
            time.sleep(60)
    if not L.get("st"):
        say(f"запущен ({VERSION}), ничего не торгует. Слежу за вводом и выводом на Bithumb раз в {EVERY / 60:g} мин.")
        if int(lt(None, "%H")) >= 9:
            L["day"] = lt(None, "%Y-%m-%d")                # в день запуска — одно сообщение, без второго итога
    while True:
        try:
            with LOCK:
                step(time.time())
        except Exception:
            log.exception("xkwallet")
        time.sleep(EVERY)

def ok():
    return mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN

@mm.app.route("/kwallet")
def kwallet_page():
    if not ok():
        return "forbidden", 403
    with LOCK:
        closed = sorted(c for c, v in (L.get("st") or {}).items() if v[0] == 0)
        return jsonify({"версия": VERSION, "итог": summary(), "события": (L.get("events") or [])[-100:],
                        "ввод_закрыт_сейчас": closed})

@mm.app.route("/kwallet/brief")
def kwallet_brief():
    if not ok():
        return "forbidden", 403
    with LOCK:
        txt = brief()
    if request.args.get("tg") == "1":
        mm.tg(txt)
    return txt, 200, {"Content-Type": "text/plain; charset=utf-8"}

if ON:
    threading.Thread(target=loop, daemon=True).start()
