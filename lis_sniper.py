# lis_sniper.py — v1: бумажный снайпер Lis-Skins -> Market.CSGO (без вложений, только данные)
import os, re, time, threading, logging, html
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

MIN_PRICE = float(env("MIN_PRICE_USD", "3"))
MAX_PRICE = float(env("MAX_PRICE_USD", "300"))
MIN_MC_VOL = int(env("MIN_MC_VOL", "20"))      # мин. лотов на MC — ликвидность выхода
MAX_ITEMS = int(env("MAX_ITEMS", "600"))       # позиций за один круг
MIN_EDGE = float(env("MIN_EDGE", "0.05"))      # алерт: от 5% чистыми после комиссий MC
MCSGO_FEE = float(env("MCSGO_FEE", "0.10"))    # 5% продажа + 5% вывод
PAGE_DELAY = float(env("PAGE_DELAY", "3"))     # пауза между страницами Lis-Skins, сек
COOLDOWN_H = float(env("ALERT_COOLDOWN_H", "12"))
SKIP_WORDS = ("Doppler", "Case Hardened", "Fade")

MC_URL = "https://market.csgo.com/api/v2/prices/USD.json"
PRICE_RE = re.compile(r"skin price \$([\d,]+\.?\d*)")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("lis")
if not DB_URL:
    raise SystemExit("DATABASE_URL пустой")

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
                  "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
                  "Accept-Language": "en-US,en;q=0.9"})
STATE = {"sent": {}, "status": {}, "cycles": 0, "cycle_items": 0, "pos": 0,
         "block_sent": False}

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
                    execute_values(cur, sql, rows, page_size=500)
                else:
                    cur.execute(sql)
                return cur.fetchall() if fetch else None
    finally:
        conn.close()

db("""CREATE TABLE IF NOT EXISTS lis_obs(ts BIGINT, name TEXT, lis REAL, mc REAL,
      edge REAL)""")
db("CREATE INDEX IF NOT EXISTS lis_obs_ts ON lis_obs(ts)")

# ---------------- Источники ----------------
def mc_asks():
    try:
        d = S.get(MC_URL, timeout=60).json()
    except Exception as e:
        log.warning("MC: %s", e)
        return {}
    items = d.get("items") if isinstance(d, dict) else None
    if isinstance(items, dict):
        items = list(items.values())
    out = {}
    for it in items or []:
        try:
            p = float(it.get("price") or 0)
            v = int(float(it.get("volume") or 0))
        except (TypeError, ValueError):
            continue
        n = it.get("market_hash_name")
        if n and p > 0:
            out[n] = (p, v)
    return out

def slug(name):
    s = name.lower().replace("stattrak™", "stattrak").replace("★", "").replace("™", "")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")

def lis_min(name):
    url = f"https://lis-skins.com/market/csgo/{slug(name)}/"
    try:
        r = S.get(url, timeout=30)
    except requests.RequestException:
        return None, url, "net"
    if r.status_code != 200:
        return None, url, str(r.status_code)
    m = PRICE_RE.search(html.unescape(r.text))
    if not m:
        return None, url, "noprice"
    return float(m.group(1).replace(",", "")), url, "ok"

# ---------------- Логика ----------------
def alert(name, lp, ask, e, url):
    if time.time() - STATE["sent"].get(name, 0) < COOLDOWN_H * 3600:
        return
    STATE["sent"][name] = time.time()
    tg(f"🦊 {name}\nLis-Skins ${lp:.2f} → MC ${ask:.2f}\n"
       f"Чистыми {e*100:.1f}% после комиссий MC\n{url}")

def cycle():
    asks = mc_asks()
    if not asks:
        return
    use_vol = any(v for _, v in asks.values())
    names = [n for n, (p, v) in asks.items()
             if MIN_PRICE <= p <= MAX_PRICE and (not use_vol or v >= MIN_MC_VOL)
             and not any(w in n for w in SKIP_WORDS)]
    names.sort(key=lambda n: -asks[n][1])
    names = names[:MAX_ITEMS]
    STATE["cycle_items"] = len(names)
    rows, blocked = [], 0
    for i, n in enumerate(names):
        STATE["pos"] = i
        if i and i % 100 == 0:
            asks.update(mc_asks())
        lp, url, st = lis_min(n)
        STATE["status"][st] = STATE["status"].get(st, 0) + 1
        if st in ("403", "429", "503"):
            blocked += 1
            if blocked >= 5:
                if not STATE["block_sent"]:
                    STATE["block_sent"] = True
                    tg(f"🔴 Lis-Skins отвечает {st} — похоже, блок. Пауза 15 мин.")
                time.sleep(900)
                blocked = 0
            continue
        blocked = 0
        if lp:
            ask = asks[n][0]
            e = ask * 0.995 * (1 - MCSGO_FEE) / lp - 1
            rows.append((int(time.time()), n, lp, ask, e))
            if e >= MIN_EDGE:
                alert(n, lp, ask, e, url)
        if len(rows) >= 50:
            db("INSERT INTO lis_obs VALUES %s", rows)
            rows = []
        time.sleep(PAGE_DELAY)
    if rows:
        db("INSERT INTO lis_obs VALUES %s", rows)
    STATE["cycles"] += 1
    log.info("круг %d готов: %d позиций, статусы %s",
             STATE["cycles"], len(names), STATE["status"])

def loop():
    tg("🟢 Lis sniper v1 (бумажный) запущен")
    while True:
        try:
            cycle()
        except Exception:
            log.exception("cycle error")
            time.sleep(60)

# ---------------- Web ----------------
app = Flask(__name__)

@app.route("/")
def health():
    return "ok"

@app.route("/stats")
def stats():
    if not STATS_TOKEN or request.args.get("t") != STATS_TOKEN:
        return "forbidden", 403
    day = int(time.time()) - 86400
    a = db(f"""SELECT COUNT(*), COUNT(DISTINCT name),
        percentile_cont(0.5) WITHIN GROUP (ORDER BY edge),
        percentile_cont(0.95) WITHIN GROUP (ORDER BY edge),
        COUNT(DISTINCT CASE WHEN edge >= {MIN_EDGE} THEN name END)
        FROM lis_obs WHERE ts > {day}""", fetch=True)[0]
    top = db(f"""SELECT name, COUNT(*), MAX(edge), MIN(lis), MAX(mc) FROM lis_obs
        WHERE ts > {day} AND edge >= {MIN_EDGE}
        GROUP BY name ORDER BY MAX(edge) DESC LIMIT 25""", fetch=True)
    return jsonify({
        "cycles": STATE["cycles"], "cycle_items": STATE["cycle_items"],
        "position": STATE["pos"], "http_status": STATE["status"],
        "obs_24h": a[0], "items_24h": a[1],
        "median_edge": round(a[2] or 0, 4), "p95_edge": round(a[3] or 0, 4),
        "items_with_deal_24h": a[4],
        "deals": [{"name": r[0], "hits": r[1], "max_edge": round(r[2], 4),
                   "lis": r[3], "mc": r[4]} for r in top],
    })

threading.Thread(target=loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))