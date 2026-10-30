"""Gestione del rischio: dimensionamento (rischio fisso allo stop, leva derivata dalla volatilita),
limiti di portafoglio, guardia di correlazione, stop giornaliero. Puro, senza I/O."""
import math
from .exchange import HARD_MAX_LEVERAGE

DEFAULT_RISK = {
    "risk_per_trade_pct": 1.0, "max_positions": 4, "max_exposure_x": 3.0, "daily_loss_stop_pct": 5.0,
    "max_leverage": 10, "liq_buffer_x": 2.0, "max_margin_per_trade_pct": 30.0, "min_notional_usd": 15.0,
    "corr_threshold": 0.7, "corr_max_same_dir": 2, "corr_lookback_h": 168,
    "est_slippage_pct": 0.05,
}


def size_position(equity, entry, stop, mmr, asset_max_lev, rp, fee_taker=0.00045):
    """Nozionale tale che la perdita allo stop (piu fee e slippage stimati) sia ~risk_per_trade_pct
    dell'equity. Leva = la piu alta che tiene la liquidazione ad almeno liq_buffer_x volte la distanza
    dello stop, con tetto min(10x, max asset, config)."""
    rp = {**DEFAULT_RISK, **(rp or {})}
    d = abs(entry - stop) / entry
    if d <= 0:
        return None
    cost = 2 * fee_taker + rp["est_slippage_pct"] / 100
    notional = equity * rp["risk_per_trade_pct"] / 100 / (d + cost)
    # distanza di liquidazione isolata ~ (1/L - mmr)/(1 - mmr) >= buffer * d
    lev_liq = 1.0 / (mmr + rp["liq_buffer_x"] * d * (1 - mmr))
    cap = min(HARD_MAX_LEVERAGE, float(rp["max_leverage"]), float(asset_max_lev))
    lev = max(1.0, min(cap, lev_liq))
    lev = math.floor(lev * 10) / 10
    margin_cap = equity * rp["max_margin_per_trade_pct"] / 100
    if notional / lev > margin_cap:
        notional = margin_cap * lev
    liq_dist = (1 / lev - mmr) / (1 - mmr)
    return {"notional": notional, "leverage": lev, "stop_dist_pct": d * 100, "liq_dist_pct": liq_dist * 100,
            "risk_usd": notional * (d + cost), "lev_from_vol": round(lev_liq, 2)}


def liq_safe(side, stop, liq_px, entry, buffer_x=1.5):
    """True se la liquidazione e oltre lo stop con margine: |entry-liq| >= buffer * |entry-stop|."""
    if liq_px is None:
        return True
    if side > 0 and liq_px >= stop:
        return False
    if side < 0 and liq_px <= stop:
        return False
    return abs(entry - liq_px) >= buffer_x * abs(entry - stop) * 0.999


def returns(closes):
    return [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1]]


def corr(a, b):
    n = min(len(a), len(b))
    if n < 24:
        return None
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a); vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)


def can_open(coin, side, notional, positions, equity, day_start_equity, rp, closes_1h=None, flags=None):
    """positions: {coin: {"side": +1/-1, "notional": usd}}. Ritorna (ok, motivo, nozionale_consentito)."""
    rp = {**DEFAULT_RISK, **(rp or {})}
    flags = flags or {}
    if flags.get("stop"):
        return False, "kill switch STOP attivo", 0
    if flags.get("pause"):
        return False, "PAUSE attivo", 0
    if day_start_equity and equity <= day_start_equity * (1 - rp["daily_loss_stop_pct"] / 100):
        return False, f"stop giornaliero -{rp['daily_loss_stop_pct']}% raggiunto", 0
    if coin in positions:
        return False, "posizione gia aperta su questa coin", 0
    if len(positions) >= rp["max_positions"]:
        return False, f"massimo {rp['max_positions']} posizioni", 0
    used = sum(p["notional"] for p in positions.values())
    room = rp["max_exposure_x"] * equity - used
    if room < rp["min_notional_usd"]:
        return False, f"esposizione massima {rp['max_exposure_x']}x raggiunta", 0
    if closes_1h and coin in closes_1h:
        rc = returns(closes_1h[coin][-int(rp["corr_lookback_h"]):])
        same = []
        for c2, p in positions.items():
            if p["side"] != side or c2 not in closes_1h:
                continue
            k = corr(rc, returns(closes_1h[c2][-int(rp["corr_lookback_h"]):]))
            if k is not None and k >= rp["corr_threshold"]:
                same.append(c2)
        if len(same) >= rp["corr_max_same_dir"]:
            return False, f"correlazione: gia {len(same)} posizioni nella stessa direzione ({','.join(same)})", 0
    allowed = min(notional, room)
    if allowed < rp["min_notional_usd"]:
        return False, "nozionale troppo piccolo", 0
    return True, "ok" if allowed >= notional - 1e-9 else "ridotto per esposizione", allowed
