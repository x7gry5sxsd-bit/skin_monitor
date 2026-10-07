# pools.py — сканер доходности пассивных пулов (мосты, лендинг, стабильные пулы DEX)
import os, time, threading, logging, statistics
import html as _html
import requests
from flask import jsonify, request
import mm_scanner as mm

def env(k, d=""):
    return os.getenv(k, d).strip()

WATCH = [w.strip().lower() for w in env(
    "POOLS_WATCH", "stargate,across,aave,morpho,compound,curve,spark,sky").split(",") if w.strip()]
CHAINS = [c.strip() for c in env(
    "POOLS_CHAINS", "Ethereum,Arbitrum,Base,Optimism,Polygon,BSC").split(",") if c.strip()]
MIN_TVL = float(env("POOLS_MIN_TVL", "5000000"))     # пулы от $5M — меньше не трогаем
SPIKE_PP = float(env("POOLS_SPIKE_PP", "3"))         # алерт: на 3+ п.п. выше планки Aave
SCAN_MIN = float(env("POOLS_SCAN_MIN", "60"))
log = logging.getLogger("pools")
ST = {"rows": [], "bench": None, "last": 0, "sent": {}, "error": ""}

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
                     "symbol": p.get("symbol"), "tvl": round((p.get("tvlUsd") or 0) / 1e6, 1),
                     "base": round(p.get("apyBase") or 0, 2),
                     "reward": round(p.get("apyReward") or 0, 2),
                     "mean30": round(p.get("apyMean30d") or 0, 2),
                     "meta": p.get("poolMeta") or ""})
    return sorted(rows, key=lambda r: -r["base"])

def bench(rows):
    xs = [r["base"] for r in rows if "aave" in r["project"].lower() and r["symbol"] in ("USDC", "USDT")]
    return round(statistics.median(xs), 2) if xs else None

def scan():
    rows = fetch()
    b = bench(rows)
    now = int(time.time())
    ST.update(rows=rows, bench=b, last=now, error="")
    mm.db("INSERT INTO pools_hist VALUES %s",
          [(now, r["pool"], r["project"], r["chain"], r["symbol"], r["tvl"], r["base"],
            r["reward"], r["mean30"]) for r in rows[:300]])
    if b is None:
        return
    for r in rows[:20]:
        spike = r["base"] >= b + SPIKE_PP and r["base"] >= 1.5 * max(r["mean30"], 0.1)
        if spike and time.time() - ST["sent"].get(r["pool"], 0) > 86400:
            ST["sent"][r["pool"]] = time.time()
            mm.tg(f"🏦 Всплеск доходности: {r['project']} · {r['chain']} · {r['symbol']}\n"
                  f"Сейчас {r['base']}% (+{r['reward']}% токенами), обычно {r['mean30']}%\n"
                  f"Планка Aave {b}% | TVL ${r['tvl']}M"
                  + ("\n🌉 Пул моста" if is_bridge(r) else ""))

def loop():
    time.sleep(60)
    mm.tg("🟢 Сканер пассивных пулов запущен (DefiLlama, только данные)")
    while True:
        t0 = time.time()
        try:
            scan()
            log.info("pools: %d rows, bench=%s", len(ST["rows"]), ST["bench"])
        except Exception as e:
            ST["error"] = type(e).__name__
            log.exception("pools loop")
        time.sleep(max(300, SCAN_MIN * 60 - (time.time() - t0)))

@mm.app.route("/pools")
def pools_page():
    if not mm.STATS_TOKEN or request.args.get("t") != mm.STATS_TOKEN:
        return "forbidden", 403
    if request.args.get("fmt") == "json":
        return jsonify({"bench": ST["bench"], "last": ST["last"], "rows": ST["rows"][:100]})
    trs = "".join(
        f"<tr{' class=b' if is_bridge(r) else ''}><td>{_html.escape(r['project'])}</td>"
        f"<td>{r['chain']}</td><td>{_html.escape(r['symbol'])}</td><td>{r['base']}%</td>"
        f"<td>{r['reward']}%</td><td>{r['mean30']}%</td><td>${r['tvl']}M</td></tr>"
        for r in ([x for x in ST["rows"] if is_bridge(x)] + [x for x in ST["rows"] if not is_bridge(x)])[:100])
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Пулы</title>
<style>body{{font-family:-apple-system,sans-serif;margin:8px;background:#111;color:#eee}}
.w{{overflow-x:auto}} table{{border-collapse:collapse;font-size:12px;white-space:nowrap}}
th,td{{border-bottom:1px solid #333;padding:4px 6px;text-align:right}}
td:first-child,th:first-child{{text-align:left}} th{{background:#222}}
tr.b td{{color:#6cf}} p{{color:#aaa;font-size:13px}}</style></head><body>
<h3>Пассивные пулы — планка Aave {ST['bench']}%</h3>
<p>Голубым — пулы мостов. «Реальная» — от комиссий и процентов; «Токенами» — награды,
которые могут подешеветь. Ошибка: {ST['error'] or 'нет'}</p>
<div class="w"><table><tr><th>Протокол</th><th>Сеть</th><th>Актив</th><th>Реальная</th>
<th>Токенами</th><th>Средн. 30д</th><th>TVL</th></tr>{trs}</table></div></body></html>"""

threading.Thread(target=loop, daemon=True).start()