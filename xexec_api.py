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
MX_KEY = env("MEXC_TRADE_KEY") or env("MEXC_KEY")         # если отдельного торгового ключа нет — берём общий
MX_SEC = env("MEXC_TRADE_SECRET") or env("MEXC_SECRET")

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
def mx_withdraw_status(coin, wid):
    """Статус вывода MEXC: 7 — успешно, 8 — ошибка, 9 — отменён; None — не найден."""
    for w in mx("GET", "/api/v3/capital/withdraw/history", coin=coin, limit=20) or []:
        if str(w.get("id")) == str(wid):
            return int(w.get("status") or 0)
    return None

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
    chain = env("XE_USDT_CHAIN", "trc20usdt")      # сеть возврата USDT (код HTX), см. /exec/check
    return hx("POST", "/v1/dw/withdraw/api/create", body={
        "address": address, "currency": "usdt", "amount": f"{amount:.2f}", "chain": chain})
# ---------- GATE ----------
import json as _json
GT = "https://api.gateio.ws"
GT_KEY, GT_SEC = env("GATE_KEY"), env("GATE_SECRET")

def gt(method, path, query=None, body=None):
    qs = urlencode(query or {})
    bs = _json.dumps(body) if body is not None else ""
    ts = str(int(time.time()))
    full = f"/api/v4{path}"
    sig_str = f"{method}\n{full}\n{qs}\n{hashlib.sha512(bs.encode()).hexdigest()}\n{ts}"
    sign = hmac.new(GT_SEC.encode(), sig_str.encode(), hashlib.sha512).hexdigest()
    r = S.request(method, f"{GT}{full}" + (f"?{qs}" if qs else ""), data=bs or None, timeout=20,
                  headers={"KEY": GT_KEY, "Timestamp": ts, "SIGN": sign,
                           "Content-Type": "application/json", "Accept": "application/json"})
    try:
        d = r.json()
    except ValueError:
        raise ApiError(f"GATE {path}: {r.status_code} {r.text[:200]}")
    if r.status_code >= 300:
        raise ApiError(f"GATE {path}: {r.status_code} {str(d)[:300]}")
    return d

def gt_free(cur):
    for a in gt("GET", "/spot/accounts", {"currency": cur.upper()}) or []:
        return float(a.get("available") or 0)
    return 0.0

def gt_networks(coin):
    """[(сеть, вывод открыт, комиссия)] — публичные сети + комиссии по ключу."""
    ch = S.get(f"{GT}/api/v4/wallet/currency_chains", params={"currency": coin.upper()}, timeout=20).json()
    fees = {}
    for w in gt("GET", "/wallet/withdraw_status", {"currency": coin.upper()}) or []:
        fees = w.get("withdraw_fix_on_chains") or {}
    return [(c.get("chain"), not c.get("is_withdraw_disabled"), fees.get(c.get("chain"))) for c in ch or []]

def gt_buy_quote(coin, usdt):
    return gt("POST", "/spot/orders", body={"currency_pair": f"{coin.upper()}_USDT", "side": "buy",
                                            "type": "market", "amount": f"{usdt:.2f}", "time_in_force": "ioc"})

def gt_withdraw(coin, chain, address, amount):
    return gt("POST", "/withdrawals", body={"currency": coin.upper(), "chain": chain,
                                             "address": address, "amount": str(amount)})

def gt_withdraw_status(coin, wid):
    for w in gt("GET", "/wallet/withdrawals", {"currency": coin.upper(), "limit": 20}) or []:
        if str(w.get("id")) == str(wid):
            return w.get("status")                     # DONE — готово; CANCEL/FAIL/INVALID — отменён
    return None

# ---------- KUCOIN ----------
KC = "https://api.kucoin.com"
KC_KEY, KC_SEC, KC_PASS = env("KUCOIN_KEY"), env("KUCOIN_SECRET"), env("KUCOIN_PASSPHRASE")

def kc(method, path, query=None, body=None):
    qs = urlencode(query or {})
    ep = path + (f"?{qs}" if qs else "")
    bs = _json.dumps(body) if body is not None else ""
    ts = str(int(time.time() * 1000))
    sign = base64.b64encode(hmac.new(KC_SEC.encode(), (ts + method + ep + bs).encode(),
                                     hashlib.sha256).digest()).decode()
    pp = base64.b64encode(hmac.new(KC_SEC.encode(), KC_PASS.encode(), hashlib.sha256).digest()).decode()
    r = S.request(method, KC + ep, data=bs or None, timeout=20,
                  headers={"KC-API-KEY": KC_KEY, "KC-API-SIGN": sign, "KC-API-TIMESTAMP": ts,
                           "KC-API-PASSPHRASE": pp, "KC-API-KEY-VERSION": "2",
                           "Content-Type": "application/json"})
    try:
        d = r.json()
    except ValueError:
        raise ApiError(f"KUCOIN {path}: {r.status_code} {r.text[:200]}")
    if str(d.get("code")) != "200000":
        raise ApiError(f"KUCOIN {path}: {str(d)[:300]}")
    return d.get("data")

def kc_free(cur, kind="trade"):
    """Свободный баланс: kind='trade' — торговый счёт, 'main' — основной (депозиты и выводы)."""
    tot = 0.0
    for a in kc("GET", "/api/v1/accounts", {"currency": cur.upper(), "type": kind}) or []:
        tot += float(a.get("available") or 0)
    return tot

def kc_networks(coin):
    d = S.get(f"{KC}/api/v3/currencies/{coin.upper()}", timeout=20).json().get("data") or {}
    return [(c.get("chainName"), c.get("chainId"), c.get("isWithdrawEnabled"), c.get("withdrawalMinFee"))
            for c in d.get("chains") or []]

def kc_transfer(cur, amount, src, dst):
    return kc("POST", "/api/v2/accounts/inner-transfer",
              body={"clientOid": str(int(time.time() * 1000)), "currency": cur.upper(),
                    "from": src, "to": dst, "amount": str(amount)})

def kc_buy_quote(coin, usdt):
    return kc("POST", "/api/v1/orders", body={"clientOid": str(int(time.time() * 1000)), "side": "buy",
                                               "symbol": f"{coin.upper()}-USDT", "type": "market",
                                               "funds": f"{usdt:.2f}"})

def kc_withdraw(coin, chain_id, address, amount):
    return kc("POST", "/api/v1/withdrawals", body={"currency": coin.upper(), "address": address,
                                                    "amount": str(amount), "chain": chain_id})

def kc_withdraw_status(coin, wid):
    d = kc("GET", "/api/v1/withdrawals", {"currency": coin.upper(), "pageSize": 20}) or {}
    for w in d.get("items") or []:
        if str(w.get("id")) == str(wid):
            return w.get("status")                     # SUCCESS — готово; FAILURE — отменён
    return None

# ---------- продажа по рынку (самолечение: вернуть монету в USDT) ----------
def mx_sell_market(symbol, qty):
    """Продать монету по рынку на MEXC; количество округляется вниз до шага пары."""
    info = S.get(f"{MX}/api/v3/exchangeInfo", params={"symbol": symbol}, timeout=15).json()
    step = float(((info.get("symbols") or [{}])[0]).get("baseSizePrecision") or 0) or 0.01
    q = math.floor(qty / step + 1e-9) * step
    return mx("POST", "/api/v3/order", symbol=symbol, side="SELL", type="MARKET",
              quantity=f"{q:.8f}".rstrip("0").rstrip("."))

def gt_sell_market(coin, qty):
    """Продать монету по рынку на Gate (количество — в монетах)."""
    pair = S.get(f"{GT}/api/v4/spot/currency_pairs/{coin.upper()}_USDT", timeout=15).json()
    prec = int(pair.get("amount_precision") or 2)
    q = math.floor(qty * 10 ** prec) / 10 ** prec
    return gt("POST", "/spot/orders", body={"currency_pair": f"{coin.upper()}_USDT", "side": "sell",
                                            "type": "market", "amount": f"{q:.{prec}f}", "time_in_force": "ioc"})

# ---------- HTX: продажа с защитой от «миражей» ----------
HX_PPREC = {}

def hx_price_prec(symbol):
    if symbol not in HX_PPREC:
        try:
            for s in S.get("https://api.huobi.pro/v1/common/symbols", timeout=20).json().get("data", []):
                HX_PPREC[s["symbol"]] = int(s.get("price-precision", 6))
        except Exception:
            pass
    return HX_PPREC.get(symbol, 6)

def hx_sell_ioc(symbol, amount, min_price):
    """Продать не дешевле min_price (лимит-IOC): что не исполнилось сразу — отменяется. Защита от «миражей» в стакане."""
    prec, pp = hx_prec(symbol), hx_price_prec(symbol)
    amt = math.floor(amount * 10 ** prec) / 10 ** prec
    price = math.ceil(min_price * 10 ** pp) / 10 ** pp
    return hx("POST", "/v1/order/orders/place", body={
        "account-id": str(hx_account()), "symbol": symbol, "type": "sell-ioc",
        "amount": f"{amt:.{prec}f}", "price": f"{price:.{pp}f}"})

def mx_book(coin):
    """Адресная книга вывода MEXC для монеты: [(сеть, адрес в нижнем регистре)]."""
    d = mx("GET", "/api/v3/capital/withdraw/address", coin=coin.upper(), limit=50)
    rows = d.get("data") if isinstance(d, dict) else d
    return [((x.get("network") or x.get("netWork") or "").upper(), (x.get("address") or "").lower())
            for x in rows or []]
