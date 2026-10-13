# xdaily.py — итог дня в Telegram (23:00 по местному времени) + /daily?t=...&tg=1 по запросу.
#   Живая торговля, реальный баланс против старта, бумага Gate, магазин (хедж/без), многоходовки, новые кандидаты.
import os, sys, time, json, threading, logging
from datetime import datetime, timezone, timedelta
from flask import jsonify, request
import mm_scanner as mm
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

TZ = timezone(timedelta(hours=float(env("TZ_OFFSET", "8"))))
HOUR = int(env("DAILY_HOUR", "23"))
BASE = float(env("XE_BASELINE", "52.52"))          # USDT на старте живой торговли
log = logging.getLogger("xdaily")
D = {}

def mod(name):
    return sys.modules.get(name)

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'daily_v1'", fetch=True)
    if r:
        D.update(json.loads(r[0][0]))
    D.setdefault("snap", {})
    D.setdefault("sent_day", "")

def save():
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('daily_v1', %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(D),))

def collect():
    now, out = time.time(), {}
    X = mod("xexec")
    if X and X.S:
        day = [r for r in X.S.get("done", []) if now - r[0] < 86400]
        out["live"] = {"trades": len(day), "wins": sum(1 for r in day if r[-3] > 0),
                       "pnl": round(sum(r[-3] for r in day), 2), "total": round(X.S.get("pnl", 0), 2),
                       "by_coin": {}, "paused": X.S.get("paused")}
        for r in day:
            k = f"{r[1]}@{r[2]}" if len(r) > 6 else r[1]
            out["live"]["by_coin"][k] = round(out["live"]["by_coin"].get(k, 0) + r[-3], 2)
        out["gate_paper_total"] = round(X.S.get("gp", {}).get("pnl", 0), 2)
        try:
            bal = {"mexc": X.api.mx_free("USDT"), "htx": X.api.hx_free("USDT")}
            if X.api.GT_KEY:
                bal["gate"] = X.api.gt_free("USDT")
            out["balance"] = {k: round(v, 2) for k, v in bal.items()}
            out["balance_total"] = round(sum(bal.values()), 2)
            out["vs_start"] = round(out["balance_total"] - BASE, 2)
        except Exception as e:
            out["balance"] = f"ошибка: {str(e)[:80]}"
    I = mod("xinv")
    if I:
        s = I.summary()
        out["shop"] = {"hedged": s["total_hedged"], "unhedged": s["total_unhedged"],
                       "coins": {k: [v["trades"], v["net_hedged"], v["missed"]] for k, v in s["coins"].items()}}
    R = mod("xroute")
    if R:
        s = R.summary()
        out["routes"] = {"legs": {k: v["open_share_pct"] for k, v in list(s["legs"].items())[:5]},
                         "cycles": {k: [v["share_pct"], v["best_pct"]] for k, v in list(s["cycles"].items())[:4]}}
    A = mod("xarb")
    if A:
        out["top"] = [c for c, _ in A.ideal_summary().get("top_coins", [])]
    return out

def delta(cur, key, val):
    prev = D["snap"].get(key)
    return round(val - prev, 2) if isinstance(prev, (int, float)) else None

def fmt(o):
    L = [f"📊 ИТОГ ДНЯ {datetime.now(TZ).strftime('%d.%m')}"]
    lv = o.get("live")
    if lv:
        L.append(f"\n💰 ЖИВАЯ ТОРГОВЛЯ: {lv['trades']} сд., в плюсе {lv['wins']}, за сутки {lv['pnl']:+.2f}$ "
                 f"(всего {lv['total']:+.2f}$){' ⏸ на паузе' if lv['paused'] else ''}")
        if lv["by_coin"]:
            L.append("  " + ", ".join(f"{k} {v:+.2f}$" for k, v in lv["by_coin"].items()))
    if isinstance(o.get("balance"), dict):
        L.append(f"🏦 Баланс USDT: " + ", ".join(f"{k} {v}" for k, v in o["balance"].items())
                 + f" = {o['balance_total']} | от старта {o['vs_start']:+.2f}$")
    if "gate_paper_total" in o:
        d = delta(o, "gate", o["gate_paper_total"])
        L.append(f"🧪 Gate на бумаге: всего {o['gate_paper_total']:+.2f}$"
                 + (f", за сутки {d:+.2f}$" if d is not None else ""))
    sh = o.get("shop")
    if sh:
        dh, du = delta(o, "hedged", sh["hedged"]), delta(o, "unhedged", sh["unhedged"])
        L.append(f"\n📦 МАГАЗИН (бумага): с хеджем {sh['hedged']:+.2f}$"
                 + (f" (сутки {dh:+.2f}$)" if dh is not None else "")
                 + f", без хеджа {sh['unhedged']:+.2f}$" + (f" (сутки {du:+.2f}$)" if du is not None else ""))
        best = sorted(sh["coins"].items(), key=lambda kv: -kv[1][1])[:4]
        L.append("  лидеры: " + ", ".join(f"{k} {v[1]:+.2f}$ ({v[0]} сд.)" for k, v in best))
        short = [k for k, v in sh["coins"].items() if v[2] > 20]
        if short:
            L.append("  мало запаса (много упущенных окон): " + ", ".join(short))
        dead = [k for k, v in sh["coins"].items() if v[0] == 0]
        if dead:
            L.append("  без сделок: " + ", ".join(dead))
    ro = o.get("routes")
    if ro:
        L.append("\n🔁 НАПРАВЛЕНИЯ (доля времени с рабочей ногой): "
                 + ", ".join(f"{k} {v}%" for k, v in ro["legs"].items()))
        if ro["cycles"]:
            L.append("  круги: " + ", ".join(f"{k} {v[0]}% (до {v[1]}%)" for k, v in ro["cycles"].items()))
    top = o.get("top") or []
    if top:
        new = [c for c in top if c not in (D["snap"].get("top") or [])]
        L.append(f"\n🔎 ТОП МОНЕТ: {', '.join(top)}")
        if new and D["snap"].get("top"):
            L.append(f"  🆕 новые в топе: {', '.join(new)} — проверить сети и фьючерс")
    return "\n".join(L)

def snapshot(o):
    D["snap"] = {"gate": o.get("gate_paper_total"), "top": o.get("top"),
                 "hedged": (o.get("shop") or {}).get("hedged"), "unhedged": (o.get("shop") or {}).get("unhedged")}

def loop():
    time.sleep(300)
    load()
    while True:
        try:
            t = datetime.now(TZ)
            if t.hour == HOUR and D["sent_day"] != t.strftime("%Y-%m-%d"):
                o = collect()
                mm.tg(fmt(o))
                snapshot(o)
                D["sent_day"] = t.strftime("%Y-%m-%d")
                save()
        except Exception:
            log.exception("xdaily")
        time.sleep(300)

@mm.app.route("/daily")
def daily_page():
    if not (mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN):
        return "forbidden", 403
    o = collect()
    text = fmt(o)
    if request.args.get("tg") == "1":
        mm.tg(text)
    return text, 200, {"Content-Type": "text/plain; charset=utf-8"}

threading.Thread(target=loop, daemon=True).start()
