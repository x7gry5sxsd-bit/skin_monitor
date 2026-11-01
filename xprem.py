# xprem.py — БУМАГА «ПРЕМИЯ ПЕРПА И ФАНДИНГ». Ничего не торгует, ключи бирж не трогает, пишет только свою строку в базе.
#   Две гипотезы записаны здесь ДО подсчёта и не меняются (идеи 1 и 2 из ответа Fable 01.11):
#   1) ПРЕМИЯ. Цена фьючерса на Gate ушла от справедливой (mark) на 0,7–2% и держится 3 минуты подряд →
#      встать против отклонения: перп дороже → шорт (продаём по лучшей цене покупателя — bid), дешевле → лонг (по ask).
#      Выход: разница вернулась до 0,15% — «возврат»; разница выросла до 2%+ или цена ушла против на 3% — стоп;
#      не дольше 60 мин. Не берём: монета за час уже +10% (шорт) / −10% (лонг) — это новости, а не перекос;
#      оборот за сутки меньше $50 тыс.; монета в делистинге. Одна монета — не чаще раза в час.
#   1б) (v1.1) ТО ЖЕ, НО К ИНДЕКСУ (средняя цена спота нескольких бирж, index_price) — те же правила, свой отсчёт
#      зачёта. Причина: за первые 2,5 ч к mark не нашлось ни одного случая — mark на Gate сам тянется за ценой перпа.
#      В отчёте — сколько «монето-минут» было с разницей 0,7%+ к mark и к индексу: видно, бывают ли такие случаи вообще.
#   1в) (v1.2) ПУТЬ ЦЕНЫ ПОСЛЕ РАЗНИЦЫ К ИНДЕКСУ — без правил, только замер: 60 мин после каждого случая из 1б
#      пишем, когда цена впервые прошла +0,5/1/2/3% и −0,5/1/2/3% для лонга (вход по ask, выход по bid) и для шорта
#      (вход по bid, выход по ask), и ход через 15/30/60 мин; то же у случайной монеты в ту же минуту. По этому
#      считаем «по ходу перпа» (перп выше индекса → лонг, ниже → шорт) и «против» с разными тейками/стопами.
#      Причина: первые 10 сделок 1б — разница не возвращалась, а росла (перп, похоже, идёт впереди спота).
#   2) ФАНДИНГ. За 15 мин до выплаты фандинг 0,2%+ за период → встать на сторону ПОЛУЧАТЕЛЯ (плюс — шорт, минус —
#      лонг), закрыть через 5 мин после выплаты. Доход = полученный фандинг + ход цены. 0,1–0,2% — пишем для справки.
#   ЧЕСТНАЯ ПРОВЕРКА:
#   • цены входа и выхода — из стакана (bid/ask), значит спред уже учтён; плюс комиссия Gate 0,05% × 2 = 0,1%;
#   • рядом с каждой сделкой — СЛУЧАЙНАЯ монета в ту же минуту, та же сторона, на то же время (у фандинга — две,
#     с обычным фандингом): так видно, сколько дал сам рынок;
#   • в зачёт — только события после первого запуска модуля (время хранится в базе);
#   • был перерыв (бот спал или перезапускался) дольше 5 мин посреди сделки — сделка не считается;
#   • «доказано» — 200+ сделок премии (100+ фандинга), в среднем плюс и лучше случайных больше чем на 2 погрешности.
#   $ — как у живого автошорта: маржа XPR_MARGIN ($20) × плечо XPR_LEV (10) = позиция $200.
#   /prem?t=... — всё таблицей, /prem/brief?t=... — коротко (&tg=1 — в Telegram), /prem/raw?t=...&days=7 — все события.
#   Раз в день в 9:10 — итог в Telegram. XPR_ON=0 — выключить, XPR_DAILY=0 — не слать.
import os, time, json, math, random, threading, logging, statistics
from collections import deque
from flask import jsonify, request
import mm_scanner as mm
import xpaper
import xexec_api as api

def env(k, d=""):
    return os.getenv(k, d).strip()

VERSION = "xprem v1.2 · 01.11"
ON = env("XPR_ON", "1") == "1"
DAILY = env("XPR_DAILY", "1") == "1"
HOUR = int(env("XPR_HOUR", "9"))
MARGIN = float(env("XPR_MARGIN", "20"))
LEV = float(env("XPR_LEV", "10"))
FEE = float(env("XPR_FEE", "0.001"))                  # комиссия туда-обратно; спред — уже в ценах bid/ask
MIN_VOL = float(env("XPR_MIN_VOL", "50000"))           # оборот за сутки, $
P_IN, P_OUT, P_STOP, PX_STOP = 0.007, 0.0015, 0.02, 0.03
STREAK, HOLD_P, PUMP1H, COOL = 3, 60, 0.10, 3600
F_IN, F_LOW, F_BEFORE, F_AFTER = 0.002, 0.001, 15 * 60, 5 * 60
N_PROOF_P, N_PROOF_F = 200, 100
GAP = 300                                              # перерыв дольше 5 мин посреди сделки — не в зачёт
MAX_OPEN, DONE_MAX, PATH_MAX = 120, 1500, 2000
LV = (0.005, 0.01, 0.02, 0.03)                         # (v1.2) уровни для замера пути цены
PCOMBO = ((0.01, 0.01), (0.02, 0.02), (0.03, 0.03), (0.01, 0.02), (0.02, 0.01))   # тейк / стоп для пути
GTF = "https://api.gateio.ws/api/v4/futures/usdt"
log = logging.getLogger("xprem")
S = {"loaded": 0, "open": [], "done": [], "paths": [], "pdone": []}
M = {"hist": {}, "streak": {}, "cool": {}, "ctr": {}, "ctr_t": 0, "last_t": 0, "saved_t": 0, "fseen": {}, "err": "",
     "snap_n": 0, "book_share": None, "now_dev": {}}
REFS = ("mark", "index")                               # (v1.1) справедливая цена: mark или индекс спота
LOCK = threading.Lock()

def lt(t=None, f="%d.%m %H:%M"):
    return time.strftime(f, time.gmtime((time.time() if t is None else t) + 8 * 3600))

def fpos(x):
    try:
        v = float(x)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None

def fnum(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d

def pct(x):
    v = fnum(x, None)
    return None if v is None else v / 100

# ---------- данные Gate (публичные) ----------
def parse_tickers(rows):
    """{МОНЕТА: {last, mark, index, bid, ask, book, fund, vol, chg}} — только USDT-фьючерсы с ценой и mark."""
    out = {}
    for x in rows or []:
        c = x.get("contract", "")
        last, mark = fpos(x.get("last")), fpos(x.get("mark_price"))
        if not c.endswith("_USDT") or not last or not mark:
            continue
        bid, ask = fpos(x.get("highest_bid")), fpos(x.get("lowest_ask"))
        book = bool(bid and ask and ask >= bid)
        if not book:                                   # стакана в ответе нет — берём последнюю цену (помечаем)
            bid = ask = last
        out[c[:-5]] = {"last": last, "mark": mark, "index": fpos(x.get("index_price")), "bid": bid, "ask": ask,
                       "book": book, "fund": fnum(x.get("funding_rate")),
                       "vol": fnum(x.get("volume_24h_quote") or x.get("volume_24h_settle")),
                       "chg": pct(x.get("change_percentage"))}
    return out

def parse_contracts(rows):
    out = {}
    for x in rows or []:
        n = x.get("name", "")
        if n.endswith("_USDT"):
            out[n[:-5]] = {"next": int(fnum(x.get("funding_next_apply"))), "iv": int(fnum(x.get("funding_interval"), 28800)
                                                                                   or 28800),
                           "del": bool(x.get("in_delisting"))}
    return out

def fetch_tickers():
    return parse_tickers(api.S.get(f"{GTF}/tickers", timeout=20).json())

def contracts(now):
    if M["ctr"] and now - M["ctr_t"] < 600:
        return M["ctr"]
    try:
        M["ctr"] = parse_contracts(api.S.get(f"{GTF}/contracts", timeout=20).json())
        M["ctr_t"] = now
        M["err_c"] = ""
    except Exception as e:
        M["err_c"] = f"контракты: {str(e)[:120]}"
    return M["ctr"]

def next_apply(c, now):
    """Ближайшая выплата фандинга (UTC-секунды); если биржа отдала прошлое время — шагаем на интервал вперёд."""
    nx, iv = c.get("next") or 0, c.get("iv") or 28800
    if nx <= 0:
        return None
    while nx <= now - F_AFTER - 120:
        nx += iv
    return nx

def market(snap):
    v = [q["chg"] for q in snap.values() if q["chg"] is not None and q["vol"] >= MIN_VOL]
    return round(sum(v) / len(v), 4) if v else None

def regime(m):
    return None if m is None else "растёт" if m >= 0.01 else "падает" if m <= -0.01 else "ровный"

def ch1(coin, now):
    h = M["hist"].get(coin)
    if not h or now - h[0][0] < 55 * 60:
        return None
    old = next((p for t, p in h if t >= now - 3660), None)
    last = h[-1][1]
    return None if not old else last / old - 1

def hist_step(now, snap):
    for c, q in snap.items():
        h = M["hist"].setdefault(c, deque())
        h.append((now, q["last"]))
        while h and now - h[0][0] > 4200:
            h.popleft()
    for c in [c for c in M["hist"] if c not in snap]:
        M["hist"].pop(c, None)
# ---------- сделки на бумаге ----------
def entry_px(q, d):
    return q["ask"] if d > 0 else q["bid"]               # лонг покупаем по ask, шорт продаём по bid

def exit_px(q, d):
    return q["bid"] if d > 0 else q["ask"]

def ret_of(d, px, ex):
    return d * (ex / px - 1)

def pick_ctl(snap, coin, k, ok=lambda q: True):
    pool = [c for c, q in snap.items() if c != coin and q["vol"] >= MIN_VOL and ok(q)
            and not M["ctr"].get(c, {}).get("del")]
    return random.sample(pool, min(k, len(pool)))

def fair_of(q, ref):
    return q["index"] if ref == "index" else q["mark"]

def open_prem(now, snap, coin, side, prem, ref="mark"):
    q, d = snap[coin], -side                           # премия (+1) → шорт (−1); дисконт (−1) → лонг (+1)
    ctl = pick_ctl(snap, coin, 1, lambda x: abs(x["last"] / x["mark"] - 1) < 0.002)
    e = {"id": f"p:{coin}:{ref}:{now}", "type": "премия" if side > 0 else "дисконт", "ref": ref, "coin": coin, "t": now,
         "d": d, "fair": fair_of(q, ref),
         "px": entry_px(q, d), "mark": q["mark"], "index": q["index"], "prem": round(prem, 5),
         "spread": round(q["ask"] / q["bid"] - 1, 5), "book": q["book"], "vol": round(q["vol"]), "ch1": ch1(coin, now),
         "mk": M.get("mk"), "fund": q["fund"], "best": 0.0, "worst": 0.0, "last_t": now,
         "ctl": [{"coin": c, "px": entry_px(snap[c], d)} for c in ctl]}
    S["open"].append(e)
    M["cool"][f"{coin}|{ref}"] = now

def open_fund(now, snap, coin, nx):
    q = snap[coin]
    d = -1 if q["fund"] > 0 else 1                     # плюс — платят лонги, получают шорты
    ctl = pick_ctl(snap, coin, 2, lambda x: abs(x["fund"]) < 0.0005)
    e = {"id": f"f:{coin}:{nx}", "type": "фандинг", "coin": coin, "t": now, "apply": nx, "d": d,
         "px": entry_px(q, d), "rate": q["fund"], "rate0": q["fund"],
         "grp": "≥0,2%" if abs(q["fund"]) >= F_IN else "0,1–0,2%", "spread": round(q["ask"] / q["bid"] - 1, 5),
         "book": q["book"], "vol": round(q["vol"]), "mk": M.get("mk"), "last_t": now,
         "ctl": [{"coin": c, "px": entry_px(snap[c], d), "rate": snap[c]["fund"]} for c in ctl]}
    S["open"].append(e)
    M["fseen"][f"{coin}:{nx}"] = now

def close(e, now, out, snap):
    e["out"], e["t_close"] = out, now
    e["min"] = round((now - e["t"]) / 60)
    if out not in ("перерыв", "нет_цены"):
        for c in e.get("ctl", []):
            q = snap.get(c["coin"])
            c["r"] = None if not q else round(ret_of(e["d"], c["px"], exit_px(q, e["d"]))
                                              - (e["d"] * c.get("rate", 0) if e["type"] == "фандинг" else 0), 5)
    S["done"].append(e)

# ---------- (v1.2) путь цены после разницы к индексу ----------
def new_path(q):
    return {"a0": q["ask"], "b0": q["bid"], "L": {"up": {}, "dn": {}}, "S": {"up": {}, "dn": {}}, "r": {}}

def path_step(p, q, m):
    rl, rs = q["bid"] / p["a0"] - 1, 1 - q["ask"] / p["b0"]   # лонг: купили по ask, продаём по bid; шорт — наоборот
    for k, r in (("L", rl), ("S", rs)):
        for x in LV:
            kx = f"{x:g}"
            if r >= x and kx not in p[k]["up"]:
                p[k]["up"][kx] = m
            if r <= -x and kx not in p[k]["dn"]:
                p[k]["dn"][kx] = m
    for t in (15, 30, 60):
        if m >= t and str(t) not in p["r"]:
            p["r"][str(t)] = [round(rl, 5), round(rs, 5)]

def open_path(now, snap, coin, side, dev):
    q = snap[coin]
    ctl = pick_ctl(snap, coin, 1, lambda x: x["index"] and abs(x["last"] / x["index"] - 1) < 0.002)
    S["paths"].append({"id": f"w:{coin}:{now}", "coin": coin, "t": now, "side": side, "dev": round(dev, 5),
                       "vol": round(q["vol"]), "spread": round(q["ask"] / q["bid"] - 1, 5), "mk": M.get("mk"),
                       "ch1": ch1(coin, now), "last_t": now, "p": new_path(q),
                       "ctl": {"coin": ctl[0], "p": new_path(snap[ctl[0]])} if ctl else None})

def track_paths(now, snap):
    keep = []
    for w in S["paths"]:
        q = snap.get(w["coin"])
        out = "перерыв" if now - w["last_t"] > GAP else "нет_цены" if not q else None
        if not out:
            w["last_t"] = now
            m = round((now - w["t"]) / 60)
            path_step(w["p"], q, m)
            c = w.get("ctl")
            if c and snap.get(c["coin"]):
                path_step(c["p"], snap[c["coin"]], m)
            out = "ok" if m >= 60 else None
        if out:
            w["out"] = out
            S["pdone"].append(w)
        else:
            keep.append(w)
    S["paths"] = keep
    if len(S["pdone"]) > PATH_MAX:
        S["pdone"] = S["pdone"][-PATH_MAX:]

def track(now, snap):
    keep = []
    for e in S["open"]:
        q = snap.get(e["coin"])
        if now - e["last_t"] > GAP:
            close(e, now, "перерыв", snap)
            continue
        if not q:
            close(e, now, "нет_цены", snap)
            continue
        e["last_t"] = now
        ex = exit_px(q, e["d"])
        r = ret_of(e["d"], e["px"], ex)
        if e["type"] == "фандинг":
            if now < e["apply"]:
                e["rate"] = q["fund"]                  # ставка, ближайшая к выплате
            elif "r_at" not in e:
                e["r_at"] = round(r, 5)                # ход цены сразу после выплаты
            if now >= e["apply"] + F_AFTER:
                e["r"], e["got"] = round(r, 5), round(-e["d"] * e["rate"], 6)
                close(e, now, "по_времени", snap)
                continue
            keep.append(e)
            continue
        e["best"], e["worst"] = round(max(e["best"], r), 5), round(min(e["worst"], r), 5)
        fair = fair_of(q, e.get("ref", "mark")) or q["mark"]
        pe = ex / fair - 1                             # разница цены выхода и справедливой
        back = pe <= P_OUT if e["d"] < 0 else pe >= -P_OUT
        wider = pe >= P_STOP if e["d"] < 0 else pe <= -P_STOP
        out = ("возврат" if back else "стоп_премия" if wider else "стоп_цена" if r <= -PX_STOP
               else "время" if now - e["t"] >= HOLD_P * 60 else None)
        if out:
            e["r"], e["exit"], e["mark_out"], e["fair_out"] = round(r, 5), ex, q["mark"], fair
            close(e, now, out, snap)
        else:
            keep.append(e)
    S["open"] = keep

def scan(now, snap, ctr):
    busy_p = {f"{e['coin']}|{e.get('ref', 'mark')}" for e in S["open"] if e["type"] != "фандинг"}
    busy_f = {e["coin"] for e in S["open"] if e["type"] == "фандинг"}
    gap = now - M["last_t"] > 150                      # пропуск минут — счётчики «3 минуты подряд» заново
    dev = {r: 0 for r in REFS}
    for c, q in snap.items():
        if q["vol"] < MIN_VOL or ctr.get(c, {}).get("del"):
            for r in REFS:
                M["streak"].pop(f"{c}|{r}", None)
            continue
        for ref in REFS:
            fair, key = fair_of(q, ref), f"{c}|{ref}"
            if not fair:
                M["streak"].pop(key, None)
                continue
            up, dn = q["bid"] / fair - 1, q["ask"] / fair - 1
            if up >= P_IN or dn <= -P_IN:
                dev[ref] += 1
            side, prem = (1, up) if P_IN <= up < P_STOP else (-1, dn) if -P_STOP < dn <= -P_IN else (0, 0.0)
            s0 = M["streak"].get(key, (0, 0))
            n = s0[1] + 1 if side and s0[0] == side and not gap else (1 if side else 0)
            M["streak"][key] = (side, n)
            if (side and n >= STREAK and key not in busy_p and now - M["cool"].get(key, 0) >= COOL
                    and len(S["open"]) < MAX_OPEN):
                h = ch1(c, now)
                if h is not None and ((side > 0 and h < PUMP1H) or (side < 0 and h > -PUMP1H)):
                    open_prem(now, snap, c, side, prem, ref)
                    busy_p.add(key)
                    if ref == "index":                 # (v1.2) и замер пути цены на 60 мин
                        open_path(now, snap, c, side, prem)
        nx = next_apply(ctr.get(c, {}), now) if c in ctr else None
        if (nx and 0 < nx - now <= F_BEFORE and abs(q["fund"]) >= F_LOW and f"{c}:{nx}" not in M["fseen"]
                and c not in busy_f):
            open_fund(now, snap, c, nx)
            busy_f.add(c)
    M["now_dev"] = dev                                 # (v1.1) сколько монет прямо сейчас с разницей 0,7%+
    dv = S.setdefault("dev", {"min": 0, "mark": 0, "index": 0})
    dv["min"] += 1
    for r in REFS:
        dv[r] += dev[r]
    for k in [k for k, t in M["fseen"].items() if now - t > 86400]:
        M["fseen"].pop(k, None)

def step(now, snap):
    ctr = contracts(now)                               # запрос к бирже — до блокировки, чтобы страницы не ждали
    with LOCK:
        M["mk"] = market(snap)
        bk = [q["book"] for q in snap.values() if q["vol"] >= MIN_VOL]
        M["book_share"] = round(100 * sum(bk) / len(bk)) if bk else None
        M["snap_n"] = len(snap)
        track(now, snap)
        track_paths(now, snap)
        hist_step(now, snap)
        scan(now, snap, ctr)
        M["last_t"] = now
        if len(S["done"]) > DONE_MAX:
            S["done"] = S["done"][-DONE_MAX:]
# ---------- итоги ----------
def usd(r):
    return MARGIN * LEV * r

def ctl_r(e):
    v = [c["r"] for c in e.get("ctl", []) if c.get("r") is not None]
    return sum(v) / len(v) if v else None

def summ(vals, mins=None, days=None):
    n = len(vals)
    if not n:
        return {"n": 0}
    m = sum(vals) / n
    se = statistics.stdev(vals) / math.sqrt(n) if n > 1 else None
    out = {"n": n, "usd": round(m, 2), "pm": None if se is None else round(se, 2), "сумма": round(sum(vals), 2)}
    if mins:
        out["медиана_мин"] = round(statistics.median(mins))
    if days:
        out["в_день"] = round(sum(vals) / days, 2)
    return out

def deals(kind_set, since):
    with LOCK:
        done = list(S["done"])
    return [e for e in done if e["type"] in kind_set and e.get("out") not in ("перерыв", "нет_цены")
            and e["t"] >= since and e.get("r") is not None]

def prem_stats(es, days=None):
    if not es:
        return {"n": 0}
    s = summ([usd(e["r"] - FEE) for e in es], [e["min"] for e in es], days)
    n = len(es)
    s["возврат_%"] = round(100 * sum(1 for e in es if e["out"] == "возврат") / n)
    s["стопов_%"] = round(100 * sum(1 for e in es if e["out"].startswith("стоп")) / n)
    s["по_времени_%"] = round(100 * sum(1 for e in es if e["out"] == "время") / n)
    return s

def fund_stats(es, days=None):
    if not es:
        return {"n": 0}
    s = summ([usd(e["r"] + e["got"] - FEE) for e in es], [e["min"] for e in es], days)
    q = [usd(e["r_at"] + e["got"] - FEE) for e in es if e.get("r_at") is not None]
    s["выход_сразу_после_выплаты"] = summ(q)
    s["фандинг_%"] = round(100 * sum(e["got"] for e in es) / len(es), 3)
    s["цена_%"] = round(100 * sum(e["r"] for e in es) / len(es), 3)
    return s

def ctl_stats(es):
    return summ([usd(c - FEE) for c in (ctl_r(e) for e in es) if c is not None])

def verdict(s, c, need):
    if s.get("n", 0) < need:
        return f"пока не доказано: сделок {s.get('n', 0)} из {need} нужных"
    if c.get("n", 0) < 30 or c.get("pm") is None or s.get("pm") is None:
        return "пока не доказано: мало случайных монет для сравнения"
    gap, pm = s["usd"] - c["usd"], math.hypot(s["pm"], c["pm"])
    if s["usd"] <= 0:
        return f"не работает: в среднем {s['usd']:+.2f}$ на сделку"
    if gap < 2 * pm:
        return f"не доказано: лучше случайных на {gap:+.2f}$, а погрешность ±{pm:.2f}$ — разница может быть случайной"
    return f"ДОКАЗАНО: лучше случайных на {gap:+.2f}$ на сделку (погрешность ±{pm:.2f}$)"

def prem_block(P, days, hyp):
    big = lambda e: abs(e["prem"]) >= 0.015
    sp, cp = prem_stats(P, days), ctl_stats(P)
    return {"гипотеза": hyp, "итог": sp, "случайные": cp, "вывод": verdict(sp, cp, N_PROOF_P),
            "по_типу": {k: prem_stats([e for e in P if e["type"] == k]) for k in ("премия", "дисконт")},
            "по_размеру": {"0,7–1,5%": prem_stats([e for e in P if not big(e)]),
                           "1,5–2%": prem_stats([e for e in P if big(e)])},
            "по_обороту": {"до $300 тыс": prem_stats([e for e in P if e["vol"] < 300000]),
                           "от $300 тыс": prem_stats([e for e in P if e["vol"] >= 300000])},
            "по_рынку": {k: prem_stats([e for e in P if regime(e.get("mk")) == k]) for k in ("падает", "ровный", "растёт")},
            "без_стакана": prem_stats([e for e in P if not e["book"]])}

def p_out(p, k, tp, sl):
    """Итог сделки по записанному пути: k — "L"/"S"; тейк и стоп в одну минуту — считаем стоп. tp=None — держать 60 мин."""
    if tp:
        a, b = p[k]["up"].get(f"{tp:g}"), p[k]["dn"].get(f"{sl:g}")
        if a is not None and (b is None or a < b):
            return tp
        if b is not None:
            return -sl
    r = p["r"].get("60")
    return None if r is None else r[0 if k == "L" else 1]

def path_block():
    """(v1.2) «По ходу перпа» (выше индекса → лонг, ниже → шорт) и «против» — по записанным путям цены."""
    fp = S.get("fixed_p") or 0
    with LOCK:
        W = [w for w in S["pdone"] if w.get("out") == "ok" and w["t"] >= fp]
    fol = lambda w: "L" if w["side"] > 0 else "S"
    agn = lambda w: "S" if w["side"] > 0 else "L"
    def st(ws, kf, tp, sl, ctl=False):
        vals = []
        for w in ws:
            p = (w.get("ctl") or {}).get("p") if ctl else w["p"]
            o = p_out(p, kf(w), tp, sl) if p else None
            if o is not None:
                vals.append(usd(o - FEE))
        return summ(vals)
    combos = list(PCOMBO) + [(None, None)]
    nm = lambda tp, sl: f"{tp * 100:g}/{sl * 100:g}" if tp else "60мин"
    up = [w for w in W if w["side"] > 0]
    dn = [w for w in W if w["side"] < 0]
    return {"с": lt(fp) if fp else None, "случаев": len(W), "перп_выше": len(up), "перп_ниже": len(dn),
            "по_ходу": {nm(a, b): st(W, fol, a, b) for a, b in combos},
            "против": {nm(a, b): st(W, agn, a, b) for a, b in combos},
            "случайные_по_ходу": {nm(a, b): st(W, fol, a, b, True) for a, b in combos},
            "по_ходу_только_лонг": {nm(a, b): st(up, fol, a, b) for a, b in combos},
            "по_ходу_только_шорт": {nm(a, b): st(dn, fol, a, b) for a, b in combos}}

def table():
    fx = S.get("fixed_t") or 0
    fi = S.get("fixed_i") or fx                        # (v1.1) у варианта «к индексу» свой отсчёт
    now = time.time()
    dd = lambda t: max((now - t) / 86400, 1 / 24) if t else None
    P = deals({"премия", "дисконт"}, 0)
    PM = [e for e in P if e.get("ref", "mark") == "mark" and e["t"] >= fx]
    PI = [e for e in P if e.get("ref") == "index" and e["t"] >= fi]
    F = deals({"фандинг"}, fx)
    F2 = [e for e in F if e["grp"] == "≥0,2%"]
    sf, cf = fund_stats(F2, dd(fx)), ctl_stats(F2)
    dv = dict(S.get("dev") or {})
    with LOCK:
        opn = list(S["open"])
    rule = "{} 0,7–2% 3 мин подряд → против отклонения; выход — разница ≤0,15%; стоп — разница 2%+ или цена −3%; до 60 мин"
    return {"версия": VERSION, "зафиксирована": lt(fx) if fx else None, "к_индексу_с": lt(fi) if fi else None,
            "ставка_$": MARGIN, "плечо": LEV,
            "комиссия_%": round(FEE * 100, 2), "ошибка": M["err"] or M.get("err_c") or None, "монет_в_ответе": M["snap_n"],
            "цены_из_стакана_%": M["book_share"], "рынок_сутки": M.get("mk"),
            "разница_0,7%+_сейчас": dict(M.get("now_dev") or {}),
            "разница_0,7%+_в_среднем_монет_в_минуту": {r: round(dv.get(r, 0) / dv["min"], 2) for r in REFS}
            if dv.get("min") else None,
            "открыто": {"премия": sum(1 for e in opn if e["type"] != "фандинг"),
                        "фандинг": sum(1 for e in opn if e["type"] == "фандинг")},
            "премия": prem_block(PM, dd(fx), rule.format("цена Gate ушла от справедливой (mark) на")),
            "премия_индекс": prem_block(PI, dd(fi), rule.format("цена Gate ушла от индекса спота на")),
            "путь_после_разницы": path_block(),
            "фандинг": {"гипотеза": "за 15 мин до выплаты фандинг 0,2%+ → сторона получателя, выход через 5 мин после",
                        "итог": sf, "случайные": cf, "вывод": verdict(sf, cf, N_PROOF_F),
                        "0,1–0,2%_справка": fund_stats([e for e in F if e["grp"] != "≥0,2%"]),
                        "по_стороне": {"шорт (фандинг +)": fund_stats([e for e in F2 if e["d"] < 0]),
                                       "лонг (фандинг −)": fund_stats([e for e in F2 if e["d"] > 0])}}}

def pline(name, x):
    if not x or not x.get("n"):
        return f"  {name}: сделок пока нет"
    pm = f" ±{x['pm']:.2f}" if x.get("pm") is not None else ""
    t = f"  {name}: {x['n']} сд., в среднем {x['usd']:+.2f}${pm}"
    if "возврат_%" in x:
        t += f", возврат {x['возврат_%']}%, стопов {x['стопов_%']}%, по времени {x['по_времени_%']}%"
    if "медиана_мин" in x:
        t += f", половина закрылась за {x['медиана_мин']} мин"
    if "в_день" in x:
        t += f", итог {x['сумма']:+.0f}$ (≈ {x['в_день']:+.1f}$ в день)"
    return t

def short(x):
    return f"{x['n']} сд. {x['usd']:+.2f}$" if x.get("n") else "нет"

def prem_lines(P, title):
    return [title, pline("сделки", P["итог"]), pline("случайные монеты на то же время", P["случайные"]),
            "  премия/дисконт: " + " · ".join(f"{k} {short(x)}" for k, x in P["по_типу"].items())
            + " | размер: " + " · ".join(f"{k} {short(x)}" for k, x in P["по_размеру"].items())
            + " | оборот: " + " · ".join(f"{k} {short(x)}" for k, x in P["по_обороту"].items()),
            "  по рынку: " + " · ".join(f"{k} {short(x)}" for k, x in P["по_рынку"].items()),
            "  Вывод: " + P["вывод"]]

def text():
    T = table()
    F = T["фандинг"]
    nd, av = T["разница_0,7%+_сейчас"], T["разница_0,7%+_в_среднем_монет_в_минуту"]
    L = [f"💹 {VERSION} — премия перпа и фандинг (бумага), {lt()}",
         f"Проверка с {T['зафиксирована'] or '—'} (к индексу — с {T['к_индексу_с'] or '—'}); ставка ${MARGIN:g} × {LEV:g}, "
         f"вход и выход по ценам стакана (спред учтён) + комиссия {T['комиссия_%']}%. Открыто сейчас: премия "
         f"{T['открыто']['премия']}, фандинг {T['открыто']['фандинг']}." + (f" ⚠️ {T['ошибка']}" if T["ошибка"] else ""),
         f"Монет с разницей 0,7%+ сейчас: к mark {nd.get('mark', '—')}, к индексу {nd.get('index', '—')}"
         + (f"; в среднем за минуту: к mark {av['mark']}, к индексу {av['index']}" if av else "")
         + (f" | цены из стакана: {T['цены_из_стакана_%']}% монет" if T["цены_из_стакана_%"] is not None else "")]
    L += prem_lines(T["премия"], f"1а) Премия к mark: {T['премия']['гипотеза']}. Перп дешевле — лонг.")
    L += prem_lines(T["премия_индекс"], f"1б) Премия к индексу спота: {T['премия_индекс']['гипотеза']}.")
    W = T["путь_после_разницы"]
    L.append(f"1в) Цена 60 мин после разницы к индексу (замер с {W['с'] or '—'}, без сделок): {W['случаев']} случаев "
             f"(перп выше индекса {W['перп_выше']}, ниже {W['перп_ниже']}); тейк/стоп в %, $ на сделку:")
    if W["случаев"]:
        for k, title in (("по_ходу", "по ходу перпа (выше → лонг, ниже → шорт)"), ("против", "против перпа"),
                         ("случайные_по_ходу", "случайные монеты"), ("по_ходу_только_лонг", "по ходу, только лонг"),
                         ("по_ходу_только_шорт", "по ходу, только шорт")):
            L.append(f"  {title}: " + " · ".join(f"{c} {x['usd']:+.2f}$" if x.get("n") else f"{c} —"
                                                for c, x in W[k].items()))
    L.append(f"2) Фандинг: {F['гипотеза']}.")
    f2 = F["итог"]
    L.append(pline("≥0,2%", f2))
    if f2.get("n"):
        q = f2["выход_сразу_после_выплаты"]
        L.append(f"    из них: фандинг в среднем +{f2['фандинг_%']}%, цена {f2['цена_%']:+.3f}%; "
                 f"выход сразу после выплаты: {short(q)}")
    L.append(pline("0,1–0,2% (справка)", F["0,1–0,2%_справка"]))
    L.append(pline("случайные монеты в то же окно", F["случайные"]))
    L.append("  Вывод: " + F["вывод"])
    return "\n".join(L)
# ---------- запуск, база, Telegram ----------
def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'prem_v1'", fetch=True)
    with LOCK:
        S.update(json.loads(r[0][0]) if r else {})
        S.setdefault("open", [])
        S.setdefault("done", [])
        S.setdefault("paths", [])
        S.setdefault("pdone", [])
        if not S.get("fixed_p"):                       # (v1.2) замер пути — с первого запуска v1.2
            S["fixed_p"] = int(time.time())
        if not S.get("fixed_t"):                       # первый запуск — с этой минуты честный зачёт
            S["fixed_t"] = int(time.time())
        if not S.get("fixed_i"):                       # (v1.1) вариант «к индексу» — свой отсчёт с первого запуска v1.1
            S["fixed_i"] = int(time.time())
    save()
    S["loaded"] = 1

def save():
    with LOCK:
        js = json.dumps({k: v for k, v in S.items() if k != "loaded"})
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('prem_v1', %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (js,))
    M["saved_t"] = time.time()

def send(txt):
    part = ""
    for ln in txt.split("\n"):
        if part and len(part) + len(ln) + 1 > 3800:
            mm.tg(part)
            part = ""
        part = (part + "\n" + ln) if part else ln[:3800]
    if part:
        mm.tg(part)

def daily(now):
    if not DAILY or not S.get("loaded"):
        return
    d, hm = lt(now, "%Y-%m-%d"), lt(now, "%H:%M")
    if hm >= f"{HOUR:02d}:10" and S.get("sent") != d:
        S["sent"] = d
        save()
        send(text())

def loop():
    time.sleep(240)                                    # после остальных модулей
    while not S.get("loaded"):
        try:
            load()
        except Exception:
            log.exception("xprem load")
            time.sleep(60)
    while True:
        t0 = time.time()
        try:
            step(t0, fetch_tickers())
            M["err"] = ""
        except Exception as e:
            M["err"] = f"цены Gate: {str(e)[:120]}"
            log.exception("xprem step")
        try:
            daily(t0)
            if t0 - M["saved_t"] >= 600:
                save()
        except Exception:
            log.exception("xprem save")
        time.sleep(max(5, 60 - (time.time() - t0)))

def ok():
    return mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN

@mm.app.route("/prem")
def prem_page():
    if not ok():
        return "forbidden", 403
    return jsonify(table())

@mm.app.route("/prem/brief")
def prem_brief():
    if not ok():
        return "forbidden", 403
    txt = text()
    if request.args.get("tg") == "1":
        send(txt)
    return txt, 200, {"Content-Type": "text/plain; charset=utf-8"}

@mm.app.route("/prem/raw")
def prem_raw():
    """Все события за &days= (по умолчанию 7) — для разбора."""
    if not ok():
        return "forbidden", 403
    since = time.time() - fnum(request.args.get("days"), 7) * 86400
    with LOCK:                                         # копия — открытые сделки меняются каждую минуту
        ev = json.loads(json.dumps([e for e in S["done"] + S["open"] if e["t"] >= since]))
        pw = json.loads(json.dumps([w for w in S["pdone"] + S["paths"] if w["t"] >= since]))
    return jsonify({"версия": VERSION, "событий": len(ev), "события": ev, "путей": len(pw), "пути": pw})

if ON:
    threading.Thread(target=loop, daemon=True).start()
