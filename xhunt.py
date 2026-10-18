# xhunt.py v2 — «быстрая полоса»: ловит НАЧАЛО всплеска окон KuCoin → HTX и заходит за ~10 минут.
#   Сигнал: за последние XA_HUNT_LOOK_MIN минут бумага сделала ≥ XA_HUNT_MIN_TRADES сделок минимум в 2 разных
#   10-минутных отрезках, И прямо сейчас живое окно KuCoin → HTX ≥ XA_HUNT_MIN_EDGE (по настоящим стаканам).
#   Заход: покупка на KuCoin (дешёвая сторона, а не пик HTX) из своего резерва USDT, магазин сразу везёт монету
#   на HTX и продаёт в окна, докупая на KuCoin; шорт открывает автохедж. Старые монеты не трогает.
#   Выход: XA_HUNT_QUIET_MIN минут без сделок — продаём в окна, через XA_HUNT_EXIT_H ч остаток порциями.
#   Одна быстрая монета одновременно (XA_HUNT_SLOTS), $XA_HUNT_USD, дневной стоп XA_HUNT_STOP. XA_HUNT=0 — выкл.
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
MIN_EDGE = float(env("XA_HUNT_MIN_EDGE", "0.012"))  # живое окно KuCoin → HTX прямо сейчас (с комиссиями торговли)
STOP = float(env("XA_HUNT_STOP", "2"))             # дневной стоп быстрой полосы, $
log = logging.getLogger("xhunt")
H = {"fb": {}, "prev": {}, "log": [], "last_live": {}}

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'hunt_v1'", fetch=True)
    if r:
        d = json.loads(r[0][0])
        for k in ("fb", "prev", "log", "freed", "asked"):
            if k in d:
                H[k] = d[k]

def save():
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('hunt_v1', %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""",
             (json.dumps({k: H.get(k) for k in ("fb", "prev", "log", "freed", "asked") if k in H}),))

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

def buy_cost(asks, qty, ex):
    left, usd = qty, 0.0
    for p, a in asks:
        take = min(left, a)
        usd += take * p
        left -= take
        if left <= 1e-12:
            return usd * (1 + xpaper.fee(ex))
    return None

def live_edge(coin, usd=15.0):
    """Окно KuCoin → HTX прямо сейчас по живым стаканам: (окно, глубина покупателей HTX в пределах 1%, $)."""
    bh, bk = xpaper.book("htx", coin), xpaper.book("kucoin", coin)
    if not bh or not bk or not bh[0] or not bk[1]:
        return None, 0.0
    q = usd / bk[1][0][0]
    got, cost = xpaper.sell_usdt(bh[0], q, "htx"), buy_cost(bk[1], q, "kucoin")
    floor = bh[0][0][0] * 0.99
    depth = sum(p * a for p, a in bh[0] if p >= floor)
    return ((got / cost - 1) if got and cost else None), depth

def hunt_enter(coin, AL, now):
    """Покупка на KuCoin из резерва быстрой полосы; магазин сам везёт её на HTX и торгует, автохедж — шорт."""
    if not AL.kc_allowed(coin):
        return False
    xf = sys.modules.get("xfer")
    try:
        if xf and xf.route("kucoin", "htx", coin, 1.0).get("state") != "open":
            return False                               # ввод на HTX или вывод с KuCoin закрыт
    except Exception:
        return False
    cid, kfee = AL.kc_net(coin)
    if not cid or (kfee or 0) / SLOT > AL.MAX_FEE:
        return False                                   # нет сети или вывод съест окна
    e, depth = live_edge(coin)
    if e is None or e < MIN_EDGE or depth < 30:
        return False                                   # прямо сейчас окна нет (или стакан HTX тонкий) — ждём
    if api.kc_free("USDT", "trade") - AL.RESERVE < SLOT * 1.02:
        if now - H.get("nocash", 0) > 3 * 3600:
            H["nocash"] = now
            say(f"Всплеск по {coin} (окно {e*100:.1f}%), но на KuCoin нет ${SLOT:.0f} сверх резерва ${AL.RESERVE:.0f}")
        return False
    api.kc_buy_quote(coin, SLOT)
    AL.A["coins"][coin] = {"mode": "active", "since": now, "managed": True, "hunt": True, "venues": ["kucoin"],
                           "net": "ETH", "batch": round(SLOT * 0.5, 2), "wd": round(kfee or 0.3, 3),
                           "knet": cid, "wd_kucoin": kfee}
    AL.save()
    H["log"] = (H["log"] + [[int(now), "заход", coin]])[-30:]
    say(f"Быстрая полоса: всплеск по {coin}, окно сейчас {e*100:.1f}% → купил на KuCoin ${SLOT:.0f}, магазин везёт "
        f"на HTX (~5–10 мин) и продаёт в окна. Шорт откроет автохедж")
    return True

def step(now):
    AL = sys.modules.get("xalloc")
    if not AL or not AL.A or "coins" not in AL.A:
        return
    mine = [c for c, v in AL.A["coins"].items() if v.get("hunt") and v["mode"] == "active"]
    for c in mine:                                     # всплеск кончился → выход
        t, n, k = burst(c, now, QUIET)
        if now - AL.A["coins"][c]["since"] >= HOLD * 60 and t == 0 and not live_recent(c, now, QUIET):
            AL.A["coins"][c].update({"mode": "exit", "exit_t": now, "exit_h": EXIT_H})
            AL.save()
            H["log"] = (H["log"] + [[int(now), "выход", c]])[-30:]
            say(f"Всплеск по {c} закончился ({QUIET:.0f} мин без сделок) → выход: продаём в окна, через "
                f"{EXIT_H*60:.0f} мин — остаток порциями")
    d = time.strftime("%Y-%m-%d", time.gmtime(now + 8 * 3600))
    if H.get("day") != d:
        H["day"], H["day_pnl0"] = d, hunt_pnl()
    if hunt_pnl() - H.get("day_pnl0", 0) <= -STOP:
        return                                         # дневной стоп быстрой полосы — новых заходов до завтра
    if not ON or len(mine) >= SLOTS:
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

def hunt_pnl():
    """Живая прибыль быстрой полосы (сделки магазина по её монетам)."""
    SH = sys.modules.get("xshop")
    if not SH:
        return 0.0
    coins = {x[2] for x in H.get("log", [])}
    return sum(d[5] for c in coins for d in ((SH.S.get("c", {}).get(c) or {}).get("done") or [])
               if d[0] >= time.time() - 86400)

def loop():
    time.sleep(150)
    try:
        load()
    except Exception:
        log.exception("xhunt load")
    if ON:
        say(f"Быстрая полоса запущена: всплеск = {MIN_T}+ сделок за {LOOK:.0f} мин и живое окно ≥ {MIN_EDGE*100:.1f}%, "
            f"заход через KuCoin по ${SLOT:.0f}, выход после {QUIET:.0f} мин тишины")
    n = 0
    while True:
        now = time.time()
        try:
            snap(now)
            n += 1
            if n % 2 == 0:
                step(now)
            if n % 5 == 0:
                save()
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
    trades_3h = sum(v[0] for b in H["fb"].values() for v in b.values())
    return jsonify({"on": ON, "slots": SLOTS, "slot_usd": SLOT, "look_min": LOOK, "quiet_min": QUIET,
                    "routes_watched": len(H["prev"]), "paper_trades_3h": trades_3h,
                    "hot_now": hot, "log": H["log"][-10:]})

threading.Thread(target=loop, daemon=True).start()
