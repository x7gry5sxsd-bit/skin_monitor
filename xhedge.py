# xhedge.py — v4.1 (бумага): «хеджированный перевод» — спот-покупка + шорт фьючерса → перевод →
#             продажа на споте + закрытие шорта. Только быстрые сети и только устойчивые окна.
import os, time, json, re, threading, logging
import requests
import psycopg2
from flask import jsonify, request
import mm_scanner as mm
import xfer
import xarb
from xpaper import book, buy_qty, sell_usdt

def env(k, d=""):
    return os.getenv(k, d).strip()

START = float(env("XH_START_USD", "500"))
TRADE_USD = float(env("XH_TRADE_USD", "50"))
MAX_POS = int(env("XH_MAX_POS", "5"))
MIN_EDGE = float(env("XH_MIN_EDGE", "0.008"))      # от 0.8% после всех комиссий
FEE = float(env("XA_FEE", "0.001"))
PERP_FEE = float(env("XH_PERP_FEE", "0.0006"))
LEV = float(env("XH_LEV", "3"))
FAST_SEC = float(env("XH_FAST_SEC", "15"))
CHECK_N = int(env("XH_CHECK_N", "6"))
MAX_DELAY_MIN = float(env("XH_MAX_DELAY_MIN", "10"))  # перевод дольше 10 минут — не входим
MIN_AGE_SEC = float(env("XH_MIN_AGE_SEC", "180"))     # окно должно продержаться 3+ минуты
NET_MIN = {"SOL": 3, "TRC20": 3, "BEP20": 3, "ARB": 3, "BASE": 3, "OP": 3, "TON": 3,
           "AVAXC": 3, "POLYGON": 5, "ERC20": 12}
DEFAULT_NET_MIN = float(env("XH_DEFAULT_NET_MIN", "15"))
BUFFER_MIN = float(env("XH_BUFFER_MIN", "5"))
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0"
log = logging.getLogger("xhedge")
H, ST, SEEN = {}, {"perp_src": {}, "last": 0, "skipped": {}}, {}

def q(sql, params=(), fetch=False):
    conn = psycopg2.connect(mm.DB_URL, connect_timeout=20)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall() if fetch else None
    finally:
        conn.close()

def load():
    q("CREATE TABLE IF NOT EXISTS paper_xarb(id TEXT PRIMARY KEY, js TEXT)")
    r = q("SELECT js FROM paper_xarb WHERE id = 'hedge_v4'", fetch=True)
    H.update(json.loads(r[0][0]) if r else {
        "cash": START, "pos": [], "trades": 0, "wins": 0, "pnl": 0.0, "gap": 0.0, "hedge": 0.0,
        "t0": time.time(), "eq": START, "log": [], "report_ts": time.time()})

def save():
    q("""INSERT INTO paper_xarb(id, js) VALUES ('hedge_v4', %s)
         ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(H),))

def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0

def note(coin, what, usd, pct=None):
    H["log"] = (H["log"] + [[int(time.time()), coin, what, round(usd, 3), pct]])[-50:]

def skip(reason):
    ST["skipped"][reason] = ST["skipped"].get(reason, 0) + 1

def perps():
    out = {}
    def add(coin, venue, b, a):
        if b > 0 and a >= b:
            out.setdefault(coin, {})[venue] = (b, a)
    srcs = {
        "mexc": lambda: [(x.get("symbol", ""), x.get("bid1"), x.get("ask1")) for x in
                         S.get("https://contract.mexc.com/api/v1/contract/ticker", timeout=15).json().get("data") or []],
        "bitget": lambda: [(x.get("symbol", ""), x.get("bidPr"), x.get("askPr")) for x in
                           S.get("https://api.bitget.com/api/v2/mix/market/tickers",
                                 params={"productType": "USDT-FUTURES"}, timeout=15).json().get("data") or []],
        "gate": lambda: [(x.get("contract", ""), x.get("highest_bid"), x.get("lowest_ask")) for x in
                         S.get("https://api.gateio.ws/api/v4/futures/usdt/tickers", timeout=15).json()],
        "bingx": lambda: [(x.get("symbol", ""), x.get("bidPrice"), x.get("askPrice")) for x in
                          S.get("https://open-api.bingx.com/openApi/swap/v2/quote/ticker", timeout=15,
                                params={"timestamp": int(time.time() * 1000)}).json().get("data") or []],
    }
    for venue, fn in srcs.items():
        try:
            n = 0
            for sym, b, a in fn():
                coin = re.sub(r"[-_]?USDT$", "", sym)
                if coin and coin != sym:
                    add(coin, venue, f(b), f(a))
                    n += 1
            ST["perp_src"][venue] = f"ok {n}"
        except Exception as e:
            ST["perp_src"][venue] = f"ошибка {type(e).__name__}"
    return out

def try_enter(r, now, perp):
    coin = r["coin"]
    if len(H["pos"]) >= MAX_POS or any(p["coin"] == coin for p in H["pos"]):
        return
    if now - SEEN.get(f"{coin}:{r['buy']}>{r['sell']}", now) < MIN_AGE_SEC:
        return skip("окно моложе 3 минут")
    pv = perp.get(coin)
    if not pv:
        return skip("нет фьючерса для хеджа")
    venue = max(pv, key=lambda v: pv[v][0])
    pbid = pv[venue][0]
    if abs(pbid / r["bid"] - 1) > 0.1:
        return skip("фьючерс — другой токен")
    rt = xfer.route(r["buy"], r["sell"], coin, r["ask"])
    if rt["state"] != "open":
        return skip("путь закрыт")
    m = re.search(r"сеть ([A-Z0-9]+)", rt["text"])
    net = m.group(1) if m else ""
    delay = NET_MIN.get(net, DEFAULT_NET_MIN) + BUFFER_MIN
    if delay > MAX_DELAY_MIN:
        return skip("медленная сеть")
    wd_usd = rt["fee_usd"] if rt["fee_usd"] is not None else 1.0
    size = min(TRADE_USD, H["cash"] / (1 + 1 / LEV))
    if size < 10:
        return skip("нет свободного капитала")
    ba, bb = book(r["buy"], coin), book(r["sell"], coin)
    if not ba or not bb:
        return skip("нет стакана")
    qty = buy_qty(ba[1], size)
    if not qty:
        return skip("тонкий стакан покупки")
    q_arr = qty - wd_usd / r["ask"]
    exp = sell_usdt(bb[0], q_arr) if q_arr > 0 else None
    if not exp:
        return skip("тонкий стакан продажи")
    edge = (exp - size - 2 * q_arr * pbid * PERP_FEE) / size
    if edge < MIN_EDGE:
        return skip("по стакану меньше 0.8%")
    H["cash"] -= size + size / LEV
    H["pos"].append({"coin": coin, "buy": r["buy"], "sell": r["sell"], "venue": venue, "net": net,
                     "size": size, "q": q_arr, "pent": pbid, "edge": round(edge * 100, 2),
                     "t": now, "ready": now + delay * 60})
    note(coin, f"вход {r['buy']}>{r['sell']} | хедж {venue} | сеть {net or '?'} {delay:.0f} мин",
         0, round(edge * 100, 2))

def settle(p, now, perp):
    bb = book(p["sell"], p["coin"])
    pv = perp.get(p["coin"], {}).get(p["venue"])
    got = sell_usdt(bb[0], p["q"]) if bb else None
    if not got or not pv:
        return False
    hedge = (p["pent"] - pv[1]) * p["q"] - p["q"] * (p["pent"] + pv[1]) * PERP_FEE
    gap = got - p["size"]
    net = gap + hedge
    H["cash"] += p["size"] + p["size"] / LEV + net
    H["trades"] += 1
    H["wins"] += net > 0
    H["pnl"] += net
    H["gap"] += gap
    H["hedge"] += hedge
    note(p["coin"], f"итог {p['buy']}>{p['sell']}: спот {gap:+.2f}$, хедж {hedge:+.2f}$",
         net, round(net / p["size"] * 100, 2))
    return True

def loop():
    time.sleep(200)
    load()
    mm.tg(f"🟢 Бумага v4.1 «хеджированный перевод»: быстрые сети, окна от 3 минут (${H['eq']:.0f})")
    perp, pts = {}, 0.0
    while True:
        t0 = time.time()
        try:
            if t0 - pts > 60:
                perp, pts = perps(), t0
            rows = [x for x in xarb.ST["rows"] if x.get("state") == "open"]
            keys = {f"{x['coin']}:{x['buy']}>{x['sell']}" for x in rows}
            for k in keys:
                SEEN.setdefault(k, t0)
            for k in list(SEEN):
                if k not in keys:
                    SEEN.pop(k)
            n0 = (H["trades"], len(H["pos"]))
            H["pos"] = [p for p in H["pos"] if not (t0 >= p["ready"] and settle(p, t0, perp))]
            for r in rows[:CHECK_N]:
                try_enter(r, t0, perp)
            H["eq"] = H["cash"] + sum(p["size"] + p["size"] / LEV for p in H["pos"])
            ST["last"] = int(t0)
            if (H["trades"], len(H["pos"])) != n0:
                save()
            if t0 - H["report_ts"] >= 86400:
                H["report_ts"] = t0
                save()
                s = summary()
                mm.tg(f"📊 Бумага v4.1 за сутки: капитал ${s['equity']} ({s['pnl']:+.2f}$) | сделок "
                      f"{s['trades']}, в плюс {s['wins']}\nСпот {s['spot_gap']:+.2f}$, хедж {s['hedge']:+.2f}$ | "
                      f"${s['per_day_usd']}/день")
        except Exception:
            log.exception("xhedge")
        time.sleep(max(3, FAST_SEC - (time.time() - t0)))

def summary():
    if not H:
        return {}
    days = max((time.time() - H["t0"]) / 86400, 0.01)
    return {"equity": round(H["eq"], 2), "pnl": round(H["pnl"], 2), "trades": H["trades"],
            "wins": H["wins"], "per_day_usd": round(H["pnl"] / days, 2), "days": round(days, 2),
            "spot_gap": round(H["gap"], 2), "hedge": round(H["hedge"], 2),
            "in_transfer": [{k: p[k] for k in ("coin", "buy", "sell", "venue", "net", "edge")}
                            for p in H["pos"]],
            "last_trades": H["log"][-8:]}

@mm.app.route("/xhedge")
def xhedge_page():
    if not mm.STATS_TOKEN or request.args.get("t") != mm.STATS_TOKEN:
        return "forbidden", 403
    return jsonify({"last": ST["last"], "perp_src": ST["perp_src"], "skipped": ST["skipped"],
                    "paper": summary()})

threading.Thread(target=loop, daemon=True).start()