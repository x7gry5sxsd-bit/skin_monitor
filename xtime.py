# xtime.py — журнал реального времени переводов: «отправили → зачислили» по каждой паре бирж и сети.
#   Пишут схема 1 (по приходу монет на HTX) и магазин (по зачислению пачки в истории пополнений HTX).
#   Медиана по маршруту используется «странником» вместо табличных минут. /xfer/times?t=...
import time, json, statistics
from flask import jsonify, request
import mm_scanner as mm
import xpaper

T = {}

def _load():
    if not T:
        r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'xtime_v1'", fetch=True)
        T.update(json.loads(r[0][0]) if r else {})
        T.setdefault("rows", [])

def record(src, dst, coin, net, minutes, kind):
    """Записать реальный перевод: откуда, куда, монета, сеть, минут, чей (схема 1 / магазин)."""
    _load()
    T["rows"] = (T["rows"] + [[int(time.time()), src, dst, coin, (net or "?").upper(), round(minutes, 1), kind]])[-400:]
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('xtime_v1', %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(T),))

def stats():
    _load()
    g = {}
    for r in T["rows"]:
        g.setdefault(f"{r[1]}>{r[2]} {r[4]}", []).append((r[5], r[3]))
    out = {}
    for k, v in g.items():
        m = sorted(x[0] for x in v)
        out[k] = {"n": len(m), "median_min": round(statistics.median(m), 1), "slow_min": m[int(0.9 * (len(m) - 1))],
                  "coins": sorted({x[1] for x in v})[:5]}
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["n"]))

def eta(src, dst, net, default):
    """Реальная медиана по маршруту и сети (если замеров ≥2), иначе — табличное значение."""
    s = stats().get(f"{src}>{dst} {(net or '').upper()}")
    return s["median_min"] if s and s["n"] >= 2 else default

def text():
    s = stats()
    if not s:
        return "⏱ ПЕРЕВОДЫ: замеров пока нет"
    return "⏱ ПЕРЕВОДЫ (факт, медиана): " + ", ".join(
        f"{k} ~{v['median_min']} мин ({v['n']}, медленный {v['slow_min']})" for k, v in list(s.items())[:5])

@mm.app.route("/xfer/times")
def xfer_times():
    if not (mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN):
        return "forbidden", 403
    _load()
    return jsonify({"routes": stats(), "last": T["rows"][-15:]})
