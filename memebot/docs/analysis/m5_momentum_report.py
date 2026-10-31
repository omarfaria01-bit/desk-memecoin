"""Compito 15: quale soglia Analyst (non di sicurezza) scarta token che poi vanno bene?
Offline, solo lettura, pensato per girare su copie /tmp (shadow_trades.csv + events.jsonl
di `git archive origin/data-snapshot`). Nessuna scrittura su stato/dati reali del bot.

Isola gli scarti "Analyst: momento/momentum" per sotto-regola esatta (dal messaggio di reject
in events.jsonl, agganciato per mint + minuto d'apertura dello shadow) e confronta pnl/rug tra:
  - il taglio grezzo gia esistente (chg_m5 sul tick < -8%, invariato, usato come riferimento)
  - il gate vero m5_gate sulle candele (MIN_CHANGE_M5_PCT, oggi 0%: qualsiasi calo a 5 min blocca)
  - acq/vend 5m (MIN_BUYSELL_M5), calo in 1h (MIN_CHANGE_H1_PCT), accelerazione volume (VOL_ACCEL_MIN)
  - "punteggio" vicino alla soglia (SCORE_THRESHOLD)
Righe valide per il learning: valido == '1' e invalid_for_learning == ''. Le 6 lezioni negative
(BEAST, DESSA, PILLSEY, GLITCH, BNB falso, HYPERCHAN) sono solo segnalate, mai come esempio buono.
"""
import csv
import json
import re
import sys
from datetime import datetime

NEG_LESSONS = {"BEAST", "DESSA", "PILLSEY", "GLITCH", "BNB", "HYPERCHAN"}


def load_events(path):
    evs = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("mint") and d.get("agent") == "Analyst" and d.get("kind") == "reject":
                d["_ts"] = datetime.fromisoformat(d["ts"]).replace(tzinfo=None)
                evs.append(d)
    return evs


def load_shadow(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def classify(msg):
    if "momento sbagliato" in msg:
        if "in 1h" in msg and ("in 5 min" not in msg and "a 5 min" not in msg):
            return "chg_h1_fuori_banda"
        if "in 5 min" in msg and "da candele" not in msg and "da chg_m5" not in msg:
            return "calo_5m_raw_meno8"          # taglio grezzo esistente, invariato
        if "prezzo in calo a 5 min" in msg:
            return "calo_5m_candele_zero"       # m5_gate: MIN_CHANGE_M5_PCT
        return "altro_momento"
    if "acq/vend 5m" in msg:
        return "bs5_debole"
    if "volume 5m" in msg:
        return "volume_accel_debole"
    if "sotto la VWAP" in msg:
        return "sotto_vwap5"
    return "altro"


def nearest_reject(by_mint, mint, t_open, window_sec=90):
    cands = [e for e in by_mint.get(mint, []) if abs((e["_ts"] - t_open).total_seconds()) < window_sec]
    if not cands:
        return None
    cands.sort(key=lambda e: abs((e["_ts"] - t_open).total_seconds()))
    return cands[0]


def stats(items):
    n = len(items)
    if n == 0:
        return {"n": 0}
    pnls = [float(r["pnl_cpmm"] or r["pnl_pct"]) for r in items]
    rugs = sum(1 for r in items if r["rug"] == "1")
    wins = sum(1 for p in pnls if p >= 30)
    lessons = sorted({r["symbol"] for r in items if r["symbol"].upper() in NEG_LESSONS})
    return {"n": n, "avg_pnl": round(sum(pnls) / n, 1), "rug": rugs, "rug_pct": round(100 * rugs / n, 1),
            "win30": wins, "win30_pct": round(100 * wins / n, 1), "lezioni_negative": lessons}


def valid_rows(rows):
    return [r for r in rows if r["valido"] == "1" and r["invalid_for_learning"] == ""]


def bucket_momentum(shadow_rows, events):
    by_mint = {}
    for e in events:
        by_mint.setdefault(e["mint"], []).append(e)
    for v in by_mint.values():
        v.sort(key=lambda e: e["_ts"])
    mom = valid_rows([r for r in shadow_rows if r["motivo_scarto"] == "Analyst: momento/momentum"])
    buckets = {}
    for r in mom:
        t_open = datetime.strptime(r["aperto"], "%Y-%m-%d %H:%M")
        ev = nearest_reject(by_mint, r["mint"], t_open)
        cls = classify(ev["msg"]) if ev else "non_trovato"
        buckets.setdefault(cls, []).append(r)
    return buckets


def score_near_miss(shadow_rows):
    rows = valid_rows([r for r in shadow_rows if r["motivo_scarto"].startswith("punteggio")])
    return rows


def main(shadow_path, events_path):
    shadow_rows = load_shadow(shadow_path)
    events = load_events(events_path)
    buckets = bucket_momentum(shadow_rows, events)

    print("# Compito 15: scarti non di sicurezza vs esito shadow\n")
    print(f"File: {shadow_path}, {events_path}\n")
    print("## Momentum (Analyst), per sotto-regola esatta\n")
    print("| sotto-regola | n validi | pnl medio | rug | win>=30% | lezioni negative |")
    print("|---|---|---|---|---|---|")
    for cls, items in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        s = stats(items)
        if s["n"] == 0:
            continue
        les = ", ".join(s["lezioni_negative"]) or "-"
        print(f"| {cls} | {s['n']} | {s['avg_pnl']:+.1f}% | {s['rug']}/{s['n']} ({s['rug_pct']:.0f}%) | "
              f"{s['win30']}/{s['n']} ({s['win30_pct']:.0f}%) | {les} |")

    print("\n## Soglia del punteggio (SCORE_THRESHOLD, vicino a 0.50)\n")
    near = score_near_miss(shadow_rows)
    s = stats(near)
    if s["n"]:
        print(f"n={s['n']} pnl medio={s['avg_pnl']:+.1f}% rug={s['rug']}/{s['n']} win>=30%={s['win30']}/{s['n']}")
    else:
        print("nessuna riga valida.")

    cg = buckets.get("calo_5m_candele_zero", [])
    sc = stats(cg)
    print("\n## Proposta\n")
    if sc.get("n", 0) >= 5 and sc["avg_pnl"] > 0 and sc["rug"] == 0:
        print(f"MIN_CHANGE_M5_PCT 0.0 -> -8.0 (limite di clamp OVR_LIMITS, combacia col taglio grezzo "
              f"gia esistente a -8%, invariato). Evidenza: n={sc['n']}, pnl medio {sc['avg_pnl']:+.1f}%, "
              f"rug {sc['rug']}/{sc['n']}, win>=30% {sc['win30']}/{sc['n']}.")
        print(json.dumps({"global": {"MIN_CHANGE_M5_PCT": -8.0}}, indent=2))
    else:
        print("Dati insufficienti o non favorevoli: nessuna proposta su questa soglia.")


if __name__ == "__main__":
    args = sys.argv[1:] or ["/tmp/desk_data/data/shadow_trades.csv", "/tmp/desk_data/data/events.jsonl"]
    main(*args)
