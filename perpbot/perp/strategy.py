"""Strategia (pura, senza I/O): usata IDENTICA dal bot live-paper e dal backtest.

Trend/breakout multi-timeframe, long e short:
  - filtro trend 1h: EMA50 vs EMA200 e chiusura 1h sopra/sotto EMA50
  - filtro regime: ATR(14) 1h in % del prezzo dentro [min, max] (niente mercati morti o impazziti)
  - ingresso 15m: chiusura oltre il Donchian di N barre precedenti, volume >= media, estensione massima
  - opzionale: mean reversion su funding estremo (crowding) con prezzo tirato rispetto a EMA20 15m
Uscite: stop iniziale ATR, TP1 parziale a k*R con stop a pareggio, trailing chandelier ATR, max hold.
"""

H1 = 3_600_000
M15 = 900_000

DEFAULT_STRATEGY = {
    "ema_fast": 50, "ema_slow": 200, "atr_len": 14,
    "donchian_n": 20, "vol_mult": 1.0, "max_ext_atr15": 1.5,
    "regime_atr_min_pct": 0.25, "regime_atr_max_pct": 3.5,
    "near_miss_atr15": 0.3,
    "sl_atr": 1.5, "tp1_r": 1.5, "tp1_frac": 0.5, "be_offset_pct": 0.1,
    "trail_activate_r": 1.0, "trail_atr": 3.0, "tp2_r": 0.0, "max_hold_h": 48,
    "funding_mr_enabled": False, "funding_mr_rate_1h": 0.0001, "funding_mr_stretch_atr15": 2.0,
    "funding_block_rate_1h": 0.0005,
    "breakout_tf": "15m",          # 15m | 1h: su quale timeframe si misura la rottura del Donchian
    "donchian_n_1h": 24, "trend_min_atr": 0.0,
}


def ema(vals, n):
    out, k, e = [None] * len(vals), 2.0 / (n + 1), None
    for i, v in enumerate(vals):
        if i < n - 1:
            continue
        if e is None:
            e = sum(vals[i - n + 1:i + 1]) / n
        else:
            e = v * k + e * (1 - k)
        out[i] = e
    return out


def atr(c, n=14):
    out, a = [None] * len(c), None
    trs = []
    for i, b in enumerate(c):
        pc = c[i - 1]["c"] if i else b["c"]
        tr = max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc))
        trs.append(tr)
        if i == n:
            a = sum(trs[1:n + 1]) / n
        elif i > n:
            a = (a * (n - 1) + tr) / n
        out[i] = a
    return out


def compute_features(c1h, c15, p, funding=None):
    """Ritorna una lista di righe, una per ogni barra 15m CHIUSA, con tutto il necessario al segnale.
    funding: dict {ora_ms: tasso} (backtest) oppure None (live: il tasso corrente si aggiunge dopo)."""
    p = {**DEFAULT_STRATEGY, **(p or {})}
    if not c1h or not c15:
        return []
    cl1 = [b["c"] for b in c1h]
    ef, es, a1 = ema(cl1, int(p["ema_fast"])), ema(cl1, int(p["ema_slow"])), atr(c1h, int(p["atr_len"]))
    cl15 = [b["c"] for b in c15]
    a15, e20 = atr(c15, int(p["atr_len"])), ema(cl15, 20)
    n = int(p["donchian_n"])
    n1 = int(p.get("donchian_n_1h", 24))
    rows, j = [], -1
    for i, b in enumerate(c15):
        T = b["t"] + M15
        while j + 1 < len(c1h) and c1h[j + 1]["t"] + H1 <= T:
            j += 1
        if j < 0 or i < max(n, 20) or ef[j] is None or es[j] is None or a1[j] is None or a15[i] is None:
            rows.append(None)
            continue
        prev = c15[i - n:i]
        vavg = sum(x["v"] for x in c15[i - 20:i]) / 20
        hour = (b["t"] // H1) * H1
        prev1 = c1h[max(0, j - n1):j]
        rows.append({
            "dh1": max(x["h"] for x in prev1) if prev1 else None, "dl1": min(x["l"] for x in prev1) if prev1 else None,
            "hour_close": T % H1 == 0,
            "t": b["t"], "close_t": T, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"],
            "vavg": vavg, "dh": max(x["h"] for x in prev), "dl": min(x["l"] for x in prev),
            "atr15": a15[i], "ema20_15": e20[i], "prev_c": c15[i - 1]["c"],
            "c1h": cl1[j], "ema_f": ef[j], "ema_s": es[j], "atr1h": a1[j],
            "funding": (funding or {}).get(hour - H1, (funding or {}).get(hour)) if funding else None,
        })
    return rows


def evaluate(row, p):
    """Valuta una barra. Ritorna dict con status: signal | near_miss | none, side (+1/-1), motivi."""
    p = {**DEFAULT_STRATEGY, **(p or {})}
    if row is None:
        return {"status": "none", "reasons": ["dati insufficienti"]}
    c, a15, a1 = row["c"], row["atr15"], row["atr1h"]
    atr_pct = a1 / c * 100
    regime_ok = p["regime_atr_min_pct"] <= atr_pct <= p["regime_atr_max_pct"]
    vol_ok = row["v"] >= p["vol_mult"] * row["vavg"]
    fr = row.get("funding")
    out = None
    tf1h = p.get("breakout_tf") == "1h"
    if tf1h and (not row.get("hour_close") or row.get("dh1") is None):
        return {"status": "none", "reasons": ["attesa chiusura 1h"]}
    for side in (1, -1):
        sep = (row["ema_f"] - row["ema_s"]) * side / a1
        trend_strong = sep > p.get("trend_min_atr", 0.0) and (row["c1h"] - row["ema_f"]) * side > 0
        trend_weak = sep > 0
        if tf1h:
            level = row["dh1"] if side > 0 else row["dl1"]
            dist = (row["c1h"] - level) * side / a1
        else:
            level = row["dh"] if side > 0 else row["dl"]
            dist = (c - level) * side / a15          # >0 = rottura
        brk = dist > 0
        near = (not brk) and dist >= -p["near_miss_atr15"]
        if not (trend_weak and (brk or near)):
            continue
        fails = []
        if not trend_strong: fails.append("trend_debole")
        if not brk: fails.append("breakout_mancato")
        if not regime_ok: fails.append("regime_atr")
        if not vol_ok: fails.append("volume")
        if brk and dist > p["max_ext_atr15"]: fails.append("estensione")
        if fr is not None and fr * side > p["funding_block_rate_1h"]: fails.append("funding_affollato")
        status = "signal" if not fails else ("near_miss" if len(fails) == 1 else "none")
        cand = {"status": status, "side": side, "kind": "breakout", "reasons": fails,
                "score": round(abs(row["ema_f"] - row["ema_s"]) / a1 + min(row["v"] / max(row["vavg"], 1e-9), 3), 3),
                "atr_pct": round(atr_pct, 3), "dist_atr15": round(dist, 3)}
        if out is None or ("signal", "near_miss", "none").index(cand["status"]) < ("signal", "near_miss", "none").index(out["status"]):
            out = cand
    if p["funding_mr_enabled"] and fr is not None and (out is None or out["status"] != "signal"):
        if abs(fr) >= p["funding_mr_rate_1h"]:
            side = -1 if fr > 0 else 1
            stretch = (c - row["ema20_15"]) * (-side) / a15
            turning = (c - row["prev_c"]) * side > 0
            fails = []
            if stretch < p["funding_mr_stretch_atr15"]: fails.append("stretch_insufficiente")
            if not turning: fails.append("nessuna_inversione")
            if not regime_ok: fails.append("regime_atr")
            st = "signal" if not fails else ("near_miss" if len(fails) == 1 else "none")
            if st != "none":
                out = {"status": st, "side": side, "kind": "funding_mr", "reasons": fails,
                       "score": round(abs(fr) * 1e4, 3), "atr_pct": round(atr_pct, 3), "dist_atr15": round(stretch, 3)}
    return out or {"status": "none", "reasons": ["nessun setup"]}


def initial_exit_plan(entry, side, row, p):
    """Stop iniziale e livelli. R = distanza dallo stop."""
    p = {**DEFAULT_STRATEGY, **(p or {})}
    stop = entry - side * p["sl_atr"] * row["atr1h"]
    R = abs(entry - stop)
    return {"side": side, "entry": entry, "stop": stop, "R": R, "atr": row["atr1h"],
            "tp1": entry + side * p["tp1_r"] * R if p["tp1_r"] > 0 else None,
            "tp2": entry + side * p["tp2_r"] * R if p["tp2_r"] > 0 else None,
            "tp1_done": False, "best": entry, "opened_ms": row["close_t"]}


def after_tp1(plan, p):
    p = {**DEFAULT_STRATEGY, **(p or {})}
    plan["tp1_done"] = True
    be = plan["entry"] * (1 + plan["side"] * p["be_offset_pct"] / 100)
    if (be - plan["stop"]) * plan["side"] > 0:
        plan["stop"] = be
    return plan


def trail_update(plan, bar_h, bar_l, atr_now, p):
    """Aggiorna massimo favorevole e stop trailing (chandelier). Lo stop si muove solo a favore."""
    p = {**DEFAULT_STRATEGY, **(p or {})}
    s = plan["side"]
    plan["best"] = max(plan["best"], bar_h) if s > 0 else min(plan["best"], bar_l)
    if atr_now:
        plan["atr"] = atr_now
    gain_r = (plan["best"] - plan["entry"]) * s / plan["R"] if plan["R"] else 0
    if gain_r >= p["trail_activate_r"]:
        ts = plan["best"] - s * p["trail_atr"] * plan["atr"]
        if (ts - plan["stop"]) * s > 0:
            plan["stop"] = ts
    return plan


def max_hold_hit(plan, now_ms, p):
    p = {**DEFAULT_STRATEGY, **(p or {})}
    return p["max_hold_h"] > 0 and now_ms - plan["opened_ms"] >= p["max_hold_h"] * H1
