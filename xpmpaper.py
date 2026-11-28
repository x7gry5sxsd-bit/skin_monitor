# xpmpaper.py — БУМАГА Polymarket: все рынки с закрытием в ближайшие 10 ч, ставки только при положительном перевесе.
#   Ничего не торгует (из России Polymarket — только закрытие позиций). Проверка гипотезы из исследования 28.11:
#   на спортивных форах и тоталах до начала матча фаворитов переоценивают. Модель: logit(P) = a + b·logit(середина),
#   a = +0,069, b = 0,822 (выборка без фильтра объёма, 1160 сторон, 365 матчей; 95% ДИ b 0,70–0,96).
#   На честной истории правило дало +5,6% на $1 (ДИ −10…+22%) — не доказано, поэтому и бумага.
#   Раз в XPP_EVERY_S (600 с): все активные рынки Gamma с endDate ≤ XPP_HOURS (10) ч. Ставка (бумага), если:
#   ожидание покупки по текущему ask после комиссии рынка ≥ XPP_MIN_EV (+10%), модель выше ask на ≥ XPP_MIN_GAP
#   (3 п.п.), цена стороны 0,15–0,85, спред ≤ 5¢, ликвидность ≥ $1000, до начала матча, один рынок на матч.
#   Размер: ¼ Келли от банка XPP_BANK ($1000), ≤ 1% банка на ставку, ≤ 2% на матч, ≤ 30% в открытых,
#   убыток дня 5% — новых ставок нет до завтра. Расчёт — по итогу рынка (Gamma), с комиссией.
#   Критерий (заранее): ≥ 300 рассчитанных матчей, итог на $1 > 0 и нижняя граница 95% ДИ по матчам > 0.
#   Telegram: итог дня в 9:00 по Иркутску; XPP_TG_ALERTS=1 — ещё и каждая ставка (их сотни в день).
#   Страницы (?t=STATS_TOKEN): /pmpaper — всё (JSON), /pmpaper/brief — коротко (&tg=1 — в Telegram). XPP_ON=0 — выкл.
import os, time, json, math, re, random, threading, logging
from datetime import datetime
import requests
from flask import jsonify, request
import mm_scanner as mm
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

VERSION = "xpmpaper v1 · 28.11"
ON = env("XPP_ON", "1") == "1"
EVERY = max(120.0, float(env("XPP_EVERY_S", "600")))
HOURS = float(env("XPP_HOURS", "10"))
MIN_EV = float(env("XPP_MIN_EV", "0.10"))
MIN_GAP = float(env("XPP_MIN_GAP", "0.03"))
MIN_LIQ = float(env("XPP_MIN_LIQ", "1000"))
MAX_SPREAD = float(env("XPP_MAX_SPREAD", "0.05"))
SIDE_LO, SIDE_HI = float(env("XPP_SIDE_LO", "0.15")), float(env("XPP_SIDE_HI", "0.85"))
BANK0 = float(env("XPP_BANK", "1000"))
KELLY = float(env("XPP_KELLY", "0.25"))
MAX_STAKE, MAX_EVENT, MAX_OPEN, DAY_LOSS = 0.01, 0.02, 0.30, 0.05
MIN_STAKE = 2.0
TG_ALERTS = env("XPP_TG_ALERTS", "0") == "1"
COEF = {"спорт: фора": (0.069, 0.822), "спорт: тотал": (0.069, 0.822)}
GAMMA = "https://gamma-api.polymarket.com"
log = logging.getLogger("xpmpaper")
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0"})
L = {}
LOCK = threading.RLock()

def lt(t=None, f="%d.%m %H:%M"):
    return time.strftime(f, time.gmtime((time.time() if t is None else t) + 8 * 3600))

def say(text):
    mm.tg("🧪🎲 Polymarket [бумага]: " + text)

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'pmpaper_v1'", fetch=True)
    L.update(json.loads(r[0][0]) if r else {})
    for k, v in (("bets", []), ("bank", BANK0), ("day", ""), ("day_pnl", 0.0), ("day_n", 0), ("scans", 0),
                 ("last", {}), ("sent_day", "")):
        L.setdefault(k, v)
    L["loaded"] = 1

def save():
    if not L.get("loaded"):
        return
    try:
        xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('pmpaper_v1', %s)
                    ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(L),))
    except Exception:
        log.exception("xpmpaper save")

# ---------- модель ----------
def category(q, fee_type):
    q, ft = (q or "").lower(), fee_type or ""
    if not ft.startswith("sports") or "exact score" in q:
        return None
    if "o/u" in q or "total" in q:
        return "спорт: тотал"
    if "spread" in q or "handicap" in q:
        return "спорт: фора"
    return None

def logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))

def fair0(cat, mid):
    a, b = COEF[cat]
    return 1 / (1 + math.exp(-(a + b * logit(mid))))

def ev_of(p, ask, rate):
    return p / ask - 1 - rate * (1 - ask)

def kelly(p, ask, rate):
    cost = ask * (1 + rate * (1 - ask)); b = 1 / cost - 1
    return max(0.0, (b * p - (1 - p)) / b) if b > 0 else 0.0

def ts(s):
    if not s:
        return None
    s = str(s).replace("Z", "+00:00").replace(" ", "T", 1)
    if len(s) >= 3 and s[-3] in "+-" and ":" not in s[-3:]:
        s += ":00"
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None

def get(url, params=None):
    for i in range(4):
        try:
            r = S.get(url, params=params, timeout=40)
            if r.status_code == 429:
                time.sleep(3 + 3 * i); continue
            return r.json()
        except Exception:
            time.sleep(2 + i)
    return None

def live_markets():
    now = time.time()
    f = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
    p0 = {"closed": "false", "active": "true", "limit": 500, "end_date_min": f(now), "end_date_max": f(now + HOURS * 3600)}
    out, cur = [], None
    for _ in range(400):                               # страница Gamma — до 100 рынков, всего их ~20 тыс.
        d = None
        for _try in range(3):                          # сбой страницы не должен обрывать весь список
            d = get(GAMMA + "/markets/keyset", dict(p0, **({"after_cursor": cur} if cur else {})))
            if isinstance(d, dict) and "markets" in d:
                break
            time.sleep(3)
        d = d if isinstance(d, dict) else {}
        ms = d.get("markets") or []
        out += ms
        cur = d.get("next_cursor")
        if not cur or not ms:
            break
    return out

def ev_slug(m):
    ev = m.get("events")
    if isinstance(ev, list) and ev:
        return ev[0].get("slug", "") or m.get("slug", "")
    return m.get("slug", "")

def evaluate(m, now):
    cat = category(m.get("question"), m.get("feeType"))
    if not cat or not m.get("acceptingOrders", True):
        return None
    try:
        outs = json.loads(m.get("outcomes") or "[]")
    except ValueError:
        return None
    end, gs = ts(m.get("endDate")), ts(m.get("gameStartTime"))
    if len(outs) != 2 or not end or not (900 <= end - now <= HOURS * 3600) or (gs and now >= gs):
        return None
    try:
        bid, ask = float(m.get("bestBid")), float(m.get("bestAsk"))
    except (TypeError, ValueError):
        return None
    if not (0 < bid < ask < 1) or ask - bid > MAX_SPREAD or float(m.get("liquidityNum") or 0) < MIN_LIQ:
        return None
    f0 = fair0(cat, (bid + ask) / 2)
    fs = m.get("feeSchedule") or {}
    rate = float(fs.get("rate") or 0) if m.get("feesEnabled", True) else 0.0
    best = None
    for side, (p, a) in enumerate(((f0, ask), (1 - f0, 1 - bid))):
        if not SIDE_LO <= a <= SIDE_HI:
            continue
        e = ev_of(p, a, rate)
        if e >= MIN_EV and p - a >= MIN_GAP and (best is None or e > best["ev"]):
            slug = ev_slug(m)
            best = {"id": str(m.get("id")), "q": m.get("question"), "outcome": outs[side], "side": side,
                    "ask": round(a, 4), "fair": round(p, 4), "ev": round(e, 4), "kelly": round(kelly(p, a, rate), 4),
                    "rate": rate, "cat": cat, "event": slug, "end": int(end), "t": int(now),
                    "liq": round(float(m.get("liquidityNum") or 0)), "url": f"https://polymarket.com/event/{slug}"}
    return best

# ---------- бумага ----------
def roll_day():
    d = lt(None, "%Y-%m-%d")
    if L["day"] != d:
        L["day"], L["day_pnl"], L["day_n"] = d, 0.0, 0

def stake_for(a):
    open_bets = [b for b in L["bets"] if b["status"] == "open"]
    if L["day_pnl"] <= -DAY_LOSS * L["bank"]:
        return 0.0
    if any(b["event"] == a["event"] for b in open_bets) or any(b["id"] == a["id"] for b in L["bets"]):
        return 0.0
    s = min(a["kelly"] * KELLY * L["bank"], MAX_STAKE * L["bank"], MAX_EVENT * L["bank"])
    s = min(s, MAX_OPEN * L["bank"] - sum(b["stake"] for b in open_bets))
    return round(s, 2) if s >= MIN_STAKE else 0.0

def resolve(now, limit=40):
    n = 0
    for b in L["bets"]:
        if b["status"] != "open" or now < b["end"] + 1800 or n >= limit:
            continue
        n += 1
        m = get(f"{GAMMA}/markets/{b['id']}")
        if not m or not m.get("closed"):
            if now > b["end"] + 7 * 86400:
                b["status"] = "void"
            continue
        try:
            op = [float(x) for x in json.loads(m.get("outcomePrices") or "[]")]
        except ValueError:
            continue
        if sorted(op) != [0.0, 1.0]:
            if len(op) == 2 and abs(op[0] - 0.5) < 0.01:      # возврат 50/50
                b.update(status="void", pnl=0.0)
            continue
        win = op[b["side"]] == 1.0
        shares = b["stake"] / (b["ask"] * (1 + b["rate"] * (1 - b["ask"])))
        pnl = round((shares if win else 0.0) - b["stake"], 4)
        b.update(status="won" if win else "lost", pnl=pnl, t_res=int(now))
        L["bank"] += pnl
        L["day_pnl"] += pnl

def stats():
    done = [b for b in L["bets"] if b["status"] in ("won", "lost")]
    out = {"ставок_рассчитано": len(done), "открыто": sum(b["status"] == "open" for b in L["bets"]),
           "банк_$": round(L["bank"], 2), "старт_$": BANK0}
    if not done:
        return out
    st = sum(b["stake"] for b in done); pnl = sum(b["pnl"] for b in done)
    ev = {}
    for b in done:
        x = ev.setdefault(b["event"], [0.0, 0.0]); x[0] += b["pnl"]; x[1] += b["stake"]
    vals = list(ev.values()); rnd = random.Random(1); bs = []
    for _ in range(1000):
        smp = [vals[rnd.randrange(len(vals))] for _ in vals]
        bs.append(sum(p for p, _ in smp) / sum(s for _, s in smp))
    bs.sort()
    gw = sum(b["pnl"] for b in done if b["pnl"] > 0); gl = -sum(b["pnl"] for b in done if b["pnl"] < 0)
    out.update({"итог_$": round(pnl, 2), "на_доллар_%": round(pnl / st * 100, 2), "матчей": len(vals),
                "ДИ95_на_доллар_%": [round(bs[25] * 100, 1), round(bs[974] * 100, 1)],
                "побед_%": round(sum(b["status"] == "won" for b in done) / len(done) * 100, 1),
                "profit_factor": round(gw / gl, 3) if gl else None,
                "средний_перевес_модели_%": round(sum(b["ev"] for b in done) / len(done) * 100, 1),
                "критерий": "выполнен" if len(vals) >= 300 and bs[25] > 0 else
                            ("провален" if len(vals) >= 300 else f"копим: {len(vals)}/300 матчей")})
    return out

def brief():
    s = stats(); last = L.get("last") or {}
    t = (f"{VERSION}. Рынков ≤{HOURS:g} ч при последнем проходе: {last.get('markets', '?')}, с перевесом: "
         f"{last.get('alerts', '?')}, новых ставок: {last.get('opened', '?')} ({lt(last.get('t'))}).\n"
         f"Рассчитано {s['ставок_рассчитано']}, открыто {s['открыто']}, банк ${s['банк_$']:.2f} (старт ${BANK0:g}).")
    if s.get("итог_$") is not None:
        t += (f"\nИтог {s['итог_$']:+.2f}$ ({s['на_доллар_%']:+.1f}% на $1, 95% ДИ {s['ДИ95_на_доллар_%'][0]:+.1f}…"
              f"{s['ДИ95_на_доллар_%'][1]:+.1f}%), побед {s['побед_%']}%, PF {s['profit_factor']}, "
              f"модель обещала {s['средний_перевес_модели_%']:+.1f}%. Критерий: {s['критерий']}.")
    return t

def step(now):
    roll_day()
    ms = live_markets()
    alerts = [a for a in (evaluate(m, now) for m in ms) if a]
    alerts.sort(key=lambda a: -a["ev"])
    opened = 0
    seen = set()
    for a in alerts:
        if a["event"] in seen:
            continue
        seen.add(a["event"])
        s = stake_for(a)
        if not s:
            continue
        L["bets"].append(dict(a, stake=s, status="open"))
        L["day_n"] += 1
        opened += 1
        if TG_ALERTS:
            say(f"{a['q']} — «{a['outcome']}» по {a['ask']:.3f}, модель {a['fair']:.3f}, EV {a['ev'] * 100:+.1f}%, "
                f"ставка ${s:.2f}, до закрытия {(a['end'] - now) / 3600:.1f} ч\n{a['url']}")
    resolve(now)
    L["bets"] = [b for b in L["bets"] if b["status"] == "open" or now - b.get("t_res", b["t"]) < 60 * 86400][-20000:]
    L["scans"] += 1
    L["last"] = {"t": int(now), "markets": len(ms), "alerts": len(seen), "opened": opened}
    if lt(now, "%H") == "09" and L["sent_day"] != L["day"]:
        L["sent_day"] = L["day"]
        say(brief())

def loop():
    time.sleep(120)
    while not L.get("loaded"):
        try:
            load()
        except Exception:
            log.exception("xpmpaper load")
            time.sleep(60)
    while True:
        try:
            with LOCK:
                step(time.time())
                save()
        except Exception:
            log.exception("xpmpaper")
        time.sleep(EVERY)

def ok():
    return mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN

@mm.app.route("/pmpaper")
def pmpaper_page():
    if not ok():
        return "forbidden", 403
    with LOCK:
        return jsonify({"версия": VERSION, "итог": stats(), "последний_проход": L.get("last"),
                        "настройки": {"часов_до_закрытия": HOURS, "мин_EV_%": MIN_EV * 100, "мин_перевес_пп": MIN_GAP * 100,
                                      "цена_стороны": [SIDE_LO, SIDE_HI], "спред_макс": MAX_SPREAD, "ликвидность_мин_$": MIN_LIQ,
                                      "модель": {k: {"a": a, "b": b} for k, (a, b) in COEF.items()}, "келли": KELLY},
                        "открытые": [b for b in L.get("bets", []) if b["status"] == "open"][-100:],
                        "рассчитанные": [b for b in L.get("bets", []) if b["status"] in ("won", "lost")][-100:]})

@mm.app.route("/pmpaper/brief")
def pmpaper_brief():
    if not ok():
        return "forbidden", 403
    with LOCK:
        txt = brief() if L.get("loaded") else "ещё загружается"
    if request.args.get("tg") == "1":
        say(txt)
    return txt, 200, {"Content-Type": "text/plain; charset=utf-8"}

if ON:
    threading.Thread(target=loop, daemon=True).start()
