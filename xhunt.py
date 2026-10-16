# xhunt.py — «охотник»: ловит НАЧАЛО всплеска окон и заходит сразу. Каждую минуту снимает сделки бумаги
#   (xinv + xinvx, маршруты на HTX) в 10-минутные корзины. Всплеск = за последние XA_HUNT_LOOK_MIN минут
#   ≥ XA_HUNT_MIN_TRADES сделок минимум в 2 разных корзинах и прибыль ≥ $0.5. Заход — покупка запаса СРАЗУ на HTX
#   (лимит не дороже лучшей цены +1%), магазин начинает продавать через минуту, автохедж открывает шорт.
#   Выход — нет ни бумажных, ни живых сделок XA_HUNT_QUIET_MIN минут: мягкий выход (в окна, через
#   XA_HUNT_EXIT_H ч — порциями). Слоты охотника — XA_HUNT_SLOTS. Деньги — USDT на HTX (держим XE_HTX_KEEP).
import os, sys, time, json, threading, logging
from flask import jsonify, request
import mm_scanner as mm
import xpaper
import xexec_api as api

def env(k, d=""):
    return os.getenv(k, d).strip()

ON = env("XA_HUNT", "1") == "1"
SLOTS = int(env("XA_HUNT_SLOTS", "1"))
SLOT = float(env("XA_HUNT_USD", env("XA_SLOT_USD", "40")))
LOOK = float(env("XA_HUNT_LOOK_MIN", "30"))       # окно поиска всплеска
MIN_T = int(env("XA_HUNT_MIN_TRADES", "3"))
MIN_NET = float(env("XA_HUNT_MIN_NET", "0.5"))
QUIET = float(env("XA_HUNT_QUIET_MIN", "45"))     # столько минут без сделок — всплеск закончился
EXIT_H = float(env("XA_HUNT_EXIT_H", "0.5"))      # мягкий выход: столько часов в окна, потом порциями
HOLD = float(env("XA_HUNT_HOLD_MIN", "30"))       # минимум держим
log = logging.getLogger("xhunt")
H = {"fb": {}, "prev": {}, "log": [], "last_live": {}}

def sources():
    """Бумажные маршруты с продажей на HTX: ключ → [сделок, чистыми $]."""
    out = {}
    I, X = sys.modules.get("xinv"), sys.modules.get("xinvx")
    for k, c in (I.S.get("c", {}).items() if I else []):
        v = I.COINS.get(k)
        if v and v[1] == "htx":
            out[k] = [c["trades"], c["pnl"] - c["wd"]]
    for k, c in (X.S.get("c", {}).items() if X else []):
        if k.endswith(">htx"):
            out[k] = [c["trades"], c["pnl"] - c["wdsum"]]
    return out

def snap(now):
    b = str(int(now // 600) * 600)
    for key, cur in sources().items():
        prev = H["prev"].get(key)
        H["prev"][key] = cur
        if not prev:
            continue
        dt, dn = cur[0] - prev[0], cur[1] - prev[1]
        if dt <= 0 and abs(dn) < 1e-9:
            continue
        coin = key.split(":")[0].split("@")[0]
        x = H["fb"].setdefault(coin, {}).setdefault(b, [0, 0.0])
        x[0], x[1] = x[0] + max(dt, 0), x[1] + dn
    old = now - 3 * 3600
    for coin in list(H["fb"]):
        H["fb"][coin] = {k: v for k, v in H["fb"][coin].items() if float(k) >= old}
        if not H["fb"][coin]:
            H["fb"].pop(coin)

def burst(coin, now, minutes):
    since = now - minutes * 60
    bs = [v for k, v in H["fb"].get(coin, {}).items() if float(k) + 600 > since]
    return sum(b[0] for b in bs), sum(b[1] for b in bs), sum(1 for b in bs if b[0] > 0)

def live_recent(coin, now, minutes):
    SH = sys.modules.get("xshop")
    c = (SH.S.get("c", {}).get(coin) or {}) if SH else {}
    return any(d[0] >= now - minutes * 60 for d in c.get("done") or [])

def say(t):
    mm.tg("🎯 " + t)

def hunt_enter(coin, AL, now):
    net, fee = AL.route_net(coin)
    venues, extra = (["mexc"] if net and AL.whitelisted(coin, net) else []), {}
    if coin in AL.KC_READY:
        cid, kfee = AL.kc_net(coin)
        if cid:
            venues.append("kucoin")
            extra = {"knet": cid, "wd_kucoin": kfee}
    if not venues:
        return False                                   # докупать негде (нет адреса в белом списке) — не берём
    b = xpaper.book("htx", coin)
    if not b:
        return False
    ask = b[1][0][0]
    oid = api.hx_buy_ioc(f"{coin.lower()}usdt", SLOT / ask, ask * 1.01).get("data")
    time.sleep(1.5)
    od = api.hx_order(oid).get("data") or {}
    got = float(od.get("field-amount") or 0)
    if got <= 0:
        return False
    AL.A["coins"][coin] = {"mode": "active", "since": now, "managed": True, "hunt": True, "venues": venues,
                           "net": net or "ETH", "batch": round(SLOT * 0.9, 2), "wd": round(fee or 0.3, 3), **extra}
    AL.save()
    H["log"] = (H["log"] + [[int(now), "заход", coin]])[-30:]
    say(f"Всплеск по {coin} → купил {got:.2f} {coin} сразу на HTX (~${SLOT:.0f}). Магазин продаёт в окна, докупка: "
        f"{', '.join(v.upper() for v in venues)}. Автохедж откроет шорт")
    return True

def step(now):
    AL = sys.modules.get("xalloc")
    if not AL or not AL.A or "coins" not in AL.A:
        return
    mine = [c for c, v in AL.A["coins"].items() if v.get("hunt") and v["mode"] == "active"]
    for c in mine:                                     # всплеск кончился → мягкий выход
        t, n, k = burst(c, now, QUIET)
        if now - AL.A["coins"][c]["since"] >= HOLD * 60 and t == 0 and not live_recent(c, now, QUIET):
            AL.A["coins"][c].update({"mode": "exit", "exit_t": now, "exit_h": EXIT_H})
            AL.save()
            H["log"] = (H["log"] + [[int(now), "выход", c]])[-30:]
            say(f"Всплеск по {c} закончился ({QUIET:.0f} мин без сделок) → мягкий выход: продаём запас в окна, "
                f"через {EXIT_H*60:.0f} мин — остаток порциями")
    if not ON or len(mine) >= SLOTS:
        return
    cash = api.hx_free("USDT")
    HA = sys.modules.get("xahedge")
    marg = HA.margin(env("XH_VENUE", "mexc")) if HA else None
    if cash < SLOT or (marg is not None and marg < SLOT / 2 * 1.1):
        return
    cands = []
    for coin in H["fb"]:
        if coin in AL.A["coins"] or not AL.hedge_ok(coin):
            continue
        t, n, k = burst(coin, now, LOOK)
        if t >= MIN_T and k >= 2 and n >= MIN_NET:
            cands.append((n, coin, t))
    for n, coin, t in sorted(cands, reverse=True)[:3]:
        if hunt_enter(coin, AL, now):
            return

def loop():
    time.sleep(150)
    say(f"Охотник запущен: всплеск = {MIN_T}+ сделок за {LOOK:.0f} мин, заход сразу на HTX по ${SLOT:.0f}, "
        f"выход после {QUIET:.0f} мин тишины | слотов {SLOTS}")
    n = 0
    while True:
        now = time.time()
        try:
            snap(now)
            n += 1
            if n % 2 == 0:
                step(now)
        except Exception as e:
            log.exception("xhunt")
            if time.time() - H.get("err", 0) > 3600:
                H["err"] = time.time()
                mm.tg(f"❗ Охотник: {str(e)[:200]}")
        time.sleep(60)

@mm.app.route("/hunt")
def hunt_page():
    if not (mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN):
        return "forbidden", 403
    now = time.time()
    hot = {c: dict(zip(("trades", "net", "buckets"), burst(c, now, LOOK))) for c in H["fb"]}
    hot = dict(sorted(((c, v) for c, v in hot.items() if v["trades"]), key=lambda kv: -kv[1]["net"])[:15])
    return jsonify({"on": ON, "slots": SLOTS, "slot_usd": SLOT, "look_min": LOOK, "quiet_min": QUIET,
                    "hot_now": hot, "log": H["log"][-10:]})

threading.Thread(target=loop, daemon=True).start()
