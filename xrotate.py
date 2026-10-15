# xrotate.py — бумажная ротация монет магазина. Каждую минуту снимает с бумаги (xinv) сделки и прибыль по монетам
#   в почасовые корзины (72 ч). Рейтинг: поток / волна / спит. Два «теневых магазина» по 2 места сами меняют монеты
#   по правилам («быстрые» — окно 12 ч, «медленные» — 24 ч) с учётом цены замены; рядом — без ротации (US+AXS).
#   Ничего не покупает. /rotate?t=... — рейтинг, тени, советы.
import os, sys, time, json, threading, logging
from flask import jsonify, request
import mm_scanner as mm
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

START = [c.strip() for c in env("XR_START", "US,AXS").split(",") if c.strip()]   # с чего стартуют тени
COST = float(env("XR_COST", "1.5"))              # цена одной замены, $ (продать запас, купить, вывести, шорт)
ADV = float(env("XR_ADV", "2"))                  # новая монета должна давать в сутки больше на COST×ADV
RULES = {"быстрые": {"win": 12, "hours": 4, "net": 2.0, "hold": 12},
         "медленные": {"win": 24, "hours": 6, "net": 3.0, "hold": 24}}
log = logging.getLogger("xrotate")
R = {}

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'rotate_v1'", fetch=True)
    if r:
        R.update(json.loads(r[0][0]))
    R.setdefault("h", {})                          # монета -> {час: [сделок, чистыми $, упущено]}
    R.setdefault("prev", {})
    R.setdefault("t0", time.time())
    R.setdefault("static", {"coins": START, "pnl": 0.0})
    for name in RULES:
        R.setdefault(name, {"slots": [{"coin": c, "since": time.time()} for c in START], "pnl": 0.0, "rot": [],
                            "last_rot": 0})

def save():
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('rotate_v1', %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(R),))

def snap(now):
    I = sys.modules.get("xinv")
    if not I:
        return
    hb = str(int(now // 3600) * 3600)
    for coin, c in I.S.get("c", {}).items():
        cur = [c["trades"], c["pnl"] - c["wd"], c["miss"]]
        prev = R["prev"].get(coin)
        R["prev"][coin] = cur
        if not prev:
            continue
        d = [cur[0] - prev[0], cur[1] - prev[1], cur[2] - prev[2]]
        if not any(d):
            continue
        b = R["h"].setdefault(coin, {}).setdefault(hb, [0, 0.0, 0])
        b[0], b[1], b[2] = b[0] + d[0], round(b[1] + d[1], 4), b[2] + d[2]
        if R["static"] and coin in R["static"]["coins"]:
            R["static"]["pnl"] += d[1]
        for name in RULES:
            if any(s["coin"] == coin for s in R[name]["slots"]):
                R[name]["pnl"] += d[1]
    old = now - 72 * 3600
    for coin in R["h"]:
        R["h"][coin] = {k: v for k, v in R["h"][coin].items() if float(k) >= old}

def stats(coin, hours, now):
    since = now - hours * 3600
    bs = [v for k, v in R["h"].get(coin, {}).items() if float(k) >= since]
    return {"trades": sum(b[0] for b in bs), "net": round(sum(b[1] for b in bs), 2),
            "missed": sum(b[2] for b in bs), "active_hours": sum(1 for b in bs if b[0] > 0)}

def kind(st, rule):
    if st["trades"] == 0:
        return "💤 спит"
    if st["active_hours"] <= 1:
        return "🌊 волна"
    return "✅ поток" if st["active_hours"] >= rule["hours"] else "⚠️ редко"

def eligible():
    """Монеты, которые магазин может вести: покупка MEXC → продажа HTX и есть фьючерс для хеджа."""
    I, X = sys.modules.get("xinv"), sys.modules.get("xhedge")
    venue = env("XH_VENUE", "mexc")
    try:
        perps = X.perps()[venue] if X else {}
    except Exception:
        perps = {}
    return [c for c, v in (I.COINS.items() if I else []) if "@" not in c and v[0] == "mexc" and v[1] == "htx"
            and (not perps or c in perps)]

def decide(now):
    ok = eligible()
    for name, rule in RULES.items():
        sh = R[name]
        if now - sh["last_rot"] < rule["hold"] * 3600:
            continue
        busy = {s["coin"] for s in sh["slots"]}
        cands = []
        for c in ok:
            st = stats(c, rule["win"], now)
            if c not in busy and st["active_hours"] >= rule["hours"] and st["net"] >= rule["net"]:
                cands.append((st["net"] * 24 / rule["win"], c))
        if not cands:
            continue
        rate_new, new = max(cands)
        weakest = None
        for s in sh["slots"]:
            st = stats(s["coin"], rule["win"], now)
            rate = st["net"] * 24 / rule["win"]
            held = now - s["since"] >= rule["hold"] * 3600
            if held and (st["trades"] < 2 or rate_new >= 3 * max(rate, 0.01)) and (not weakest or rate < weakest[0]):
                weakest = (rate, s)
        if weakest and rate_new - weakest[0] > COST * ADV:
            old = weakest[1]["coin"]
            weakest[1].update({"coin": new, "since": now})
            sh["pnl"] -= COST
            sh["last_rot"] = now
            sh["rot"] = (sh["rot"] + [[int(now), old, new, round(weakest[0], 2), round(rate_new, 2)]])[-20:]
            mm.tg(f"🔄 [бумага] Тень «{name}»: {old} → {new} (было ≈${weakest[0]:.2f}/сутки, стало ≈${rate_new:.2f}/сутки, "
                  f"замена −${COST:.2f})")

def rating(now, hours=12):
    rule = {"hours": 4}
    out = {}
    for c in eligible():
        st = stats(c, hours, now)
        out[c] = {**st, "kind": kind(st, rule), "per_day": round(st["net"] * 24 / hours, 2)}
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["net"]))

def text():
    now = time.time()
    if not R:
        return "🔄 Ротация: данные копятся"
    rt = rating(now)
    shadows = ", ".join(f"тень «{n}» {R[n]['pnl']:+.2f}$ (замен {len(R[n]['rot'])})" for n in RULES)
    top = ", ".join(f"{c} {v['kind']} {v['net']:+.2f}$" for c, v in list(rt.items())[:5])
    live = ", ".join(f"{c} {rt.get(c, {}).get('kind', '—')}" for c in R["static"]["coins"])
    return (f"🔄 РОТАЦИЯ (бумага, {round((now - R['t0']) / 3600)} ч): {shadows}, без ротации {R['static']['pnl']:+.2f}$\n"
            f"  за 12 ч: {top}\n  наши монеты: {live}")

def loop():
    time.sleep(200)
    load()
    n = 0
    while True:
        now = time.time()
        try:
            snap(now)
            n += 1
            if n % 30 == 0:
                decide(now)
            if n % 5 == 0:
                save()
        except Exception:
            log.exception("xrotate")
        time.sleep(60)

@mm.app.route("/rotate")
def rotate_page():
    if not (mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN):
        return "forbidden", 403
    now = time.time()
    return jsonify({"hours": round((now - R.get("t0", now)) / 3600, 1), "text": text() if R else None,
                    "rating_12h": rating(now) if R else {},
                    "shadows": {n: {"slots": [s["coin"] for s in R[n]["slots"]], "pnl": round(R[n]["pnl"], 2),
                                    "rotations": R[n]["rot"]} for n in RULES} if R else {},
                    "static": R.get("static")})

threading.Thread(target=loop, daemon=True).start()
