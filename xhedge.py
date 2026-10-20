# xhedge.py v2 — фьючерсы для автохеджа. Шаг 1 (/hedge/check): где есть фьючерсы по нашим монетам, отвечают ли
#   ключи, какие шорты открыты и совпадают ли они с запасом магазина (HTX + MEXC + Gate).
#   Шаг 2 (/hedge/test): открыть и сразу закрыть маленький шорт через API — проверка, что биржа даёт торговать.
import os, sys, time, hmac, hashlib, base64, json
from urllib.parse import urlencode
from flask import jsonify, request
import mm_scanner as mm
import xexec_api as api
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

WATCH = ["US", "BLUR", "M", "ETHW", "XTZ", "SKL", "NES", "DOS", "BLUE", "MAGIC", "HIPPO", "AMP", "BILL", "PENDLE", "NAVI"]
MXF, KCF = "https://contract.mexc.com", "https://api-futures.kucoin.com"
FUT_SKIP = {x.strip() for x in os.getenv("XH_FUT_SKIP", "").split(",") if x.strip()}   # XH_FUT_SKIP=kucoin — не опрашивать
CACHE = {}

# ---------- публичные списки фьючерсов ----------
def perps():
    """{биржа: {МОНЕТА: монет в одном контракте}} — кэш 6 ч."""
    c = CACHE.get("perps")
    if c and time.time() - c[0] < 6 * 3600:
        return c[1]
    out = {"mexc": {}, "gate": {}, "kucoin": {}}
    try:
        for x in api.S.get(f"{MXF}/api/v1/contract/detail", timeout=20).json().get("data") or []:
            if x.get("symbol", "").endswith("_USDT") and x.get("state") == 0:
                out["mexc"][x["symbol"][:-5]] = float(x.get("contractSize") or 1)
    except Exception:
        pass
    try:
        for x in api.S.get("https://api.gateio.ws/api/v4/futures/usdt/contracts", timeout=20).json() or []:
            if not x.get("in_delisting"):
                out["gate"][x["name"].split("_")[0]] = float(x.get("quanto_multiplier") or 1)
    except Exception:
        pass
    try:
        for x in api.S.get(f"{KCF}/api/v1/contracts/active", timeout=20).json().get("data") or []:
            if x.get("quoteCurrency") == "USDT" and x.get("status") == "Open":
                base = "BTC" if x.get("baseCurrency") == "XBT" else x.get("baseCurrency")
                out["kucoin"][base] = float(x.get("multiplier") or 1)
    except Exception:
        pass
    CACHE["perps"] = (time.time(), out)
    return out

# ---------- приватные запросы ----------
def mxf(method, path, params=None):
    """MEXC фьючерсы: подпись = HMAC(secret, key + время + параметры)."""
    ts = str(int(time.time() * 1000))
    p = params or {}
    ps = urlencode(sorted(p.items())) if method == "GET" else (json.dumps(p) if p else "")
    sig = hmac.new(api.MX_SEC.encode(), (api.MX_KEY + ts + ps).encode(), hashlib.sha256).hexdigest()
    url = f"{MXF}{path}" + (f"?{ps}" if method == "GET" and ps else "")
    r = api.S.request(method, url, data=None if method == "GET" else ps, timeout=20,
                      headers={"ApiKey": api.MX_KEY, "Request-Time": ts, "Signature": sig,
                               "Content-Type": "application/json"})
    d = r.json()
    if not d.get("success"):
        raise api.ApiError(f"MEXC фьючерсы {path}: {str(d)[:200]}")
    return d.get("data")

def kcf(method, path, query=None, body=None):
    """KuCoin фьючерсы: та же подпись, что у спота, другой адрес."""
    qs = urlencode(query or {})
    ep = path + (f"?{qs}" if qs else "")
    bs = json.dumps(body) if body is not None else ""
    ts = str(int(time.time() * 1000))
    sign = base64.b64encode(hmac.new(api.KC_SEC.encode(), (ts + method + ep + bs).encode(), hashlib.sha256).digest()).decode()
    pp = base64.b64encode(hmac.new(api.KC_SEC.encode(), api.KC_PASS.encode(), hashlib.sha256).digest()).decode()
    r = api.S.request(method, KCF + ep, data=bs or None, timeout=20,
                      headers={"KC-API-KEY": api.KC_KEY, "KC-API-SIGN": sign, "KC-API-TIMESTAMP": ts,
                               "KC-API-PASSPHRASE": pp, "KC-API-KEY-VERSION": "2", "Content-Type": "application/json"})
    d = r.json()
    if str(d.get("code")) != "200000":
        raise api.ApiError(f"KuCoin фьючерсы {path}: {str(d)[:200]}")
    return d.get("data")

def balances():
    out = {}
    for name, fn in (("mexc", lambda: [(a.get("currency"), a.get("equity"), a.get("availableBalance"))
                                       for a in mxf("GET", "/api/v1/private/account/assets") or []
                                       if a.get("currency") == "USDT"]),
                     ("gate", lambda: (lambda a: {"total": a.get("total"), "available": a.get("available")})(
                         api.gt("GET", "/futures/usdt/accounts") or {})),
                     ("kucoin", lambda: (lambda a: {"equity": a.get("accountEquity"), "available": a.get("availableBalance")})(
                         kcf("GET", "/api/v1/account-overview", {"currency": "USDT"}) or {}))):
        if name in FUT_SKIP:
            continue
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = f"ошибка: {str(e)[:160]}"
    return out

def shorts():
    """Открытые шорты: {МОНЕТА: [(биржа, монет, цена входа)]}."""
    pp, out, err = perps(), {}, {}
    try:
        for x in mxf("GET", "/api/v1/private/position/open_positions") or []:
            coin = x.get("symbol", "")[:-5]
            qty = float(x.get("holdVol") or 0) * pp["mexc"].get(coin, 1)
            if x.get("positionType") == 2 and qty:
                out.setdefault(coin, []).append(("mexc", round(qty, 2), x.get("holdAvgPrice")))
    except Exception as e:
        err["mexc"] = str(e)[:160]
    try:
        for x in api.gt("GET", "/futures/usdt/positions") or []:
            coin, size = x.get("contract", "").split("_")[0], float(x.get("size") or 0)
            if size < 0:
                out.setdefault(coin, []).append(("gate", round(-size * pp["gate"].get(coin, 1), 2), x.get("entry_price")))
    except Exception as e:
        err["gate"] = str(e)[:160]
    if "kucoin" not in FUT_SKIP:                       # фьючерсы KuCoin не используем — их ошибка не должна стопорить хедж
        try:
            for x in kcf("GET", "/api/v1/positions") or []:
                sym, q = x.get("symbol", ""), float(x.get("currentQty") or 0)
                coin = sym[:-5] if sym.endswith("USDTM") else sym
                if q < 0:
                    out.setdefault(coin, []).append(("kucoin", round(-q * pp["kucoin"].get(coin, 1), 2), x.get("avgEntryPrice")))
        except Exception as e:
            err["kucoin"] = str(e)[:160]
    return out, err

def hedge_gap():
    """Магазин: запас на HTX против шортов — насколько хедж совпадает с запасом."""
    SH = sys.modules.get("xshop")
    sh, _ = shorts()
    out = {}
    for coin in (SH.COINS if SH else []):
        try:                                           # весь запас: HTX + купленное на MEXC/Gate, ждущее пачки
            inv = api.hx_free(coin) + api.mx_free(coin) + (api.gt_free(coin) if api.GT_KEY else 0)
        except Exception:
            continue
        short = sum(q for _, q, _ in sh.get(coin, []))
        out[coin] = {"inventory_total": round(inv, 2), "short": round(short, 2),
                     "gap_pct": round((inv - short) / inv * 100, 1) if inv else None}
    return out

# ---------- шаг 2: открыть / закрыть шорт ----------
def contracts(venue, coin, qty):
    mult = perps()[venue].get(coin)
    if not mult:
        raise api.ApiError(f"на {venue} нет фьючерса {coin}")
    return int(qty / mult), mult

def open_short(venue, coin, qty, lev=2):
    """Открыть шорт на qty монет (вниз до целых контрактов), изолированная маржа, плечо lev."""
    n, mult = contracts(venue, coin, qty)
    if n < 1:
        raise api.ApiError(f"{coin}: {qty:.2f} монет меньше одного контракта ({mult})")
    if venue == "gate":
        c = f"{coin}_USDT"
        api.gt("POST", f"/futures/usdt/positions/{c}/leverage", {"leverage": str(lev)})
        r = api.gt("POST", "/futures/usdt/orders", body={"contract": c, "size": -n, "price": "0", "tif": "ioc"})
    elif venue == "mexc":
        r = mxf("POST", "/api/v1/private/order/submit", {"symbol": f"{coin}_USDT", "vol": n, "leverage": lev,
                                                          "side": 3, "type": 5, "openType": 1})
    else:
        r = kcf("POST", "/api/v1/orders", body={"clientOid": str(int(time.time() * 1000)), "side": "sell",
                                                  "symbol": f"{coin}USDTM", "type": "market", "size": n,
                                                  "leverage": str(lev), "marginMode": "ISOLATED"})
    return {"contracts": n, "coins": n * mult, "resp": r}

def close_short(venue, coin, qty):
    """Уменьшить шорт на qty монет (покупка «только на уменьшение позиции»)."""
    n, mult = contracts(venue, coin, qty)
    if n < 1:
        return {"contracts": 0, "coins": 0}
    if venue == "gate":
        r = api.gt("POST", "/futures/usdt/orders", body={"contract": f"{coin}_USDT", "size": n, "price": "0",
                                                           "tif": "ioc", "reduce_only": True})
    elif venue == "mexc":
        r = mxf("POST", "/api/v1/private/order/submit", {"symbol": f"{coin}_USDT", "vol": n, "leverage": 2,
                                                          "side": 2, "type": 5, "openType": 1})
    else:
        r = kcf("POST", "/api/v1/orders", body={"clientOid": str(int(time.time() * 1000)), "side": "buy",
                                                  "symbol": f"{coin}USDTM", "type": "market", "size": n,
                                                  "reduceOnly": True, "marginMode": "ISOLATED"})
    return {"contracts": n, "coins": n * mult, "resp": r}

def ok():
    return mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN

@mm.app.route("/hedge/test")
def hedge_test():
    """Шаг 2: открыть и сразу закрыть маленький шорт через API. Открывать ОДИН раз на биржу."""
    if not ok():
        return "forbidden", 403
    v, coin = request.args.get("venue", "gate"), request.args.get("coin", "US").upper()
    usd = float(request.args.get("usd", "1.5"))
    out = {"venue": v, "coin": coin}
    try:
        b = xpaper.book("mexc", coin) or xpaper.book("htx", coin)
        out["open"] = open_short(v, coin, usd / b[0][0][0])
        time.sleep(2)
        out["after_open"] = shorts()[0].get(coin)
        out["close"] = close_short(v, coin, out["open"]["coins"])
        time.sleep(2)
        out["after_close"] = shorts()[0].get(coin)
    except Exception as e:
        out["error"] = str(e)[:300]
    return jsonify(out)

@mm.app.route("/hedge/check")
def hedge_check():
    if not ok():
        return "forbidden", 403
    pp = perps()
    sh, err = shorts()
    return jsonify({"futures_keys": balances(), "positions_errors": err, "shorts": sh, "shop_hedge": hedge_gap(),
                    "perps": {c: [v for v in ("mexc", "gate", "kucoin") if c in pp[v]] for c in WATCH}})

@mm.app.route("/net")
def net_page():
    """Сети вывода любой монеты на MEXC, Gate и KuCoin: /net?t=...&coin=AXS"""
    if not ok():
        return "forbidden", 403
    c, out = request.args.get("coin", "").upper(), {}
    for name, fn in (("mexc", api.mx_networks), ("gate", api.gt_networks), ("kucoin", api.kc_networks)):
        try:
            out[name] = fn(c)
        except Exception as e:
            out[name] = f"ошибка: {str(e)[:150]}"
    return jsonify(out)

