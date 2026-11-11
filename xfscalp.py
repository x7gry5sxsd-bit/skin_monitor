# xfscalp.py — СКАЛЬП «СБРОС ПОСЛЕ ФАНДИНГА». По умолчанию бумага (XFS_MODE=paper); XFS_MODE=live — торгует на Gate.
#   ЗАВИСИМОСТЬ. Когда у фьючерса сильно отрицательная ставка фандинга (шорты платят лонгам 1% и больше за один
#   расчёт), лонг перед расчётом — это «купон»: держишь позицию в момент расчёта — получаешь ставку. Поэтому перед
#   расчётом монету покупают и держат. Сразу после расчёта купон оплачен, держать больше незачем — и цена обязана
#   упасть примерно на величину ставки. Рынок делает это не мгновенно: через 1 с пройдено ~2/3 пути, остальное —
#   за следующие минуты (те, кто держал ради фандинга, выходят постепенно). Этот «хвост» и забирает бот.
#   ПРАВИЛО. В ровный час расчёта, если ставка ≤ −XFS_MIN_RATE (1%) и по монете идёт живая торговля (за 25 с перед
#   расчётом наторговано от XFS_MIN_ACT = $3000): через XFS_DELAY_S (1,5 с) ПОСЛЕ расчёта — шорт по рынку; выход
#   по рынку через XFS_HOLD_S (300 с); стоп +XFS_SL (5%) на бирже (по последней цене, как в проверке). Шорт открыт
#   после снимка позиций, поэтому сам фандинг не платит — бот проверяет это по каждой живой сделке и встаёт на
#   паузу, если биржа его списала.
#   ПРОВЕРКА 11.11 на полной ленте сделок Gate за 178 дней (16.05–11.11): все расчёты со ставкой ≤ −1% и оборотом
#   от $3000 — 373 сделки по 112 монетам, ~2 в сутки. Цена входа и выхода — по реальным сделкам той же стороны на
#   нужный объём, комиссия тейкера 0,10% и стоп 5% (по каждой сделке на всём пути) учтены. При сделке $100:
#   в среднем +0,85% на сделку (медиана +0,59%), в плюсе 60%, стопов 8%; первые 60% истории +0,66%, отложенные 40%
#   +1,21%; без 10 лучших сделок +0,46%; худшая −6,1%; худшая просадка −$34. При $300: +0,80%. Вход на 3 с позже:
#   +0,73%. Выход через 180 с: +0,62%. Без стопа: +0,95%, но худшая −13%. По монетам (бутстреп 95%): +0,5…+1,3%.
#   Где эффекта нет: ставка слабее 1% (−0,1%); положительная ставка; тихие монеты (оборот меньше $3000): +0,15%,
#   от нуля не отличить — бот их пропускает.
#   ХОД ЦИКЛА (время — по часам сервера Gate, сверяется перед каждым расчётом):
#     −45 с: тикеры → кандидаты (ставка близка к порогу и расчёт у контракта именно в этот час);
#     −12 с: сверка часов;  −8 с: финальная ставка, монеты с позицией на Gate пропускаются;
#     −5 с: лента сделок → оборот за 25 с; отбор до XFS_MAX_POS монет (самая отрицательная ставка — первой), плечо;
#     +XFS_DELAY_S: шорт по рынку и сразу стоп на бирже;  +XFS_HOLD_S: закрытие по рынку, итог и фандинг — с биржи.
#   РАЗМЕР. XFS_USD ($100) на сделку, плечо XFS_LEV (5). Первые XFS_PROBE_N (5) живых сделок — пробные, по
#   XFS_PROBE_USD ($20): бот убеждается, что биржа не списывает фандинг, и только потом переходит на полный размер.
#   Общий убыток XFS_MAX_LOSS ($30) → пауза (/fscalp/resume). Бумага считает вход и выход по стакану на тот же
#   объём и комиссию 0,10% — как живой ордер.
#   Страницы (?t=STATS_TOKEN): /fscalp, /fscalp/brief, /fscalp/pause, /fscalp/resume. XFS_MODE=off — выключить.
import os, time, json, threading, logging
from decimal import Decimal
from flask import jsonify, request
import mm_scanner as mm
import xpaper
import xexec_api as api

def env(k, d=""):
    return os.getenv(k, d).strip()

VERSION = "xfscalp v1 · 11.11"
MODE = env("XFS_MODE", "paper").lower()                # live | paper | off
LIVE = MODE == "live"
MIN_RATE = float(env("XFS_MIN_RATE", "0.01"))          # ставка ≤ −1% за расчёт
MIN_ACT = float(env("XFS_MIN_ACT", "3000"))            # оборот за 25 с перед расчётом, $
USD = float(env("XFS_USD", "100"))                     # размер сделки, $
PROBE_USD = float(env("XFS_PROBE_USD", "20"))          # размер пробной живой сделки, $
PROBE_N = int(env("XFS_PROBE_N", "5"))                 # сколько пробных сделок без списания фандинга нужно
LEV = int(float(env("XFS_LEV", "5")))
MAX_POS = int(env("XFS_MAX_POS", "3"))                 # сделок на один расчёт
DELAY = max(1.0, float(env("XFS_DELAY_S", "1.5")))     # секунд после расчёта (не меньше 1 — запас от снимка позиций)
HOLD = float(env("XFS_HOLD_S", "300"))
SL = float(env("XFS_SL", "0.05"))
MAX_LOSS = float(env("XFS_MAX_LOSS", "30"))
FEE = 0.0005                                           # тейкер, одна сторона (для бумаги)
GTF = "https://api.gateio.ws/api/v4/futures/usdt"
GTS = "https://api.gateio.ws/api/v4/spot/time"
TXT = "t-xfs"
PARALLEL = True                                        # одновременные запросы (в тестах выключается)
log = logging.getLogger("xfscalp")
S = api.S                                              # одна сессия на всё: соединения с Gate прогреты заранее
L = {}
LOCK = threading.RLock()
CLOCK = {"off": 0.0, "rtt": None, "t": 0}

class Skip(Exception):
    pass

def lt(t=None, f="%d.%m %H:%M"):
    return time.strftime(f, time.gmtime((time.time() if t is None else t) + 8 * 3600))

def say(text, live=None):
    live = LIVE if live is None else live
    mm.tg(("⚡ Фандинг-скальп: " if live else "🧪⚡ Фандинг-скальп [бумага]: ") + text)

def zero():
    return {"n": 0, "win": 0, "sl": 0, "pnl": 0.0, "pct": 0.0}

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'xfscalp_v1'", fetch=True)
    L.update(json.loads(r[0][0]) if r else {})
    for k, v in (("pos", {}), ("done", []), ("paused", ""), ("last", {}), ("warned", {}), ("probe", 0),
                 ("tot", zero()), ("tot_p", zero())):
        L.setdefault(k, v)
    L["loaded"] = 1

def save():
    if not L.get("loaded"):
        return
    try:
        with LOCK:
            js = json.dumps(L)
        xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('xfscalp_v1', %s)
                    ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (js,))
    except Exception:
        log.exception("xfscalp save")

def fan(fn, items, width=8):
    """fn для каждого элемента одновременно, не больше width сразу (в тестах — по очереди).
    fn не должна бросать исключения."""
    items = list(items)
    if not PARALLEL or len(items) < 2:
        return [fn(x) for x in items]
    out = [None] * len(items)
    def run(i, x):
        out[i] = fn(x)
    for k in range(0, len(items), width):
        th = [threading.Thread(target=run, args=(i, items[i]), daemon=True)
              for i in range(k, min(k + width, len(items)))]
        for t in th:
            t.start()
        for t in th:
            t.join(30)
    return out

# ---------- время ----------
def sync_clock():
    """Сдвиг часов сервера Gate относительно наших, с. Берём замер с самым быстрым ответом."""
    best = None
    for _ in range(3):
        try:
            t0 = time.time()
            d = S.get(GTS, timeout=5).json()
            t1 = time.time()
            srv = float(d["server_time"]) / 1000
            if best is None or t1 - t0 < best[1]:
                best = (srv - (t0 + t1) / 2, t1 - t0)
        except Exception:
            pass
        time.sleep(0.15)
    if best is None:
        raise Skip("не сверил часы с Gate")
    CLOCK.update(off=best[0], rtt=best[1], t=time.time())
    if best[1] > 1.0:
        raise Skip(f"связь с Gate медленная (ответ {best[1]:.2f} с) — время входа ненадёжно")
    return best

def srv_now():
    return time.time() + CLOCK["off"]

def wait_until(ts):
    """Спим до момента ts по часам сервера Gate."""
    while True:
        d = ts - srv_now()
        if d <= 0:
            return
        time.sleep(min(d, 0.5))

def warm(n):
    """Открыть n соединений с Gate заранее: ордер не ждёт установки соединения (это 0,3–0,7 с)."""
    def w(_):
        try:
            S.get(GTS, timeout=5)
        except Exception:
            pass
    fan(w, range(n + 2))                                   # с запасом: сессию делят и другие модули

# ---------- биржа (публично) ----------
def rnd(x, tick):
    try:
        t = Decimal(str(tick))
    except Exception:
        t = Decimal(0)
    if t <= 0:
        return float(f"{x:.8g}")
    return float((Decimal(str(x)) / t).quantize(Decimal(1)) * t)

def ps(x):
    return format(Decimal(str(x)).normalize(), "f")

def tickers():
    out = {}
    for x in S.get(f"{GTF}/tickers", timeout=15).json() or []:
        c = str(x.get("contract", ""))
        if not c.endswith("_USDT"):
            continue
        try:
            out[c[:-5]] = {"rate": float(x.get("funding_rate") or 0), "last": float(x.get("last") or 0)}
        except (TypeError, ValueError):
            continue
    return out

def quote(coin):
    """Лучшие цены: {bid, ask, last, mark}; None — нет данных."""
    r = S.get(f"{GTF}/tickers", params={"contract": f"{coin}_USDT"}, timeout=8)
    d = r.json() if r.status_code == 200 else None
    if not d or not isinstance(d, list):
        return None
    x = d[0]
    last = float(x.get("last") or 0)
    if last <= 0:
        return None
    return {"bid": float(x.get("highest_bid") or 0) or last, "ask": float(x.get("lowest_ask") or 0) or last,
            "last": last, "mark": float(x.get("mark_price") or 0) or last}

def book_px(coin, side, n):
    """Средняя цена рыночного ордера на n контрактов по стакану: 'sell' — по покупателям, 'buy' — по продавцам.
    Стакана не хватило — остаток считаем на 0,3% хуже последнего уровня. None — нет данных."""
    r = S.get(f"{GTF}/order_book", params={"contract": f"{coin}_USDT", "limit": 50}, timeout=8)
    d = r.json() if r.status_code == 200 else None
    need, cost, last = float(n), 0.0, 0.0
    for x in (d or {}).get("bids" if side == "sell" else "asks") or []:
        p, s = float(x["p"]), abs(float(x["s"]))
        take = min(need, s)
        cost += take * p
        need -= take
        last = p
        if need <= 0:
            break
    if last <= 0 or n <= 0:
        return None
    if need > 0:
        cost += need * last * (0.997 if side == "sell" else 1.003)
    return cost / n

def contract(coin):
    r = S.get(f"{GTF}/contracts/{coin}_USDT", timeout=10)
    d = r.json() if r.status_code == 200 else None
    if not isinstance(d, dict) or not d.get("quanto_multiplier"):
        raise Skip("нет данных контракта")
    if d.get("in_delisting"):
        raise Skip("фьючерс снимается с торгов")
    return d

def activity(coin, mult, T):
    """Оборот по ленте сделок за 25 с (от T−30 до T−5), $. None — лента не ответила."""
    try:
        r = S.get(f"{GTF}/trades", params={"contract": f"{coin}_USDT", "from": int(T - 30), "to": int(T - 5),
                                           "limit": 1000}, timeout=4)
        d = r.json() if r.status_code == 200 else None
        if not isinstance(d, list):
            return None
        return sum(abs(float(x.get("size") or 0)) * mult * float(x.get("price") or 0) for x in d)
    except Exception:
        return None

# ---------- биржа (по ключу) ----------
def positions():
    return {x.get("contract", "")[:-5]: x for x in api.gt("GET", "/futures/usdt/positions") or []
            if float(x.get("size") or 0) != 0}

def find_all(tries=2):
    """Все позиции на бирже; None — биржа не ответила."""
    for i in range(tries):
        try:
            return positions()
        except Exception:
            if i + 1 < tries:
                time.sleep(0.3)
    return None

def find_pos(coin, tries=3):
    """Шорт по монете на бирже: позиция, False — её нет, None — биржа не ответила."""
    for _ in range(tries):
        try:
            x = positions().get(coin)
            return x if x and float(x.get("size") or 0) < 0 else False
        except Exception:
            time.sleep(1.0)
    return None

def adopt(p, x):
    """Размер и цену входа взять из позиции на бирже."""
    p["px"] = float(x.get("entry_price") or 0) or p.get("px") or p["p_pre"]
    p["n"] = abs(int(float(x.get("size") or 0))) or p["n"]
    p["sl"] = rnd(p["px"] * (1 + SL), p["tick"])
    p.pop("unsure", None)

def protect(p):
    """Стоп на бирже. Не встал — p['stop_ok'] = False, стоп ведёт сам бот."""
    try:
        stop_place(p)
        p["stop_ok"] = True
    except Exception as e:
        p["stop_ok"], p["stop_err"] = False, str(e)[:120]

def stop_place(p):
    api.gt("POST", "/futures/usdt/price_orders", body={
        "initial": {"contract": f"{p['coin']}_USDT", "size": 0, "price": "0", "close": True, "tif": "ioc",
                    "reduce_only": True, "text": TXT},
        "trigger": {"strategy_type": 0, "price_type": 0, "price": ps(rnd(p["sl"], p["tick"])), "rule": 1,
                    "expiration": 86400},
        "order_type": "close-short-position"})

def stop_cancel(coin):
    try:
        api.gt("DELETE", "/futures/usdt/price_orders", {"contract": f"{coin}_USDT"})
    except Exception:
        pass

def closed_info(coin, t0):
    """Закрытая позиция с биржи: (цена выхода, итог $, фандинг $) или None."""
    for x in api.gt("GET", "/futures/usdt/position_close", {"contract": f"{coin}_USDT", "limit": 10}) or []:
        if x.get("side") == "short" and float(x.get("time") or 0) >= t0 - 5:
            f = lambda k: float(x[k]) if x.get(k) not in (None, "") else None
            return f("long_price"), f("pnl"), f("pnl_fund")
    return None

def fund_book(coin, t0):
    """Фандинг по монете из истории счёта с момента t0, $ (0 — не было). None — не узнал."""
    try:
        rows = api.gt("GET", "/futures/usdt/account_book", {"type": "fund", "from": int(t0) - 5, "limit": 100})
    except Exception:
        return None
    name = f"{coin}_USDT"
    return sum(float(x.get("change") or 0) for x in rows or []
               if x.get("contract") == name or name in str(x.get("text") or ""))

# ---------- отбор ----------
def size_now():
    """Размер сделки сейчас: пока биржа не подтвердила PROBE_N живых сделок без фандинга — пробный."""
    return PROBE_USD if LIVE and L.get("probe", 0) < PROBE_N else USD

def scan(T):
    """Кандидаты за ~45 с до расчёта: ставка близка к порогу и расчёт у контракта именно в момент T."""
    tk = tickers()
    near = sorted((c for c, x in tk.items() if x["rate"] <= -MIN_RATE * 0.7 and x["last"] > 0),
                  key=lambda c: tk[c]["rate"])               # самые отрицательные ставки — первыми
    def one(coin):
        try:
            d = contract(coin)
            if d.get("contract_type") or d.get("is_pre_market"):   # только обычные крипто-фьючерсы — на них проверка
                return None
            if abs(int(float(d.get("funding_next_apply") or 0)) - T) > 5:
                return None
            return {"coin": coin, "mult": float(d["quanto_multiplier"]), "tick": d.get("order_price_round"),
                    "minv": max(1, int(float(d.get("order_size_min") or 1))),
                    "maxv": int(float(d.get("order_size_max") or 0)) or None,
                    "lev_max": int(float(d.get("leverage_max") or LEV))}
        except Exception:
            return None
    return [c for c in fan(one, near[:40]) if c]

def select(cands, T):
    """Финальная ставка (за ~8 с), оборот (за ~5 с), отбор. Возвращает (к входу, пропуски)."""
    tk = tickers()
    held = {}
    if LIVE:
        held = find_all()
        if held is None:
            raise Skip("Gate не отдал список позиций — не знаю, что уже открыто")
    usd = size_now()
    short, skips = [], []
    for c in sorted(cands, key=lambda c: tk.get(c["coin"], {}).get("rate", 0)):
        coin, x = c["coin"], tk.get(c["coin"])
        if not x or x["last"] <= 0:
            continue
        if x["rate"] > -MIN_RATE:
            skips.append(f"{coin}: ставка {x['rate'] * 100:+.2f}% — слабее порога −{MIN_RATE * 100:g}%")
            continue
        if coin in held or coin in L["pos"]:
            skips.append(f"{coin}: на Gate уже есть позиция — не смешиваю")
            continue
        n = int(usd / (x["last"] * c["mult"]))
        if n < c["minv"]:
            skips.append(f"{coin}: минимальный ордер ≈${x['last'] * c['mult'] * c['minv']:.0f} больше сделки ${usd:.0f}")
            continue
        if c["maxv"]:
            n = min(n, c["maxv"])
        short.append(dict(c, rate=x["rate"], p_pre=x["last"], n=n, lev=max(1, min(LEV, c["lev_max"]))))
    short = short[:MAX_POS + 3]
    if not short:
        return [], skips
    wait_until(T - 5)
    pick = []
    for c, a in zip(short, fan(lambda c: activity(c["coin"], c["mult"], T), short)):
        if a is None:
            skips.append(f"{c['coin']}: лента сделок не ответила — пропуск")
        elif a < MIN_ACT:
            skips.append(f"{c['coin']}: ставка {c['rate'] * 100:+.2f}%, но тихо — за 25 с наторговано ${a:.0f}, нужно от ${MIN_ACT:.0f}")
        elif len(pick) >= MAX_POS:
            skips.append(f"{c['coin']}: ставка {c['rate'] * 100:+.2f}% — сверх лимита {MAX_POS} сделок на расчёт")
        else:
            pick.append(dict(c, act=round(a)))
    if LIVE and pick:
        try:
            a = api.gt("GET", "/futures/usdt/accounts") or {}
        except Exception as e:
            raise Skip(f"Gate не отдал счёт фьючерсов ({str(e)[:80]})")
        if a.get("in_dual_mode") or a.get("position_mode", "single") != "single":
            raise Skip("на Gate двусторонний режим позиций — нужен одиночный (One-way)")
        need = sum(p["n"] * p["mult"] * p["p_pre"] / p["lev"] for p in pick) * 1.1
        if float(a.get("available") or 0) < need:
            raise Skip(f"на фьючерсах Gate свободно ${float(a.get('available') or 0):.0f}, нужно ~${need:.0f}")
        for p in list(pick):
            try:
                api.gt("POST", f"/futures/usdt/positions/{p['coin']}_USDT/leverage", {"leverage": str(p["lev"])})
            except Exception as e:
                pick.remove(p)
                skips.append(f"{p['coin']}: не выставил плечо ({str(e)[:80]}) — пропуск")
    return pick, skips

# ---------- сделка ----------
def enter(c, T):
    """Шорт по рынку. Возвращает запись позиции (и кладёт её в L['pos'])."""
    coin = c["coin"]
    p = {"coin": coin, "T": int(T), "rate": c["rate"], "p_pre": c["p_pre"], "n": c["n"], "mult": c["mult"],
         "tick": c["tick"], "lev": c["lev"], "act": c.get("act"), "mode": "live" if LIVE else "paper",
         "sent": round(srv_now() - T, 3)}
    if not LIVE:
        px = book_px(coin, "sell", c["n"])
        if not px:
            raise Skip("нет стакана на входе")
        p.update(px=px, t=time.time(), ack=round(srv_now() - T, 3))
    else:
        try:
            o = api.gt("POST", "/futures/usdt/orders", body={"contract": f"{coin}_USDT", "size": -int(c["n"]),
                                                             "price": "0", "tif": "ioc", "text": TXT}) or {}
            err = None
        except Exception as e:
            o, err = {}, str(e)[:150]
        p.update(t=time.time(), ack=round(srv_now() - T, 3))
        px = float(o.get("fill_price") or 0)
        left = abs(int(float(o.get("left") or 0)))
        if err is None and left >= c["n"]:
            raise Skip("ордер не исполнен — в стакане не было покупателей")
        if err is None and px > 0:
            p.update(px=px, n=c["n"] - left)
        else:                                              # ответа нет или он неполный — сверяемся с биржей
            x = find_pos(coin)
            if x is False:
                raise Skip(f"ордер не прошёл ({err or str(o)[:120]})")
            if x is None:                                  # биржа молчит: считаем, что шорт мог открыться
                p.update(px=c["p_pre"], unsure=1, err=err)
            else:
                adopt(p, x)
    p["sl"] = rnd(p["px"] * (1 + SL), p["tick"])
    with LOCK:
        L["pos"][coin] = p
    if LIVE:
        protect(p)
    return p

def finish(p, outcome, ex, pnl, now, fund=None, note=""):
    """Закрыть запись сделки. pnl=None — считаем по ценам и комиссии 0,10%."""
    live = p["mode"] == "live"
    nt = p["n"] * p["mult"] * p["px"]
    if pnl is None:
        pnl = nt * (p["px"] - ex) / p["px"] - nt * 2 * FEE
    pct = pnl / nt * 100 if nt else 0.0
    fresh = now - p["t"] < 3000                            # держали дольше (бот лежал) — мог пройти следующий расчёт
    paid = live and fresh and fund is not None and fund < -0.01
    rec = {"coin": p["coin"], "mode": p["mode"], "T": p["T"], "rate": p["rate"], "act": p.get("act"), "px": p["px"],
           "exit": ex, "usd": round(nt, 2), "pnl": round(pnl, 3), "pct": round(pct, 3), "outcome": outcome,
           "sent": p.get("sent"), "ack": p.get("ack"),
           "pre": round((p["px"] / p["p_pre"] - 1) * 100, 3) if p.get("p_pre") else None,
           "hold": round(now - p["t"]), "fund": fund}
    with LOCK:
        L["pos"].pop(p["coin"], None)
        L["done"] = (L["done"] + [rec])[-400:]
        t = L["tot" if live else "tot_p"]
        t["n"] += 1
        t["win"] += 1 if pnl > 0 else 0
        t["sl"] += 1 if outcome.startswith("стоп") else 0
        t["pnl"] = round(t["pnl"] + pnl, 3)
        t["pct"] = round(t["pct"] + pct, 3)
        was = L.get("probe", 0)
        if live and fresh and fund is not None and not paid:
            L["probe"] = was + 1
        if paid and not L.get("paused"):
            L["paused"] = (f"биржа списала фандинг {fund:+.2f}$ по {p['coin']}: шорт попал в снимок позиций. "
                           f"Увеличь XFS_DELAY_S (сейчас {DELAY:g})")
        if live and t["pnl"] <= -MAX_LOSS and not L.get("paused"):
            L["paused"] = f"общий убыток {t['pnl']:+.2f}$ — больше лимита ${MAX_LOSS:.0f}"
        tot = dict(t)
        probe = L.get("probe", 0)
    msg = (f"{'✅' if pnl > 0 else '❌'} {p['coin']} (ставка {p['rate'] * 100:+.2f}%): шорт {p['px']:.6g} → {ex:.6g}, {outcome}, "
           f"{pct:+.2f}% = {pnl:+.2f}$ на ${nt:.0f}{note}. Всего: {tot['n']} сд., в плюсе {tot['win']}, {tot['pnl']:+.2f}$")
    if live and fund is None:
        msg += "\n⚠️ Биржа не отдала данные о фандинге по этой сделке — проверь её в истории Gate"
    if live and was < PROBE_N <= probe:
        msg += f"\n✅ Проба пройдена: {PROBE_N} сделок без списания фандинга. Дальше сделки по ${USD:.0f}"
    if L.get("paused"):
        msg += f"\n⛔ Пауза: {L['paused']}. Снять: /fscalp/resume"
    say(msg, live=live)
    save()
    return rec

def drop(p):
    """Сделки не было (ордер не дошёл до биржи): убрать запись без счёта, снять стоп, если он встал."""
    stop_cancel(p["coin"])
    with LOCK:
        L["pos"].pop(p["coin"], None)
    say(f"{p['coin']}: ордер не дошёл до биржи — сделки нет", live=True)
    save()

def settle(p, outcome, now):
    """Позиции на бирже, похоже, уже нет: взять итог и фандинг с биржи, снять стоп, закрыть запись.
    Записи о закрытии нет, а позиция ещё есть (закрылась не вся) или биржа молчит — запись не трогаем, повторим."""
    coin = p["coin"]
    info = None
    for _ in range(4):
        try:
            info = closed_info(coin, p["t"])
        except Exception:
            info = None
        if info:
            break
        time.sleep(1.5)
    if not info:
        x = find_pos(coin, tries=2)
        if x is None or x:                                 # не знаем или шорт ещё открыт — стоп оставляем
            p["retry"] = time.time() + 3
            return
        if p.get("unsure"):                                # ордер так и не дошёл до биржи
            drop(p)
            return
    stop_cancel(coin)
    ex, pnl, fund = info if info else (None, None, None)
    if fund is None:
        fund = fund_book(coin, p["t"])
    if not ex:
        try:
            q = quote(coin)
        except Exception:
            q = None
        ex = q["ask"] if q else p["px"]
    if outcome == "?":                                     # позиция пропала до срока: стоп, вручную или ликвидация
        outcome = "стоп" if ex >= p["sl"] * 0.99 else "закрыта на бирже (вручную?)"
    finish(p, outcome, ex, pnl, now, fund=fund, note="" if info else " (итог по ценам — биржа не отдала запись)")

def manage(now=None):
    """Один проход по открытым сделкам: стоп, пропажа позиции, выход по времени."""
    now = time.time() if now is None else now
    due, held = [], None
    def gate():
        nonlocal held
        if held is None:
            try:
                held = positions()
            except Exception:
                held = False
        return held
    for coin, p in list(L["pos"].items()):
        age, live = now - p["t"], p["mode"] == "live"
        if now < p.get("retry", 0):
            continue
        if live and p.get("unsure") and now - p.get("chk", 0) >= 5:   # биржа молчала на входе — выясняем
            p["chk"] = now
            h = gate()
            if h is not False:
                x = h.get(coin)
                if not x or float(x.get("size") or 0) >= 0:
                    drop(p)
                    continue
                adopt(p, x)
                protect(p)
                say(f"{coin}: шорт подтверждён биржей — вход {p['px']:.6g}, стоп {p['sl']:.6g}"
                    + (" на бирже ✅" if p.get("stop_ok") else " веду сам"), live=True)
        if age >= HOLD:
            due.append((p, "время"))
            continue
        if not live or not p.get("stop_ok"):               # бумага и живой без стопа на бирже — стоп ведём сами
            try:
                q = quote(coin)
            except Exception:
                q = None
            if q and q["last"] >= p["sl"]:
                due.append((p, "стоп"))
                continue
        if live and not p.get("unsure") and now - p.get("chk", p["t"]) >= 10:
            p["chk"] = now
            h = gate()
            if h is not False and coin not in h:
                settle(p, "?", now)
    for p, why in due:
        coin = p["coin"]
        if p["mode"] != "live":
            try:
                ex = book_px(coin, "buy", p["n"])
            except Exception:
                ex = None
            if not ex:
                p["retry"] = now + 5                       # цены нет — повторим, запись не теряем
                continue
            finish(p, why, max(ex, p["sl"]) if why == "стоп" else ex, None, now)
            continue
        h = gate()
        if h is not False and coin not in h:               # позиции уже нет: биржевой стоп или наш прошлый ордер
            settle(p, p.pop("closing", None) or "?", now)
            continue
        try:
            api.gt("POST", "/futures/usdt/orders", body={"contract": f"{coin}_USDT", "size": 0, "price": "0",
                                                         "close": True, "tif": "ioc", "text": TXT})
        except Exception as e:
            p["fails"] = p.get("fails", 0) + 1
            p["retry"] = now + min(60, 3 * p["fails"])
            if now - L["warned"].get("cl" + coin, 0) > 600:
                L["warned"]["cl" + coin] = now
                say(f"❗ {coin}: не смог закрыть шорт ({str(e)[:120]}) — пробую снова. Не выйдет — закрой вручную на Gate",
                    live=True)
            continue
        p["closing"] = why
    for p, why in due:
        if p.get("closing") and p["coin"] in L["pos"]:
            settle(p, p["closing"], now)

def cycle(T):
    """Один расчёт фандинга в момент T (ровный час, время сервера): отбор и вход. Дальше сделки ведёт manage()."""
    info = {"T": int(T), "когда": lt(T), "кандидатов": 0, "вход": [], "пропуски": []}
    L["last"] = info
    if L.get("paused"):
        info["пропуски"].append(f"пауза: {L['paused']}")
        return info
    wait_until(T - 45)
    cands = scan(T)
    info["кандидатов"] = len(cands)
    if not cands:
        return info
    wait_until(T - 12)
    try:
        off, rtt = sync_clock()
        info["часы"] = f"сдвиг {off * 1000:+.0f} мс, ответ {rtt * 1000:.0f} мс"
        wait_until(T - 8)
        pick, skips = select(cands, T)
    except Skip as e:
        info["пропуски"].append(str(e))
        say(f"расчёт {lt(T)}: пропускаю — {e}")
        return info
    except Exception as e:                                 # сеть или биржа подвели до входа — денег в рынке нет
        log.exception("xfscalp select")
        info["пропуски"].append(f"ошибка до входа: {str(e)[:120]}")
        say(f"расчёт {lt(T)}: пропускаю — ошибка до входа ({str(e)[:120]})")
        return info
    info["пропуски"] += skips
    if not pick:
        return info
    wait_until(T - 2.5)
    warm(len(pick))
    wait_until(T + DELAY)
    def one(c):
        try:
            late = srv_now() - T - DELAY
            if late > 1.5:
                raise Skip(f"опоздал ко входу на {late:.1f} с")
            p = enter(c, T)
        except Skip as e:
            info["пропуски"].append(f"{c['coin']}: {e}")
            return
        except Exception as e:
            log.exception("xfscalp enter %s", c["coin"])
            info["пропуски"].append(f"{c['coin']}: ошибка входа {str(e)[:100]}")
            return
        info["вход"].append(f"{c['coin']} {c['rate'] * 100:+.2f}% по {p['px']:.6g} (+{p['ack']:.2f} с)")
        save()                                             # запись о сделке переживёт перезапуск
        if p["mode"] == "live" and p.get("unsure"):
            say(f"❗ {c['coin']}: ордер на шорт отправлен, но биржа не ответила ({p.get('err')}). Проверяю позицию сам; "
                f"если шорт открылся — закрою его через {HOLD:g} с", live=True)
        elif p["mode"] == "live":
            say(f"шорт {c['coin']}: ставка {c['rate'] * 100:+.2f}%, вход {p['px']:.6g} через {p['ack']:.2f} с после расчёта "
                f"(цена до расчёта {c['p_pre']:.6g}, {(p['px'] / c['p_pre'] - 1) * 100:+.2f}%), "
                f"${p['n'] * p['mult'] * p['px']:.0f} ×{p['lev']}"
                + (f" — пробная {L.get('probe', 0) + 1} из {PROBE_N}" if L.get("probe", 0) < PROBE_N else "")
                + f", стоп {p['sl']:.6g}" + (" на бирже ✅" if p.get("stop_ok") else
                                             f" — ⚠️ на бирже не встал ({p.get('stop_err', '')}), веду сам")
                + f". Выход через {HOLD:g} с", live=True)
    fan(one, pick)
    return info

def orphans():
    """После перезапуска: наш стоп на бирже есть, а записи о сделке нет (бот упал в секунду входа) —
    взять шорт под управление: он закроется на первом же проходе."""
    if not LIVE:
        return
    try:
        rows = api.gt("GET", "/futures/usdt/price_orders", {"status": "open", "limit": 100}) or []
        mine = {str((x.get("initial") or {}).get("contract", ""))[:-5] for x in rows
                if (x.get("initial") or {}).get("text") == TXT}
        mine = {c for c in mine if c and c not in L["pos"]}
        if not mine:
            return
        held = positions()
        for coin in mine:
            x = held.get(coin)
            if not x or float(x.get("size") or 0) >= 0:
                stop_cancel(coin)                          # стоп без позиции — снять
                continue
            if float(x.get("entry_price") or 0) <= 0:
                continue
            d = contract(coin)
            p = {"coin": coin, "T": int(time.time()), "rate": 0.0, "n": 0, "mult": float(d["quanto_multiplier"]),
                 "tick": d.get("order_price_round"), "lev": LEV, "mode": "live", "t": time.time() - HOLD,
                 "p_pre": float(x.get("entry_price") or 0), "stop_ok": True}
            adopt(p, x)
            L["pos"][coin] = p
            say(f"после перезапуска нашёл свой шорт {coin} без записи — закрываю", live=True)
    except Exception:
        log.exception("xfscalp orphans")

def tick():
    """Один шаг главного цикла. Возвращает, сколько секунд спать до следующего шага."""
    if L["pos"]:                                           # открытые сделки — раз в секунду, пока не закроются
        with LOCK:
            manage()
        return 1.0
    now = srv_now()
    T = (int(now) // 3600 + 1) * 3600
    if T - now > 50:
        return min(T - now - 50, 300)
    if MODE != "off":
        cycle(T)
    return 0.0 if L["pos"] else max(1.0, T + 5 - srv_now())

def loop():
    time.sleep(40)
    while not L.get("loaded"):
        try:
            load()
        except Exception:
            log.exception("xfscalp load")
            time.sleep(60)
    try:
        sync_clock()
    except Exception:
        pass
    orphans()
    while True:
        try:
            time.sleep(tick())
        except Exception:
            log.exception("xfscalp")
            time.sleep(5)

# ---------- страницы ----------
def ok():
    return mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN

def tot_line(t):
    n = t.get("n", 0)
    return (f"сделок {n}, в плюсе {t.get('win', 0)}" + (f" ({t.get('win', 0) * 100 // n}%)" if n else "")
            + f", стопов {t.get('sl', 0)}, {t.get('pnl', 0):+.2f}$" + (f", в среднем {t.get('pct', 0) / n:+.2f}% на сделку" if n else ""))

def brief():
    out = [f"⚡ {VERSION} — {'ЖИВОЙ (Gate)' if LIVE else 'бумага' if MODE == 'paper' else MODE}, {lt(None)}",
           f"Шорт через {DELAY:g} с после расчёта при ставке ≤ −{MIN_RATE * 100:g}% и обороте от ${MIN_ACT:.0f} за 25 с · "
           f"${size_now():.0f} ×{LEV} · выход через {HOLD:g} с · стоп +{SL * 100:g}% · сделок на расчёт ≤{MAX_POS}"
           + (f" · ⛔ {L['paused']}" if L.get("paused") else "")]
    if LIVE and L.get("probe", 0) < PROBE_N:
        out.append(f"Проба: первые сделки по ${PROBE_USD:.0f}, без списания фандинга прошло {L.get('probe', 0)} из {PROBE_N}; "
                   f"потом по ${USD:.0f}")
    if LIVE or (L.get("tot") or {}).get("n"):
        out.append("Живые: " + tot_line(L.get("tot") or {}))
    if not LIVE or (L.get("tot_p") or {}).get("n"):
        out.append("Бумага: " + tot_line(L.get("tot_p") or {}))
    la = L.get("last") or {}
    if la:
        out.append(f"Последний расчёт {la.get('когда')}: кандидатов {la.get('кандидатов', 0)}"
                   + (f", вход: {'; '.join(la['вход'])}" if la.get("вход") else ", входов нет")
                   + (f" · часы: {la['часы']}" if la.get("часы") else ""))
        for s in (la.get("пропуски") or [])[:6]:
            out.append(f"  пропуск: {s}")
    for p in (L.get("pos") or {}).values():
        out.append(f"Открыт{'' if p['mode'] == 'live' else ' (бумага)'}: {p['coin']} с {lt(p['t'], '%H:%M:%S')} по {p['px']:.6g}, "
                   f"стоп {p['sl']:.6g}")
    for d in (L.get("done") or [])[-10:]:
        out.append(f"  {lt(d['T'])} {d['coin']}{'' if d['mode'] == 'live' else ' (б)'} ставка {d['rate'] * 100:+.2f}%: {d['outcome']}, "
                   f"{d['pct']:+.2f}% = {d['pnl']:+.2f}$ (вход через {d.get('ack') or 0:.2f} с, к цене до расчёта {d.get('pre') or 0:+.2f}%)")
    return "\n".join(out)

@mm.app.route("/fscalp")
def fscalp_page():
    if not ok():
        return "forbidden", 403
    with LOCK:
        return jsonify({"версия": VERSION, "режим": MODE, "пауза": L.get("paused"), "живые": L.get("tot"),
                        "бумага": L.get("tot_p"), "проба_пройдено": L.get("probe"), "последний_расчёт": L.get("last"),
                        "открыто": L.get("pos"), "сделки": (L.get("done") or [])[-200:], "часы": CLOCK,
                        "настройки": {"ставка_от_%": MIN_RATE * 100, "оборот_от_$": MIN_ACT, "сделка_$": USD,
                                      "пробная_$": PROBE_USD, "пробных": PROBE_N, "плечо": LEV, "сделок_на_расчёт": MAX_POS,
                                      "вход_через_с": DELAY, "держу_с": HOLD, "стоп_%": SL * 100, "общий_убыток_$": MAX_LOSS}})

@mm.app.route("/fscalp/brief")
def fscalp_brief():
    if not ok():
        return "forbidden", 403
    with LOCK:
        txt = brief()
    if request.args.get("tg") == "1":
        mm.tg(txt)
    return txt, 200, {"Content-Type": "text/plain; charset=utf-8"}

@mm.app.route("/fscalp/pause")
def fscalp_pause():
    if not ok():
        return "forbidden", 403
    with LOCK:
        L["paused"] = "вручную"
        save()
    return "фандинг-скальп: пауза — новых сделок нет, открытые бот доведёт до выхода. Снять: /fscalp/resume"

@mm.app.route("/fscalp/resume")
def fscalp_resume():
    if not ok():
        return "forbidden", 403
    with LOCK:
        L["paused"] = ""
        if request.args.get("reset") == "1":
            L["tot"], L["tot_p"] = zero(), zero()
        save()
    return "фандинг-скальп: работает" + (" — счёт обнулён" if request.args.get("reset") == "1" else "")

threading.Thread(target=loop, daemon=True).start()
