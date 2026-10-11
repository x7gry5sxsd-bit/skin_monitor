# xexec_api.py — приватные API MEXC и HTX для исполнителя: баланс, ордера, вывод
import os, time, math, hmac, hashlib, base64
from datetime import datetime, timezone
from urllib.parse import urlencode, quote
import requests

S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0"

def env(k, d=""):
    return os.getenv(k, d).strip()

class ApiError(Exception):
    pass

# ---------- MEXC ----------
MX = "https://api.mexc.com"
MX_KEY, MX_SEC = env("MEXC_TRADE_KEY"), env("MEXC_TRADE_SECRET")

def mx(method, path, **params):
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 10000
    qs = urlencode(params)
    sig = hmac.new(MX_SEC.encode(), qs.encode(), hashlib.sha256).hexdigest()
    r = S.request(method, f"{MX}{path}?{qs}&signature={sig}", timeout=20,
                  headers={"X-MEXC-APIKEY": MX_KEY, "Content-Type": "application/json"})
    try:
        d = r.json()
    except ValueError:
        raise ApiError(f"MEXC {path}: {r.status_code} {r.text[:200]}")
    if r.status_code != 200 or (isinstance(d, dict) and d.get("code") not in (None, 0, 200, "0")):
        raise ApiError(f"MEXC {path}: {r.status_code} {str(d)[:300]}")
    return d

def mx_free(asset):
    for b in mx("GET", "/api/v3/account").get("balances", []):
        if b.get("asset") == asset:
            return float(b.get("free") or 0)
    return 0.0

def mx_buy_quote(symbol, usdt):
    return mx("POST", "/api/v3/order", symbol=symbol, side="BUY", type="MARKET", quoteOrderQty=f"{usdt:.2f}")

def mx_withdraw(coin, network, address, amount):
    return mx("POST", "/api/v3/capital/withdraw", coin=coin, netWork=network, address=address, amount=amount)

def mx_networks(coin):
    d = mx("GET", "/api/v3/capital/config/getall")
    for c in d if isinstance(d, list) else []:
        if c.get("coin") == coin:
            return [(n.get("netWork"), n.get("withdrawEnable"), n.get("withdrawFee"), n.get("withdrawMin"))
                    for n in c.get("networkList", [])]
    return []

# ---------- HTX ----------
HX = "api.huobi.pro"
HX_KEY, HX_SEC = env("HTX_KEY"), env("HTX_SECRET")
HX_ACC, HX_PREC = {}, {}

def hx(method, path, params=None, body=None):
    p = {"AccessKeyId": HX_KEY, "SignatureMethod": "HmacSHA256", "SignatureVersion": "2",
         "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")}
    if params:
        p.update(params)
    qs = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in sorted(p.items()))
    payload = f"{method}\n{HX}\n{path}\n{qs}"
    sig = base64.b64encode(hmac.new(HX_SEC.encode(), payload.encode(), hashlib.sha256).digest()).decode()
    r = S.request(method, f"https://{HX}{path}?{qs}&Signature={quote(sig, safe='')}", json=body,
                  headers={"Content-Type": "application/json"}, timeout=20)
    try:
        d = r.json()
    except ValueError:
        raise ApiError(f"HTX {path}: {r.status_code} {r.text[:200]}")
    if d.get("status") == "error" or d.get("code") not in (None, 200):
        raise ApiError(f"HTX {path}: {str(d)[:300]}")
    return d

def hx_account():
    if "id" not in HX_ACC:
        for a in hx("GET", "/v1/account/accounts").get("data", []):
            if a.get("type") == "spot":
                HX_ACC["id"] = a["id"]
    return HX_ACC["id"]

def hx_free(cur):
    d = hx("GET", f"/v1/account/accounts/{hx_account()}/balance")
    for b in (d.get("data") or {}).get("list", []):
        if b.get("currency") == cur.lower() and b.get("type") == "trade":
            return float(b.get("balance") or 0)
    return 0.0

def hx_prec(symbol):
    if symbol not in HX_PREC:
        try:
            for s in S.get("https://api.huobi.pro/v1/common/symbols", timeout=20).json().get("data", []):
                HX_PREC[s["symbol"]] = int(s.get("amount-precision", 2))
        except Exception:
            pass
    return HX_PREC.get(symbol, 2)

def hx_sell_market(symbol, amount):
    prec = hx_prec(symbol)
    amt = math.floor(amount * 10 ** prec) / 10 ** prec
    return hx("POST", "/v1/order/orders/place", body={
        "account-id": str(hx_account()), "symbol": symbol, "type": "sell-market",
        "amount": f"{amt:.{prec}f}"})

def hx_order(order_id):
    return hx("GET", f"/v1/order/orders/{order_id}")

def hx_withdraw_usdt(address, amount):
    return hx("POST", "/v1/dw/withdraw/api/create", body={
        "address": address, "currency": "usdt", "amount": f"{amount:.2f}", "chain": "trc20usdt"})