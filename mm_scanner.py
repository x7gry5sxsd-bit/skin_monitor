# mm_scanner.py — v2: спред «заявка → продажа» на MC + спрос и стабильность по Skinport (только данные)
import os, time, threading, logging
import requests
import psycopg2
from psycopg2.extras import execute_values
from flask import Flask, jsonify, request

def env(k, d=""):
    return os.getenv(k, d).strip()

DB_URL = env("DATABASE_URL")
TG_TOKEN = env("TG_BOT_TOKEN")
if TG_TOKEN.startswith("bot"):
    TG_TOKEN = TG_TOKEN[3:]
TG_CHAT = env("TG_CHAT_ID")
STATS_TOKEN = env("STATS_TOKEN")

GAMES = {"cs2": ("https://market.csgo.com", 730), "dota2": ("https://market.dota2.net", 570),
         "rust": ("https://rust.tm", 252490), "tf2": ("https://tf2.tm", 440)}
ENABLED = [g.strip() for g in env("GAMES", "cs2,dota2,rust,tf2").split(",") if g.strip()]
MIN_PRICE = float(env("MIN_PRICE_USD", "0.5"))
MAX_PRICE = float(env("MAX_PRICE_USD", "300"))
MIN_VOL = int(env("MIN_VOL", "10"))            # мин. лотов в продаже на MC
MIN_SALES7 = int(env("MIN_SALES7", "30"))      # мин. продаж за 7 дней (Skinport) — спрос
MAX_RANGE7 = float(env("MAX_RANGE7", "0.3"))   # макс. разброс цены за 7 дней — риск холда
SELL_FEE = float(env("SELL_FEE", "0.05"))      # 5% с продажи на MC
MIN_EDGE = float(env("MIN_EDGE", "0.03"))
SCAN_MIN = float(env("SCAN_MIN", "30"))
HIST_H = float(env("HIST_H", "2"))             # как часто обновлять историю Skinport
REPORT_H = float(env("REPORT_H", "6"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mm")
if not DB_URL:
    raise SystemExit("DATABASE_URL пустой")
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 uspex-mm", "Accept-Encoding": "br, gzip"})
STATE = {"src": {}, "hist": {}, "hist_ts": {}, "last_scan": 0, "last_counts": {},
         "funnel": {}, "probe": {}, "last_report": 0}

def tg(text):
    if not (TG_TOKEN and TG_CHAT):
        return
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                          json={"chat_id": TG_CHAT, "text": text,
                                "disable_web_page_preview": True}, timeout=10)
        if r.status_code != 200:
            log.error("tg HTTP %s: %s", r.status_code, r.text[:200])
    except requests.RequestException as e:
        log.warning("tg: %s", e)

def db(sql, rows=None, fetch=False):
    conn = psycopg2.connect(DB_URL, connect_timeout=20)
    try:
        with conn:
            with conn.cursor() as cur:
                if rows is not None:
                    execute_values(cur, sql, rows, page_size=1000)
                else:
                    cur.execute(sql)
                return cur.fetchall() if fetch else None
    finally:
        conn.close()

db("""CREATE TABLE IF NOT EXISTS mm2(ts BIGINT, game TEXT, name TEXT, bid REAL, ask REAL,
      vol INT, sales7 INT, range7 REAL, edge REAL)""")
db("CREATE INDEX IF NOT EXISTS mm2_ts ON mm2(ts)")

def mc_prices(base, path):
    try:
        r = S.get(f"{base}/api/v2/prices/{path}", timeout=60)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        d = r.json()
    except Exception as e:
        return None, type(e).__name__
    items = d.get("items") if isinstance(d, dict) else None
    if isinstance(items, dict):
        items = list(items.values())
    out = {}
    for it in items or []:
        try:
            p, v = float(it.get("price") or 0), int(float(it.get("volume") or 0))
        except (TypeError, ValueError):
            continue
        if it.get("market_hash_name") and p > 0:
            out[it["market_hash_name"]] = (p, v)
    return out, f"ok {len(out)}"

def sp_hist(app):
    try:
        r = S.get("https://api.skinport.com/v1/sales/history",
                  params={"app_id": app, "currency": "USD"}, timeout=120)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        d = r.json()
    except Exception as e:
        return None, type(e).__name__
    out = {}
    for it in d or []:
        w = it.get("last_7_days") or {}
        med = w.get("median")
        if med:
            out[it["market_hash_name"]] = (int(w.get("volume") or 0), float(med),
                                           float(w.get("min") or 0), float(w.get("max") or 0))
    return out, f"ok {len(out)}"

def scan():
    now, rows, per = int(time.time()), [], {}
    for g in ENABLED:
        if g not in GAMES:
            continue
        base, app = GAMES[g]
        if now - STATE["hist_ts"].get(g, 0) >= HIST_H * 3600:
            h, s = sp_hist(app)
            STATE["src"][g + "_skinport"] = s
            if h:
                STATE["hist"][g], STATE["hist_ts"][g] = h, now
            time.sleep(40)                      # лимит Skinport: 8 запросов / 5 мин
        hist = STATE["hist"].get(g, {})
        asks, s1 = mc_prices(base, "USD.json")
        bids, s2 = mc_prices(base, "orders/USD.json")
        STATE["src"][g] = f"asks {s1} | bids {s2}"
        if not asks or not bids:
            continue
        if g == "cs2":
            STATE["probe"] = {"ask": asks.get("Glove Case"), "bid": bids.get("Glove Case"),
                              "skinport": hist.get("Glove Case")}
        f = {"spread": 0, "demand": 0, "stable": 0}
        for n, (ask, vol) in asks.items():
            b = bids.get(n)
            if not b or not (MIN_PRICE <= ask <= MAX_PRICE) or vol < MIN_VOL or b[0] >= ask:
                continue
            e = ask * 0.998 * (1 - SELL_FEE) / (b[0] * 1.002) - 1
            if e < MIN_EDGE or e > 0.6 or b[0] < 0.5 * ask:
                continue
            f["spread"] += 1
            hs = hist.get(n)
            if not hs or hs[0] < MIN_SALES7:
                continue
            f["demand"] += 1
            sales7, med, lo, hi = hs
            rng = (hi - lo) / med if med else 9
            if rng > MAX_RANGE7 or not (0.5 <= ask / med <= 2.0):
                continue
            f["stable"] += 1
            rows.append((now, g, n, b[0], ask, vol, sales7, rng, e))
        per[g], STATE["funnel"][g] = f["stable"], f
    if rows:
        db("INSERT INTO mm2 VALUES %s", rows)
    STATE.update(last_scan=now, last_counts=per)
    log.info("scan: %s | funnel %s", per, STATE["funnel"])

def top_rows(limit):
    day = int(time.time()) - 86400
    return db(f"""SELECT game, name, COUNT(*), AVG(edge), MIN(sales7), MAX(range7),
        AVG(bid), AVG(ask), AVG(edge * ask * sales7 / 7.0) AS score
        FROM mm2 WHERE ts > {day} GROUP BY game, name HAVING COUNT(*) >= 3
        ORDER BY score DESC LIMIT {limit}""", fetch=True)

def report():
    top = top_rows(12)
    lines = [f"📊 MC маркетмейкинг, 24ч: спред ≥{MIN_EDGE*100:.0f}%, продаж ≥{MIN_SALES7}/нед, "
             f"разброс ≤{MAX_RANGE7*100:.0f}%", f"Воронка: {STATE['funnel']}"]
    for g, n, hits, e, s7, rng, bid, ask, sc in top:
        lines.append(f"{g} | {n} | +{e*100:.1f}% | {s7}/нед | ±{rng*100:.0f}% | "
                     f"${bid:.2f}→${ask:.2f}")
    tg("\n".join(lines))

def loop():
    tg("🟢 MM scanner v2 запущен (спред + спрос + стабильность)")
    while True:
        t0 = time.time()
        try:
            scan()
            if not STATE["last_report"]:
                STATE["last_report"] = time.time()
            elif time.time() - STATE["last_report"] >= REPORT_H * 3600:
                report()
                STATE["last_report"] = time.time()
        except Exception:
            log.exception("scan error")
        time.sleep(max(60, SCAN_MIN * 60 - (time.time() - t0)))

app = Flask(__name__)

@app.route("/")
def health():
    return "ok"

@app.route("/stats")
def stats():
    if not STATS_TOKEN or request.args.get("t") != STATS_TOKEN:
        return "forbidden", 403
    return jsonify({
        "last_scan": STATE["last_scan"], "sources": STATE["src"], "funnel": STATE["funnel"],
        "probe_glove_case": STATE["probe"],
        "top_24h": [{"game": g, "name": n, "hours": round(h * SCAN_MIN / 60, 1),
                     "edge": round(e, 4), "sales7": s7, "range7": round(r, 3),
                     "bid": round(b, 2), "ask": round(a, 2), "score": round(sc, 2)}
                    for g, n, h, e, s7, r, b, a, sc in top_rows(30)],
    })

if env("MM_SCAN", "1") == "1":                     # MM_SCAN=0 — сканер скинов выключен (не тянет сеть)
    threading.Thread(target=loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))