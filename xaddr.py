# xaddr.py — пул адресов HTX для выводов с MEXC. Если MEXC отклонил адрес («используйте другой адрес»),
#   помечаем его плохим и переходим на следующий из списка. Список: XE_ADDRS_SUI (или XE_ADDRS_<МОНЕТА>).
import os, json
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

A = {}

def _pool(coin):
    raw = env(f"XE_ADDRS_{coin}") or env(f"XE_ADDRS_{env(f'XE_NET_{coin}')}") or env(f"XE_ADDR_{coin}")
    return [a.strip() for a in raw.split(",") if a.strip()]

def _load():
    if not A:
        r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'addr_v1'", fetch=True)
        A.update(json.loads(r[0][0]) if r else {})
        A.setdefault("bad", [])

def _save():
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('addr_v1', %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(A),))

def get(coin):
    """Текущий рабочий адрес HTX для вывода монеты с MEXC."""
    _load()
    pool = _pool(coin)
    for a in pool:
        if a not in A["bad"]:
            return a
    return pool[-1] if pool else ""

def bad(coin):
    """MEXC отклонил текущий адрес: пометить и вернуть следующий (или None, если запасных нет)."""
    _load()
    cur = get(coin)
    if cur and cur not in A["bad"]:
        A["bad"].append(cur)
        _save()
    nxt = get(coin)
    return nxt if nxt and nxt not in A["bad"] else None

def status(coin):
    _load()
    return {"current": get(coin), "pool": len(_pool(coin)), "bad": [a for a in _pool(coin) if a in A["bad"]]}
