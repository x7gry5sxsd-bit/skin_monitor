# bx_cycles.py — v2: сеть обменов BingX без ограничения длины цепочки (Беллман-Форд), только данные
import os, time, threading, logging, math
import requests
from flask import jsonify, request
import mm_scanner as mm

def env(k, d=""):
    return os.getenv(k, d).strip()

BASE = "https://open-api.bingx.com"
FEE = float(env("BX_FEE", "0.001"))                 # комиссия за один обмен (тейкер 0.1%)
SCAN_SEC = float(env("BX_SCAN_SEC", "30"))           # проверка всей сети каждые 30 секунд
MIN_SIZE = float(env("BX_MIN_SIZE_USD", "20"))       # алерт, если цикл вмещает от $20
MAX_CYCLES = int(env("BX_MAX_CYCLES", "20"))
log = logging.getLogger("bx")
ST = {"cycles": [], "raw": [], "pairs": 0, "coins": 0, "quotes": {}, "last": 0, "error": "",
      "seen": {}, "sent": {}, "scans": 0, "hit_scans": 0, "raw_scans": 0, "best": None,
      "best_raw": None, "calc_sec": 0, "report_ts": time.time()}

def tickers():
    d = requests.get(f"{BASE}/openApi/spot/v1/ticker/24hr",
                     params={"timestamp": int(time.time() * 1000)}, timeout=20).json()
    out = []
    for x in (d.get("data") if isinstance(d, dict) else None) or []:
        sym = x.get("symbol") or ""
        if "-" not in sym:
            continue
        b, qt = sym.split("-", 1)
        try:
            bid, ask = float(x.get("bidPrice") or 0), float(x.get("askPrice") or 0)
            bq, aq = float(x.get("bidQty") or 0), float(x.get("askQty") or 0)
        except (TypeError, ValueError):
            continue
        if bid > 0 and ask >= bid:
            out.append((b, qt, bid, ask, bq, aq))
    return out

def build(rows, fee):
    nodes, edges = set(), []
    for b, qt, bid, ask, bq, aq in rows:
        nodes.update((b, qt))
        rb, rs = (1 / ask) * (1 - fee), bid * (1 - fee)
        edges.append((qt, b, rb, -math.log(rb), aq * ask, f"{b}-{qt}", "купить", ask))
        edges.append((b, qt, rs, -math.log(rs), bq, f"{b}-{qt}", "продать", bid))
    return sorted(nodes), edges

def neg_cycles(nodes, edges):
    """Беллман-Форд от «виртуального старта» ко всем монетам: любые прибыльные петли любой длины."""
    idx = {n: i for i, n in enumerate(nodes)}
    n = len(nodes)
    E = [(idx[e[0]], idx[e[1]], e[3], k) for k, e in enumerate(edges)]
    dist, pred = [0.0] * n, [-1] * n
    for _ in range(n - 1):
        changed = False
        for u, v, w, k in E:
            if dist[u] + w < dist[v] - 1e-12:
                dist[v], pred[v], changed = dist[u] + w, k, True
        if not changed:
            break
    found, seen = [], set()
    for u, v, w, k in E:
        if dist[u] + w >= dist[v] - 1e-12:
            continue
        pred[v] = k
        x = v
        for _ in range(n):                                   # уходим внутрь петли
            if pred[x] < 0:
                break
            x = idx[edges[pred[x]][0]]
        cyc, y = [], x
        while pred[y] >= 0 and len(cyc) <= n:
            cyc.append(pred[y])
            y = idx[edges[pred[y]][0]]
            if y == x:
                break
        if y != x or not cyc:
            continue
        cyc.reverse()
        key = frozenset(cyc)
        if key not in seen:
            seen.add(key)
            found.append(cyc)
            if len(found) >= MAX_CYCLES:
                break
    return found

def describe(cyc, edges, usd):
    starts = [i for i, k in enumerate(cyc) if edges[k][0] in ("USDT", "USDC")]
    if starts:
        cyc = cyc[starts[0]:] + cyc[:starts[0]]              # начинаем с доллара, если он в петле
    mult, cap = 1.0, float("inf")
    for k in cyc:
        e = edges[k]
        cap = min(cap, e[4] / mult) if e[4] > 0 else 0.0
        mult *= e[2]
    start = edges[cyc[0]][0]
    size = cap * usd.get(start, 0) if cap != float("inf") else 0.0
    path = " → ".join(f"{edges[k][6]} {edges[k][5]}" for k in cyc)
    return {"profit_pct": round((mult - 1) * 100, 4), "hops": len(cyc), "start": start,
            "size_usd": round(size, 2), "path": path}

def scan():
    rows = tickers()
    if not rows:
        ST["error"] = "нет данных BingX"
        return
    t0 = time.time()
    usd = {"USDT": 1.0, "USDC": 1.0}
    for b, qt, bid, ask, *_ in rows:
        if qt == "USDT":
            usd[b] = (bid + ask) / 2
    res = {}
    for mode, fee in (("real", FEE), ("raw", 0.0)):
        nodes, edges = build(rows, fee)
        res[mode] = sorted((describe(c, edges, usd) for c in neg_cycles(nodes, edges)),
                           key=lambda r: -r["profit_pct"])
    now = time.time()
    quotes = {}
    for _, qt, *_ in rows:
        quotes[qt] = quotes.get(qt, 0) + 1
    ST.update(pairs=len(rows), coins=len(nodes), last=int(now), error="", raw=res["raw"][:10],
              calc_sec=round(now - t0, 2),
              quotes=dict(sorted(quotes.items(), key=lambda kv: -kv[1])[:10]))
    ST["scans"] += 1
    ST["raw_scans"] += bool(res["raw"])
    ST["hit_scans"] += any(c["size_usd"] >= MIN_SIZE for c in res["real"])
    keep = {}
    for c in res["real"]:
        first = ST["seen"].get(c["path"], now)
        keep[c["path"]] = first
        c["alive_sec"] = int(now - first)
        if (c["size_usd"] >= MIN_SIZE and c["alive_sec"] >= 2 * SCAN_SEC
                and now - ST["sent"].get(c["path"], 0) > 3600):
            ST["sent"][c["path"]] = now
            mm.tg(f"🔄 Цикл BingX +{c['profit_pct']:.3f}% после комиссий | {c['hops']} обменов | "
                  f"вмещает ~${c['size_usd']:.0f} | живёт {c['alive_sec']} с\n{c['path']}")
        if not ST["best"] or c["profit_pct"] > ST["best"]["profit_pct"]:
            ST["best"] = dict(c, ts=int(now))
    for c in res["raw"][:1]:
        if not ST["best_raw"] or c["profit_pct"] > ST["best_raw"]["profit_pct"]:
            ST["best_raw"] = dict(c, ts=int(now))
    ST["seen"], ST["cycles"] = keep, res["real"]

def loop():
    time.sleep(120)
    mm.tg("🟢 Сеть обменов BingX v2: вся сеть, цепочки любой длины, каждые 30 секунд")
    while True:
        t0 = time.time()
        try:
            scan()
            if time.time() - ST["report_ts"] >= 86400:
                ST["report_ts"] = time.time()
                s = max(ST["scans"], 1)
                mm.tg(f"📊 Сеть BingX за сутки: {ST['scans']} сканов, монет {ST['coins']}, "
                      f"пар {ST['pairs']}\nС реальным циклом (от ${MIN_SIZE:.0f}): "
                      f"{ST['hit_scans']} ({ST['hit_scans'] / s * 100:.2f}%)\n"
                      f"Щели в ценах без комиссий: {ST['raw_scans']} ({ST['raw_scans'] / s * 100:.1f}%)\n"
                      f"Лучший реальный: {ST['best']}\nЛучший без комиссий: {ST['best_raw']}")
        except Exception as e:
            ST["error"] = type(e).__name__
            log.exception("bx cycles")
        time.sleep(max(5, SCAN_SEC - (time.time() - t0)))

@mm.app.route("/cycles")
def cycles_page():
    if not mm.STATS_TOKEN or request.args.get("t") != mm.STATS_TOKEN:
        return "forbidden", 403
    return jsonify({k: ST[k] for k in ("last", "error", "calc_sec", "pairs", "coins", "quotes",
                                       "scans", "hit_scans", "raw_scans", "best", "best_raw",
                                       "cycles", "raw")})

threading.Thread(target=loop, daemon=True).start()