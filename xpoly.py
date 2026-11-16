# xpoly.py — БУМАГА + АЛЕРТЫ по рынкам Polymarket «What price will <монета> hit on <дата>?» (касание цены за день).
#   Ничего не покупает и не продаёт: читает только открытые данные Polymarket, Binance (или Gate) и Deribit.
#   ИДЕЯ. В дневных рынках «коснётся ли монета цены K сегодня» толпа переплачивает за «ДА» — за лотерейный билет «вдруг
#   долетит». Мы считаем честную вероятность касания: случайное блуждание цены с волатильностью из часовых цен (для BTC и
#   ETH — пополам с подразумеваемой волатильностью Deribit DVOL): P(коснётся) = 2 × P(цена в конце дня за K). Если цена
#   «НЕТ» в стакане (с комиссией Polymarket) дешевле нашей вероятности «НЕТ» хотя бы на XPM_EDGE (0,12) — алерт и
#   бумажная покупка «НЕТ» на XPM_USD ($20) по настоящему стакану, держим до расчёта рынка.
#   ПРАВИЛО (записано до запуска): монеты ETH, SOL, XRP (XPM_COINS; по BTC рынок точный — края нет); до закрытия от
#   XPM_H_MIN (2) до XPM_H_MAX (20) часов; цена «НЕТ» в стакане 0,30–0,95; если цена уже коснулась K сегодня — не берём;
#   один вход на рынок; не больше XPM_DAY_MAX (10) входов в сутки.
#   ПРОВЕРКА НА ИСТОРИИ (Polymarket 18.08–16.11.2026, рынки ETH/SOL/XRP, только «живые» цены — без пустых стаканов с
#   ценой 0,50; спред +0,04 и комиссия Polymarket 0,07·p·(1−p)): 140 сделок за 49 дней, +27% на вложенный $1, «НЕТ»
#   выиграл в 89% случаев, первая половина +28%, вторая +25%. Но история цен Polymarket — это середина стакана, а не цена
#   покупки, поэтому бумага здесь считает по НАСТОЯЩЕМУ стакану: только она покажет, есть ли этот перевес на деле.
#   Комиссия посредника (если торговать через другой терминал) здесь не учтена.
#   /poly?t=... — таблица, /poly/brief?t=... — коротко (&tg=1 — в Telegram). XPM_ON=0 — выключить, XPM_TG=0 — без алертов.
import os, time, json, math, threading, logging, datetime as dt
from zoneinfo import ZoneInfo
import requests
from flask import jsonify, request
import mm_scanner as mm
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

VERSION = "xpoly v1 · 16.11"
ON = env("XPM_ON", "1") == "1"
TG = env("XPM_TG", "1") == "1"
COINS = [c.strip().upper() for c in env("XPM_COINS", "ETH,SOL,XRP").split(",") if c.strip()]
WATCH = ["BTC", "ETH", "SOL", "XRP"]                    # считаем и показываем все, алерты — только по COINS
EDGE = float(env("XPM_EDGE", "0.12"))
USD = float(env("XPM_USD", "20"))
H_MIN = float(env("XPM_H_MIN", "2"))
H_MAX = float(env("XPM_H_MAX", "20"))
NO_MIN, NO_MAX = float(env("XPM_NO_MIN", "0.30")), float(env("XPM_NO_MAX", "0.95"))
DAY_MAX = int(env("XPM_DAY_MAX", "10"))
FEE_RATE = float(env("XPM_FEE", "0.07"))              # комиссия тейкера Polymarket: rate · p · (1 − p) за долю
STEP = 300
NAME = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "xrp"}
MON = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
ET = ZoneInfo("America/New_York")
GAMMA, CLOB = "https://gamma-api.polymarket.com", "https://clob.polymarket.com"
log = logging.getLogger("xpoly")
S = {"loaded": 0, "pos": {}, "done": [], "log": [], "seen": {}}
M = {"err": "", "last": 0, "cand": [], "src": "", "vol": {}}
LOCK = threading.Lock()

def lt(t=None, f="%d.%m %H:%M"):
    return time.strftime(f, time.gmtime((t if t is not None else time.time()) + 8 * 3600))

def get(url, **params):
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def fnum(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d

# ---------- цены монет ----------
def klines(coin, iv, start_ms=None, limit=1000):
    """[(t_сек, o, h, l, c)] — сначала Binance (по нему рассчитывается Polymarket), если недоступен — Gate."""
    try:
        p = {"symbol": coin + "USDT", "interval": iv, "limit": limit}
        if start_ms: p["startTime"] = int(start_ms)
        d = get("https://api.binance.com/api/v3/klines", **p)
        M["src"] = "Binance"
        return [(int(x[0]) // 1000, float(x[1]), float(x[2]), float(x[3]), float(x[4])) for x in d]
    except Exception:
        p = {"currency_pair": coin + "_USDT", "interval": iv, "limit": min(limit, 1000)}
        if start_ms:
            a = int(start_ms // 1000)
            p = {"currency_pair": coin + "_USDT", "interval": iv, "from": a, "to": min(int(time.time()), a + 999 * 60)}
        d = get("https://api.gateio.ws/api/v4/spot/candlesticks", **p)
        M["src"] = "Gate"
        return sorted((int(x[0]), float(x[5]), float(x[3]), float(x[4]), float(x[2])) for x in d)

def day_path(coin, start):
    """Цена сейчас и максимум/минимум с начала дня (минутные свечи)."""
    rows, t = [], start
    while True:
        part = klines(coin, "1m", start_ms=t * 1000)
        rows += part
        if len(part) < 1000 or not part:
            break
        t = part[-1][0] + 60
    if not rows:
        return None
    return rows[-1][4], max(r[2] for r in rows), min(r[3] for r in rows)

def vol(coin):
    """Годовая волатильность: EWMA часовых доходностей (полураспад 72 ч); для BTC/ETH — пополам с Deribit DVOL."""
    rows = klines(coin, "1h", limit=400)
    lr = [math.log(rows[i][4] / rows[i - 1][4]) for i in range(1, len(rows)) if rows[i - 1][4] > 0]
    if len(lr) < 50:
        return None
    a = 1 - 0.5 ** (1 / 72)
    v = lr[0] ** 2
    for x in lr[1:]:
        v = (1 - a) * v + a * x * x
    rv = math.sqrt(v * 24 * 365)
    sig = rv
    if coin in ("BTC", "ETH"):
        try:
            now = int(time.time() * 1000)
            d = get("https://www.deribit.com/api/v2/public/get_volatility_index_data", currency=coin,
                    start_timestamp=now - 3 * 3600 * 1000, end_timestamp=now, resolution="3600")["result"]["data"]
            if d:
                sig = 0.5 * rv + 0.5 * float(d[-1][4]) / 100
        except Exception:
            pass
    M["vol"][coin] = round(sig, 3)
    return sig

def ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def p_touch(S0, K, up, sig, hours):
    """Вероятность, что цена коснётся K за оставшиеся hours (блуждание без сноса): 2 × P(в конце за K)."""
    if hours <= 0 or sig <= 0:
        return 0.0
    s = sig * math.sqrt(hours / (24 * 365))
    z = math.log(K / S0) / s if up else math.log(S0 / K) / s
    return min(1.0, 2 * (1 - ncdf(z)))

def fee(p):
    return FEE_RATE * p * (1 - p)

# ---------- Polymarket ----------
def strike(g):
    g = (g or "").replace(",", "").replace("$", "").strip()
    up = not g.startswith("↓")
    g = g.lstrip("↑↓ ").strip()
    try:
        return float(g), up
    except ValueError:
        return None, up

def event(coin, day):
    slug = f"what-price-will-{NAME[coin]}-hit-on-{MON[day.month - 1]}-{day.day}-{day.year}"
    d = get(GAMMA + "/events", slug=slug)
    return (d[0] if d else None), slug

def book_fill(token, usd):
    """Покупка на usd по asks → (долей, средняя цена, лучшая цена) или None."""
    b = get(CLOB + "/book", token_id=token)
    asks = sorted((fnum(x.get("price")), fnum(x.get("size"))) for x in b.get("asks") or [] if fnum(x.get("price")) > 0)
    got = spent = 0.0
    for p, q in asks:
        v = min(usd - spent, p * q)
        spent += v
        got += v / p
        if spent >= usd - 1e-9:
            return got, spent / got, asks[0][0]
    return None

def note(text, now):
    S["log"] = (S["log"] + [{"t": int(now), "x": text}])[-300:]

def scan(now):
    day = dt.datetime.fromtimestamp(now, ET).date()
    start = int(dt.datetime(day.year, day.month, day.day, tzinfo=ET).timestamp())
    cand = []
    today = sum(1 for p in list(S["pos"].values()) + S["done"] if dt.datetime.fromtimestamp(p["t"], ET).date() == day)
    for coin in WATCH:
        try:
            ev, slug = event(coin, day)
            if not ev or ev.get("closed"):
                continue
            path = day_path(coin, start)
            sig = vol(coin)
            if not path or not sig:
                continue
            S0, hi, lo = path
            for m in ev.get("markets") or []:
                K, up = strike(m.get("groupItemTitle"))
                if not K or m.get("closed") or not m.get("acceptingOrders", True):
                    continue
                end = int(dt.datetime.fromisoformat((m.get("endDate") or ev["endDate"]).replace("Z", "+00:00")).timestamp())
                hours = (end - now) / 3600
                if (up and hi >= K) or (not up and lo <= K):
                    continue                                    # уже коснулась — исход решён
                q = p_touch(S0, K, up, sig, hours)
                toks = json.loads(m.get("clobTokenIds") or "[]")
                if len(toks) != 2:
                    continue
                f = book_fill(toks[1], USD)                     # покупаем «НЕТ»
                if not f:
                    continue
                shares, avg, best = f
                eff = avg + fee(avg)
                edge = (1 - q) - eff
                row = {"coin": coin, "K": K, "up": up, "q_yes": round(q, 4), "no_ask": round(avg, 4), "eff": round(eff, 4),
                       "edge": round(edge, 4), "h": round(hours, 1), "S": S0, "slug": slug, "mid": m.get("id"), "q": m.get("question")}
                cand.append(row)
                key = f"{m.get('id')}"
                ok = (coin in COINS and H_MIN <= hours <= H_MAX and NO_MIN <= avg <= NO_MAX and edge >= EDGE
                      and key not in S["pos"] and key not in S["seen"] and today < DAY_MAX)
                if ok:
                    open_pos(key, row, shares, avg, eff, end, toks[1], now)
                    today += 1
        except Exception as e:
            log.warning("xpoly %s: %s", coin, e)
            M["err"] = f"{coin}: {str(e)[:100]}"
    M["cand"] = sorted(cand, key=lambda r: -r["edge"])[:25]

def open_pos(key, row, shares, avg, eff, end, tok, now):
    p = dict(row, t=int(now), end=end, shares=shares, cost=round(shares * eff, 4), avg=avg, tok=tok)
    with LOCK:
        S["pos"][key] = p
        S["seen"][key] = int(now)
    word = "поднимется до" if row["up"] else "опустится до"
    txt = (f"🎯 Polymarket (бумага): купить «НЕТ» — «{row['coin']} {word} {row['K']:g} сегодня?» по цене до {avg:.3f}. "
           f"Наша вероятность «НЕТ» {1 - row['q_yes']:.0%}, цена с комиссией {eff:.3f}, перевес +{row['edge']:.2f}. "
           f"Сейчас {row['S']:.6g}, до закрытия {row['h']:.1f} ч. https://polymarket.com/event/{row['slug']}")
    note(f"ВХОД {row['coin']} {'↑' if row['up'] else '↓'}{row['K']:g}: НЕТ по {avg:.3f}, перевес +{row['edge']:.2f}", now)
    if TG:
        mm.tg(txt)

def settle(now):
    for key, p in list(S["pos"].items()):
        if now < p["end"] + 600:
            continue
        try:
            ev = get(GAMMA + "/events", slug=p["slug"])
            m = next((x for x in (ev[0].get("markets") or []) if str(x.get("id")) == str(p["mid"])), None) if ev else None
            if not m or not m.get("closed"):
                continue
            op = json.loads(m.get("outcomePrices") or "[]")
            if len(op) != 2 or op[1] not in ("0", "1"):
                continue
            win = op[1] == "1"
            pnl = (p["shares"] if win else 0.0) - p["cost"]
            d = dict(p, t1=int(now), win=win, pnl=round(pnl, 4))
            with LOCK:
                S["pos"].pop(key, None)
                S["done"] = (S["done"] + [d])[-2000:]
            note(f"{'✅' if win else '❌'} {p['coin']} {'↑' if p['up'] else '↓'}{p['K']:g}: {pnl:+.2f}$", now)
            if TG:
                mm.tg(f"🎯 Polymarket (бумага): {p['coin']} {'↑' if p['up'] else '↓'}{p['K']:g} — «НЕТ» "
                      f"{'выиграл' if win else 'проиграл'}, {pnl:+.2f}$ на ${p['cost']:.2f}")
        except Exception as e:
            log.warning("xpoly settle %s: %s", key, e)
    old = int(now) - 3 * 86400
    S["seen"] = {k: v for k, v in S["seen"].items() if v > old}

def step(now):
    settle(now)
    scan(now)
    M["last"] = now
    save()

# ---------- отчёт ----------
def totals():
    with LOCK:
        pos = json.loads(json.dumps(S["pos"]))
        done = list(S["done"])
    n = len(done)
    win = sum(1 for d in done if d["win"])
    pnl = sum(d["pnl"] for d in done)
    cost = sum(d["cost"] for d in done)
    return {"pos": pos, "done": done, "n": n, "win": win, "pnl": pnl, "cost": cost, "open_cost": sum(p["cost"] for p in pos.values())}

def text():
    T = totals()
    L = [f"🎯 {VERSION} — БУМАГА: Polymarket «коснётся ли цена K сегодня», покупаем «НЕТ». {lt()}",
         f"Монеты {', '.join(COINS)} · ${USD:.0f} на сигнал · перевес ≥{EDGE:.2f} · {H_MIN:g}–{H_MAX:g} ч до закрытия · цены монет: {M['src'] or '—'}",
         f"Закрыто {T['n']}: «НЕТ» выиграл {T['win']} ({(T['win'] / T['n'] * 100) if T['n'] else 0:.0f}%), итог {T['pnl']:+.2f}$ на ${T['cost']:.0f} "
         f"({(T['pnl'] / T['cost'] * 100) if T['cost'] else 0:+.1f}%) · открыто {len(T['pos'])} на ${T['open_cost']:.0f}",
         "Волатильность: " + (", ".join(f"{k} {v:.0%}" for k, v in M["vol"].items()) or "—")]
    for p in sorted(T["pos"].values(), key=lambda x: x["end"]):
        L.append(f"  открыт {p['coin']} {'↑' if p['up'] else '↓'}{p['K']:g}: НЕТ по {p['avg']:.3f}, перевес +{p['edge']:.2f}, "
                 f"расчёт {lt(p['end'])}")
    for d in T["done"][-6:]:
        L.append(f"  {'✅' if d['win'] else '❌'} {d['coin']} {'↑' if d['up'] else '↓'}{d['K']:g} {lt(d['end'], '%d.%m')}: {d['pnl']:+.2f}$")
    L.append("Лучшие расхождения сейчас (наша P(НЕТ) − цена НЕТ с комиссией):")
    for c in M["cand"][:6]:
        L.append(f"  {c['coin']} {'↑' if c['up'] else '↓'}{c['K']:g}: P(да) {c['q_yes']:.2f}, НЕТ {c['no_ask']:.3f}, перевес {c['edge']:+.2f}, {c['h']} ч")
    if M["err"]:
        L.append("⚠️ " + M["err"])
    return "\n".join(L)

def table():
    T = totals()
    return {"версия": VERSION, "закрыто": T["n"], "выиграл_НЕТ": T["win"], "итог_$": round(T["pnl"], 2), "вложено_$": round(T["cost"], 2),
            "открыто": T["pos"], "сделки": T["done"][-300:], "кандидаты": M["cand"], "журнал": S["log"][-150:],
            "волатильность": M["vol"], "источник_цен": M["src"], "ошибка": M["err"]}

# ---------- база и цикл ----------
def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'poly_v1'", fetch=True)
    with LOCK:
        S.update(json.loads(r[0][0]) if r else {})
        for k, d in (("pos", {}), ("done", []), ("log", []), ("seen", {})):
            S.setdefault(k, d)
    S["loaded"] = 1

def save():
    with LOCK:
        js = json.dumps({k: v for k, v in S.items() if k != "loaded"})
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('poly_v1', %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (js,))

def loop():
    time.sleep(120)
    while not S.get("loaded"):
        try:
            load()
        except Exception:
            log.exception("xpoly load")
            time.sleep(60)
    while True:
        t0 = time.time()
        M["err"] = ""
        try:
            step(t0)
        except Exception as e:
            M["err"] = str(e)[:120]
            log.exception("xpoly step")
        time.sleep(max(30, STEP - (time.time() - t0)))

def ok():
    return mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN

@mm.app.route("/poly")
def poly_page():
    if not ok():
        return "forbidden", 403
    return jsonify(table())

@mm.app.route("/poly/brief")
def poly_brief():
    if not ok():
        return "forbidden", 403
    txt = text()
    if request.args.get("tg") == "1":
        mm.tg(txt)
    return txt, 200, {"Content-Type": "text/plain; charset=utf-8"}

if ON:
    threading.Thread(target=loop, daemon=True).start()
