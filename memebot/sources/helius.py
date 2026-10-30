"""v8.2: Helius (piano free) come RPC principale, con limiti e contatore crediti.

La chiave si legge SOLO da os.environ["HELIUS_API_KEY"] e l'URL si costruisce a runtime: mai scritta in file,
log o eventi (usare mask() su ogni testo che puo contenere un URL). Senza chiave tutto torna agli RPC pubblici.
Piano free: 1M crediti/mese, 10 richieste/s RPC, 2/s DAS, getProgramAccounts 5/s. 'funded-by' = solo piani a
pagamento (403), quindi non usato.
"""
import json
import math
import os
import re
import threading
import time
from datetime import datetime

BASE = "https://mainnet.helius-rpc.com/?api-key="
CFG = {
    "MONTHLY_CREDITS": 1_000_000,
    "BUDGET_FRAC": 0.85,       # usiamo al massimo l'85% del mese (margine)
    "RPS": 6.0,                # sotto il limite di 10/s (a 8/s arrivavano 429)
    "DAS_RPS": 1.5,            # sotto il limite di 2/s
    "GPA_RPS": 4.0,            # sotto il limite di 5/s
    "SLACK": 1500,             # crediti di anticipo concessi rispetto al ritmo della giornata
    "LOW_FRAC": 0.6,           # le chiamate "low" (KOL) solo sotto il 60% del ritmo
    "HOUR_FRAC": 1.3,          # bulk/low: al massimo 1.3 x (budget del giorno / 24) in ogni ora
    "CORE_STOP": 0.97,         # oltre il 97% del budget del giorno: niente Helius nemmeno per le chiamate core
    "COOLDOWN_429": 8.0,       # dopo un 429: 8 s solo RPC pubblici
    # v8.4: budget del CICLO mensile (piano Free: 1M crediti/mese, documentazione helius.dev/docs/billing).
    # Mai oltre il piano gratuito: ritmo del giorno = residuo (fino a STOP_AT) / giorni rimanenti del ciclo.
    "CYCLE_DAYS": 30,          # ciclo stimato: 30 giorni dal primo uso registrato (prudente se il ciclo vero e prima)
    "SAVE_AT": 0.80,           # dall'80% del mese: solo chiamate core (cluster/holder "bulk" e KOL vanno al pubblico)
    "STOP_AT": 0.95,           # dal 95% del mese: niente Helius, solo RPC pubblici
}
COST = {"getProgramAccounts": 10, "getProgramAccountsV2": 1, "getTokenAccounts": 10, "getAsset": 10,
        "getTransactionsForAddress": 10, "getTransfersByAddress": 10}
DAS = {"getTokenAccounts", "getAsset", "getAssetBatch", "searchAssets", "getAssetsByOwner", "getSignaturesForAsset"}
USAGE_PATH = None              # impostato da desk.py (DATA_DIR/helius_usage.json), contiene solo contatori
_MASK = re.compile(r"(api-key=)[^&\s'\"]+", re.I)
_lock = threading.Lock()
_next = {"rpc": 0.0, "das": 0.0, "gpa": 0.0}
_cool = [0.0]
U = {"day": "", "credits": 0, "calls": 0, "r429": 0, "fails": 0, "by_method": {}, "hours": {}, "month": "",
     "month_credits": 0, "skipped": 0,
     # v8.4: ciclo mensile persistente
     "cycle_start": "", "cycle_credits": 0, "day_start_cycle": 0, "official": None}
_saved = [0.0]


def key():
    return (os.environ.get("HELIUS_API_KEY") or "").strip() or None


def on():
    return key() is not None


def url():
    k = key()
    return BASE + k if k else None


def is_helius(u):
    return isinstance(u, str) and ("helius-rpc.com" in u or "helius.xyz" in u)


def mask(s):
    s = _MASK.sub(r"\1***", str(s))
    k = key()
    return s.replace(k, "***") if k else s


def cost(method):
    return COST.get(method, 1)


def cycle_bounds(now=None):
    """(inizio, fine) del ciclo in secondi. Dal ciclo ufficiale (Admin API) se noto, altrimenti CYCLE_DAYS dal
    primo uso registrato."""
    now = now or time.time()
    try:
        start = datetime.strptime(U.get("cycle_start") or "", "%Y-%m-%d").timestamp()
    except ValueError:
        start = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    off = U.get("official") or {}
    if off.get("end"):
        try:
            return start, datetime.strptime(off["end"], "%Y-%m-%d").timestamp()
        except ValueError:
            pass
    return start, start + CFG["CYCLE_DAYS"] * 86400


def _roll(now=None):
    now = now or time.time()
    d = datetime.fromtimestamp(now)
    day, mon = d.strftime("%Y-%m-%d"), d.strftime("%Y-%m")
    if U["month"] != mon:
        U["month"], U["month_credits"] = mon, 0
    if not U.get("cycle_start"):   # primo avvio v8.4: il ciclo parte oggi e include quanto gia speso oggi
        U.update(cycle_start=day, cycle_credits=max(int(U.get("cycle_credits") or 0), int(U.get("credits") or 0)),
                 day_start_cycle=0)
    _s, end = cycle_bounds(now)
    if now >= end and not (U.get("official") or {}).get("end"):   # nuovo ciclo (stimato)
        U.update(cycle_start=day, cycle_credits=0, day_start_cycle=0)
    if U["day"] != day:
        U.update(day=day, credits=0, calls=0, r429=0, fails=0, by_method={}, hours={}, skipped=0,
                 day_start_cycle=U.get("cycle_credits", 0))
    return d


def days_left(now=None):
    now = now or time.time()
    _s, end = cycle_bounds(now)
    return max(1, int(math.ceil((end - now) / 86400.0)))


def cycle_pct(now=None):
    return float(U.get("cycle_credits") or 0) / CFG["MONTHLY_CREDITS"]


def level(now=None):
    p = cycle_pct(now)
    return "stop" if p >= CFG["STOP_AT"] else ("risparmio" if p >= CFG["SAVE_AT"] else "ok")


def apply_official(d, now=None):
    """v8.4: consumo UFFICIALE dall'Admin API Helius (solo se c'e HELIUS_PROJECT_ID): sostituisce la stima."""
    if not isinstance(d, dict) or d.get("creditsUsed") is None:
        return False
    with _lock:
        cyc = d.get("creditCycle") or {}
        lim = ((d.get("subscriptionDetails") or {}).get("creditsLimit"))
        if cyc.get("start"):
            U["cycle_start"] = cyc["start"]
        U["official"] = {"end": cyc.get("end"), "limit": lim, "ts": time.time()}
        U["cycle_credits"] = int(d["creditsUsed"])
    save(force=True)
    return True


def load(path):
    global USAGE_PATH
    USAGE_PATH = path
    try:
        with open(path) as f:
            d = json.load(f)
        if isinstance(d, dict):
            U.update({k: d[k] for k in U if k in d})
    except Exception:  # noqa: BLE001
        pass
    _roll()


def save(force=False):
    if not USAGE_PATH or (not force and time.time() - _saved[0] < 60):
        return
    _saved[0] = time.time()
    try:
        with _lock:
            data = json.dumps(U)
        tmp = USAGE_PATH + ".tmp"
        with open(tmp, "w") as f:
            f.write(data)
        os.replace(tmp, USAGE_PATH)
    except Exception:  # noqa: BLE001
        pass


def day_budget(now=None):
    """v8.4: ritmo del giorno = residuo del ciclo (fino a STOP_AT) a inizio giornata / giorni rimanenti."""
    left = CFG["MONTHLY_CREDITS"] * CFG["STOP_AT"] - float(U.get("day_start_cycle") or 0)
    return max(0.0, left) / days_left(now)


def pace_budget(now=None):
    """Crediti che possiamo aver speso a quest'ora del giorno (ritmo uniforme) + anticipo."""
    d = _roll(now)
    frac = (d.hour * 3600 + d.minute * 60 + d.second) / 86400.0
    return day_budget() * frac + CFG["SLACK"]


def allow(method, tier="bulk", now=None):
    """tier: 'core' (holder, mint, honeypot), 'bulk' (analisi cluster per holder), 'low' (KOL, extra)."""
    if not on():
        return False
    now = now or time.time()
    with _lock:
        _roll(now)
        if now < _cool[0]:
            return False
        lv = level(now)   # v8.4: mai oltre il piano gratuito
        if lv == "stop" or (lv == "risparmio" and tier != "core"):
            U["skipped"] += 1
            return False
        used, c = U["credits"], cost(method)
        if used + c > day_budget() * CFG["CORE_STOP"]:
            ok = False
        elif tier == "core":
            ok = True
        else:
            hour_cap = day_budget() / 24.0 * CFG["HOUR_FRAC"]
            hour_used = U["hours"].get(datetime.fromtimestamp(now).strftime("%H"), 0)
            frac = CFG["LOW_FRAC"] if tier == "low" else 1.0
            ok = used + c <= pace_budget(now) * frac and hour_used + c <= hour_cap * frac
        if not ok:
            U["skipped"] += 1
        return ok


def _slot(method):
    lane = "das" if method in DAS else ("gpa" if method.startswith("getProgramAccounts") else "rpc")
    rps = {"das": CFG["DAS_RPS"], "gpa": CFG["GPA_RPS"], "rpc": CFG["RPS"]}[lane]
    with _lock:
        now = time.time()
        t = max(now, _next[lane], _next["rpc"])
        _next[lane] = t + 1.0 / rps
        if lane != "rpc":
            _next["rpc"] = max(_next["rpc"], t + 1.0 / CFG["RPS"])
        return t - now


async def acquire(method):
    import asyncio
    w = _slot(method)
    if w > 0:
        await asyncio.sleep(w)


def acquire_sync(method):
    w = _slot(method)
    if w > 0:
        time.sleep(w)


def note(method, status=200, ok=True):
    """Registra una chiamata fatta a Helius. 429 -> pausa; le risposte fallite contano comunque (prudenza)."""
    with _lock:
        d = _roll()
        c = cost(method) if status != 403 else 0
        U["credits"] += c
        U["month_credits"] += c
        U["cycle_credits"] = int(U.get("cycle_credits") or 0) + c
        U["calls"] += 1
        U["by_method"][method] = U["by_method"].get(method, 0) + c
        h = d.strftime("%H")
        U["hours"][h] = U["hours"].get(h, 0) + c
        if status == 429:
            U["r429"] += 1
            _cool[0] = time.time() + CFG["COOLDOWN_429"]
        elif not ok:
            U["fails"] += 1
            U.setdefault("fail_status", {})
            U["fail_status"][str(status)] = U["fail_status"].get(str(status), 0) + 1
    save()


def summary():
    with _lock:
        _roll()
        last_h = U["hours"].get(datetime.now().strftime("%H"), 0)
        prev = sorted(U["hours"].items())
        hrs = max(1, len([1 for _, v in prev if v]))
        return {"on": on(), "credits_today": U["credits"], "day_budget": int(day_budget()),
                "month_credits": U["month_credits"], "calls_today": U["calls"], "credits_this_hour": last_h,
                "avg_per_hour": round(U["credits"] / hrs), "r429": U["r429"], "fails": U["fails"],
                "skipped": U["skipped"], "top": sorted(U["by_method"].items(), key=lambda x: -x[1])[:4],
                # v8.4
                "cycle_credits": U.get("cycle_credits", 0), "limit": CFG["MONTHLY_CREDITS"],
                "cycle_pct": round(cycle_pct() * 100, 2), "level": level(), "days_left": days_left(),
                "cycle_start": U.get("cycle_start"), "official": bool(U.get("official"))}


def wait_estimate(method):
    lane = "das" if method in DAS else ("gpa" if method.startswith("getProgramAccounts") else "rpc")
    with _lock:
        return max(0.0, max(_next[lane], _next["rpc"]) - time.time())


def projection(now=None):
    """v8.4: crediti a fine ciclo se si continua al ritmo di oggi (stima)."""
    now = now or time.time()
    d = datetime.fromtimestamp(now)
    frac = max(0.1, (d.hour * 3600 + d.minute * 60 + d.second) / 86400.0)
    rate = float(U.get("credits") or 0) / frac
    return int(round(float(U.get("cycle_credits") or 0) + rate * (1 - frac) + rate * max(0, days_left(now) - 1)))


ADMIN = "https://admin-api.helius.xyz/v0/admin/projects/{}/usage"


def fetch_official(timeout=10.0):
    """v8.4: consumo ufficiale del ciclo (Admin API, costa 0 crediti RPC) SOLO se c'e env HELIUS_PROJECT_ID.
    La chiave va nell'header X-Api-Key, mai nell'URL o nei log. Ritorna True se aggiornato."""
    pid = (os.environ.get("HELIUS_PROJECT_ID") or "").strip()
    if not pid or not on() or not re.fullmatch(r"[A-Za-z0-9-]{8,64}", pid):
        return False
    import urllib.request
    try:
        req = urllib.request.Request(ADMIN.format(pid), headers={"X-Api-Key": key(), "User-Agent": "desk-bot"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return apply_official(json.loads(r.read().decode()))
    except Exception:  # noqa: BLE001
        return False
