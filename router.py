# router.py — v0 PAPER: самоадаптирующийся роутер капитала (бумажные деньги, без ключей)
import os, time, json, threading, logging
from datetime import datetime, timezone, timedelta
import requests
import psycopg2
from flask import jsonify, request
import mm_scanner as mm

def env(k, d=""):
    return os.getenv(k, d).strip()

COINS = [c.strip().upper() for c in env("ROUTER_COINS", "BTC,ETH,SOL").split(",") if c.strip()]
START = float(env("ROUTER_PAPER_USD", "1000"))       # бумажный стартовый капитал, $
FEE_RT = float(env("ROUTER_FEE_RT", "0.002"))        # вход+выход по 2 ногам мейкером ≈ 0.2%
ENTRY_PP = float(env("ROUTER_ENTRY_PP", "8"))        # вход: funding выше безопасной на 8+ п.п.
MAX_SHARE0 = float(env("ROUTER_MAX_SHARE", "0.5"))   # стартовая доля под арбитраж
MAX_COINS = int(env("ROUTER_MAX_COINS", "2"))
STEP_MIN = float(env("ROUTER_STEP_MIN", "60"))
REPORT_HOUR = int(env("ROUTER_REPORT_HOUR", "9"))
TZ = timezone(timedelta(hours=float(env("TZ_OFFSET", "8"))))
OKX = "https://www.okx.com/api/v5"
log = logging.getLogger("router")
S = requests.Session()
STATE = {}

def q(sql, params=(), fetch=False):
    conn = psycopg2.connect(mm.DB_URL, connect_timeout=20)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall() if fetch else None
    finally:
        conn.close()

q("CREATE TABLE IF NOT EXISTS router_state(id INT PRIMARY KEY, js TEXT)")
q("CREATE TABLE IF NOT EXISTS router_log(ts BIGINT, kind TEXT, text TEXT)")

def load():
    r = q("SELECT js FROM router_state WHERE id = 1", fetch=True)
    if r:
        return json.loads(r[0][0])
    return {"cash": START, "vault": 0.0, "pos": {}, "max_share": MAX_SHARE0, "start": START,
            "t0": time.time(), "ts": time.time(), "hist": [], "adapt_ts": time.time(),
            "report_day": "", "regime": "?", "safe": [0, ""], "fund": {}}

def save(st):
    q("""INSERT INTO router_state(id, js) VALUES (1, %s)
         ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(st),))

def event(kind, text):
    q("INSERT INTO router_log VALUES (%s, %s, %s)", (int(time.time()), kind, text))
    mm.tg(text)

def okx(path, **params):
    try:
        d = S.get(f"{OKX}{path}", params=params, timeout=20).json()
        return (d.get("data") or []) if d.get("code") == "0" else []
    except Exception:
        return []

def funding(coin):
    h = okx("/public/funding-rate-history", instId=f"{coin}-USDT-SWAP", limit=9)
    rates = [float(x.get("realizedRate") or x.get("fundingRate") or 0) for x in h]
    cur = okx("/public/funding-rate", instId=f"{coin}-USDT-SWAP")
    now = float(cur[0].get("fundingRate") or 0) if cur else (rates[0] if rates else 0.0)
    avg3 = sum(rates[:3]) / len(rates[:3]) if rates else now
    return {"now": now * 3 * 365 * 100, "avg": avg3 * 3 * 365 * 100,       # % годовых
            "neg2": len(rates) >= 2 and rates[0] < 0 and rates[1] < 0}

def safe_apy():
    d = okx("/finance/savings/lending-rate-summary", ccy="USDT")
    try:
        if d:
            r = float(d[0].get("estRate") or d[0].get("avgRate"))
            return round(r * 100 if r < 1 else r, 2), "OKX Earn"
    except (TypeError, ValueError):
        pass
    try:
        import pools
        if pools.ST.get("bench"):
            return float(pools.ST["bench"]), "Aave (DeFi)"
    except Exception:
        pass
    return 4.0, "по умолчанию"

def equity(st):
    return st["cash"] + st["vault"] + sum(p["capital"] + p["pnl"] for p in st["pos"].values())

def realized_arb(st):
    rows = [h for h in st["hist"] if h[2] > 0][-336:]               # до 14 дней по часам
    if len(rows) < 24:
        return None
    hours = (rows[-1][0] - rows[0][0]) / 3600 or 1
    cap = sum(h[2] for h in rows) / len(rows)
    return sum(h[1] for h in rows) / cap / hours * 8760 * 100

def step(st):
    now = time.time()
    dt_h = max(0.0, (now - st["ts"]) / 3600)
    st["ts"] = now
    safe, src = safe_apy()
    fund = {c: funding(c) for c in COINS}
    st["vault"] += st["vault"] * safe / 100 / 8760 * dt_h            # 1) начисления
    gain = 0.0
    for c, p in st["pos"].items():
        g = p["notional"] * fund[c]["now"] / 100 / 8760 * dt_h
        p["pnl"] += g
        gain += g
    st["hist"] = (st["hist"] + [[now, gain, sum(p["capital"] for p in st["pos"].values())]])[-400:]
    for c in list(st["pos"]):                                          # 2) выходы
        f = fund[c]
        if f["neg2"] or f["avg"] < safe:
            p = st["pos"].pop(c)
            fee = p["notional"] * FEE_RT / 2
            st["cash"] += p["capital"] + p["pnl"] - fee
            event("exit", f"📤 Выход {c}: funding {f['avg']:.1f}% < безопасной {safe:.1f}% | "
                          f"итог позиции {p['pnl'] - fee:+.2f}$")
    eq = equity(st)                                                    # 3) входы
    room = st["max_share"] * eq - sum(p["capital"] for p in st["pos"].values())
    cands = sorted(((f["avg"], c) for c, f in fund.items() if c not in st["pos"]
                    and f["avg"] >= safe + ENTRY_PP and not f["neg2"]), reverse=True)
    for apr, c in cands:
        slots = MAX_COINS - len(st["pos"])
        if slots <= 0 or room < 20:
            break
        a = min(room / slots, st["cash"] + st["vault"])
        if a < 20:
            break
        take = min(a, st["cash"])
        st["cash"] -= take
        st["vault"] -= a - take
        notional = a / 1.5                                             # спот N + маржа перпа N/2
        st["pos"][c] = {"capital": a, "notional": notional, "since": now, "apr_in": apr,
                        "pnl": -notional * FEE_RT / 2}
        room -= a
        event("enter", f"📥 Вход {c}: funding {apr:.1f}% vs безопасная {safe:.1f}% ({src})\n"
                       f"Капитал {a:.0f}$: спот {notional:.0f}$ + маржа перпа {notional / 2:.0f}$")
    st["vault"] += st["cash"]                                          # 4) остаток — в безопасный путь
    st["cash"] = 0.0
    if now - st["adapt_ts"] >= 86400:                                  # 5) адаптация раз в сутки
        st["adapt_ts"] = now
        arb, old = realized_arb(st), st["max_share"]
        if arb is not None:
            st["max_share"] = (min(0.7, old + 0.05) if arb > safe + ENTRY_PP / 2
                               else max(0.2, old - 0.05) if arb < safe else old)
            if st["max_share"] != old:
                event("adapt", f"🧠 Роутер: доля под арбитраж {old*100:.0f}% → "
                               f"{st['max_share']*100:.0f}% (факт {arb:.1f}% vs безопасная {safe:.1f}%)")
    best = max((f["avg"] for f in fund.values()), default=0)
    st["regime"] = ("🔥 перегрев" if best >= safe + 2 * ENTRY_PP else
                    "⚡ окно для арбитража" if best >= safe + ENTRY_PP else "🧊 затишье")
    st["safe"] = [safe, src]
    st["fund"] = {c: round(f["avg"], 1) for c, f in fund.items()}

def report(st):
    eq = equity(st)
    days = max((time.time() - st["t0"]) / 86400, 0.01)
    ret = (eq / st["start"] - 1) * 100
    pos = "\n".join(f"  • {c}: {p['capital']:.0f}$ | funding при входе {p['apr_in']:.1f}% | "
                    f"заработано {p['pnl']:+.2f}$" for c, p in st["pos"].items()) or "  • нет"
    mm.tg(f"🧭 Роутер (бумага) · {st['regime']}\nКапитал {eq:.2f}$ ({ret:+.2f}% за {days:.0f} дн., "
          f"≈{ret / days * 365:.1f}% годовых)\nБезопасный путь: {st['vault']:.0f}$ под "
          f"{st['safe'][0]}% ({st['safe'][1]})\nАрбитраж (лимит {st['max_share']*100:.0f}%):\n{pos}\n"
          f"Funding сейчас: {', '.join(f'{c} {v}%' for c, v in st['fund'].items())}")

def loop():
    global STATE
    time.sleep(90)
    STATE = load()
    mm.tg(f"🟢 Роутер v0 запущен (бумажный капитал {STATE['start']:.0f}$)")
    while True:
        try:
            step(STATE)
            today = datetime.now(TZ)
            if today.hour >= REPORT_HOUR and STATE["report_day"] != today.strftime("%Y-%m-%d"):
                STATE["report_day"] = today.strftime("%Y-%m-%d")
                report(STATE)
            save(STATE)
        except Exception:
            log.exception("router step")
        time.sleep(STEP_MIN * 60)

@mm.app.route("/router")
def router_page():
    if not mm.STATS_TOKEN or request.args.get("t") != mm.STATS_TOKEN:
        return "forbidden", 403
    st = STATE or {}
    return jsonify({"equity": round(equity(st), 2) if st else None, "regime": st.get("regime"),
                    "safe": st.get("safe"), "max_share": st.get("max_share"),
                    "vault": round(st.get("vault", 0), 2), "positions": st.get("pos"),
                    "funding_apr": st.get("fund")})

threading.Thread(target=loop, daemon=True).start()