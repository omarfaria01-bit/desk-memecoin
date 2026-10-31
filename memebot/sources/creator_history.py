"""v8.4e: storico del creatore (deployer) di un token pump.fun, da fonti gratuite.

Fonte: API pubblica pump.fun (frontend-api-v3), 2 chiamate per creatore nuovo, poi cache su disco.
  coins-v2/{mint}                                   -> creator, created_timestamp
  coins-v2/user-created-coins/{creator}?limit=50    -> ultimi 50 token creati
Niente Helius (budget 0): token sconosciuti a pump.fun (es. HYPERCHAN, BNB, AgenC) = 'unknown', nessun effetto.

Metrica (calibrata il 31/10 su 45 creatori degli shadow):
  - 'precedenti' = token creati PRIMA di questo, vecchi di almeno PREV_MIN_AGE_H, che hanno davvero
    tradato (ATH >= TRACTION_USD). I lanci mai partiti (~90% dei token dei deployer seriali, anche quelli
    di Bonnie e iCoin, vincenti) sono contati a parte come 'tentativi' e non pesano.
  - 'morti/rug' = oggi sotto DEAD_FROM_ATH del massimo, oppure is_banned.
  - HARD se precedenti >= HARD_MIN_N e quota morti/rug >= HARD_SHARE (regola Kiwi: >= 5 e >= 70%).
  - soft graduale: da SOFT_FROM (50%) a HARD_SHARE la penalita sale fino a SOFT_PEN_MAX, pesata per n/HARD_MIN_N.
  Storico: nessuno dei 45 creatori arriva all'HARD; Bonnie 2/3 (soft ~0.02), iCoin 8/14 (~0.014), PILLY/TYGR
  10/17, SSI 5/13 (sotto). I token cattivi BEAST/PILLSEY/GLITCH/DESSA hanno creatori al primo token (nessun effetto).
"""
import json
import os
import time

CFG = {
    "TRACTION_USD": 15000.0, "DEAD_FROM_ATH": 0.10, "PREV_MIN_AGE_H": 24.0,
    "HARD_MIN_N": 5, "HARD_SHARE": 0.70,
    "SOFT_MIN_N": 3, "SOFT_FROM": 0.50, "SOFT_PEN_MAX": 0.04,
    "CACHE_TTL_H": 12.0, "CACHE_MAX": 3000, "LIMIT": 50,
}
API = "https://frontend-api-v3.pump.fun/coins-v2"
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "creator_history_cache.json")
_MEM = {"loaded": False, "data": {"mint": {}, "creator": {}}}
# v8.7 (D): seme di reputazione dal corpus Slinky21 (giu-lug 2026): creatore -> [lanci, graduati, rug, falliti]
SEED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "creator_seed.json")
SEED = {"loaded": False, "c": {}}
SERIAL_MIN = 10


def seed_info(creator):
    """v8.7 (D): storico del creatore nel corpus, letto PRIMA delle API. None se assente. Solo informativo:
    'serial_launcher' (>= 10 lanci, 0 graduati) e un flag soft SHADOW, nessun effetto su livello/penalita."""
    if not SEED["loaded"]:
        SEED["loaded"] = True
        try:
            with open(SEED_PATH, encoding="utf-8") as fh:
                SEED["c"] = (json.load(fh) or {}).get("c") or {}
        except Exception:  # noqa: BLE001
            SEED["c"] = {}
    v = SEED["c"].get(str(creator or ""))
    if not v:
        return None
    n, g, r, f = (list(v) + [0, 0, 0, 0])[:4]
    return {"n": n, "g": g, "rug": r, "fail": f, "serial_launcher": bool(n >= SERIAL_MIN and g == 0)}


def seed_for_mint(mint, creator=None):
    """v8.7: creatore dalla cache (o dato) -> seed_info. Nessuna chiamata di rete."""
    try:
        cr = creator or ((_load()["mint"].get(mint) or {}).get("creator"))
        return (cr, seed_info(cr)) if cr else (None, None)
    except Exception:  # noqa: BLE001
        return None, None


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def evaluate(coins, mint, created_ms=None, now=None, cfg=None):
    """Funzione pura. coins = lista pump.fun dei token del creatore. Ritorna un dict con level none/soft/hard."""
    c = dict(CFG)
    c.update(cfg or {})
    now_ms = (now or time.time()) * 1000
    t0 = _f(created_ms) or now_ms
    prev = [x for x in coins or [] if isinstance(x, dict) and x.get("mint") != mint
            and 0 < _f(x.get("created_timestamp")) < t0
            and now_ms - _f(x.get("created_timestamp")) >= c["PREV_MIN_AGE_H"] * 3600e3]
    traded = [x for x in prev if _f(x.get("ath_market_cap")) >= c["TRACTION_USD"] or x.get("complete")]
    dead = [x for x in traded if x.get("is_banned")
            or _f(x.get("usd_market_cap")) < c["DEAD_FROM_ATH"] * max(_f(x.get("ath_market_cap")), 1.0)]
    n, nd = len(traded), len(dead)
    share = round(nd / n, 3) if n else 0.0
    level, pen = "none", 0.0
    if n >= c["HARD_MIN_N"] and share >= c["HARD_SHARE"]:
        level, pen = "hard", c["SOFT_PEN_MAX"]
    elif n >= c["SOFT_MIN_N"] and share >= c["SOFT_FROM"]:
        span = max(1e-9, c["HARD_SHARE"] - c["SOFT_FROM"])
        # graduale sulla quota e sulla confidenza (n/HARD_MIN_N): Bonnie 2/3 -> ~0.02, iCoin 8/14 -> ~0.014
        pen = round(c["SOFT_PEN_MAX"] * min(1.0, (share - c["SOFT_FROM"]) / span)
                    * min(1.0, n / c["HARD_MIN_N"]), 4)
        level = "soft"
    why = (f"creatore: {nd}/{n} token precedenti con trading morti o rug ({share * 100:.0f}%), "
           f"{len(prev) - n} lanci mai partiti")
    return {"level": level, "pen": pen, "prev": n, "dead": nd, "share": share, "attempts": len(prev) - n,
            "why": why}


def _load():
    if not _MEM["loaded"]:
        _MEM["loaded"] = True
        try:
            with open(CACHE_PATH, encoding="utf-8") as fh:
                d = json.load(fh)
            if isinstance(d, dict):
                _MEM["data"] = {"mint": dict(d.get("mint") or {}), "creator": dict(d.get("creator") or {})}
        except Exception:  # noqa: BLE001
            pass
    return _MEM["data"]


def _save():
    d = _MEM["data"]
    for k in ("mint", "creator"):
        if len(d[k]) > CFG["CACHE_MAX"]:
            for key in sorted(d[k], key=lambda z: d[k][z].get("t", 0))[:len(d[k]) - CFG["CACHE_MAX"]]:
                d[k].pop(key, None)
    try:
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        os.replace(tmp, CACHE_PATH)
    except OSError:
        pass


async def lookup(get, mint, creator=None, now=None, cfg=None):
    """get = coroutine url -> json|None (client http del desk, con HOST_GAPS). Mai eccezioni: 'unknown'."""
    c = dict(CFG)
    c.update(cfg or {})
    now = now or time.time()
    d = _load()
    seed = seed_info(creator) if creator else None   # v8.7 (D): prima delle API
    try:
        mi = d["mint"].get(mint)
        if not mi or now - mi.get("t", 0) > c["CACHE_TTL_H"] * 3600:
            info = await get(f"{API}/{mint}")
            if isinstance(info, dict) and info.get("creator"):
                mi = {"t": now, "creator": str(info["creator"]), "created": _f(info.get("created_timestamp"))}
            else:
                mi = {"t": now, "creator": None, "created": 0.0}
            d["mint"][mint] = mi
            _save()
        cr = mi.get("creator") or creator
        if cr and cr != creator:
            seed = seed_info(cr)
        if not cr:
            return {"level": "unknown", "pen": 0.0, "why": "creatore sconosciuto a pump.fun", "creator": None}
        ce = d["creator"].get(cr)
        if not ce or now - ce.get("t", 0) > c["CACHE_TTL_H"] * 3600:
            lst = await get(f"{API}/user-created-coins/{cr}?offset=0&limit={int(c['LIMIT'])}")
            if not isinstance(lst, dict) or not isinstance(lst.get("coins"), list):
                return {"level": "unknown", "pen": 0.0, "why": "storico creatore non disponibile", "creator": cr,
                        "seed": seed}
            keep = ("mint", "created_timestamp", "ath_market_cap", "usd_market_cap", "complete", "is_banned")
            ce = {"t": now, "count": lst.get("count"), "coins": [{k: x.get(k) for k in keep} for x in lst["coins"]
                                                                  if isinstance(x, dict)]}
            d["creator"][cr] = ce
            _save()
        r = evaluate(ce["coins"], mint, mi.get("created"), now, c)
        r.update(creator=cr, total=ce.get("count"), seed=seed)
        return r
    except Exception as e:  # noqa: BLE001
        return {"level": "unknown", "pen": 0.0, "why": f"errore storico creatore ({type(e).__name__})", "creator": None}
