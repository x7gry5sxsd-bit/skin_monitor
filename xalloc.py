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
GT_READY = [c.strip().upper() for c in env("XA_GATE", "").split(",") if c.strip()]
GT_ALL = "*" in GT_READY                          # XA_GATE=* — Gate для всех монет (вторая биржа докупки)
GT_CHAIN = {"ERC20": "ETH", "BEP20": "BSC", "ETH": "ETH", "BSC": "BSC"}
NETS = {"ERC20": "ETH", "BEP20": "BSC", "ETHW": "ETHW", "SUI": "SUI", "ARB": "ARB"}   # сеть у сканера → код у MEXC
MIN_RATE = float(env("XA_MIN_RATE", "0.3"))      # реальная бумага ≥ $0.3/сутки, чтобы заходить
GAP = float(env("XA_SWAP_GAP", "0.5"))           # замена: новая лучше старой на $0.5/сутки
HOLD_H = float(env("XA_HOLD_H", "12"))           # монету держим минимум 12 ч
SWAP_H = float(env("XA_SWAP_EVERY_H", "6"))      # не чаще одного выхода в 6 ч
EXIT_H = float(env("XA_EXIT_H", "6"))            # мягкий выход: 6 ч продаём только в окна, потом порциями
RESERVE = float(env("XA_RESERVE", "15"))         # на MEXC всегда оставляем USDT на докупки
MAX_FEE = float(env("XA_MAX_FEE", "0.008"))      # вывод пачки не дороже 0.8% слота — иначе комиссия съедает окна
EVERY_MIN = float(env("XA_EVERY_MIN", "15"))     # как часто проверять (мин)
DEAD = float(env("XA_DEAD", "0.1"))              # «мёртвая»: реальная бумага < $0.1/сутки и 12 ч без живых сделок
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
    if env("XA_SCORE", "real") == "real":              # реальная бумага: готова через 6 ч сбора данных
        SP = sys.modules.get("xshopp")
        return bool(SP and SP.ready())
    R = sys.modules.get("xrotate")
    return bool(R and R.R and R.R.get("h"))

def score(coin):
    if env("XA_SCORE", "real") == "real":              # реальная бумага магазина (xshopp) + живые сделки
        SP = sys.modules.get("xshopp")
        if SP and SP.ready():
            n, pnl = live_trades(coin, 24)
            if coin in A.get("coins", {}) and n >= 5:
                return round(pnl, 2)                   # живой результат важнее любой бумаги
            return SP.rate(coin, 24)
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
    SP = sys.modules.get("xshopp")
    if env("XA_SCORE", "real") == "real" and SP:
        cs |= SP.coins()                               # монеты, где реальная бумага в плюсе
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

def gt_allowed(coin):
    return bool(getattr(api, "GT_KEY", "")) and (GT_ALL or coin in GT_READY)

def gt_net(coin):
    """Gate → HTX: сеть Gate (ETH/BSC — по тому, какую принимает HTX) и комиссия вывода в $; (None, None) — нельзя."""
    xf = sys.modules.get("xfer")
    try:
        b = xpaper.book("gate", coin)
        px = b[1][0][0] if b and b[1] else 0
        rt = xf.route("gate", "htx", coin, px or 1.0) if xf else {}
        if rt.get("state") != "open":
            return None, None
        want = GT_CHAIN.get((rt.get("text", "").split("сеть ")[-1].split(",")[0] or "").upper())
        for chain, ok, fee in api.gt_networks(coin):
            if ok and chain in ("ETH", "BSC") and (want is None or chain == want):
                return chain, round(float(fee or 0) * px, 3)
    except Exception:
        pass
    return None, None
def buy_bad(coin, v):
    """Покупка этой монеты на этой бирже недавно не прошла (пара закрыта для аккаунта/ключа) — сутки не используем."""
    return A.get("badbuy", {}).get(f"{coin}:{v}", 0) > time.time()

def rebuy_venues(coin):
    """Где докупать монету и возить на HTX: (биржи, доп. настройки, мин. комиссия $, сеть MEXC, комиссия MEXC)."""
    venues, extra, fees = [], {}, []
    net, fee = route_net(coin)
    if net and whitelisted(coin, net) and mexc_good(coin, net) and not buy_bad(coin, "mexc"):   # MEXC — есть адрес
        venues.append("mexc")
        fees.append(fee if fee is not None else 0.3)
    if kc_allowed(coin) and not buy_bad(coin, "kucoin"):
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
    if gt_allowed(coin) and not buy_bad(coin, "gate"):   # Gate — вторая биржа докупки (если KuCoin подведёт)
        gch, gfee = gt_net(coin)
        if gch:
            venues.append("gate")
            extra = {**extra, "gnet": gch, "wd_gate": gfee}
            fees.append(gfee)
    return venues, extra, (min(fees) if fees else None), net, fee

def htx_depth(coin, pct=0.01):
    """Сколько $ можно купить на HTX не дороже лучшей цены +pct (тонкий стакан = мало)."""
    b = xpaper.book("htx", coin)
    if not b or not b[1]:
        return 0.0
    cap = b[1][0][0] * (1 + pct)
    return sum(p * q for p, q in b[1] if p <= cap)

def fund_venue(venues, coin=None):
    """Где купить запас при заходе: HTX → KuCoin → Gate → MEXC. На каждой бирже оставляем запас USDT на докупки
    (XA_RESERVE): заход в новую монету не должен оставлять магазин и «Маятник» без денег."""
    keep_htx = float(env("XE_HTX_KEEP", "0"))            # USDT «Маятника» на HTX
    hunt = float(env("XA_HUNT_USD", "60")) if env("XA_HUNT", "1") == "1" else 0.0   # резерв быстрой полосы на KuCoin
    try:
        if (api.hx_free("USDT") - keep_htx - RESERVE >= SLOT * 1.02 and not (coin and buy_bad(coin, "htx"))
                and (coin is None or htx_depth(coin) >= SLOT * 0.9)):
            return "htx"
    except Exception:
        pass
    try:
        if "kucoin" in venues and not (coin and buy_bad(coin, "kucoin")) and api.kc_free("USDT", "trade") - RESERVE - hunt >= SLOT * 1.02:
            return "kucoin"
    except Exception:
        pass
    try:
        if "gate" in venues and not (coin and buy_bad(coin, "gate")) and api.gt_free("USDT") - RESERVE >= SLOT * 1.02:
            return "gate"
    except Exception:
        pass
    if "mexc" in venues and not (coin and buy_bad(coin, "mexc")) and api.mx_free("USDT") - RESERVE >= SLOT:
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
    """Активным монетам подключаем KuCoin (XA_KUCOIN) и Gate (XA_GATE), если путь до HTX открыт."""
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
    for coin, c in A["coins"].items():                 # Gate — вторая биржа докупки
        if c["mode"] != "active" or not gt_allowed(coin) or "gate" in (c.get("venues") or []):
            continue
        if time.time() - c.get("gt_try", 0) < 3600:
            continue                                   # проверяем путь Gate → HTX не чаще раза в час
        c["gt_try"] = time.time()
        gch, gfee = gt_net(coin)
        if not gch:
            continue
        vs = list(c.get("venues") or ["mexc"])
        c.update({"venues": vs + ["gate"], "gnet": gch, "wd_gate": gfee})
        mm.tg(f"🧠 {coin}: Gate подключён как биржа докупки (сеть {gch}, вывод на HTX ${gfee:.2f}). Магазин докупает "
              f"там, где дешевле: {', '.join(v.upper() for v in vs + ['gate'])}")

def total_value(coin):
    """Сколько монеты осталось везде (HTX + MEXC + Gate + KuCoin), в штуках."""
    q = api.hx_free(coin) + api.mx_free(coin)
    if api.GT_KEY:
        q += api.gt_free(coin)
    if getattr(api, "KC_KEY", ""):
        q += api.kc_free(coin, "trade") + api.kc_free(coin, "main")
    return q

def enter(coin, venues, extra, net, fee):
    """Заход: купить запас там, где есть деньги (HTX → KuCoin → Gate → MEXC), и отдать монету магазину."""
    where = fund_venue(venues, coin)
    if not where:
        mm.tg(f"🧠 Хотел зайти в {coin}, но свободных ${SLOT:.0f} нет ни на одной бирже")
        return False
    try:
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
        elif where == "gate":
            api.gt_buy_quote(coin, SLOT)
        else:
            api.mx_buy_quote(f"{coin}USDT", SLOT)
    except Exception as e:                             # биржа отклонила покупку (пара закрыта для аккаунта/ключа и т. п.)
        A.setdefault("badbuy", {})[f"{coin}:{where}"] = time.time() + 24 * 3600
        mm.tg(f"🧠 {coin}: покупка на {where.upper()} не прошла — {str(e)[:120]}. {where.upper()} для {coin} отключаю "
              f"на сутки, беру другую биржу или монету")
        return False
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
    """Монеты, которые аллокатор не выводит и не меняет: «Маятник» (XP_COINS) и закреплённые вручную (XA_PIN)."""
    raw = f'{env("XP_COINS")},{env("XA_PIN")}'
    return {x.strip().upper() for x in raw.split(",") if x.strip() and x.strip() != "*"}
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
    held = [c for c in active if now - A["coins"][c]["since"] >= HOLD_H * 3600 and c not in pinned()
            and not A["coins"][c].get("hunt")]           # монетами быстрой полосы управляет она сама
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
    pick, where = (cands[0] if cands else None), None
    for cand in cands:                                 # первый кандидат, которого есть где купить
        w = fund_venue(cand[2], cand[1])
        if w:
            pick, where = cand, w
            break
    if not cands:
        A["why"] = f"держу монеты: нет кандидатов лучше ${MIN_RATE:.0f}/сутки по бумаге"
        return
    if len([c for c, v in A["coins"].items() if not v.get("hunt")]) < SLOTS:   # 2) свободный слот
        if not where:
            A["why"] = (f"хочу зайти в {pick[1]} (≈${pick[0]:.2f}/сутки), но нет ${SLOT:.0f} свободных сверх "
                        f"резерва ${RESERVE:.0f} ни на одной бирже")
            return
        if marg is not None and marg < SLOT / 2 * 1.1:
            A["why"] = f"хочу зайти в {pick[1]}, но на фьючерсах свободно ${marg:.2f}, нужно ~${SLOT / 2 * 1.1:.0f}"
            if time.time() - A.get("warn_m", 0) > 6 * 3600:
                A["warn_m"] = time.time()
                mm.tg(f"🧠 {A['why']}. Переведи USDT на фьючерсный счёт")
            return
        s, c, venues, extra, net, fee = pick
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
        if getattr(api, "GT_KEY", ""):
            cash += f", Gate ${api.gt_free('USDT'):.0f}"
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

@mm.app.route("/alloc/check")
def alloc_check():
    """Проверка монеты перед ручным заходом: /alloc/check?t=...&coin=PONS (ничего не покупает)."""
    if not ok():
        return "forbidden", 403
    c = request.args.get("coin", "").upper()
    venues, extra, mfee, net, fee = rebuy_venues(c)
    busy = len([x for x, v in A.get("coins", {}).items() if not v.get("hunt") and v.get("mode") != "exit"])
    out = {"монета": c, "уже_в_работе": c in A.get("coins", {}), "фьючерс_MEXC": hedge_ok(c),
           "биржи_докупки": venues, "пачка_$": mfee, "лимит_пачки_$": round(MAX_FEE * SLOT, 2),
           "купить_запас_на": fund_venue(venues, c), "слоты": f"{busy} из {SLOTS} (без выходящих)"}
    try:
        out["стакан_HTX_$"] = round(htx_depth(c), 2)
    except Exception as e:
        out["стакан_HTX_$"] = f"ошибка: {str(e)[:80]}"
    try:
        out["бумага_$_сутки"] = round(score(c), 2)
    except Exception:
        out["бумага_$_сутки"] = None
    PS = sys.modules.get("xpscout")
    if PS:
        out["разведка_24ч"] = [x for x in (PS.summary(f"{c}:{v}", 24) for v in ("kucoin", "gate")) if x]
    why = []
    if not out["фьючерс_MEXC"]:
        why.append("нет фьючерса на MEXC")
    if not venues:
        why.append("нет открытого пути докупки → HTX")
    if mfee is not None and mfee / SLOT > MAX_FEE:
        why.append(f"пачка дороже ${MAX_FEE * SLOT:.0f}")
    if not out["купить_запас_на"]:
        why.append(f"нет ${SLOT:.0f} + резерв ${RESERVE:.0f} ни на одной бирже")
    out["можно_заходить"] = "да" if not why else "нет: " + "; ".join(why)
    return jsonify(out)

@mm.app.route("/alloc/enter")
def alloc_enter():
    """Ручной заход в выбранную монету (по разведке, а не по бумаге): /alloc/enter?t=...&coin=PONS"""
    if not ok():
        return "forbidden", 403
    c = request.args.get("coin", "").upper()
    if c in A.get("coins", {}):
        return f"{c} уже в работе"
    busy = len([x for x, v in A["coins"].items() if not v.get("hunt") and v.get("mode") != "exit"])
    if busy >= SLOTS:
        return f"слоты заняты ({busy} из {SLOTS}) — сначала выйди из слабой монеты (/alloc/exit) или подними XA_SLOTS"
    if not hedge_ok(c):
        return f"у {c} нет фьючерса на MEXC — нечем хеджировать"
    venues, extra, mfee, net, fee = rebuy_venues(c)
    if not venues:
        return f"у {c} нет открытого пути докупки на HTX (KuCoin / Gate / MEXC)"
    if mfee is not None and mfee / SLOT > MAX_FEE:
        return f"пачка {c} стоит ${mfee}: дороже {MAX_FEE * 100:.0f}% слота"
    H = sys.modules.get("xahedge")
    marg = H.margin(env("XH_VENUE", "mexc")) if H else None
    if marg is not None and marg < SLOT / 2 * 1.1:
        return f"на фьючерсах MEXC свободно ${marg:.2f}, нужно ~${SLOT / 2 * 1.1:.0f} — пополни и повтори"
    done = enter(c, venues, extra, net, fee)
    save()
    return f"заход в {c}: " + ("готово — подробности в Telegram" if done else "не удался — причина в Telegram")

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

@mm.app.route("/alloc/gate")
def alloc_gate():
    """Диагностика Gate как биржи докупки: по каждой монете — путь Gate → HTX, сети Gate, итог. /alloc/gate?t=..."""
    if not ok():
        return "forbidden", 403
    xf, out = sys.modules.get("xfer"), {"XA_GATE": env("XA_GATE"), "GATE_KEY": bool(getattr(api, "GT_KEY", ""))}
    for coin in list(A.get("coins", {})) + [c[0] for c in (A.get("cands") or [])[:3]]:
        r = {"разрешён": gt_allowed(coin)}
        try:
            b = xpaper.book("gate", coin)
            r["цена_gate"] = b[1][0][0] if b and b[1] else None
            r["путь"] = xf.route("gate", "htx", coin, r["цена_gate"] or 1.0).get("text") if xf else "xfer не загружен"
        except Exception as e:
            r["путь"] = f"ошибка: {str(e)[:100]}"
        try:
            r["сети_gate"] = [[c, ok_, f] for c, ok_, f in api.gt_networks(coin)]
        except Exception as e:
            r["сети_gate"] = f"ошибка: {str(e)[:100]}"
        r["итог"] = gt_net(coin)
        (A.get("coins", {}).get(coin) or {}).pop("gt_try", None)   # следующая проверка аллокатора — сразу
        out[coin] = r
    return jsonify(out)
