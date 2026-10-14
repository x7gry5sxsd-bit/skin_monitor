# xmiss.py — учёт упущенных окон: сам смотрит рынок каждые 3 с теми же формулами, что схема 1 и магазин,
#   и по каждому окну пишет, взяли его или нет — и почему нет. Только наблюдение, в торговлю не вмешивается.
import os, sys, time, json, threading, logging
from datetime import datetime, timezone, timedelta
from flask import jsonify, request
import mm_scanner as mm
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

STEP = float(env("XM_STEP_SEC", "3"))
TZ = timezone(timedelta(hours=float(env("TZ_OFFSET", "8"))))
log = logging.getLogger("xmiss")
M = {}
EP = {}                                               # открытые сейчас окна: key -> {...}

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'miss_v1'", fetch=True)
    if r:
        M.update(json.loads(r[0][0]))
    M.setdefault("day", "")
    M.setdefault("d", {})                             # за сегодня: причина -> [окон, упущено $]
    M.setdefault("total", {})

def save():
    xpaper.q("""INSERT INTO paper_xarb(id, js) VALUES ('miss_v1', %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(M),))

def add(why, usd):
    for book in (M["d"], M["total"]):
        r = book.setdefault(why, [0, 0.0])
        r[0] += 1
        r[1] = round(r[1] + usd, 2)

def s1_traded(X, coin, t0):
    return any(c["coin"] == coin and c["t0"] >= t0 - 1 and not c.get("paper") for c in X.S.get("cycles", [])) or \
        any(r[1] == coin and r[0] >= t0 - 1 for r in X.S.get("done", []))

def s1_reason(X, coin, v):
    if X.S.get("paused"):
        return "пауза"
    live = [c for c in X.S.get("cycles", []) if X.live(c)]
    if any(c["coin"] == coin for c in live):
        return "монета уже в пути"
    if len(live) >= X.MAX_ACTIVE:
        return "лимит одновременных сделок"
    if time.time() < X.S.get("cool", {}).get(f"{coin}@{v}", 0):
        return "пауза после ошибки"
    try:
        if X.v_free(v, "USDT") < X.TRADE:
            return f"мало USDT на {v.upper()}"
    except Exception:
        pass
    return "другое"

def sh_reason(H, coin, w):
    if H.S.get("paused"):
        return "пауза"
    try:
        if H.api.hx_free(coin) < w["q"]:
            return "кончился запас на HTX"
        if all(H.v_free(o["venue"], "USDT") < o["cost"] * 1.02 for o in w["opts"] if o["edge"] >= H.MIN_EDGE):
            return "мало USDT для докупки"
    except Exception:
        pass
    if time.time() < H.S.get("c", {}).get(coin, {}).get("cool", 0):
        return "пауза 60 с после прошлой сделки"
    return "другое"

def track(key, edge, thr, usd, now, traded_fn, reason_fn, short_sec):
    e = EP.get(key)
    if edge is not None and edge >= thr:
        if not e:
            EP[key] = e = {"t0": now, "max": edge, "why": None}
        e["max"] = max(e["max"], edge)
        if not traded_fn(e["t0"]):
            e["why"] = reason_fn()                    # причина — пока окно ещё открыто
        return
    if e:
        EP.pop(key)
        if traded_fn(e["t0"]):
            add("✅ взяли", 0)
        elif now - e["t0"] < short_sec:
            add("короткое окно — не успели", e["max"] * usd)
        else:
            add(e["why"] or "другое", e["max"] * usd)

def scan():
    now = time.time()
    X, H = sys.modules.get("xexec"), sys.modules.get("xshop")
    if X and X.S and not X.DRY:
        for coin in X.COINS:
            res, _ = X.check(coin)
            for v in X.VENUES:
                if X.is_paper(v):
                    continue
                edge = res.get(v, {}).get("edge")
                track(f"S1 {coin}@{v}", edge, X.MIN_EDGE, X.TRADE, now,
                      lambda t0, c=coin: s1_traded(X, c, t0),
                      lambda c=coin, v=v: s1_reason(X, c, v), (X.CONFIRM + 1) * X.STEP_SEC)
    if H and H.S.get("c") and not H.DRY:
        for coin in H.COINS:
            w = H.window(coin)
            n0 = H.S["c"].get(coin, {}).get("trades", 0)
            key = f"SH {coin}"
            if w and key not in EP and w["edge"] >= H.MIN_EDGE:
                EP.setdefault("_n", {})[key] = n0
            track(key, w["edge"] if w else None, H.MIN_EDGE, H.TRADE, now,
                  lambda t0, c=coin, k=key: H.S["c"].get(c, {}).get("trades", 0) > EP.get("_n", {}).get(k, 1e9),
                  lambda c=coin, w=w: sh_reason(H, c, w), H.STEP * 1.5)

def text():
    d = M.get("d", {})
    got = d.get("✅ взяли", [0, 0])[0]
    miss = {k: v for k, v in d.items() if k != "✅ взяли"}
    if not got and not miss:
        return "🎯 Окон по живым монетам сегодня не было"
    lost = sum(v[1] for v in miss.values())
    parts = [f"{k} — {v[0]}" for k, v in sorted(miss.items(), key=lambda kv: -kv[1][0])]
    return (f"🎯 ОКНА: взяли {got}, упустили {sum(v[0] for v in miss.values())} (≈${lost:.2f})"
            + (": " + "; ".join(parts) if parts else ""))

def loop():
    time.sleep(150)
    load()
    while True:
        try:
            d = datetime.now(TZ).strftime("%Y-%m-%d")
            if M["day"] != d:
                M["day"], M["d"] = d, {}
            scan()
            if time.time() - M.get("_saved", 0) > 30:          # в базу — раз в 30 с, не каждые 3
                M["_saved"] = time.time()
                save()
        except Exception:
            log.exception("xmiss")
        time.sleep(STEP)

@mm.app.route("/miss")
def miss_page():
    if not (mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN):
        return "forbidden", 403
    return jsonify({"today": M.get("d", {}), "total": M.get("total", {}), "open_now": list(k for k in EP if k != "_n"),
                    "text": text()})

threading.Thread(target=loop, daemon=True).start()
