#!/usr/bin/env python3
"""Esporta lo stato reale del bot (state.json, trades.csv, equity.csv, events.jsonl)
in una riga `const DATA = {...};` per la Page "Desk Memecoin – Live (paper)".

Uso:  ./venv/bin/python export_dashboard.py      -> scrive dashboard_data.txt e la stampa
Per aggiornare la Page: sostituire la riga che inizia con `const DATA =` nel sorgente
della Page con il contenuto di dashboard_data.txt (EditCanvas)."""
import csv, json, os, time
from datetime import datetime
from zoneinfo import ZoneInfo

D = os.path.dirname(os.path.abspath(__file__))
TZ = ZoneInfo("Europe/Rome")
# parametri dell'utente (uguali a desk.py, letti da config.json se sovrascritti)
CFG = {"STOP_LOSS_PCT": -10.0, "CRASH_EXIT_PCT": -15.0, "TP1_PCT": 30.0, "TP1_FRACTION": 0.30,
       "TAKE_PROFIT_PCT": 80.0, "TAKE_PROFIT_FRACTION": 0.30, "BREAKEVEN_PCT": 3.0, "TRAIL_ACTIVATE_PCT": 40.0,
       "TRAIL_DISTANCE_PCT": 15.0, "MAX_HOLD_MIN": 180, "START_BALANCE": 1000.0, "FEE_PCT": 1.0,
       "SLIPPAGE_MULT": 2.0, "PRIORITY_FEE_SOL": 0.001, "SOL_USD_FALLBACK": 150.0, "MODE": "paper"}
try:
    CFG.update({k: v for k, v in json.load(open(os.path.join(D, "config.json"))).items() if k in CFG})
except Exception:
    pass


def load(name, default):
    try:
        return json.load(open(os.path.join(D, name)))
    except Exception:
        return default


st = load("state.json", {})
now = time.time()

# equity (equity.csv: iso,valore) campionata a max 80 punti
eq = []
try:
    for line in open(os.path.join(D, "equity.csv")):
        ts, v = line.strip().rsplit(",", 1)
        eq.append((ts[11:16], round(float(v), 2)))
except Exception:
    pass
if len(eq) > 80:
    step = len(eq) / 80
    eq = [eq[int(i * step)] for i in range(80)] + [eq[-1]]

positions = []
for mint, p in (st.get("positions") or {}).items():
    entry = p.get("entry_mark") or 0
    last = p.get("last_price") or 0
    # valore netto calcolato dal Risk (impatto, slippage 2 x size/liq, commissione, priority fee);
    # se manca (posizione appena aperta) stessa formula con il prezzo SOL prudente
    gross = p.get("tokens", 0) * last
    liq = max(p.get("liq", 1.0), 1.0)
    impact = gross / (liq / 2 + gross) if gross > 0 else 0.0
    slip = min(0.5, CFG["SLIPPAGE_MULT"] * gross / liq)
    value = p.get("mark_net")
    if value is None:
        value = max(0.0, gross * (1 - impact) * (1 - slip) * (1 - CFG["FEE_PCT"] / 100)
                    - CFG["PRIORITY_FEE_SOL"] * CFG["SOL_USD_FALLBACK"]) if gross > 0 else 0.0
    basis = p.get("tokens", 0) * entry
    pnl = p.get("pnl_net")
    if pnl is None:
        pnl = (value / basis - 1) * 100 if basis > 0 else 0.0
    peak = p.get("peak_pct", 0.0)
    # valori effettivi scritti dal Risk (con eventuali override da overrides.json), altrimenti i default
    ef = p.get("eff") or {}
    stop_v, tp_v = ef.get("stop", CFG["STOP_LOSS_PCT"]), ef.get("tp", CFG["TAKE_PROFIT_PCT"])
    tstart, tdist = ef.get("trail_start", CFG["TRAIL_ACTIVATE_PCT"]), ef.get("trail_dist", CFG["TRAIL_DISTANCE_PCT"])
    trail_on = peak >= tstart
    stage = int(ef.get("tp_stage", p.get("tp_stage", 0)) or 0)
    tp1_v, tp1_f = ef.get("tp1", CFG["TP1_PCT"]), ef.get("tp1_frac", CFG["TP1_FRACTION"])
    tp2_f = ef.get("tp_frac", CFG["TAKE_PROFIT_FRACTION"])
    steps = [f"TP1 {tp1_f * 100:.0f}% a +{tp1_v:.0f}%", f"TP2 {tp2_f * 100:.0f}% a +{tp_v:.0f}%"]
    tp_text = " | ".join(("[fatto] " if i < stage else "") + x for i, x in enumerate(steps))
    tp_text += " | resto: trailing" + (f"; stop a pareggio {stop_v:+.0f}%" if stage >= 1 else "")
    positions.append({
        "sym": p.get("symbol", "?"), "mint": mint, "entry": entry, "last": last, "pnl": round(pnl, 2),
        "peak": round(peak, 2), "cost": round(p.get("cost", 0), 2), "value": round(value, 2),
        "min": int((now - p.get("opened", now)) / 60),
        "stop": round(stop_v, 1), "tpStage": stage,
        "trail": (f"attivo, esce a {peak - tdist:+.1f}%" if trail_on
                  else f"si attiva a +{tstart:.0f}%"),
        "tp": tp_text,
        "score": round(p.get("score", 0), 2), "url": p.get("url", ""),
    })

trades = []
try:
    with open(os.path.join(D, "trades.csv")) as f:
        for r in csv.DictReader(f):
            trades.append({"t": r["chiuso"][5:], "sym": r["symbol"], "cost": float(r["cost"]), "pnl": float(r["pnl"]),
                           "pct": float(r["pnl_pct"]), "min": int(r["minutes"]), "reason": r["reason"]})
except Exception:
    pass
trades = trades[-40:][::-1]

events = []
try:
    lines = open(os.path.join(D, "events.jsonl")).readlines()[-400:]
    for line in lines:
        e = json.loads(line)
        events.append({"t": e.get("ts", "")[11:19], "a": e.get("agent", ""), "k": e.get("kind", ""),
                       "m": e.get("msg", "")[:180]})
except Exception:
    pass
events = events[-60:][::-1]

s = st.get("stats") or {}
equity_now = eq[-1][1] if eq else st.get("cash", CFG["START_BALANCE"])
start = st.get("period_start_equity") or CFG["START_BALANCE"]
data = {
    "updated": datetime.now(TZ).strftime("%d/%m/%Y %H:%M"),
    "since": datetime.fromtimestamp(st.get("period_start_ts", now), TZ).strftime("%d/%m %H:%M"),
    "mode": st.get("mode", CFG["MODE"]).upper(),
    "equity": round(equity_now, 2), "start": round(start, 2), "cash": round(st.get("cash", 0), 2),
    "threshold": round(st.get("threshold", 0.6), 3), "learned": (st.get("model") or {}).get("n", 0),
    "blacklist": len(st.get("blacklist") or []), "paused": bool(st.get("paused")),
    "stopFile": os.path.exists(os.path.join(D, "STOP")),
    "stats": {k: s.get(k, 0) for k in ("seen", "volume_ok", "filter_pass", "rejected", "buys", "missed", "dodged")},
    "reasons": sorted(((k, v) for k, v in (st.get("reject_reasons") or {}).items()), key=lambda x: -x[1])[:12],
    "eq": eq, "positions": positions, "trades": trades, "events": events,
    "lessons": (st.get("lessons") or [])[-8:][::-1],
}
out = "const DATA: Data = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";"
open(os.path.join(D, "dashboard_data.txt"), "w").write(out + "\n")
print(out)
