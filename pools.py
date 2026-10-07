# pools.py — v2: пассивный доход на стейблкоинах — оценка, стабильность, всплески, ротация
import os, time, threading, logging, statistics
import html as _html
import requests
from flask import jsonify, request
import mm_scanner as mm

def env(k, d=""):
    return os.getenv(k, d).strip()

WATCH = [w.strip().lower() for w in env(
    "POOLS_WATCH", "stargate,across,aave,morpho,compound,curve,spark,sky,pendle,euler,fluid"
).split(",") if w.strip()]
CHAINS = [c.strip() for c in env(
    "POOLS_CHAINS", "Ethereum,Arbitrum,Base,Optimism,Polygon,BSC").split(",") if c.strip()]
L2 = {"Arbitrum", "Base", "Optimism", "Polygon", "BSC"}
MIN_TVL = float(env("POOLS_MIN_TVL", "5000000"))
SAFE_TVL = float(env("POOLS_SAFE_TVL", "30000000"))   # от $30M — без штрафа
SPIKE_PP = float(env("POOLS_SPIKE_PP", "3"))
ROT_PP = float(env("POOLS_ROT_PP", "2"))              # ротация, если лучше на 2+ п.п.
MY_POOL = env("POOLS_MY_POOL")                        # id вашего пула (появится после вложения)
SCAN_MIN = float(env("POOLS_SCAN_MIN", "60"))
MAJOR = ("USDC", "USDT", "DAI", "USDS", "USDE", "GHO", "PYUSD", "CRVUSD", "FRAX")
log = logging.getLogger("pools")
ST = {"rows": [], "eth": [], "l2": [], "spikes": [], "bench": None, "last": 0,
      "sent": {}, "error": ""}

mm.db("""CREATE TABLE IF NOT EXISTS pools_hist(ts BIGINT, pool TEXT, project TEXT, chain TEXT,
      symbol TEXT, tvl REAL, apy_base REAL, apy_reward REAL, apy_30d REAL)""")
mm.db("CREATE INDEX IF NOT EXISTS pools_hist_pool_ts ON pools_hist(pool, ts)")

def is_bridge(r):
    p = r["project"].lower()
    return "stargate" in p or "across" in p

def fetch():
    data = requests.get("https://yields.llama.fi/pools", timeout=60).json().get("data", [])
    rows = []
    for p in data:
        proj = (p.get("project") or "").lower()
        if not any(w in proj for w in WATCH) or p.get("chain") not in CHAINS:
            continue
        if not p.get("stablecoin") or p.get("ilRisk") != "no" or (p.get("tvlUsd") or 0) < MIN_TVL:
            continue
        rows.append({"pool": p.get("pool"), "project": p.get("project"), "chain": p.get("chain"),
                     "symbol": p.get("symbol") or "", "tvl": round((p.get("tvlUsd") or 0) / 1e6, 1),
                     "base": round(p.get("apyBase") or 0, 2),
                     "reward": round(p.get("apyReward") or 0, 2),
                     "mean30": round(p.get("apyMean30d") or 0, 2)})
    return rows

def bench(rows):
    xs = [r["base"] for r in rows if "aave" in r["project"].lower() and r["symbol"] in ("USDC", "USDT")]
    return round(statistics.median(xs), 2) if xs else None

def stats7(b):
    since = int(time.time()) - 7 * 86400
    res = mm.db(f"""SELECT pool, COUNT(*), AVG(apy_base), COALESCE(STDDEV_POP(apy_base), 0),
        SUM(CASE WHEN apy_base >= {float(b)} THEN 1 ELSE 0 END)
        FROM pools_hist WHERE ts > {since} GROUP BY pool""", fetch=True) or []
    return {p: {"avg7": a, "sd7": sd, "above": ab / n if n else 0} for p, n, a, sd, ab in res}

def enrich(rows, st):
    for r in rows:
        s = st.get(r["pool"], {})
        r["avg7"] = round(s.get("avg7") or r["base"], 2)
        r["sd7"] = round(s.get("sd7") or 0, 2)
        r["above7"] = round((s.get("above") or 0) * 100)
        r["l2"] = r["chain"] in L2
        pen = min(r["sd7"], 3.0)                                   # скачущая доходность
        if r["tvl"] < SAFE_TVL / 1e6:
            pen += 1.0                                             # маленький пул
        if not any(m in r["symbol"].upper() for m in MAJOR):
            pen += 2.0                                             # экзотический стейблкоин
        if r["project"].lower().startswith("pendle"):
            pen += 1.0                                             # блок до погашения
        r["score"] = round(min(r["base"], r["avg7"]) - pen, 2)    # консервативно

def scan():
    rows = fetch()
    b = bench(rows)
    now = int(time.time())
    mm.db("INSERT INTO pools_hist VALUES %s",
          [(now, r["pool"], r["project"], r["chain"], r["symbol"], r["tvl"], r["base"],
            r["reward"], r["mean30"]) for r in rows[:400]])
    enrich(rows, stats7(b) if b is not None else {})
    rows.sort(key=lambda r: -r["score"])
    spikes = [r for r in rows if b is not None and r["base"] >= b + SPIKE_PP
              and r["base"] >= 1.5 * max(r["mean30"], 0.1)]
    ST.update(rows=rows, bench=b, last=now, error="", spikes=spikes,
              eth=[r for r in rows if not r["l2"]][:5], l2=[r for r in rows if r["l2"]][:5])
    for r in spikes[:5]:
        if time.time() - ST["sent"].get("s" + r["pool"], 0) > 86400:
            ST["sent"]["s" + r["pool"]] = time.time()
            mm.tg(f"🏦 Всплеск: {r['project']} · {r['chain']} · {r['symbol']}\n"
                  f"Сейчас {r['base']}%, обычно {r['mean30']}% | планка Aave {b}% | TVL ${r['tvl']}M")
    rotation(rows)

def rotation(rows):
    mine = next((r for r in rows if r["pool"] == MY_POOL), None) if MY_POOL else None
    if not mine:
        return
    cands = [r for r in rows if r["l2"] == mine["l2"] and r["pool"] != MY_POOL and r["above7"] >= 70]
    best = max(cands, key=lambda r: r["score"], default=None)
    if best and best["score"] - mine["score"] >= ROT_PP \
            and time.time() - ST["sent"].get("rot", 0) > 3 * 86400:
        ST["sent"]["rot"] = time.time()
        mm.tg(f"🔁 Ротация: ваш пул {mine['project']} {mine['symbol']} — оценка {mine['score']}%\n"
              f"Лучше: {best['project']} · {best['chain']} · {best['symbol']} — оценка {best['score']}% "
              f"(выше планки {best['above7']}% времени за 7 дней)")

def loop():
    time.sleep(60)
    mm.tg("🟢 Сканер пулов v2 запущен (оценка, стабильность, ротация)")
    while True:
        t0 = time.time()
        try:
            scan()
            log.info("pools: %d rows, bench=%s", len(ST["rows"]), ST["bench"])
        except Exception as e:
            ST["error"] = type(e).__name__
            log.exception("pools loop")
        time.sleep(max(300, SCAN_MIN * 60 - (time.time() - t0)))

def table(rows):
    trs = "".join(
        f"<tr{' class=b' if is_bridge(r) else ''}><td>{_html.escape(r['project'])}</td>"
        f"<td>{r['chain']}</td><td>{_html.escape(r['symbol'])}</td><td>{r['base']}%</td>"
        f"<td>{r['avg7']}%</td><td>{r['above7']}%</td><td><b>{r['score']}%</b></td>"
        f"<td>{r['reward']}%</td><td>${r['tvl']}M</td>"
        f"<td><a href='https://defillama.com/yields/pool/{r['pool']}'>↗</a> {r['pool'][:8]}</td></tr>"
        for r in rows)
    return ("<div class='w'><table><tr><th>Протокол</th><th>Сеть</th><th>Актив</th><th>Сейчас</th>"
            "<th>7д ср.</th><th>Выше планки 7д</th><th>Оценка</th><th>Токенами</th><th>TVL</th>"
            f"<th>id</th></tr>{trs}</table></div>")

@mm.app.route("/pools")
def pools_page():
    if not mm.STATS_TOKEN or request.args.get("t") != mm.STATS_TOKEN:
        return "forbidden", 403
    if request.args.get("fmt") == "json":
        return jsonify({k: ST[k] for k in ("bench", "last", "eth", "l2", "spikes")})
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Пулы v2</title>
<style>body{{font-family:-apple-system,sans-serif;margin:8px;background:#111;color:#eee}}
.w{{overflow-x:auto}} table{{border-collapse:collapse;font-size:12px;white-space:nowrap}}
th,td{{border-bottom:1px solid #333;padding:4px 6px;text-align:right}} a{{color:#6cf}}
td:first-child,th:first-child{{text-align:left}} th{{background:#222}} tr.b td{{color:#6cf}}
p{{color:#aaa;font-size:13px}} h4{{margin:14px 0 4px}}</style></head><body>
<h3>Пулы стейблкоинов — планка Aave {ST['bench']}%</h3>
<p>«Оценка» = меньшее из «сейчас» и «7д ср.» минус штрафы за риск. Ошибка: {ST['error'] or 'нет'}</p>
<h4>💰 Для крупных сумм (Ethereum)</h4>{table(ST['eth'])}
<h4>🪙 Для малых сумм (L2 — газ копеечный)</h4>{table(ST['l2'])}
<h4>⚡ Всплески сейчас</h4>{table(ST['spikes'][:10])}
<h4>Все пулы по оценке</h4>{table(ST['rows'][:60])}</body></html>"""

threading.Thread(target=loop, daemon=True).start()