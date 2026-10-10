# xfer.py — публичные статусы ввода/вывода монет: HTX, Gate, KuCoin, Bitget (без ключей)
import time
import requests

S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0"
CACHE, TTL = {}, 1800                                   # кэш статусов на 30 минут
SUPPORTED = ("htx", "gate", "kucoin", "bitget")
ALIAS = {"ETH": "ERC20", "ERC20": "ERC20", "ETHEREUM": "ERC20", "TRX": "TRC20", "TRC20": "TRC20",
         "TRON": "TRC20", "BSC": "BEP20", "BEP20": "BEP20", "BNB": "BEP20", "BNBSMARTCHAIN": "BEP20",
         "BEP20BSC": "BEP20", "SOL": "SOL", "SOLANA": "SOL", "SPL": "SOL", "ARB": "ARB",
         "ARBITRUM": "ARB", "ARBITRUMONE": "ARB", "ARBEVM": "ARB", "MATIC": "POLYGON",
         "POLYGON": "POLYGON", "POL": "POLYGON", "OP": "OP", "OPTIMISM": "OP", "BASE": "BASE",
         "AVAXC": "AVAXC", "AVAXCCHAIN": "AVAXC", "TON": "TON"}

def norm(name):
    k = "".join(ch for ch in str(name or "").upper() if ch.isalnum())
    return ALIAS.get(k, k)

def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None

def _htx(coin):
    d = S.get("https://api.huobi.pro/v2/reference/currencies",
              params={"currency": coin.lower()}, timeout=15).json()
    out = {}
    for cur in d.get("data") or []:
        for ch in cur.get("chains") or []:
            name = ch.get("displayName") or ch.get("baseChainProtocol") or ch.get("chain")
            out[norm(name)] = {"dep": ch.get("depositStatus") == "allowed",
                               "wd": ch.get("withdrawStatus") == "allowed",
                               "fee": _f(ch.get("transactFeeWithdraw")), "contract": ""}
    return out

def _gate(coin):
    d = S.get("https://api.gateio.ws/api/v4/wallet/currency_chains",
              params={"currency": coin.upper()}, timeout=15).json()
    out = {}
    for ch in d if isinstance(d, list) else []:
        off = bool(ch.get("is_disabled"))
        out[norm(ch.get("chain"))] = {"dep": not off and not ch.get("is_deposit_disabled"),
                                      "wd": not off and not ch.get("is_withdraw_disabled"),
                                      "fee": None,
                                      "contract": (ch.get("contract_address") or "").lower()}
    return out

def _kucoin(coin):
    d = S.get(f"https://api.kucoin.com/api/v3/currencies/{coin.upper()}", timeout=15).json()
    out = {}
    for ch in (d.get("data") or {}).get("chains") or []:
        out[norm(ch.get("chainName") or ch.get("chainId"))] = {
            "dep": bool(ch.get("isDepositEnabled")), "wd": bool(ch.get("isWithdrawEnabled")),
            "fee": _f(ch.get("withdrawalMinFee")),
            "contract": (ch.get("contractAddress") or "").lower()}
    return out

def _bitget(coin):
    d = S.get("https://api.bitget.com/api/v2/spot/public/coins",
              params={"coin": coin.upper()}, timeout=15).json()
    out = {}
    for c in d.get("data") or []:
        for ch in c.get("chains") or []:
            out[norm(ch.get("chain"))] = {
                "dep": str(ch.get("rechargeable")).lower() == "true",
                "wd": str(ch.get("withdrawable")).lower() == "true",
                "fee": _f(ch.get("withdrawFee")),
                "contract": (ch.get("contractAddress") or "").lower()}
    return out

FETCH = {"htx": _htx, "gate": _gate, "kucoin": _kucoin, "bitget": _bitget}

def status(ex, coin):
    if ex not in FETCH:
        return None
    key = (ex, coin)
    c = CACHE.get(key)
    if c and time.time() - c[0] < TTL:
        return c[1]
    try:
        st = FETCH[ex](coin)
    except Exception:
        st = None
    CACHE[key] = (time.time(), st)
    time.sleep(0.2)
    return st

def route(buy, sell, coin, price):
    """Можно ли перевести монету с биржи покупки на биржу продажи."""
    a, b = status(buy, coin), status(sell, coin)
    if not a or not b:
        miss = buy if not a else sell
        return {"state": "unknown", "fee_usd": None, "text": f"не проверено ({miss}: нет публичных статусов)"}
    best, mismatch = None, False
    for net, ia in a.items():
        ib = b.get(net)
        if not ib:
            continue
        if ia["contract"] and ib["contract"] and ia["contract"] != ib["contract"]:
            mismatch = True
            continue
        if ia["wd"] and ib["dep"]:
            fee = ia["fee"] * price if ia["fee"] is not None else None
            if best is None or (fee or 0) < (best[1] or 0):
                best = (net, fee)
    if best:
        fee_txt = f"${best[1]:.2f}" if best[1] is not None else "не известна"
        return {"state": "open", "fee_usd": best[1],
                "text": f"✅ путь открыт: сеть {best[0]}, комиссия вывода {fee_txt}"}
    if mismatch:
        return {"state": "mismatch", "fee_usd": None, "text": "❌ контракты разные — это другой токен"}
    return {"state": "closed", "fee_usd": None,
            "text": f"❌ перевод закрыт (вывод {buy} / ввод {sell} в общей сети)"}