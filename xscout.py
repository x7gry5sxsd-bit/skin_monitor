# xscout.py — автофильтр монет: раз в 6 ч берёт монеты, у которых сканер видит окна MEXC/Gate → HTX,
#   и сам проверяет: общая открытая сеть, подтверждения на HTX, комиссия вывода с MEXC, фьючерс на MEXC.
#   Вердикт: «схема 1» (быстрая сеть, дешёвый вывод) / «магазин» (есть фьючерс, вывод дёшев для пачки) / «нет».
import os, sys, time, json, threading, logging
from flask import jsonify, request
import requests
import mm_scanner as mm
import xpaper, xfer

def env(k, d=""):
    return os.getenv(k, d).strip()

EVERY = float(env("SCOUT_HOURS", "6")) * 3600
TOPN = int(env("SCOUT_TOPN", "15"))
FAST = {"SUI", "ARB", "SOL", "BASE", "BEP20", "TRC20", "APT", "TON", "OP", "POLYGON", "AVAXC"}
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0"
log = logging.getLogger("xscout")
R = {"ready": [], "t": 0}

def futures():
    try:
        d = S.get("https://contract.mexc.com/api/v1/contract/detail", timeout=20).json()
        return {x.get("symbol", "").split("_")[0] for x in d.get("data") or [] if x.get("symbol", "").endswith("_USDT")}
    except Exception:
        return set()

def htx_chains(coin):
    d = S.get("https://api.huobi.pro/v2/reference/currencies", params={"currency": coin.lower()}, timeout=15).json()
    out = {}
    for cur in d.get("data") or []:
        for ch in cur.get("chains") or []:
            name = xfer.norm(ch.get("displayName") or ch.get("baseChainProtocol") or ch.get("chain"))
            out[name] = {"dep": ch.get("depositStatus") == "allowed",
                         "conf": int(ch.get("numOfConfirmations") or 0)}
    return out

def candidates():
    seen = {}
    xr, xa = sys.modules.get("xroute"), sys.modules.get("xarb")
    if xr:
        for leg in ("mexc>htx", "gate>htx"):
            for c, n in xr.R.get("legs", {}).get(leg, {}).get("coins", {}).items():
                seen[c] = seen.get(c, 0) + n
    if xa:
        for c, v in xa.ideal_summary().get("top_coins", []):
            seen[c] = seen.get(c, 0) + 1
    return [c for c, _ in sorted(seen.items(), key=lambda kv: -kv[1])[:TOPN]]

def price(coin):
    xa = sys.modules.get("xarb")
    q = ((xa.ST.get("by") or {}).get(coin) or {}) if xa else {}
    for ex in ("mexc", "htx", "gate"):
        if ex in q and q[ex][1] > 0:
            return q[ex][1]
    return None

def judge(coin, fut):
    px = price(coin)
    mx = xfer.status("mexc", coin) or {}
    hx = htx_chains(coin)
    best = None
    for net, m in mx.items():
        h = hx.get(net)
        if not (m.get("wd") and h and h["dep"] and m.get("fee") is not None and px):
            continue
        fee_usd = m["fee"] * px
        fast = net in FAST and h["conf"] <= 30
        speed = "быстро (~1–3 мин)" if fast else ("~15–20 мин" if h["conf"] <= 64 else f"медленно ({h['conf']} подтв.)")
        cand = {"coin": coin, "net": net, "conf": h["conf"], "speed": speed, "fee_usd": round(fee_usd, 3),
                "fee_pct_15": round(fee_usd / 15 * 100, 2), "fee_pct_100": round(fee_usd / 100 * 100, 2),
                "futures": coin in fut, "fast": fast}
        if best is None or (cand["fast"], -cand["fee_usd"]) > (best["fast"], -best["fee_usd"]):
            best = cand
    if not best:
        return {"coin": coin, "verdict": "нет общей открытой сети MEXC → HTX"}
    v = []
    if best["fast"] and best["fee_pct_15"] <= 1.0:
        v.append("схема 1")
    if best["futures"] and best["fee_pct_100"] <= 0.5:
        v.append("магазин")
    best["verdict"] = " + ".join(v) if v else "не подходит"
    return best

def scan():
    fut = futures()
    out = []
    for c in candidates():
        try:
            out.append(judge(c, fut))
        except Exception as e:
            out.append({"coin": c, "verdict": f"ошибка проверки: {str(e)[:60]}"})
        time.sleep(0.3)
    R["all"], R["t"] = out, time.time()
    R["ready"] = [x for x in out if "схема 1" in x.get("verdict", "") or "магазин" in x.get("verdict", "")]

def text():
    if not R.get("ready"):
        return "🧭 Кандидатов, прошедших фильтр, пока нет"
    return "🧭 КАНДИДАТЫ (прошли фильтр): " + "; ".join(
        f"{x['coin']} [{x['verdict']}] сеть {x['net']}, {x['speed']}, вывод ${x['fee_usd']}"
        f"{', фьючерс ✅' if x['futures'] else ''}" for x in R["ready"][:6])

def loop():
    time.sleep(600)
    while True:
        try:
            scan()
        except Exception:
            log.exception("xscout")
        time.sleep(EVERY)

@mm.app.route("/scout")
def scout_page():
    if not (mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN):
        return "forbidden", 403
    if request.args.get("now") == "1":
        scan()
    return jsonify({"checked_ago_min": round((time.time() - R["t"]) / 60) if R["t"] else None,
                    "ready": R.get("ready", []), "all": R.get("all", [])})

threading.Thread(target=loop, daemon=True).start()