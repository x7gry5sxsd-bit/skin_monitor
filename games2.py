# games2.py — Dota 2 (+ PUBG по адресу): спред на площадке TM + спрос из маркета Steam (только данные)
import os, time, threading, logging
import html as _html
import requests
from flask import jsonify, request
import mm_scanner as mm

def env(k, d=""):
    return os.getenv(k, d).strip()

GAMES = {"dota2": ("https://market.dota2.net", 570)}
if env("PUBG_MARKET"):
    GAMES["pubg"] = (env("PUBG_MARKET").rstrip("/"), 578080)
MIN_PRICE = float(env("G2_MIN_PRICE", "0.3"))
MAX_PRICE = float(env("G2_MAX_PRICE", "200"))
MIN_VOL = int(env("G2_MIN_VOL", "5"))             # мин. лотов в продаже на площадке
MIN_EDGE = float(env("G2_MIN_EDGE", "0.05"))      # от 5% после комиссии 5%
MIN_SVOL = int(env("G2_MIN_STEAM_VOL", "10"))     # мин. продаж в маркете Steam за сутки
CHECK_N = int(env("G2_CHECK_N", "120"))           # сколько кандидатов проверять в Steam за круг
SCAN_MIN = float(env("G2_SCAN_MIN", "60"))
REPORT_H = float(env("G2_REPORT_H", "6"))
log = logging.getLogger("games2")
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 uspex-g2"})
ST = {"rows": {}, "cache": {}, "last": 0, "src": {}, "steam_429": 0, "report_ts": 0}

mm.db("""CREATE TABLE IF NOT EXISTS g2_hist(ts BIGINT, game TEXT, name TEXT, bid REAL, ask REAL,
      edge REAL, svol INT)""")
mm.db("CREATE INDEX IF NOT EXISTS g2_hist_ts ON g2_hist(ts)")

def steam_vol(appid, name):
    key = f"{appid}:{name}"
    c = ST["cache"].get(key)
    if c and time.time() - c[0] < 86400:                      # кэш на сутки
        return c[1]
    try:
        r = S.get("https://steamcommunity.com/market/priceoverview/",
                  params={"appid": appid, "currency": 1, "market_hash_name": name}, timeout=20)
    except requests.RequestException:
        return None
    if r.status_code == 429:                                  # Steam просит притормозить
        ST["steam_429"] += 1
        time.sleep(60)
        return None
    try:
        d = r.json()
    except ValueError:
        return None
    v = int(str(d.get("volume") or "0").replace(",", "") or 0) if d.get("success") else 0
    ST["cache"][key] = (time.time(), v)
    time.sleep(3.5)                                           # ~17 запросов в минуту
    return v

def scan():
    now, hist = int(time.time()), []
    for g, (base, appid) in GAMES.items():
        asks, s1 = mm.mc_prices(base, "USD.json")
        bids, s2 = mm.mc_prices(base, "orders/USD.json")
        ST["src"][g] = f"asks {s1} | bids {s2}"
        if not asks or not bids:
            continue
        cands = []
        for n, (ask, vol) in asks.items():
            b = bids.get(n)
            if not b or not (MIN_PRICE <= ask <= MAX_PRICE) or vol < MIN_VOL or b[0] >= ask:
                continue
            e = ask * 0.998 * 0.95 / (b[0] * 1.002) - 1
            if e >= MIN_EDGE:
                cands.append((e * ask, n, b[0], ask, e))
        cands.sort(reverse=True)
        rows = []
        for _, n, bid, ask, e in cands[:CHECK_N]:
            sv = steam_vol(appid, n)
            if sv is None or sv < MIN_SVOL:
                continue
            rows.append({"name": n, "bid": round(bid, 2), "ask": round(ask, 2), "edge": round(e, 4),
                         "svol": sv, "score": round(e * ask * min(sv, 50), 2)})
            hist.append((now, g, n, bid, ask, e, sv))
        ST["rows"][g] = sorted(rows, key=lambda r: -r["score"])
    if hist:
        mm.db("INSERT INTO g2_hist VALUES %s", hist)
    ST["last"] = now

def report():
    lines = ["🎮 Dota 2 / PUBG — спред на TM + спрос в Steam"]
    for g, rows in ST["rows"].items():
        lines.append(f"\n{g}: {len(rows)} позиций (продаж в Steam ≥{MIN_SVOL}/сутки)")
        for r in rows[:8]:
            lines.append(f"  {r['name']} | +{r['edge']*100:.1f}% | ${r['bid']}→${r['ask']} | "
                         f"Steam {r['svol']}/сут")
    mm.tg("\n".join(lines))

def loop():
    time.sleep(600)                              # стартуем позже остальных — бережём память
    mm.tg(f"🟢 Сканер Dota 2/PUBG запущен: {', '.join(GAMES)}")
    while True:
        t0 = time.time()
        try:
            scan()
            if time.time() - ST["report_ts"] >= REPORT_H * 3600:
                ST["report_ts"] = time.time()
                report()
            log.info("games2: %s | 429=%d", {g: len(r) for g, r in ST["rows"].items()}, ST["steam_429"])
        except Exception:
            log.exception("games2")
        time.sleep(max(300, SCAN_MIN * 60 - (time.time() - t0)))

@mm.app.route("/dota")
def dota_page():
    if not mm.STATS_TOKEN or request.args.get("t") != mm.STATS_TOKEN:
        return "forbidden", 403
    if request.args.get("fmt") == "json":
        return jsonify({"last": ST["last"], "src": ST["src"], "steam_429": ST["steam_429"],
                        "rows": {g: r[:50] for g, r in ST["rows"].items()}})
    body = ""
    for g, rows in ST["rows"].items():
        trs = "".join(f"<tr><td>{_html.escape(r['name'])}</td><td>{r['edge']*100:.1f}%</td>"
                      f"<td>${r['bid']}</td><td>${r['ask']}</td><td>{r['svol']}</td></tr>"
                      for r in rows[:60])
        body += (f"<h4>{g} — {len(rows)}</h4><div class='w'><table><tr><th>Позиция</th>"
                 f"<th>Выгода</th><th>Заявка</th><th>Продажа</th><th>Steam/сут</th></tr>{trs}"
                 f"</table></div>")
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Dota/PUBG</title>
<style>body{{font-family:-apple-system,sans-serif;margin:8px;background:#111;color:#eee}}
.w{{overflow-x:auto}} table{{border-collapse:collapse;font-size:12px;white-space:nowrap}}
th,td{{border-bottom:1px solid #333;padding:4px 6px;text-align:right}} th{{background:#222}}
td:first-child,th:first-child{{text-align:left}} p{{color:#aaa;font-size:13px}}</style></head>
<body><h3>Dota 2 / PUBG</h3><p>Источники: {ST['src']} | Steam 429: {ST['steam_429']}</p>
{body or '<p>Первый скан ещё идёт (~20 минут после запуска).</p>'}</body></html>"""

threading.Thread(target=loop, daemon=True).start()