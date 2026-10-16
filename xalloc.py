# xalloc.py — «аллокатор»: мозг магазина. Сам решает, в каких монетах держать капитал, и сам переставляет его туда,
#   где окна: раз в час рейтинг монет (бумага за 12/24 ч + прогноз), заход в лучшую (купить на MEXC → магазин сам
#   везёт пачкой на HTX → автохедж сам открывает шорт), мягкий выход из уснувшей (продаём только в окна, через
#   XA_EXIT_H часов — остаток порциями; шорт закрывает автохедж). Кандидаты — ВСЕ монеты из бумаги с продажей на
#   HTX и фьючерсом; сеть ERC20/BEP20/SUI/ETHW/ARB, адреса — пул XE_ADDRS_<сеть>. Есть ли адрес в белом списке
#   MEXC, бот проверяет сам через API; нет — пишет, какой адрес добавить, и заходит, как только он появится.
#   XA_AUTO=0 — предлагает в Telegram ссылкой «да», XA_AUTO=1 — делает сам. /alloc?t=...
#   XA_KUCOIN — монеты, для которых адрес HTX есть в белом списке KuCoin: им сам добавляет KuCoin как вторую биржу
#   докупки (магазин берёт там, где дешевле). Рейтинг монеты — лучший из её маршрутов на HTX (MEXC/KuCoin/Gate).
import os, sys, time, json, threading, logging
from flask import jsonify, request
import mm_scanner as mm
import xpaper
import xexec_api as api

def env(k, d=""):
    return os.getenv(k, d).strip()

ON = env("XA_ON", "0") == "1"
AUTO = env("XA_AUTO", "0") == "1"
SLOT = float(env("XA_SLOT_USD", "50"))           # сколько капитала на одну монету
SLOTS = int(env("XA_SLOTS", "5"))
READY = [c.strip().upper() for c in env("XA_READY", "").split(",") if c.strip()]
KC_READY = [c.strip().upper() for c in env("XA_KUCOIN", "").split(",") if c.strip()]
NETS = {"ERC20": "ETH", "BEP20": "BSC", "ETHW": "ETHW", "SUI": "SUI", "ARB": "ARB"}   # сеть у сканера → код у MEXC
MIN_RATE = float(env("XA_MIN_RATE", "1.0"))      # бумага ≥ $1/сутки, чтобы заходить
GAP = float(env("XA_SWAP_GAP", "3.0"))           # замена: новая лучше старой на $3/сутки по бумаге
HOLD_H = float(env("XA_HOLD_H", "12"))           # монету держим минимум 12 ч
SWAP_H = float(env("XA_SWAP_EVERY_H", "6"))      # не чаще одного выхода в 6 ч
EXIT_H = float(env("XA_EXIT_H", "6"))            # мягкий выход: 6 ч продаём только в окна, потом порциями
RESERVE = float(env("XA_RESERVE", "15"))         # на MEXC всегда оставляем USDT на докупки
log = logging.getLogger("xalloc")
A = {}

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'alloc_v1'", fetch=True)
    A.update(json.loads(r[0][0]) if r else {})
    A.setdefault("coins", {})
    A.setdefault("log", [])
    A.setdefault("pending", {})
    A.setdefault("last_exit", 0)
    if not A["coins"]:                                 # старт: то, что уже ведёт магазин
        for c in [x.strip().upper() for x in env("XS_COINS", env("XS_COIN", "US")).split(",") if x.strip()]:
            A["coins"][c] = {"mode": "active", "since": time.time(), "managed": False}

def save():
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('alloc_v1', %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(A),))

def cfg(coin, key, default=None):
    """Настройка монеты, которую завёл аллокатор (для магазина и пула адресов)."""
    return (A.get("coins", {}).get(coin) or {}).get(key, default)

def coins():
    return list(A.get("coins", {}))

def mode(coin):
    return cfg(coin, "mode", "active")

def say(text):
    mm.tg(("🧠 " if AUTO else "🧠❔ ") + text)

def rate(coin, hours):
    """Бумажная прибыль монеты на маршруте MEXC → HTX, $ в сутки, за последние hours часов (из корзин xrotate)."""
    R = sys.modules.get("xrotate")
    if not R or not R.R:
        return 0.0
    now = time.time()
    best = 0.0
    keys = [coin, f"{coin}:mexc>htx", f"{coin}:gate>htx"] + ([f"{coin}:kucoin>htx"] if coin in KC_READY else [])
    for key in keys:
        st = R.stats(key, hours, now)
        best = max(best, st["net"] * 24 / hours)
    return best

def score(coin):
    r = 0.6 * rate(coin, 12) + 0.4 * rate(coin, 24)
    P = sys.modules.get("xpredict")
    if P and coin in (P.P.get("trade") or {}):
        r *= 1.2                                       # прогнозист ждёт окон — небольшой бонус
    return round(r, 2)

def route_net(coin):
    """Сеть MEXC → HTX: только разрешённые (адреса в белом списке), путь должен быть открыт."""
    xf = sys.modules.get("xfer")
    try:
        rt = xf.route("mexc", "htx", coin, 1.0) if xf else {}
    except Exception:
        return None, None
    if rt.get("state") != "open":
        return None, None
    net = (rt.get("text", "").split("сеть ")[-1].split(",")[0] or "").upper()
    return NETS.get(net), rt.get("fee_usd")

def hedge_ok(coin):
    X = sys.modules.get("xhedge")
    try:
        return coin in X.perps()[env("XH_VENUE", "mexc")] if X else False
    except Exception:
        return False

def ensure_paper(now):
    """Монеты из XA_READY должны считаться в бумаге, иначе их не с чем сравнить."""
    X, I = sys.modules.get("xinvx"), sys.modules.get("xinv")
    if not X or "c" not in X.S:
        return
    for c in READY:
        if (I and c in I.COINS) or f"{c}:mexc>htx" in X.S["c"]:
            continue
        X.make_route(c, "mexc>htx", now, "аллокатор")

def universe():
    """Все монеты, которые бумага видит с продажей на HTX (+ XA_READY)."""
    I, X = sys.modules.get("xinv"), sys.modules.get("xinvx")
    cs = set(READY)
    if I:
        cs |= {c for c, v in I.COINS.items() if "@" not in c and v[1] == "htx"}
    if X:
        cs |= {k.split(":")[0] for k in X.S.get("c", {}) if k.endswith(">htx")}
    return cs

def pool(coin, net):
    raw = env(f"XE_ADDRS_{coin}") or env(f"XE_ADDRS_{net}")
    return [a.strip() for a in raw.split(",") if a.strip()]

def whitelisted(coin, net):
    """Все адреса пула для этой монеты есть в белом списке MEXC? Проверка через API, кэш 1 ч."""
    p = {a.lower() for a in pool(coin, net)}
    if not p:
        return False
    c = A.setdefault("wl", {}).get(coin)
    if c and time.time() - c[0] < 3600:
        return c[1]
    try:
        ok = coin in READY or p <= {a for n, a in api.mx_book(coin)}   # XA_READY — ручное «да, адрес есть»
    except Exception:
        ok = coin in READY                             # API не ответил — верим списку XA_READY
    A["wl"][coin] = [time.time(), ok]
    return ok
def kc_net(coin):
    """KuCoin → HTX по ERC20: chainId и комиссия вывода в $ (или None, если вывод закрыт)."""
    try:
        b = xpaper.book("kucoin", coin)
        px = b[1][0][0] if b else 0
        for name, cid, ok, fee in api.kc_networks(coin):
            if ok and str(cid).lower() in ("eth", "erc20"):
                return cid, round(float(fee or 0) * px, 3)
    except Exception:
        pass
    return None, None

def refresh_venues():
    """Монетам из XA_KUCOIN добавляем KuCoin как вторую биржу докупки (если путь KuCoin → HTX открыт)."""
    SH, xf = sys.modules.get("xshop"), sys.modules.get("xfer")
    for coin, c in A["coins"].items():
        if c["mode"] != "active" or coin not in KC_READY:
            continue
        vs = list(c.get("venues") or (SH.venues_for(coin) if SH else ["mexc"]))
        if "kucoin" in vs:
            continue
        try:
            if xf and xf.route("kucoin", "htx", coin, 1.0).get("state") != "open":
                continue
        except Exception:
            continue
        cid, fee = kc_net(coin)
        if not cid:
            continue
        c.update({"venues": vs + ["kucoin"], "knet": cid, "wd_kucoin": fee})
        mm.tg(f"🧠 {coin}: KuCoin подключён как биржа докупки (вывод на HTX ${fee:.2f}). Магазин докупает там, "
              f"где дешевле: {', '.join(v.upper() for v in vs + ['kucoin'])}")

def total_value(coin):
    """Сколько монеты осталось везде (HTX + MEXC + Gate + KuCoin), в штуках."""
    q = api.hx_free(coin) + api.mx_free(coin)
    if api.GT_KEY:
        q += api.gt_free(coin)
    if getattr(api, "KC_KEY", ""):
        q += api.kc_free(coin, "trade") + api.kc_free(coin, "main")
    return q

def enter(coin, net, fee):
    b = xpaper.book("mexc", coin)
    price = b[1][0][0] if b else None
    api.mx_buy_quote(f"{coin}USDT", SLOT)
    A["coins"][coin] = {"mode": "active", "since": time.time(), "managed": True, "venues": ["mexc"], "net": net,
                        "batch": round(SLOT * 0.9, 2), "wd": round(fee or 0.3, 3)}
    refresh_venues()                                   # сразу добавить KuCoin, если монета в XA_KUCOIN
    A["log"] = (A["log"] + [[int(time.time()), "заход", coin, SLOT]])[-50:]
    mm.tg(f"🧠 Заход в {coin}: куплено на ${SLOT:.0f} на MEXC (цена ~{price}). Магазин сам отвезёт пачку на HTX "
          f"(сеть {net}), автохедж сам откроет шорт")

def exit_(coin):
    A["coins"][coin].update({"mode": "exit", "exit_t": time.time()})
    A["last_exit"] = time.time()
    A["log"] = (A["log"] + [[int(time.time()), "выход", coin, 0]])[-50:]
    mm.tg(f"🧠 Мягкий выход из {coin}: докупки остановлены, запас продаётся только в окна, через {EXIT_H:.0f} ч — "
          f"остаток порциями. Шорт уменьшает автохедж")

def propose(kind, coin, why, extra=None):
    pid = f"{kind}-{coin}-{int(time.time())}"
    A["pending"] = {k: v for k, v in A["pending"].items() if time.time() - v["t"] < 6 * 3600}
    if any(v["kind"] == kind and v["coin"] == coin for v in A["pending"].values()):
        return
    A["pending"][pid] = {"kind": kind, "coin": coin, "t": time.time(), "extra": extra or {}}
    host = env("PUBLIC_URL", "https://skin-monitor.onrender.com")
    say(f"Предлагаю: {'ЗАЙТИ в' if kind == 'enter' else 'ВЫЙТИ из'} {coin} — {why}\n"
        f"Да: {host}/alloc/ok?t={mm.STATS_TOKEN}&id={pid}")

def do(kind, coin, extra):
    if kind == "enter":
        enter(coin, extra.get("net"), extra.get("fee"))
    else:
        exit_(coin)

def cleanup():
    """Монета вышла полностью (почти ничего не осталось и шорта нет) — освобождаем слот."""
    X = sys.modules.get("xhedge")
    shorts = {}
    try:
        shorts = X.shorts()[0] if X else {}
    except Exception:
        return
    for coin, c in list(A["coins"].items()):
        if c["mode"] != "exit":
            continue
        b = xpaper.book("htx", coin)
        px = b[0][0][0] if b else 0
        short = sum(q for _, q, _ in shorts.get(coin, []))
        if total_value(coin) * px < 2 and short * px < 2:
            A["coins"].pop(coin)
            A["log"] = (A["log"] + [[int(time.time()), "освобождён", coin, 0]])[-50:]
            mm.tg(f"🧠 {coin}: выход завершён, шорт закрыт — слот свободен")

def decide(now):
    active = [c for c, v in A["coins"].items() if v["mode"] == "active"]
    sc = {c: score(c) for c in universe() | set(active)}
    A["scores"] = dict(list(sorted(sc.items(), key=lambda kv: -kv[1]))[:15])
    cands, missing = [], []
    for c in sorted(sc, key=lambda x: -sc[x]):
        if c in A["coins"] or sc[c] < MIN_RATE or not hedge_ok(c):
            continue
        net, fee = route_net(c)
        if not net:
            continue
        (cands if whitelisted(c, net) else missing).append((sc[c], c, net, fee))
    cands.sort(reverse=True)
    if missing and (not cands or missing[0][0] - cands[0][0] >= GAP):  # лучшая монета ждёт адрес в белом списке
        s, c, net, fee = missing[0]
        if time.time() - A.setdefault("asked", {}).get(c, 0) > 12 * 3600:
            A["asked"][c] = time.time()
            mm.tg(f"🧠 Нашёл монету {c}: ≈${s:.2f}/сутки по бумаге. Чтобы зайти, добавь в белый список MEXC для {c} "
                  f"(сеть {net}) адрес(а): {', '.join(pool(c, net)) or f'— пул XE_ADDRS_{net} пуст'}. "
                  f"Проверю сам в течение часа и зайду")
    cash = api.mx_free("USDT") - RESERVE
    H = sys.modules.get("xahedge")
    marg = H.margin(env("XH_VENUE", "mexc")) if H else None
    if cands and len(A["coins"]) < SLOTS and cash >= SLOT and marg is not None and marg < SLOT / 2 * 1.1:
        if time.time() - A.get("warn_m", 0) > 6 * 3600:          # деньги на монету есть, а маржи под шорт — нет
            A["warn_m"] = time.time()
            mm.tg(f"🧠 Хочу зайти в {cands[0][1]}, но на фьючерсах MEXC свободно ${marg:.2f}, а под шорт нужно "
                  f"~${SLOT / 2 * 1.1:.0f}. Переведи USDT на фьючерсный счёт")
        return
    if cands and len(A["coins"]) < SLOTS and cash >= SLOT:      # есть свободный слот и деньги → заходим
        s, c, net, fee = cands[0]
        why = f"бумага ≈${s:.2f}/сутки, сеть {net}, свободно ${cash:.0f}"
        if AUTO:
            enter(c, net, fee)
        else:
            propose("enter", c, why, {"net": net, "fee": fee})
        return
    if not cands or now - A["last_exit"] < SWAP_H * 3600:
        return
    held = [(sc.get(c, 0), c) for c in active if now - A["coins"][c]["since"] >= HOLD_H * 3600]
    if not held:
        return
    weak, wc = min(held)
    best, bc = cands[0][0], cands[0][1]
    if best - weak >= GAP:                                     # есть монета заметно лучше → освобождаем слот
        why = f"{wc} ≈${weak:.2f}/сутки, а {bc} ≈${best:.2f}/сутки — на освободившиеся деньги зайдём в {bc}"
        if AUTO:
            exit_(wc)
        else:
            propose("exit", wc, why)

def loop():
    time.sleep(600)
    load()
    say(f"Аллокатор запущен: {'сам решает' if AUTO else 'предлагает, вы подтверждаете'} | слоты {SLOTS} × ${SLOT:.0f} | "
        f"кандидаты: {', '.join(READY) or 'XA_READY пуст'}")
    while True:
        now = time.time()
        try:
            ensure_paper(now)
            refresh_venues()
            cleanup()
            if ON:
                decide(now)
            save()
        except Exception as e:
            log.exception("xalloc")
            mm.tg(f"❗ Аллокатор: {str(e)[:200]}")
        time.sleep(3600)

def ok():
    return mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN

@mm.app.route("/alloc")
def alloc_page():
    if not ok():
        return "forbidden", 403
    return jsonify({"on": ON, "auto": AUTO, "slots": SLOTS, "slot_usd": SLOT, "coins": A.get("coins"),
                    "scores": A.get("scores"), "pending": A.get("pending"), "log": A.get("log", [])[-15:]})

@mm.app.route("/alloc/ok")
def alloc_ok():
    if not ok():
        return "forbidden", 403
    p = A.get("pending", {}).pop(request.args.get("id", ""), None)
    if not p:
        return "предложение не найдено или устарело"
    do(p["kind"], p["coin"], p.get("extra") or {})
    save()
    return f"сделано: {'заход в' if p['kind'] == 'enter' else 'выход из'} {p['coin']}"

threading.Thread(target=loop, daemon=True).start()
