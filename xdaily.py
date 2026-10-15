# xdaily.py — итог дня в Telegram (23:00 по местному времени) + /daily?t=...&tg=1 по запросу.
#   Всё в одном: торговля, баланс, магазин, упущенные окна, глубина, бумага, направления с монетами, кандидаты.
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
    SH = mod("xshop")
    if SH and SH.S:
        if "c" in SH.S:                                                     # магазин v2 (несколько монет)
            coins = {k: (v["trades"], round(v["pnl"] - v["wd_usd"], 2), v.get("edge_now")) for k, v in SH.S["c"].items()}
        else:                                                               # магазин v1 (одна монета)
            coins = {getattr(SH, "COIN", "US"): (SH.S.get("trades", 0),
                     round(SH.S.get("pnl", 0) - SH.S.get("wd_usd", 0), 2), SH.S.get("edge_now"))}
        inv = {}
        for k in coins:
            try:
                inv[k] = round(SH.api.hx_free(k), 2)
            except Exception:
                pass
        out["shopl"] = {"mode": "бумага" if SH.DRY else "живой", "coins": coins, "inv": inv,
                        "rev_hits": SH.S.get("rev_hits"), "day": round(SH.S.get("pnl_day", 0), 2)}
    SC = mod("xscout")
    if SC:
        out["scout"] = SC.text()
    MI = mod("xmiss")
    if MI:
        out["miss"] = MI.text()
    I = mod("xinv")
    if I:
        s = I.summary()
        out["shop"] = {"hedged": s["total_hedged"], "unhedged": s["total_unhedged"],
                       "coins": {k: [v["trades"], v["net_hedged"], v["missed"]] for k, v in s["coins"].items()}}
    R = mod("xroute")
    if R:
        s = R.summary()
        out["routes"] = {"legs": {k: [v["open_share_pct"], v["avg_best_pct"], [c for c, _ in v["top_coins"][:3]]]
                                  for k, v in list(s["legs"].items())[:6] if v["open_share_pct"] > 0},
                         "cycles": {k: [v["share_pct"], v["best_pct"]] for k, v in list(s["cycles"].items())[:4]}}
    Z = mod("xsize")
    if Z:
        out["size"] = {k: v["window_holds_pct"] for k, v in Z.summary()["coins"].items() if v["open_samples"] >= 10}
    TM = mod("xtime")
    if TM:
        out["xtime"] = TM.text()
    WD = mod("xwander")
    if WD:
        out["wander"] = WD.text()
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
    sl = o.get("shopl")
    if sl:
        L.append(f"🏪 МАГАЗИН ({sl['mode']}): за день {sl['day']:+.2f}$ | " + ", ".join(
            f"{k}: {v[0]} сд., чистыми {v[1]:+.2f}$, окно сейчас {v[2]}%" for k, v in sl["coins"].items()))
        if sl["inv"]:
            L.append("  запас на HTX: " + ", ".join(f"{k} {v}" for k, v in sl["inv"].items())
                     + (f" | обратных окон: {sl['rev_hits']}" if sl.get("rev_hits") is not None else ""))
    if o.get("miss"):
        L.append(o["miss"])
    if o.get("size"):
        L.append("📏 ГЛУБИНА (окно держится на $30 / $50): " + ", ".join(
            f"{k} {v['$30']}%/{v['$50']}%" for k, v in o["size"].items()))
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
        L.append("\n🔁 НАПРАВЛЕНИЯ (открыто % времени, средний %, главные монеты):")
        for k, v in ro["legs"].items():
            L.append(f"  {k}: {v[0]}%, ~{v[1]}% — {', '.join(v[2])}")
        if ro["cycles"]:
            L.append("  круги: " + ", ".join(f"{k} {v[0]}% (до {v[1]}%)" for k, v in ro["cycles"].items()))
    if o.get("xtime"):
        L.append(o["xtime"])
    if o.get("wander"):
        L.append(o["wander"])
    top = o.get("top") or []
    if top:
        new = [c for c in top if c not in (D["snap"].get("top") or [])]
        L.append(f"\n🔎 ТОП МОНЕТ: {', '.join(top)}")
        if new and D["snap"].get("top"):
            L.append(f"  🆕 новые в топе: {', '.join(new)} — проверить сети и фьючерс")
    if o.get("scout"):
        L.append("\n" + o["scout"])
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
