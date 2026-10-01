# tm_bot.py — v1 DRY RUN: маркетмейкинг на rust.tm (только чтение и расчёт, ничего не покупает)
import os, time, threading, logging
import requests
from flask import jsonify, request
import mm_scanner as mm     # сканер работает в этом же процессе; берём у него app и tg

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
ITEMS = {"Weapon Barrel": 1, "High Quality Crate": 2,       # позиция: сколько штук держать
         "High Quality Bag": 1, "Reptile Hunter Helmet": 1}

log = logging.getLogger("tm")
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 uspex-tm"})
LOCK, LAST = threading.Lock(), {"t": 0.0}
ST = {"plan": [], "health": {}, "plan_ts": 0, "health_ts": 0, "last_report": 0, "flags": {}}

def tm(method, **params):
    """Приватный API rust.tm, не чаще 2 запросов/сек (при >5/сек ключ удаляют)."""
    with LOCK:
        wait = 0.5 - (time.time() - LAST["t"])
        if wait > 0:
            time.sleep(wait)
        LAST["t"] = time.time()
    try:
        r = S.get(f"{TM}/api/v2/{method}", params=params,
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
        sell = round(ask - TICK, 2)
        ceiling = round(sell * (1 - SELL_FEE) / (1 + MIN_MARGIN), 2)
        target = round(min(top + TICK, ceiling), 2)
        profit = round(sell * (1 - SELL_FEE) - target, 2)
        rows.append({"name": name, "count": cnt, "ask": ask, "top_bid": top,
                     "my_bid": target, "ceiling": ceiling, "sell_at": sell, "profit": profit,
                     "margin": round(profit / target, 3) if target else 0,
                     "status": "первые в очереди" if top + TICK <= ceiling
                               else "конкурент выше потолка"})
    ST["plan"], ST["plan_ts"] = rows, int(time.time())
    return rows

def health():
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
    marks = " ".join(f"{k}:{'✅' if v is True else '❌'}" for k, v in t.items())
    return (f"Баланс: {h.get('money')} {h.get('currency')} | ордеров: {h.get('orders')} | "
            f"наших предметов: {h.get('inv_our_items')} (можно продать: {h.get('inv_tradable')})\n"
            f"{marks}")

def loop():
    if not TM_KEY:
        mm.tg("🔴 TM бот: TM_API_KEY не задан в Render")
        return
    h, p = health(), make_plan()
    mode = "DRY RUN — ничего не покупает" if DRY_RUN else "БОЕВОЙ"
    mm.tg(f"🤖 TM бот запущен ({mode})\n{fmt_health(h)}\n\nПлан заявок:\n{fmt_plan(p)}")
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

@mm.app.route("/tm")
def tm_stats():
    if not mm.STATS_TOKEN or request.args.get("t") != mm.STATS_TOKEN:
        return "forbidden", 403
    return jsonify({"dry_run": DRY_RUN, "health": ST["health"], "plan": ST["plan"]})

threading.Thread(target=loop, daemon=True).start()

if __name__ == "__main__":
    mm.app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))