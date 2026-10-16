# xbal.py — полный капитал системы в долларах: USDT и монеты на споте MEXC / HTX / Gate / KuCoin + фьючерсные
#   счета (вместе с прибылью/убытком шортов). Точка отсчёта — первый замер или /balance?t=...&set=1.
#   Строка «💼 КАПИТАЛ» — в итог дня. /balance?t=...
import os, time, json, logging
from flask import jsonify, request
import mm_scanner as mm
import xpaper
import xexec_api as api
import xhedge as hx

log = logging.getLogger("xbal")
B = {}

def load():
    if not B:
        r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'bal_v1'", fetch=True)
        B.update(json.loads(r[0][0]) if r else {})

def save():
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('bal_v1', %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(B),))

def price(coin):
    for ex in ("htx", "mexc", "gate", "kucoin"):
        b = xpaper.book(ex, coin)
        if b and b[0] and b[1]:
            return (b[0][0][0] + b[1][0][0]) / 2
    return 0.0

def spot():
    """Спот на всех биржах: {биржа: {валюта: количество}} (свободное + в ордерах)."""
    out = {}
    try:
        out["mexc"] = {b["asset"]: float(b["free"]) + float(b["locked"])
                       for b in api.mx("GET", "/api/v3/account").get("balances", [])
                       if float(b["free"]) + float(b["locked"]) > 0}
    except Exception as e:
        out["mexc"] = f"ошибка: {str(e)[:120]}"
    try:
        m = {}
        for b in (api.hx("GET", f"/v1/account/accounts/{api.hx_account()}/balance").get("data") or {}).get("list", []):
            v = float(b.get("balance") or 0)
            if v > 0:
                m[b["currency"].upper()] = m.get(b["currency"].upper(), 0) + v
        out["htx"] = m
    except Exception as e:
        out["htx"] = f"ошибка: {str(e)[:120]}"
    if api.GT_KEY:
        try:
            out["gate"] = {a["currency"]: float(a["available"]) + float(a["locked"])
                           for a in api.gt("GET", "/spot/accounts") or []
                           if float(a["available"]) + float(a["locked"]) > 0}
        except Exception as e:
            out["gate"] = f"ошибка: {str(e)[:120]}"
    if getattr(api, "KC_KEY", ""):
        try:
            m = {}
            for a in api.kc("GET", "/api/v1/accounts") or []:
                v = float(a.get("balance") or 0)
                if a.get("type") in ("main", "trade") and v > 0:
                    m[a["currency"]] = m.get(a["currency"], 0) + v
            out["kucoin"] = m
        except Exception as e:
            out["kucoin"] = f"ошибка: {str(e)[:120]}"
    return out

def futures():
    """Капитал фьючерсных счетов, $ (маржа + прибыль/убыток открытых шортов)."""
    out, b = {}, hx.balances()
    try:
        out["mexc"] = float(b["mexc"][0][1])
    except Exception:
        pass
    try:
        g = api.gt("GET", "/futures/usdt/accounts") or {}
        out["gate"] = float(g.get("total") or 0) + float(g.get("unrealised_pnl") or 0)
    except Exception:
        pass
    try:
        out["kucoin"] = float(b["kucoin"].get("equity") or 0)
    except Exception:
        pass
    return out

def snapshot():
    sp, fu = spot(), futures()
    usdt = coins = 0.0
    detail, errors = {}, {}
    for ex, m in sp.items():
        if not isinstance(m, dict):
            errors[ex] = m
            continue
        for cur, q in m.items():
            if cur == "USDT":
                usdt += q
                detail.setdefault(ex, {})["USDT"] = round(q, 2)
                continue
            v = q * price(cur)
            if v >= 0.5:
                coins += v
                detail.setdefault(ex, {})[cur] = {"qty": round(q, 4), "usd": round(v, 2)}
    fut = sum(fu.values())
    total = round(usdt + coins + fut, 2)
    load()
    if not B.get("start"):
        B.update({"start": total, "start_t": int(time.time())})
        save()
    return {"total": total, "usdt_spot": round(usdt, 2), "coins": round(coins, 2), "futures": round(fut, 2),
            "futures_by_exchange": {k: round(v, 2) for k, v in fu.items()}, "spot": detail, "errors": errors,
            "start": B["start"], "since_start": round(total - B["start"], 2),
            "start_date": time.strftime("%d.%m %H:%M", time.gmtime(B["start_t"] + 8 * 3600))}

def text():
    try:
        s = snapshot()
    except Exception as e:
        return f"💼 КАПИТАЛ: не посчитать — {str(e)[:100]}"
    return (f"💼 КАПИТАЛ: ${s['total']:.2f} (USDT ${s['usdt_spot']:.2f} + монеты ${s['coins']:.2f} + фьючерсы "
            f"${s['futures']:.2f}) | от точки отсчёта {s['start_date']}: {s['since_start']:+.2f}$")

@mm.app.route("/balance")
def balance_page():
    if not (mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN):
        return "forbidden", 403
    if request.args.get("set") == "1":                 # сделать текущий капитал точкой отсчёта
        load()
        B["start"] = None
    return jsonify(snapshot())
