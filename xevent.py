# xevent.py — v2 · 21.11 — POLYMARKET: ВСЕ СОБЫТИЯ, КРОМЕ КРИПТО, С РАСЧЁТОМ В БЛИЖАЙШИЕ 10 ЧАСОВ. Ничего не покупает.
#   Алерт в Telegram: какой исход покупать и не дороже какой цены. Каждый алерт сразу записывается бумажной сделкой
#   на $XEV_USD и сверяется с итогом рынка — так видно, насколько модель права. Заменяет v1 (горизонт 1–45 дней).
#
#   ЧТО СМОТРИТ. Раз в XEV_SEC (600 с) — все открытые события Polymarket с концом в ближайшие XEV_H_MAX (10) ч.
#   Крипто исключено (тег Crypto, названия монет, тип комиссии). Время расчёта спорта = начало матча + длительность
#   игры (футбол 2 ч, баскетбол 2,6 ч, хоккей 2,7 ч, американский футбол 3,6 ч, киберспорт 3 ч …).
#
#   ПИРАМИДА — алерт «СТАВКА» только если пройдены все ступени, снизу вверх:
#   ① ДАННЫЕ: рынок принимает ставки, 2 исхода, ещё не решён, матч не начался, до начала/конца ≥ 20 мин, ликвидность
#      ≥ $XEV_LIQ (погода ≥ $XEV_LIQ_WX), спред ≤ XEV_SPREAD (погода ≤ XEV_SPREAD_WX), цена стороны 0,06–0,94, за час
#      цена не прыгнула на XEV_MOVE (0,10) и больше (скачок = новость, которую мы ещё не видим).
#   ② НЕЗАВИСИМАЯ ОЦЕНКА — хотя бы один внешний источник (сама цена рынка — не основание):
#      • БУКМЕКЕРЫ: коэффициенты матча с ESPN (DraftKings), маржа букмекера убрана; с ключом XEV_ODDS_KEY (the-odds-api.com)
#        — ещё и Pinnacle, самый точный букмекер;
#      • ПОГОДА: рынки «самая высокая / низкая температура в городе за день». Фактические замеры станции из правил рынка
#        (METAR, aviationweather.gov) + ансамблевый прогноз Open-Meteo на оставшиеся часы дня (десятки вариантов
#        прогноза) → вероятность каждого диапазона;
#      • ИИ (с ключом XEV_AI_KEY): Claude с поиском в интернете читает свежие новости и даёт свою вероятность. Цену рынка
#        ему не показываем, чтобы он не подстраивался;
#      • КАЛИБР: поправка из «тени» (ниже) — включается сама, только когда ошибка толпы доказана статистикой.
#   ③ СОГЛАСИЕ: каждый источник по отдельности за ту же сторону; противоречит хоть один — алерта нет. Нужен сильный
#      источник (Pinnacle, погода с замерами станции) или два согласных.
#   ④ ПЕРЕВЕС: наша вероятность минус цена покупки на $XEV_USD по настоящему стакану (с комиссией рынка) ≥ XEV_EDGE
#      (0,05) и доходность ≥ XEV_ROI (8%) на вложенный доллар.
#   ⑤ ПРОВЕРКА НОВОСТЕЙ ИИ (если ключ задан): ИИ уверенно против или видит свежие новости против — вето.
#   ⑥ ЛИМИТЫ: не больше XEV_DAY_MAX (8) алертов в сутки (по Иркутску), один рынок — раз в сутки.
#   «ИИ-СТАВКА» — отдельный тип для политики, культуры, упоминаний и т. п., где нет букмекеров и погоды: только если ИИ
#   уверен (high) и перевес ≥ 0,10. Её точность считается отдельно (XEV_AI_SOLO=0 — выключить).
#
#   НЕСТЫКОВКА (без прогноза, чистая математика): во взаимоисключающих исходах одного события сумма цен покупки «ДА»
#   с комиссией < 1 — купить «ДА» на все исходы (выплата ровно $1); или сумма «НЕТ» дешевле гарантированной выплаты.
#   Каждая нога проверяется по настоящему стакану.
#
#   ТЕНЬ — поиск новых зависимостей. По каждому рынку окна пишется снимок (цена, часы до расчёта, категория, спред,
#   ликвидность, ход цены за час и сутки, перекос суммы группы, оценка внешних источников), после расчёта — исход.
#   /ev/deps показывает, где толпа ошибается системно: по категориям, диапазонам цены и признакам, со стандартной ошибкой.
#   Сравнение «рынок против источника» (оценка Брайера) показывает, какой источник точнее рынка.
#
#   КОМИССИЯ — из параметров рынка (feeSchedule): доли × ставка × (p × (1 − p))^степень; если их нет — по таблице
#   категорий (ноябрь 2026: спорт, финансы, технологии, выборы, прочее 0,05; политика без выборов, экономика, культура,
#   погода, упоминания 0,01; геополитика 0). Комиссию Pulse Market модуль не знает — сверяйте цену перед ставкой.
#
#   Страницы (?t=STATS_TOKEN): /ev — всё таблицей, /ev/brief — коротко (&tg=1 — в Telegram), /ev/deps — зависимости,
#   /ev/ask?q=<ссылка на событие Polymarket> — разбор одного события (&ai=1 — с ИИ, тратит лимит XEV_AI_DAY).
#   XEV_ON=0 — выключить, XEV_TG=0 — без алертов, XEV_DAILY=0 — без итога дня в XEV_DAILY_HOUR (9:00 Иркутск).
#   ЧЕСТНО: рынки Polymarket в целом точные, поэтому алертов будет немного — это нормально. Ставить деньги — только
#   после 30+ бумажных сделок в плюсе по этому типу (см. /ev/brief).
import os, re, time, json, math, random, threading, logging, unicodedata, datetime as dt
import requests
from flask import jsonify, request
import mm_scanner as mm
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

VERSION = "xevent v2 · 21.11"
ON = env("XEV_ON", "1") == "1"
TG = env("XEV_TG", "1") == "1"
DAILY = env("XEV_DAILY", "1") == "1"
DAILY_HOUR = int(env("XEV_DAILY_HOUR", "9"))
STEP = int(env("XEV_SEC", "600"))                       # пауза между проходами, сек
H_MAX = float(env("XEV_H_MAX", "10"))                   # расчёт рынка не позже, ч
H_MIN = float(env("XEV_H_MIN", "0.33"))                 # до начала матча / конца рынка не меньше, ч (успеть поставить)
PAGES = int(env("XEV_PAGES", "8"))                      # страниц по 100 событий за проход
EV_LIQ = float(env("XEV_EV_LIQ", "300"))                # ликвидность события в запросе
LIQ, LIQ_WX = float(env("XEV_LIQ", "2000")), float(env("XEV_LIQ_WX", "150"))
SPR, SPR_WX = float(env("XEV_SPREAD", "0.05")), float(env("XEV_SPREAD_WX", "0.12"))
EDGE = float(env("XEV_EDGE", "0.05"))
ROI = float(env("XEV_ROI", "0.08"))
PMIN, PMAX = float(env("XEV_PMIN", "0.06")), float(env("XEV_PMAX", "0.94"))
MOVE = float(env("XEV_MOVE", "0.10"))
USD = float(env("XEV_USD", "20"))
MIN_FILL = float(env("XEV_MIN_FILL", "5"))              # стакан тоньше — не алертим
DAY_MAX = int(env("XEV_DAY_MAX", "8"))
ARB = float(env("XEV_ARB", "0.015"))                    # минимум гарантированной прибыли на $1 после комиссий
ESPN_ON = env("XEV_ESPN", "1") == "1"
WX_ON = env("XEV_WX", "1") == "1"
ODDS_KEY = env("XEV_ODDS_KEY")
ODDS_DAY = int(env("XEV_ODDS_DAY", "15"))               # запросов к the-odds-api в сутки (бесплатно 500 в месяц)
ODDS_SPORTS = [s.strip() for s in env("XEV_ODDS_SPORTS").split(",") if s.strip()]   # пусто — все лиги из TAG_ODDS
AI_KEY = env("XEV_AI_KEY") or env("ANTHROPIC_API_KEY")
AI_MODEL = env("XEV_AI_MODEL", "claude-sonnet-5-5")
AI_DAY = int(env("XEV_AI_DAY", "12"))                   # вызовов ИИ в сутки (≈ $0,05–0,15 за вызов с поиском)
AI_SOLO = env("XEV_AI_SOLO", "1") == "1"
SH_MAX = int(env("XEV_SHADOW", "6000"))
GAMMA, CLOB = "https://gamma-api.polymarket.com", "https://clob.polymarket.com"
TZ8 = 8 * 3600                                           # Иркутск
log = logging.getLogger("xevent")
SES = requests.Session()
SES.headers.update({"User-Agent": "Mozilla/5.0 skin-monitor-xevent"})
LOCK = threading.Lock()
S = {"loaded": 0, "pos": {}, "done": [], "seen": {}, "day": ["", 0], "log": [], "arb_n": 0, "ai": ["", 0],
     "odds": ["", 0], "aic": {}, "daily": ""}
SH = {"loaded": 0, "sh": {}, "shl": {}, "shd": []}
M = {"err": "", "last": 0, "dur": 0, "n_ev": 0, "n_cr": 0, "n_mk": 0, "by_cat": {}, "cand": [], "rej": {}, "src": {},
     "cal": {}, "veto": []}
CACHE = {}                                               # url → (время, данные)

W = {"pinnacle": 0.75, "espn": 0.5, "wx": 0.8, "wx_weak": 0.55, "ai_high": 0.45, "ai_medium": 0.3, "cal": 0.6}
NAMES = {"pinnacle": "Pinnacle", "espn": "букмекеры ESPN", "wx": "погода", "ai": "ИИ", "cal": "калибр"}
CATS = ["sports", "esports", "weather", "politics", "elections", "geopolitics", "economics", "finance", "tech",
        "culture", "mentions", "other"]
CAT_RU = {"sports": "спорт", "esports": "киберспорт", "weather": "погода", "politics": "политика", "elections": "выборы",
          "geopolitics": "геополитика", "economics": "экономика", "finance": "финансы", "tech": "технологии",
          "culture": "культура", "mentions": "упоминания", "other": "прочее"}
FEE_CAT = {"sports": 0.05, "esports": 0.05, "finance": 0.05, "tech": 0.05, "elections": 0.05, "other": 0.05,
           "politics": 0.01, "economics": 0.01, "culture": 0.01, "weather": 0.01, "mentions": 0.01, "geopolitics": 0.0}
SPORT_DUR = (("esports", 3.0), ("esport", 3.0), ("soccer", 2.0), ("football", 3.6), ("nfl", 3.6), ("cfb", 3.6), ("basketball", 2.6),
             ("nba", 2.6), ("ncaab", 2.4), ("hockey", 2.7), ("nhl", 2.7), ("baseball", 3.2), ("mlb", 3.2),
             ("tennis", 2.5), ("ufc", 5.0), ("mma", 5.0), ("boxing", 5.0), ("cricket", 8.0), ("rugby", 2.0),
             ("golf", 6.0))
SPORT_RE = re.compile(r"(?<![a-z0-9])(sports?|soccer|football|basketball|hockey|baseball|tennis|nba|nfl|nhl|mlb|ufc|mma|"
                      r"boxing|cricket|rugby|golf|f1|formula[ -]1|nascar|ncaa[bf]?|cfb|cbb|epl|premier[ -]league|la[ -]liga|"
                      r"serie[ -]a|bundesliga|ligue[ -]1|champions[ -]league|europa[ -]league|mls)(?![a-z0-9])")
ESPN_LEAGUES = {"soccer": ["eng.1", "eng.2", "esp.1", "ita.1", "ger.1", "fra.1", "ned.1", "por.1", "sco.1", "tur.1",
                           "bel.1", "usa.1", "mex.1", "bra.1", "arg.1", "chi.1", "col.1", "ksa.1", "jpn.1", "aus.1",
                           "uefa.champions", "uefa.europa", "uefa.europa.conf"],
                "basketball": ["nba", "mens-college-basketball"], "football": ["nfl", "college-football"],
                "hockey": ["nhl"], "baseball": ["mlb"]}
CR_TAGS = ("crypto", "bitcoin", "ethereum", "solana", "xrp", "dogecoin", "defi", "memecoin", "altcoin", "stablecoin")
CR_RE = re.compile(r"\b(bitcoin|btc|ethereum|eth|solana|xrp|dogecoin|doge|crypto\w*|memecoin|altcoin|stablecoin|usdt|"
                   r"usdc|coinbase|binance|airdrop|fdv|hyperliquid|megaeth|token launch)\b", re.I)
MON = {m: i + 1 for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                       "september", "october", "november", "december"])}
MON.update({k[:3]: v for k, v in list(MON.items())})
MON["sept"] = 9
WX_RE = re.compile(r"\b(highest|lowest|high|low)\s+temp(?:erature)?\s+in\s+(.+?)\s+on\s+([A-Za-z]+)\.?\s+(\d{1,2})\b", re.I)
STOP = {"fc", "cf", "sc", "ac", "afc", "cd", "ud", "sd", "rc", "rcd", "ca", "club", "de", "del", "la", "el", "the",
        "calcio", "ssc", "as", "us", "fk", "sk", "bk", "if", "sv", "cp", "sl", "and", "of", "1", "ii"}

# ---------- мелочи ----------
def lt(t=None, f="%d.%m %H:%M"):
    return time.strftime(f, time.gmtime((t if t is not None else time.time()) + TZ8))

def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))

def get(url, timeout=25, **params):
    r = SES.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()

def purge():
    """Кэш живёт не дольше 2 ч: вчерашние табло и прогнозы не копятся в памяти."""
    old = time.time() - 7200
    for k in [k for k, v in list(CACHE.items()) if v[0] < old]:
        CACHE.pop(k, None)

def memo(key, ttl, fn):
    """Результат fn() (уже сжатый, не сырой ответ API) — из кэша, если свежий."""
    hit = CACHE.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    v = fn()
    purge()
    CACHE[key] = (time.time(), v)
    return v

def fnum(x, d=None):
    try:
        v = float(x)
        return v if math.isfinite(v) else d
    except (TypeError, ValueError):
        return d

def jl(x):
    if isinstance(x, list):
        return x
    try:
        v = json.loads(x or "[]")
        return v if isinstance(v, list) else []
    except (TypeError, ValueError):
        return []

def ts(s):
    if not s:
        return None
    try:
        s = str(s).strip().replace("Z", "+00:00").replace(" ", "T", 1)
        s = re.sub(r"([+-]\d{2})$", r"\1:00", s)
        d = dt.datetime.fromisoformat(s)
        return (d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)).timestamp()
    except Exception:
        return None

def lg(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))

def sg(z):
    return 1 / (1 + math.exp(-max(-30, min(30, z))))

def fee(p, rate, ex=1.0):
    return rate * (p * (1 - p)) ** (ex or 1.0)

def day8(t):
    return time.strftime("%Y-%m-%d", time.gmtime(t + TZ8))

def note(text, now):
    S["log"] = (S["log"] + [{"t": int(now), "x": text[:200]}])[-300:]

def src_state(name, text):
    M["src"][name] = text

# ---------- классификация ----------
def tags_text(ev):
    return " ".join(f"{t.get('slug', '')} {t.get('label', '')}" for t in ev.get("tags") or [] if isinstance(t, dict)).lower()

def category(ev):
    s, title = tags_text(ev), (ev.get("title") or "").lower()
    if "esport" in s or "e-sport" in s:
        return "esports"
    if SPORT_RE.search(s):
        return "sports"
    if "weather" in s or "temperature" in title or re.search(r"\b(rain|snow|hurricane)\b", s):
        return "weather"
    if "election" in s:
        return "elections"
    if "geopolit" in s or "world" in s:
        return "geopolitics"
    if "mention" in s:
        return "mentions"
    if "politic" in s:
        return "politics"
    if "econom" in s or "fed" in s.split():
        return "economics"
    if "financ" in s or "stock" in s or "earnings" in s:
        return "finance"
    if "tech" in s or " ai " in f" {s} ":
        return "tech"
    if any(w in s for w in ("culture", "entertainment", "music", "movie", "celebrit", "tweet", "award")) or \
            re.search(r"\b(tv|pop)\b", s):
        return "culture"
    return "other"

def word_in(w, s):
    return re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", s) is not None

def sport_of(ev):
    s = tags_text(ev) + " " + (ev.get("title") or "").lower()
    for w, _ in SPORT_DUR:
        if word_in(w, s):
            return {"esports": "esport", "nfl": "football", "cfb": "football", "nba": "basketball", "ncaab": "basketball",
                    "nhl": "hockey", "mlb": "baseball", "ufc": "mma", "boxing": "mma"}.get(w, w)
    return "sport"

def sport_dur(ev):
    s = tags_text(ev) + " " + (ev.get("title") or "").lower()
    for w, h in SPORT_DUR:
        if word_in(w, s):
            return h
    return 3.0

def is_crypto(ev):
    s = tags_text(ev)
    if any(k in s for k in CR_TAGS) or CR_RE.search(ev.get("title") or ""):
        return True
    return any("crypto" in str(m.get("feeType") or "").lower() for m in (ev.get("markets") or [])[:3])

# ---------- рынки ----------
def norm(m, ev, cat, now, window=True):
    """Рынок с двумя исходами и живыми котировками в окне расчёта → словарь; иначе None."""
    if m.get("closed") or m.get("active") is False or m.get("acceptingOrders") is False or m.get("enableOrderBook") is False:
        return None
    outs, toks = [str(o) for o in jl(m.get("outcomes"))], [str(t) for t in jl(m.get("clobTokenIds"))]
    if len(outs) != 2 or len(toks) != 2:
        return None
    if str(m.get("umaResolutionStatus") or "").lower() in ("proposed", "resolved", "disputed", "challenged"):
        return None
    ask, bid = fnum(m.get("bestAsk")), fnum(m.get("bestBid"))
    if ask is None or not 0 < ask < 1:
        return None
    if bid is None:
        sp = fnum(m.get("spread"))
        bid = max(0.0, ask - sp) if sp is not None else 0.0
    if bid > ask or ask <= 0.015 or bid >= 0.985:
        return None                                           # исход уже решён ценой
    end, gs = ts(m.get("endDate")) or ts(ev.get("endDate")), ts(m.get("gameStartTime"))
    if cat in ("sports", "esports") and not gs:
        gs = ts(ev.get("startTime"))
    if gs:
        res, act = max(end or 0, gs + sport_dur(ev) * 3600), gs
    else:
        res, act = end, end
    if not res:
        return None
    if window and (res > now + H_MAX * 3600 or act - now < H_MIN * 3600):
        return None
    fs = m.get("feeSchedule") if isinstance(m.get("feeSchedule"), dict) else {}
    if m.get("feesEnabled") is False:
        rate, ex = 0.0, 1.0
    elif fnum(fs.get("rate")) is not None:
        rate, ex = fnum(fs.get("rate")), fnum(fs.get("exponent"), 1.0) or 1.0
    else:
        rate, ex = FEE_CAT.get(cat, 0.05), 1.0
    liq = fnum(m.get("liquidityNum")) or fnum(m.get("liquidity")) or fnum(m.get("liquidityClob")) or 0.0
    return {"id": str(m.get("id")), "q": m.get("question") or "", "g": m.get("groupItemTitle") or "", "outs": outs,
            "toks": toks, "bid": bid, "ask": ask, "mid": (bid + ask) / 2, "spr": ask - bid, "liq": liq,
            "vol": fnum(m.get("volume24hr"), 0.0), "d1h": fnum(m.get("oneHourPriceChange"), 0.0) or 0.0,
            "d1d": fnum(m.get("oneDayPriceChange"), 0.0) or 0.0, "end": end or res, "gs": gs, "res": res, "act": act,
            "rate": rate, "ex": ex, "smt": str(m.get("sportsMarketType") or ""), "slug": ev.get("slug") or "",
            "title": ev.get("title") or "", "cat": cat, "neg": bool(ev.get("negRisk")),
            "yn": [o.lower() for o in outs] == ["yes", "no"], "desc": (m.get("description") or "")[:900], "p0": None}

def label(x):
    name = x["g"] or x["q"]
    return name if x["g"] or len(name) < 90 else name[:87] + "…"

def side_name(x, i):
    if x["yn"]:
        return "ДА" if i == 0 else "НЕТ"
    return f"«{x['outs'][i]}»"

PAGE_MODE = {"i": 0}                                     # какой набор параметров Gamma принял (0 — полный)

def pages(now):
    """События с концом в окне, страницами по 100. Если Gamma не примет параметр — повтор с более простым запросом."""
    base = {"closed": "false", "end_date_min": iso(now), "end_date_max": iso(now + (H_MAX + 4) * 3600), "limit": 100}
    modes = [{"exclude_tag_id": "21", "liquidity_min": f"{EV_LIQ:g}", "order": "endDate", "ascending": "true"},
             {"exclude_tag_id": "21", "liquidity_min": f"{EV_LIQ:g}"}, {}]
    for i in range(PAGES):
        while True:
            try:
                d = get(GAMMA + "/events", timeout=45, offset=i * 100, **base, **modes[PAGE_MODE["i"]])
                break
            except requests.HTTPError as e:
                code = getattr(e.response, "status_code", 0)
                if code in (400, 422) and PAGE_MODE["i"] < len(modes) - 1:
                    PAGE_MODE["i"] += 1
                    src_state("gamma", f"запрос упрощён до варианта {PAGE_MODE['i']} (HTTP {code})")
                    continue
                raise
        if not d:
            return
        yield d
        if len(d) < 100:
            return
        time.sleep(0.3)

def book_fill(tok, usd, limit=0.99):
    """Покупка на usd по asks не дороже limit → (потрачено $, долей, средняя цена, лучшая цена) или None."""
    b = get(CLOB + "/book", timeout=15, token_id=tok)
    asks = sorted((fnum(a.get("price"), 0), fnum(a.get("size"), 0)) for a in b.get("asks") or [])
    asks = [(p, q) for p, q in asks if 0 < p <= limit and q > 0]
    if not asks:
        return None
    got = spent = 0.0
    for p, q in asks:
        v = min(usd - spent, p * q)
        spent += v
        got += v / p
        if spent >= usd - 1e-9:
            break
    return (spent, got, spent / got, asks[0][0]) if got else None

# ---------- источник: букмекеры ----------
def tok_set(name):
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    return {w for w in re.split(r"[^a-z0-9]+", s) if len(w) > 1 and w not in STOP}

def sim(a, b):
    """Похожесть названия рынка a на название источника b. Доля общих слов от БОЛЬШЕГО названия, чтобы одно общее
    слово («Wildcats», «Los Angeles») не давало совпадения. Короткое имя рынка («Lakers») внутри полного имени
    источника («Los Angeles Lakers») засчитывается, только если совпало последнее слово источника."""
    A, B = tok_set(a), tok_set(b)
    if not A or not B:
        return 0.0
    s = len(A & B) / max(len(A), len(B))
    last = [w for w in re.split(r"[^a-z0-9]+", unicodedata.normalize("NFKD", str(b)).encode("ascii", "ignore").decode().lower())
            if len(w) > 1 and w not in STOP]
    if A <= B and last and last[-1] in A:
        s = max(s, 0.75)
    return s

def amer(v):
    if v is None:
        return None
    s = str(v).strip().lower().replace("+", "")
    if s in ("even", "ev", "pk"):
        s = "100"
    o = fnum(s)
    if o is None or o == 0:
        return None
    return -o / (-o + 100) if o < 0 else 100 / (o + 100)

def espn_odds(comp):
    for o in comp.get("odds") or []:
        ml = o.get("moneyline") or {}

        def pick(side):
            s = ml.get(side) or {}
            for k in ("close", "current", "open"):
                v = (s.get(k) or {}).get("odds") if isinstance(s.get(k), dict) else None
                if v not in (None, ""):
                    return v
            return None
        h, a, d = pick("home"), pick("away"), pick("draw")
        h = h if h is not None else (o.get("homeTeamOdds") or {}).get("moneyLine")
        a = a if a is not None else (o.get("awayTeamOdds") or {}).get("moneyLine")
        d = d if d is not None else (o.get("drawOdds") or {}).get("moneyLine")
        ph, pa, pd = amer(h), amer(a), amer(d)
        if ph and pa:
            tot = ph + pa + (pd or 0)
            p = {"home": ph / tot, "away": pa / tot}
            if pd:
                p["draw"] = pd / tot
            return p, (o.get("provider") or {}).get("name") or "ESPN"
    return None, None

ESPN_PATH = {lg_: sp for sp, lgs in ESPN_LEAGUES.items() for lg_ in lgs}
TAG_ESPN = {"epl": "eng.1", "premier-league": "eng.1", "english-premier-league": "eng.1", "championship": "eng.2",
            "efl-championship": "eng.2", "la-liga": "esp.1", "laliga": "esp.1", "serie-a": "ita.1", "bundesliga": "ger.1",
            "ligue-1": "fra.1", "eredivisie": "ned.1", "primeira-liga": "por.1", "liga-portugal": "por.1",
            "scottish-premiership": "sco.1", "super-lig": "tur.1", "mls": "usa.1", "liga-mx": "mex.1",
            "brasileirao": "bra.1", "brazil-serie-a": "bra.1", "argentina-primera": "arg.1", "chile-primera": "chi.1",
            "colombia-primera": "col.1", "saudi-pro-league": "ksa.1", "j-league": "jpn.1", "a-league": "aus.1",
            "ucl": "uefa.champions", "champions-league": "uefa.champions", "uefa-champions-league": "uefa.champions",
            "uel": "uefa.europa", "europa-league": "uefa.europa", "uecl": "uefa.europa.conf",
            "conference-league": "uefa.europa.conf", "nba": "nba", "ncaab": "mens-college-basketball",
            "cbb": "mens-college-basketball", "college-basketball": "mens-college-basketball", "nfl": "nfl",
            "cfb": "college-football", "college-football": "college-football", "ncaaf": "college-football", "nhl": "nhl",
            "mlb": "mlb"}
TAG_ODDS = {"epl": "soccer_epl", "premier-league": "soccer_epl", "english-premier-league": "soccer_epl",
            "la-liga": "soccer_spain_la_liga", "laliga": "soccer_spain_la_liga", "serie-a": "soccer_italy_serie_a",
            "bundesliga": "soccer_germany_bundesliga", "ligue-1": "soccer_france_ligue_one",
            "ucl": "soccer_uefa_champs_league", "champions-league": "soccer_uefa_champs_league",
            "uefa-champions-league": "soccer_uefa_champs_league", "uel": "soccer_uefa_europa_league",
            "europa-league": "soccer_uefa_europa_league", "mls": "soccer_usa_mls", "nba": "basketball_nba",
            "nfl": "americanfootball_nfl", "nhl": "icehockey_nhl", "cfb": "americanfootball_ncaaf",
            "college-football": "americanfootball_ncaaf", "ncaaf": "americanfootball_ncaaf", "ncaab": "basketball_ncaab",
            "cbb": "basketball_ncaab", "college-basketball": "basketball_ncaab", "mlb": "baseball_mlb"}

ODDS_SPORT = {"soccer": "soccer", "basketball": "basketball", "americanfootball": "football", "icehockey": "hockey",
              "baseball": "baseball"}

def tag_keys(ev):
    out = set()
    for t in ev.get("tags") or []:
        if isinstance(t, dict):
            for v in (t.get("slug"), t.get("label")):
                if v:
                    out.add(re.sub(r"[^a-z0-9]+", "-", str(v).lower()).strip("-"))
    return out

def book_needs(xs):
    """Какие лиги ESPN и the-odds-api спрашивать: по тегам событий; лига неизвестна — весь вид спорта ESPN."""
    espn, odds = set(), set()
    for x in xs:
        if x["cat"] != "sports":
            continue
        e = {TAG_ESPN[t] for t in x.get("tags", ()) if t in TAG_ESPN}
        espn |= e or (set(ESPN_LEAGUES.get(x["sport"], [])) if ESPN_ON else set())
        odds |= {TAG_ODDS[t] for t in x.get("tags", ()) if t in TAG_ODDS}
    allow = set(ODDS_SPORTS) or set(TAG_ODDS.values())
    return sorted(espn) if ESPN_ON else [], sorted(odds & allow) if ODDS_KEY else []

def espn_board(sport, lg_, day):
    """Табло ESPN одной лиги за одну дату → сжатый список игр с вероятностями (сырой ответ в памяти не держим)."""
    extra = {"groups": "50"} if lg_ == "mens-college-basketball" else ({"groups": "80"} if lg_ == "college-football" else {})
    d = get(f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{lg_}/scoreboard", timeout=20, dates=day, limit=300,
            **extra)
    out = []
    for e in d.get("events") or []:
        comp = (e.get("competitions") or [{}])[0]
        st = ((e.get("status") or {}).get("type") or {}).get("state") or \
             ((comp.get("status") or {}).get("type") or {}).get("state")
        teams = {}
        for c in comp.get("competitors") or []:
            tm = c.get("team") or {}
            teams[c.get("homeAway") or ""] = [tm.get(k) for k in ("displayName", "shortDisplayName") if tm.get(k)]
        p, prov = espn_odds(comp)
        if p and "home" in teams and "away" in teams:
            if sport == "soccer" and "draw" not in p:
                continue                                      # в футболе без ничьей вероятности неверны
            out.append({"id": e.get("id"), "t": ts(e.get("date") or comp.get("date")), "st": st, "teams": teams, "p": p,
                        "src": "espn", "prov": prov, "lg": lg_, "sport": sport})
    return out

def espn_games(leagues, now):
    """Игры с коэффициентами из табло ESPN. Дата у ESPN — по Нью-Йорку (UTC−5), диапазон дат API не принимает."""
    out, ok, bad = [], 0, 0
    dates = sorted({time.strftime("%Y%m%d", time.gmtime(now + h * 3600 - 5 * 3600)) for h in (0, H_MAX)})
    seen = set()
    for lg_ in leagues:
        sport = ESPN_PATH.get(lg_)
        if not sport:
            continue
        for day in dates:
            try:
                games = memo(f"espn:{lg_}:{day}", 1800, lambda: espn_board(sport, lg_, day))
                ok += 1
            except Exception:
                bad += 1
                continue
            for g in games:
                if g["id"] not in seen:
                    seen.add(g["id"])
                    out.append(g)
    return out, ok, bad

def odds_games(keys, now):
    """Pinnacle через the-odds-api (если задан ключ): только нужные лиги, не чаще раза в 90 мин, дневной лимит."""
    if not ODDS_KEY:
        return []
    out = []
    for key in keys:
        url = f"https://api.the-odds-api.com/v4/sports/{key}/odds"
        ck = url + "?pin"
        hit = CACHE.get(ck)
        if not hit or time.time() - hit[0] > 5400:
            d8 = day8(now)
            if S["odds"][0] != d8:
                S["odds"] = [d8, 0]
            if S["odds"][1] >= ODDS_DAY:                       # лимит на сегодня: старше 2 ч — не используем
                data = hit[1] if hit and time.time() - hit[0] <= 7200 else []
            else:
                S["odds"][1] += 1
                try:                                          # ключ — в заголовке, чтобы не попал в логи с адресом
                    r = SES.get(url, headers={"x-api-key": ODDS_KEY}, timeout=20,
                                params={"regions": "eu", "markets": "h2h", "bookmakers": "pinnacle", "oddsFormat": "decimal",
                                        "commenceTimeFrom": iso(now), "commenceTimeTo": iso(now + H_MAX * 3600)})
                    r.raise_for_status()
                    data = r.json()
                    left = r.headers.get("x-requests-remaining")
                    if left is not None:
                        src_state("pinnacle", f"осталось запросов в месяце: {left}")
                except Exception as e:
                    src_state("pinnacle", f"ошибка {type(e).__name__} {getattr(getattr(e, 'response', None), 'status_code', '')}")
                    data = []
                purge()
                CACHE[ck] = (time.time(), data)
        else:
            data = hit[1]
        for e in data or []:
            bk = next((b for b in e.get("bookmakers") or [] if b.get("key") == "pinnacle"), None)
            mk = next((x for x in (bk or {}).get("markets") or [] if x.get("key") == "h2h"), None)
            if not mk:
                continue
            imp = {o.get("name"): 1 / fnum(o.get("price"), 0) for o in mk.get("outcomes") or [] if fnum(o.get("price"), 0) > 1}
            tot = sum(imp.values())
            h, a = e.get("home_team"), e.get("away_team")
            if not tot or h not in imp or a not in imp:
                continue
            p = {"home": imp[h] / tot, "away": imp[a] / tot}
            if "Draw" in imp:
                p["draw"] = imp["Draw"] / tot
            elif key.startswith("soccer_"):
                continue
            out.append({"t": ts(e.get("commence_time")), "st": "pre", "teams": {"home": [h], "away": [a]}, "p": p,
                        "src": "pinnacle", "prov": "Pinnacle", "lg": key, "sport": ODDS_SPORT.get(key.split("_")[0], "")})
    return out

def split_teams(title):
    t = re.split(r"\s+-\s+", title or "")[0]
    parts = re.split(r"\s+(?:vs\.?|v\.?|@|at)\s+", t, flags=re.I)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else None

def team_score(name, names):
    return max((sim(name, n) for n in names), default=0.0)

def sports_layers(x, games):
    """Оценки букмекеров для рынка «кто победит» → [(источник, q исхода A, текст)]."""
    if x["smt"] and "moneyline" not in x["smt"] and x["smt"] != "draw":
        return []
    if re.search(r"\b(half|quarter|period|set|map|game \d|inning|round)\b", x["q"], re.I):
        return []
    pair = split_teams(x["title"])
    if not pair or not x["gs"]:
        return []
    if x["yn"]:
        if re.search(r"\bdraw\b", x["q"], re.I):
            target = "draw"
        else:
            mt = re.match(r"^\s*will\s+(.+?)\s+win\b", x["q"], re.I)
            if not mt:
                return []
            target = mt.group(1)
    else:
        target = x["outs"][0]
    out = []
    for src in ("pinnacle", "espn"):
        hits = []
        for g in games:
            if g["src"] != src or g.get("sport") != x.get("sport") or not g["t"] or abs(g["t"] - x["gs"]) > 2400 \
                    or g["st"] not in ("pre", None, ""):
                continue                                      # другой вид спорта, другое время или игра уже идёт
            h, a = g["teams"]["home"], g["teams"]["away"]
            s1 = min(team_score(pair[0], h), team_score(pair[1], a))
            s2 = min(team_score(pair[0], a), team_score(pair[1], h))
            if max(s1, s2) >= 0.6:
                hits.append((max(s1, s2), g))
        if len(hits) != 1:
            continue                                          # не нашли или две похожие игры — не гадаем
        g = hits[0][1]
        if target == "draw":
            q = g["p"].get("draw")
        else:
            sh, sa = team_score(target, g["teams"]["home"]), team_score(target, g["teams"]["away"])
            if max(sh, sa) < 0.6 or abs(sh - sa) < 0.2:
                continue
            q = g["p"]["home"] if sh > sa else g["p"]["away"]
        if q:
            who = "ничья" if target == "draw" else target
            out.append((src, q, f"{g['prov']} ({g['lg']}), без маржи: «{who}» {q:.0%}"))
    return out

# ---------- источник: погода ----------
def wx_event(ev):
    m = WX_RE.search(ev.get("title") or "")
    if not m or m.group(3).lower()[:4].rstrip(".") not in MON and m.group(3).lower()[:3] not in MON:
        return None
    mon = MON.get(m.group(3).lower()) or MON.get(m.group(3).lower()[:3])
    end = ts(ev.get("endDate")) or time.time()
    ed = dt.datetime.fromtimestamp(end, dt.timezone.utc)
    y = ed.year
    try:
        cand = dt.date(y, mon, int(m.group(4)))
    except ValueError:
        return None
    if (cand - ed.date()).days > 180:
        cand = dt.date(y - 1, mon, int(m.group(4)))
    elif (ed.date() - cand).days > 180:
        cand = dt.date(y + 1, mon, int(m.group(4)))
    return ("max" if m.group(1).lower().startswith("high") else "min"), m.group(2).strip(), cand

def wx_station(text):
    for rx in (r"ids=([A-Z0-9]{4})\b", r"wunderground\.com/history/daily/(?:[^\s/]+/){1,4}([A-Z0-9]{4})\b",
               r"\(([A-Z]{4})\)", r"\b(?:station|airport)\s*\(?([A-Z]{4})\b", r"\b(K[A-Z]{3})\b"):
        m = re.search(rx, text or "")
        if m:
            return m.group(1)
    return None

def wx_bucket(g):
    g = (g or "").replace("º", "°").replace("–", "-").replace("−", "-").strip()
    unit = "F" if "F" in g.upper() else "C"
    m = re.search(r"(-?\d+)\s*°?\s*[CF]?\s*(?:-|to)\s*(-?\d+)", g)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return min(a, b), max(a, b), unit
    m = re.search(r"(-?\d+)", g)
    if not m:
        return None
    n = int(m.group(1))
    lo = g.lower()
    if re.search(r"or (below|lower|less|under)|& below|≤|<=", lo):
        return -999, n, unit
    if re.search(r"or (higher|above|more|over)|& above|≥|>=|\+", lo):
        return n, 999, unit
    return n, n, unit

def metar(icao):
    return memo(f"metar:{icao}", 600, lambda: metar_load(icao))

def metar_load(icao):
    d = get("https://aviationweather.gov/api/data/metar", timeout=20, ids=icao, format="json", hours=36)
    rows, ll = [], None
    for o in d or []:
        t, tc = fnum(o.get("obsTime")), fnum(o.get("temp"))
        m = re.search(r"\sT([01])(\d{3})[01]\d{3}\b", o.get("rawOb") or "")
        if m:                                                   # точная температура из примечаний (США)
            tc = (-1 if m.group(1) == "1" else 1) * int(m.group(2)) / 10
        if t and tc is not None:
            rows.append((t, tc, str(o.get("metarType") or "METAR").upper()))
        if ll is None and fnum(o.get("lat")) is not None:
            ll = (fnum(o.get("lat")), fnum(o.get("lon")))
    return sorted(rows), ll

def forecast(lat, lon):
    """Ансамбль Open-Meteo: [ряды температур], время (unix), сдвиг часового пояса, ансамбль ли это.
    Сначала ICON + GFS (десятки вариантов), потом только ICON, в крайнем случае — обычный прогноз (один ряд)."""
    p = {"latitude": round(lat, 3), "longitude": round(lon, 3), "hourly": "temperature_2m", "forecast_days": 3,
         "timezone": "auto", "timeformat": "unixtime"}
    return memo(f"om:{p['latitude']}:{p['longitude']}", 5400, lambda: forecast_load(p))

def forecast_load(p):
    d, ens = None, True
    for models in ("icon_seamless,gfs_seamless", "icon_seamless"):
        try:
            d = get("https://ensemble-api.open-meteo.com/v1/ensemble", timeout=30, models=models, **p)
            break
        except Exception:
            continue
    if d is None:
        d, ens = get("https://api.open-meteo.com/v1/forecast", timeout=30, **p), False
    h = d.get("hourly") or {}
    times = [int(x) for x in h.get("time") or []]
    rows = [v for k, v in h.items() if k.startswith("temperature_2m") and isinstance(v, list) and len(v) == len(times)]
    return rows, times, int(d.get("utc_offset_seconds") or 0), ens and len(rows) > 3

def wx_model(kind, day, obs, fc, now, seed):
    """Распределение итоговой температуры дня (°C) по замерам и прогнозу → (выборка, текст, сила)."""
    rows, times, off, ens = fc
    if not times or not rows:
        return None
    start = dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc).timestamp() - off
    end = start + 86400
    agg = max if kind == "max" else min
    day_obs = [(t, c) for t, c, _ in obs if start <= t < min(end, now + 60)]
    oe = agg(c for _, c in day_obs) if day_obs else None
    last = [(t, c) for t, c, _ in obs if t <= now + 60]
    t0, c0 = last[-1] if last else (None, None)
    rem = [i for i, t in enumerate(times) if max(now, start) < t < end]
    rnd = random.Random(seed)
    samples, fe = [], []
    j0 = min(range(len(times)), key=lambda i: abs(times[i] - t0)) if t0 is not None else None
    if rem:
        per = max(20, 1500 // max(1, len(rows)))
        for r in rows:
            b = 0.0                                            # поправка варианта прогноза по последнему замеру
            if j0 is not None and r[j0] is not None and abs(times[j0] - t0) <= 5400:
                b = c0 - r[j0]
            vals = [(r[i] + b * math.exp(-max(0, times[i] - (t0 or now)) / 21600), times[i]) for i in rem if r[i] is not None]
            if not vals:
                continue
            f, tf = agg(vals, key=lambda v: v[0])
            fe.append(f)
            # ошибка растёт с удалением экстремума: ближайший час ±0,6°C, через 8 ч ±1,1°C (без ансамбля — ×1,6)
            sig = min(1.6, 0.5 + 0.08 * max(0.0, (tf - now) / 3600)) * (1.0 if ens else 1.6)
            for _ in range(per):
                v = f + rnd.gauss(0, sig)
                samples.append(agg(oe, v) if oe is not None else v)
    elif oe is not None:
        samples = [oe] * 200                                   # день закончился — итог известен по замерам
    if not samples:
        return None
    fe.sort()
    word = "макс." if kind == "max" else "мин."
    lo, hi = (fe[len(fe) // 10], fe[len(fe) * 9 // 10]) if fe else (None, None)
    txt = (f"замер {word} пока {oe:.1f}°C" if oe is not None else "замеров за этот день ещё нет") + \
          (f", прогноз {word} на оставшиеся {len(rem)} ч: {lo:.1f}…{hi:.1f}°C ({len(rows)} вариантов прогноза)"
           if fe else ", день закончился")
    # сильный: станция уже меряет этот день, а варианты прогноза расходятся не больше чем на 2,5°C (или день закончился)
    strong = (len(day_obs) >= 3 and ens and fe and hi - lo <= 2.5) or (not rem and oe is not None)
    return samples, txt, bool(strong)

def wx_layers(ev, ms, now):
    """{id рынка: (q «ДА», текст, сильный ли)} для рынков-диапазонов температуры одного события."""
    w = wx_event(ev)
    if not w:
        return {}
    kind, city, day = w
    desc = " ".join((m.get("description") or "") + " " + (m.get("resolutionSource") or "") for m in (ev.get("markets") or [])[:2])
    icao = wx_station(desc)
    if not icao:
        M["src"]["погода_пропуск"] = f"станция не найдена: {city}"
        return {}
    obs, ll = metar(icao)
    if not ll:
        return {}
    res = wx_model(kind, day, obs, forecast(*ll), now, hash((ev.get("id"), int(now // 3600))) & 0xFFFFFFFF)
    if not res:
        return {}
    samples, txt, strong = res
    out = {}
    for x in ms:
        b = wx_bucket(x["g"]) if x["yn"] else None
        if not b:
            continue
        lo, hi, unit = b
        conv = [math.floor((v * 9 / 5 + 32 if unit == "F" else v) + 0.5) for v in samples]
        q = sum(1 for v in conv if lo <= v <= hi) / len(conv)
        out[x["id"]] = (min(0.995, max(0.005, q)), f"{icao} ({city}): {txt} → «{x['g']}» {q:.0%}", strong)
    return out

# ---------- источник: ИИ ----------
def ai_estimate(x, now, force=False):
    """Claude с поиском в интернете: вероятность исхода A по свежим новостям. Кэш 3 ч, дневной лимит."""
    if not AI_KEY:
        return None
    c = S["aic"].get(x["id"])
    if c and now - c["t"] < 3 * 3600:
        return c
    d8 = day8(now)
    if S["ai"][0] != d8:
        S["ai"] = [d8, 0]
    if S["ai"][1] >= AI_DAY and not force:
        return None
    fail = S.setdefault("aif", {})
    if now - fail.get(x["id"], 0) < 3600:
        return None                                           # недавно не ответил — час не тратим деньги на этот рынок
    S["ai"][1] += 1
    prompt = (f"You are a careful, calibrated forecaster. Current time (UTC): {iso(now)}.\n"
              f"Prediction market question: \"{x['q']}\"\nEvent: \"{x['title']}\"\n"
              f"Outcome A: \"{x['outs'][0]}\". Outcome B: \"{x['outs'][1]}\".\n"
              f"Trading closes {iso(x['end'])}; the result should be known by about {iso(x['res'])}.\n"
              f"Resolution rules (excerpt): {x['desc'][:800]}\n\n"
              "Search the web for the most recent relevant information (news of the last 48 hours, official sources, "
              "injuries and line-ups, schedules, forecasts, statements). Then estimate the probability that Outcome A "
              "happens under these rules. Do not try to guess the market price. If information is insufficient, say so.\n"
              "Finish with ONE line of JSON and nothing after it: {\"p_a\": <number 0..1 or null>, \"confidence\": "
              "\"low\"|\"medium\"|\"high\", \"facts\": \"<2-3 key facts with dates, in Russian>\", \"news_risk\": "
              "<true if very recent news could still change the outcome, else false>}")
    # размышления модели входят в max_tokens — запас 8000; у claude-sonnet-5-5 размышление «между поисками»
    body = {"model": AI_MODEL, "max_tokens": 8000, "messages": [{"role": "user", "content": prompt}],
            "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 4}]}
    if AI_MODEL.startswith("claude-sonnet-5-5"):
        body["thinking"] = {"type": "between_tools"}
    try:
        r = requests.post("https://api.anthropic.com/v1/messages", json=body, timeout=240,
                          headers={"x-api-key": AI_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        r.raise_for_status()
        d = r.json()
        txt = "\n".join(b.get("text", "") for b in d.get("content") or [] if b.get("type") == "text")
        js = re.findall(r"\{[^{}]*\"p_a\"[^{}]*\}", txt)
        v = json.loads(js[-1]) if js else None
    except Exception as e:
        src_state("ИИ", f"ошибка {type(e).__name__} {getattr(getattr(e, 'response', None), 'status_code', '')}")
        fail[x["id"]] = int(now)
        return None
    if d.get("stop_reason") != "end_turn" or not isinstance(v, dict):
        src_state("ИИ", f"ответ оборван ({d.get('stop_reason')}), {S['ai'][1]}/{AI_DAY} вызовов сегодня")
        fail[x["id"]] = int(now)
        if len(fail) > 200:
            S["aif"] = dict(sorted(fail.items(), key=lambda kv: kv[1])[-100:])
        return None                                           # не кэшируем ответ, через час можно спросить заново
    p = fnum(v.get("p_a"))
    if p is not None and not 0 <= p <= 1:
        p = None                                              # «65» вместо 0,65 — не гадаем
    res = {"t": int(now), "p": min(0.99, max(0.01, p)) if p is not None else None,
           "conf": str(v.get("confidence") or "low").lower(), "facts": str(v.get("facts") or "")[:300],
           "risk": bool(v.get("news_risk"))}
    S["aic"][x["id"]] = res
    if len(S["aic"]) > 300:
        S["aic"] = dict(sorted(S["aic"].items(), key=lambda kv: kv[1]["t"])[-200:])
    src_state("ИИ", f"{S['ai'][1]}/{AI_DAY} вызовов сегодня, модель {AI_MODEL}")
    return res

# ---------- калибр из тени ----------
def pbin(p):
    for i, hi in enumerate((0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.01)):
        if p < hi:
            return i
    return 9

def calib_build():
    """(категория, корзина цены) → (n, средняя ошибка цены «y − цена», t-статистика) по рассчитанной тени."""
    acc = {}
    for r in SH["shd"]:
        if r[10] != 0:
            continue                                          # только первый снимок рынка — без двойного счёта
        p = min(0.99, max(0.01, r[0] + r[8]))                 # справедливая цена группы (как в cal_layer)
        for key in ((r[3], pbin(p)), (-1, pbin(p))):
            a = acc.setdefault(key, [0, 0.0, 0.0])
            e = r[11] - p
            a[0] += 1; a[1] += e; a[2] += e * e
    out = {}
    for k, (n, s, s2) in acc.items():
        if n >= 30:
            mu = s / n
            sd = math.sqrt(max(1e-9, s2 / n - mu * mu))
            out[k] = (n, mu, mu / (sd / math.sqrt(n)))
    M["cal"] = out

def cal_layer(x, p0):
    c = M["cal"].get((CATS.index(x["cat"]), pbin(p0)))
    if not c or c[0] < 400 or abs(c[2]) < 4:                  # исходы внутри события связаны — порог строже обычного
        return None
    n, mu, t = c
    q = min(0.98, max(0.02, p0 + mu * n / (n + 600)))
    return ("cal", q, f"тень: в «{CAT_RU[x['cat']]}» при цене {pbin(p0) / 10:.1f}–{pbin(p0) / 10 + 0.1:.1f} исход сбывается "
                      f"на {mu:+.3f} {'чаще' if mu > 0 else 'реже'} цены ({n} рынков, t={t:.1f})", False)

# ---------- решение ----------
def combine(p0, layers):
    ws = [(lg(q) - lg(p0), W.get(k, 0.3)) for k, q, *_ in layers]
    tot = sum(w for _, w in ws)
    k = 0.9 / tot if tot > 0.9 else 1.0
    return sg(lg(p0) + sum(d * w * k for d, w in ws))

def decide(x, layers):
    """Сторона с наибольшим перевесом и проверки ②–④ (без стакана)."""
    p0 = x["p0"] if x["p0"] is not None else x["mid"]
    q = combine(p0, layers)
    best = None
    for i in (0, 1):
        price = x["ask"] if i == 0 else 1 - x["bid"]
        cost = price + fee(price, x["rate"], x["ex"])
        qs = q if i == 0 else 1 - q
        row = {"i": i, "price": price, "cost": cost, "q": qs, "edge": qs - cost, "roi": qs / cost - 1 if cost else 0}
        if best is None or row["edge"] > best["edge"]:
            best = row
    agree = [l for l in layers if (l[1] if best["i"] == 0 else 1 - l[1]) >= best["cost"]]
    against = [l for l in layers if l not in agree]
    strong = any(l[3] for l in layers if len(l) > 3 and l in agree) or len(agree) >= 2
    best.update(p0=p0, layers=layers, agree=agree, against=against, strong=strong)
    return best

def gate(x, r):
    """Ступени пирамиды по порядку; пустая строка — пройдены все (кроме стакана и ИИ — они в fire)."""
    wx = x["cat"] == "weather"
    if x["liq"] < (LIQ_WX if wx else LIQ): return "ликвидность"
    if x["spr"] > (SPR_WX if wx else SPR): return "спред"
    if abs(x["d1h"]) >= MOVE: return "скачок за час"
    if not PMIN <= r["price"] <= PMAX: return "цена"
    if not r["layers"]: return "нет источника"
    if r["edge"] < EDGE or r["roi"] < ROI: return "перевес мал"
    if r["against"]: return "источники спорят"
    if not r["strong"]: return "слабое основание"
    return ""

# ---------- нестыковки ----------
def raw_quotes(m):
    toks = jl(m.get("clobTokenIds"))
    if len(toks) != 2 or [str(o).lower() for o in jl(m.get("outcomes"))] != ["yes", "no"]:
        return None
    return {"id": str(m.get("id")), "g": m.get("groupItemTitle") or m.get("question") or "",
            "ask": fnum(m.get("bestAsk")), "bid": fnum(m.get("bestBid")), "tY": str(toks[0]), "tN": str(toks[1])}

def fill_shares(tok, n):
    """Средняя цена покупки n долей по asks или None, если глубины не хватает."""
    b = get(CLOB + "/book", timeout=15, token_id=tok)
    asks = sorted((fnum(a.get("price"), 0), fnum(a.get("size"), 0)) for a in b.get("asks") or [])
    got = spent = 0.0
    for p, q in asks:
        if p <= 0 or q <= 0:
            continue
        v = min(n - got, q)
        got += v
        spent += v * p
        if got >= n - 1e-9:
            return spent / got
    return None

def arbs(ev, raw, cat, now):
    """Взаимоисключающие исходы (negRisk): все «ДА» дешевле $1 или все «НЕТ» дешевле выплаты n − 1."""
    if not ev.get("negRisk") or ev.get("negRiskAugmented") or not 2 <= len(raw) <= 25 \
            or len(raw) != len(ev.get("markets") or []):
        return                                                # закрытый исход мог уже выиграть — «гарантии» нет
    qs = [raw_quotes(m) for m in raw]
    if any(q is None for q in qs) or any(str(m.get("umaResolutionStatus") or "").lower() in ("proposed", "resolved")
                                         for m in raw):
        return
    m0 = raw[0]
    fs = m0.get("feeSchedule") if isinstance(m0.get("feeSchedule"), dict) else {}
    rate = fnum(fs.get("rate"), FEE_CAT.get(cat, 0.05)) if m0.get("feesEnabled") is not False else 0.0
    ex = fnum(fs.get("exponent"), 1.0) or 1.0
    title, slug = ev.get("title") or "", ev.get("slug") or ""
    if all(q["ask"] for q in qs):
        yes = sum(q["ask"] + fee(q["ask"], rate, ex) for q in qs)
        if 1 - yes >= ARB:
            check_arb(f"yes:{ev.get('id')}", title, slug, qs, "tY", 1.0, rate, ex, now, "купить «ДА» на все исходы")
    if all(q["bid"] is not None for q in qs):
        no = sum((1 - q["bid"]) + fee(1 - q["bid"], rate, ex) for q in qs)
        if (len(qs) - 1) - no >= ARB:
            check_arb(f"no:{ev.get('id')}", title, slug, qs, "tN", len(qs) - 1.0, rate, ex, now,
                      "купить «НЕТ» на все исходы")

def check_arb(key, title, slug, qs, tk, payout, rate, ex, now, what):
    if S["seen"].get(key, 0) > now - 12 * 3600 or not day_ok(now):
        return
    cost, legs = 0.0, []
    for q in qs:                                              # каждая нога — 10 долей по настоящему стакану
        try:
            p = fill_shares(q[tk], 10.0)
        except Exception:
            return
        if p is None:
            return
        cost += p + fee(p, rate, ex)
        legs.append(f"{q['g'][:28]} {p:.3f}")
    net = payout - cost
    if net < ARB:
        return
    if net > 0.12:                                            # «гарантия» больше 12% — почти наверняка какого-то исхода
        note(f"нестыковка пропущена как подозрительная (+{net:.2f}): {title[:60]}", now)   # нет в списке или цена устарела
        S["seen"][key] = int(now)
        return
    S["seen"][key] = int(now)
    S["arb_n"] += 1
    send(f"🧩 Polymarket · НЕСТЫКОВКА (без прогноза): «{title}»\n{what} ({len(qs)} ног). Комплект — по 1 доле каждой "
         f"ноги — стоит {cost:.3f} с комиссией, а выплата гарантирована {payout:.0f} → ≈ +{net:.3f} с комплекта "
         f"(проверено по стакану на 10 комплектов).\n"
         f"Ноги (цена доли): {'; '.join(legs)}\nПокупать все ноги сразу и одинаковым числом долей, глубина по лучшим "
         f"ценам мала. Проверьте правила рынка.\nhttps://polymarket.com/event/{slug}", now)

# ---------- алерты и бумага ----------
def day_ok(now):
    d = day8(now)
    if S["day"][0] != d:
        S["day"] = [d, 0]
    return S["day"][1] < DAY_MAX

def send(text, now):
    S["day"][1] += 1
    note(text[:180], now)
    if TG:
        mm.tg(text)

def fire(x, r, now, kind="СТАВКА", need_ai=False):
    """Стакан → ИИ (вето или подтверждение) → алерт и бумажная сделка. need_ai — основание слабое, ИИ должен согласиться."""
    tok = x["toks"][r["i"]]
    lim = min(0.99, math.floor((r["q"] - EDGE - fee(r["q"], x["rate"], x["ex"])) * 1000) / 1000)
    try:
        f = book_fill(tok, USD, limit=lim)
    except Exception as e:
        M["veto"] = (M["veto"] + [f"{label(x)[:40]}: стакан недоступен ({str(e)[:40]})"])[-20:]
        return False
    if not f or f[0] < MIN_FILL:
        M["veto"] = (M["veto"] + [f"{label(x)[:40]}: в стакане мало объёма до {lim:.3f}"])[-20:]
        return False
    spent, shares, avg, top = f
    cost = avg + fee(avg, x["rate"], x["ex"])
    edge, roi = r["q"] - cost, r["q"] / cost - 1
    if edge < EDGE or roi < ROI:
        return False
    ai, ai_line = None, "⑤ ИИ-проверка: ключ XEV_AI_KEY не задан"
    if kind != "СТАВКА":
        ai_line = "⑤ ИИ здесь — единственный источник (уверенность high); точность этого типа считается отдельно"
    elif AI_KEY:
        ai = ai_estimate(x, now)
        if ai and ai["p"] is not None:
            qa = ai["p"] if r["i"] == 0 else 1 - ai["p"]
            sure = ai["conf"] in ("medium", "high")
            if sure and (qa < cost - 0.02 or ai["risk"]):
                M["veto"] = (M["veto"] + [f"{label(x)[:40]}: вето ИИ ({qa:.0%}, {ai['facts'][:60]})"])[-20:]
                note(f"вето ИИ: {label(x)[:60]} — {ai['facts'][:80]}", now)
                S["seen"][x["id"]] = int(now)
                return False
            if need_ai and not (sure and qa >= cost):
                M["veto"] = (M["veto"] + [f"{label(x)[:40]}: ИИ не подтвердил ({qa:.0%}, {ai['conf']})"])[-20:]
                S["seen"][x["id"]] = int(now)
                return False
            ai_line = f"⑤ ИИ-проверка новостей: {qa:.0%} ({ai['conf']}) — {ai['facts'][:170]}"
        elif need_ai:
            return False
        else:
            ai_line = "⑤ ИИ-проверка: не ответил или лимит на сегодня исчерпан"
    elif need_ai:
        return False
    agree = r["agree"]
    pos = {"id": x["id"], "kind": kind, "t": int(now), "title": x["title"][:120], "name": label(x)[:100], "slug": x["slug"],
           "cat": x["cat"], "i": r["i"], "side": side_name(x, r["i"]), "avg": round(avg, 4), "cost_px": round(cost, 4),
           "q": round(r["q"], 4), "edge": round(edge, 4), "usd": round(spent * cost / avg, 2), "shares": round(shares, 4),
           "res": int(x["res"]), "p0": round(r["p0"], 4),
           "why": sorted({l[0].split("_")[0] for l in agree} | ({"ai"} if ai and ai.get("p") is not None else set()))}
    with LOCK:
        S["pos"][x["id"]] = pos
        S["seen"][x["id"]] = int(now)
    when = f"начало {lt(x['gs'])}" if x["gs"] else f"конец рынка {lt(x['end'])}"
    srcs = "\n".join(f"   • {NAMES.get(l[0].split('_')[0], l[0])}: {l[2][:200]}" for l in agree)
    n_src = len(agree) + (1 if need_ai else 0)
    send(f"🎯 Polymarket · {kind} (расчёт ≈ {lt(x['res'])} по Иркутску, через {(x['res'] - now) / 3600:.1f} ч)\n"
         f"«{x['title'][:90]}» — {label(x)}\n"
         f"👉 Покупать: {side_name(x, r['i'])} — не дороже {lim:.3f}\n"
         f"Наша вероятность {r['q']:.0%} (цена рынка {r['p0']:.0%}) · по стакану {avg:.3f}, с комиссией {cost:.3f} · "
         f"перевес +{edge:.2f} ({roi:+.0%} на $1)\n"
         f"Пирамида:\n① данные: ликвидность ${x['liq']:,.0f}, спред {x['spr']:.2f}, {when} (Иркутск), "
         f"категория {CAT_RU.get(x['cat'], x['cat'])}\n② независимые оценки:\n{srcs}\n"
         f"③ согласие: {n_src} из {n_src} источников за эту сторону\n"
         f"④ перевес по стакану на ${pos['usd']:.0f}: +{edge:.2f}\n{ai_line}\n"
         f"Бумажная сделка ${pos['usd']:.0f} записана. Сверьте цену на Pulse. Расчёт модели — не гарантия.\n"
         f"https://polymarket.com/event/{x['slug']}", now)
    return True

def closed_outcomes(ids):
    """id → доля выплаты исходу A (1, 0 или 0,5) для рассчитанных рынков."""
    out = {}
    for i in range(0, len(ids), 40):
        chunk = ids[i:i + 40]
        try:
            r = SES.get(GAMMA + "/markets", params=[("id", x) for x in chunk] + [("closed", "true"), ("limit", "100")],
                        timeout=30)
            r.raise_for_status()
            rows = r.json()
        except Exception as e:
            log.warning("xevent closed: %s", e)
            continue
        for m in rows or []:
            op = [fnum(v) for v in jl(m.get("outcomePrices"))]
            if not m.get("closed") or len(op) != 2 or None in op:
                continue
            if op[0] >= 0.99:
                out[str(m.get("id"))] = 1.0
            elif op[0] <= 0.01:
                out[str(m.get("id"))] = 0.0
            elif abs(op[0] - 0.5) < 0.01:
                out[str(m.get("id"))] = 0.5
    return out

def settle(now):
    due = [k for k, p in S["pos"].items() if now >= p["res"] + 1800]
    sdue = [k for k, v in SH["sh"].items() if now >= v[10] + 1800]
    ldue = [k for k, v in SH["shl"].items() if now >= v[10] + 1800]
    sdue, ldue = random.sample(sdue, min(400, len(sdue))), random.sample(ldue, min(200, len(ldue)))
    got = closed_outcomes(sorted(set(due + sdue + ldue))) if (due or sdue or ldue) else {}
    for k in due:
        p = S["pos"][k]
        if k not in got:
            if now > p["res"] + 4 * 86400:
                with LOCK:
                    S["pos"].pop(k, None)
                note(f"не рассчитан за 4 дня, снят: {p['name'][:60]}", now)
            continue
        y = got[k] if p["i"] == 0 else 1 - got[k]
        pnl = p["shares"] * y - p["usd"]
        with LOCK:
            S["pos"].pop(k, None)
            S["done"] = (S["done"] + [dict(p, t1=int(now), y=y, pnl=round(pnl, 2))])[-600:]
        note(f"{'✅' if y >= 1 else '❌' if y <= 0 else '➖'} {p['name'][:50]} {p['side']}: {pnl:+.2f}$", now)
        if TG:
            mm.tg(f"🎯 Polymarket (бумага): «{p['name'][:80]}» — {p['side']} "
                  f"{'выиграла' if y >= 1 else 'проиграла' if y <= 0 else 'возврат половины'}, {pnl:+.2f}$ на ${p['usd']:.0f}")
    for store, kind in ((SH["sh"], 0), (SH["shl"], 1)):
        for k in [k for k in list(store) if k in got]:
            v = store.pop(k)
            SH["shd"].append(v[:10] + [kind, got[k]])
        for k in [k for k, v in list(store.items()) if now > v[10] + 5 * 86400]:
            store.pop(k, None)
    SH["shd"] = SH["shd"][-10000:]
    S["seen"] = {k: v for k, v in S["seen"].items() if v > now - 3 * 86400}

def shadow(x, ext, now):
    """Снимок рынка для поиска зависимостей: [цена, y-заглушка, часы, кат, спред, ликв, ход 1ч, ход 1д, перекос группы,
    внешняя оценка, время расчёта] → после расчёта [..10 полей.., ранний/поздний, исход]."""
    if x["liq"] < 200 or not 0.02 <= x["mid"] <= 0.98:
        return
    hrs = (x["res"] - now) / 3600
    row = [round(x["mid"], 4), 0, round(hrs, 2), CATS.index(x["cat"]), round(x["spr"], 3), round(x["liq"]),
           round(x["d1h"], 3), round(x["d1d"], 3), round((x["p0"] or x["mid"]) - x["mid"], 4),
           round(ext, 4) if ext is not None else -1, int(x["res"])]
    if x["id"] not in SH["sh"] and len(SH["sh"]) < SH_MAX:
        SH["sh"][x["id"]] = row
    elif hrs <= 2 and x["id"] not in SH["shl"] and len(SH["shl"]) < SH_MAX // 2:
        SH["shl"][x["id"]] = row

# ---------- проход ----------
def step(now):
    settle(now)
    M.update(n_ev=0, n_cr=0, n_mk=0, by_cat={}, veto=M["veto"][-10:])
    rej, cand, ms, wx_ev = {}, [], [], []
    for page in pages(now):
        for ev in page:
            M["n_ev"] += 1
            try:
                if is_crypto(ev):
                    M["n_cr"] += 1
                    continue
                if ev.get("live") or ev.get("ended"):
                    rej["матч идёт"] = rej.get("матч идёт", 0) + 1
                    continue
                cat = category(ev)
                raw = [m for m in ev.get("markets") or [] if not m.get("closed")]
                xs = [x for x in (norm(m, ev, cat, now) for m in raw) if x]
                if not xs:
                    continue                                  # вне окна 10 ч, матч идёт или исход уже решён
                arbs(ev, raw, cat, now)
                if ev.get("negRisk") and len(xs) == len(raw) >= 2 and all(x["yn"] for x in xs):
                    s = sum(x["mid"] for x in xs)
                    for x in xs:
                        x["p0"] = x["mid"] / s if s > 0 else x["mid"]
                for x in xs:
                    x["sport"] = sport_of(ev) if cat in ("sports", "esports") else ""
                    x["tags"] = tag_keys(ev)
                if cat == "weather" and WX_ON and WX_RE.search(ev.get("title") or ""):
                    wx_ev.append((ev, xs))
                ms += xs
                M["by_cat"][cat] = M["by_cat"].get(cat, 0) + len(xs)
            except Exception as e:
                log.warning("xevent %s: %s", ev.get("slug"), e)
                M["err"] = f"{ev.get('slug')}: {str(e)[:90]}"
    M["n_mk"] = len(ms)
    games = []                                                # букмекеры — только лиги, что есть в окне
    if ESPN_ON or ODDS_KEY:
        lgs, keys = book_needs(ms)
        g, n_ok, n_bad = espn_games(lgs, now) if lgs else ([], 0, 0)
        games += g
        try:
            games += odds_games(keys, now)
        except Exception as e:
            src_state("pinnacle", f"ошибка {str(e)[:60]}")
        src_state("букмекеры", f"игр с коэффициентами {len(games)} (лиг ESPN {len(lgs)}: ответов {n_ok}, ошибок {n_bad})"
                  + (f", Pinnacle: лиг {len(keys)}, {S['odds'][1] if S['odds'][0] == day8(now) else 0}/{ODDS_DAY} "
                     f"запросов сегодня" if ODDS_KEY else ", Pinnacle: ключ XEV_ODDS_KEY не задан"))
    wx = {}
    n_st = 0
    for ev, xs in wx_ev:
        try:
            r = wx_layers(ev, xs, now)
            if r:
                n_st += 1
                wx.update(r)
        except Exception as e:
            src_state("погода", f"ошибка {ev.get('slug')}: {str(e)[:60]}")
    if wx_ev:
        src_state("погода", f"событий {len(wx_ev)}, посчитано {n_st}, рынков {len(wx)}")
    calib_build()
    matched = 0
    solo = []
    for x in ms:
        layers = []
        if x["cat"] == "sports" and games:
            for src, q, txt in sports_layers(x, games):
                layers.append((src, q, txt, src == "pinnacle"))
        if x["id"] in wx:
            q, txt, strong = wx[x["id"]]
            layers.append(("wx" if strong else "wx_weak", q, txt, strong))
        c = cal_layer(x, x["p0"] if x["p0"] is not None else x["mid"])
        if c:
            layers.append(c)
        ext = None
        if layers:
            matched += 1
            ext = combine(x["p0"] if x["p0"] is not None else x["mid"], layers)
        shadow(x, ext, now)
        if not layers:
            rej["нет источника"] = rej.get("нет источника", 0) + 1
            if (AI_SOLO and AI_KEY and x["cat"] not in ("sports", "esports", "weather") and x["liq"] >= max(LIQ, 5000)
                    and 0.15 <= x["mid"] <= 0.85 and x["spr"] <= SPR and abs(x["d1h"]) < MOVE):
                solo.append(x)
            continue
        r = decide(x, layers)
        why = gate(x, r)
        rej[why or "ок"] = rej.get(why or "ок", 0) + 1
        cand.append(dict(id=x["id"], title=x["title"][:80], name=label(x)[:80], cat=x["cat"], side=side_name(x, r["i"]),
                         price=round(r["price"], 3), q=round(r["q"], 3), edge=round(r["edge"], 3), gate=why,
                         res=lt(x["res"]), src="+".join(l[0] for l in layers), slug=x["slug"]))
        same_ev = sum(1 for p in S["pos"].values() if p["slug"] == x["slug"])   # по одному событию — не больше 2 ставок
        fresh = x["id"] not in S["pos"] and S["seen"].get(x["id"], 0) < now - 86400 and day_ok(now) and same_ev < 2
        weak = why == "слабое основание" and AI_KEY and r["edge"] >= EDGE + 0.02 and r["roi"] >= ROI \
            and gate(x, dict(r, strong=True)) == ""             # всё прошло, кроме силы: второй голос — ИИ
        if fresh and (not why or weak):
            try:
                fire(x, r, now, need_ai=bool(why))
            except Exception as e:
                log.warning("xevent fire %s: %s", x["id"], e)
    src_state("сопоставлено", f"рынков с внешней оценкой {matched} из {len(ms)}")
    ai_today = S["ai"][1] if S["ai"][0] == day8(now) else 0
    if solo and ai_today < AI_DAY // 2:                        # «ИИ-ставка» тратит не больше половины дневного лимита
        x = max((s for s in solo if s["id"] not in S["aic"]), key=lambda s: s["liq"], default=None)
        if x and day_ok(now):
            ai = ai_estimate(x, now)
            if ai and ai["p"] is not None and ai["conf"] == "high":
                r = decide(x, [("ai_high", ai["p"], f"{ai['facts'][:200]}", False)])
                if (r["edge"] >= max(EDGE, 0.10) and r["roi"] >= 0.15 and PMIN <= r["price"] <= PMAX
                        and x["id"] not in S["pos"] and S["seen"].get(x["id"], 0) < now - 86400):
                    r["agree"] = r["layers"]
                    try:
                        fire(x, r, now, kind="ИИ-СТАВКА")
                    except Exception as e:
                        log.warning("xevent ai fire %s: %s", x["id"], e)
    M["cand"] = sorted(cand, key=lambda c: -c["edge"])[:30]
    M["rej"] = rej
    daily(now)

def daily(now):
    if not (DAILY and TG):
        return
    d8 = day8(now)
    if S["daily"] == d8 or int(time.strftime("%H", time.gmtime(now + TZ8))) < DAILY_HOUR:
        return
    S["daily"] = d8
    mm.tg(text(short=True))

# ---------- отчёт ----------
def stats(rows):
    n = len(rows)
    w = sum(1 for d in rows if d["y"] >= 1)
    pnl, usd = sum(d["pnl"] for d in rows), sum(d["usd"] for d in rows)
    return n, w, pnl, usd

def text(short=False):
    with LOCK:
        done, pos = list(S["done"]), dict(S["pos"])
    n, w, pnl, usd = stats(done)
    ago = (time.time() - M["last"]) / 60 if M["last"] else None
    L = [f"🎯 {VERSION} — Polymarket: всё, кроме крипто, с расчётом ≤ {H_MAX:g} ч. {lt()}",
         f"Проход {f'{ago:.0f} мин назад' if ago is not None else '— ещё не было'} ({M['dur']:.0f} с): событий {M['n_ev']} "
         f"(крипто отсеяно {M['n_cr']}), рынков в окне {M['n_mk']}: "
         + (", ".join(f"{CAT_RU.get(k, k)} {v}" for k, v in sorted(M["by_cat"].items(), key=lambda kv: -kv[1])) or "—"),
         "Источники: " + (" · ".join(f"{k}: {v}" for k, v in M["src"].items()) or "—")
         + ("" if AI_KEY else " · ИИ: ключ XEV_AI_KEY не задан"),
         f"СТАВКИ (бумага ${USD:.0f}): закрыто {n}, выиграно {w} ({(w / n * 100) if n else 0:.0f}%), итог {pnl:+.2f}$ на ${usd:.0f} "
         f"({(pnl / usd * 100) if usd else 0:+.1f}%), открыто {len(pos)} · НЕСТЫКОВОК {S['arb_n']} · алертов сегодня "
         f"{S['day'][1] if S['day'][0] == day8(time.time()) else 0}/{DAY_MAX}"]
    by = {}
    for d in done:
        for k in [d.get("kind", "СТАВКА")] + [f"источник {s}" for s in d.get("why") or []] + [f"кат. {CAT_RU.get(d.get('cat'), '?')}"]:
            by.setdefault(k, []).append(d)
    for k, rows in sorted(by.items()):
        a, b, c, u = stats(rows)
        L.append(f"  {k}: {a} шт., выиграно {b}, {c:+.2f}$ ({(c / u * 100) if u else 0:+.0f}%)")
    for p in sorted(pos.values(), key=lambda p: p["res"])[:8]:
        L.append(f"  открыта: {p['name'][:44]} — {p['side']} по {p['avg']:.3f}, оценка {p['q']:.0%}, расчёт {lt(p['res'])}")
    if not short:
        L.append("Отсев: " + (", ".join(f"{k} {v}" for k, v in sorted(M["rej"].items(), key=lambda kv: -kv[1])[:8]) or "—"))
        L.append("Лучшие расхождения с внешними оценками сейчас (даже если отсеяны):")
        for c in M["cand"][:8]:
            L.append(f"  {c['name'][:42]} — {c['side']} {c['price']:.2f}, оценка {c['q']:.0%}, перевес {c['edge']:+.2f} "
                     f"[{c['src']}] {'ОК' if not c['gate'] else 'отсев: ' + c['gate']}")
        if M["veto"]:
            L.append("Не прошли в последний момент: " + "; ".join(M["veto"][-4:]))
    L.append(f"Тень: ждут расчёта {len(SH['sh']) + len(SH['shl'])}, рассчитано {len(SH['shd'])} — зависимости: /ev/deps")
    top = deps_rows()[:3]
    for lab, nn, mu, t in top:
        L.append(f"  {lab}: {nn} рынков, исход сбывается на {mu:+.3f} {'чаще' if mu > 0 else 'реже'} цены (t={t:.1f})")
    if M["err"]:
        L.append("⚠️ " + M["err"])
    return "\n".join(L)

FEATS = [("фаворит (цена ≥ 0,70)", lambda r: r[0] >= 0.70), ("аутсайдер (цена ≤ 0,25)", lambda r: r[0] <= 0.25),
         ("середина 0,40–0,60", lambda r: 0.40 <= r[0] <= 0.60), ("за час выросла на 0,05+", lambda r: r[6] >= 0.05),
         ("за час упала на 0,05+", lambda r: r[6] <= -0.05), ("за сутки выросла на 0,10+", lambda r: r[7] >= 0.10),
         ("за сутки упала на 0,10+", lambda r: r[7] <= -0.10), ("спред ≥ 0,04", lambda r: r[4] >= 0.04),
         ("ликвидность < $2k", lambda r: r[5] < 2000), ("ликвидность ≥ $50k", lambda r: r[5] >= 50000),
         ("до расчёта < 2 ч", lambda r: r[2] < 2), ("до расчёта 6–10 ч", lambda r: 6 <= r[2] <= 10.5),
         ("в группе дороже справедливой (сумма > 1)", lambda r: r[8] <= -0.01),
         ("в группе дешевле справедливой", lambda r: r[8] >= 0.01),
         ("внешний источник выше цены на 0,03+", lambda r: r[9] >= 0 and r[9] - r[0] >= 0.03),
         ("внешний источник ниже цены на 0,03+", lambda r: r[9] >= 0 and r[0] - r[9] >= 0.03)]

def deps_rows():
    early = [r for r in SH["shd"] if r[10] == 0]
    late = [r for r in SH["shd"] if r[10] == 1]
    out = []
    groups = [(lab, f) for lab, f in FEATS] + [(f"кат. {CAT_RU[c]}", (lambda ci: lambda r: r[3] == ci)(i))
                                               for i, c in enumerate(CATS)]
    for lab, f in groups:
        sel = [r for r in (late if lab == "до расчёта < 2 ч" else early) if f(r)]
        if len(sel) < 30:
            continue
        es = [r[11] - r[0] for r in sel]
        mu = sum(es) / len(es)
        sd = math.sqrt(max(1e-9, sum((e - mu) ** 2 for e in es) / len(es)))
        out.append((lab, len(sel), mu, mu / (sd / math.sqrt(len(sel)))))
    return sorted(out, key=lambda r: -abs(r[3]))

def deps_text():
    rows = SH["shd"]
    L = [f"🔎 {VERSION} — зависимости по тени: рассчитано {len(rows)} снимков рынков (ранние + за 2 ч до расчёта).",
         "«Сбывается чаще цены на +0,05» = покупка «ДА»/исхода A по середине стакана давала +5 центов на долю до комиссии.",
         "Значимо — когда |t| ≥ 3 (случайность маловероятна). Поправка «калибр» включается сама при ≥ 400 рынках и |t| ≥ 4;",
         "одна она алерт не даёт — только вместе с другим источником.",
         "", "Калибровка по цене (все категории):"]
    for b in range(10):
        c = M["cal"].get((-1, b))
        if c:
            L.append(f"  цена {b / 10:.1f}–{b / 10 + 0.1:.1f}: {c[0]} рынков, исход сбывается на {c[1]:+.3f} к цене (t={c[2]:.1f})")
    L.append("")
    L.append("Признаки (по силе, |t|):")
    for lab, n, mu, t in deps_rows()[:20]:
        L.append(f"  {lab}: {n} рынков, {mu:+.3f} (t={t:.1f}){' ← значимо' if abs(t) >= 3 else ''}")
    ex = [r for r in rows if r[9] >= 0]
    if len(ex) >= 30:
        bm = sum((r[11] - r[0]) ** 2 for r in ex) / len(ex)
        bx = sum((r[11] - r[9]) ** 2 for r in ex) / len(ex)
        L += ["", f"Внешние источники против рынка ({len(ex)} рынков, оценка Брайера — меньше лучше): рынок {bm:.4f}, "
                  f"наша оценка {bx:.4f} → {'наша точнее' if bx < bm else 'рынок точнее'}"]
    if len(rows) < 30:
        L.append("Пока мало данных: нужно хотя бы 30 рассчитанных рынков в группе — это 1–2 дня работы.")
    return "\n".join(L)

def health_line():
    with LOCK:
        done, pos = list(S["done"]), len(S["pos"])
    n, w, pnl, _ = stats(done)
    ago = (time.time() - M["last"]) / 60 if M["last"] else None
    return (f"{VERSION}: проход {f'{ago:.0f} мин назад' if ago is not None else 'ещё не было'}, рынков в окне {M['n_mk']}, "
            f"ставок открыто {pos}, закрыто {n} ({w} в плюс, {pnl:+.2f}$), тень {len(SH['shd'])}"
            + (f" | ⚠️ {M['err'][:60]}" if M["err"] else ""))

# ---------- разбор одного события ----------
def ask(q, use_ai=False):
    m = re.search(r"polymarket\.com/(?:[a-z]{2}/)?event/([^/?#\s]+)", q or "")
    slug = m.group(1) if m else (q or "").strip().strip("/").split("/")[-1]
    d = get(GAMMA + "/events", slug=slug)
    if not d:
        return f"Событие «{slug}» не найдено"
    ev, now = d[0], time.time()
    cat = category(ev)
    raw = [mk for mk in ev.get("markets") or [] if not mk.get("closed")]
    xs = [x for x in (norm(mk, ev, cat, now, window=False) for mk in raw) if x]
    if ev.get("negRisk") and len(xs) == len(raw) >= 2 and all(x["yn"] for x in xs):
        s = sum(x["mid"] for x in xs)
        for x in xs:
            x["p0"] = x["mid"] / s
    for x in xs:
        x["sport"] = sport_of(ev) if cat in ("sports", "esports") else ""
        x["tags"] = tag_keys(ev)
    games = []
    if cat == "sports" and xs:
        lgs, keys = book_needs(xs)
        games += espn_games(lgs, now)[0] + odds_games(keys, now)
    wx = wx_layers(ev, xs, now) if cat == "weather" else {}
    L = [f"🔍 «{ev.get('title')}» — {CAT_RU.get(cat, cat)}, рынков с котировками {len(xs)} из {len(raw)}"]
    ai_ids = {x["id"] for x in sorted(xs, key=lambda x: -x["liq"])[:3]} if use_ai else set()
    for x in xs[:25]:
        layers = [(s, q_, t, s == "pinnacle") for s, q_, t in sports_layers(x, games)] if games else []
        if x["id"] in wx:
            q_, t, st = wx[x["id"]]
            layers.append(("wx" if st else "wx_weak", q_, t, st))
        if x["id"] in ai_ids:
            ai = ai_estimate(x, now)                          # дневной лимит XEV_AI_DAY соблюдается
            if ai and ai["p"] is not None and ai["conf"] in ("medium", "high"):
                layers.append((f"ai_{ai['conf']}", ai["p"], ai["facts"][:200], False))
        hrs = (x["res"] - now) / 3600
        head = f"• {label(x)[:70]} — цена {x['bid']:.2f}/{x['ask']:.2f}, расчёт через {hrs:.1f} ч"
        if not layers:
            L.append(head + " — внешней оценки нет")
            continue
        r = decide(x, layers)
        why = gate(x, r)
        L.append(head + f"\n  → {side_name(x, r['i'])}: оценка {r['q']:.0%}, цена с комиссией {r['cost']:.3f}, "
                        f"перевес {r['edge']:+.2f} — {'проходит все ступени' if not why else 'не проходит: ' + why}")
        for l in layers:
            L.append(f"    {NAMES.get(l[0].split('_')[0], l[0])}: {l[2][:180]}")
    return "\n".join(L)

# ---------- база и цикл ----------
def load():
    r = xpaper.q("SELECT id, js FROM paper_xarb WHERE id IN ('event_v2', 'event_v2_sh')", fetch=True) or []
    for k, js in r:
        d = json.loads(js)
        with LOCK:
            (S if k == "event_v2" else SH).update(d)
    S["loaded"] = SH["loaded"] = 1

LAST_SAVED = {"h": None}

def save(full=False):
    with LOCK:
        js = json.dumps({k: v for k, v in S.items() if k != "loaded"})
    h = hash(js)
    if h != LAST_SAVED["h"]:                                 # ничего не изменилось — базу не трогаем
        xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('event_v2', %s)
                    ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (js,))
        LAST_SAVED["h"] = h
    if full:
        js = json.dumps({k: v for k, v in SH.items() if k != "loaded"})
        xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('event_v2_sh', %s)
                    ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (js,))

def loop():
    time.sleep(150)
    while not S.get("loaded"):
        try:
            load()
        except Exception:
            log.exception("xevent load")
            time.sleep(60)
    n = 0
    while True:
        t0 = time.time()
        M["err"] = ""
        try:
            step(t0)
        except Exception as e:
            M["err"] = str(e)[:120]
            log.exception("xevent step")
        M["last"], M["dur"] = t0, time.time() - t0
        n += 1
        try:
            save(full=n % 6 == 1)                             # тень — раз в час, она большая
        except Exception:
            log.exception("xevent save")
        time.sleep(max(60, STEP - (time.time() - t0)))

def ok():
    return mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN

def plain(txt):
    return txt, 200, {"Content-Type": "text/plain; charset=utf-8"}

@mm.app.route("/ev")
def ev_page():
    if not ok():
        return "forbidden", 403
    with LOCK:
        pos, done = dict(S["pos"]), list(S["done"][-300:])
    return jsonify({"версия": VERSION, "событий": M["n_ev"], "крипто_отсеяно": M["n_cr"], "рынков_в_окне": M["n_mk"],
                    "по_категориям": M["by_cat"], "источники": M["src"], "отсев": M["rej"], "кандидаты": M["cand"],
                    "открыто": pos, "закрыто": done, "нестыковок": S["arb_n"], "вето": M["veto"][-20:],
                    "тень_ждёт": len(SH["sh"]) + len(SH["shl"]), "тень_рассчитано": len(SH["shd"]),
                    "журнал": S["log"][-100:], "ошибка": M["err"]})

@mm.app.route("/ev/brief")
def ev_brief():
    if not ok():
        return "forbidden", 403
    txt = text()
    if request.args.get("tg") == "1":
        mm.tg(txt)
    return plain(txt)

@mm.app.route("/ev/deps")
def ev_deps():
    if not ok():
        return "forbidden", 403
    calib_build()
    txt = deps_text()
    if request.args.get("tg") == "1":
        mm.tg(txt)
    return plain(txt)

@mm.app.route("/ev/ask")
def ev_ask():
    if not ok():
        return "forbidden", 403
    try:
        return plain(ask(request.args.get("q", ""), use_ai=request.args.get("ai") == "1"))
    except Exception as e:
        return plain(f"ошибка: {str(e)[:200]}")

if ON:
    threading.Thread(target=loop, daemon=True).start()
