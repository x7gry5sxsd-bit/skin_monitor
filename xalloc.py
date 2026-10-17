# xalloc.py — «аллокатор»: мозг магазина. Сам решает, в каких монетах держать капитал, и сам переставляет его туда,
#   где окна: раз в час рейтинг монет (бумага за 12/24 ч + прогноз), заход в лучшую (купить на MEXC → магазин сам
#   везёт пачкой на HTX → автохедж сам открывает шорт), мягкий выход из уснувшей (продаём только в окна, через
#   XA_EXIT_H часов — остаток порциями; шорт закрывает автохедж). Кандидаты — ВСЕ монеты из бумаги с продажей на
#   HTX и фьючерсом; сеть ERC20/BEP20/SUI/ETHW/ARB, адреса — пул XE_ADDRS_<сеть>. Есть ли адрес в белом списке
#   MEXC, бот проверяет сам через API; нет — пишет, какой адрес добавить, и заходит, как только он появится.
#   XA_AUTO=0 — предлагает в Telegram ссылкой «да», XA_AUTO=1 — делает сам. /alloc?t=...
#   Заход: запас покупается СРАЗУ на HTX (если там есть USDT сверх запаса охотника), иначе на KuCoin, иначе на MEXC.
#   Докупка — где можно возить на HTX: KuCoin (XA_KUCOIN — список монет или «*» = все) и/или MEXC (адрес в белом
#   списке, проверка через API). Рейтинг монеты — лучший из её маршрутов на HTX (MEXC/KuCoin/Gate).
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
KC_ALL = "*" in KC_READY                          # XA_KUCOIN=* — KuCoin для всех монет
NETS = {"ERC20": "ETH", "BEP20": "BSC", "ETHW": "ETHW", "SUI": "SUI", "ARB": "ARB"}   # сеть у сканера → код у MEXC
MIN_RATE = float(env("XA_MIN_RATE", "1.0"))      # бумага ≥ $1/сутки, чтобы заходить
GAP = float(env("XA_SWAP_GAP", "3.0"))           # замена: новая лучше старой на $3/сутки по бумаге
HOLD_H = float(env("XA_HOLD_H", "12"))           # монету держим минимум 12 ч
SWAP_H = float(env("XA_SWAP_EVERY_H", "6"))      # не чаще одного выхода в 6 ч
EXIT_H = float(env("XA_EXIT_H", "6"))            # мягкий выход: 6 ч продаём только в окна, потом порциями
RESERVE = float(env("XA_RESERVE", "15"))         # на MEXC всегда оставляем USDT на докупки
MAX_FEE = float(env("XA_MAX_FEE", "0.008"))      # вывод пачки не дороже 0.8% слота — иначе комиссия съедает окна
EVERY_MIN = float(env("XA_EVERY_MIN", "15"))     # как часто проверять (мин)
DEAD = float(env("XA_DEAD", "0.5"))              # «мёртвая» монета: бумага за 12 ч ниже $0.5/сутки и 12 ч без сделок
REPORT_H = float(env("XA_REPORT_H", "3"))        # отчёт аллокатора в Telegram раз в 3 ч
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
    keys = [coin, f"{coin}:mexc>htx", f"{coin}:gate>htx"] + ([f"{coin}:kucoin>htx"] if kc_allowed(coin) else [])
    for key in keys:
        st = R.stats(key, hours, now)
        best = max(best, st["net"] * 24 / hours)
    return best

def live_trades(coin, hours=12):
    """Живые сделки монеты за последние hours часов: (число, прибыль $) — магазин + «Маятник»."""
    since, n, pnl = time.time() - hours * 3600, 0, 0.0
    SH, PD = sys.modules.get("xshop"), sys.modules.get("xpend")
    for d in ((SH.S.get("c", {}).get(coin) or {}).get("done") or []) if SH else []:
        if d[0] >= since:
            n, pnl = n + 1, pnl + d[5]
    for d in (((PD.P.get("c") or {}).get(coin) or {}).get("done") or []) if PD else []:
        if d[0] >= since:
            n, pnl = n + 1, pnl + d[5]
    return n, round(pnl, 2)

def paper_ready():
    R = sys.modules.get("xrotate")
    return bool(R and R.R and R.R.get("h"))

def score(coin):
    r = 0.6 * rate(coin, 12) + 0.4 * rate(coin, 24)
    P = sys.modules.get("xpredict")
    if P and coin in (P.P.get("trade") or {}):
        r *= 1.2                                       # прогнозист ждёт окон — небольшой бонус
    return round(r, 2)

def route_net(coin):
    """Сеть MEXC → HTX: только разрешённые (адреса в белом списке), путь должен быть открыт."""
    xf = sys.modules.get("xfer")
    b = xpaper.book("mexc", coin)
    px = b[1][0][0] if b and b[1] else 1.0             # настоящая цена — чтобы комиссия считалась в долларах
    try:
        rt = xf.route("mexc", "htx", coin, px) if xf else {}
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
        ok = p <= {a for n, a in api.mx_book(coin)}    # сверяемся с белым списком MEXC через API
    except Exception:
        ok = coin in READY                             # API не ответил — верим списку XA_READY
    A["wl"][coin] = [time.time(), ok]
    return ok

def mexc_good(coin, net):
    """Адреса пула, которые MEXC ещё не отклонял (отклонённые помнит xaddr)."""
    X = sys.modules.get("xaddr")
    try:
        X._load()
        bad = {a.lower() for a in X.A.get("bad", [])}
    except Exception:
        bad = set()
    return [a for a in pool(coin, net) if a.lower() not in bad]
def kc_allowed(coin):
    return KC_ALL or coin in KC_READY

def rebuy_venues(coin):
    """Где докупать монету и возить на HTX: (биржи, доп. настройки, мин. комиссия $, сеть MEXC, комиссия MEXC)."""
    venues, extra, fees = [], {}, []
    net, fee = route_net(coin)
    if net and whitelisted(coin, net) and mexc_good(coin, net):   # MEXC — только если есть неотклонённый адрес
        venues.append("mexc")
        fees.append(fee if fee is not None else 0.3)
    if kc_allowed(coin):
        xf = sys.modules.get("xfer")
        try:
            kc_open = bool(xf) and xf.route("kucoin", "htx", coin, 1.0).get("state") == "open"
        except Exception:
            kc_open = False
        cid, kfee = kc_net(coin) if kc_open else (None, None)
        if cid:
            venues.append("kucoin")
            extra = {"knet": cid, "wd_kucoin": kfee}
            fees.append(kfee)
    return venues, extra, (min(fees) if fees else None), net, fee

def htx_depth(coin, pct=0.01):
    """Сколько $ можно купить на HTX не дороже лучшей цены +pct (тонкий стакан = мало)."""
    b = xpaper.book("htx", coin)
    if not b or not b[1]:
        return 0.0
    cap = b[1][0][0] * (1 + pct)
    return sum(p * q for p, q in b[1] if p <= cap)

def fund_venue(venues, coin=None):
    """Где купить запас при заходе: HTX → KuCoin → MEXC. На каждой бирже оставляем запас USDT на докупки
    (XA_RESERVE): заход в новую монету не должен оставлять магазин и «Маятник» без денег."""
    keep_htx = float(env("XE_HTX_KEEP", "0")) + (float(env("XA_HUNT_USD", "60")) if env("XA_HUNT", "1") == "1" else 0)
    try:
        if api.hx_free("USDT") - keep_htx - RESERVE >= SLOT * 1.02 and (coin is None or htx_depth(coin) >= SLOT * 0.9):
            return "htx"
    except Exception:
        pass
    try:
        if "kucoin" in venues and api.kc_free("USDT", "trade") - RESERVE >= SLOT * 1.02:
            return "kucoin"
    except Exception:
        pass
    if "mexc" in venues and api.mx_free("USDT") - RESERVE >= SLOT:
        return "mexc"
    return None

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
        if c["mode"] != "active" or not kc_allowed(coin):
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

def enter(coin, venues, extra, net, fee):
    """Заход: купить запас там, где есть деньги (HTX → KuCoin → MEXC), и отдать монету магазину."""
    where = fund_venue(venues, coin)
    if not where:
        mm.tg(f"🧠 Хотел зайти в {coin}, но свободных ${SLOT:.0f} нет ни на HTX, ни на KuCoin, ни на MEXC")
        return False
    if where == "htx":                                 # сразу на HTX: торговля через минуту, без вывода
        b = xpaper.book("htx", coin)
        ask = b[1][0][0] if b and b[1] else None
        if not ask:
            return False
        oid = api.hx_buy_ioc(f"{coin.lower()}usdt", SLOT / ask, ask * 1.01).get("data")
        time.sleep(1.5)
        od = api.hx_order(oid).get("data") or {}
        if float(od.get("field-amount") or 0) <= 0:
            mm.tg(f"🧠 {coin}: покупка на HTX не исполнилась (цена ушла) — попробую на следующей проверке")
            return False
        spent = float(od.get("field-cash-amount") or 0)
        if spent < SLOT * 0.5:
            mm.tg(f"🧠 {coin}: на HTX исполнилось только ${spent:.2f} из ${SLOT:.0f} — стакан тонкий")
    elif where == "kucoin":
        api.kc_buy_quote(coin, SLOT)
    else:
        api.mx_buy_quote(f"{coin}USDT", SLOT)
    A["coins"][coin] = {"mode": "active", "since": time.time(), "managed": True, "venues": list(venues),
                        "net": net or "ETH", "batch": round(SLOT * 0.9, 2), "wd": round(fee or 0.3, 3), **extra}
    A["log"] = (A["log"] + [[int(time.time()), "заход", coin, SLOT, where]])[-50:]
    tail = "запас уже на HTX — магазин торгует сразу" if where == "htx" else \
        f"магазин сам отвезёт пачку с {where.upper()} на HTX"
    mm.tg(f"🧠 Заход в {coin}: куплено на ${SLOT:.0f} на {where.upper()} — {tail}. Докупка: "
          f"{', '.join(v.upper() for v in venues)}. Автохедж откроет шорт")
    return True

def exit_(coin):
    if coin in {x.strip().upper() for x in env("XP_COINS").split(",") if x.strip() and x.strip() != "*"}:
        return                                         # монета закреплена за «Маятником» — аллокатор её не выводит
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
        enter(coin, extra.get("venues") or ["mexc"], extra.get("extra") or {}, extra.get("net"), extra.get("fee"))
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

def pinned():
    return {x.strip().upper() for x in env("XP_COINS").split(",") if x.strip() and x.strip() != "*"}
def decide(now):
    """Раз в EVERY_MIN: 1) мёртвые монеты — на выход; 2) свободный слот и деньги — заход в лучшую;
    3) слоты заняты — замена слабейшей, если кандидат лучше на GAP. Причина решения пишется в A['why']."""
    if not paper_ready():
        A["why"] = "жду, пока загрузится бумага (после перезапуска ~3 мин)"
        return
    active = [c for c, v in A["coins"].items() if v["mode"] == "active"]
    sc = {c: score(c) for c in universe() | set(active)}
    A["scores"] = dict(list(sorted(sc.items(), key=lambda kv: -kv[1]))[:15])
    cands, missing = [], []
    for c in sorted(sc, key=lambda x: -sc[x]):
        if c in A["coins"] or sc[c] < MIN_RATE or not hedge_ok(c):
            continue
        venues, extra, mfee, net, fee = rebuy_venues(c)
        if mfee is not None and mfee / SLOT > MAX_FEE:   # дорогой вывод при нашем слоте — не берём
            continue
        if venues:
            cands.append((sc[c], c, venues, extra, net, fee))
        elif net:
            missing.append((sc[c], c, net, fee))
    cands.sort(key=lambda x: -x[0])
    A["cands"] = [[c[1], c[0], c[2]] for c in cands[:5]]
    if missing and (not cands or missing[0][0] - cands[0][0] >= GAP):  # лучшая монета ждёт адрес в белом списке
        s, c, net, fee = missing[0]
        if time.time() - A.setdefault("asked", {}).get(c, 0) > 12 * 3600:
            A["asked"][c] = time.time()
            mm.tg(f"🧠 Нашёл монету {c}: ≈${s:.2f}/сутки по бумаге. Чтобы зайти, добавь в белый список MEXC для {c} "
                  f"(сеть {net}) адрес(а): {', '.join(pool(c, net)) or f'— пул XE_ADDRS_{net} пуст'}. "
                  f"Проверю сам в течение часа и зайду")
    held = [c for c in active if now - A["coins"][c]["since"] >= HOLD_H * 3600 and c not in pinned()]
    dead = [c for c in held if sc.get(c, 0) < DEAD and live_trades(c)[0] == 0]
    if dead and now - A["last_exit"] >= 3600:              # 1) мёртвая монета: нет окон ни на бумаге, ни вживую
        c = min(dead, key=lambda x: sc.get(x, 0))
        why = f"{c} уснула: бумага ≈${sc.get(c, 0):.2f}/сутки, 12 ч без сделок — освобождаю деньги"
        A["why"] = why
        if AUTO:
            exit_(c)
        else:
            propose("exit", c, why)
        return
    H = sys.modules.get("xahedge")
    marg = H.margin(env("XH_VENUE", "mexc")) if H else None
    where = fund_venue(cands[0][2], cands[0][1]) if cands else None
    if not cands:
        A["why"] = f"держу монеты: нет кандидатов лучше ${MIN_RATE:.0f}/сутки по бумаге"
        return
    if len(A["coins"]) < SLOTS:                                # 2) свободный слот
        if not where:
            A["why"] = (f"хочу зайти в {cands[0][1]} (≈${cands[0][0]:.2f}/сутки), но нет ${SLOT:.0f} свободных сверх "
                        f"резерва ${RESERVE:.0f} ни на одной бирже")
            return
        if marg is not None and marg < SLOT / 2 * 1.1:
            A["why"] = f"хочу зайти в {cands[0][1]}, но на фьючерсах свободно ${marg:.2f}, нужно ~${SLOT / 2 * 1.1:.0f}"
            if time.time() - A.get("warn_m", 0) > 6 * 3600:
                A["warn_m"] = time.time()
                mm.tg(f"🧠 {A['why']}. Переведи USDT на фьючерсный счёт")
            return
        s, c, venues, extra, net, fee = cands[0]
        why = f"бумага ≈${s:.2f}/сутки, куплю на {where.upper()}, докупка {', '.join(v.upper() for v in venues)}"
        A["why"] = f"захожу в {c}: {why}"
        if AUTO:
            enter(c, venues, extra, net, fee)
        else:
            propose("enter", c, why, {"venues": venues, "extra": extra, "net": net, "fee": fee})
        return
    if not held:                                               # 3) слоты заняты — замена
        A["why"] = f"слоты заняты; монеты держу минимум {HOLD_H:.0f} ч — замен пока нет"
        return
    if now - A["last_exit"] < SWAP_H * 3600:
        A["why"] = f"слоты заняты; последняя замена была меньше {SWAP_H:.0f} ч назад"
        return
    weak, wc = min((sc.get(c, 0), c) for c in held)
    best, bc = cands[0][0], cands[0][1]
    if best - weak < GAP:
        A["why"] = f"слоты заняты; лучший кандидат {bc} (≈${best:.2f}) не лучше слабейшей {wc} (≈${weak:.2f}) на ${GAP:.0f}"
        return
    why = f"{wc} ≈${weak:.2f}/сутки, а {bc} ≈${best:.2f}/сутки — на освободившиеся деньги зайду в {bc}"
    A["why"] = f"замена: {why}"
    if AUTO:
        exit_(wc)
    else:
        propose("exit", wc, why)

def report_text():
    lines = ["🧠 Аллокатор — отчёт", "Наши монеты (12 ч):"]
    for c, v in A.get("coins", {}).items():
        n, pnl = live_trades(c)
        s = score(c)
        icon = "↩️" if v["mode"] == "exit" else ("✅" if n >= 3 or s >= 3 else ("⚠️" if s >= DEAD or n else "💤"))
        who = "Маятник" if c in pinned() or (env("XP_COINS") == "*" and "kucoin" in (v.get("venues") or [])) else "магазин"
        lines.append(f"  {icon} {c} ({'выход' if v['mode'] == 'exit' else who}): сделок {n}, {pnl:+.2f}$ | "
                     f"бумага ≈${s:.2f}/сут")
    cs = A.get("cands") or []
    lines.append("Кандидаты: " + (", ".join(f"{c} ≈${s:.2f}/сут" for c, s, v in cs[:3]) if cs else "нет"))
    try:
        cash = f"HTX ${api.hx_free('USDT'):.0f}, KuCoin ${api.kc_free('USDT', 'trade'):.0f}, MEXC ${api.mx_free('USDT'):.0f}"
    except Exception:
        cash = "—"
    lines.append(f"USDT: {cash} (на заход нужно ${SLOT:.0f} + резерв ${RESERVE:.0f} на одной бирже)")
    lines.append(f"Решение: {A.get('why') or '—'}")
    return "\n".join(lines)

def cycle():
    now = time.time()
    ensure_paper(now)
    refresh_venues()
    cleanup()
    if ON:
        decide(now)
    if now - A.get("rep_t", 0) >= REPORT_H * 3600 and paper_ready():
        A["rep_t"] = now
        mm.tg(report_text())
    save()

def loop():
    load()                                             # сразу: магазин увидит монеты аллокатора без 10-минутной паузы
    time.sleep(120)
    say(f"Аллокатор запущен: {'сам решает' if AUTO else 'предлагает, вы подтверждаете'} | слоты {SLOTS} × ${SLOT:.0f} | "
        f"проверка раз в {EVERY_MIN:.0f} мин")
    while True:
        try:
            cycle()
        except Exception as e:
            log.exception("xalloc")
            mm.tg(f"❗ Аллокатор: {str(e)[:200]}")
        time.sleep(EVERY_MIN * 60)

def ok():
    return mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN

@mm.app.route("/alloc")
def alloc_page():
    if not ok():
        return "forbidden", 403
    return jsonify({"on": ON, "auto": AUTO, "slots": SLOTS, "slot_usd": SLOT, "coins": A.get("coins"),
                    "why": A.get("why"), "candidates": A.get("cands"),
                    "scores": A.get("scores"), "pending": A.get("pending"), "log": A.get("log", [])[-15:]})

@mm.app.route("/alloc/exit")
def alloc_exit():
    """Выйти из монеты вручную: /alloc/exit?t=...&coin=NES (&fast=1 — сразу порциями, без ожидания окон)"""
    if not ok():
        return "forbidden", 403
    c = request.args.get("coin", "").upper()
    if c not in A.get("coins", {}):
        return "такой монеты нет"
    exit_(c)
    if request.args.get("fast") == "1":
        A["coins"][c]["exit_h"] = 0
    save()
    return f"выход из {c} начат"

@mm.app.route("/alloc/now")
def alloc_now():
    """Проверить прямо сейчас (после пополнения или добавления адреса): /alloc/now?t=..."""
    if not ok():
        return "forbidden", 403
    if not A:
        load()
    A.pop("wl", None)                                  # белый список — перепроверить, а не из кэша
    cycle()
    return jsonify({"coins": list(A.get("coins", {})), "scores": A.get("scores"), "pending": A.get("pending")})

@mm.app.route("/alloc/report")
def alloc_report():
    """Отчёт аллокатора прямо сейчас (то же, что приходит в Telegram раз в XA_REPORT_H ч): /alloc/report?t=..."""
    if not ok():
        return "forbidden", 403
    return "<pre>" + report_text() + "</pre>"

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
