# xalloc.py — «аллокатор»: мозг магазина. Сам решает, в каких монетах держать капитал, и сам переставляет его туда,
#   где окна: рейтинг монет по бумаге магазина (xshopp) и живым сделкам, заход в лучшую, мягкий выход из уснувшей
#   (продаём только в окна, через XA_EXIT_H часов — остаток порциями; шорт закрывает автохедж).
#   XA_AUTO=0 — предлагает в Telegram ссылкой «да», XA_AUTO=1 — делает сам. /alloc?t=...
#   v4 (22.10) — защита от лишних заходов и выходов:
#   • СНАЧАЛА ШОРТ, ПОТОМ ПОКУПКА: MEXC не открыл шорт — монету не покупаем и баним на XA_BAN_D дней. Фьючерсы,
#     где MEXC запретил торговлю через API (apiAllowed=false), в кандидаты не попадают.
#   • ОДНА ШКАЛА: держимая монета и кандидат сравниваются в «живых» $/сутки. Бумага умножается на поправку K —
#     сколько бумаги реально зарабатывается (считается сама по держимым монетам за 48 ч; мало данных — XA_PAPER_K).
#   • ОСТОРОЖНАЯ БУМАГА: без самого удачного часа (одиночный всплеск не в счёт), минимум 12 ч данных, только по
#     биржам, где есть ключ; путь не подтверждён — вдвое меньше.
#   • ФИЛЬТР ПАМПА: не заходим, если цена за сутки ушла больше XA_PUMP_24H, премия HTX сейчас выше обычной на
#     XA_SPIKE или фандинг шорта хуже XA_MIN_FUND.
#   • ОКУПАЕМОСТЬ: заход только если оценка × XA_PAYBACK_D суток покрывает цену захода и выхода (комиссии, премия
#     к справедливой цене, пачка). Запас покупается там, где заход дешевле, а не «сначала HTX».
#   • ТЕМП: не больше XA_MAX_ENTRIES_DAY заходов в сутки, после выхода монету не берём XA_REENTRY_H ч, первые
#     XA_WARMUP_MIN минут после перезапуска решений нет.
#   • ВЫХОД: монета держится минимум XA_HOLD_H; выходим, только если и живой итог за 48 ч, и бумага × K ниже
#     XA_EXIT_LIVE $/сутки. Замена — только если выигрыш за XA_SWAP_HORIZON_D суток больше её цены.
#   Живой итог считается по счётчикам магазина (снимок раз в 30 мин), а не по последним 20 сделкам.
import os, sys, time, json, threading, logging
from flask import jsonify, request
import mm_scanner as mm
import xpaper
import xexec_api as api

def env(k, d=""):
    return os.getenv(k, d).strip()

VERSION = "xalloc v4 · 22.10"
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
MIN_RATE = float(env("XA_MIN_RATE", "0.5"))      # оценка (живые $/сутки) от $0.5, чтобы заходить
GAP = float(env("XA_SWAP_GAP", "1"))             # замена: новая лучше старой минимум на $1/сутки
HOLD_H = float(env("XA_HOLD_H", "48"))           # монету держим минимум 48 ч
SWAP_H = float(env("XA_SWAP_EVERY_H", "24"))     # не чаще одного выхода в сутки
EXIT_H = float(env("XA_EXIT_H", "24"))           # мягкий выход: столько часов продаём только в окна, потом порциями
RESERVE = float(env("XA_RESERVE", "15"))         # на бирже всегда оставляем USDT на докупки
MAX_FEE = float(env("XA_MAX_FEE", "0.008"))      # вывод пачки не дороже этой доли слота — иначе комиссия съедает окна
EVERY_MIN = float(env("XA_EVERY_MIN", "15"))     # как часто проверять (мин)
DEAD = float(env("XA_DEAD", "0.1"))              # только для значков отчёта
REPORT_H = float(env("XA_REPORT_H", "3"))        # отчёт аллокатора в Telegram раз в 3 ч
PAPER_K = float(env("XA_PAPER_K", "0.4"))        # бумага → жизнь, пока своих замеров мало
PAYBACK_D = float(env("XA_PAYBACK_D", "2"))      # заход окупается за 2 суток по осторожной оценке
SWAP_D = float(env("XA_SWAP_HORIZON_D", "3"))    # замена окупается за 3 суток
REENTRY_H = float(env("XA_REENTRY_H", "72"))     # вышли из монеты — 72 ч её не берём
MAX_IN_DAY = int(env("XA_MAX_ENTRIES_DAY", "1"))  # не больше 1 автоматического захода в сутки
PUMP = float(env("XA_PUMP_24H", "0.15"))         # цена за сутки ±15% — памп или обвал, не заходим
SPIKE = float(env("XA_SPIKE", "0.02"))           # премия HTX сейчас выше обычной на 2 п.п. — пик, ждём
MAX_PREM = float(env("XA_MAX_PREM", "0.08"))     # премия HTX больше 8% — другой токен или закрыт ввод
MIN_FUND = float(env("XA_MIN_FUND", "-0.001"))   # фандинг хуже −0,1% за 8 ч — шорт дорогой
BAN_D = float(env("XA_BAN_D", "7"))              # бан монеты после отказа MEXC
EXIT_LIVE = float(env("XA_EXIT_LIVE", "0.3"))    # выход: живой итог и бумага × K ниже $0.3/сутки
WARMUP = float(env("XA_WARMUP_MIN", "30")) * 60  # после перезапуска 30 мин без решений
LIVE_H = 48
COST_EXIT = 0.007                                 # выход: комиссия HTX 0,2% + продажа до −0,5% к рынку
log = logging.getLogger("xalloc")
A = {}
TK = {}

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'alloc_v1'", fetch=True)
    A.update(json.loads(r[0][0]) if r else {})
    A.setdefault("coins", {})
    A.setdefault("log", [])
    A.setdefault("pending", {})
    A.setdefault("last_exit", 0)
    for k in ("ban", "exited", "live", "skip"):
        A.setdefault(k, {})
    for x in A["log"]:                                 # выходы до v4 тоже дают паузу: недавние монеты сразу не берём
        if len(x) > 2 and x[1] == "выход":
            A["exited"][x[2]] = max(A["exited"].get(x[2], 0), x[0])
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

def logx(kind, coin, *rest):
    A["log"] = (A["log"] + [[int(time.time()), kind, coin, *rest]])[-80:]

# ---------- бан и пауза после выхода ----------
def ban(coin, days, why):
    A.setdefault("ban", {})[coin] = [time.time() + days * 86400, why[:160]]
    logx("бан", coin, round(days, 1), why[:80])

def banned(coin):
    b = A.get("ban", {}).get(coin)
    return b[1] if b and b[0] > time.time() else None

def cooldown(coin):
    """Сколько часов ещё нельзя брать монету после выхода из неё (0 — можно)."""
    t = A.get("exited", {}).get(coin, 0)
    return max(0.0, (t + REENTRY_H * 3600 - time.time()) / 3600)

def entries_24h():
    return sum(1 for x in A.get("log", []) if x[1] == "заход" and time.time() - x[0] < 86400 and "вручную" not in x)

# ---------- живой итог: снимки счётчиков магазина ----------
def shop_net(coin):
    """Счётчики магазина по монете: (сделок всего, $ чистыми с пачками всего)."""
    SH = sys.modules.get("xshop")
    c = (SH.S.get("c", {}).get(coin) or {}) if SH else {}
    return int(c.get("trades", 0)), float(c.get("pnl", 0.0)) - float(c.get("wd_usd", 0.0))

def snap_live(now):
    """Раз в 30 мин записываем счётчики магазина: живой итог за 24–48 ч считается точно, без лимита в 20 сделок."""
    L = A.setdefault("live", {})
    for coin in A.get("coins", {}):
        rows = L.setdefault(coin, [])
        if rows and now - rows[-1][0] < 1800:
            continue
        n, net = shop_net(coin)
        rows.append([int(now), n, round(net, 4)])
        L[coin] = [r for r in rows if now - r[0] <= 80 * 3600]
    for coin in [c for c in L if c not in A.get("coins", {})]:
        L.pop(coin)

def live(coin, hours, now=None):
    """Живой итог монеты за последние hours ч по снимкам: (сделок, $ чистыми, сколько часов реально покрыто)."""
    now = now or time.time()
    rows = (A.get("live") or {}).get(coin) or []
    base = next((r for r in rows if r[0] >= now - hours * 3600), None)
    if not base:
        return 0, 0.0, 0.0
    n, net = shop_net(coin)
    return max(0, n - base[1]), round(net - base[2], 2), (now - base[0]) / 3600

def live_trades(coin, hours=12):
    """Живые сделки монеты за hours ч: (число, прибыль $). По снимкам, а пока их мало — по последним сделкам магазина."""
    n, pnl, cov = live(coin, hours)
    if cov >= min(hours, 12) - 0.6:
        return n, pnl
    since, n, pnl = time.time() - hours * 3600, 0, 0.0
    SH = sys.modules.get("xshop")
    for d in ((SH.S.get("c", {}).get(coin) or {}).get("done") or []) if SH else []:
        if d[0] >= since:
            n, pnl = n + 1, pnl + d[5]
    return n, round(pnl, 2)

def live_day(coin, hours=LIVE_H):
    """Живой итог в $/сутки за последние hours ч или None, если данных меньше суток."""
    n, pnl, cov = live(coin, hours)
    return round(pnl / cov * 24, 2) if cov >= 24 else None

# ---------- бумага и оценка ----------
def paper_ready():
    if env("XA_SCORE", "real") == "real":
        SP = sys.modules.get("xshopp")
        return bool(SP and SP.ready())
    R = sys.modules.get("xrotate")
    return bool(R and R.R and R.R.get("h"))

def partners_on():
    """Партнёры, с которых реально докупаем: только биржи с ключами."""
    out = set()
    if getattr(api, "GT_KEY", ""):
        out.add("gate")
    if getattr(api, "KC_KEY", ""):
        out.add("kucoin")
    return out

def paper_rate(coin, hours=24):
    """Бумага магазина, $/сутки, осторожно: без самого удачного часа, минимум 12 ч данных, только партнёры с ключами,
    путь не подтверждён — вдвое меньше; фандинг — какой он сейчас. Без поправки K."""
    SP = sys.modules.get("xshopp")
    if not SP or not SP.ready():
        return 0.0
    PS = sys.modules.get("xpscout")
    fnow = ((PS.S.get("fund") or {}).get(coin)) if PS else None
    lo, best = int(time.time() // 3600) - hours, 0.0
    for v in set(getattr(SP, "PARTNERS", ())) & partners_on():
        c = SP.S.get("p", {}).get(f"{coin}:{v}")
        if not c:
            continue
        rows = [r for r in c.get("h", []) if r[0] > lo]
        if len(rows) < 12:
            continue
        trade = [SP.g(r, 4) - SP.g(r, 5) for r in rows]
        top = max(trade + [0.0])
        days = len(rows) / 24
        fund = fnow * 3 * SLOT if fnow is not None else sum(SP.g(r, 9) for r in rows) / days
        day = (sum(trade) - top) / days + fund
        best = max(best, day * (1 if c.get("st") == "open" else 0.5))
    return round(best, 2)

def calib():
    """Поправка K «бумага → жизнь» по держимым монетам за 48 ч (0,15–1). Мало данных — XA_PAPER_K."""
    num = den = 0.0
    for coin, v in A.get("coins", {}).items():
        if v.get("mode") != "active" or v.get("hunt"):
            continue
        d = live_day(coin)
        if d is None:
            continue
        num += d
        den += paper_rate(coin, LIVE_H)
    k = PAPER_K if den < 1.0 else min(1.0, max(0.15, num / den))
    A["k"] = round(k, 2)
    return k

def score(coin, k=None):
    """Оценка монеты в «живых» $/сутки — одна шкала для держимых монет и кандидатов."""
    if env("XA_SCORE", "real") != "real":
        return round(0.6 * rate(coin, 12) + 0.4 * rate(coin, 24), 2)
    k = A.get("k", PAPER_K) if k is None else k
    if coin in A.get("coins", {}):                     # живой результат важнее любой бумаги
        d = live_day(coin)                             # 48 ч, если данных хотя бы на сутки
        if d is not None:
            return d
        n, pnl, cov = live(coin, 24)
        if cov >= 12 and n >= 5:
            return round(pnl / cov * 24, 2)
    return round(paper_rate(coin) * k, 2)

def rate(coin, hours):
    """Старая оценка по корзинам xrotate (XA_SCORE не real)."""
    R = sys.modules.get("xrotate")
    if not R or not R.R:
        return 0.0
    now, best = time.time(), 0.0
    for key in [coin, f"{coin}:mexc>htx", f"{coin}:gate>htx"]:
        st = R.stats(key, hours, now)
        best = max(best, st["net"] * 24 / hours)
    return best

# ---------- фильтр пампа ----------
def ticker(coin):
    """Фьючерс MEXC: (цена, изменение за 24 ч, фандинг за 8 ч). Один запрос на все монеты, кэш 5 мин."""
    c = TK.get("t")
    if not c or time.time() - c[0] > 300:
        try:
            d = xpaper.S.get("https://contract.mexc.com/api/v1/contract/ticker", timeout=20).json().get("data") or []
            TK["t"] = (time.time(), {x["symbol"][:-5]: (float(x.get("lastPrice") or 0), float(x.get("riseFallRate") or 0),
                                                         float(x.get("fundingRate") or 0))
                                     for x in d if str(x.get("symbol", "")).endswith("_USDT")})
        except Exception:
            TK["t"] = (time.time() - 240, (c or (0, {}))[1])   # не ответил — старые цены, повтор через минуту
    return TK["t"][1].get(coin)

def usual_prem(coin):
    """Обычная премия HTX к партнёру за сутки (по бумаге), доля; None — не знаем."""
    SP = sys.modules.get("xshopp")
    vals = []
    for v in partners_on():
        s = SP.summary(f"{coin}:{v}", 24) if SP else None
        if s and s.get("htx_premium_pct") is not None:
            vals.append(s["htx_premium_pct"] / 100)
    return min(vals) if vals else None

def pump_block(coin):
    """Причина НЕ заходить сейчас (памп, пик премии HTX, дорогой фандинг) или None."""
    t = ticker(coin)
    if not t or not t[0]:
        return "нет цены фьючерса MEXC"
    px, ch, fr = t
    if abs(ch) > PUMP:
        return f"цена за сутки {ch*100:+.0f}% — памп или обвал, жду успокоения"
    if fr < MIN_FUND:
        return f"фандинг {fr*100:.2f}% за 8 ч — шорт слишком дорогой"
    b = xpaper.book("htx", coin)
    if b and b[0]:
        prem = b[0][0][0] / px - 1
        if prem > MAX_PREM:
            return f"HTX дороже MEXC на {prem*100:.1f}% — похоже на другой токен или закрытый ввод"
        u = usual_prem(coin)
        if u is not None and prem - u > SPIKE:
            return f"премия HTX сейчас {prem*100:.1f}% при обычной {u*100:.1f}% — пик, жду"
    return None
# ---------- маршруты докупки ----------
def route_net(coin):
    """Сеть MEXC → HTX: только разрешённые (адреса в белом списке), путь должен быть открыт."""
    xf = sys.modules.get("xfer")
    b = xpaper.book("mexc", coin)
    px = b[1][0][0] if b and b[1] else 1.0
    try:
        rt = xf.route("mexc", "htx", coin, px) if xf else {}
    except Exception:
        return None, None
    if rt.get("state") != "open":
        return None, None
    net = (rt.get("text", "").split("сеть ")[-1].split(",")[0] or "").upper()
    return NETS.get(net), rt.get("fee_usd")

def hedge_ok(coin):
    """Фьючерс MEXC есть и принимает ордера через API."""
    X = sys.modules.get("xhedge")
    try:
        if X and hasattr(X, "api_ok"):
            return X.api_ok(coin, env("XH_VENUE", "mexc"))
        return coin in X.perps()[env("XH_VENUE", "mexc")] if X else False
    except Exception:
        return False

def ensure_paper(now):
    """Монеты из XA_READY должны считаться в бумаге (старые модули xinv/xinvx; без них — ничего)."""
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
        cs |= SP.coins()
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
        ok = p <= {a for n, a in api.mx_book(coin)}
    except Exception:
        ok = coin in READY
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
    return bool(getattr(api, "KC_KEY", "")) and (KC_ALL or coin in KC_READY)   # нет ключа KuCoin — KuCoin нигде нет

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
    if net and whitelisted(coin, net) and mexc_good(coin, net) and not buy_bad(coin, "mexc"):
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
    if gt_allowed(coin) and not buy_bad(coin, "gate"):
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

def entry_cost(coin, venue, wd):
    """Цена захода и выхода на слот, $: комиссия покупки, премия к справедливой цене (фьючерс MEXC), пачка до HTX,
    выход (комиссия HTX + продажа чуть ниже рынка)."""
    b, t = xpaper.book(venue, coin), ticker(coin)
    if not b or not b[1] or not t or not t[0]:
        return None
    prem = max(0.0, b[1][0][0] / t[0] - 1)
    batch = 0.0 if venue == "htx" else (wd if wd is not None else 0.3)
    return round(SLOT * (xpaper.fee(venue) + prem + COST_EXIT) + batch, 2)

def entry_venue(coin, venues, extra, mexc_fee=None):
    """Где покупать запас при заходе: среди бирж, где хватает USDT сверх резерва, — с самой низкой ценой захода
    (премия HTX и пачка учтены). Возвращает (цена захода $, биржа) или (None, None)."""
    keep_htx = float(env("XE_HTX_KEEP", "0"))
    wd = {"gate": extra.get("wd_gate"), "kucoin": extra.get("wd_kucoin"), "mexc": mexc_fee}
    cash = (("htx", lambda: api.hx_free("USDT") - keep_htx),
            ("gate", lambda: api.gt_free("USDT")),
            ("mexc", lambda: api.mx_free("USDT")),
            ("kucoin", lambda: api.kc_free("USDT", "trade")))
    opts = []
    for v, fn in cash:
        if v != "htx" and v not in venues:
            continue
        if buy_bad(coin, v):
            continue
        try:
            if fn() - RESERVE < SLOT * 1.02:
                continue
            if v == "htx" and htx_depth(coin) < SLOT * 0.9:
                continue                               # тонкий стакан HTX — запас не купить
            c = entry_cost(coin, v, wd.get(v))
            if c is not None:
                opts.append((c, v))
        except Exception:
            continue
    return min(opts) if opts else (None, None)

def fund_venue(venues, coin=None):
    """Совместимость: биржа, где заход дешевле (см. entry_venue)."""
    return entry_venue(coin, venues, {})[1] if coin else None

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
        mm.tg(f"🧠 {coin}: KuCoin подключён как биржа докупки (вывод на HTX ${fee:.2f})")
    for coin, c in A["coins"].items():
        if c["mode"] != "active" or not gt_allowed(coin) or "gate" in (c.get("venues") or []):
            continue
        if time.time() - c.get("gt_try", 0) < 3600:
            continue
        c["gt_try"] = time.time()
        gch, gfee = gt_net(coin)
        if not gch:
            continue
        vs = list(c.get("venues") or ["mexc"])
        c.update({"venues": vs + ["gate"], "gnet": gch, "wd_gate": gfee})
        mm.tg(f"🧠 {coin}: Gate подключён как биржа докупки (сеть {gch}, вывод на HTX ${gfee:.2f})")

def total_value(coin):
    """Сколько монеты осталось везде (HTX + MEXC + Gate + KuCoin), в штуках."""
    q = api.hx_free(coin) + api.mx_free(coin)
    if api.GT_KEY:
        q += api.gt_free(coin)
    if getattr(api, "KC_KEY", ""):
        q += api.kc_free(coin, "trade") + api.kc_free(coin, "main")
    return q

# ---------- заход: сначала шорт, потом покупка ----------
def money_error(e):
    s = str(e).lower()
    return any(w in s for w in ("insufficient", "balance", "margin", "余额", "not enough"))

def spot_buy(coin, where):
    """Покупка запаса на SLOT. Возвращает потраченные $ (0 — не исполнилось)."""
    if where == "htx":
        b = xpaper.book("htx", coin)
        ask = b[1][0][0] if b and b[1] else None
        if not ask:
            return 0.0
        oid = api.hx_buy_ioc(f"{coin.lower()}usdt", SLOT / ask, ask * 1.01).get("data")
        time.sleep(1.5)
        od = api.hx_order(oid).get("data") or {}
        return float(od.get("field-cash-amount") or 0) if float(od.get("field-amount") or 0) > 0 else 0.0
    if where == "kucoin":
        api.kc_buy_quote(coin, SLOT)
    elif where == "gate":
        api.gt_buy_quote(coin, SLOT)
    else:
        api.mx_buy_quote(f"{coin}USDT", SLOT)
    return SLOT

def enter(coin, venues, extra, net, fee, manual=False):
    """Заход: 1) шорт на MEXC на весь слот; 2) только после него — покупка запаса там, где заход дешевле.
    Шорт не открылся — монету не покупаем (бан на XA_BAN_D дней, если это не нехватка маржи).
    Покупка не прошла — шорт закрываем обратно."""
    cost, where = entry_venue(coin, venues, extra, fee)
    if not where:
        mm.tg(f"🧠 Хотел зайти в {coin}, но свободных ${SLOT:.0f} сверх резерва ${RESERVE:.0f} нет ни на одной бирже")
        return False
    X, H = sys.modules.get("xhedge"), sys.modules.get("xahedge")
    hv, lev = env("XH_VENUE", "mexc"), (getattr(H, "LEV", 2) if H else 2)
    t, b = ticker(coin), xpaper.book(where, coin)
    px = (b[1][0][0] if b and b[1] else None) or (t[0] if t else None)
    if not X or not px:
        mm.tg(f"🧠 {coin}: нет цены или модуля фьючерсов — заход отменён")
        return False
    try:                                               # 1) шорт
        shorted = float((X.open_short(hv, coin, SLOT / px, lev) or {}).get("coins") or 0)
    except Exception as e:
        if money_error(e):
            mm.tg(f"🧠 {coin}: MEXC не открыл шорт — мало маржи ({str(e)[:100]}). Монету не покупаю. "
                  f"Переведи USDT на фьючерсный счёт MEXC")
        else:
            ban(coin, BAN_D, f"MEXC не открыл шорт: {str(e)[:120]}")
            mm.tg(f"🧠 {coin}: MEXC не открыл шорт ({str(e)[:120]}). Монету НЕ покупаю, бан {BAN_D:.0f} дн.")
        return False
    try:                                               # 2) покупка
        spent = spot_buy(coin, where)
        if spent <= 0:
            raise api.ApiError("покупка не исполнилась (цена ушла)")
    except Exception as e:
        A.setdefault("badbuy", {})[f"{coin}:{where}"] = time.time() + 24 * 3600
        try:
            X.close_short(hv, coin, shorted)
            undo = "шорт закрыт обратно"
        except Exception as e2:
            undo = f"❗ шорт закрыть не вышло ({str(e2)[:80]}) — автохедж закроет сам через ~10 мин"
        mm.tg(f"🧠 {coin}: покупка на {where.upper()} не прошла — {str(e)[:120]}. {undo}. {where.upper()} для {coin} "
              f"отключаю на сутки")
        return False
    A["coins"][coin] = {"mode": "active", "since": time.time(), "managed": True, "venues": list(venues),
                        "net": net or "ETH", "batch": round(SLOT * 0.9, 2), "wd": round(fee or 0.3, 3), **extra}
    logx("заход", coin, SLOT, where, cost, *(["вручную"] if manual else []))
    tail = "запас уже на HTX — магазин торгует сразу" if where == "htx" else f"магазин сам отвезёт пачку с {where.upper()} на HTX"
    mm.tg(f"🧠 Заход в {coin}: шорт {shorted:.4g} на MEXC открыт, куплено на ${spent:.0f} на {where.upper()} "
          f"(цена захода и выхода ≈${cost:.2f}) — {tail}")
    return True

def exit_(coin, exit_h=None, why=""):
    if coin in {x.strip().upper() for x in env("XP_COINS").split(",") if x.strip() and x.strip() != "*"}:
        return                                         # монета закреплена за «Маятником» — аллокатор её не выводит
    c = A["coins"].get(coin)
    if not c or c.get("mode") == "exit":
        return
    c.update({"mode": "exit", "exit_t": time.time()})
    if exit_h is not None:
        c["exit_h"] = exit_h
    A["last_exit"] = time.time()
    A.setdefault("exited", {})[coin] = time.time()
    logx("выход", coin, 0, why[:80])
    h = EXIT_H if exit_h is None else exit_h
    mm.tg(f"🧠 Мягкий выход из {coin}{': ' + why if why else ''}. Докупки остановлены, запас продаётся в окна, через "
          f"{h:.0f} ч — остаток порциями. Шорт уменьшает автохедж. Снова брать {coin} — не раньше чем через {REENTRY_H:.0f} ч")

def hedge_refused(coin, err):
    """Автохедж не смог добавить шорт держимой монете: бан и быстрый выход — монета без хеджа = ставка на цену."""
    if money_error(err):
        return
    ban(coin, BAN_D, f"автохедж: MEXC отказал — {str(err)[:100]}")
    if (A.get("coins", {}).get(coin) or {}).get("mode") == "active":
        exit_(coin, exit_h=2, why="MEXC не даёт держать шорт — без хеджа не торгуем")
    save()

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
        if banned(coin):
            mm.tg(f"🧠 {coin} в бане ({banned(coin)}) — заход отменён")
            return
        enter(coin, extra.get("venues") or ["mexc"], extra.get("extra") or {}, extra.get("net"), extra.get("fee"))
    else:
        exit_(coin, why=extra.get("why", "") if isinstance(extra, dict) else "")

def cleanup():
    """Монета вышла полностью (почти ничего не осталось и шорта нет) — освобождаем слот."""
    X = sys.modules.get("xhedge")
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
            logx("освобождён", coin, 0)
            mm.tg(f"🧠 {coin}: выход завершён, шорт закрыт — слот свободен")

def pinned():
    """Монеты, которые аллокатор не выводит и не меняет: «Маятник» (XP_COINS) и закреплённые вручную (XA_PIN)."""
    raw = f'{env("XP_COINS")},{env("XA_PIN")}'
    return {x.strip().upper() for x in raw.split(",") if x.strip() and x.strip() != "*"}
# ---------- решения ----------
def candidates(sc):
    """Кандидаты на заход по убыванию оценки: [(оценка, монета, биржи, настройки, сеть, комиссия MEXC)] и причины
    отказа по лучшим монетам (для /alloc)."""
    cands, skip = [], {}
    for c in sorted(sc, key=lambda x: -sc[x]):
        if c in A["coins"] or sc[c] < MIN_RATE:
            continue
        why = None
        if banned(c):
            why = f"бан: {banned(c)}"
        elif cooldown(c) > 0:
            why = f"недавно вышли, ещё {cooldown(c):.0f} ч"
        elif not hedge_ok(c):
            why = "нет фьючерса MEXC с торговлей через API"
        else:
            why = pump_block(c)
        if why:
            skip[c] = why
            continue
        venues, extra, mfee, net, fee = rebuy_venues(c)
        if not venues:
            skip[c] = "нет открытого пути докупки → HTX"
            continue
        if mfee is not None and mfee / SLOT > MAX_FEE:
            skip[c] = f"пачка ${mfee:.2f} дороже {MAX_FEE*100:.1f}% слота"
            continue
        cands.append((sc[c], c, venues, extra, net, fee))
        if len(cands) >= 5:
            break
    A["skip"] = dict(list(skip.items())[:10])
    return cands

def decide(now):
    """Раз в EVERY_MIN: 1) монета без дохода (живой итог и бумага × K) — на выход; 2) свободный слот — заход в
    лучшую, если заход окупается; 3) слоты заняты — замена, если выигрыш за XA_SWAP_HORIZON_D суток больше её цены."""
    if not paper_ready():
        A["why"] = "жду, пока загрузится бумага (после перезапуска ~3 мин)"
        return
    if now - A.get("boot", 0) < WARMUP:
        A["why"] = f"прогрев после перезапуска: решений нет ещё {(A['boot'] + WARMUP - now) / 60:.0f} мин"
        return
    k = calib()
    active = [c for c, v in A["coins"].items() if v["mode"] == "active"]
    sc = {c: score(c, k) for c in universe() | set(active)}
    A["scores"] = dict(list(sorted(sc.items(), key=lambda kv: -kv[1]))[:15])
    cands = candidates(sc)
    A["cands"] = [[c[1], c[0], c[2]] for c in cands]
    held = [c for c in active if now - A["coins"][c]["since"] >= HOLD_H * 3600 and c not in pinned()
            and not A["coins"][c].get("hunt")]
    ev = {}
    for c in active:
        ev[c] = {"живой_$_сутки_48ч": live_day(c), "бумага_×K_$_сутки": round(paper_rate(c) * k, 2),
                 "держим_ч": round((now - A["coins"][c]["since"]) / 3600, 1)}
    A["eval"] = ev
    # 1) монета без дохода
    dead = [c for c in held if ev[c]["живой_$_сутки_48ч"] is not None and ev[c]["живой_$_сутки_48ч"] < EXIT_LIVE
            and ev[c]["бумага_×K_$_сутки"] < EXIT_LIVE]
    if dead and now - A["last_exit"] >= 3600:
        c = min(dead, key=lambda x: ev[x]["живой_$_сутки_48ч"])
        why = (f"живой итог {ev[c]['живой_$_сутки_48ч']:+.2f}$/сутки за 48 ч и бумага × K "
               f"{ev[c]['бумага_×K_$_сутки']:.2f}$ — ниже ${EXIT_LIVE:.2f}")
        A["why"] = f"выход из {c}: {why}"
        if AUTO:
            exit_(c, why=why)
        else:
            propose("exit", c, why, {"why": why})
        return
    if not cands:
        A["why"] = f"держу монеты: кандидатов от ${MIN_RATE:.2f}/сутки (с поправкой K={k:.2f}) без запретов нет"
        return
    busy = len([c for c, v in A["coins"].items() if not v.get("hunt")])
    if busy < SLOTS:                                   # 2) свободный слот
        if entries_24h() >= MAX_IN_DAY:
            A["why"] = f"слот свободен, но заходов за сутки уже {entries_24h()} (лимит {MAX_IN_DAY})"
            return
        H = sys.modules.get("xahedge")
        marg = H.margin(env("XH_VENUE", "mexc")) if H else None
        if marg is not None and marg < SLOT / 2 * 1.1:
            A["why"] = f"хочу зайти в {cands[0][1]}, но на фьючерсах свободно ${marg:.2f}, нужно ~${SLOT / 2 * 1.1:.0f}"
            if time.time() - A.get("warn_m", 0) > 6 * 3600:
                A["warn_m"] = time.time()
                mm.tg(f"🧠 {A['why']}. Переведи USDT на фьючерсный счёт")
            return
        notes = []
        for s, c, venues, extra, net, fee in cands:
            cost, where = entry_venue(c, venues, extra, fee)
            if not where:
                notes.append(f"{c}: нет ${SLOT:.0f} + резерв ${RESERVE:.0f} на бирже покупки")
                continue
            if s * PAYBACK_D < cost:
                notes.append(f"{c}: ≈${s:.2f}/сутки × {PAYBACK_D:.0f} сут < цены захода ${cost:.2f}")
                continue
            why = f"оценка ≈${s:.2f}/сутки (K={k:.2f}), заход ≈${cost:.2f} окупается за {cost / s:.1f} сут, куплю на {where.upper()}"
            A["why"] = f"захожу в {c}: {why}"
            if AUTO:
                enter(c, venues, extra, net, fee)
            else:
                propose("enter", c, why, {"venues": venues, "extra": extra, "net": net, "fee": fee})
            return
        A["why"] = "слот свободен, но заход не окупается: " + "; ".join(notes[:3])
        return
    if not held:                                       # 3) слоты заняты — замена
        A["why"] = f"слоты заняты; монеты держим минимум {HOLD_H:.0f} ч — замен пока нет"
        return
    if now - A["last_exit"] < SWAP_H * 3600:
        A["why"] = f"слоты заняты; последняя замена была меньше {SWAP_H:.0f} ч назад"
        return
    weak, wc = min((sc.get(c, 0), c) for c in held)
    best, bc = cands[0][0], cands[0][1]
    cost, where = entry_venue(bc, cands[0][2], cands[0][3], cands[0][5])
    swap_cost = (cost if cost is not None else SLOT * 0.02) + SLOT * COST_EXIT
    if best - weak < GAP or (best - weak) * SWAP_D < swap_cost:
        A["why"] = (f"слоты заняты; {bc} ≈${best:.2f} против {wc} ≈${weak:.2f}: выигрыш за {SWAP_D:.0f} сут "
                    f"${(best - weak) * SWAP_D:.2f} не покрывает замену ${swap_cost:.2f} или разрыв меньше ${GAP:.2f}")
        return
    why = f"{wc} ≈${weak:.2f}/сутки, а {bc} ≈${best:.2f} — замена ${swap_cost:.2f} окупится за {swap_cost / (best - weak):.1f} сут"
    A["why"] = f"замена: {why}"
    if AUTO:
        exit_(wc, why=f"замена на {bc}")
    else:
        propose("exit", wc, why, {"why": f"замена на {bc}"})

def report_text():
    k = A.get("k", PAPER_K)
    lines = [f"🧠 Аллокатор — отчёт (поправка бумаги K={k:.2f})", "Наши монеты:"]
    for c, v in A.get("coins", {}).items():
        n, pnl = live_trades(c, 24)
        ld, s = live_day(c), score(c, k)
        icon = "↩️" if v["mode"] == "exit" else ("✅" if (ld or 0) >= EXIT_LIVE or s >= 1 else ("⚠️" if s >= DEAD or n else "💤"))
        who = "магазин, закреплена" if c in pinned() else "магазин"
        lines.append(f"  {icon} {c} ({'выход' if v['mode'] == 'exit' else who}): за 24 ч сделок {n}, {pnl:+.2f}$ | "
                     f"за 48 ч {'—' if ld is None else f'{ld:+.2f}$/сут'} | оценка ≈${s:.2f}/сут")
    cs = A.get("cands") or []
    lines.append("Кандидаты: " + (", ".join(f"{c} ≈${s:.2f}/сут" for c, s, v in cs[:3]) if cs else "нет"))
    bans = {c: b[1] for c, b in (A.get("ban") or {}).items() if b[0] > time.time()}
    if bans:
        lines.append("Бан: " + "; ".join(f"{c} — {w[:60]}" for c, w in list(bans.items())[:5]))
    cash = []
    for name, key, fn in (("HTX", "x", lambda: api.hx_free("USDT")), ("Gate", "GT_KEY", lambda: api.gt_free("USDT")),
                          ("MEXC", "x", lambda: api.mx_free("USDT")), ("KuCoin", "KC_KEY", lambda: api.kc_free("USDT", "trade"))):
        if key != "x" and not getattr(api, key, ""):
            continue
        try:
            cash.append(f"{name} ${fn():.0f}")
        except Exception:
            pass
    lines.append(f"USDT: {', '.join(cash) or '—'} (на заход нужно ${SLOT:.0f} + резерв ${RESERVE:.0f} на одной бирже)")
    lines.append(f"Заходов за сутки: {entries_24h()} из {MAX_IN_DAY}")
    lines.append(f"Решение: {A.get('why') or '—'}")
    return "\n".join(lines)

def cycle():
    now = time.time()
    ensure_paper(now)
    refresh_venues()
    cleanup()
    snap_live(now)
    if ON:
        decide(now)
    if now - A.get("rep_t", 0) >= REPORT_H * 3600 and paper_ready():
        A["rep_t"] = now
        mm.tg(report_text())
    save()

def loop():
    load()                                             # сразу: магазин увидит монеты аллокатора без паузы
    A["boot"] = time.time()
    time.sleep(120)
    say(f"Аллокатор v4 запущен: {'сам решает' if AUTO else 'предлагает, вы подтверждаете'} | слоты {SLOTS} × ${SLOT:.0f} | "
        f"шорт до покупки, заход от ${MIN_RATE:.2f}/сут с окупаемостью ≤ {PAYBACK_D:.0f} сут, не больше {MAX_IN_DAY} в сутки, "
        f"держим ≥ {HOLD_H:.0f} ч | первые {WARMUP / 60:.0f} мин решений нет")
    while True:
        try:
            cycle()
        except Exception as e:
            log.exception("xalloc")
            mm.tg(f"❗ Аллокатор: {str(e)[:200]}")
        time.sleep(EVERY_MIN * 60)

# ---------- страницы ----------
def ok():
    return mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN

@mm.app.route("/alloc")
def alloc_page():
    if not ok():
        return "forbidden", 403
    now = time.time()
    return jsonify({"version": VERSION, "on": ON, "auto": AUTO, "slots": SLOTS, "slot_usd": SLOT, "coins": A.get("coins"),
                    "why": A.get("why"), "candidates": A.get("cands"), "почему_не_берём": A.get("skip"),
                    "оценка_держимых": A.get("eval"), "K_бумага_в_жизнь": A.get("k", PAPER_K),
                    "заходов_за_сутки": entries_24h(),
                    "бан": {c: {"до": time.strftime("%d.%m %H:%M", time.gmtime(b[0] + 8 * 3600)), "почему": b[1]}
                            for c, b in (A.get("ban") or {}).items() if b[0] > now},
                    "пауза_после_выхода_ч": {c: round(cooldown(c), 1) for c in A.get("exited", {}) if cooldown(c) > 0},
                    "scores": A.get("scores"), "pending": A.get("pending"), "log": A.get("log", [])[-15:]})

@mm.app.route("/alloc/exit")
def alloc_exit():
    """Выйти из монеты вручную: /alloc/exit?t=...&coin=NES (&fast=1 — сразу порциями, без ожидания окон)"""
    if not ok():
        return "forbidden", 403
    c = request.args.get("coin", "").upper()
    if c not in A.get("coins", {}):
        return "такой монеты нет"
    fast = request.args.get("fast") == "1"
    if A["coins"][c].get("mode") == "exit":            # уже выходит — можно только ускорить
        if fast:
            A["coins"][c]["exit_h"] = 0
            save()
            return f"выход из {c} ускорен: остаток порциями"
        return f"{c} уже на выходе"
    exit_(c, exit_h=0 if fast else None, why="вручную")
    save()
    return f"выход из {c} начат"

@mm.app.route("/alloc/check")
def alloc_check():
    """Проверка монеты перед заходом: /alloc/check?t=...&coin=PONS (ничего не покупает)."""
    if not ok():
        return "forbidden", 403
    c = request.args.get("coin", "").upper()
    k = A.get("k", PAPER_K)
    venues, extra, mfee, net, fee = rebuy_venues(c)
    cost, where = entry_venue(c, venues, extra, fee)
    busy = len([x for x, v in A.get("coins", {}).items() if not v.get("hunt") and v.get("mode") != "exit"])
    s = score(c, k)
    out = {"монета": c, "уже_в_работе": c in A.get("coins", {}), "фьючерс_MEXC_с_API": hedge_ok(c),
           "бан": banned(c), "пауза_после_выхода_ч": round(cooldown(c), 1), "памп_или_пик": pump_block(c),
           "биржи_докупки": venues, "пачка_$": mfee, "лимит_пачки_$": round(MAX_FEE * SLOT, 2),
           "купить_запас_на": where, "цена_захода_и_выхода_$": cost,
           "бумага_$_сутки": paper_rate(c), "K": k, "оценка_$_сутки": s,
           "окупится_за_сут": round(cost / s, 1) if cost and s > 0 else None,
           "слоты": f"{busy} из {SLOTS} (без выходящих)", "заходов_за_сутки": f"{entries_24h()} из {MAX_IN_DAY}"}
    try:
        out["стакан_HTX_$"] = round(htx_depth(c), 2)
    except Exception as e:
        out["стакан_HTX_$"] = f"ошибка: {str(e)[:80]}"
    PS = sys.modules.get("xpscout")
    if PS:
        out["разведка_24ч"] = [x for x in (PS.summary(f"{c}:{v}", 24) for v in ("kucoin", "gate")) if x]
    why = []
    if not out["фьючерс_MEXC_с_API"]:
        why.append("нет фьючерса MEXC с торговлей через API")
    if out["бан"]:
        why.append(f"бан: {out['бан']}")
    if out["пауза_после_выхода_ч"] > 0:
        why.append("недавно вышли из монеты")
    if out["памп_или_пик"]:
        why.append(out["памп_или_пик"])
    if not venues:
        why.append("нет открытого пути докупки → HTX")
    if mfee is not None and mfee / SLOT > MAX_FEE:
        why.append(f"пачка дороже ${MAX_FEE * SLOT:.2f}")
    if not where:
        why.append(f"нет ${SLOT:.0f} + резерв ${RESERVE:.0f} ни на одной бирже")
    elif s * PAYBACK_D < (cost or 0):
        why.append(f"заход ${cost:.2f} не окупается за {PAYBACK_D:.0f} сут при ≈${s:.2f}/сут")
    out["можно_заходить"] = "да" if not why else "нет: " + "; ".join(why)
    return jsonify(out)

@mm.app.route("/alloc/enter")
def alloc_enter():
    """Ручной заход: /alloc/enter?t=...&coin=PONS. Шорт — до покупки. Бан, паузу и фильтр пампа обходит только &force=1"""
    if not ok():
        return "forbidden", 403
    c = request.args.get("coin", "").upper()
    force = request.args.get("force") == "1"
    if c in A.get("coins", {}):
        return f"{c} уже в работе"
    busy = len([x for x, v in A["coins"].items() if not v.get("hunt") and v.get("mode") != "exit"])
    if busy >= SLOTS:
        return f"слоты заняты ({busy} из {SLOTS}) — сначала выйди из слабой монеты (/alloc/exit) или подними XA_SLOTS"
    if not hedge_ok(c):
        return f"у {c} нет фьючерса MEXC с торговлей через API — нечем хеджировать"
    if not force:
        for why in (banned(c) and f"бан: {banned(c)}", cooldown(c) > 0 and f"недавно вышли, ещё {cooldown(c):.0f} ч",
                    pump_block(c)):
            if why:
                return f"{c}: {why}. Если уверен — добавь &force=1"
    venues, extra, mfee, net, fee = rebuy_venues(c)
    if not venues:
        return f"у {c} нет открытого пути докупки на HTX"
    if mfee is not None and mfee / SLOT > MAX_FEE:
        return f"пачка {c} стоит ${mfee}: дороже {MAX_FEE * 100:.1f}% слота"
    H = sys.modules.get("xahedge")
    marg = H.margin(env("XH_VENUE", "mexc")) if H else None
    if marg is not None and marg < SLOT / 2 * 1.1:
        return f"на фьючерсах MEXC свободно ${marg:.2f}, нужно ~${SLOT / 2 * 1.1:.0f} — пополни и повтори"
    done = enter(c, venues, extra, net, fee, manual=True)
    save()
    return f"заход в {c}: " + ("готово — подробности в Telegram" if done else "не удался — причина в Telegram")

@mm.app.route("/alloc/unblock")
def alloc_unblock():
    """Снять запреты монеты: суточный запрет биржи, бан и паузу после выхода: /alloc/unblock?t=...&coin=EPIC"""
    if not ok():
        return "forbidden", 403
    c = request.args.get("coin", "").upper()
    bad = A.setdefault("badbuy", {})
    gone = [k.split(":")[1] for k in list(bad) if k.split(":")[0] == c]
    for v in gone:
        bad.pop(f"{c}:{v}", None)
    if A.get("ban", {}).pop(c, None):
        gone.append("бан")
    if A.get("exited", {}).pop(c, None):
        gone.append("пауза после выхода")
    save()
    return f"{c}: снято — {', '.join(gone)}" if gone else f"{c}: запретов не было"

@mm.app.route("/alloc/ban")
def alloc_ban():
    """Забанить монету вручную: /alloc/ban?t=...&coin=BLEND&days=30"""
    if not ok():
        return "forbidden", 403
    c = request.args.get("coin", "").upper()
    try:
        d = float(request.args.get("days", str(BAN_D)))
    except ValueError:
        d = BAN_D
    ban(c, d, "вручную")
    save()
    return f"{c}: бан на {d:.0f} дн."

@mm.app.route("/alloc/now")
def alloc_now():
    """Проверить прямо сейчас (после пополнения или добавления адреса): /alloc/now?t=..."""
    if not ok():
        return "forbidden", 403
    if not A:
        load()
        A["boot"] = time.time()
    A.pop("wl", None)
    cycle()
    return jsonify({"coins": list(A.get("coins", {})), "why": A.get("why"), "scores": A.get("scores"),
                    "pending": A.get("pending")})

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
        (A.get("coins", {}).get(coin) or {}).pop("gt_try", None)
        out[coin] = r
    return jsonify(out)
