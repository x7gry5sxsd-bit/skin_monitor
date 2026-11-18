# xpolyan.py — РАСЧЁТ ЧЕСТНЫХ ВЕРОЯТНОСТЕЙ для ценовых крипто-рынков Polymarket (без сети, только математика и разбор).
#   Модель одна на все темы: цена монеты — случайное блуждание в логарифмах без сноса с годовой волатильностью sig.
#   • «above K» в момент T:            P(S_T > K)
#   • «price on» (корзины a–b):         P(a < S_T ≤ b)
#   • «hit / reach / dip» до T:         P(коснётся) = 2 × P(S_T за K); если уже коснулась с начала окна — 1
#   • «hit A or B first»:               P(сначала нижняя) = ln(U/S) / ln(U/L)
#   • «Up or Down» (интервал идёт):     P(вверх) = P(S_конец ≥ S_начало | цена сейчас)
#   Остальные темы (FDV после запуска, запуск токена, законы, ETF) — модели нет, показываем только цену рынка.
#   VERDICT — что показала проверка на закрытых рынках 05–11.2026 (14 тыс. рынков).
import re, math, json, datetime as dt
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
COIN = {"bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL", "xrp": "XRP", "dogecoin": "DOGE", "bnb": "BNB",
        "hyperliquid": "HYPE", "hype": "HYPE", "zcash": "ZEC", "litecoin": "LTC", "cardano": "ADA", "sui": "SUI",
        "chainlink": "LINK", "avalanche": "AVAX", "monero": "XMR", "pepe": "PEPE", "shiba inu": "SHIB", "tron": "TRX"}
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
TYPE_RU = {"hit_day": "касание за день", "hit_week": "касание за неделю", "hit_month": "касание за месяц", "hit_long": "касание до даты",
           "above": "выше K в момент", "range": "цена в диапазоне", "first": "что раньше", "updown": "вверх или вниз", "other": "без модели"}

def verdict(kind, coin):
    if kind == "hit_day" and coin in ("ETH", "SOL", "XRP"):
        return "проверено: перевес есть (толпа переплачивает за ДА)"
    if kind in ("hit_day", "above", "hit_week", "hit_month"):
        return "проверено: рынок точный, перевеса нет"
    return "не проверено — копим бумагу"

def ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def num(s):
    s = (s or "").replace(",", "").replace("$", "").strip().lower()
    m = re.match(r"^([\d\.]+)\s*(k|m|b)?", s)
    if not m:
        return None
    return float(m.group(1)) * {"k": 1e3, "m": 1e6, "b": 1e9}.get(m.group(2) or "", 1)

def ts(s):
    return int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()) if s else None

def coin_of(title):
    t = title.lower()
    for k in sorted(COIN, key=len, reverse=True):
        if re.search(r"\b" + re.escape(k) + r"\b", t):
            return COIN[k]
    return None

def classify(title):
    t = title
    if "Up or Down" in t: return "updown"
    if re.search(r"above ___ on [A-Z]", t) and "FDV" not in t: return "above"
    if re.search(r"price on [A-Z]", t): return "range"
    if re.search(r"What price will .* hit (on )?[A-Z][a-z]+ \d+\?", t): return "hit_day"
    if re.search(r"What price will .* hit [A-Z][a-z]+ \d+-\d+\?", t) or re.search(r"hit [A-Z][a-z]+ \d+-[A-Z][a-z]+ \d+\?", t): return "hit_week"
    if re.search(r"What price will .* hit in [A-Z][a-z]+\?", t): return "hit_month"
    if re.search(r"What price will .* hit (in|before) \d{4}\?", t) or re.search(r"When will .* hit \$", t): return "hit_long"
    if re.search(r"hit \$[\d,\.]+k? or \$[\d,\.]+k? first", t): return "first"
    return "other"

def tail(z):
    return 1 - ncdf(z)

def p_above(S, K, sig, sec):
    if sec <= 0: return 1.0 if S > K else 0.0
    s = sig * math.sqrt(sec / (365 * 86400))
    return tail(math.log(K / S) / s)

def p_touch(S, K, up, sig, sec):
    if (up and S >= K) or (not up and S <= K): return 1.0
    if sec <= 0: return 0.0
    s = sig * math.sqrt(sec / (365 * 86400))
    z = math.log(K / S) / s if up else math.log(S / K) / s
    return min(1.0, 2 * tail(z))

def updown_minutes(title):
    m = re.search(r"(\d{1,2})(?::(\d\d))?(AM|PM)-(\d{1,2})(?::(\d\d))?(AM|PM)", title)
    if m:
        def mins(h, mi, ap):
            h = int(h) % 12 + (12 if ap == "PM" else 0)
            return h * 60 + int(mi or 0)
        d = (mins(m.group(4), m.group(5), m.group(6)) - mins(m.group(1), m.group(2), m.group(3))) % 1440
        return d or 1440
    if re.search(r", \d{1,2}(AM|PM) ET", title): return 60
    if re.search(r"Up or Down on [A-Z]", title): return 1440
    return None

def window_start(kind, ev, m, end):
    """Начало окна для касания (сек). None — окно неизвестно. Страйки, добавленные позже, считаются с момента создания рынка."""
    created = ts(m.get("startDate") or m.get("createdAt"))
    if created and "creation of this market" in (m.get("description") or ""):
        nominal = window_start(kind, ev, {}, end) if kind not in ("hit_long", "first") else None
        return max(created, nominal) if nominal else created
    if kind == "hit_day": return end - 86400
    if kind == "hit_week": return end - 7 * 86400
    if kind == "hit_month":
        e = dt.datetime.fromtimestamp(end - 3600, ET)
        return int(dt.datetime(e.year, e.month, 1, tzinfo=ET).timestamp())
    if kind in ("hit_long", "first"):
        return ts(m.get("startDate") or ev.get("startDate") or m.get("createdAt"))
    return None

def analyze(ev, now, px):
    """ev — событие gamma; px(coin) → объект цен с методами now(), hilo(start), at(t), sig().
    Возвращает строки: тема, вопрос, рынок, наша вероятность, перевес."""
    title = ev.get("title") or ""
    kind = classify(title)
    coin = coin_of(title)
    out = []
    P = px(coin) if (coin and kind != "other") else None
    for m in ev.get("markets") or []:
        if m.get("closed") or not m.get("active", True):
            continue
        try:
            outs = json.loads(m.get("outcomes") or "[]")
        except Exception:
            outs = []
        end = ts(m.get("endDate") or ev.get("endDate"))
        if not end or end <= now:
            continue
        bid, ask = m.get("bestBid"), m.get("bestAsk")
        row = {"kind": kind, "coin": coin or "", "event": title, "q": m.get("question") or title, "g": m.get("groupItemTitle") or "",
               "out0": outs[0] if outs else "Yes", "out1": outs[1] if len(outs) > 1 else "No", "bid": bid, "ask": ask, "end": end, "h": round((end - now) / 3600, 1),
               "slug": ev.get("slug"), "mid": m.get("id"), "p": None, "note": ""}
        sec = end - now
        try:
            if P is None or not P.ok():
                row["note"] = "модели нет — только цена рынка" if kind == "other" else "нет цены монеты"
                out.append(row)
                continue
            S, sig = P.now(), P.sig()
            g = row["g"]
            if kind == "above":
                K = num(g) or num(re.search(r"above \$?([\d,\.]+k?)", row["q"]).group(1))
                row["p"] = p_above(S, K, sig, sec); row["K"] = K
            elif kind == "range":
                g2 = g.replace(" ", "")
                if g2.startswith("<"):
                    row["p"] = 1 - p_above(S, num(g2[1:]), sig, sec)
                elif g2.startswith(">"):
                    row["p"] = p_above(S, num(g2[1:]), sig, sec)
                else:
                    a, b = g2.split("-")[:2]
                    row["p"] = max(0.0, p_above(S, num(a), sig, sec) - p_above(S, num(b), sig, sec))
            elif kind.startswith("hit"):
                if kind == "hit_long" and g.lower().startswith("by "):
                    K = num(re.search(r"\$([\d,\.]+k?)", title).group(1)); up = True if K >= S else False
                    if "dip" in row["q"].lower(): up = False
                else:
                    K = num(g.lstrip("↑↓ ")); up = not g.strip().startswith("↓")
                    if "dip" in row["q"].lower(): up = False
                start = window_start(kind, ev, m, end)
                hi, lo = P.hilo(start) if start else (None, None)
                if hi is not None and ((up and hi >= K) or (not up and lo <= K)):
                    row["p"] = 1.0; row["note"] = "уже коснулась"
                else:
                    row["p"] = p_touch(S, K, up, sig, sec)
                row["K"] = K
            elif kind == "first":
                a, b = [num(x) for x in re.findall(r"\$([\d,\.]+k?)", title)[:2]]
                L, U = min(a, b), max(a, b)
                start = window_start(kind, ev, m, end)
                hi, lo = P.hilo(start) if start else (None, None)
                if hi is not None and hi >= U: pl = 0.0
                elif lo is not None and lo <= L: pl = 1.0
                else: pl = min(1.0, max(0.0, math.log(U / S) / math.log(U / L)))
                first_is_low = num(row["out0"]) == L if num(row["out0"]) else True
                row["p"] = pl if first_is_low else 1 - pl
            elif kind == "updown":
                mins = updown_minutes(title)
                if not mins or mins < 60:
                    continue                                    # 5- и 15-минутные — это гонка роботов, не наш рынок
                st = end - mins * 60
                if st > now:
                    row["p"] = 0.5; row["note"] = "интервал ещё не начался"
                else:
                    S0 = P.at(st)
                    if not S0:
                        row["note"] = "нет цены начала"; out.append(row); continue
                    s = sig * math.sqrt(sec / (365 * 86400))
                    pu = 1.0 if (sec <= 0 and S >= S0) else ncdf(math.log(S / S0) / s) if s > 0 else (1.0 if S >= S0 else 0.0)
                    row["p"] = pu if str(row["out0"]).lower() == "up" else 1 - pu
            else:
                row["note"] = "модели нет — только цена рынка"
        except Exception as e:
            row["note"] = f"не разобрал: {str(e)[:60]}"
            row["p"] = None
        out.append(row)
    return out

def labels(row):
    """Подписи сторон: для «ДА/НЕТ» — ДА и НЕТ, для «Up or Down» и «что раньше» — названия исходов."""
    o0, o1 = row.get("out0") or "Yes", row.get("out1") or "No"
    if row["kind"] == "updown":
        return ("ВВЕРХ" if o0.lower() == "up" else "ВНИЗ"), ("ВНИЗ" if o0.lower() == "up" else "ВВЕРХ")
    if row["kind"] == "first":
        return f"сначала {o0}", f"сначала {o1}"
    return "ДА", "НЕТ"

def edge(row, fee):
    """Лучшая сторона с комиссией тейкера → (подпись, 0/1 — первый/второй исход, цена с комиссией, перевес) или Nones."""
    p, bid, ask = row.get("p"), row.get("bid"), row.get("ask")
    if p is None:
        return None, None, None, None
    l0, l1 = labels(row)
    best = (None, None, None, -9.0)
    try:
        if ask is not None and 0 < float(ask) < 1:
            a = float(ask); e = p - (a + fee(a))
            best = (l0, 0, a + fee(a), e)
        if bid is not None and 0 < float(bid) < 1:
            n = 1 - float(bid); e = (1 - p) - (n + fee(n))
            if e > best[3]:
                best = (l1, 1, n + fee(n), e)
    except (TypeError, ValueError):
        return None, None, None, None
    if best[0] is None:
        return None, None, None, None
    return best[0], best[1], round(best[2], 4), round(best[3], 4)
