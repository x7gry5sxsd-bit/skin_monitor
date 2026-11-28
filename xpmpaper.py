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
#   v1.2 ТЕНЬ (XPP_SHADOW=1): записываю ВСЕ рынки ≤10 ч с двумя исходами и стаканом (спорт, крипто, погода — любые),
#   без отбора моделью и без лимитов банка: последнюю цену до начала матча (без gameStartTime — за 15 мин до конца)
#   и первую увиденную. После итога рынка — кто выиграл. Таблица pm_shadow, по строке на рынок.
#   /pmpaper/all — итог «купить каждую сторону по ask после комиссии» по типам рынка × цене стороны × часам до старта,
#   отдельно — что дала бы модель (EV ≥ XPP_MIN_EV) без лимитов. Отсюда и отбирать правила, а не из 30 ставок.
#   Хранится XPP_SHADOW_DAYS (45) дней, ~10 МБ в базе за сутки. XPP_SHADOW=0 — выключить тень.
import os, time, json, math, re, random, threading, logging, gc
from datetime import datetime
import requests
from flask import jsonify, request
import mm_scanner as mm
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

VERSION = "xpmpaper v1.2 · 28.11"
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
SHADOW = env("XPP_SHADOW", "1") == "1"
SHADOW_DAYS = float(env("XPP_SHADOW_DAYS", "45"))       # тень хранится столько дней (~10 МБ в базе за сутки)
COEF = {"спорт: фора": (0.069, 0.822), "спорт: тотал": (0.069, 0.822)}
GAMMA = "https://gamma-api.polymarket.com"
log = logging.getLogger("xpmpaper")
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0"})
L = {}
SH = {}                                                # тень: id → последний снимок до старта (только в памяти)
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

def live_markets(now, keep, seen=None):
    """v1.1: обходит Gamma постранично и сразу оценивает каждый рынок; в памяти остаются только сигналы.
    v1 держал весь список (~24 тыс. рынков ≈ 210 МБ JSON → >600 МБ в Python) — сервис падал по памяти."""
    f = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
    p0 = {"closed": "false", "active": "true", "limit": 500, "end_date_min": f(now), "end_date_max": f(now + HOURS * 3600)}
    n, out, cur = 0, [], None
    for _ in range(400):                               # страница Gamma — до 100 рынков, всего их ~24 тыс.
        d = None
        for _try in range(3):                          # сбой страницы не должен обрывать весь список
            d = get(GAMMA + "/markets/keyset", dict(p0, **({"after_cursor": cur} if cur else {})))
            if isinstance(d, dict) and "markets" in d:
                break
            time.sleep(3)
        d = d if isinstance(d, dict) else {}
        ms = d.get("markets") or []
        cur = d.get("next_cursor")
        k = len(ms)
        n += k
        for m in ms:
            if seen is not None:
                shadow(m, now, seen)
            a = keep(m, now)
            if a:
                out.append(a)
        del d, ms
        if not cur or not k:
            break
    gc.collect()
    return n, out


# ---------- тень: все рынки без отбора ----------
SQL_T = """CREATE TABLE IF NOT EXISTS pm_shadow(id TEXT PRIMARY KEY, t BIGINT, t0 BIGINT, end_t BIGINT, gs BIGINT,
           smt TEXT, ft TEXT, cat TEXT, q TEXT, slug TEXT, bid REAL, ask REAL, bid0 REAL, ask0 REAL, liq REAL, rate REAL,
           fair REAL, win SMALLINT, res_t BIGINT)"""
SH_COLS = ("id", "t", "t0", "end_t", "gs", "smt", "ft", "cat", "q", "slug", "bid", "ask", "bid0", "ask0", "liq", "rate", "fair")
_sh_ready = []

def sh_init():
    xpaper.q(SQL_T)
    xpaper.q("CREATE INDEX IF NOT EXISTS pm_shadow_open ON pm_shadow(end_t) WHERE win IS NULL")
    xpaper.q("CREATE INDEX IF NOT EXISTS pm_shadow_end ON pm_shadow(end_t)")

def sh_cut(gs, end):
    return gs if gs else end - 900

def shadow(m, now, seen):
    try:
        mid_ = str(m.get("id"))
        seen.add(mid_)
        end, gs = ts(m.get("endDate")), ts(m.get("gameStartTime"))
        if not end or now >= sh_cut(gs, end):
            return
        bid, ask = float(m.get("bestBid")), float(m.get("bestAsk"))
        if not (0 < bid < ask < 1) or len(json.loads(m.get("outcomes") or "[]")) != 2:
            return
        r = SH.get(mid_)
        liq = float(m.get("liquidityNum") or 0)
        if r:
            r.update(t=int(now), bid=bid, ask=ask, liq=liq)
            return
        q, ft = m.get("question") or "", m.get("feeType") or ""
        cat = category(q, ft)
        fs = m.get("feeSchedule") or {}
        SH[mid_] = dict(id=mid_, t=int(now), t0=int(now), end_t=int(end), gs=int(gs) if gs else None,
                        smt=m.get("sportsMarketType") or "", ft=ft, cat=cat or "", q=q[:120], slug=ev_slug(m)[:80],
                        bid=bid, ask=ask, bid0=bid, ask0=ask, liq=liq,
                        rate=float(fs.get("rate") or 0) if m.get("feesEnabled", True) else 0.0,
                        fair=round(fair0(cat, (bid + ask) / 2), 4) if cat in COEF else None)
    except (TypeError, ValueError):
        return

def sh_flush(now, seen):
    """Рынки, которые начались или пропали из списка, → pm_shadow. Повторная запись — только если итога ещё нет."""
    out = [r for k, r in SH.items() if k not in seen or now >= sh_cut(r["gs"], r["end_t"])]
    for r in out:
        SH.pop(r["id"], None)
    rows = _sh_ready + [tuple(r[c] for c in SH_COLS) for r in out]
    _sh_ready.clear()
    if not rows:
        return 0
    try:
        import psycopg2.extras
        conn = xpaper.psycopg2.connect(mm.DB_URL, connect_timeout=20)
        try:
            with conn, conn.cursor() as cur:
                psycopg2.extras.execute_values(cur, f"""INSERT INTO pm_shadow({",".join(SH_COLS)}) VALUES %s
                    ON CONFLICT (id) DO UPDATE SET t = EXCLUDED.t, bid = EXCLUDED.bid, ask = EXCLUDED.ask, liq = EXCLUDED.liq
                    WHERE pm_shadow.win IS NULL AND EXCLUDED.t > pm_shadow.t""", rows, page_size=500)
        finally:
            conn.close()
    except Exception:
        log.exception("pm_shadow flush")
        _sh_ready.extend(rows[-20000:])                 # повторю на следующем проходе
        return 0
    return len(rows)

def sh_resolve(now, limit=2000):
    """Итоги рынков пачками по 50 (Gamma /markets?id=…&closed=true). win: 0/1 — сторона, −1 — возврат, −2 — нет итога за 7 дн."""
    ids = [r[0] for r in xpaper.q("SELECT id FROM pm_shadow WHERE win IS NULL AND end_t < %s ORDER BY end_t LIMIT %s",
                                    (int(now) - 3600, limit), fetch=True) or []]
    upd = []
    for i in range(0, len(ids), 50):
        part = ids[i:i + 50]
        d = get(GAMMA + "/markets", [("id", x) for x in part] + [("closed", "true"), ("limit", 100)])
        for m in d if isinstance(d, list) else []:
            try:
                op = [float(x) for x in json.loads(m.get("outcomePrices") or "[]")]
            except ValueError:
                continue
            w = 0 if op == [1.0, 0.0] else 1 if op == [0.0, 1.0] else -1 if len(op) == 2 and abs(op[0] - 0.5) < 0.01 else None
            if w is not None:
                upd.append((w, int(now), str(m.get("id"))))
    if upd:
        conn = xpaper.psycopg2.connect(mm.DB_URL, connect_timeout=20)
        try:
            with conn, conn.cursor() as cur:
                cur.executemany("UPDATE pm_shadow SET win = %s, res_t = %s WHERE id = %s", upd)
        finally:
            conn.close()
    xpaper.q("UPDATE pm_shadow SET win = -2 WHERE win IS NULL AND end_t < %s", (int(now) - 7 * 86400,))
    xpaper.q("DELETE FROM pm_shadow WHERE end_t < %s", (int(now - SHADOW_DAYS * 86400),))
    return len(upd)

SQL_ALL = """WITH s AS (
  SELECT id, slug, smt, ft, cat, t, gs, end_t, win, fair, liq, ask AS p, rate, 0 AS side FROM pm_shadow WHERE win IN (0, 1) AND t >= %(since)s
  UNION ALL
  SELECT id, slug, smt, ft, cat, t, gs, end_t, win, CASE WHEN fair IS NULL THEN NULL ELSE 1 - fair END, liq, 1 - bid, rate, 1 FROM pm_shadow
  WHERE win IN (0, 1) AND t >= %(since)s),
r AS (SELECT *, (CASE WHEN win = side THEN 1.0 ELSE 0.0 END) / (p * (1 + rate * (1 - p))) - 1 AS roi,
             CASE WHEN fair IS NULL THEN NULL ELSE fair / p - 1 - rate * (1 - p) END AS ev FROM s WHERE p > 0.005 AND p < 0.995)"""

def sh_table(since, by):
    keys = {"тип": "CASE WHEN smt <> '' THEN smt ELSE ft END",
            "цена": "CASE WHEN p < 0.05 THEN '0.00–0.05' WHEN p < 0.15 THEN '0.05–0.15' WHEN p < 0.30 THEN '0.15–0.30' "
                    "WHEN p < 0.50 THEN '0.30–0.50' WHEN p < 0.70 THEN '0.50–0.70' WHEN p < 0.85 THEN '0.70–0.85' "
                    "WHEN p < 0.95 THEN '0.85–0.95' ELSE '0.95–1.00' END",
            "до_старта": "CASE WHEN COALESCE(gs, end_t) - t < 1800 THEN '<0,5 ч' WHEN COALESCE(gs, end_t) - t < 7200 THEN '0,5–2 ч' "
                         "ELSE '2–10 ч' END",
            "модель": "CASE WHEN ev IS NULL THEN 'нет модели' WHEN ev >= %(ev)s THEN 'модель: EV ≥ порога' ELSE 'модель: ниже порога' END",
            "ликвидность": "CASE WHEN liq < 1000 THEN '<$1k' WHEN liq < 10000 THEN '$1–10k' ELSE '>$10k' END"}
    g = ", ".join(keys[b] for b in by)
    rows = xpaper.q(SQL_ALL + f""" SELECT {g}, COUNT(*), COUNT(DISTINCT slug), AVG(roi), STDDEV_SAMP(roi),
        AVG(CASE WHEN win = side THEN 1.0 ELSE 0.0 END) FROM r GROUP BY {g} ORDER BY {g}""",
                    {"since": since, "ev": MIN_EV}, fetch=True) or []
    out = []
    for row in rows:
        k, (n, ne, mu, sd, hit) = row[:len(by)], row[len(by):]
        mu = float(mu or 0); sd = float(sd or 0)
        se = sd / math.sqrt(max(1, ne))                  # грубо: по числу матчей, не сторон
        out.append({**{b: v for b, v in zip(by, k)}, "сторон": n, "матчей": ne, "на_$1_%": round(mu * 100, 2),
                    "ДИ95_%": [round((mu - 1.96 * se) * 100, 1), round((mu + 1.96 * se) * 100, 1)],
                    "выигрышей_%": round(float(hit or 0) * 100, 1)})
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
    seen = set() if SHADOW else None
    n_mk, alerts = live_markets(now, evaluate, seen)
    if SHADOW:
        try:
            L["sh_last"] = {"t": int(now), "в_памяти": len(SH), "записано": sh_flush(now, seen) if seen else 0}
            L["sh_last"]["итогов"] = sh_resolve(now)
        except Exception:
            log.exception("pm_shadow")
    alerts.sort(key=lambda a: -a["ev"])
    opened = 0
    seen = set()                                       # матчи, по которым уже есть ставка в этом проходе
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
    L["last"] = {"t": int(now), "markets": n_mk, "alerts": len(seen), "opened": opened}
    if lt(now, "%H") == "09" and L["sent_day"] != L["day"]:
        L["sent_day"] = L["day"]
        say(brief())

def loop():
    time.sleep(120)
    if SHADOW:
        try:
            sh_init()
        except Exception:
            log.exception("pm_shadow init")
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

@mm.app.route("/pmpaper/all")
def pmpaper_all():
    """Тень: ВСЕ рынки без отбора. ?days=N (по умолчанию 30), ?by=тип,цена (из: тип, цена, до_старта, модель, ликвидность)."""
    if not ok():
        return "forbidden", 403
    days = float(request.args.get("days", "30"))
    by = [b for b in (request.args.get("by") or "").split(",") if b] or None
    since = int(time.time() - days * 86400)
    try:
        cnt = xpaper.q("SELECT COUNT(*), COUNT(*) FILTER (WHERE win IN (0, 1)), COUNT(*) FILTER (WHERE win IS NULL), "
                       "COUNT(*) FILTER (WHERE win = -1) FROM pm_shadow WHERE t >= %s", (since,), fetch=True)[0]
        res = {"версия": VERSION, "за_дней": days, "в_памяти_до_старта": len(SH), "последний_проход": L.get("sh_last"),
               "рынков": {"записано": cnt[0], "с_итогом": cnt[1], "ждут_итога": cnt[2], "возврат": cnt[3]},
               "как_читать": "на_$1_% — средний итог покупки стороны по ask (вторая сторона — по 1−bid) после комиссии рынка; "
                             "ДИ грубый, по числу матчей. Обе стороны каждого рынка входят в таблицу."}
        if by:
            res["таблица"] = sh_table(since, by)
        else:
            for b in (["тип"], ["цена"], ["до_старта"], ["модель"], ["тип", "цена"]):
                res["по_" + "_и_".join(b)] = sh_table(since, b)
    except Exception as e:
        log.exception("pmpaper/all")
        res = {"ошибка": str(e)[:300]}
    return jsonify(res)

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
