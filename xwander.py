# xwander.py — «странник» на бумаге: капитал живёт на любой бирже (MEXC/HTX/Gate/KuCoin) и каждый раз делает
#   лучший следующий шаг оттуда, где лежит: купил монету → «перевёл» (с настоящей задержкой сети) → продал на другой
#   бирже → ищет новую ногу уже оттуда. Так сами собой складываются прямые сделки, круги и треугольники.
#   Кандидаты ног — из xroute (путь открыт, комиссия вывода учтена), проверка — по живым стаканам на нашу сумму.
#   Только бумага: ничего не покупает. Главная цифра — «ждали / получили» после перевода.
import os, re, time, json, threading, logging
from flask import jsonify, request
import mm_scanner as mm
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

POCKETS = env("XW_POCKETS", "mexc:30,htx:30,gate:30,kucoin:30")
MIN_EDGE = float(env("XW_MIN_EDGE", "0.015"))     # нога берётся от +1.5% после всех комиссий на нашу сумму
CONFIRM = int(env("XW_CONFIRM", "2"))
STEP = float(env("XW_STEP_SEC", "30"))
FAST = {"SUI", "ARB", "SOL", "BASE", "BEP20", "TRC20", "APT", "TON", "OP", "POLYGON", "AVAXC"}
log = logging.getLogger("xwander")
W = {}

def eta_min(net):
    return 3 if net in FAST else (20 if net in ("ERC20", "ETH") else 30)

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'wander_v1'", fetch=True)
    if r:
        W.update(json.loads(r[0][0]))
    if not W.get("p"):
        W["p"] = []
        for i, part in enumerate(POCKETS.split(",")):
            v, usd = part.split(":")
            W["p"].append({"id": i + 1, "venue": v.strip(), "usdt": float(usd), "start": float(usd),
                           "stage": "idle", "legs": 0, "hist": []})
    W.setdefault("legs", [])                          # все ноги: [t, карман, откуда>куда, монета, ждали%, получили%, $]
    W.setdefault("streak", {})
    W.setdefault("t0", time.time())

def save():
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('wander_v1', %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(W),))

def candidates(venue):
    """Ноги из venue куда угодно, которые xroute сейчас видит открытыми: [(куда, монета, сеть, комиссия $)]."""
    xr = __import__("sys").modules.get("xroute")
    out = []
    for leg, items in ((xr.R.get("now") or {}) if xr else {}).items():
        a, b = leg.split(">")
        if a != venue:
            continue
        for it in items:
            m = re.search(r"сеть (\S+?), комиссия вывода \$([\d.]+)", it.get("text", ""))
            if m:
                out.append((b, it["coin"], m.group(1).upper(), float(m.group(2))))
    return out

BK = {}

def book(ex, coin):                                   # стакан с кэшем на один проход (меньше запросов к биржам)
    k = (ex, coin)
    if k not in BK:
        BK[k] = xpaper.book(ex, coin)
    return BK[k]

def price_leg(src, dst, coin, usd, fee_usd):
    """Проверка по живым стаканам на нашу сумму: (ждём %, монет купим, монет дойдёт) или None."""
    bs, bd = book(src, coin), book(dst, coin)
    if not bs or not bd:
        return None
    qty = xpaper.buy_qty(bs[1], usd, src)
    if not qty:
        return None
    arrive = qty - fee_usd / bs[1][0][0]               # комиссия вывода — в монетах
    got = xpaper.sell_usdt(bd[0], arrive, dst) if arrive > 0 else None
    return ((got / usd - 1), qty, arrive) if got else None

def step_idle(p, now):
    best = None
    for dst, coin, net, fee in candidates(p["venue"]):
        r = price_leg(p["venue"], dst, coin, p["usdt"], fee)
        key = f"{p['id']}:{p['venue']}>{dst}:{coin}"
        if not r or r[0] < MIN_EDGE:
            W["streak"].pop(key, None)
            continue
        W["streak"][key] = W["streak"].get(key, 0) + 1
        if W["streak"][key] >= CONFIRM and (not best or r[0] > best[0]):
            best = (r[0], dst, coin, net, r[2])
    if not best:
        return
    edge, dst, coin, net, arrive = best
    W["streak"] = {k: v for k, v in W["streak"].items() if not k.startswith(f"{p['id']}:")}
    p.update({"stage": "transfer", "dst": dst, "coin": coin, "net": net, "arrive": arrive, "exp": round(edge * 100, 2),
              "t_in": now, "eta": now + eta_min(net) * 60, "spent": p["usdt"]})

def step_transfer(p, now):
    if now < p["eta"]:
        return
    bd = book(p["dst"], p["coin"])
    got = xpaper.sell_usdt(bd[0], p["arrive"], p["dst"]) if bd else None
    if not got:
        if now - p["eta"] > 3600:                      # продать не по чему час — считаем по нулю, фиксируем
            got = 0.0
        else:
            return
    real = (got / p["spent"] - 1) * 100
    leg = f"{p['venue']}>{p['dst']}"
    W["legs"] = (W["legs"] + [[int(now), p["id"], leg, p["coin"], p["exp"], round(real, 2),
                               round(got - p["spent"], 3), p["net"]]])[-300:]
    p["hist"] = (p["hist"] + [p["venue"]])[-6:]
    circle = p["dst"] in p["hist"]                     # вернулись на биржу, где уже были — круг
    p.update({"venue": p["dst"], "usdt": got, "stage": "idle", "legs": p["legs"] + 1})
    mm.tg(f"🧭 [бумага] Странник #{p['id']}: {leg} {p['coin']} ({p['net']}) — ждали +{p['exp']}%, "
          f"получили {real:+.2f}% → ${got:.2f}{' 🔁 круг замкнулся' if circle else ''}")

def summary():
    legs = W.get("legs", [])
    by = {}
    for r in legs:
        s = by.setdefault(r[2], {"legs": 0, "wins": 0, "pnl": 0.0, "exp": 0.0, "real": 0.0, "coins": {}})
        s["legs"] += 1
        s["wins"] += r[6] > 0
        s["pnl"] += r[6]
        s["exp"] += r[4]
        s["real"] += r[5]
        s["coins"][r[3]] = s["coins"].get(r[3], 0) + 1
    routes = {k: {"legs": v["legs"], "wins": v["wins"], "pnl": round(v["pnl"], 2),
                  "avg_expected_pct": round(v["exp"] / v["legs"], 2), "avg_real_pct": round(v["real"] / v["legs"], 2),
                  "coins": sorted(v["coins"].items(), key=lambda kv: -kv[1])[:4]}
              for k, v in sorted(by.items(), key=lambda kv: -kv[1]["pnl"])}
    pk = [{"id": p["id"], "now_on": p["venue"], "stage": p["stage"], "usdt": round(p["usdt"], 2),
           "result": round(p["usdt"] - p["start"], 2) if p["stage"] == "idle" else "в пути",
           "legs": p["legs"], "route": " > ".join(p["hist"] + [p["venue"]])} for p in W.get("p", [])]
    done = [p for p in W.get("p", []) if p["stage"] == "idle"]
    return {"hours": round((time.time() - W.get("t0", time.time())) / 3600, 1), "legs": len(legs),
            "wins": sum(1 for r in legs if r[6] > 0), "pnl": round(sum(r[6] for r in legs), 2),
            "avg_expected_pct": round(sum(r[4] for r in legs) / len(legs), 2) if legs else None,
            "avg_real_pct": round(sum(r[5] for r in legs) / len(legs), 2) if legs else None,
            "pockets_result": round(sum(p["usdt"] - p["start"] for p in done), 2),
            "routes": routes, "pockets": pk, "last": legs[-8:]}

def text():
    s = summary()
    if not s["legs"]:
        return "🧭 Странник (бумага): ног пока не было"
    top = list(s["routes"].items())[:3]
    return (f"🧭 Странник (бумага): {s['legs']} ног, в плюсе {s['wins']}, {s['pnl']:+.2f}$ | "
            f"ждали в среднем +{s['avg_expected_pct']}%, получили {s['avg_real_pct']:+}% | лучшие: "
            + ", ".join(f"{k} {v['pnl']:+.2f}$ ({v['legs']})" for k, v in top))

def loop():
    time.sleep(240)
    load()
    mm.tg(f"🧭 [бумага] Странник запущен: карманы {POCKETS}, нога от +{MIN_EDGE*100:.1f}%")
    while True:
        now = time.time()
        BK.clear()
        try:
            for p in W["p"]:
                (step_idle if p["stage"] == "idle" else step_transfer)(p, now)
            save()
        except Exception:
            log.exception("xwander")
        time.sleep(STEP)

@mm.app.route("/wander")
def wander_page():
    if not (mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN):
        return "forbidden", 403
    return jsonify(summary())

threading.Thread(target=loop, daemon=True).start()
