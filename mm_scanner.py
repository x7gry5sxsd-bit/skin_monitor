# mm_scanner.py — v1: сканер спреда «заявка → продажа» на площадках сети Market.CSGO (только данные)
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

GAMES = {"cs2": "https://market.csgo.com", "dota2": "https://market.dota2.net",
         "rust": "https://rust.tm", "tf2": "https://tf2.tm"}
ENABLED = [g.strip() for g in env("GAMES", "cs2,dota2,rust,tf2").split(",") if g.strip()]
MIN_PRICE = float(env("MIN_PRICE_USD", "0.5"))
MAX_PRICE = float(env("MAX_PRICE_USD", "300"))
MIN_VOL = int(env("MIN_VOL", "10"))          # мин. лотов в продаже — ликвидность
SELL_FEE = float(env("SELL_FEE", "0.05"))    # 5% с продажи; вывод не считаем — баланс оборачиваем
MIN_EDGE = float(env("MIN_EDGE", "0.03"))    # от 3% чистыми за оборот
SCAN_MIN = float(env("SCAN_MIN", "30"))
REPORT_H = float(env("REPORT_H", "6"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mm")
if not DB_URL:
    raise SystemExit("DATABASE_URL пустой")
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 uspex-mm"})
STATE = {"src": {}, "last_scan": 0, "last_counts": {}, "probe": {}, "last_report": 0}

def tg(text):
    if not (TG_TOKEN and TG_CHAT):
        log.warning("TG не настроен")
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

db("""CREATE TABLE IF NOT EXISTS mm_obs(ts BIGINT, game TEXT, name TEXT, bid REAL,
      ask REAL, vol INT, bvol INT, edge REAL)""")
db("CREATE INDEX IF NOT EXISTS mm_obs_ts ON mm_obs(ts)")

def prices(base, path):
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
        n = it.get("market_hash_name")
        if n and p > 0:
            out[n] = (p, v)
    return out, f"ok {len(out)}"

def scan():
    now, rows, per = int(time.time()), [], {}
    for g in ENABLED:
        base = GAMES.get(g)
        if not base:
            continue
        asks, s1 = prices(base, "USD.json")
        bids, s2 = prices(base, "orders/USD.json")
        STATE["src"][g] = f"asks {s1} | bids {s2}"
        if not asks or not bids:
            continue
        if g == "cs2":
            STATE["probe"] = {"Glove Case": {"ask": asks.get("Glove Case"),
                                             "bid": bids.get("Glove Case")}}
        cnt = 0
        for n, (ask, vol) in asks.items():
            b = bids.get(n)
            if not b or not (MIN_PRICE <= ask <= MAX_PRICE) or vol < MIN_VOL:
                continue
            bid, bvol = b
            if bid >= ask:            # фантомная заявка: у покупателя нет денег
                continue
            e = ask * 0.998 * (1 - SELL_FEE) / (bid * 1.002) - 1
            if e >= MIN_EDGE:
                rows.append((now, g, n, bid, ask, vol, bvol, e))
                cnt += 1
        per[g] = cnt
    if rows:
        db("INSERT INTO mm_obs VALUES %s", rows)
    STATE.update(last_scan=now, last_counts=per)
    log.info("scan: %s | %s", per, STATE["src"])

def top_rows(limit):
    day = int(time.time()) - 86400
    return db(f"""SELECT game, name, COUNT(*), AVG(edge), MIN(vol), MIN(bvol), AVG(bid), AVG(ask)
        FROM mm_obs WHERE ts > {day} GROUP BY game, name HAVING COUNT(*) >= 3
        ORDER BY COUNT(*) DESC, AVG(edge) DESC LIMIT {limit}""", fetch=True)

def report():
    day = int(time.time()) - 86400
    per = db(f"""SELECT game, COUNT(DISTINCT name) FROM mm_obs WHERE ts > {day}
        GROUP BY game""", fetch=True)
    top = top_rows(12)
    lines = [f"📊 Спреды MC за 24ч (≥{MIN_EDGE*100:.0f}% после {SELL_FEE*100:.0f}%):",
             "Позиций по играм: " + (", ".join(f"{g} {c}" for g, c in per) or "нет")]
    for g, n, hits, e, vol, bvol, bid, ask in top:
        lines.append(f"{g} | {n} | +{e*100:.1f}% | {hits*SCAN_MIN/60:.0f}ч | "
                     f"${bid:.2f}→${ask:.2f} | лотов {vol}")
    tg("\n".join(lines))

def loop():
    tg("🟢 MM scanner v1 запущен (CS2, Dota 2, Rust, TF2 — только данные)")
    while True:
        t0 = time.time()
        try:
            scan()
            if time.time() - STATE["last_report"] >= REPORT_H * 3600 and STATE["last_report"]:
                report()
                STATE["last_report"] = time.time()
            elif not STATE["last_report"]:
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
    top = top_rows(30)
    return jsonify({
        "last_scan": STATE["last_scan"], "sources": STATE["src"],
        "last_counts": STATE["last_counts"], "probe": STATE["probe"],
        "top_24h": [{"game": g, "name": n, "hours": round(h * SCAN_MIN / 60, 1),
                     "avg_edge": round(e, 4), "min_vol": v, "min_bvol": bv,
                     "bid": round(b, 2), "ask": round(a, 2)}
                    for g, n, h, e, v, bv, b, a in top],
    })

threading.Thread(target=loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))