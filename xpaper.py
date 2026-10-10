# xpaper.py — бумажная торговля межбиржевыми окнами: $100, задержка перевода, реальные цены
import os, time, json, logging
import psycopg2
import mm_scanner as mm

def env(k, d=""):
    return os.getenv(k, d).strip()

START = float(env("XP_START_USD", "100"))             # бумажный капитал каждого счёта
TRADE_USD = float(env("XP_TRADE_USD", "50"))           # сумма одной сделки
UNKNOWN_SIZE_USD = float(env("XP_UNKNOWN_SIZE_USD", "25"))  # если объём стакана неизвестен
DELAY_MIN = float(env("XP_DELAY_MIN", "15"))           # время перевода между биржами
FEE = float(env("XA_FEE", "0.001"))
DEFAULT_WD = float(env("XP_DEFAULT_WD_USD", "1.0"))    # комиссия вывода, если не известна
MIN_AFTER = float(env("XA_MIN_NET", "0.005")) * 100
log = logging.getLogger("xpaper")
ACC = {}

def q(sql, params=(), fetch=False):
    conn = psycopg2.connect(mm.DB_URL, connect_timeout=20)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall() if fetch else None
    finally:
        conn.close()

q("CREATE TABLE IF NOT EXISTS paper_xarb(id TEXT PRIMARY KEY, js TEXT)")

def new_acc():
    return {"cash": START, "pos": [], "closed": 0, "wins": 0, "pnl": 0.0, "t0": time.time(), "log": []}

def load():
    for name in ("open", "half"):
        r = q("SELECT js FROM paper_xarb WHERE id = %s", (name,), fetch=True)
        ACC[name] = json.loads(r[0][0]) if r else new_acc()

def save():
    for name, a in ACC.items():
        q("""INSERT INTO paper_xarb(id, js) VALUES (%s, %s)
             ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (name, json.dumps(a)))

def step(rows, by):
    if not ACC:
        load()
    now, changed = time.time(), False
    for a in ACC.values():                                    # 1) завершаем переводы
        keep = []
        for p in a["pos"]:
            quote = by.get(p["coin"], {}).get(p["sell"])
            if quote:
                p["last_bid"] = quote[0]
            if now >= p["ready"] and p.get("last_bid"):
                proceeds = p["qty"] * p["last_bid"] * (1 - FEE) - p["wd"]
                pnl = proceeds - p["cost"]
                a["cash"] += proceeds
                a["closed"] += 1
                a["wins"] += pnl > 0
                a["pnl"] += pnl
                a["log"] = (a["log"] + [[int(now), p["coin"], f"{p['buy']}>{p['sell']}",
                                         round(pnl, 3), round(pnl / p["cost"] * 100, 2)]])[-50:]
                changed = True
            else:
                keep.append(p)
        a["pos"] = keep
    for r in rows:                                            # 2) входим в новые окна
        st = r.get("state")
        if st not in ("open", "half") or (r.get("after_wd_pct") or -1) < MIN_AFTER:
            continue
        for name in (("open", "half") if st == "open" else ("half",)):
            a = ACC[name]
            if any(p["coin"] == r["coin"] for p in a["pos"]):
                continue
            usd = min(TRADE_USD, r["size_usd"] or UNKNOWN_SIZE_USD, a["cash"])
            if usd < 10:
                continue
            wd = r.get("wd_usd") if r.get("wd_usd") is not None else DEFAULT_WD
            a["cash"] -= usd
            a["pos"].append({"coin": r["coin"], "buy": r["buy"], "sell": r["sell"], "cost": usd,
                             "qty": usd / (r["ask"] * (1 + FEE)), "wd": wd, "t": now,
                             "ready": now + DELAY_MIN * 60, "last_bid": r["bid"],
                             "entry_pct": r["after_wd_pct"]})
            changed = True
    if changed:
        save()

def summary():
    out = {}
    for name, a in ACC.items():
        days = max((time.time() - a["t0"]) / 86400, 0.01)
        eq = a["cash"] + sum(p["cost"] for p in a["pos"])    # в пути — по цене входа
        out[name] = {"equity": round(eq, 2), "pnl": round(a["pnl"], 2), "trades": a["closed"],
                     "wins": a["wins"], "in_transfer": len(a["pos"]),
                     "per_day_usd": round(a["pnl"] / days, 2), "days": round(days, 2),
                     "last_trades": a["log"][-5:]}
    return out