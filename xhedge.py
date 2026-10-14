# xhedge.py v1 — фьючерсы для автохеджа, шаг 1 (только чтение, без сделок):
#   где есть бессрочные фьючерсы по нашим монетам (MEXC, Gate, KuCoin), отвечают ли ключи на фьючерсы,
#   какие шорты открыты и совпадают ли они с запасом магазина на HTX. /hedge/check?t=...
import os, sys, time, hmac, hashlib, base64, json
from urllib.parse import urlencode
from flask import jsonify, request
import mm_scanner as mm
import xexec_api as api

def env(k, d=""):
    return os.getenv(k, d).strip()

WATCH = ["US", "BLUR", "M", "ETHW", "XTZ", "SKL", "NES", "DOS", "BLUE", "MAGIC", "HIPPO", "AMP", "BILL", "PENDLE", "NAVI"]
MXF, KCF = "https://contract.mexc.com", "https://api-futures.kucoin.com"
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
        try:
            inv = api.hx_free(coin)
        except Exception:
            continue
        short = sum(q for _, q, _ in sh.get(coin, []))
        out[coin] = {"inventory_htx": round(inv, 2), "short": round(short, 2),
                     "gap_pct": round((inv - short) / inv * 100, 1) if inv else None}
    return out

@mm.app.route("/hedge/check")
def hedge_check():
    if not (mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN):
        return "forbidden", 403
    pp = perps()
    sh, err = shorts()
    return jsonify({"futures_keys": balances(), "positions_errors": err, "shorts": sh, "shop_hedge": hedge_gap(),
                    "perps": {c: [v for v in ("mexc", "gate", "kucoin") if c in pp[v]] for c in WATCH}})
