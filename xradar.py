# xradar.py — радар монет «как EPIC»: находит кандидатов раньше, чем бумага успеет насчитать доход.
#   Сигналы по каждой монете (продажа на HTX, докупка у партнёра, прямое направление):
#   1) разогрев — почти-окон за 3 ч в XR_HEAT раз больше обычного, и лучшее окно уже крупное;
#   2) новая пара — монета только что появилась на HTX и у партнёра (листинги часто дают всплески);
#   3) память — монета уже давала доход по бумаге, и у неё снова почти-окна;
#   4) доля HTX в объёме торгов выросла за сутки — на HTX появился свой спрос.
#   Кандидата радар ставит под частую проверку бумаги (xshopp, раз в 8 с) и пишет в Telegram.
#   Через сутки сверяет: стал ли кандидат доходным по бумаге — так видно, насколько радар угадывает.
#   Ничего не торгует. Страница: /radar?t=...
import os, sys, time, json, threading, logging
from flask import jsonify, request
import mm_scanner as mm
import xpaper

def env(k, d=""):
    return os.getenv(k, d).strip()

PARTNER = env("XR_PARTNER", "gate")
HEAT = float(env("XR_HEAT", "3"))              # во сколько раз почти-окон за 3 ч больше обычного
MIN_NEAR = int(env("XR_MIN_NEAR", "4"))        # минимум почти-окон за 3 ч
MIN_BEST = float(env("XR_MIN_BEST", "1.5"))    # лучшее окно за 3 ч, % (для разогрева)
MIN_FUND = float(env("XR_MIN_FUND", "-0.5"))   # фандинг шорта, $/сутки на слот $100 — не хуже
NEW_BEST = float(env("XR_NEW_BEST", "1.5"))    # «новая пара» — только с окном от 1,5%
GOOD = float(env("XR_GOOD", "3"))              # «угадал»: через сутки бумага даёт от $3/сутки
MAX_PICKS = int(env("XR_MAX_PICKS", "6"))      # сколько кандидатов одновременно под частой проверкой
NEW_H, PICK_H, MEM_D, EVERY = 72, 12, 7, 600
log = logging.getLogger("xradar")
S = {"seen": {}, "mem": {}, "share": {}, "alerts": [], "picks": {}, "said": {}}
PICKS = set()                                  # ключи пар для xshopp: «МОНЕТА:партнёр»
RT = {"t": 0, "hour": 0, "save": time.time(), "last": None}

def g(r, i):
    return r[i] if len(r) > i else 0

def load():
    r = xpaper.q("SELECT js FROM paper_xarb WHERE id = 'radar_v1'", fetch=True)
    if r:
        S.update(json.loads(r[0][0]))

def save():
    S["alerts"] = S["alerts"][-200:]
    xpaper.q("""INSERT INTO paper_xarb (id, js) VALUES ('radar_v1', %s)
                ON CONFLICT (id) DO UPDATE SET js = EXCLUDED.js""", (json.dumps(S),))

def feats(SP, coin, now):
    """Почти-окна за 3 ч, обычный уровень за 3 ч (по прошлым 21 ч) и лучшее окно за 3 ч."""
    c = SP.S["p"].get(f"{coin}:{PARTNER}") or {}
    h = int(now // 3600)
    r3 = [r for r in c.get("h", []) if r[0] > h - 3]
    old = [r for r in c.get("h", []) if h - 24 < r[0] <= h - 3]
    near3 = sum(g(r, 11) for r in r3)
    base = sum(g(r, 11) for r in old) / max(len(old), 1) * 3
    return near3, base, (near3 + 1) / (base + 1), max([g(r, 12) for r in r3] or [0])

def share(q):
    v = {ex: q[ex][4] for ex in ("htx", "gate", "mexc") if ex in q and q[ex][4] > 0}
    return v.get("htx", 0) / sum(v.values()) if v else 0

def hourly(SP, by, now):
    """Раз в час: снимок доли HTX в объёме и память о доходных монетах."""
    h = int(now // 3600)
    for coin, q in by.items():
        if "htx" in q:
            S["share"][coin] = ([x for x in S["share"].get(coin, []) if x[0] > h - 30] + [[h, round(share(q), 4)]])[-30:]
    for k in list(SP.S["p"]):
        if k.endswith(f":{PARTNER}"):
            s = SP.summary(k, 24)
            if s and s["hours"] >= SP.MIN_HOURS and s["net_usd_day"] >= GOOD:
                S["mem"][k.split(":")[0]] = now
    for a in S["alerts"]:                                      # через сутки — проверка: угадал ли радар
        if a[5] is None and now - a[0] > 86400:
            s = SP.summary(f"{a[1]}:{PARTNER}", 24)
            a[5] = s["net_usd_day"] if s else 0.0

def dshare(coin, h):
    xs = S["share"].get(coin) or []
    old = [x for x in xs if x[0] <= h - 20]
    return xs[-1][1] - old[-1][1] if xs and old else 0.0

def step(now):
    SP, XA, X, PS, AL = (sys.modules.get(m) for m in ("xshopp", "xarb", "xhedge", "xpscout", "xalloc"))
    by = XA.ST.get("by") if XA else None
    if not SP or not by or not SP.ready():
        return
    try:
        perp = set(X.perps()["mexc"]) if X else set()
    except Exception:
        perp = set()
    if not S["seen"]:                                          # первый запуск: всё, что уже есть, — не «новое»
        S["seen"] = {c: now - NEW_H * 3600 for c, q in by.items() if "htx" in q and PARTNER in q}
    h, held = int(now // 3600), set(AL.A.get("coins", {})) if AL else set()
    if h != RT["hour"]:
        RT["hour"] = h
        hourly(SP, by, now)
    fund = (PS.S.get("fund") or {}) if PS else {}
    found = []
    for coin, q in by.items():
        if "htx" not in q or PARTNER not in q or coin not in perp or coin in held:
            continue
        S["seen"].setdefault(coin, now)
        near3, base, heat, best3 = feats(SP, coin, now)
        why = []
        if near3 >= MIN_NEAR and heat >= HEAT and best3 >= MIN_BEST:
            why.append(f"разогрев: почти-окон {near3} за 3 ч (обычно ~{base:.0f}), лучшее {best3:.1f}%")
        if now - S["seen"][coin] < NEW_H * 3600 and best3 >= NEW_BEST:
            why.append(f"новая пара, лучшее окно {best3:.1f}%")
        if now - S["mem"].get(coin, 0) < MEM_D * 86400 and near3 >= 3:
            why.append("уже давала доход — снова почти-окна")
        ds = dshare(coin, h)
        if ds >= 0.15 and best3 >= 1.0:
            why.append(f"доля HTX в объёме +{ds*100:.0f} п.п. за сутки")
        if not why:
            continue
        fd = (fund.get(coin) or 0) * 3 * 100                   # фандинг шорта на слот $100, $/сутки
        if fd < MIN_FUND:
            continue
        st, wd, mins = SP.route_fee(coin, PARTNER, "htx", q[PARTNER][1])
        if st in ("closed", "mismatch"):
            continue
        found.append((best3, coin, why, fd, st, wd))
    for best3, coin, why, fd, st, wd in sorted(found, reverse=True):
        S["picks"][coin] = now
        if now - S["said"].get(coin, 0) > PICK_H * 3600:
            S["said"][coin] = now
            near3 = feats(SP, coin, now)[0]
            S["alerts"].append([int(now), coin, "; ".join(why), round(best3, 2), near3, None])
            fee = f"${wd:.2f}" if wd is not None else "не известна"
            mm.tg(f"🔭 Радар: {coin} — {'; '.join(why)}. Пачка {PARTNER.upper()}→HTX {fee}, "
                  f"фандинг шорта {fd:+.2f}$/сутки. Ставлю под частую проверку бумаги. "
                  f"Через 6–12 ч смотри /shopp?coin={coin}, перед заходом — /alloc/check?coin={coin}")
    for c in [c for c, t in S["picks"].items() if now - t > PICK_H * 3600]:
        S["picks"].pop(c)
    top = sorted(S["picks"].items(), key=lambda x: -x[1])[:MAX_PICKS]
    PICKS.clear()
    PICKS.update(f"{c}:{PARTNER}" for c, _ in top)
    RT["last"] = {"кандидатов": len(found), "под_проверкой": sorted(PICKS)}

def loop():
    time.sleep(240)
    load()
    while True:
        now = time.time()
        try:
            if now - RT["t"] >= EVERY:
                RT["t"] = now
                step(now)
            if now - RT["save"] > 1800:
                RT["save"] = now
                save()
        except Exception:
            log.exception("xradar")
        time.sleep(30)

@mm.app.route("/radar")
def radar_page():
    """Радар: /radar?t=... — кто под частой проверкой, последние сигналы и сколько из них сбылось."""
    if not (mm.STATS_TOKEN and request.args.get("t") == mm.STATS_TOKEN):
        return "forbidden", 403
    done = [a for a in S["alerts"] if a[5] is not None]
    hits = [a for a in done if a[5] >= GOOD]
    return jsonify({"партнёр": PARTNER, "под_частой_проверкой": sorted(PICKS), "последний_проход": RT["last"],
                    "сбылось": f"{len(hits)} из {len(done)}" if done else "ещё нет итогов (сутки после сигнала)",
                    "память_доходных": sorted(S["mem"]),
                    "сигналы": [{"когда": time.strftime("%d.%m %H:%M", time.gmtime(a[0] + 3 * 3600)), "монета": a[1],
                                 "почему": a[2], "лучшее_окно_%": a[3], "почти_окон_3ч": a[4],
                                 "через_сутки_$": a[5]} for a in S["alerts"][-20:][::-1]],
                    "настройки": {"разогрев_x": HEAT, "почти_окон_мин": MIN_NEAR, "лучшее_окно_мин_%": MIN_BEST,
                                  "фандинг_мин_$": MIN_FUND, "угадал_от_$": GOOD}})

threading.Thread(target=loop, daemon=True).start()
