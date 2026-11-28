# xrelay.py — БУМАГА «эстафета Корея → США» (исследование 28.11, цепочка Т2). НИЧЕГО НЕ ТОРГУЕТ.
#   Новости по памяти выходят днём по Корее, акции США в это время закрыты. В 15:00 KST (KRX закрылась) считаю ход
#   SK hynix и Samsung на Gate за сессию 09:00→15:00 KST. Если средний ход ≥ XRL_THR (2%) по модулю — на бумаге та же
#   сторона по SOXL, AMD, TSM, MU, NVDA, WDC, SNDK на Gate. Два входа: А — сразу в 15:00 KST, Б — на открытии NYSE
#   (9:30 NY). Выход обоих — 16:00 NY. Итог для тейкера (0,075% + проскальзывание по обороту, на сторону) и для лимиток (0,08% на круг).
#   История: 20 лет (2005–2026, Yahoo) при пороге 2% — +0,30% в день портфеля до издержек, все 5 периодов в плюсе;
#   тейкер Gate съедает почти всё, плюс — только с лимитками. Пробный срок бумаги — 2 недели (до 12.12.2026), дальше решаем.
#   Критерий для денег (заранее): ≥ 60 дней с сигналом и нижняя граница 95% ДИ > 0 (за 2 недели не наберётся — смотрим тенденцию).
#   Страницы (?t=STATS_TOKEN): /relay — всё (JSON), /relay/brief — коротко (&tg=1 — в Telegram). XRL_ON=0 — выкл.
import os, time, json, random, threading, logging, datetime as dt
from zoneinfo import ZoneInfo
import requests
from flask import jsonify, request
import mm_scanner as mm
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

VERSION = "xrelay v1 · 28.11"
ON = env("XRL_ON", "1") == "1"
THR = float(env("XRL_THR", "0.02"))
KR = ["SKHYNIX_USDT", "SAMSUNG_USDT"]
US = ["SOXL_USDT", "AMD_USDT", "TSM_USDT", "MU_USDT", "NVDA_USDT", "WDC_USDT", "SNDK_USDT"]
TAKER, MAKER_RT = 0.00075, 0.0008
SEOUL, NY = ZoneInfo("Asia/Seoul"), ZoneInfo("America/New_York")
API = "https://api.gateio.ws/api/v4/futures/usdt"
log = logging.getLogger("xrelay")
S = requests.Session(); S.headers["User-Agent"] = "Mozilla/5.0"
L = {}
LOCK = threading.RLock()

def slip(v):
    return 0.0004 if v > 20e6 else 0.0006 if v > 5e6 else 0.0009 if v > 1e6 else 0.003

def kst(d, hh):
    return int(dt.datetime(d.year, d.month, d.day, hh, tzinfo=SEOUL).timestamp())

def nyt(d, hh, mm_=0):
    return int(dt.datetime(d.year, d.month, d.day, hh, mm_, tzinfo=NY).timestamp())

def lt(t):
    return time.strftime("%d.%m %H:%M", time.gmtime(t + 8 * 3600))

def candle(contract, t):
    """Часовая свеча, начинающаяся в t: (open, close) или None."""
    t = t // 3600 * 3600
    try:
        r = S.get(f"{API}/candlesticks", params={"contract": contract, "interval": "1h", "from": t, "to": t + 3600}, timeout=20).json()
        for c in r if isinstance(r, list) else []:
            if int(c["t"]) == t:
                return float(c["o"]), float(c["c"])
    except Exception:
        log.exception("xrelay candle")
    return None

def jump(contract, a, b):
    """Сплит/перевыпуск в окне (скачок > 30% за час) — такую сделку не считаю."""
    try:
        r = S.get(f"{API}/candlesticks", params={"contract": contract, "interval": "1h", "from": a // 3600 * 3600, "to": b}, timeout=20).json()
        prev = None
        for c in r if isinstance(r, list) else []:
            o, cl = float(c["o"]), float(c["c"])
            if (prev and abs(o / prev - 1) > 0.3) or abs(cl / o - 1) > 0.3:
                return True
            prev = cl
    except Exception:
        return False
    return False

def signal(moves):
    xs = [m for m in moves if m is not None and abs(m) < 0.3]
    if len(xs) < len(KR):
        return None, 0
    x = sum(xs) / len(xs)
    return x, (1 if x >= THR else -1 if x <= -THR else 0)

def say(text):
    mm.tg("🧪🇰🇷→🇺🇸 Эстафета [бумага]: " + text)

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'relay_v1'", fetch=True)
    L.update(json.loads(r[0][0]) if r else {})
    L.setdefault("days", {}); L.setdefault("open", []); L.setdefault("start", int(time.time()))
    L["loaded"] = 1

def save():
    if L.get("loaded"):
        try:
            xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('relay_v1', %s)
                        ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(L),))
        except Exception:
            log.exception("xrelay save")

def vols():
    try:
        return {t["contract"]: float(t.get("volume_24h_quote") or 0) for t in S.get(f"{API}/tickers", timeout=20).json()}
    except Exception:
        return {}

def step(now):
    d = dt.datetime.fromtimestamp(now, SEOUL).date(); key = d.isoformat()
    if d.weekday() < 5 and now >= kst(d, 15) + 180 and key not in L["days"]:
        moves = []
        for c in KR:
            a, b = candle(c, kst(d, 9)), candle(c, kst(d, 15))
            moves.append(b[0] / a[0] - 1 if a and b else None)
        x, side = signal(moves)
        L["days"][key] = {"x": x, "side": side, "trades": []}
        if side:
            v = vols(); ex = nyt(d, 16)
            for c in US:
                p = candle(c, kst(d, 15))
                if p:
                    L["open"].append({"day": key, "c": c, "side": side, "var": "А", "p0": p[0], "t0": kst(d, 15), "exit": ex, "vol": v.get(c, 0)})
                L["open"].append({"day": key, "c": c, "side": side, "var": "Б", "p0": None, "t0": nyt(d, 9, 30), "exit": ex, "vol": v.get(c, 0)})
            say(f"{key}: SK hynix+Samsung за сессию KRX {x * 100:+.2f}% → {'ЛОНГ' if side > 0 else 'ШОРТ'} "
                f"{', '.join(c[:-5] for c in US)} (А — с 15:00 KST, Б — с открытия NYSE), закрою в 16:00 NY")
    keep, closed = [], 0
    for p in L["open"]:
        if p["p0"] is None and now >= p["t0"] + 1800 + 180:          # Б: close свечи 9:00 NY ≈ цена после открытия 9:30
            c = candle(p["c"], p["t0"] - 1800)
            p["p0"] = c[1] if c else None
            if p["p0"] is None and now > p["exit"]:
                continue
        if p["p0"] is None or now < p["exit"] + 180:
            keep.append(p); continue
        c = candle(p["c"], p["exit"])
        if not c:
            if now > p["exit"] + 86400: continue
            keep.append(p); continue
        if jump(p["c"], p["t0"], p["exit"]):
            continue
        g = p["side"] * (c[0] / p["p0"] - 1)
        L["days"][p["day"]]["trades"].append({"c": p["c"], "var": p["var"], "p0": p["p0"], "p1": c[0],
                                              "net": round(g - 2 * (TAKER + slip(p["vol"])), 5), "net_maker": round(g - MAKER_RT, 5)})
        closed += 1
    L["open"] = keep
    if closed:
        say(brief())

def stats():
    out = {}
    for var in ("А", "Б"):
        for k, lab in (("net", "тейкер"), ("net_maker", "лимитки")):
            dr = [sum(t[k] for t in ts) / len(ts) for v in L["days"].values()
                  for ts in [[t for t in v["trades"] if t["var"] == var]] if ts]
            if not dr: continue
            rnd = random.Random(1); bs = sorted(sum(rnd.choice(dr) for _ in dr) / len(dr) for _ in range(2000))
            out[f"{var}_{lab}"] = {"дней": len(dr), "средний_день_%": round(sum(dr) / len(dr) * 100, 3),
                                   "ДИ95_%": [round(bs[50] * 100, 2), round(bs[1949] * 100, 2)], "дней_в_плюсе": sum(x > 0 for x in dr)}
    return out

def brief():
    s = stats(); n = len(L["days"]); sig = sum(1 for v in L["days"].values() if v["side"])
    t = f"{VERSION}. С {lt(L['start'])}: торговых дней {n}, с сигналом (|ход| ≥ {THR * 100:g}%) {sig}, открыто позиций {len(L['open'])}."
    for k, v in s.items():
        t += f"\n{k.replace('_', ', ')}: дней {v['дней']}, день {v['средний_день_%']:+.2f}% (ДИ {v['ДИ95_%'][0]:+.2f}…{v['ДИ95_%'][1]:+.2f}%), в плюсе {v['дней_в_плюсе']}"
    return t

def loop():
    time.sleep(90)
    while not L.get("loaded"):
        try: load()
        except Exception:
            log.exception("xrelay load"); time.sleep(60)
    while True:
        try:
            with LOCK:
                step(time.time()); save()
        except Exception:
            log.exception("xrelay")
        time.sleep(300)

def ok():
    return mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN

@mm.app.route("/relay")
def relay_page():
    if not ok(): return "forbidden", 403
    with LOCK:
        return jsonify({"версия": VERSION, "порог_%": THR * 100, "итог": stats(), "открыто": L.get("open", []),
                        "дни": dict(sorted(L.get("days", {}).items())[-40:])})

@mm.app.route("/relay/brief")
def relay_brief():
    if not ok(): return "forbidden", 403
    with LOCK:
        txt = brief() if L.get("loaded") else "ещё загружается"
    if request.args.get("tg") == "1": say(txt)
    return txt, 200, {"Content-Type": "text/plain; charset=utf-8"}

if ON:
    threading.Thread(target=loop, daemon=True).start()
