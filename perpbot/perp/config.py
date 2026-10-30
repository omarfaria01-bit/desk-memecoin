"""Caricamento config.json + overrides.json (con clamp). Tutti i parametri stanno nel config."""
import json, os
from .strategy import DEFAULT_STRATEGY
from .risk import DEFAULT_RISK

# limiti degli override: nessun override puo uscire da questi intervalli
BOUNDS = {
    "strategy": {
        "donchian_n": (10, 80, int), "vol_mult": (0.5, 3.0, float), "max_ext_atr15": (0.5, 4.0, float),
        "regime_atr_min_pct": (0.05, 1.0, float), "regime_atr_max_pct": (1.0, 10.0, float),
        "near_miss_atr15": (0.0, 1.0, float), "sl_atr": (0.8, 4.0, float), "tp1_r": (0.0, 5.0, float),
        "tp1_frac": (0.0, 1.0, float), "be_offset_pct": (0.0, 1.0, float), "trail_activate_r": (0.3, 5.0, float),
        "trail_atr": (1.0, 8.0, float), "tp2_r": (0.0, 15.0, float), "max_hold_h": (4, 168, float),
        "funding_mr_enabled": (0, 1, bool), "funding_mr_rate_1h": (0.00003, 0.001, float),
        "funding_mr_stretch_atr15": (0.5, 5.0, float), "funding_block_rate_1h": (0.0001, 0.005, float),
    },
    "risk": {
        "risk_per_trade_pct": (0.1, 2.0, float), "max_positions": (1, 6, int), "max_exposure_x": (0.5, 3.0, float),
        "daily_loss_stop_pct": (1.0, 5.0, float), "max_leverage": (1, 10, float), "liq_buffer_x": (1.5, 5.0, float),
        "max_margin_per_trade_pct": (5.0, 50.0, float), "corr_threshold": (0.5, 0.95, float),
        "corr_max_same_dir": (1, 4, int), "min_notional_usd": (10.0, 200.0, float),
    },
}


def clamp(section, key, val):
    b = BOUNDS.get(section, {}).get(key)
    if b is None:
        return None, "chiave non consentita"
    lo, hi, typ = b
    try:
        if typ is bool:
            return bool(val), None
        v = typ(val)
    except (TypeError, ValueError):
        return None, "valore non valido"
    cv = min(max(v, lo), hi)
    return typ(cv), (f"limitato a {cv}" if cv != v else None)


def load_config(base_dir):
    with open(os.path.join(base_dir, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["strategy"] = {**DEFAULT_STRATEGY, **cfg.get("strategy", {})}
    cfg["risk"] = {**DEFAULT_RISK, **cfg.get("risk", {})}
    cfg["risk"]["max_leverage"] = min(float(cfg["risk"]["max_leverage"]), 10.0)
    return cfg


def apply_overrides(cfg, base_dir, log=None):
    """Ritorna (strategy_params, risk_params, note) con gli override clampati sopra il config."""
    sp, rp, notes = dict(cfg["strategy"]), dict(cfg["risk"]), []
    path = os.path.join(base_dir, "overrides.json")
    if os.path.exists(path):
        try:
            ov = json.load(open(path, encoding="utf-8"))
            for sec, tgt in (("strategy", sp), ("risk", rp)):
                for k, v in (ov.get(sec) or {}).items():
                    cv, note = clamp(sec, k, v)
                    if cv is None:
                        notes.append(f"{sec}.{k} ignorato: {note}")
                        continue
                    tgt[k] = cv
                    if note:
                        notes.append(f"{sec}.{k} {note}")
        except Exception as e:
            notes.append(f"overrides.json non valido: {e}")
    rp["max_leverage"] = min(float(rp["max_leverage"]), 10.0)
    return sp, rp, notes
