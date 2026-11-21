"""Шорт на Gate после объявления Binance (запуск фьючерса / листинг на споте): 24–72 ч, против рынка, с фандингом."""
import os as _os
HERE = _os.path.dirname(_os.path.abspath(__file__))
import sys, os, re, json, math, statistics as st, time, collections, datetime as dt
sys.path.insert(0, HERE)
import gate
D = HERE + "/bn"; FD = HERE + "/fund"
arts = json.load(open(f"{D}/announcements.json"))
NOW = int(time.time()) // 3600 * 3600
T0 = NOW - 9800 * 3600                                     # глубина часовых свечей Gate
SKIP = {"USDT", "USDC", "BTC", "ETH", "BNB", "FDUSD", "EUR", "TRY", "BRL", "JPY", "USD", "BUSD", "TUSD", "AEUR", "XUSD", "USD1"}
gc_names = {c["name"][:-5] for c in gate.contracts() if c["name"].endswith("_USDT")}
ev = []
for a in arts:
    t = a["releaseDate"] / 1000
    if t < T0 + 10 * 86400 or t > NOW - 80 * 3600: continue
    ti = a["title"]
    if "Futures Will Launch" in ti and "TradFi" not in ti and "USDC" not in ti: kind = "запуск фьючерса"
    elif "Binance Will List" in ti: kind = "листинг на споте"
    elif "HODLer Airdrop" in ti: kind = "HODLer"
    else: continue
    syms = set(re.findall(r"\(([A-Z0-9]{2,12})\)", ti)) | {m[:-4] for m in re.findall(r"\b([A-Z0-9]{2,15}USDT)\b", ti)}
    for s in syms - SKIP:
        if s in gc_names: ev.append((t, kind, s, ti[:90]))
print("событий с фьючерсом на Gate:", len(ev), collections.Counter(e[1] for e in ev))
tick = {x["contract"][:-5]: float(x.get("volume_24h_quote") or 0) for x in gate.get("/tickers") if x["contract"].endswith("_USDT")}
BASKET = [c for c, v in sorted(tick.items(), key=lambda kv: -kv[1])[:60]]
CC = {}
def cd(c):
    if c not in CC: CC[c] = gate.candles(f"{c}_USDT", T0, NOW)
    return CC[c]
MK = {}
def mret(a, b):
    if (a, b) not in MK:
        rs = [cd(c)[b][3] / cd(c)[a][3] - 1 for c in BASKET if a in cd(c) and b in cd(c)]
        MK[(a, b)] = st.median(rs) if len(rs) > 20 else 0.0
    return MK[(a, b)]
def fund(coin, a, b):
    f = f"{FD}/{coin}.json"
    if not os.path.exists(f):
        rows, x = [], a - 86400
        try:
            d = gate.get("/funding_rate", contract=f"{coin}_USDT", limit=1000, **{"from": int(a - 86400), "to": int(b + 86400)})
            rows = [(int(y["t"]), float(y["r"])) for y in d]
        except Exception: pass
        return sum(r for t, r in rows if a < t <= b)
    return sum(r for t, r in json.load(open(f)) if a < t <= b)
def short(coin, t, hold, sl):
    k = cd(coin); h0 = int(t) // 3600 * 3600 + 3600          # вход — закрытие часа объявления (с опозданием до часа)
    if h0 - 3600 not in k or h0 not in k: return None
    p0 = k[h0][3]
    for i in range(1, hold + 1):
        x = k.get(h0 + i * 3600)
        if not x: return None
        if sl and x[1] / p0 - 1 >= sl:
            t1, pr = h0 + i * 3600, -(sl + 0.01); break
    else:
        t1, pr = h0 + hold * 3600, -(k[h0 + hold * 3600][3] / p0 - 1)
    fu = fund(coin, h0, t1)
    return h0, t1, pr + fu - 0.004, pr + fu - 0.004 + mret(h0, t1), fu
for kind in ("запуск фьючерса", "листинг на споте", "HODLer"):
    E = sorted({(t, s) for t, k, s, _ in ev if k == kind})
    for hold, sl in ((24, 0.15), (24, None), (48, 0.20), (72, 0.20)):
        rs = [r for r in (short(s, t, hold, sl) for t, s in E) if r]
        if len(rs) < 8: continue
        net = [r[2] for r in rs]; exc = [r[3] for r in rs]; se = st.pstdev(net) / math.sqrt(len(net)); h = len(net) // 2
        bym = collections.defaultdict(float)
        for r in rs: bym[time.strftime("%y-%m", time.gmtime(r[0]))] += r[2] * 100
        print(f"{kind:17} шорт {hold} ч, стоп {sl or '—'}: n={len(rs)} ИТОГ {st.mean(net)*100:+.2f}% (t={st.mean(net)/se:.1f}) в плюсе {sum(x>0 for x in net)/len(net):.0%} "
              f"| сверх рынка {st.mean(exc)*100:+.2f}% | фандинг {st.mean(r[4] for r in rs)*100:+.2f}% | половины {st.mean(net[:h])*100:+.1f}/{st.mean(net[h:])*100:+.1f}% "
              f"| худшая {min(net)*100:+.0f}% | $ на $100 по мес.: " + " ".join(f"{m}:{v:+.0f}" for m, v in sorted(bym.items())))
