# tm_bot.py — v1.1 DRY RUN: маркетмейкинг на rust.tm (чтение, расчёт, история). ЧАСТЬ 1 из 2
import os, time, threading, logging
import html as _html
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
import requests
import psycopg2
from flask import jsonify, request
import mm_scanner as mm     # сканер работает в этом же процессе; берём у него app, db и tg
try:
    import pools      # сканер пассивных пулов (мосты, лендинг)
except Exception:
    logging.getLogger("tm").exception("pools не загрузился")
try:
    import loops      # сканер петель с плечом
except Exception:
    logging.getLogger("tm").exception("loops не загрузился")
try:
    import router     # самоадаптирующийся роутер капитала (бумага)
except Exception:
    logging.getLogger("tm").exception("router не загрузился")
try:
    import bx_cycles  # сеть обменов BingX
except Exception:
    logging.getLogger("tm").exception("bx_cycles не загрузился")
try:
    import xarb       # межбиржевые окна, 9 бирж
except Exception:
    logging.getLogger("tm").exception("xarb не загрузился")

def env(k, d=""):
    return os.getenv(k, d).strip()

TM = "https://rust.tm"
TM_KEY = env("TM_API_KEY")
DRY_RUN = env("DRY_RUN", "1") != "0"
SELL_FEE = float(env("TM_SELL_FEE", "0.05"))       # комиссия с продажи
MIN_MARGIN = float(env("TM_MIN_MARGIN", "0.08"))   # мин. чистая маржа на сделку
TICK = 0.01
PLAN_MIN = float(env("TM_PLAN_MIN", "10"))
HEALTH_MIN = float(env("TM_HEALTH_MIN", "30"))
REPORT_H = float(env("TM_REPORT_H", "6"))
TZ = timezone(timedelta(hours=float(env("TZ_OFFSET", "8"))))     # Иркутск = UTC+8
ITEMS = {"High Quality Crate": 3, "Weapon Barrel": 1, "High Quality Bag": 1,
         "Whiteout Roadsign Gloves": 1, "Blackout Jacket": 1}

log = logging.getLogger("tm")
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 uspex-tm"})
LOCK, LAST = threading.Lock(), {"t": 0.0}
ST = {"plan": [], "health": {}, "plan_ts": 0, "health_ts": 0, "last_report": 0, "flags": {}}

# ---------------- API rust.tm ----------------
def tm(method, **params):
    """Приватный API rust.tm, не чаще 2 запросов/сек (при >5/сек ключ удаляют)."""
    with LOCK:
        wait = 0.5 - (time.time() - LAST["t"])
        if wait > 0:
            time.sleep(wait)
        LAST["t"] = time.time()
    try:
        r = S.get(f"{TM}/api/v2/{method}", params={**params, "key": TM_KEY},
                  headers={"X-API-KEY": TM_KEY}, timeout=30)
    except requests.RequestException as e:
        return {"success": False, "error": type(e).__name__}
    try:
        return r.json()
    except ValueError:
        return {"success": False, "error": f"HTTP {r.status_code}"}

def public(path):
    try:
        d = S.get(f"{TM}/api/v2/prices/{path}", timeout=60).json()
    except Exception as e:
        log.warning("public %s: %s", path, e)
        return {}
    items = d.get("items") if isinstance(d, dict) else None
    if isinstance(items, dict):
        items = list(items.values())
    out = {}
    for it in items or []:
        try:
            p = float(it.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if it.get("market_hash_name") in ITEMS and p > 0:
            out[it["market_hash_name"]] = p
    return out

# ---------------- План заявок ----------------
def make_plan():
    asks, bids = public("RUB.json"), public("orders/RUB.json")
    rows = []
    for name, cnt in ITEMS.items():
        ask, top = asks.get(name), bids.get(name, 0.0)
        if not ask:
            rows.append({"name": name, "status": "нет цены продажи"})
            continue
        if top >= ask:                                  # фантомная заявка без денег
            top = 0.0
        med = q("""SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY ask) FROM tm_plan
                   WHERE name = %s AND ts > %s""", (name, int(time.time()) - 7 * 86400))
        sell = round(min(ask, med[0][0]) - TICK, 2) if med and med[0][0] else round(ask - TICK, 2)
        ceiling = round(sell * (1 - SELL_FEE) / (1 + MIN_MARGIN), 2)
        target = round(min(top + TICK, ceiling), 2)
        profit = round(sell * (1 - SELL_FEE) - target, 2)
        rows.append({"name": name, "count": cnt, "ask": ask, "top_bid": top,
                     "my_bid": target, "ceiling": ceiling, "sell_at": sell, "profit": profit,
                     "margin": round(profit / target, 3) if target else 0,
                     "status": "первые в очереди" if top + TICK <= ceiling
                               else "конкурент выше потолка"})
    now = int(time.time())
    ST["plan"], ST["plan_ts"] = rows, now
    data = [(now, r["name"], r["ask"], r["top_bid"], r["my_bid"], r["sell_at"],
             r["profit"], r["margin"]) for r in rows if "ask" in r]
    if data:
        try:
            mm.db("INSERT INTO tm_plan VALUES %s", data)
        except Exception:
            log.exception("tm_plan insert")
    return rows

# ---------------- Состояние аккаунта ----------------
def health():
    if not TM_KEY:
        h = {"test": {"error": "TM_API_KEY не задан"}}
        ST["health"], ST["health_ts"] = h, int(time.time())
        return h
    t = tm("test")
    m, o, it = tm("get-money"), tm("get-orders"), tm("items")
    inv = tm("my-inventory/", lang="en")
    by_status = {}
    for x in it.get("items") or []:
        s = str(x.get("status"))
        by_status[s] = by_status.get(s, 0) + 1
    ours = [x for x in inv.get("items") or [] if x.get("market_hash_name") in ITEMS]
    flags = t.get("status") if isinstance(t.get("status"), dict) else {}
    h = {"test": flags or {"error": t.get("error") or t.get("message") or "нет ответа"},
         "money": m.get("money"), "currency": m.get("currency"),
         "orders": len(o.get("orders") or []), "items_by_status": by_status,
         "inv_our_items": len(ours), "inv_tradable": sum(1 for x in ours if x.get("tradable"))}
    ST["health"], ST["health_ts"] = h, int(time.time())
    for k, v in flags.items():                          # сообщаем, когда условие изменилось
        old = ST["flags"].get(k)
        if old is not None and old != v:
            mm.tg(f"{'✅' if v else '⚠️'} rust.tm: {k} → {v}")
    ST["flags"] = dict(flags)
    return h

# ---------------- Отчёты и цикл ----------------
def fmt_plan(rows):
    out = []
    for r in rows:
        if "ask" not in r:
            out.append(f"• {r['name']}: {r['status']}")
            continue
        out.append(f"• {r['name']} ×{r['count']}: заявка {r['my_bid']:.2f}₽ "
                   f"(верх {r['top_bid']:.2f}) → продажа {r['sell_at']:.2f}₽ | "
                   f"+{r['profit']:.2f}₽ ({r['margin']*100:.0f}%) | {r['status']}")
    return "\n".join(out)

def fmt_health(h):
    t = h.get("test", {})
    if "error" in t:
        marks = f"⚠️ rust.tm: {t['error']}"
    else:
        marks = " ".join(f"{k}:{'✅' if v is True else '❌'}" for k, v in t.items())
    if h.get("money") is None:
        return marks
    return (f"Баланс: {h.get('money')} {h.get('currency')} | ордеров: {h.get('orders')} | "
            f"наших предметов: {h.get('inv_our_items')} (можно продать: "
            f"{h.get('inv_tradable')})\n{marks}")

def loop():
    h, p = health(), make_plan()
    mode = "DRY RUN — ничего не покупает" if DRY_RUN else "БОЕВОЙ"
    mm.tg(f"🤖 TM бот v1.1 запущен ({mode})\n{fmt_health(h)}\n\nПлан заявок:\n{fmt_plan(p)}")
    ST["last_report"] = time.time()
    while True:
        time.sleep(60)
        try:
            now = time.time()
            if now - ST["plan_ts"] >= PLAN_MIN * 60:
                make_plan()
            if now - ST["health_ts"] >= HEALTH_MIN * 60:
                health()
            if now - ST["last_report"] >= REPORT_H * 3600:
                mm.tg(f"🤖 TM план ({'DRY RUN' if DRY_RUN else 'LIVE'}):\n"
                      f"{fmt_health(ST['health'])}\n\n{fmt_plan(ST['plan'])}")
                ST["last_report"] = now
        except Exception:
            log.exception("tm loop")
# ---------------- ЧАСТЬ 2 из 2: база истории и веб-страницы ----------------
def q(sql, params=()):
    conn = psycopg2.connect(mm.DB_URL, connect_timeout=20)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall() if cur.description else None
    finally:
        conn.close()

q("""CREATE TABLE IF NOT EXISTS tm_plan(ts BIGINT, name TEXT, ask REAL, top_bid REAL,
     my_bid REAL, sell_at REAL, profit REAL, margin REAL)""")
q("CREATE INDEX IF NOT EXISTS tm_plan_name_ts ON tm_plan(name, ts)")

def fmt_t(ts):
    return datetime.fromtimestamp(int(ts), TZ).strftime("%d.%m %H:%M")

def page(title, sections, note=""):
    body = ""
    for sub, head, rows in sections:
        th = "".join(f"<th>{h}</th>" for h in head)
        trs = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
        body += f"<h4>{sub}</h4><div class='w'><table><tr>{th}</tr>{trs}</table></div>"
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<style>body{{font-family:-apple-system,sans-serif;margin:8px;background:#111;color:#eee}}
h3,h4{{margin:8px 0 4px}} p{{color:#aaa;font-size:13px}} .w{{overflow-x:auto}}
table{{border-collapse:collapse;font-size:12px;white-space:nowrap}}
th,td{{border-bottom:1px solid #333;padding:4px 6px;text-align:right}}
th{{background:#222}} td:first-child,th:first-child{{text-align:left}} a{{color:#6cf}}
</style></head><body><h3>{title}</h3><p>{note}</p>{body}</body></html>"""

def authed():
    return bool(mm.STATS_TOKEN) and request.args.get("t") == mm.STATS_TOKEN

def days_arg():
    try:
        return max(1, min(60, int(request.args.get("days", "7"))))
    except ValueError:
        return 7

@mm.app.route("/tm")
def tm_stats():
    if not authed():
        return "forbidden", 403
    return jsonify({"dry_run": DRY_RUN, "health": ST["health"], "plan": ST["plan"]})

@mm.app.route("/history")
def history():
    if not authed():
        return "forbidden", 403
    days, game, tok = days_arg(), request.args.get("game", ""), request.args.get("t", "")
    since = int(time.time()) - days * 86400
    flt = " AND game = %s" if game in mm.GAMES else ""
    rows = q(f"""SELECT game, name, COUNT(*), MIN(ts), MAX(ts), AVG(edge), MIN(edge), MAX(edge),
        (ARRAY_AGG(bid ORDER BY ts))[1], (ARRAY_AGG(bid ORDER BY ts DESC))[1],
        (ARRAY_AGG(ask ORDER BY ts))[1], (ARRAY_AGG(ask ORDER BY ts DESC))[1], MAX(sales7)
        FROM mm2 WHERE ts > %s{flt} GROUP BY game, name
        ORDER BY COUNT(*) DESC LIMIT 150""", (since, game) if flt else (since,)) or []
    scans = days * 24 * 60 / mm.SCAN_MIN
    out = []
    for g, n, c, t0, t1, ea, emin, emax, b0, b1, a0, a1, s7 in rows:
        link = (f'<a href="/item?t={quote(tok)}&days={days}&name={quote(n)}">'
                f'{_html.escape(n)}</a>')
        out.append([link, g, f"{min(100, c / scans * 100):.0f}%", fmt_t(t0), fmt_t(t1),
                    f"{ea*100:.1f}%", f"{emin*100:.0f}…{emax*100:.0f}%",
                    f"{b0:.2f}→{b1:.2f}", f"{a0:.2f}→{a1:.2f}", s7])
    return page(f"Кандидаты за {days} дн. ({len(out)})",
                [("", ["Позиция", "Игра", "В списке", "Первый", "Последний", "Выгода ср.",
                       "Мин…макс", "Заявка $", "Продажа $", "Продаж/нед"], out)],
                "«В списке» — доля сканов, где позиция проходила все фильтры. "
                "Нажмите на название — полная история позиции.")

@mm.app.route("/item")
def item():
    if not authed():
        return "forbidden", 403
    name, days = request.args.get("name", ""), days_arg()
    since = int(time.time()) - days * 86400
    r1 = q("""SELECT ts, game, bid, ask, edge, sales7, range7 FROM mm2
        WHERE name = %s AND ts > %s ORDER BY ts DESC LIMIT 400""", (name, since)) or []
    r2 = q("""SELECT ts, top_bid, my_bid, ask, profit, margin FROM tm_plan
        WHERE name = %s AND ts > %s ORDER BY ts DESC LIMIT 400""", (name, since)) or []
    s1 = [[fmt_t(t), g, f"{b:.2f}", f"{a:.2f}", f"{e*100:.1f}%", s, f"{r*100:.0f}%"]
          for t, g, b, a, e, s, r in r1]
    s2 = [[fmt_t(t), f"{tb:.2f}", f"{mb:.2f}", f"{a:.2f}", f"{p:.2f}", f"{m*100:.0f}%"]
          for t, tb, mb, a, p, m in r2]
    return page(_html.escape(name) + f" — {days} дн.",
                [("Сканер (MC, $)", ["Время", "Игра", "Заявка", "Продажа", "Выгода",
                                     "Продаж/нед", "Разброс"], s1),
                 ("План бота (rust.tm, ₽)", ["Время", "Верх. заявка", "Моя заявка",
                                             "Продажа", "Прибыль", "Маржа"], s2)],
                "Пропуски в истории сканера значат, что позиция в это время не проходила фильтры.")
@mm.app.route("/report")
def report_txt():
    """Весь анализ одним текстом. ?tg=1 — прислать в Telegram (удобно копировать)."""
    import sys
    if not authed():
        return "forbidden", 403
    L = [f"ОТЧЁТ {datetime.now(TZ).strftime('%d.%m %H:%M')}", "", "[СКИНЫ — сканер]",
         f"воронка: {mm.STATE.get('funnel')}", f"Glove Case: {mm.STATE.get('probe')}"]
    try:
        for g, n, h, e, s7, r, b, a, sc in mm.top_rows(12):
            L.append(f"{g} | {n} | +{e*100:.1f}% | {s7}/нед | ±{r*100:.0f}% | "
                     f"${b:.2f}→${a:.2f} | {h * mm.SCAN_MIN / 60:.0f}ч")
    except Exception as e:
        L.append(f"ошибка сканера: {type(e).__name__}")
    L += ["", "[СКИНЫ — бот rust.tm]", fmt_health(ST["health"]), fmt_plan(ST["plan"])]
    p = sys.modules.get("pools")
    if p:
        L += ["", f"[ПУЛЫ] планка Aave {p.ST.get('bench')}% | ошибка: {p.ST.get('error') or 'нет'}"]
        for title, key in (("крупные суммы", "eth"), ("малые суммы (L2)", "l2"), ("всплески", "spikes")):
            L.append(f"— {title}:")
            for r in (p.ST.get(key) or [])[:5]:
                L.append(f"  {r['project']} {r['chain']} {r['symbol']}: сейчас {r['base']}% | "
                         f"7д {r.get('avg7')}% | оценка {r.get('score')}% | TVL ${r['tvl']}M")
    lp = sys.modules.get("loops")
    if lp:
        L += ["", f"[ПЕТЛИ] займ: {lp.ST.get('borrow')} | ошибка: {lp.ST.get('error') or 'нет'}"]
        for r in (lp.ST.get("rows") or [])[:5]:
            lev = " ".join(f"{k}:{v}%" for k, v in r.items() if k.startswith("x"))
            L.append(f"  {r['sym']} {r['proj']} {r['chain']}: залог {r['y']}% займ {r['borrow']}% "
                     f"спред {r['spread']} | {lev}")
    ro = sys.modules.get("router")
    if ro and ro.STATE:
        st = ro.STATE
        L += ["", f"[РОУТЕР] {st.get('regime')} | капитал {ro.equity(st):.2f}$ | "
                  f"безопасный {st.get('safe')} | доля арбитража {st.get('max_share')}",
              f"  funding: {st.get('fund')} | позиции: {list(st.get('pos', {})) or 'нет'}"]
    text = "\n".join(L)
    if request.args.get("tg") == "1":
        for i in range(0, len(text), 3800):              # лимит Telegram ~4096 символов
            mm.tg(text[i:i + 3800])
        return "отправлено в Telegram", 200, {"Content-Type": "text/plain; charset=utf-8"}
    return text, 200, {"Content-Type": "text/plain; charset=utf-8"}
threading.Thread(target=loop, daemon=True).start()

if __name__ == "__main__":
    mm.app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))