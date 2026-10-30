"""
momentum.py - punteggio "giovane + momentum" per token Solana giovani (< MOMENTUM_MAX_AGE_H, default 6h).
PAPER / sola lettura. Solo stdlib. Dati: DexScreener API pubblica gratuita (fino a 30 mint per chiamata),
opzionale: attributi pool di GeckoTerminal (che il bot gia' scarica) per i buyers unici reali.

Componenti (ognuno 0..1, si fa la media pesata SOLO di quelli disponibili):
  vol_accel_5m   volume 5m annualizzato all'ora vs volume 1h        (0.8x -> 0, 2.0x -> 1)
  vol_accel_1h   volume 1h vs media oraria 6h (solo se eta' >= 1.5h)   (1.0x -> 0, 3.0x -> 1)
  buy_ratio      buys/(buys+sells) su 5m e 1h                       (50% -> 0, 70% -> 1)
  buyers_growth  buyers unici 5m*12 vs 1h (Gecko); senza Gecko: proxy sui conteggi buy (peso ridotto)
  liq_growth     crescita liquidita' tra snapshot nostri (>= 5 min)   (0% -> 0, +50% -> 1)
REGOLA: i grandi pump NON vengono MAI penalizzati. Il priceChange non entra nel punteggio.
Guardia anti-spam: se il controvalore medio per transazione a 5m e' < $5 (micro-buy dei bot), i conteggi
buy/sell non valgono (resta solo il volume in $).

Uso:
    from sources.momentum import momentum, momentum_batch, momentum_bonus
    m = momentum(mint, gecko_attrs=None)      # mai eccezioni
    bonus, tags = momentum_bonus(m, cfg)
"""
from __future__ import annotations

import asyncio
import json
import time
import urllib.request

DEX = "https://api.dexscreener.com/tokens/v1/solana/{}"
TIMEOUT = 10
CACHE_SEC = 60
MAX_AGE_H = 6.0
_pairs: dict = {}     # mint -> (ts, pair)
_snaps: dict = {}     # mint -> [(ts, liq_usd)]


def _lin(x, lo, hi):
    if x is None:
        return None
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def _fetch(mints):
    out = {}
    for i in range(0, len(mints), 30):
        chunk = mints[i:i + 30]
        req = urllib.request.Request(DEX.format(",".join(chunk)), headers={"User-Agent": "DeskMemecoin-paper/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                data = json.loads(r.read())
        except Exception:  # noqa: BLE001
            continue
        for p in data or []:
            m = (p.get("baseToken") or {}).get("address")
            liq = (p.get("liquidity") or {}).get("usd") or 0
            if m and (m not in out or liq > ((out[m].get("liquidity") or {}).get("usd") or 0)):
                out[m] = p   # pair piu' liquido
    return out


def _pair(mints):
    now = time.time()
    need = [m for m in mints if m not in _pairs or now - _pairs[m][0] > CACHE_SEC]
    if need:
        for m, p in _fetch(need).items():
            _pairs[m] = (now, p)
            liq = (p.get("liquidity") or {}).get("usd")
            if liq:
                s = _snaps.setdefault(m, [])
                s.append((now, float(liq)))
                del s[:-30]
    return {m: _pairs.get(m, (0, None))[1] for m in mints}


def _score(mint, p, gecko_attrs=None):
    now = time.time()
    if not p:
        return {"mint": mint, "score": None, "young": None, "error": "nessun pair DexScreener"}
    age_h = (now - p["pairCreatedAt"] / 1000) / 3600 if p.get("pairCreatedAt") else None
    v = p.get("volume") or {}
    tx = p.get("txns") or {}
    v5, v1, v6 = v.get("m5") or 0, v.get("h1") or 0, v.get("h6") or 0
    b5, s5 = (tx.get("m5") or {}).get("buys", 0), (tx.get("m5") or {}).get("sells", 0)
    b1, s1 = (tx.get("h1") or {}).get("buys", 0), (tx.get("h1") or {}).get("sells", 0)
    tags = []
    comp, w = {}, {}
    # token < 1h: il volume "h1" copre solo la vita del token -> lo riporto a tasso orario
    v1_rate = v1 / max(age_h, 1 / 12) if (age_h is not None and age_h < 1) else v1
    comp["vol_accel_5m"] = _lin(v5 * 12 / v1_rate, 0.8, 2.0) if v1_rate > 0 else None
    w["vol_accel_5m"] = 1.0
    if age_h and age_h >= 1.5 and v6 > 0:
        comp["vol_accel_1h"] = _lin(v1 / (v6 / 6), 1.0, 3.0)
        w["vol_accel_1h"] = 1.0
    avg_tx = v5 / (b5 + s5) if (b5 + s5) else None
    spam = avg_tx is not None and avg_tx < 5
    if spam:
        tags.append("microtx_spam")
    if not spam and (b5 + s5) >= 10:
        r5 = b5 / (b5 + s5)
        r1 = b1 / (b1 + s1) if (b1 + s1) else r5
        comp["buy_ratio"] = _lin(0.5 * r5 + 0.5 * r1, 0.5, 0.7)
        w["buy_ratio"] = 1.0
    ga = ((gecko_attrs or {}).get("transactions") or {})
    gb5, gb1 = (ga.get("m5") or {}).get("buyers"), (ga.get("h1") or {}).get("buyers")
    if gb5 is not None and gb1:
        comp["buyers_growth"] = _lin(gb5 * 12 / gb1, 0.8, 2.0)
        w["buyers_growth"] = 1.0
    elif not spam and b1:
        comp["buyers_growth"] = _lin(b5 * 12 / b1, 0.8, 2.0)
        w["buyers_growth"] = 0.5
        tags.append("buyers_proxy")
    snaps = _snaps.get(mint) or []
    old = [s for s in snaps if now - s[0] >= 300]
    if old and old[-1][1] > 0:
        comp["liq_growth"] = _lin(snaps[-1][1] / old[-1][1] - 1, 0.0, 0.5)
        w["liq_growth"] = 1.0
    avail = {k: c for k, c in comp.items() if c is not None}
    score = sum(avail[k] * w[k] for k in avail) / sum(w[k] for k in avail) if avail else None
    return {"mint": mint, "age_h": round(age_h, 2) if age_h is not None else None,
            "young": age_h is not None and age_h < MAX_AGE_H, "score": round(score, 3) if score is not None else None,
            "components": {k: (round(c, 3) if c is not None else None) for k, c in comp.items()},
            "n_components": len(avail), "tags": tags,
            "raw": {"vol_m5": v5, "vol_h1": v1, "vol_h6": v6, "buys_m5": b5, "sells_m5": s5,
                    "liq_usd": (p.get("liquidity") or {}).get("usd"), "price_change": p.get("priceChange")}}


def momentum(mint, gecko_attrs=None):
    try:
        return _score(mint, _pair([mint]).get(mint), gecko_attrs)
    except Exception as e:  # noqa: BLE001
        return {"mint": mint, "score": None, "error": f"{type(e).__name__}: {e}"[:160]}


def momentum_batch(mints, gecko=None):
    try:
        ps = _pair(list(mints))
        return {m: _score(m, ps.get(m), (gecko or {}).get(m)) for m in mints}
    except Exception as e:  # noqa: BLE001
        return {m: {"mint": m, "score": None, "error": str(e)[:120]} for m in mints}


async def amomentum(mint, gecko_attrs=None):
    return await asyncio.to_thread(momentum, mint, gecko_attrs)


def momentum_bonus(m, cfg):
    """Solo token giovani, punteggio >= MOMENTUM_MIN_SCORE (0.5), almeno 2 componenti. Mai negativo."""
    b = float(cfg.get("MOMENTUM_YOUNG_BONUS", 0.03))
    if not m.get("young") or m.get("score") is None or m.get("n_components", 0) < 2:
        return 0.0, []
    if m["score"] < float(cfg.get("MOMENTUM_MIN_SCORE", 0.5)):
        return 0.0, []
    return round(b * m["score"], 4), [f"momentum_young:{m['score']:.2f}"]


if __name__ == "__main__":
    import sys
    mints = sys.argv[1:] or ["3ApVr1xphYMpi9dn3rpqU4hwvpmCQDVwxET1rdCDpump"]
    for m, r in momentum_batch(mints).items():
        print(json.dumps(r, indent=1))
        print("bonus:", momentum_bonus(r, {}))
