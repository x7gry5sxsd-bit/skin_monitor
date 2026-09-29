# skin_monitor.py — v1: спред Skinport <-> Market.CSGO + история цен (только данные и алерты)
import os, time, threading, logging
import requests
import psycopg2
from psycopg2.extras import execute_values
from flask import Flask, jsonify, request

DB_URL = os.environ["DATABASE_URL"]
TG_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT = os.getenv("TG_CHAT_ID", "")
STATS_TOKEN = os.getenv("STATS_TOKEN", "")

MIN_PRICE = float(os.getenv("MIN_PRICE_USD", "3"))
MAX_PRICE = float(os.getenv("MAX_PRICE_USD", "500"))
MIN_VOL7 = int(os.getenv("MIN_VOL7", "20"))          # мин. продаж на Skinport за 7 дней
MIN_EDGE = float(os.getenv("MIN_EDGE", "0.05"))      # порог: 5% чистыми
MCSGO_FEE = float(os.getenv("MCSGO_FEE", "0.10"))    # 5% продажа + 5% вывод
SP_FEE = float(os.getenv("SKINPORT_FEE", "0.08"))
SCAN_MIN = float(os.getenv("SCAN_MIN", "10"))
HIST_MIN = float(os.getenv("HIST_MIN", "60"))        # история + снапшот раз в час
COOLDOWN_H = float(os.getenv("ALERT_COOLDOWN_H", "6"))
SKIP_WORDS = ("Doppler", "Case Hardened", "Fade")    # цена зависит от фазы/паттерна

MC = "https://market.csgo.com/api/v2/prices"
SP = "https://api.skinport.com/v1"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("monitor")
S = requests.Session()
S.headers.update({"Accept-Encoding": "br, gzip", "User-Agent": "Mozilla/5.0 uspex-monitor"})
STATE = {"hist": {}, "hist_ts": 0, "sent": {}, "last_scan": 0, "last_opps": 0,
         "liquid": 0, "blocked_sent": False}

# ---------------- HTTP / TG ----------------
def get_json(url, params=None, tries=4):
    for a in range(tries):
        try:
            r = S.get(url, params=params, timeout=60)
        except requests.RequestException as e:
            log.warning("net %s: %s", url, e)
            time.sleep(5 * (a + 1))
            continue
        if r.status_code == 429:
            ra = r.headers.get("Retry-After", "")
            w = int(ra) + 1 if ra.isdigit() else 60
            log.warning("429 %s, sleep %ss", url, w)
            time.sleep(w)
            continue
        if r.status_code == 403:
            log.error("403 %s (Cloudflare?)", url)
            return "BLOCKED"
        if r.status_code != 200:
            log.error("HTTP %s %s: %s", r.status_code, url, r.text[:150])
            time.sleep(5)
            continue
        try:
            return r.json()
        except ValueError:
            log.error("bad json %s", url)
            return None
    return None

def tg(text):
    if not (TG_TOKEN and TG_CHAT):
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      json={"chat_id": TG_CHAT, "text": text,
                            "disable_web_page_preview": True}, timeout=10)
    except requests.RequestException as e:
        log.warning("tg: %s", e)

# ---------------- DB (Postgres) ----------------
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

db("""CREATE TABLE IF NOT EXISTS snap(ts BIGINT, name TEXT, sp_min REAL, sp_qty INT,
      mc_ask REAL, mc_bid REAL, med7 REAL, vol7 INT, min7 REAL, max7 REAL, vol30 INT)""")
db("CREATE INDEX IF NOT EXISTS snap_name_ts ON snap(name, ts)")
db("""CREATE TABLE IF NOT EXISTS opps(ts BIGINT, dir TEXT, name TEXT, buy REAL,
      sell REAL, edge REAL, range7 REAL, vol7 INT)""")
db("CREATE INDEX IF NOT EXISTS opps_ts ON opps(ts)")

# ---------------- Источники ----------------
def mc_prices(path):
    d = get_json(f"{MC}/{path}")
    items = d.get("items") if isinstance(d, dict) else None
    if isinstance(items, dict):
        items = list(items.values())
    out = {}
    for it in items or []:
        try:
            p = float(it.get("price") or 0)
        except (TypeError, ValueError):
            continue
        n = it.get("market_hash_name")
        if n and p > 0:
            out[n] = p
    return out

def sp_items():
    d = get_json(f"{SP}/items", {"app_id": 730, "currency": "USD", "tradable": "true"})
    if d == "BLOCKED":
        return d
    out = {}
    for it in d or []:
        p = it.get("min_price")
        if p:
            out[it["market_hash_name"]] = (float(p), int(it.get("quantity") or 0),
                                           it.get("item_page", ""))
    return out

def sp_history():
    d = get_json(f"{SP}/sales/history", {"app_id": 730, "currency": "USD"})
    if d == "BLOCKED":
        return d
    out = {}
    for it in d or []:
        w7 = it.get("last_7_days") or {}
        w30 = it.get("last_30_days") or {}
        if not w7.get("median"):
            continue
        out[it["market_hash_name"]] = {
            "med7": float(w7["median"]), "vol7": int(w7.get("volume") or 0),
            "min7": float(w7.get("min") or 0), "max7": float(w7.get("max") or 0),
            "vol30": int(w30.get("volume") or 0)}
    return out

# ---------------- Логика ----------------
def blocked():
    if not STATE["blocked_sent"]:
        STATE["blocked_sent"] = True
        tg("🔴 Skinport отвечает 403 (Cloudflare). Монитор стоит — пришли логи Render.")

def alert(o, sp):
    _, d, name, buy, sell, e, rng, vol7 = o
    key = d + name
    if time.time() - STATE["sent"].get(key, 0) < COOLDOWN_H * 3600:
        return
    STATE["sent"][key] = time.time()
    link = sp.get(name, (0, 0, ""))[2]
    how = "Skinport → заявка MC" if d == "SP->MC" else "MC → Skinport"
    risk = " ⚠️ разброс > маржи" if rng > e else ""
    tg(f"💱 {name}\n{how}\nКупить ${buy:.2f} → продать ${sell:.2f}\n"
       f"Чистыми {e*100:.1f}% | продаж 7д: {vol7} | разброс 7д: {rng*100:.0f}%{risk}\n{link}")

def scan():
    now = int(time.time())
    fresh = False
    if not STATE["hist"] or now - STATE["hist_ts"] >= HIST_MIN * 60:
        h = sp_history()
        if h == "BLOCKED":
            return blocked()
        if h:
            STATE["hist"], STATE["hist_ts"], fresh = h, now, True
    hist = STATE["hist"]
    if not hist:
        return
    sp = sp_items()
    if sp == "BLOCKED":
        return blocked()
    asks, bids = mc_prices("USD.json"), mc_prices("orders/USD.json")
    if not asks or not sp:
        log.warning("пустые данные: asks=%d sp=%d", len(asks), len(sp or {}))
        return
    snaps, opps, liquid = [], [], 0
    for name, h in hist.items():
        if h["vol7"] < MIN_VOL7 or any(w in name for w in SKIP_WORDS):
            continue
        liquid += 1
        s, ask, bid = sp.get(name), asks.get(name), bids.get(name)
        rng = (h["max7"] - h["min7"]) / h["med7"]
        if fresh:
            snaps.append((now, name, s[0] if s else None, s[1] if s else None, ask, bid,
                          h["med7"], h["vol7"], h["min7"], h["max7"], h["vol30"]))
        # A: купить на Skinport -> продать в заявку Market.CSGO (мгновенный выход)
        if s and bid and MIN_PRICE <= s[0] <= MAX_PRICE:
            e = bid * (1 - MCSGO_FEE) / s[0] - 1
            if e >= MIN_EDGE:
                opps.append((now, "SP->MC", name, s[0], bid, e, rng, h["vol7"]))
        # B: купить на Market.CSGO -> продать на Skinport (min из медианы 7д и текущей цены)
        if ask and MIN_PRICE <= ask <= MAX_PRICE:
            sell = min(h["med7"], s[0]) if s else h["med7"]
            e = sell * (1 - SP_FEE) / ask - 1
            if e >= MIN_EDGE:
                opps.append((now, "MC->SP", name, ask, sell, e, rng, h["vol7"]))
    if snaps:
        db("INSERT INTO snap VALUES %s", snaps)
    if opps:
        db("INSERT INTO opps VALUES %s", opps)
    STATE.update(last_scan=now, last_opps=len(opps), liquid=liquid)
    log.info("scan: liquid=%d opps=%d snaps=%d", liquid, len(opps), len(snaps))
    for o in sorted(opps, key=lambda x: -x[5])[:5]:
        alert(o, sp)

def loop():
    tg("🟢 Skin monitor v1 запущен (Skinport ↔ Market.CSGO, только данные)")
    while True:
        t0 = time.time()
        try:
            scan()
        except Exception:
            log.exception("scan error")
        time.sleep(max(30, SCAN_MIN * 60 - (time.time() - t0)))

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
    by_dir = db(f"""SELECT dir, COUNT(DISTINCT name), COUNT(*), AVG(edge) FROM opps
        WHERE ts>{day} GROUP BY dir""", fetch=True)
    top = db(f"""SELECT dir, name, COUNT(*), MAX(edge), MIN(buy), MAX(range7), MAX(vol7)
        FROM opps WHERE ts>{day} GROUP BY dir, name ORDER BY COUNT(*) DESC LIMIT 25""",
        fetch=True)
    sn = db("SELECT COUNT(*), COUNT(DISTINCT name) FROM snap", fetch=True)[0]
    return jsonify({
        "last_scan": STATE["last_scan"], "last_scan_opps": STATE["last_opps"],
        "liquid_items": STATE["liquid"], "snap_rows": sn[0], "snap_items": sn[1],
        "last_24h": [{"dir": r[0], "items": r[1], "hits": r[2],
                      "avg_edge": round(r[3], 4)} for r in by_dir],
        "top_persistent": [{"dir": r[0], "name": r[1], "minutes": int(r[2] * SCAN_MIN),
                            "max_edge": round(r[3], 4), "buy": r[4],
                            "range7": round(r[5] or 0, 3), "vol7": r[6]} for r in top],
    })

threading.Thread(target=loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))