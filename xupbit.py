# xupbit.py — ШОРТ ПОСЛЕ ЛИСТИНГА НА UPBIT (фьючерсы Gate).
#   Идея (проверено на истории 14 мес., 86 случаев): перед стартом торгов монетой на корейской бирже Upbit её
#   разгоняют, после старта она падает. Шорт на Gate в момент старта, держим 3 дня: в плюсе 81% сделок, в среднем
#   +13%. С мая 2026 со всеми расходами (фандинг, комиссия 0,4%) и стопом 20% — около +7% на сделку.
#   Что делает: раз в XUP_EVERY_S (30 с) берёт список рынков Upbit. Появился новый рынок KRW-XXX → ждёт первой сделки
#   на Upbit → если на Gate есть фьючерс XXX_USDT — шорт на XUP_USD ($200) с плечом не выше XUP_LEV (3, изолированная
#   маржа) и сразу НА БИРЖЕ стоп +XUP_SL (20%) по справедливой цене. Через XUP_HOLD_H (72 ч) закрывает по рынку.
#   Тейка нет: прибыль в том, чтобы держать.
#   Страховка: раз в 30 с бот сам смотрит позицию; стоп на бирже пропал — ставит снова; цена выше стопа, а позиция
#   жива — закрывает сам; цена прошла 85% пути до ликвидации — закрывает сам.
#   Ограничения: позиций не больше XUP_MAX_POS (3); общий убыток модуля XUP_MAX_LOSS ($80) — пауза до /upbit/resume;
#   по монете на Gate уже есть позиция (например, автошорт) — пропуск; торги на Upbit начались больше XUP_LATE_H (6 ч)
#   назад (сервер был выключен) — только бумага.
#   Бумага: по КАЖДОМУ новому рынку (и пропущенному) пишет цену Gate на старте и через 1, 3 и 7 дней.
#   XUP_MODE: live (по умолчанию) — торгует; paper — только записывает; off — новых нет, открытые ведёт до конца.
#   Первый запуск: все рынки, что уже есть на Upbit, запоминает и не трогает — ждёт только новые.
#   Страницы (?t=STATS_TOKEN): /upbit — всё (JSON), /upbit/brief — коротко (&tg=1 — в Telegram),
#   /upbit/close?coin=XXX — закрыть сейчас, /upbit/pause, /upbit/resume.
#   v2 (03.11): + BITHUMB (вторая биржа Кореи, тот же эффект: на истории 78 листингов, стоп 20%, 72 ч, комиссия 0,4% —
#   +$13 на сделку, в плюсе 69%, обе половины периода в плюсе). Источники — XUP_SOURCES (upbit,bithumb). Одна монета
#   не шортится второй раз 5 дней (часто листинг на обеих биржах в одни дни). Первый запуск Bithumb — запоминает рынки.
#   v2.1 (04.11): БУМАГА «КОРЕЙСКАЯ ПЕРЕПЛАТА» — ничего не торгует. Раз в 5 мин: цена монеты на Upbit/Bithumb (KRW,
#   пересчёт по KRW-USDT той же биржи) против фьючерса Gate; переплата сверх обычной (медиана по всем монетам) ≥ XUP_PREM_MIN
#   (2%) → запись «шорт на Gate» и итог через 4 и 24 ч (комиссия 0,4% учтена; отдельно — со стопом 15%). На истории
#   14 мес.: +$4 на сделку без стопа, но при большей переплате хуже — поэтому сначала месяц бумаги. XUP_PREM=0 — выкл.
#   Страница /upbit/prem, итог — в /upbit/brief и раз в день в 9:00 (Иркутск) в Telegram.
import os, time, json, threading, logging, statistics
from datetime import datetime, timezone
from decimal import Decimal
from flask import jsonify, request
import mm_scanner as mm
import xpaper
import xexec_api as api

def env(k, d=""):
    return os.getenv(k, d).strip()

VERSION = "xupbit v2.1 · 04.11"
MODE = env("XUP_MODE", "live").lower()                 # live | paper | off
LIVE = MODE == "live"
USD = float(env("XUP_USD", "200"))                     # размер позиции, $ (маржа = USD / плечо)
LEV = int(float(env("XUP_LEV", "3")))                  # плечо не выше
SL = float(env("XUP_SL", "0.20"))                      # стоп: цена выше входа на 20%
HOLD_H = float(env("XUP_HOLD_H", "72"))                # держим 3 дня
MAX_POS = int(env("XUP_MAX_POS", "3"))
MAX_LOSS = float(env("XUP_MAX_LOSS", "80"))            # общий убыток модуля, $ → пауза
LATE_H = float(env("XUP_LATE_H", "6"))                 # торги на Upbit идут дольше — не входим (только бумага)
EVERY = float(env("XUP_EVERY_S", "30"))
LIQ_GUARD = 0.85
BASE = {"upbit": "https://api.upbit.com/v1", "bithumb": "https://api.bithumb.com/v1"}   # у Bithumb v1 — тот же формат
NAME = {"upbit": "Upbit", "bithumb": "Bithumb"}
SOURCES = [x.strip() for x in env("XUP_SOURCES", "upbit,bithumb").split(",") if x.strip() in BASE]
AGAIN_D = 5                                            # одну монету — не чаще раза в 5 дней (листинг на двух биржах)
PREM_ON = env("XUP_PREM", "1") == "1"                  # бумага «корейская переплата»
PREM_MIN = float(env("XUP_PREM_MIN", "0.02"))          # переплата сверх обычной, доля
PREM_EVERY = 300                                       # раз в 5 мин
PREM_VOL = 1e6                                         # оборот фьючерса Gate за сутки от $1 млн
PREM_COST = 0.4                                        # комиссия на круг, % (как в проверке на истории)
GTF = "https://api.gateio.ws/api/v4/futures/usdt"
TXT = "t-xup"
log = logging.getLogger("xupbit")
L = {}
LOCK = threading.RLock()

class Skip(Exception):
    pass

def lt(t=None, f="%d.%m %H:%M"):
    return time.strftime(f, time.gmtime((time.time() if t is None else t) + 8 * 3600))

def say(text):
    mm.tg(("🇰🇷 Корея-шорт: " if LIVE else "🧪🇰🇷 Корея-шорт [бумага]: ") + text)

# ---------- состояние ----------
def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'upbit_v1'", fetch=True)
    L.update(json.loads(r[0][0]) if r else {})
    for k, v in (("known", []), ("wait", {}), ("pos", {}), ("done", []), ("ev", []), ("paused", ""),
                 ("tot", {"n": 0, "sl": 0, "time": 0, "other": 0, "pnl": 0.0}), ("warned", {}), ("recent", {})):
        L.setdefault(k, v)
    if "known_by" not in L:                            # v1 → v2: известные рынки и ожидания были только Upbit
        L["known_by"] = {"upbit": L.get("known") or []}
        L["wait"] = {(k if "|" in k else "upbit|" + k): v for k, v in (L.get("wait") or {}).items()}
    L["loaded"] = 1

def save():
    if not L.get("loaded"):
        return
    try:
        xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('upbit_v1', %s)
                    ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(L),))
    except Exception:
        log.exception("xupbit save")

# ---------- Upbit (публично) ----------
def up_get(path, src="upbit", **p):
    r = api.S.get(BASE[src] + path, params=p, timeout=15)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()

def krw_markets(src):
    d = up_get("/market/all", src) or []
    return {x["market"] for x in d if str(x.get("market", "")).startswith("KRW-")}

def up_started(market, src="upbit"):
    """Когда на бирже была первая сделка: время (с) или None — торги ещё не начались."""
    t = up_get("/ticker", src, markets=market)
    if not t or not float(t[0].get("acc_trade_price_24h") or 0) > 0:
        return None
    c = up_get("/candles/minutes/60", src, market=market, count=200) or []
    if len(c) >= 200:                                  # 200 часовых свечей и больше — торгуется давно
        return int(time.time()) - 200 * 3600
    if not c:
        return int(time.time())
    first = min(x["candle_date_time_utc"] for x in c)  # самая старая часовая свеча — час начала торгов
    return int(datetime.strptime(first[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())

# ---------- Gate ----------
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

def gt_ticker(coin):
    r = api.S.get(f"{GTF}/tickers", params={"contract": f"{coin}_USDT"}, timeout=15)
    d = r.json() if r.status_code == 200 else None
    if not d or not isinstance(d, list) or not float(d[0].get("last") or 0) > 0:
        return None
    x = d[0]
    return {"last": float(x["last"]), "fair": float(x.get("mark_price") or 0) or float(x["last"]),
            "fund": float(x.get("funding_rate") or 0), "chg": float(x.get("change_percentage") or 0) / 100}

def gt_contract(coin):
    r = api.S.get(f"{GTF}/contracts/{coin}_USDT", timeout=15)
    d = r.json() if r.status_code == 200 else None
    if not isinstance(d, dict) or not d.get("quanto_multiplier"):
        raise Skip("на Gate нет фьючерса")
    if d.get("in_delisting"):
        raise Skip("фьючерс Gate снимается с торгов")
    return d

def gt_positions():
    out = {}
    for x in api.gt("GET", "/futures/usdt/positions") or []:
        if float(x.get("size") or 0) != 0:
            out[x.get("contract", "")[:-5]] = x
    return out

def stop_find(coin):
    return [x.get("id") for x in api.gt("GET", "/futures/usdt/price_orders",
                                        {"status": "open", "contract": f"{coin}_USDT"}) or []]

def stop_place(p):
    """Стоп на бирже: справедливая цена ≥ стопа → закрыть всю позицию по рынку. Вернёт True, если он виден."""
    try:
        api.gt("DELETE", "/futures/usdt/price_orders", {"contract": f"{p['coin']}_USDT"})
    except Exception:
        pass
    api.gt("POST", "/futures/usdt/price_orders", body={
        "initial": {"contract": f"{p['coin']}_USDT", "size": 0, "price": "0", "close": True, "tif": "ioc",
                    "reduce_only": True, "text": TXT},
        "trigger": {"strategy_type": 0, "price_type": 1, "price": ps(rnd(p["sl"], p["tick"])), "rule": 1,
                    "expiration": 5 * 86400},
        "order_type": "close-short-position"})
    time.sleep(1)
    return bool(stop_find(p["coin"]))

def closed_info(coin, t0):
    for x in api.gt("GET", "/futures/usdt/position_close", {"contract": f"{coin}_USDT", "limit": 10}) or []:
        if x.get("side") == "short" and float(x.get("time") or 0) >= t0 - 5:
            return (float(x.get("long_price") or 0) or None,
                    float(x["pnl"]) if x.get("pnl") is not None else None, str(x.get("text") or ""))
    return None

# ---------- события и вход ----------
def new_event(market, src, t_start, now):
    coin = market[4:]
    e = {"coin": coin, "src": src, "t_seen": int(now), "t_start": int(t_start), "late_h": round((now - t_start) / 3600, 1)}
    try:
        q = gt_ticker(coin)
    except Exception:
        q = None
    if q:
        e.update({"px": q["last"], "fund": q["fund"], "chg24": q["chg"], "hi": q["last"], "after": {}})
    L["ev"] = (L["ev"] + [e])[-200:]
    return e, q

def try_open(e, q, now):
    coin = e["coin"]
    if not q:
        raise Skip("на Gate нет фьючерса")
    if MODE == "off":
        raise Skip("модуль выключен (XUP_MODE=off)")
    if L.get("paused"):
        raise Skip(f"пауза: {L['paused']}")
    if e["late_h"] > LATE_H:
        raise Skip(f"торги на {NAME[e['src']]} начались {e['late_h']:.0f} ч назад — поздно, только бумага")
    if coin in L["pos"]:
        raise Skip("уже в шорте (листинг на другой бирже)")
    if now - (L.get("recent") or {}).get(coin, 0) < AGAIN_D * 86400:
        raise Skip(f"уже шортили меньше {AGAIN_D} дней назад (листинг на другой бирже)")
    if len(L["pos"]) >= MAX_POS:
        raise Skip(f"уже {len(L['pos'])} позиции — лимит {MAX_POS}")
    d = gt_contract(coin)
    cs, mmr = float(d["quanto_multiplier"]), max(0.0, float(d.get("maintenance_rate") or 0.005))
    px = q["last"]
    lev = max(1, min(LEV, int(float(d.get("leverage_max") or LEV)), int(1 / (SL + 0.03 + mmr + 0.002))))
    n = int(USD / (px * cs))
    minv = max(1, int(float(d.get("order_size_min") or 1)))
    if n < minv:
        raise Skip(f"1 контракт ≈${px * cs * minv:.0f} — больше позиции ${USD:.0f}")
    if d.get("order_size_max"):
        n = min(n, int(float(d["order_size_max"])))
    tick = d.get("order_price_round")
    p = {"coin": coin, "src": e["src"], "t": int(now), "t_start": e["t_start"], "px": px, "n": n, "cs": cs, "lev": lev, "tick": tick,
         "sl": rnd(px * (1 + SL), tick), "hi": px, "mode": "live" if LIVE else "paper", "fund0": q["fund"],
         "chg24": q["chg"], "seen": 0}
    if not LIVE:
        L["pos"][coin] = p
        L["recent"][coin] = int(now)
        say(open_text(p))
        return
    a = api.gt("GET", "/futures/usdt/accounts") or {}
    if a.get("in_dual_mode") or a.get("position_mode", "single") != "single":
        raise Skip("на Gate двусторонний режим позиций — нужен одиночный (One-way)")
    if coin in gt_positions():
        raise Skip("на Gate по монете уже есть позиция (автошорт?) — не смешиваю")
    need = n * cs * px / lev * 1.05
    if float(a.get("available") or 0) < need:
        raise Skip(f"на фьючерсах Gate свободно ${float(a.get('available') or 0):.0f}, нужно ~${need:.0f}")
    L["pos"][coin] = p                                 # запись до ордера: при сбое связи бот сам найдёт позицию
    L["recent"][coin] = int(now)
    save()
    try:
        api.gt("POST", f"/futures/usdt/positions/{coin}_USDT/leverage", {"leverage": str(lev)})
        api.gt("POST", "/futures/usdt/orders", body={"contract": f"{coin}_USDT", "size": -int(n), "price": "0",
                                                      "tif": "ioc", "text": TXT})
    except Exception as x:
        p["open_err"] = str(x)[:150]
    for _ in range(6):
        time.sleep(1.5)
        try:
            x = gt_positions().get(coin)
        except Exception:
            continue
        if x and float(x.get("size") or 0) < 0:
            adopt(p, x)
            return
    say(f"{coin}: {p.get('open_err') or 'ордер отправлен'} — позиции пока не вижу, проверяю ещё 2 мин")

def adopt(p, x):
    p["seen"] = 1
    p["px"] = float(x.get("entry_price") or p["px"])
    p["n"] = abs(int(float(x.get("size") or -p["n"])))
    p["sl"] = rnd(p["px"] * (1 + SL), p["tick"])
    p["liq"] = float(x.get("liq_price") or 0) or None
    if p["liq"] and p["liq"] < p["px"] * (1 + SL + 0.01):
        say(f"⚠️ {p['coin']}: ликвидация {p['liq']:.6g} ближе стопа — закрываю сразу")
        close_now(p, "закрыта сразу: ликвидация ближе стопа", time.time())
        return
    try:
        p["stop_ok"] = stop_place(p)
    except Exception as e:
        p["stop_ok"], p["stop_err"] = False, str(e)[:150]
    p["stop_t"] = time.time()
    say(open_text(p))
    save()

def open_text(p):
    end = p["t"] + HOLD_H * 3600
    return (f"шорт {p['coin']} на Gate: вход {p['px']:.6g}, ${p['n'] * p['cs'] * p['px']:.0f} ×{p['lev']} "
            f"(маржа ${p['n'] * p['cs'] * p['px'] / p['lev']:.0f}), стоп {p['sl']:.6g} (+{SL * 100:g}%)"
            + ("" if p["mode"] != "live" else " — на бирже ✅" if p.get("stop_ok") else
               f" — ⚠️ стоп на бирже не встал ({p.get('stop_err', 'нет в списке')}), веду сам")
            + f". Закрою {lt(end)} (через {HOLD_H:g} ч). Торги на {NAME.get(p.get('src', 'upbit'))} с {lt(p['t_start'])}; "
              f"на Gate за сутки {p['chg24'] * 100:+.0f}%, фандинг {p['fund0'] * 100:+.3f}%")

# ---------- бумага «корейская переплата» ----------
def krw_prices(src):
    """{МОНЕТА: цена в KRW} по всем рынкам KRW биржи (Upbit — одним запросом, Bithumb — пачками по 100)."""
    if src == "upbit":
        d = up_get("/ticker/all", src, quote_currencies="KRW") or []
    else:
        ms = sorted((L.get("known_by") or {}).get(src) or [])
        d = []
        for k in range(0, len(ms), 100):
            d += up_get("/ticker", src, markets=",".join(ms[k:k + 100])) or []
    return {x["market"][4:]: float(x["trade_price"]) for x in d
            if str(x.get("market", "")).startswith("KRW-") and float(x.get("trade_price") or 0) > 0}

def gate_all():
    """Все фьючерсы Gate: {МОНЕТА: (последняя цена, оборот за сутки $)} — один запрос."""
    out = {}
    for x in api.S.get(f"{GTF}/tickers", timeout=20).json() or []:
        c = x.get("contract", "")
        if c.endswith("_USDT") and float(x.get("last") or 0) > 0:
            out[c[:-5]] = (float(x["last"]), float(x.get("volume_24h_quote") or 0))
    return out

def prem_step(now):
    if not PREM_ON or now - L.get("prem_t", 0) < PREM_EVERY:
        return
    L["prem_t"] = now
    try:
        gt = gate_all()
    except Exception:
        return
    P, cool = L.setdefault("prem", []), L.setdefault("prem_cool", {})
    for src in SOURCES:
        try:
            kp = krw_prices(src)
        except Exception:
            continue
        usd = kp.get("USDT")
        if not usd:
            continue
        pr = {c: kp[c] / (g[0] * usd) - 1 for c, g in gt.items() if c in kp and c != "USDT" and g[1] >= PREM_VOL}
        if len(pr) < 20:
            continue
        med = statistics.median(pr.values())
        for c, v in pr.items():
            ex, key = v - med, f"{src}|{c}"
            if PREM_MIN <= ex < 0.5 and now - cool.get(key, 0) >= 24 * 3600:
                cool[key] = now
                P.append({"coin": c, "src": src, "t": int(now), "ex": round(ex * 100, 2), "prem": round(v * 100, 2),
                          "med": round(med * 100, 2), "px": gt[c][0], "hi": gt[c][0], "r": {}})
    for e in P:                                        # итоги: через 4 и 24 ч; максимум против шорта — для стопа 15%
        if "24ч" in e["r"] or e["coin"] not in gt:
            continue
        px, age = gt[e["coin"]][0], now - e["t"]
        e["hi"] = max(e["hi"], px)
        r = round((e["px"] / px - 1) * 100 - PREM_COST, 2)
        if age >= 4 * 3600 and "4ч" not in e["r"]:
            e["r"]["4ч"] = r
        if age >= 24 * 3600:
            e["r"]["24ч"] = r
            e["r"]["24ч_стоп15"] = round(-15 - PREM_COST, 2) if e["hi"] >= e["px"] * 1.15 else r
    L["prem"] = P[-600:]
    L["prem_cool"] = {k: t for k, t in cool.items() if now - t < 2 * 86400}
    d = lt(now, "%Y-%m-%d")
    if int(lt(now, "%H")) >= 9 and L.get("prem_day") != d:
        L["prem_day"] = d
        if any("24ч" in e["r"] for e in L["prem"]):
            say(prem_summary())

def prem_summary(days=None):
    P = [e for e in (L.get("prem") or []) if days is None or time.time() - e["t"] <= days * 86400]
    done = [e for e in P if "24ч" in e["r"]]
    head = f"Бумага «корейская переплата» (≥{PREM_MIN * 100:g}% сверх обычной, шорт на Gate на 24 ч): сигналов {len(P)}"
    if not done:
        return head + ", итогов за сутки пока нет"
    r = [e["r"]["24ч"] for e in done]
    rs = [e["r"]["24ч_стоп15"] for e in done]
    r4 = [e["r"]["4ч"] for e in P if "4ч" in e["r"]]
    m = statistics.mean
    return (head + f", закрыто {len(done)} | без стопа {m(r):+.2f}% = {m(r) * 2:+.2f}$ на сделку $200, "
            f"в плюсе {sum(1 for x in r if x > 0) * 100 // len(r)}% | со стопом 15%: {m(rs) * 2:+.2f}$"
            + (f" | через 4 ч: {m(r4) * 2:+.2f}$" if r4 else "") + " (комиссия 0,4% учтена)")

# ---------- ведение ----------
def finish(p, outcome, ex, pnl, now, note=""):
    L["pos"].pop(p["coin"], None)
    nt = p["n"] * p["cs"] * p["px"]
    if pnl is None:
        pnl = nt * (p["px"] - ex) / p["px"] - nt * 0.001
    rec = {"coin": p["coin"], "mode": p["mode"], "t": p["t"], "t_close": int(now), "px": p["px"], "exit": ex,
           "pnl": round(pnl, 2), "outcome": outcome, "worst_pct": round((p["hi"] / p["px"] - 1) * 100, 1), "note": note}
    L["done"] = (L["done"] + [rec])[-200:]
    if p["mode"] == ("live" if LIVE else "paper"):
        t = L["tot"]
        t["n"] += 1
        k = "sl" if outcome.startswith("стоп") or outcome == "ликвидация" else "time" if outcome == "время" else "other"
        t[k] = t.get(k, 0) + 1
        t["pnl"] = round(t["pnl"] + pnl, 2)
        if t["pnl"] <= -MAX_LOSS and not L.get("paused"):
            L["paused"] = f"общий убыток {t['pnl']:+.2f}$ — больше лимита ${MAX_LOSS:.0f}"
            say(f"⛔ {L['paused']}. Новых шортов нет. Включить снова: /upbit/resume")
    t = L["tot"]
    say(f"{'✅' if pnl > 0 else '❌'} {p['coin']}: {outcome} через {(now - p['t']) / 3600:.1f} ч, выход {ex:.6g}, "
        f"итог {pnl:+.2f}$ (хуже всего по пути +{rec['worst_pct']:.1f}%){note}\n"
        f"Всего: сделок {t['n']}, {t['pnl']:+.2f}$")
    save()

def close_now(p, outcome, now, note=""):
    coin = p["coin"]
    if p["mode"] != "live":
        try:
            q = gt_ticker(coin)
        except Exception:
            q = None
        finish(p, outcome, q["last"] if q else p["px"], None, now, note)
        return True
    try:
        api.gt("POST", "/futures/usdt/orders", body={"contract": f"{coin}_USDT", "size": 0, "price": "0",
                                                      "close": True, "tif": "ioc", "text": TXT})
    except Exception as x:
        if now - L["warned"].get("cl" + coin, 0) > 600:
            L["warned"]["cl" + coin] = now
            say(f"❗ {coin}: не смог закрыть шорт ({str(x)[:120]}) — пробую снова. Не выйдет — закрой вручную на Gate")
        return False
    time.sleep(2)
    try:
        api.gt("DELETE", "/futures/usdt/price_orders", {"contract": f"{coin}_USDT"})
    except Exception:
        pass
    info = None
    for _ in range(3):
        try:
            info = closed_info(coin, p["t"])
        except Exception:
            info = None
        if info:
            break
        time.sleep(2)
    q = None
    if not info:
        try:
            q = gt_ticker(coin)
        except Exception:
            q = None
    ex = (info[0] if info else None) or (q["last"] if q else p["px"])
    finish(p, outcome, ex, info[1] if info else None, now, note)
    return True

def manage(p, q, x, now):
    """Одна позиция: x — позиция на Gate (или None), q — цена."""
    coin = p["coin"]
    if q:
        p["hi"] = max(p["hi"], q["last"], q["fair"])
    if p["mode"] != "live":
        if q and q["fair"] >= p["sl"]:
            finish(p, "стоп", max(p["sl"], q["last"]), None, now)
        elif now - p["t"] >= HOLD_H * 3600:
            close_now(p, "время", now)
        return
    short = x is not None and float(x.get("size") or 0) < 0
    if not p.get("seen"):
        if short:
            adopt(p, x)
        elif now - p["t"] > 150:
            L["pos"].pop(coin, None)
            say(f"{coin}: шорт так и не появился на Gate — запись снята. {p.get('open_err', '')}")
            save()
        return
    if not short:                                      # закрыта на бирже: стоп, вручную или ликвидация
        p.setdefault("gone_t", now)
        try:
            info = closed_info(coin, p["t"])
        except Exception:
            info = None
        if not info and now - p["gone_t"] < 90:
            return
        try:
            api.gt("DELETE", "/futures/usdt/price_orders", {"contract": f"{coin}_USDT"})
        except Exception:
            pass
        ex = (info[0] if info else None) or (q["last"] if q else p["px"])
        pnl = info[1] if info else None
        nt = p["n"] * p["cs"] * p["px"]
        out = ("ликвидация" if (info and "liq" in info[2].lower()) or (pnl is not None and pnl <= -0.8 * nt / p["lev"])
               else "стоп" if ex >= p["sl"] * 0.99 else "закрыта на бирже (вручную?)")
        finish(p, out, ex, pnl, now)
        return
    p.pop("gone_t", None)
    if float(x.get("liq_price") or 0) > 0:
        p["liq"] = float(x["liq_price"])
    if now - p["t"] >= HOLD_H * 3600:
        close_now(p, "время", now)
        return
    if not q:
        return
    lq = p.get("liq")
    if lq and lq > p["px"] and max(q["fair"], q["last"]) >= p["px"] + (lq - p["px"]) * LIQ_GUARD:
        close_now(p, "стоп (до ликвидации)", now, note=f" — цена прошла {LIQ_GUARD * 100:.0f}% пути до ликвидации")
        return
    p["bk"] = p.get("bk", 0) + 1 if q["fair"] >= p["sl"] * 1.01 else 0
    if p["bk"] >= 2:                                   # стоп на бирже не сработал, а цена выше — закрываю сам
        close_now(p, "стоп (закрыл сам)", now, note=" — биржевой стоп не сработал")
        return
    if now - p.get("stop_t", 0) > 600:                 # раз в 10 мин: стоп на бирже на месте?
        p["stop_t"] = now
        try:
            if not stop_find(coin):
                p["stop_ok"] = stop_place(p)
                if not p["stop_ok"] and now - L["warned"].get("st" + coin, 0) > 3600:
                    L["warned"]["st" + coin] = now
                    say(f"⚠️ {coin}: стоп на Gate не встаёт — веду сам (закрою при +{SL * 100:g}%)")
        except Exception:
            log.exception("xupbit stop check")

def track(now):
    """Бумага по каждому событию: цена Gate через 1, 3 и 7 дней и максимум против шорта."""
    for e in L["ev"]:
        if "px" not in e or len(e.get("after", {})) >= 3 or now - e["t_seen"] > 8 * 86400:
            continue
        due = [k for k, h in (("1д", 24), ("3д", 72), ("7д", 168)) if now - e["t_seen"] >= h * 3600 and k not in e["after"]]
        if not due and now - e.get("hi_t", 0) < 600:
            continue
        try:
            q = gt_ticker(e["coin"])
        except Exception:
            q = None
        if not q:
            continue
        e["hi_t"] = now
        if now - e["t_seen"] <= 72 * 3600:
            e["hi"] = max(e.get("hi", e["px"]), q["last"])
        for k in due:
            e["after"][k] = round((e["px"] / q["last"] - 1) * 100, 1)     # итог шорта, % (без расходов)

def step(now):
    for src in SOURCES:
        try:
            cur = krw_markets(src)
        except Exception as x:
            cur = None
            if now - L["warned"].get("up" + src, 0) > 3 * 3600:
                L["warned"]["up" + src] = now
                say(f"⚠️ не вижу {NAME[src]} ({str(x)[:100]}) — новые листинги там пока не ловлю")
        if not cur:
            continue
        kb = L["known_by"].setdefault(src, [])
        if not kb:
            L["known_by"][src] = sorted(cur)
            say(f"{NAME[src]}: запущен — {'ЖИВОЙ' if LIVE else MODE}: знаю {len(cur)} рынков KRW, жду новые. "
                f"${USD:.0f} ×{LEV}, стоп +{SL * 100:g}%, держу {HOLD_H:g} ч, позиций ≤{MAX_POS}")
            save()
            continue
        known = set(kb)
        for m in sorted(cur - known):
            L["wait"][f"{src}|{m}"] = int(now)
            say(f"{NAME[src]} добавил {m} — жду начала торгов")
        if cur - known:
            L["known_by"][src] = sorted(known | cur)
            save()
    for key, t0 in list(L["wait"].items()):
        src, m = key.split("|", 1)
        if now - t0 > 3 * 86400 or src not in BASE:
            L["wait"].pop(key, None)
            continue
        try:
            ts = up_started(m, src)
        except Exception:
            continue
        if ts is None:
            continue
        L["wait"].pop(key, None)
        e, q = new_event(m, src, ts, now)
        try:
            try_open(e, q, now)
            pp = L["pos"].get(e["coin"]) or {}
            e["action"] = "шорт" if pp.get("seen") or pp.get("mode") == "paper" else "ордер отправлен, позиции не видно"
        except Skip as x:
            e["action"] = f"пропуск: {x}"
            say(f"{NAME[src]} {m}: торги начались, но не захожу — {x}" + (f" (на Gate {q['last']:.6g}, пишу на бумагу)" if q else ""))
        except Exception as x:
            log.exception("xupbit open %s", m)
            e["action"] = f"ошибка: {str(x)[:150]}"
            say(f"❗ {NAME[src]} {m}: ошибка при входе — {str(x)[:150]}")
        save()
    if L["pos"]:
        sh = None
        if any(p["mode"] == "live" for p in L["pos"].values()):
            try:
                sh = gt_positions()
            except Exception:
                sh = None
        for coin, p in list(L["pos"].items()):
            try:
                q = gt_ticker(coin)
            except Exception:
                q = None
            if p["mode"] == "live" and sh is None:
                continue
            manage(p, q, (sh or {}).get(coin), now)
    track(now)
    try:
        prem_step(now)
    except Exception:
        log.exception("xupbit prem")
    for c, t in list((L.get("recent") or {}).items()):
        if now - t > 10 * 86400:
            L["recent"].pop(c, None)

def loop():
    time.sleep(120)
    while not L.get("loaded"):
        try:
            load()
        except Exception:
            log.exception("xupbit load")
            time.sleep(60)
    n = 0
    while True:
        try:
            with LOCK:
                step(time.time())
        except Exception:
            log.exception("xupbit")
        n += 1
        if n % 10 == 0:
            with LOCK:
                save()
        time.sleep(EVERY)

# ---------- страницы ----------
def ok():
    return mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN

def brief():
    t = L.get("tot") or {}
    kb = L.get("known_by") or {}
    out = [f"🇰🇷 {VERSION} — {'ЖИВОЙ (Gate)' if LIVE else MODE}, {lt(None)}",
           f"${USD:.0f} ×{LEV} · стоп +{SL * 100:g}% · держу {HOLD_H:g} ч · позиций ≤{MAX_POS} · знаю рынков: "
           + ", ".join(f"{NAME[s_]} {len(kb.get(s_) or [])}" for s_ in SOURCES) + (f" · ⛔ {L['paused']}" if L.get("paused") else ""),
           f"Всего: сделок {t.get('n', 0)}, по времени {t.get('time', 0)}, стопов {t.get('sl', 0)}, {t.get('pnl', 0):+.2f}$"]
    for p in (L.get("pos") or {}).values():
        out.append(f"Открыт: {p['coin']} с {lt(p['t'])} по {p['px']:.6g}, стоп {p['sl']:.6g}, закрою "
                   f"{lt(p['t'] + HOLD_H * 3600)}" + (" · стоп на бирже ✅" if p.get("stop_ok") else ""))
    for k in (L.get("wait") or {}):
        out.append(f"Жду начала торгов: {k.replace('|', ' ')}")
    for e in (L.get("ev") or [])[-8:]:
        out.append(f"{e['coin']} ({NAME.get(e.get('src', 'upbit'))}) {lt(e['t_start'])}: {e.get('action', '—')}" +
                   (f" | шорт по бумаге: {', '.join(f'{k} {v:+.1f}%' for k, v in e['after'].items())}" if e.get("after") else ""))
    for d in (L.get("done") or [])[-5:]:
        out.append(f"Закрыт {d['coin']}: {d['outcome']}, {d['pnl']:+.2f}$")
    if PREM_ON:
        out.append(prem_summary())
    return "\n".join(out)

@mm.app.route("/upbit")
def upbit_page():
    if not ok():
        return "forbidden", 403
    with LOCK:
        return jsonify({"версия": VERSION, "режим": MODE, "пауза": L.get("paused"),
                        "настройки": {"позиция_$": USD, "плечо": LEV, "стоп_%": SL * 100, "держим_ч": HOLD_H,
                                      "позиций": MAX_POS, "общий_убыток_$": MAX_LOSS, "поздно_ч": LATE_H,
                                      "биржи": SOURCES},
                        "всего": L.get("tot"), "открыто": list((L.get("pos") or {}).values()),
                        "жду_торгов": L.get("wait"), "события": (L.get("ev") or [])[-50:],
                        "сделки": (L.get("done") or [])[-50:],
                        "рынков_known": {s_: len(v) for s_, v in (L.get("known_by") or {}).items()}})

@mm.app.route("/upbit/prem")
def upbit_prem():
    """Бумага «корейская переплата»: последние сигналы и итоги. &days=7 — итог за 7 дней."""
    if not ok():
        return "forbidden", 403
    with LOCK:
        try:
            days = float(request.args.get("days")) if request.args.get("days") else None
        except ValueError:
            days = None
        return jsonify({"версия": VERSION, "итог": prem_summary(days), "порог_%": PREM_MIN * 100,
                        "сигналы": [dict(e, когда=lt(e["t"])) for e in (L.get("prem") or [])[-200:]]})

@mm.app.route("/upbit/brief")
def upbit_brief():
    if not ok():
        return "forbidden", 403
    with LOCK:
        txt = brief()
    if request.args.get("tg") == "1":
        mm.tg(txt)
    return txt, 200, {"Content-Type": "text/plain; charset=utf-8"}

@mm.app.route("/upbit/pause")
def upbit_pause():
    if not ok():
        return "forbidden", 403
    with LOCK:
        L["paused"] = "вручную"
        save()
    return "пауза: новых шортов нет, открытые бот ведёт дальше. Снять: /upbit/resume"

@mm.app.route("/upbit/resume")
def upbit_resume():
    if not ok():
        return "forbidden", 403
    with LOCK:
        L["paused"] = ""
        if request.args.get("reset") == "1":           # обнулить счёт убытка (после разбора)
            L["tot"] = {"n": 0, "sl": 0, "time": 0, "other": 0, "pnl": 0.0}
        save()
    return "работает" + (" — счёт обнулён" if request.args.get("reset") == "1" else "")

@mm.app.route("/upbit/close")
def upbit_close():
    if not ok():
        return "forbidden", 403
    coin = request.args.get("coin", "").upper()
    with LOCK:
        p = (L.get("pos") or {}).get(coin)
        if not p:
            return f"по {coin} открытого Upbit-шорта нет"
        done = close_now(p, "закрыта вручную", time.time())
    return f"{coin}: " + ("закрыт — итог в Telegram" if done else "закрыть не вышло — причина в Telegram")

threading.Thread(target=loop, daemon=True).start()   # и при off: новых нет, но открытые ведём до конца
