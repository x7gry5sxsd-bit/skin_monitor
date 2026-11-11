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
#   v2.2 (04.11): КОРЕЙСКИЙ ПАМП — второй сигнал, торгует. Проверено на истории 14 мес. на ДВУХ биржах отдельно:
#   Upbit 289 сделок, +$7.8 на сделку $200, 12 из 13 мес. в плюсе; Bithumb 397 сделок, +$7.9, 13 из 14 (комиссия 0,4%,
#   стоп 20%, фандинг с мая учтён). Правило (по закрытому часу): на Upbit/Bithumb оборот часа ≥ XUP_PUMP_MULT (5) × средний
#   за 7 дней и ≥ $300 тыс., цена там за час ≥ +XUP_PUMP_UP (3%) и на XUP_PUMP_GAP (2%) больше, чем на Gate за тот же час
#   (гонят корейцы, а не весь мир); монета торгуется там и на Gate ≥ 14 дней; оборот фьючерса Gate ≥ $1 млн; цена там и
#   на Gate — одна монета (отношение 0,8–1,25). Вход — шорт на Gate через час после закрытия часа-сигнала (как в проверке),
#   держим 72 ч, стоп +20% на бирже (ведение — то же, что у листингов). Одна монета — раз в сутки.
#   Размер XUP_PUMP_USD ($100), позиций XUP_PUMP_POS (5, отдельно от листингов), общий убыток XUP_PUMP_LOSS ($60) → пауза
#   только памп (/upbit/resume?kind=pump). XUP_PUMP_MODE: как XUP_MODE (live/paper/off). XUP_PUMP=0 — выключить.
#   Бумага по КАЖДОМУ сигналу (и пропущенному): итог шорта через 24 и 72 ч (со стопом 20%) — /upbit/pump.
#   БУМАГА «ПРЕДУПРЕЖДЕНИЯ БИРЖ» (XUP_FLAGS=1): раз в 10 мин метки Upbit (предупреждение, скачки цены, взлёт оборота,
#   наплыв депозитов, разница с мировой ценой, много мелких счетов) и Bithumb (предупреждение). Новая метка → запись цены
#   Gate и итог шорта через 1, 24 и 72 ч. Ничего не торгует — копит историю, которой в открытых данных нет.
#   Итог всех бумаг — в /upbit/brief и раз в день в 9:00 (Иркутск) в Telegram.
#   v2.3 (09.11): ПАМП ВИДИТ «СПЯЩИЕ» МОНЕТЫ. Раньше список монет для слежения обновлялся раз в 6 ч и брал только
#   фьючерсы Gate с оборотом ≥ $1 млн — а памп случается как раз на тихих монетах (CYBER 06.11: за сутки до пампа
#   $0,04 млн на Gate) → сигнал пропускался. Теперь: каждый час слежу за ВСЕМИ монетами Upbit/Bithumb, у которых есть
#   фьючерс Gate; оборот ≥ $1 млн проверяется в момент сигнала (как было). Пропущенные свечи добираются сами.
#   Каждый кандидат и причина отсева — в /upbit/pump («отсев»), там же — сколько монет слежу и ошибок за час.
#   СВЯЗЬ С БИРЖАМИ: тревога «не вижу Upbit/Bithumb» — только после XUP_FAIL_N (5) неудачных опросов подряд (~2,5 мин),
#   а не от одного сбоя; когда связь вернулась — «✅ снова на связи». Последний удачный опрос — в /upbit/brief.
#   v2.4 (11.11): «КОРЕЙСКАЯ ПЕРЕПЛАТА» МОЖЕТ ТОРГОВАТЬ. XUP_PREM_MODE=live (по умолчанию paper — как раньше, только
#   бумага): на каждый сигнал переплаты — шорт на Gate $XUP_PREM_USD (60 = маржа $20 ×3), стоп +XUP_PREM_SL (15%) на
#   бирже, держу XUP_PREM_HOLD_H (24 ч), позиций ≤ XUP_PREM_POS (5), общий убыток XUP_PREM_LOSS ($30) → пауза только
#   переплаты (/upbit/resume?kind=prem). XUP_PREM_TP — тейк, доля (0 = без тейка; на бумаге 46 сделок: без тейка
#   +$4,3 на $200, с тейком 2% +$0,3 — тейк срезает редкие сильные падения, в которых вся прибыль). Тейк ведёт бот
#   (опрос раз в 30 с), стоп стоит на бирже. Бумага по всем сигналам пишется как раньше.
#   v2.5 (11.11): «МЕТКИ UPBIT» МОГУТ ТОРГОВАТЬ. XUP_FLAG_MODE=live (по умолчанию paper): новая метка Upbit из списка
#   XUP_FLAG_SET (взлёт оборота, наплыв депозитов, разница с мировой ценой, скачки цены, много мелких счетов) у монеты
#   с фьючерсом Gate (оборот ≥ XUP_FLAG_VOL, $200 тыс.) → шорт $XUP_FLAG_USD (60 = маржа $20 ×3), стоп +XUP_FLAG_SL
#   (20%) на бирже, держу XUP_FLAG_HOLD_H (72 ч), позиций ≤ XUP_FLAG_POS (5), общий убыток XUP_FLAG_LOSS ($30) →
#   пауза только меток (/upbit/resume?kind=flag). Бумага 05–08.11: 38 шортов на 72 ч, +8,4% на сделку против −1,0% у
#   случайных монет в те же часы, в плюсе 76% — но это 4 дня, поэтому размер малый. Метка «предупреждение» не торгуется.
import os, time, json, threading, logging, statistics
from datetime import datetime, timezone
from decimal import Decimal
from flask import jsonify, request
import mm_scanner as mm
import xpaper
import xexec_api as api

def env(k, d=""):
    return os.getenv(k, d).strip()

VERSION = "xupbit v2.5 · 11.11"
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
FAIL_N = int(env("XUP_FAIL_N", "5"))                    # тревога «не вижу биржу» — после стольких неудачных опросов подряд
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
PUMP_ON = env("XUP_PUMP", "1") == "1"                  # корейский памп
PUMP_MODE = env("XUP_PUMP_MODE", MODE).lower()         # live | paper | off (по умолчанию как XUP_MODE)
PUMP_LIVE = PUMP_MODE == "live"
PUMP_USD = float(env("XUP_PUMP_USD", "100"))
PUMP_POS = int(env("XUP_PUMP_POS", "5"))
PUMP_LOSS = float(env("XUP_PUMP_LOSS", "60"))
PUMP_MULT = float(env("XUP_PUMP_MULT", "5"))           # оборот часа ≥ 5× среднего часа за 7 дней
PUMP_UP = float(env("XUP_PUMP_UP", "0.03"))            # цена на корейской бирже за час ≥ +3%
PUMP_GAP = float(env("XUP_PUMP_GAP", "0.02"))          # и на 2% больше хода Gate за тот же час
PUMP_MINV = 3e5                                        # оборот часа там ≥ $300 тыс.
PUMP_GVOL = 1e6                                        # фьючерс Gate: оборот за сутки ≥ $1 млн
PUMP_AGE_D = 14                                        # и там, и на Gate торгуется не меньше 14 дней
PUMP_COOL_H = 24                                       # одна монета — раз в сутки
PUMP_DELAY_S = 7200                                    # вход: конец следующего часа после часа-сигнала (как в проверке)
PUMP_COST = 0.4                                        # комиссия на круг для бумаги, %
PREM_MODE = env("XUP_PREM_MODE", "paper").lower()      # (v2.4) live | paper — торговать ли переплату
PREM_LIVE = PREM_MODE == "live"
PREM_USD = float(env("XUP_PREM_USD", "60"))            # размер позиции, $ (маржа $20 при ×3)
PREM_POS = int(env("XUP_PREM_POS", "5"))
PREM_LOSS = float(env("XUP_PREM_LOSS", "30"))
PREM_SL = float(env("XUP_PREM_SL", "0.15"))
PREM_HOLD_H = float(env("XUP_PREM_HOLD_H", "24"))
PREM_TP = float(env("XUP_PREM_TP", "0"))               # тейк, доля; 0 — без тейка (как в бумаге)
FLAG_MODE = env("XUP_FLAG_MODE", "paper").lower()      # (v2.5) live | paper — торговать ли метки Upbit
FLAG_LIVE = FLAG_MODE == "live"
FLAG_USD = float(env("XUP_FLAG_USD", "60"))
FLAG_POS = int(env("XUP_FLAG_POS", "5"))
FLAG_LOSS = float(env("XUP_FLAG_LOSS", "30"))
FLAG_SL = float(env("XUP_FLAG_SL", "0.20"))
FLAG_HOLD_H = float(env("XUP_FLAG_HOLD_H", "72"))
FLAG_VOL = float(env("XUP_FLAG_VOL", "200000"))        # оборот фьючерса Gate за сутки, $
FLAG_SET = {x.strip() for x in env("XUP_FLAG_SET", "взлёт оборота,наплыв депозитов,разница с мировой ценой,скачки цены,"
                                                   "много мелких счетов").split(",") if x.strip()}
KINDS = {
    "listing": dict(ru="листинг", by="по листингам", gen="листингов", live=LIVE, mode=MODE, usd=USD, pos=MAX_POS,
                    loss=MAX_LOSS, sl=SL, hold=HOLD_H, tp=0, tot="tot", paused="paused", q=""),
    "pump": dict(ru="памп", by="по пампу", gen="пампа", live=PUMP_LIVE, mode=PUMP_MODE, usd=PUMP_USD, pos=PUMP_POS,
                 loss=PUMP_LOSS, sl=SL, hold=HOLD_H, tp=0, tot="tot_pump", paused="paused_pump", q="?kind=pump"),
    "prem": dict(ru="переплата", by="по переплате", gen="переплаты", live=PREM_LIVE, mode=PREM_MODE, usd=PREM_USD,
                 pos=PREM_POS, loss=PREM_LOSS, sl=PREM_SL, hold=PREM_HOLD_H, tp=PREM_TP, tot="tot_prem",
                 paused="paused_prem", q="?kind=prem"),
    "flag": dict(ru="метка", by="по меткам", gen="меток", live=FLAG_LIVE, mode=FLAG_MODE, usd=FLAG_USD, pos=FLAG_POS,
                 loss=FLAG_LOSS, sl=FLAG_SL, hold=FLAG_HOLD_H, tp=0, tot="tot_flag", paused="paused_flag",
                 q="?kind=flag")}
FLAGS_ON = env("XUP_FLAGS", "1") == "1"                # бумага «предупреждения бирж»
FLAGS_EVERY = 600
CAUTION_RU = {"PRICE_FLUCTUATIONS": "скачки цены", "TRADING_VOLUME_SOARING": "взлёт оборота",
              "DEPOSIT_AMOUNT_SOARING": "наплыв депозитов", "GLOBAL_PRICE_DIFFERENCES": "разница с мировой ценой",
              "CONCENTRATION_OF_SMALL_ACCOUNTS": "много мелких счетов"}
GTF = "https://api.gateio.ws/api/v4/futures/usdt"
TXT = "t-xup"
log = logging.getLogger("xupbit")
L = {}
LOCK = threading.RLock()

class Skip(Exception):
    pass

def lt(t=None, f="%d.%m %H:%M"):
    return time.strftime(f, time.gmtime((time.time() if t is None else t) + 8 * 3600))

def say(text, live=None):
    live = LIVE if live is None else live
    mm.tg(("🇰🇷 Корея-шорт: " if live else "🧪🇰🇷 Корея-шорт [бумага]: ") + text)

# ---------- состояние ----------
def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'upbit_v1'", fetch=True)
    L.update(json.loads(r[0][0]) if r else {})
    for k, v in (("known", []), ("wait", {}), ("pos", {}), ("done", []), ("ev", []), ("paused", ""),
                 ("tot", {"n": 0, "sl": 0, "time": 0, "other": 0, "pnl": 0.0}), ("warned", {}), ("recent", {}),
                 ("tot_pump", {"n": 0, "sl": 0, "time": 0, "other": 0, "pnl": 0.0}), ("paused_pump", ""),
                 ("pump_sig", []), ("pump_cool", {}), ("flags", {}), ("fev", []), ("pump_skip", []),
                 ("upfail", {}), ("up_ok", {}), ("paused_prem", ""), ("paused_flag", ""),
                 ("tot_flag", {"n": 0, "sl": 0, "time": 0, "other": 0, "pnl": 0.0}),
                 ("tot_prem", {"n": 0, "sl": 0, "time": 0, "other": 0, "pnl": 0.0})):
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

def kind_of(p):
    return p.get("kind", "listing")

def try_open(e, q, now, kind="listing"):
    coin, K = e["coin"], KINDS[kind]
    live, usd, mode, slp = K["live"], K["usd"], K["mode"], K["sl"]
    if not q:
        raise Skip("на Gate нет фьючерса")
    if mode == "off":
        raise Skip("модуль выключен (XUP_MODE=off)" if kind == "listing" else f"{K['ru']}: выключено")
    paused = L.get(K["paused"])
    if paused:
        raise Skip(f"пауза: {paused}")
    if kind == "listing" and e["late_h"] > LATE_H:
        raise Skip(f"торги на {NAME[e['src']]} начались {e['late_h']:.0f} ч назад — поздно, только бумага")
    if coin in L["pos"]:
        raise Skip("по монете уже открыт шорт этого модуля")
    if kind == "listing" and now - (L.get("recent") or {}).get(coin, 0) < AGAIN_D * 86400:
        raise Skip(f"уже шортили меньше {AGAIN_D} дней назад (листинг на другой бирже)")
    lim = K["pos"]
    mine = sum(1 for x in L["pos"].values() if kind_of(x) == kind)
    if mine >= lim:
        raise Skip(f"уже {mine} позиции ({K['ru']}) — лимит {lim}")
    d = gt_contract(coin)
    cs, mmr = float(d["quanto_multiplier"]), max(0.0, float(d.get("maintenance_rate") or 0.005))
    px = q["last"]
    lev = max(1, min(LEV, int(float(d.get("leverage_max") or LEV)), int(1 / (slp + 0.03 + mmr + 0.002))))
    n = int(usd / (px * cs))
    minv = max(1, int(float(d.get("order_size_min") or 1)))
    if n < minv:
        raise Skip(f"1 контракт ≈${px * cs * minv:.0f} — больше позиции ${usd:.0f}")
    if d.get("order_size_max"):
        n = min(n, int(float(d["order_size_max"])))
    tick = d.get("order_price_round")
    p = {"coin": coin, "src": e["src"], "t": int(now), "t_start": e["t_start"], "px": px, "n": n, "cs": cs, "lev": lev, "tick": tick,
         "sl": rnd(px * (1 + slp), tick), "hi": px, "mode": "live" if live else "paper", "fund0": q["fund"],
         "chg24": q["chg"], "seen": 0, "kind": kind, "slp": slp, "hold_h": K["hold"]}
    if K["tp"] > 0:
        p["tpp"], p["tp"] = K["tp"], rnd(px * (1 - K["tp"]), tick)
    if e.get("why"):
        p["why"] = e.get("why", "")
    if not live:
        L["pos"][coin] = p
        if kind in ("listing", "pump"):
            L["recent"][coin] = int(now)
        say(open_text(p), live=False)
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
    if kind in ("listing", "pump"):
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
    slp = p.get("slp", SL)
    p["sl"] = rnd(p["px"] * (1 + slp), p["tick"])
    if p.get("tpp"):
        p["tp"] = rnd(p["px"] * (1 - p["tpp"]), p["tick"])
    p["liq"] = float(x.get("liq_price") or 0) or None
    if p["liq"] and p["liq"] < p["px"] * (1 + slp + 0.01):
        say(f"⚠️ {p['coin']}: ликвидация {p['liq']:.6g} ближе стопа — закрываю сразу")
        close_now(p, "закрыта сразу: ликвидация ближе стопа", time.time())
        return
    try:
        p["stop_ok"] = stop_place(p)
    except Exception as e:
        p["stop_ok"], p["stop_err"] = False, str(e)[:150]
    p["stop_t"] = time.time()
    say(open_text(p), live=p["mode"] == "live")
    save()

def open_text(p):
    hold, slp = p.get("hold_h", HOLD_H), p.get("slp", SL)
    end = p["t"] + hold * 3600
    why = (f"Корейский памп: {p.get('why', '')}" if kind_of(p) == "pump" else
           f"Корейская переплата ({NAME.get(p.get('src', 'upbit'))}): {p.get('why', '')}" if kind_of(p) == "prem" else
           f"Метка {NAME.get(p.get('src', 'upbit'))}: {p.get('why', '')}" if kind_of(p) == "flag" else
           f"Торги на {NAME.get(p.get('src', 'upbit'))} с {lt(p['t_start'])}")
    return (f"шорт {p['coin']} на Gate: вход {p['px']:.6g}, ${p['n'] * p['cs'] * p['px']:.0f} ×{p['lev']} "
            f"(маржа ${p['n'] * p['cs'] * p['px'] / p['lev']:.0f}), стоп {p['sl']:.6g} (+{slp * 100:g}%)"
            + (f", тейк {p['tp']:.6g} (−{p['tpp'] * 100:g}%, ведёт бот)" if p.get("tp") else "")
            + ("" if p["mode"] != "live" else " — на бирже ✅" if p.get("stop_ok") else
               f" — ⚠️ стоп на бирже не встал ({p.get('stop_err', 'нет в списке')}), веду сам")
            + f". Закрою {lt(end)} (через {hold:g} ч). {why}; "
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
                if PREM_LIVE:                          # (v2.4) живой шорт по сигналу
                    try:
                        try_open({"coin": c, "src": src, "t_start": int(now), "late_h": 0,
                                  "why": f"на {ex * 100:.1f}% дороже обычного"}, gt_ticker(c), now, kind="prem")
                        P[-1]["live"] = "шорт"
                    except Skip as x:
                        P[-1]["live"] = f"пропуск: {x}"
                    except Exception as x:
                        log.exception("prem open %s", c)
                        P[-1]["live"] = f"ошибка: {str(x)[:100]}"
                    save()
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

def daily(now):
    """Раз в день в 9:00 (Иркутск): итоги бумаг — переплата, памп (все сигналы), предупреждения бирж."""
    d = lt(now, "%Y-%m-%d")
    if int(lt(now, "%H")) < 9 or L.get("prem_day") == d:
        return
    L["prem_day"] = d
    parts = []
    if PREM_ON and any("24ч" in e["r"] for e in L.get("prem") or []):
        parts.append(prem_summary())
    if PUMP_ON and any("72ч" in e.get("r", {}) for e in L.get("pump_sig") or []):
        parts.append(pump_summary())
    if FLAGS_ON and any("24ч" in e.get("r", {}) for e in L.get("fev") or []):
        parts.append(flags_summary())
    if parts:
        say("Итоги бумаги за всё время:\n" + "\n".join(parts))

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

# ---------- корейский памп ----------
HIST = {"upbit": {}, "bithumb": {}}                    # {биржа: {монета: {час: (закрытие KRW, оборот KRW)}}} — в памяти
TRACK = {"t": 0, "coins": {}}                          # какие монеты слежу (все, у кого есть фьючерс Gate; v2.3)

def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def candles(src, coin, count, to=None):
    """Часовые свечи биржи: {начало часа (с, UTC): (закрытие, оборот KRW)}. to — время UTC (с)."""
    p = {"market": f"KRW-{coin}", "count": count}
    if to:                                             # у Bithumb «to» — время Кореи без зоны
        p["to"] = iso(to) if src == "upbit" else time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(to + 9 * 3600))
    time.sleep(0.12 if src == "upbit" else 0.08)
    d = up_get("/candles/minutes/60", src, **p) or []
    out = {}
    for x in d if isinstance(d, list) else []:
        t = int(datetime.strptime(x["candle_date_time_utc"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
        out[t] = (float(x["trade_price"]), float(x.get("candle_acc_trade_price") or 0))
    return out

def gate_hour(coin, h):
    """Закрытие часа h и часа до него на фьючерсе Gate (свечи 1 ч)."""
    r = api.S.get(f"{GTF}/candlesticks", params={"contract": f"{coin}_USDT", "interval": "1h", "limit": 4}, timeout=15)
    d = r.json() if r.status_code == 200 else []
    c = {int(x["t"]): float(x["c"]) for x in d if isinstance(x, dict) and x.get("c")}
    return c.get(h), c.get(h - 3600)

def pump_lists(now, gt):
    """Каждый час: все монеты Upbit/Bithumb, у которых есть фьючерс Gate (оборот проверяется в момент сигнала)."""
    TRACK["gate"] = gt
    TRACK["coins"] = {src: sorted({m[4:] for m in (L.get("known_by") or {}).get(src) or [] if m[4:] in gt} | {"USDT"})
                      for src in SOURCES}
    TRACK["t"] = now


def pump_skip(src, coin, h, why, reason):
    with LOCK:
        L["pump_skip"] = (L.get("pump_skip", []) + [{"t": int(h), "src": src, "coin": coin, "why": why,
                                                       "отсев": reason}])[-100:]

def pump_hour(h, now):
    """Час h (начало, UTC) закрыт: обновить свечи и найти «корейский памп»."""
    gt = gate_all()
    pump_lists(now, gt)
    found = []
    stat = {"час": lt(h + 3600), "монет": {}, "ошибок": {}, "кандидатов": 0}
    t_run = time.time()
    for src in SOURCES:
        hist = HIST.setdefault(src, {})
        stat["монет"][src] = len(TRACK["coins"].get(src, []))
        errs = 0
        for coin in TRACK["coins"].get(src, []):
            old = hist.get(coin)
            need = 200 if not old else min(200, max(3, int((h - max(old)) // 3600) + 3))   # добрать пропущенные часы
            c = None
            for k in range(2):
                try:
                    c = candles(src, coin, need)
                    break
                except Exception:
                    time.sleep(1.5)
            if c is None:
                errs += 1
                continue
            hh = hist.setdefault(coin, {})
            hh.update(c)
            for t in [t for t in hh if t < h - 200 * 3600]:
                hh.pop(t, None)
        uh = hist.get("USDT") or {}
        usd = uh[h][0] if h in uh else (uh[max(uh)][0] if uh else 0)    # курс KRW за 1 USDT на этой бирже
        if not usd:
            continue
        for coin in TRACK["coins"].get(src, []):
            hh = hist.get(coin) or {}
            if coin == "USDT" or h not in hh or h - 3600 not in hh or min(hh) > h - 160 * 3600:
                continue
            (c1, v1), (c0, _) = hh[h], hh[h - 3600]
            avg = sum(v for t, (_, v) in hh.items() if h - 168 * 3600 <= t < h) / 168
            ur1 = c1 / c0 - 1 if c0 > 0 else 0
            if not (v1 / usd >= PUMP_MINV and avg > 0 and v1 >= PUMP_MULT * avg and ur1 >= PUMP_UP):
                continue
            found.append((src, coin, c1, c0, v1, avg, ur1, usd))
        stat["ошибок"][src] = errs
    stat["кандидатов"] = len(found)
    stat["сек"] = round(time.time() - t_run)
    TRACK["stat"] = stat
    for src, coin, c1, c0, v1, avg, ur1, usd in found:
        why = f"{NAME[src]} +{ur1 * 100:.1f}% за час, оборот ×{v1 / avg:.0f} (${v1 / usd / 1e3:.0f} тыс.)"
        sig = {"coin": coin, "src": src, "t": int(h), "ur1": round(ur1 * 100, 2), "mult": round(v1 / avg, 1),
               "usd_h": round(v1 / usd), "why": why, "enter_at": int(h + PUMP_DELAY_S + 30), "r": {}}
        try:
            g1, g0 = gate_hour(coin, h)
            if not g1 or not g0:
                raise Skip("нет часовых свечей Gate")
            gr1 = g1 / g0 - 1
            sig["gr1"] = round(gr1 * 100, 2)
            sig["why"] = why = why + f", Gate за тот же час {gr1 * 100:+.1f}%"
            if ur1 - gr1 < PUMP_GAP:
                raise Skip(f"Gate вырос почти так же ({gr1 * 100:+.1f}%) — это не корейский памп")
            ratio = (c1 / usd) / g1
            if not 0.8 <= ratio <= 1.25:
                raise Skip(f"цена там и на Gate не сходится (×{ratio:.2f}) — похоже, разные монеты")
            if not candles(src, coin, 1, to=now - PUMP_AGE_D * 86400):
                raise Skip(f"на {NAME[src]} торгуется меньше {PUMP_AGE_D} дней")
            d = gt_contract(coin)
            ct = float(d.get("create_time") or d.get("launch_time") or 0)
            if ct and now - ct < PUMP_AGE_D * 86400:
                raise Skip(f"фьючерс Gate моложе {PUMP_AGE_D} дней")
            if gt.get(coin, (0, 0))[1] < PUMP_GVOL:
                raise Skip("на Gate оборот меньше $1 млн за сутки")
            with LOCK:
                if now - L["pump_cool"].get(coin, 0) < PUMP_COOL_H * 3600:
                    raise Skip("по монете уже был сигнал за сутки")
                L["pump_cool"][coin] = int(now)
                sig["state"] = "жду входа"
                L["pump_sig"] = (L["pump_sig"] + [sig])[-300:]
                save()
            say(f"сигнал «корейский памп» {coin}: {why}. Шорт на Gate — в {lt(sig['enter_at'])}", live=PUMP_LIVE)
        except Skip as x:
            log.info("pump %s %s: %s", src, coin, x)
            pump_skip(src, coin, h, why, str(x))
        except Exception as x:
            log.exception("pump check %s", coin)
            pump_skip(src, coin, h, why, f"ошибка проверки: {str(x)[:80]}")

def pump_enter(now):
    """Вход через час после сигнала + бумага по каждому сигналу (итог шорта через 24 и 72 ч, стоп 20%)."""
    gt = None
    for s in L["pump_sig"]:
        if s.get("state") == "жду входа" and now >= s["enter_at"]:
            try:
                q = gt_ticker(s["coin"])
            except Exception:
                q = None
            s["px"] = q["last"] if q else None
            s["hi"] = s["px"]
            if now - s["enter_at"] > 1800:
                s["state"] = "пропуск: сервер был выключен — поздно"
                continue
            e = {"coin": s["coin"], "src": s["src"], "t_start": s["t"], "late_h": 0, "why": s["why"]}
            try:
                try_open(e, q, now, kind="pump")
                pp = L["pos"].get(s["coin"]) or {}
                s["state"] = "шорт" if pp.get("seen") or pp.get("mode") == "paper" else "ордер отправлен"
            except Skip as x:
                s["state"] = f"пропуск: {x}"
                say(f"памп {s['coin']}: не захожу — {x} (пишу на бумагу)", live=PUMP_LIVE)
            except Exception as x:
                log.exception("pump open %s", s["coin"])
                s["state"] = f"ошибка: {str(x)[:120]}"
            save()
        elif s.get("px") and "72ч" not in s["r"] and now - L.get("pump_trk_t", 0) >= 300:
            age = now - s["enter_at"]
            if gt is None:
                try:
                    gt = gate_all()
                except Exception:
                    return
            px = (gt.get(s["coin"]) or (0, 0))[0]
            if not px:
                continue
            if age <= 72 * 3600:
                s["hi"] = max(s.get("hi") or px, px)
            stopped = (s.get("hi") or 0) >= s["px"] * (1 + SL)
            r = round(-SL * 100 - PUMP_COST if stopped else (s["px"] / px - 1) * 100 - PUMP_COST, 2)
            if age >= 24 * 3600 and "24ч" not in s["r"]:
                s["r"]["24ч"] = r
            if age >= 72 * 3600:
                s["r"]["72ч"] = r
    if gt is not None:
        L["pump_trk_t"] = now

def pump_summary():
    S = L.get("pump_sig") or []
    done = [s["r"]["72ч"] for s in S if "72ч" in s.get("r", {})]
    t = L.get("tot_pump") or {}
    head = (f"Корейский памп ({'ЖИВОЙ' if PUMP_LIVE else PUMP_MODE}, ${PUMP_USD:.0f} ×{LEV}, позиций ≤{PUMP_POS}): "
            f"сделок {t.get('n', 0)}, {t.get('pnl', 0):+.2f}$ · сигналов {len(S)}")
    if L.get("paused_pump"):
        head += f" · ⛔ {L['paused_pump']}"
    if not done:
        return head + ", итогов по бумаге пока нет"
    return (head + f" | бумага по всем сигналам: закрыто {len(done)}, в среднем {statistics.mean(done):+.2f}% = "
            f"{statistics.mean(done) * 2:+.2f}$ на $200, в плюсе {sum(1 for x in done if x > 0) * 100 // len(done)}% "
            f"(72 ч, стоп 20%, комиссия 0,4%)")

def pump_loop():
    time.sleep(150)
    while not L.get("loaded"):
        time.sleep(30)
    while True:
        try:
            now = time.time()
            h = int(now // 3600) * 3600 - 3600                 # последний закрытый час
            if PUMP_ON and PUMP_MODE != "off" and L.get("pump_h", 0) < h and now - (h + 3600) >= 90:
                pump_hour(h, now)
                L["pump_h"] = h
            if PUMP_ON:
                with LOCK:
                    pump_enter(time.time())
        except Exception:
            log.exception("xupbit pump")
        time.sleep(20)

# ---------- бумага «предупреждения бирж» ----------
def flags_now(src):
    d = up_get("/market/all", src, isDetails="true") or []
    out = {}
    for x in d:
        m = str(x.get("market", ""))
        if not m.startswith("KRW-"):
            continue
        f = set()
        ev = x.get("market_event") or {}
        if ev.get("warning") or str(x.get("market_warning") or "NONE").upper() not in ("NONE", ""):
            f.add("предупреждение")
        for k, v in (ev.get("caution") or {}).items():
            if v:
                f.add(CAUTION_RU.get(k, k))
        out[m[4:]] = sorted(f)
    return out

def flags_step(now):
    if not FLAGS_ON or now - L.get("flags_t", 0) < FLAGS_EVERY:
        return
    L["flags_t"] = now
    try:
        gt = gate_all()
    except Exception:
        return
    for src in SOURCES:
        try:
            cur = flags_now(src)
        except Exception:
            continue
        prev = L["flags"].get(src)
        L["flags"][src] = cur
        if prev is None:                               # первый запуск: запоминаю, событий нет
            continue
        for coin, fl in cur.items():
            for f in set(fl) - set(prev.get(coin, [])):
                if coin in gt:
                    L["fev"].append({"coin": coin, "src": src, "flag": f, "t": int(now), "px": gt[coin][0],
                                     "hi": gt[coin][0], "r": {}})
                    if FLAG_LIVE and f in FLAG_SET:    # (v2.5) живой шорт по метке
                        ev = L["fev"][-1]
                        try:
                            if gt[coin][1] < FLAG_VOL:
                                raise Skip(f"оборот фьючерса Gate ${gt[coin][1] / 1e3:.0f} тыс. за сутки — меньше ${FLAG_VOL / 1e3:.0f} тыс.")
                            try_open({"coin": coin, "src": src, "t_start": int(now), "late_h": 0, "why": f},
                                     gt_ticker(coin), now, kind="flag")
                            ev["live"] = "шорт"
                        except Skip as x:
                            ev["live"] = f"пропуск: {x}"
                        except Exception as x:
                            log.exception("flag open %s", coin)
                            ev["live"] = f"ошибка: {str(x)[:100]}"
                        save()
    L["fev"] = L["fev"][-400:]
    for e in L["fev"]:
        if "72ч" in e["r"] or e["coin"] not in gt:
            continue
        px, age = gt[e["coin"]][0], now - e["t"]
        e["hi"] = max(e["hi"], px)
        r = round((e["px"] / px - 1) * 100 - PUMP_COST, 2)
        for k, hh in (("1ч", 1), ("24ч", 24), ("72ч", 72)):
            if age >= hh * 3600 and k not in e["r"]:
                e["r"][k] = r

def flags_summary():
    by = {}
    for e in L.get("fev") or []:
        if "24ч" in e["r"]:
            by.setdefault(f"{NAME[e['src']]}: {e['flag']}", []).append(e)
    if not by:
        return f"Бумага «предупреждения бирж»: событий {len(L.get('fev') or [])}, итогов за сутки пока нет"
    rows = []
    for k, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
        r24 = [e["r"]["24ч"] for e in v]; r72 = [e["r"]["72ч"] for e in v if "72ч" in e["r"]]
        rows.append(f"{k} — {len(v)}: шорт 24 ч {statistics.mean(r24) * 2:+.2f}$"
                    + (f", 72 ч {statistics.mean(r72) * 2:+.2f}$" if r72 else ""))
    return "Бумага «предупреждения бирж» (шорт на Gate, $200, комиссия 0,4%): " + "; ".join(rows[:6])

# ---------- ведение ----------
def finish(p, outcome, ex, pnl, now, note=""):
    L["pos"].pop(p["coin"], None)
    nt = p["n"] * p["cs"] * p["px"]
    if pnl is None:
        pnl = nt * (p["px"] - ex) / p["px"] - nt * 0.001
    rec = {"coin": p["coin"], "mode": p["mode"], "t": p["t"], "t_close": int(now), "px": p["px"], "exit": ex,
           "pnl": round(pnl, 2), "outcome": outcome, "worst_pct": round((p["hi"] / p["px"] - 1) * 100, 1), "note": note}
    K = KINDS[kind_of(p)]
    rec["kind"] = kind_of(p)
    L["done"] = (L["done"] + [rec])[-200:]
    tkey, pkey, lim = K["tot"], K["paused"], K["loss"]
    if p["mode"] == ("live" if K["live"] else "paper"):
        t = L[tkey]
        t["n"] += 1
        k = "sl" if outcome.startswith("стоп") or outcome == "ликвидация" else "time" if outcome == "время" else "other"
        t[k] = t.get(k, 0) + 1
        t["pnl"] = round(t["pnl"] + pnl, 2)
        if t["pnl"] <= -lim and not L.get(pkey):
            L[pkey] = f"общий убыток {K['gen']} {t['pnl']:+.2f}$ — больше лимита ${lim:.0f}"
            say(f"⛔ {L[pkey]}. Новых шортов {K['by']} нет. Включить снова: "
                f"/upbit/resume{K['q']}", live=p["mode"] == "live")
    t = L[tkey]
    say(f"{'✅' if pnl > 0 else '❌'} {p['coin']} ({K['ru']}): {outcome} через {(now - p['t']) / 3600:.1f} ч, "
        f"выход {ex:.6g}, итог {pnl:+.2f}$ (хуже всего по пути +{rec['worst_pct']:.1f}%){note}\n"
        f"Всего {K['by']}: сделок {t['n']}, {t['pnl']:+.2f}$", live=p["mode"] == "live")
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
        elif q and p.get("tp") and q["last"] <= p["tp"]:
            finish(p, "тейк", q["last"], None, now)
        elif now - p["t"] >= p.get("hold_h", HOLD_H) * 3600:
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
    if now - p["t"] >= p.get("hold_h", HOLD_H) * 3600:
        close_now(p, "время", now)
        return
    if not q:
        return
    if p.get("tp") and q["last"] <= p["tp"]:           # (v2.4) тейк ведёт бот
        close_now(p, "тейк", now)
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
                    say(f"⚠️ {coin}: стоп на Gate не встаёт — веду сам (закрою при +{p.get('slp', SL) * 100:g}%)")
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
        f = L.setdefault("upfail", {})
        try:
            cur = krw_markets(src)
        except Exception as x:
            cur = None
            s_ = f.setdefault(src, {"n": 0, "t0": int(now)})
            s_["n"] += 1
            s_["err"] = str(x)[:100]
            if s_["n"] >= FAIL_N and now - s_.get("warn_t", 0) > 3 * 3600:
                s_["warn_t"] = int(now)
                say(f"⚠️ не вижу {NAME[src]} уже {round((now - s_['t0']) / 60)} мин ({s_['n']} опросов подряд): "
                    f"{s_['err']} — новые листинги там пока не ловлю")
        if cur:
            s_ = f.pop(src, None)
            if s_ and s_.get("warn_t"):
                say(f"✅ {NAME[src]} снова на связи (не отвечал {round((now - s_['t0']) / 60)} мин) — листинги снова ловлю")
            L.setdefault("up_ok", {})[src] = int(now)
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
    try:
        flags_step(now)
    except Exception:
        log.exception("xupbit flags")
    try:
        daily(now)
    except Exception:
        log.exception("xupbit daily")
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
           f"Всего: сделок {t.get('n', 0)}, по времени {t.get('time', 0)}, стопов {t.get('sl', 0)}, {t.get('pnl', 0):+.2f}$",
           "Связь: " + " · ".join(
               f"{NAME[s_]} " + (f"⚠️ не отвечает {round((time.time() - (L.get('upfail') or {})[s_]['t0']) / 60)} мин"
                                 if (L.get("upfail") or {}).get(s_, {}).get("n", 0) >= FAIL_N
                                 else f"ок {lt((L.get('up_ok') or {}).get(s_), '%H:%M:%S')}" if (L.get("up_ok") or {}).get(s_)
                                 else "ещё не опрашивал") for s_ in SOURCES)]
    for p in (L.get("pos") or {}).values():
        out.append(f"Открыт ({KINDS[kind_of(p)]['ru']}{'' if p['mode'] == 'live' else ', бумага'}): "
                   f"{p['coin']} с {lt(p['t'])} по {p['px']:.6g}, стоп {p['sl']:.6g}, закрою "
                   f"{lt(p['t'] + p.get('hold_h', HOLD_H) * 3600)}" + (" · стоп на бирже ✅" if p.get("stop_ok") else ""))
    for k in (L.get("wait") or {}):
        out.append(f"Жду начала торгов: {k.replace('|', ' ')}")
    for e in (L.get("ev") or [])[-8:]:
        out.append(f"{e['coin']} ({NAME.get(e.get('src', 'upbit'))}) {lt(e['t_start'])}: {e.get('action', '—')}" +
                   (f" | шорт по бумаге: {', '.join(f'{k} {v:+.1f}%' for k, v in e['after'].items())}" if e.get("after") else ""))
    for d in (L.get("done") or [])[-5:]:
        out.append(f"Закрыт {d['coin']} ({KINDS.get(d.get('kind'), KINDS['listing'])['ru']}): {d['outcome']}, {d['pnl']:+.2f}$")
    if PUMP_ON:
        out.append(pump_summary())
        st = TRACK.get("stat")
        if st:
            out.append(f"  памп слежу: " + ", ".join(f"{NAME[s_]} {n} монет" + (f" (ошибок {st['ошибок'].get(s_)})"
                                                                               if st["ошибок"].get(s_) else "")
                                                     for s_, n in st["монет"].items())
                       + f" · час до {st['час']}: кандидатов {st['кандидатов']}, проверка {st.get('сек', '?')} с")
        for x in (L.get("pump_skip") or [])[-3:]:
            out.append(f"  отсев {x['coin']} ({NAME.get(x['src'])}) {lt(x['t'] + 3600)}: {x['отсев']}")
        for s in (L.get("pump_sig") or [])[-5:]:
            out.append(f"  памп {s['coin']} {lt(s['t'] + 3600)}: {s['why']} → {s.get('state', '—')}"
                       + (f" | бумага: {', '.join(f'{k} {v:+.1f}%' for k, v in s['r'].items())}" if s.get("r") else ""))
    if PREM_ON:
        tp_ = L.get("tot_prem") or {}
        out.append(f"Переплата вживую ({'ЖИВОЙ' if PREM_LIVE else PREM_MODE}, ${PREM_USD:.0f} ×{LEV}, стоп +{PREM_SL * 100:g}%, "
                   + (f"тейк {PREM_TP * 100:g}%" if PREM_TP > 0 else "без тейка") + f", держу {PREM_HOLD_H:g} ч, позиций ≤{PREM_POS}): "
                   f"сделок {tp_.get('n', 0)}, стопов {tp_.get('sl', 0)}, {tp_.get('pnl', 0):+.2f}$"
                   + (f" · ⛔ {L['paused_prem']}" if L.get("paused_prem") else ""))
        out.append(prem_summary())
    if FLAGS_ON:
        tf_ = L.get("tot_flag") or {}
        out.append(f"Метки Upbit вживую ({'ЖИВОЙ' if FLAG_LIVE else FLAG_MODE}, ${FLAG_USD:.0f} ×{LEV}, стоп +{FLAG_SL * 100:g}%, "
                   f"держу {FLAG_HOLD_H:g} ч, позиций ≤{FLAG_POS}): сделок {tf_.get('n', 0)}, стопов {tf_.get('sl', 0)}, "
                   f"{tf_.get('pnl', 0):+.2f}$" + (f" · ⛔ {L['paused_flag']}" if L.get("paused_flag") else ""))
        for e in [x for x in (L.get("fev") or []) if x.get("live")][-4:]:
            out.append(f"  метка {e['coin']} {lt(e['t'])} «{e['flag']}» → {e['live']}")
        out.append(flags_summary())
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
                        "рынков_known": {s_: len(v) for s_, v in (L.get("known_by") or {}).items()},
                        "памп": {"режим": PUMP_MODE if PUMP_ON else "off", "позиция_$": PUMP_USD, "позиций": PUMP_POS,
                                 "общий_убыток_$": PUMP_LOSS, "оборот_×": PUMP_MULT, "рост_%": PUMP_UP * 100,
                                 "больше_Gate_на_%": PUMP_GAP * 100, "всего": L.get("tot_pump"),
                                 "пауза": L.get("paused_pump"), "слежу_монет": {s_: len(v) for s_, v in (TRACK.get("coins") or {}).items()}}})

@mm.app.route("/upbit/pump")
def upbit_pump():
    """Корейский памп: все сигналы (и пропущенные) с итогом бумаги через 24 и 72 ч."""
    if not ok():
        return "forbidden", 403
    with LOCK:
        return jsonify({"версия": VERSION, "итог": pump_summary(), "предупреждения_бирж": flags_summary(),
                        "сигналы": [dict(s, час=lt(s["t"]), вход=lt(s["enter_at"])) for s in (L.get("pump_sig") or [])[-200:]],
                        "отсев": [dict(x, час=lt(x["t"] + 3600)) for x in (L.get("pump_skip") or [])[-100:]],
                        "слежение": TRACK.get("stat"),
                        "метки_бирж_события": (L.get("fev") or [])[-100:]})

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
    K = KINDS.get(request.args.get("kind"), KINDS["listing"])
    with LOCK:
        L[K["paused"]] = "вручную"
        save()
    return (f"пауза {K['gen']}: новых шортов нет, открытые бот ведёт дальше. "
            f"Снять: /upbit/resume{K['q']}")

@mm.app.route("/upbit/resume")
def upbit_resume():
    if not ok():
        return "forbidden", 403
    K = KINDS.get(request.args.get("kind"), KINDS["listing"])
    with LOCK:
        L[K["paused"]] = ""
        if request.args.get("reset") == "1":           # обнулить счёт убытка (после разбора)
            L[K["tot"]] = {"n": 0, "sl": 0, "time": 0, "other": 0, "pnl": 0.0}
        save()
    return f"{K['ru']}: работает" + (" — счёт обнулён" if request.args.get("reset") == "1" else "")

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
threading.Thread(target=pump_loop, daemon=True).start()
