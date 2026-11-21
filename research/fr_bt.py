"""Лонг после сильно отрицательного фандинга на Gate: цена + полученный фандинг − комиссии, против рынка."""
import os
HERE = os.path.dirname(os.path.abspath(__file__))

import sys, json, os, math, statistics as st, time, collections
sys.path.insert(0, HERE)
import gate, datetime as dt
FD = HERE + "/fund"
START = int(dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc).timestamp()); END = int(time.time()) // 3600 * 3600
CS = json.load(open(HERE + "/fr_list.json"))
tick = {t["contract"]: float(t.get("volume_24h_quote") or 0) for t in gate.get("/tickers")}
BASKET = [c for c in sorted(CS, key=lambda c: -tick.get(c, 0))[:60]]
CC = {}
def cd(c):
    if c not in CC: CC[c] = gate.candles(c, START - 8 * 86400, END)
    return CC[c]
def close(c, h):
    k = cd(c).get(h); return k[3] if k else None
MK = {}
def mret(t0, t1):
    if (t0, t1) not in MK:
        rs = []
        for c in BASKET:
            a, b = close(c, t0), close(c, t1)
            if a and b: rs.append(b / a - 1)
        MK[(t0, t1)] = st.median(rs) if len(rs) > 20 else None
    return MK[(t0, t1)]
FR = {c: sorted(json.load(open(f"{FD}/{c[:-5]}.json"))) for c in CS if os.path.exists(f"{FD}/{c[:-5]}.json")}
def run(thr, hold, sl=None, exit_norm=None, fee=0.003, minvol=1e6):
    out = []
    for c, rows in FR.items():
        busy = 0
        for t, r in rows:
            if t < START + 86400 or t >= END - hold * 3600 or r > thr or t < busy:
                continue
            h0 = t - t % 3600                                  # вход — по закрытию часа, в котором прошёл расчёт
            p0 = close(c, h0)
            gv = sum((cd(c).get(x) or (0,) * 5)[4] for x in range(h0 - 23 * 3600, h0 + 3600, 3600))
            if not p0 or gv < minvol:
                continue
            fu, exit_h, px1 = 0.0, None, None
            for i in range(1, hold + 1):
                k = cd(c).get(h0 + i * 3600)
                if not k: break
                if sl and k[2] / p0 - 1 <= -sl:
                    exit_h, px1 = h0 + i * 3600, p0 * (1 - sl - 0.01); break
                fu += -sum(rr for tt, rr in rows if h0 + (i - 1) * 3600 < tt <= h0 + i * 3600)   # лонг получает при r<0
                if exit_norm is not None:
                    last = [rr for tt, rr in rows if tt <= h0 + i * 3600]
                    if last and last[-1] > exit_norm:
                        exit_h, px1 = h0 + i * 3600, k[3]; break
            if exit_h is None:
                k = cd(c).get(h0 + hold * 3600)
                if not k: continue
                exit_h, px1 = h0 + hold * 3600, k[3]
            m = mret(h0, exit_h)
            if m is None: continue
            pr = px1 / p0 - 1
            out.append((t, c, pr, fu, pr + fu - fee, pr - m + fu - fee))
            busy = exit_h
    return out
def show(name, o):
    if len(o) < 10: print(name, "мало:", len(o)); return
    o.sort(); net = [x[4] for x in o]; exc = [x[5] for x in o]; h = len(o) // 2
    se = st.pstdev(net) / math.sqrt(len(net))
    bym = collections.defaultdict(float)
    for x in o: bym[time.strftime("%m", time.gmtime(x[0]))] += x[4] * 100
    print(f"{name}: n={len(o)} ({len(o)/((END-START)/86400):.1f}/сут) | цена {st.mean(x[2] for x in o)*100:+.2f}% фандинг {st.mean(x[3] for x in o)*100:+.2f}% "
          f"| ИТОГ {st.mean(net)*100:+.2f}% (t={st.mean(net)/se:.1f}) в плюсе {sum(n>0 for n in net)/len(net):.0%} | сверх рынка {st.mean(exc)*100:+.2f}% "
          f"| половины {st.mean(net[:h])*100:+.1f}/{st.mean(net[h:])*100:+.1f}% | $ по мес. на $100: " + " ".join(f"{m}:{v:+.0f}" for m, v in sorted(bym.items())))
if __name__ == "__main__":
    for thr in (-0.003, -0.005, -0.01, -0.015):
        for hold in (8, 24, 72):
            show(f"ставка ≤ {thr*100:.1f}%, держим {hold} ч", run(thr, hold))
        show(f"ставка ≤ {thr*100:.1f}%, до нормализации (>-0,05%), макс 72 ч, стоп 15%", run(thr, 72, sl=0.15, exit_norm=-0.0005))
