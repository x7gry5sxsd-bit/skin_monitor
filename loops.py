# loops.py — сканер «стабильных петель» с плечом (только данные, без денег и ключей)
import os, time, threading, logging
import html as _html
import requests
from flask import jsonify, request
import mm_scanner as mm

def env(k, d=""):
    return os.getenv(k, d).strip()

CHAINS = [c.strip() for c in env(
    "LOOP_CHAINS", "Ethereum,Arbitrum,Base,Optimism").split(",") if c.strip()]
LEV = [float(x) for x in env("LOOP_LEV", "2,3").split(",")]
MIN_SPREAD = float(env("LOOP_MIN_SPREAD", "2"))     # залог − займ от 2 п.п.
MIN_TVL = float(env("LOOP_MIN_TVL", "20000000"))
SCAN_H = float(env("LOOP_SCAN_H", "3"))
COLL = ("SUSDE", "SUSDS", "SDAI", "SYRUPUSDC", "SYRUPUSDT", "SFRXUSD", "SCRVUSD")
LENDERS = ("aave", "morpho", "spark", "compound", "euler", "fluid")
log = logging.getLogger("loops")
ST = {"rows": [], "borrow": {}, "last": 0, "error": "", "sent": {}}

def fetch():
    pools = requests.get("https://yields.llama.fi/pools", timeout=90).json().get("data", [])
    meta, coll = {}, []
    for p in pools:
        ch, sym = p.get("chain"), (p.get("symbol") or "").upper()
        proj = (p.get("project") or "").lower()
        if ch not in CHAINS or (p.get("tvlUsd") or 0) < MIN_TVL:
            continue
        if sym in ("USDC", "USDT") and any(k in proj for k in LENDERS):
            meta[p["pool"]] = (proj, ch, sym)
        if sym in COLL or (sym.startswith("PT-") and p.get("stablecoin")):
            coll.append({"proj": proj, "chain": ch, "sym": sym,
                         "apy": round(p.get("apy") or 0, 2),
                         "mean30": round(p.get("apyMean30d") or 0, 2),
                         "tvl": round(p["tvlUsd"] / 1e6, 1)})
    del pools
    lb = requests.get("https://yields.llama.fi/lendBorrow", timeout=90).json()
    borrow = {}
    for x in lb if isinstance(lb, list) else []:
        m = meta.get(x.get("pool"))
        if not m or x.get("borrowable") is False:
            continue
        cost = (x.get("apyBaseBorrow") or 0) - (x.get("apyRewardBorrow") or 0)
        if cost <= 0:
            continue
        proj, ch, sym = m
        if ch not in borrow or cost < borrow[ch][0]:
            borrow[ch] = (round(cost, 2), f"{proj} {sym}")
    return coll, borrow

def scan():
    coll, borrow = fetch()
    rows = []
    for c in coll:
        b = borrow.get(c["chain"])
        if not b:
            continue
        y = min(c["apy"], c["mean30"]) if c["mean30"] > 0 else c["apy"]   # консервативно
        spread = round(y - b[0], 2)
        if spread < MIN_SPREAD:
            continue
        r = dict(c, y=y, borrow=b[0], src=b[1], spread=spread)
        for L in LEV:
            r[f"x{L:g}"] = round(y + (L - 1) * spread, 2)
        rows.append(r)
    top = f"x{LEV[-1]:g}"
    rows.sort(key=lambda r: -r[top])
    ST.update(rows=rows, borrow=borrow, last=int(time.time()), error="")
    for r in rows[:3]:
        key = r["chain"] + r["sym"]
        if time.time() - ST["sent"].get(key, 0) > 86400:
            ST["sent"][key] = time.time()
            lev = " | ".join(f"{L:g}x: {r[f'x{L:g}']}%" for L in LEV)
            mm.tg(f"🔁 Петля: {r['sym']} ({r['proj']}, {r['chain']})\n"
                  f"Залог {r['y']}% − займ {r['borrow']}% ({r['src']}) = спред {r['spread']} п.п.\n"
                  f"{lev}\n⚠️ Проверить: принимается ли залог в этом рынке и его LTV")

def loop():
    time.sleep(1200)                       # стартуем позже сканера пулов — бережём память
    mm.tg("🟢 Сканер петель запущен (только данные)")
    while True:
        t0 = time.time()
        try:
            scan()
            log.info("loops: %d rows, borrow=%s", len(ST["rows"]), ST["borrow"])
        except Exception as e:
            ST["error"] = type(e).__name__
            log.exception("loops")
        time.sleep(max(600, SCAN_H * 3600 - (time.time() - t0)))

@mm.app.route("/loops")
def loops_page():
    if not mm.STATS_TOKEN or request.args.get("t") != mm.STATS_TOKEN:
        return "forbidden", 403
    if request.args.get("fmt") == "json":
        return jsonify({"last": ST["last"], "borrow": ST["borrow"], "rows": ST["rows"][:50]})
    levs = "".join(f"<th>{L:g}x</th>" for L in LEV)
    trs = "".join(
        f"<tr><td>{_html.escape(r['sym'])}</td><td>{r['proj']}</td><td>{r['chain']}</td>"
        f"<td>{r['y']}%</td><td>{r['borrow']}%</td><td>{r['spread']}</td>"
        + "".join(f"<td><b>{r[f'x{L:g}']}%</b></td>" for L in LEV)
        + f"<td>${r['tvl']}M</td></tr>" for r in ST["rows"][:50])
    bor = " · ".join(f"{ch}: {v[0]}% ({v[1]})" for ch, v in ST["borrow"].items())
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Петли</title>
<style>body{{font-family:-apple-system,sans-serif;margin:8px;background:#111;color:#eee}}
.w{{overflow-x:auto}} table{{border-collapse:collapse;font-size:12px;white-space:nowrap}}
th,td{{border-bottom:1px solid #333;padding:4px 6px;text-align:right}} th{{background:#222}}
td:first-child,th:first-child{{text-align:left}} p{{color:#aaa;font-size:13px}}</style></head>
<body><h3>Стабильные петли — доход с плечом</h3>
<p>Самый дешёвый займ по сетям: {bor or 'нет данных'}<br>Ошибка: {ST['error'] or 'нет'}.
Перед входом проверить: принимает ли рынок этот залог, его LTV и запас до ликвидации.</p>
<div class="w"><table><tr><th>Залог</th><th>Протокол</th><th>Сеть</th><th>Доход залога</th>
<th>Займ</th><th>Спред п.п.</th>{levs}<th>TVL</th></tr>{trs}</table></div></body></html>"""

threading.Thread(target=loop, daemon=True).start()