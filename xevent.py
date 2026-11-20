# xevent.py — БУМАГА + АЛЕРТЫ: Polymarket, ВСЕ события кроме крипто (крипто разбирает xpoly). Ничего не покупает.
#   «Пирамида» проверок — от жёстких к мягким. Алерт только если основание твёрдое:
#   1) НЕСТЫКОВКА (чистая математика, без прогноза): рынки одного события противоречат друг другу — «к июню» дороже
#      «к декабрю», «выше 4%» дороже «выше 3%» или сумма всех исходов сильно не 1. Тогда набор покупок даёт выплату
#      ≥ $1 дешевле $1 даже после комиссии. Алерт «НЕСТЫКОВКА» (проверьте правила обоих рынков и глубину стакана).
#   2) ОЦЕНКА: за основу — цена рынка; слои двигают её в шансах: VIG (сумма «ДА» по взаимоисключающим исходам > 1 —
#      делим на сумму) и BIAS (маловероятное толпа переоценивает; множители XEV_CAL — это ГИПОТЕЗА, её проверяет «тень»).
#      Алерт «СТАВКА» — только если ≥ 2 слоя тянут в одну сторону и перевес после комиссии ≥ XEV_EDGE.
#   3) ФИЛЬТРЫ: ликвидность, спред, не идёт матч, цена не прыгала за сутки (скачок = новость, которой мы не видим), срок.
#   ПРОВЕРКА: каждая «СТАВКА» ведётся на бумаге до расчёта ($XEV_USD); плюс «тень» — цены тысяч рынков и их исходы:
#   по ним в /ev/brief видно, правда ли толпа переоценивает маловероятное. Без новостей и внешних данных слои мягкие,
#   алертов будет мало — это нормально. Новости, букмекеры, погода, опросы — следующие слои, ставятся поверх этих.
#   Комиссия: fee = доли × ставка × p × (1−p); ставка по категории (с 20.11.2026: геополитика 0, политика без выборов,
#   экономика, культура, погода 0,01; спорт, финансы, технологии, выборы, прочее 0,05); XEV_FEE — одна ставка на всё.
#   /ev?t=... — таблица, /ev/brief?t=... — коротко (&tg=1 — в Telegram). XEV_ON=0 — выключить, XEV_TG=0 — без алертов.
import os, re, time, json, math, threading, logging, datetime as dt
import requests
from flask import jsonify, request
import mm_scanner as mm
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

VERSION = "xevent v1 · 19.11"
ON = env("XEV_ON", "1") == "1"
TG = env("XEV_TG", "1") == "1"
STEP = int(env("XEV_SEC", "900"))                       # пауза между проходами, сек
N_EV = int(env("XEV_EVENTS", "800"))                    # сколько событий (по обороту за сутки) смотрим за проход
LIQ = float(env("XEV_LIQ", "3000"))                     # минимум ликвидности рынка, $
SPR = float(env("XEV_SPREAD", "0.05"))                  # максимум спреда ДА
EDGE = float(env("XEV_EDGE", "0.04"))                   # перевес «оценка − цена с комиссией» для СТАВКИ
ROI = float(env("XEV_ROI", "0.05"))                     # и не меньше этой доходности на вложенный доллар
LAYERS = int(env("XEV_LAYERS", "2"))                    # сколько слоёв должны тянуть в одну сторону
USD = float(env("XEV_USD", "20"))
DAY_MAX = int(env("XEV_DAY_MAX", "6"))                  # алертов в сутки
D_MIN, D_MAX = float(env("XEV_D_MIN", "1")), float(env("XEV_D_MAX", "45"))   # дней до закрытия
MOVE = float(env("XEV_MOVE", "0.08"))                   # скачок цены за сутки — пропуск
ARB = float(env("XEV_ARB", "0.02"))                     # минимум гарантированной прибыли на $1 после комиссий
W_VIG, W_BIAS = float(env("XEV_W_VIG", "1.0")), float(env("XEV_W_BIAS", "0.6"))
SH_MAX = int(env("XEV_SHADOW", "4000"))                 # размер «тени»
FEE_ALL = env("XEV_FEE")
CAL = [(0.03, 0.75), (0.08, 0.85), (0.15, 0.93)]        # цена ДА ниже порога → вероятность ×множитель (гипотеза)
if env("XEV_CAL"):
    CAL = sorted((float(a), float(b)) for a, b in (x.split(":") for x in env("XEV_CAL").split(",") if ":" in x))
GAMMA, CLOB = "https://gamma-api.polymarket.com", "https://clob.polymarket.com"
log = logging.getLogger("xevent")
S = {"loaded": 0, "pos": {}, "done": [], "log": [], "seen": {}, "sh": {}, "shd": [], "arb_n": 0, "day": ["", 0]}
M = {"err": "", "last": 0, "cand": [], "arbs": [], "n_ev": 0, "n_mk": 0, "n_cr": 0, "rej": {}}
LOCK = threading.Lock()
MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec"
DATE_RE = re.compile(r"\b(?:%s)\b\.?(?:\s+\d{1,2}(?!\d)(?:st|nd|rd|th)?)?(?:,?\s+\d{4})?|\b\d{4}-\d{2}-\d{2}\b|\bq[1-4]\s*\d{4}\b" % MONTHS, re.I)
NUM_RE = re.compile(r"(\d[\d,]*\.?\d*)\s*(k|m|bn|b|million|billion|%)?", re.I)
UP_RE = re.compile(r"\b(above|over|exceed\w*|at least|more than|higher than|greater than)\b")
DN_RE = re.compile(r"\b(below|under|less than|at most|lower than|fewer than)\b")
CR_TAGS = ("crypto", "bitcoin", "ethereum", "solana", "xrp", "dogecoin", "defi", "memecoin", "altcoin", "stablecoin")
CR_RE = re.compile(r"\b(bitcoin|btc|ethereum|eth|solana|xrp|dogecoin|doge|crypto\w*|memecoin|altcoin|stablecoin|usdt|usdc|"
                   r"coinbase|binance|airdrop|fdv|hyperliquid|megaeth)\b", re.I)

def lt(t=None, f="%d.%m %H:%M"):
    return time.strftime(f, time.gmtime((t if t is not None else time.time()) + 8 * 3600))

def get(url, **params):
    r = requests.get(url, params=params, timeout=25)
    r.raise_for_status()
    return r.json()

def fnum(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d

def jl(x):
    if isinstance(x, list):
        return x
    try:
        return json.loads(x or "[]")
    except (TypeError, ValueError):
        return []

def ts(s):
    try:
        d = dt.datetime.fromisoformat(re.sub(r"([+-]\d{2})$", r"\1:00", str(s).replace("Z", "+00:00")))
        return (d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)).timestamp()
    except Exception:
        return None

def lg(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))

def sg(z):
    return 1 / (1 + math.exp(-z))

def fee(p, r):
    return r * p * (1 - p)

def fee_rate(ev):
    if FEE_ALL:
        return float(FEE_ALL)
    s = " ".join([ev.get("category") or ""] + [f"{t.get('slug', '')} {t.get('label', '')}" for t in ev.get("tags") or []]).lower()
    if "sport" in s:
        return 0.05
    if "geopolit" in s or "world event" in s or "world-event" in s:
        return 0.0
    if "election" in s:
        return 0.05
    if any(k in s for k in ("economy", "economic", "culture", "weather", "mention", "politic")):
        return 0.01
    return 0.05

def is_crypto(ev):
    s = " ".join(f"{t.get('slug', '')} {t.get('label', '')}" for t in ev.get("tags") or []).lower()
    return any(k in s for k in CR_TAGS) or bool(CR_RE.search(ev.get("title") or ""))

def sig(q):
    return re.sub(r"\s+", " ", NUM_RE.sub("<n>", DATE_RE.sub("<d>", q.lower()))).strip()

def nums(q):
    out = []
    for a, u in NUM_RE.findall(DATE_RE.sub(" ", q)):
        v = fnum(a.replace(",", ""))
        if v is not None:
            out.append(v * {"k": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}.get(u.lower(), 1))
    return out

def bias_p(p):
    for hi, f in CAL:
        if p < hi:
            return p * f
    return p

def note(text, now):
    S["log"] = (S["log"] + [{"t": int(now), "x": text}])[-300:]

# ---------- данные ----------
def mk(m, ev, now):
    """Рынок «Да/Нет» с живыми котировками → словарь, иначе None."""
    if m.get("closed") or m.get("active") is False or m.get("acceptingOrders") is False or m.get("enableOrderBook") is False:
        return None
    toks = jl(m.get("clobTokenIds"))
    if [str(o).lower() for o in jl(m.get("outcomes"))] != ["yes", "no"] or len(toks) != 2:
        return None
    bid, ask = fnum(m.get("bestBid")), fnum(m.get("bestAsk"))
    end = ts(m.get("endDate")) or ts(ev.get("endDate"))
    if bid is None or ask is None or not 0 < bid <= ask < 1 or not end:
        return None
    gs = ts(m.get("gameStartTime"))
    if gs and gs < now + 3600:
        return None                                      # матч идёт или вот-вот начнётся
    liq = fnum(m.get("liquidityNum")) or fnum(m.get("liquidity")) or 0.0
    return {"id": str(m.get("id")), "q": m.get("question") or "", "g": m.get("groupItemTitle") or "", "bid": bid, "ask": ask,
            "mid": (bid + ask) / 2, "spr": ask - bid, "liq": liq, "end": end, "d1": fnum(m.get("oneDayPriceChange"), 0.0),
            "tokY": toks[0], "tokN": toks[1]}

def pages():
    off = 0
    while off < N_EV:
        d = get(GAMMA + "/events", active="true", closed="false", order="volume24hr", ascending="false", limit=100, offset=off)
        if not d:
            return
        yield d
        if len(d) < 100:
            return
        off += 100
        time.sleep(0.3)

def best_ask(tok):
    try:
        a = [fnum(x.get("price")) for x in get(CLOB + "/book", token_id=tok).get("asks") or []]
        a = [x for x in a if x]
        return min(a) if a else None
    except Exception:
        return None

# ---------- слой 1: нестыковки ----------
def chains(ms, rate):
    """Пары (A ⊂ B): если A случилось, то и B. Покупка «НЕТ» на A + «ДА» на B даёт выплату ≥ $1."""
    out, g = [], {}
    for x in ms:
        g.setdefault(sig(x["q"]), []).append(x)
    for s, grp in g.items():
        if len(grp) < 2:
            continue
        ns = [nums(x["q"]) for x in grp]
        ends = [x["end"] for x in grp]
        order = None
        if re.search(r"\b(by|before)\s+<d>", s) and all(n == ns[0] for n in ns) and max(ends) - min(ends) >= 86400:
            order = sorted(grp, key=lambda x: x["end"])          # раньше ⊂ позже
        elif bool(UP_RE.search(s)) != bool(DN_RE.search(s)) and max(ends) - min(ends) < 129600 and len({len(n) for n in ns}) == 1:
            diff = [i for i in range(len(ns[0])) if len({n[i] for n in ns}) > 1]
            if len(diff) == 1:
                i = diff[0]
                order = sorted(grp, key=lambda x: nums(x["q"])[i], reverse=bool(UP_RE.search(s)))   # «выше»: больший порог ⊂ меньший
        for a in range(len(order or [])):
            for b in range(a + 1, len(order)):
                A, B = order[a], order[b]
                net = A["bid"] - B["ask"] - fee(A["bid"], rate) - fee(B["ask"], rate)
                if net >= ARB and min(A["liq"], B["liq"]) >= LIQ:
                    out.append((net, A, B))
    return sorted(out, key=lambda r: -r[0])[:2]

def multileg(ev, ms, n_open, rate):
    """Взаимоисключающие исходы: если Σbid(«ДА») > 1, то «НЕТ» на все исходы дешевле гарантированной выплаты n−1."""
    if not ev.get("negRisk") or len(ms) != n_open or not 3 <= len(ms) <= 14 or min(x["liq"] for x in ms) < LIQ / 3:
        return None
    net = sum(x["bid"] for x in ms) - 1 - sum(fee(x["bid"], rate) for x in ms)
    return ("НЕТ на все исходы", net, len(ms)) if net >= ARB else None

# ---------- слой 2: оценка ----------
def judge(x, vig, rate, now):
    p = x["mid"]
    ev = []
    if vig and vig >= 1.03:
        ev.append(("VIG", W_VIG * (lg(p / vig) - lg(p)), f"сумма «ДА» по событию {vig:.2f}: рынок переплачивает за исходы"))
    if bias_p(p) < p:
        ev.append(("BIAS", W_BIAS * (lg(bias_p(p)) - lg(p)), "маловероятное толпа обычно переоценивает"))
    q = sg(lg(p) + sum(v for _, v, _ in ev))
    best = None
    for side, ask, qs, tok in (("ДА", x["ask"], q, x["tokY"]), ("НЕТ", 1 - x["bid"], 1 - q, x["tokN"])):
        c = ask + fee(ask, rate)
        why = [e for e in ev if (e[1] > 0) == (side == "ДА") and abs(e[1]) >= 0.03]
        row = {"side": side, "ask": ask, "c": c, "q": qs, "edge": qs - c, "roi": (qs / c - 1) if c else 0, "tok": tok,
               "why": [w[0] for w in why], "txt": [w[2] for w in why]}
        if best is None or row["edge"] > best["edge"]:
            best = row
    return best

def gate(x, r, now):
    d = (x["end"] - now) / 86400
    if x["liq"] < LIQ: return "ликвидность"
    if x["spr"] > SPR: return "спред"
    if not D_MIN <= d <= D_MAX: return "срок"
    if abs(x["d1"]) >= MOVE: return "скачок цены"
    if not 0.10 <= r["ask"] <= 0.93: return "цена"
    if len(r["why"]) < LAYERS: return "мало слоёв"
    if r["edge"] < EDGE or r["roi"] < ROI: return "перевес мал"
    return ""

def analyse(ev, now, cand, rej):
    rate = fee_rate(ev)
    allm = [m for m in ev.get("markets") or [] if not m.get("closed")]
    ms = [x for x in (mk(m, ev, now) for m in allm) if x]
    M["n_mk"] += len(ms)
    if not ms:
        return
    slug, title = ev.get("slug") or "", ev.get("title") or ""
    for net, A, B in chains(ms, rate):
        fire_arb(f"arb:{A['id']}:{B['id']}", title, slug, rate, A, B, net, now)
    ml = multileg(ev, ms, len(allm), rate)
    if ml:
        key = f"ml:{ev.get('id')}"
        if key not in S["seen"] and day_ok(now):
            S["seen"][key] = int(now)
            send(f"🧩 Polymarket (нестыковка по сумме исходов): «{title}» — {ml[0]}, {ml[2]} рынков, гарантированно ≈ +{ml[1]:.3f} "
                 f"на $1 после комиссий. Много ног: проверьте стаканы и правила. https://polymarket.com/event/{slug}", now)
    vig = sum(x["mid"] for x in ms) if ev.get("negRisk") and len(ms) == len(allm) and len(ms) >= 3 else None
    for x in ms:
        r = judge(x, vig, rate, now)
        why = gate(x, r, now)
        row = dict(r, id=x["id"], title=title, name=x["g"] or x["q"], slug=slug, mid=round(x["mid"], 3), days=round((x["end"] - now) / 86400, 1),
                   liq=round(x["liq"]), spr=round(x["spr"], 3), end=x["end"], rate=rate, gate=why, vig=round(vig, 3) if vig else None)
        if r["why"]:
            cand.append(row)
        rej[why or "ок"] = rej.get(why or "ок", 0) + 1
        if not why and x["id"] not in S["pos"] and S["seen"].get(x["id"], 0) < now - 3 * 86400 and day_ok(now):
            fire_bet(row, now)
        if (x["id"].isdigit() and int(x["id"]) % 3 == 0 and x["liq"] >= LIQ and 1 <= (x["end"] - now) / 86400 <= 60
                and 0.03 <= x["mid"] <= 0.97 and x["id"] not in S["sh"] and len(S["sh"]) < SH_MAX):
            S["sh"][x["id"]] = [int(now), round(x["mid"], 4), int(x["end"]), round(vig or 0, 3)]

def day_ok(now):
    d = dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime("%Y-%m-%d")
    if S["day"][0] != d:
        S["day"] = [d, 0]
    return S["day"][1] < DAY_MAX

def send(text, now):
    S["day"][1] += 1
    note(text[:160], now)
    if TG:
        mm.tg(text)

def fire_arb(key, title, slug, rate, A, B, net, now):
    if S["seen"].get(key, 0) > now - 12 * 3600 or not day_ok(now):
        return
    ya, nb = best_ask(A["tokN"]), best_ask(B["tokY"])               # проверяем по настоящему стакану
    if ya is None or nb is None:
        return
    net = 1 - ya - nb - fee(ya, rate) - fee(nb, rate)
    if net < ARB:
        return
    S["seen"][key] = int(now)
    S["arb_n"] += 1
    send(f"🧩 Polymarket (НЕСТЫКОВКА): «{title}»\nA «{A['g'] or A['q']}» (закрытие {lt(A['end'], '%d.%m')}) ⊂ B «{B['g'] or B['q']}» "
         f"({lt(B['end'], '%d.%m')}).\nКупить НЕТ на A по {ya:.3f} и ДА на B по {nb:.3f}: выплата ≥ $1 при затратах {ya + nb + fee(ya, rate) + fee(nb, rate):.3f} "
         f"→ гарантированно ≈ +{net:.3f} на $1. Глубина по лучшей цене мала: проверьте стакан и правила обоих рынков.\n"
         f"https://polymarket.com/event/{slug}", now)

def fire_bet(row, now):
    a = best_ask(row["tok"])
    if a is not None:                                                  # пересчёт по настоящему стакану
        if abs(a - row["ask"]) > 0.05:
            return                                                     # цены разошлись — данные устарели
        row["ask"], row["c"] = a, a + fee(a, row["rate"])
        row["edge"], row["roi"] = row["q"] - row["c"], row["q"] / row["c"] - 1
        if row["edge"] < EDGE or row["roi"] < ROI:
            return
    row["t"], row["shares"], row["cost"] = int(now), USD / row["c"], USD
    with LOCK:
        S["pos"][row["id"]] = row
        S["seen"][row["id"]] = int(now)
    send(f"🧠 Polymarket (бумага): «{row['title']}» — {row['name']}\nСтавка модели: {row['side']} по цене до {row['ask']:.3f} "
         f"(с комиссией {row['c']:.3f}). Оценка {row['q']:.0%} против {row['c']:.0%} в цене → перевес +{row['edge']:.2f}.\n"
         f"Основания: {'; '.join(row['txt'])}.\nДо закрытия {row['days']:.0f} дн., ликвидность ${row['liq']:,}, спред {row['spr']:.2f}. "
         f"Сигнал модели для бумажной проверки, не гарантия.\nhttps://polymarket.com/event/{row['slug']}", now)

# ---------- расчёт ----------
def outcome(mid):
    """1 — «ДА», 0 — «НЕТ», None — рынок ещё не рассчитан."""
    m = get(f"{GAMMA}/markets/{mid}")
    op = [fnum(x) for x in jl(m.get("outcomePrices"))]
    if not m.get("closed") or len(op) != 2 or None in op or op[0] not in (0.0, 1.0):
        return None
    return int(op[0])

def settle(now):
    n = 0
    for key, p in sorted(S["pos"].items(), key=lambda kv: kv[1]["end"]):
        if now < p["end"] + 3600 or n >= 20:
            continue
        n += 1
        try:
            y = outcome(key)
            if y is None:
                continue
            win = (y == 1) == (p["side"] == "ДА")
            pnl = (p["shares"] if win else 0.0) - p["cost"]
            with LOCK:
                S["pos"].pop(key, None)
                S["done"] = (S["done"] + [dict(p, t1=int(now), win=win, pnl=round(pnl, 4))])[-2000:]
            note(f"{'✅' if win else '❌'} {p['name'][:50]} {p['side']}: {pnl:+.2f}$", now)
            if TG:
                mm.tg(f"🧠 Polymarket (бумага): «{p['name'][:80]}» — {p['side']} {'выиграла' if win else 'проиграла'}, {pnl:+.2f}$ на ${p['cost']:.0f}")
        except Exception as e:
            log.warning("xevent settle %s: %s", key, e)
    for key, v in sorted(S["sh"].items(), key=lambda kv: kv[1][2]):
        if now < v[2] + 3600 or n >= 40:
            continue
        n += 1
        try:
            y = outcome(key)
            if y is not None:
                S["shd"] = (S["shd"] + [[v[1], y, v[3]]])[-8000:]
                S["sh"].pop(key, None)
            elif now > v[2] + 14 * 86400:
                S["sh"].pop(key, None)
        except Exception:
            if now > v[2] + 30 * 86400:
                S["sh"].pop(key, None)
    S["seen"] = {k: v for k, v in S["seen"].items() if v > int(now) - 5 * 86400}

def step(now):
    settle(now)
    M["n_ev"] = M["n_mk"] = M["n_cr"] = 0
    cand, rej = [], {}
    for page in pages():
        for ev in page:
            M["n_ev"] += 1
            if is_crypto(ev):
                M["n_cr"] += 1
                continue
            try:
                analyse(ev, now, cand, rej)
            except Exception as e:
                log.warning("xevent %s: %s", ev.get("slug"), e)
                M["err"] = f"{ev.get('slug')}: {str(e)[:90]}"
    M["cand"] = sorted(cand, key=lambda r: -r["edge"])[:25]
    M["rej"], M["last"] = rej, now
    save()

# ---------- отчёт ----------
def calib():
    L = []
    for lo, hi in ((0.03, 0.08), (0.08, 0.15), (0.15, 0.30), (0.30, 0.50), (0.50, 0.70), (0.70, 0.85), (0.85, 0.92), (0.92, 0.97)):
        b = [d for d in S["shd"] if lo <= d[0] < hi]
        if len(b) >= 10:
            pm, ym = sum(d[0] for d in b) / len(b), sum(d[1] for d in b) / len(b)
            L.append(f"  цена ДА {lo:.2f}–{hi:.2f}: {len(b)} рынков, цена {pm:.3f}, «ДА» сбылось {ym:.3f} ({ym - pm:+.3f})")
    return L or [f"  пока мало данных: рассчитано {len(S['shd'])} из «тени» (нужно ≥10 в корзине), ждут расчёта {len(S['sh'])}"]

def text():
    with LOCK:
        done, pos = list(S["done"]), dict(S["pos"])
    n, win = len(done), sum(1 for d in done if d["win"])
    pnl, cost = sum(d["pnl"] for d in done), sum(d["cost"] for d in done)
    L = [f"🧠 {VERSION} — БУМАГА: Polymarket, все события кроме крипто. {lt()}",
         f"За проход: событий {M['n_ev']} (крипто пропущено {M['n_cr']}), рынков с котировками {M['n_mk']}. Перевес ≥{EDGE:.2f} и ≥{ROI:.0%}, "
         f"ликвидность ≥${LIQ:.0f}, спред ≤{SPR:.2f}, {D_MIN:g}–{D_MAX:g} дн., ${USD:.0f} на сигнал",
         f"Отсев: " + (", ".join(f"{k} {v}" for k, v in sorted(M["rej"].items(), key=lambda kv: -kv[1])[:7]) or "—"),
         f"НЕСТЫКОВОК найдено {S['arb_n']}. СТАВОК закрыто {n}: выиграло {win} ({(win / n * 100) if n else 0:.0f}%), итог {pnl:+.2f}$ на ${cost:.0f} "
         f"({(pnl / cost * 100) if cost else 0:+.1f}%), открыто {len(pos)}"]
    by = {}
    for d in done:
        k = "+".join(d["why"]) or "—"
        a = by.setdefault(k, [0, 0, 0.0])
        a[0] += 1; a[1] += d["win"]; a[2] += d["pnl"]
    for k, (a, w, p) in by.items():
        L.append(f"  слои {k}: {a} шт., выиграло {w}, {p:+.2f}$")
    for p in sorted(pos.values(), key=lambda x: x["end"])[:8]:
        L.append(f"  открыта: {p['name'][:46]} — {p['side']} по {p['ask']:.3f}, перевес +{p['edge']:.2f}, расчёт {lt(p['end'], '%d.%m')}")
    L.append(f"Калибровка «тени» (толпа переоценивает маловероятное, если «ДА» сбывается реже цены):")
    L += calib()
    L.append("Сильнейшие расхождения сейчас (даже если отсеяны):")
    for c in M["cand"][:6]:
        L.append(f"  {c['name'][:44]} — {c['side']} {c['ask']:.2f}, оценка {c['q']:.0%}, перевес {c['edge']:+.2f}, слои {'+'.join(c['why'])}, "
                 f"{'ОК' if not c['gate'] else 'отсев: ' + c['gate']}")
    if M["err"]:
        L.append("⚠️ " + M["err"])
    return "\n".join(L)

def table():
    return {"версия": VERSION, "события": M["n_ev"], "крипто_пропущено": M["n_cr"], "рынков": M["n_mk"], "отсев": M["rej"],
            "нестыковок": S["arb_n"], "открыто": S["pos"], "закрыто": S["done"][-300:], "кандидаты": M["cand"],
            "тень_ждёт": len(S["sh"]), "тень_рассчитано": len(S["shd"]), "калибровка": calib(), "журнал": S["log"][-150:], "ошибка": M["err"]}

# ---------- база и цикл ----------
def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'event_v1'", fetch=True)
    with LOCK:
        S.update(json.loads(r[0][0]) if r else {})
    S["loaded"] = 1

def save():
    with LOCK:
        js = json.dumps({k: v for k, v in S.items() if k != "loaded"})
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('event_v1', %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (js,))

def loop():
    time.sleep(150)
    while not S.get("loaded"):
        try:
            load()
        except Exception:
            log.exception("xevent load")
            time.sleep(60)
    while True:
        t0 = time.time()
        M["err"] = ""
        try:
            step(t0)
        except Exception as e:
            M["err"] = str(e)[:120]
            log.exception("xevent step")
        time.sleep(max(60, STEP - (time.time() - t0)))

def ok():
    return mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN

@mm.app.route("/ev")
def ev_page():
    if not ok():
        return "forbidden", 403
    return jsonify(table())

@mm.app.route("/ev/brief")
def ev_brief():
    if not ok():
        return "forbidden", 403
    txt = text()
    if request.args.get("tg") == "1":
        mm.tg(txt)
    return txt, 200, {"Content-Type": "text/plain; charset=utf-8"}

if ON:
    threading.Thread(target=loop, daemon=True).start()
