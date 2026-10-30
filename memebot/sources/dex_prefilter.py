"""
dex_prefilter.py - v8.3: prima scrematura ECONOMICA stile filtri DexScreener (PAPER, sola lettura).

Usa solo i campi gia scaricati da DexScreener /tokens/v1 (nessuna chiamata in piu) e viene applicata PRIMA dei
controlli costosi del bot (RPC cluster, RugCheck, honeypot). Ogni soglia e una chiave CFG "PF_*" modificabile da
overrides.json entro LIMITS; il Learner (learn_step) le allenta o le stringe di un passo, al massimo una per ora.
Il prefiltro non sostituisce e non salta nessun filtro HARD: puo solo scartare prima.

Nota: DexScreener non da gli acquirenti UNICI: "acquirenti" = numero di acquisti (txns buys).
"""
from __future__ import annotations

import json
import os
import time

# chiave -> (default, (min, max), passo del Learner, direzione "allenta": -1 = abbassare allenta, +1 = alzare allenta)
PF = {
    "PF_MIN_LIQ_USD":   (8000.0,   (3000.0, 50000.0),   1000.0, -1),
    "PF_MIN_VOL_M5":    (500.0,    (0.0, 20000.0),      250.0,  -1),
    "PF_MIN_TXNS_M5":   (10.0,     (0.0, 200.0),        5.0,    -1),
    "PF_MIN_TXNS_H1":   (150.0,    (0.0, 3000.0),       25.0,   -1),
    "PF_MIN_BUYS_M5":   (5.0,      (0.0, 100.0),        2.0,    -1),
    "PF_MIN_BSR_H1":    (0.5,      (0.2, 1.5),          0.05,   -1),
    "PF_MIN_BSR_M5":    (0.3,      (0.0, 1.5),          0.05,   -1),
    "PF_MIN_AGE_MIN":   (3.0,      (0.0, 60.0),         1.0,    -1),
    "PF_MAX_AGE_H":     (2160.0,   (6.0, 8760.0),       24.0,   +1),
    "PF_MIN_CHG_M5":    (-25.0,    (-60.0, 0.0),        2.0,    -1),
    "PF_MIN_CHG_H1":    (-50.0,    (-90.0, 0.0),        5.0,    -1),
    "PF_MAX_CHG_H1":    (10000.0,  (500.0, 20000.0),    500.0,  +1),
    "PF_MIN_MCAP":      (15000.0,  (0.0, 500000.0),     5000.0, -1),
    "PF_MAX_MCAP":      (5.0e8,    (1.0e6, 5.0e9),      5.0e7,  +1),
    "PF_REQUIRE_SOCIAL": (0.0,     (0.0, 1.0),          1.0,    -1),
    "PF_REQUIRE_PAID":  (0.0,      (0.0, 1.0),          1.0,    -1),
}
DEFAULTS = {k: v[0] for k, v in PF.items()}
LIMITS = {k: v[1] for k, v in PF.items()}
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "prefilter_log.jsonl")


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def evaluate(m, cfg, paid=False):
    """Funzione pura. m = summarize() di desk.py (o pair_metrics di trending_agg). Ritorna (fails, snapshot):
    fails = nomi delle soglie non superate (lista vuota = passa)."""
    g = lambda k: _f(cfg.get(k, DEFAULTS[k]), DEFAULTS[k])  # noqa: E731
    tx5 = int(_f(m.get("buys_m5"))) + int(_f(m.get("sells_m5")))
    tx1 = int(_f(m.get("buys_h1"))) + int(_f(m.get("sells_h1")))
    bsr1 = _f(m.get("buys_h1")) / max(1.0, _f(m.get("sells_h1")))
    bsr5 = _f(m.get("buys_m5")) / max(1.0, _f(m.get("sells_m5")))
    age = m.get("age_min")
    social = bool(m.get("twitter") or m.get("telegram") or m.get("website") or m.get("social"))
    mcap = _f(m.get("mcap"))
    snap = {"liq": round(_f(m.get("liq"))), "vol_m5": round(_f(m.get("vol_m5"))), "vol_h1": round(_f(m.get("vol_h1"))),
            "tx_m5": tx5, "tx_h1": tx1, "buys_m5": int(_f(m.get("buys_m5"))), "bsr_h1": round(bsr1, 2),
            "bsr_m5": round(bsr5, 2), "age_min": round(age, 1) if age is not None else None,
            "chg_m5": round(_f(m.get("chg_m5")), 1), "chg_h1": round(_f(m.get("chg_h1")), 1), "mcap": round(mcap),
            "social": social, "paid": bool(paid), "price": _f(m.get("price"))}
    checks = [
        ("PF_MIN_LIQ_USD", snap["liq"] < g("PF_MIN_LIQ_USD")),
        ("PF_MIN_VOL_M5", snap["vol_m5"] < g("PF_MIN_VOL_M5")),
        ("PF_MIN_TXNS_M5", tx5 < g("PF_MIN_TXNS_M5")),
        ("PF_MIN_TXNS_H1", tx1 < g("PF_MIN_TXNS_H1")),
        ("PF_MIN_BUYS_M5", snap["buys_m5"] < g("PF_MIN_BUYS_M5")),
        ("PF_MIN_BSR_H1", tx1 > 0 and bsr1 < g("PF_MIN_BSR_H1")),
        ("PF_MIN_BSR_M5", tx5 > 0 and bsr5 < g("PF_MIN_BSR_M5")),
        ("PF_MIN_AGE_MIN", age is not None and age < g("PF_MIN_AGE_MIN")),
        ("PF_MAX_AGE_H", age is not None and age > g("PF_MAX_AGE_H") * 60),
        ("PF_MIN_CHG_M5", snap["chg_m5"] < g("PF_MIN_CHG_M5")),
        ("PF_MIN_CHG_H1", snap["chg_h1"] < g("PF_MIN_CHG_H1")),
        ("PF_MAX_CHG_H1", snap["chg_h1"] > g("PF_MAX_CHG_H1")),
        ("PF_MIN_MCAP", mcap > 0 and mcap < g("PF_MIN_MCAP")),
        ("PF_MAX_MCAP", mcap > g("PF_MAX_MCAP")),
        ("PF_REQUIRE_SOCIAL", g("PF_REQUIRE_SOCIAL") >= 0.5 and not social),
        ("PF_REQUIRE_PAID", g("PF_REQUIRE_PAID") >= 0.5 and not paid),
    ]
    return [k for k, bad in checks if bad], snap


def log_eval(mint, sym, src, fails, snap, path=None, now=None):
    """Registro di ogni valutazione (passati e scartati) per il Learner: prefilter_log.jsonl."""
    rec = {"t": round(now or time.time()), "mint": mint, "sym": sym, "src": src, "fails": fails, "m": snap}
    try:
        p = path or LOG_PATH
        if os.path.exists(p) and os.path.getsize(p) > 5_000_000:
            os.replace(p, p + ".1")
        with open(p, "a") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    except OSError:
        pass
    return rec


def load_pending(path=None, min_age=3600, max_age=3 * 3600, done=None):
    """Valutazioni con almeno 60 minuti, ancora senza esito (per l'esito a 60 min via DexScreener batch)."""
    out, now, done = [], time.time(), done or set()
    try:
        with open(path or LOG_PATH) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if min_age <= now - r["t"] <= max_age and (r["mint"], r["t"]) not in done and r["m"].get("price"):
                    out.append(r)
    except OSError:
        pass
    return out


def learn_step(outcomes, cfg_eff, min_n=10, edge=5.0):
    """outcomes: [(fails, ret_60m_pct)]. Per ogni soglia F: esiti dei token scartati SOLO da F contro quelli passati.
    Scartati meglio dei passati di >= edge punti -> allenta di un passo; peggio di >= edge -> stringe. Limiti LIMITS.
    Ritorna {chiave: (vecchio, nuovo, media_scartati, n_scartati, media_passati, n_passati)} (una sola chiave)."""
    passed = [r for f, r in outcomes if not f]
    if len(passed) < min_n:
        return {}
    ap = sum(passed) / len(passed)
    best = None
    for k, (dflt, (lo, hi), step, loosen) in PF.items():
        cut = [r for f, r in outcomes if f == [k]]
        if len(cut) < min_n:
            continue
        ac = sum(cut) / len(cut)
        if ac >= ap + edge:
            d = loosen
        elif ac <= ap - edge:
            d = -loosen
        else:
            continue
        old = _f(cfg_eff.get(k, dflt), dflt)
        new = max(lo, min(hi, old + d * step))
        if new != old and (best is None or abs(ac - ap) > abs(best[1][2] - best[1][4])):
            best = (k, (old, new, round(ac, 2), len(cut), round(ap, 2), len(passed)))
    return dict([best]) if best else {}
