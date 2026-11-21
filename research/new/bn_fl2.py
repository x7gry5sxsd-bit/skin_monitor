"""Запуск фьючерса Binance: подмножество «спот уже торговался на Binance» — тот же шорт на Gate (часовые свечи), и минутный профиль по споту Binance."""
import os as _os
HERE = _os.path.dirname(_os.path.abspath(__file__))
import sys, json, math, statistics as st, time, collections
sys.path.insert(0, HERE)
src = open(HERE + "/bn_fade.py").read()
exec(src.split("for kind in (")[0])
E = json.load(open(f"{D}/events.json"))
fl = [e for e in E if e["kind"] == "fut_launch"]
spot_pre = {(int(e["t"]), e["sym"]) for e in fl if "spot" in (e.get("mk") or {})}
print("запусков фьючерса всего", len(fl), "| спот уже был на Binance:", len(spot_pre))
for name, pick in (("спот уже был на Binance", lambda t, s: (int(t), s) in spot_pre), ("спота на Binance не было", lambda t, s: (int(t), s) not in spot_pre)):
    S = sorted({(t, s) for t, k, s, _ in ev if k == "запуск фьючерса" and pick(t, s)})
    for hold, sl in ((4, None), (24, None), (24, 0.2), (24, 0.3), (48, None)):
        rs = [r for r in (short(s, t, hold, sl) for t, s in S) if r]
        if len(rs) < 6: continue
        net = [r[2] for r in rs]; se = (st.pstdev(net) or 1e-9) / math.sqrt(len(net)); h = len(net) // 2
        print(f"  {name:26} шорт {hold:2}ч стоп {sl or '—':4}: n={len(rs):3} {st.mean(net)*100:+.2f}% (t={st.mean(net)/se:+.1f}) медиана {st.median(net)*100:+.2f}% в плюсе {sum(x>0 for x in net)/len(net):.0%} "
              f"| сверх рынка {st.mean(r[3] for r in rs)*100:+.2f}% | половины {st.mean(net[:h])*100:+.1f}/{st.mean(net[h:])*100:+.1f}% | худшая {min(net)*100:+.0f}%")
