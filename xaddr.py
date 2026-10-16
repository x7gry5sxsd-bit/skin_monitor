# xaddr.py v2 — пул адресов HTX для выводов с MEXC. Адреса чередуются по кругу (нагрузка на каждый в N раз меньше).
#   Если MEXC отклонил адрес («используйте другой адрес») — он помечается плохим и выпадает из круга.
#   Когда рабочих остаётся ≤2 — сигнал в Telegram. Список: XE_ADDRS_SUI (или XE_ADDRS_<МОНЕТА>).
import os, sys, json
import mm_scanner as mm
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

A = {}

def _net(coin):
    """Код сети монеты: из настроек или от аллокатора (для монет, которые он завёл сам)."""
    n = env(f"XE_NET_{coin}")
    if n:
        return n
    X = sys.modules.get("xalloc")
    try:
        return (X.cfg(coin, "net") or "") if X and X.A else ""
    except Exception:
        return ""

def _key(coin):
    return _net(coin) or coin

def _pool(coin):
    net = _net(coin)
    raw = env(f"XE_ADDRS_{coin}") or (env(f"XE_ADDRS_{net}") if net else "") or env(f"XE_ADDR_{coin}")
    return [a.strip() for a in raw.split(",") if a.strip()]

def _load():
    if not A:
        r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'addr_v1'", fetch=True)
        A.update(json.loads(r[0][0]) if r else {})
        A.setdefault("bad", [])
        A.setdefault("rr", {})

def _save():
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('addr_v1', %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(A),))

def _good(coin):
    return [a for a in _pool(coin) if a not in A["bad"]]

def get(coin):
    """Следующий рабочий адрес HTX по кругу (каждый вызов — следующий адрес)."""
    _load()
    good, k = _good(coin), _key(coin)
    if not good:
        pool = _pool(coin)
        return pool[-1] if pool else ""
    i = A["rr"].get(k, 0) % len(good)
    A["rr"][k] = i + 1
    _save()
    return good[i]

def bad(coin, addr=None):
    """MEXC отклонил адрес: пометить и вернуть следующий рабочий (или None, если запасных нет)."""
    _load()
    good = _good(coin)
    if addr is None:                                   # какой адрес отклонён — последний выданный
        i = (A["rr"].get(_key(coin), 1) - 1) % max(len(good), 1)
        addr = good[i] if good else None
    if addr and addr not in A["bad"]:
        A["bad"].append(addr)
        _save()
    left = _good(coin)
    if len(left) <= 2:
        mm.tg(f"⚠️ Пул адресов HTX ({_key(coin)}): рабочих осталось {len(left)}. "
              f"Создай 3 новых адреса на HTX, добавь их в адресную книгу MEXC и в XE_ADDRS_{_key(coin)}")
    return left[0] if left else None

def status(coin):
    _load()
    return {"pool": len(_pool(coin)), "good": len(_good(coin)),
            "bad": [a[:8] + "…" + a[-4:] for a in _pool(coin) if a in A["bad"]]}
