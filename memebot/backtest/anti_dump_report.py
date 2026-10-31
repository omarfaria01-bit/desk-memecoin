"""Compito 10: anti-dump dopo la migrazione. SOLA LETTURA su COPIE dei CSV del bot.

Uso:
    cp shadow_trades.csv trades.csv /tmp/
    python3 backtest/anti_dump_report.py /tmp/shadow_trades.csv /tmp/trades.csv > backtest/results_anti_dump.md

Righe usate: valido != 0 e invalid_for_learning vuoto. Crash = pnl <= -50%.
Per ogni feature d'ingresso (entry_features) cerca la soglia che blocca piu crash perdendo meno vincenti, con due
vincoli: i controlli positivi (docs/kiwi_controls.md) mai bloccati, le lezioni negative sempre bloccate (se presenti).
Riporta anche l'ingresso ritardato (age_gate 10/15/20 min dalla migrazione) e una proposta di soglie in JSON da
rivedere: NESSUNA soglia viene attivata da questo script."""
import csv
import json
import math
import sys

CONTROLS = {"nxPqZ4F7ux8Vph5NFu25rEP6Kx9Gr1U6Fmj36Rjpump": "Dacity"}   # controllo positivo confermato (Kiwi)
PROBABLE_GOOD = {"4kjruqGHDsSqkMdFfCjV7Vb62HPFhtXyWc2t6JpQzDnz": "GTA6", "6GmAFSYs4gk3FDao5FzzySQpPZaWsa4rUJHacpMpUNgx": "STONK",
                 "Bv1a7cEj5aRnJQhMw4kRQqAFesMSd5LwcDnUzq52BMmr": "FLOAT", "Ce2gx9KGXJ6C9Mp5b5x1sn9Mg87JwEbrQby4Zqo3pump": "neet",
                 "HvitmAeh6TkToQonoiNwqWcrZsYdYiaSZHP7yMmspump": "LUMI"}
LESSONS = {"HRJPbDu8h4cH2gT1rKcmzNcvzX1M7jHKogS7zRJkpump": "BEAST", "npz2FhoufQ7pKqhGpaipYWacrAkidyW2yDpVWSvpump": "DESSA",
           "J5jxG3PFaWpj6BLEQExyC6FSqSvaTKNdQ9G3sZwBpump": "PILLSEY", "EYqygbhZjP7EbM4fSn8devctnksAvPAD1Bt57XBFpump": "BNB",
           "8Xg89xT8Ve34sKiYqYuMs46mycREsCA2F362UscRpump": "GLITCH", "3ApVr1xphYMpi9dn3rpqU4hwvpmCQDVwxET1rdCDpump": "HYPERCHAN"}
# feature -> direzione del blocco: 'ge' blocca valori >= soglia, 'le' blocca valori <= soglia
FEATURES = {"top10": "ge", "top1_pct": "ge", "top3_pct": "ge", "bs5": "le", "sniper_pct": "ge",
            "first20_sold_pct": "ge", "dev_first_sell_dt": "le", "serial_launcher": "ge", "discord_call": "ge",
            "min_since_migration": "le"}
BS5_HIGH = "bs5_alto"   # anche la direzione opposta del rapporto acquisti/vendite (bot che comprano in massa)


def fnum(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def load(paths):
    rows = []
    for path in paths:
        try:
            with open(path, newline="") as f:
                for r in csv.DictReader(f):
                    if str(r.get("valido", "1")).strip() == "0" or (r.get("invalid_for_learning") or "").strip():
                        continue
                    pnl = fnum(r.get("pnl_pct"))
                    if pnl is None:
                        continue
                    try:
                        fe = json.loads(r.get("entry_features") or "{}") or {}
                    except ValueError:
                        fe = {}
                    rows.append({"mint": r.get("mint", ""), "sym": r.get("symbol", ""), "pnl": pnl, "fe": fe,
                                 "rug": str(r.get("rug", "")).strip().lower() in ("1", "true", "si", "yes"),
                                 "src": path.rsplit("/", 1)[-1]})
        except OSError as e:
            print(f"> file non letto: {path} ({e})", file=sys.stderr)
    return rows


def is_crash(r):
    """v8.9: crash = pnl <= -50% oppure rug (un rug non e mai un esempio positivo, anche con pnl > 0 dopo un TP1)."""
    return r["pnl"] <= -50 or r.get("rug", False)


def is_win(r):
    return r["pnl"] > 0 and not r.get("rug", False)


def feat(r, k):
    if k == BS5_HIGH:
        return fnum(r["fe"].get("bs5"))
    return fnum(r["fe"].get(k))


def scan(rows, k, d):
    """Soglie candidate = valori osservati. Ritorna la migliore per (crash bloccati - vincenti bloccati) con i vincoli."""
    have = [r for r in rows if feat(r, k) is not None]
    if len(have) < 10:
        return None, len(have)
    crash = [r for r in have if is_crash(r)]
    best = None
    for t in sorted({feat(r, k) for r in have}):
        blk = [r for r in have if (feat(r, k) >= t if d == "ge" else feat(r, k) <= t)]
        if any(r["mint"] in CONTROLS for r in blk):
            continue
        les_have = [r for r in have if r["mint"] in LESSONS]
        if les_have and not all(r in blk for r in les_have):
            continue
        bc = sum(1 for r in blk if is_crash(r))
        bw = sum(1 for r in blk if is_win(r))
        gain = -sum(r["pnl"] for r in blk)   # pnl tolto dal totale (positivo = conviene bloccare)
        score = bc - bw
        if bc and (best is None or (score, gain) > (best["score"], best["gain"])):
            best = {"t": t, "blocked": len(blk), "crash_blocked": bc, "crash_tot": len(crash), "win_blocked": bw,
                    "win_tot": sum(1 for r in have if is_win(r)), "gain": round(gain, 1), "score": score,
                    "probable_good_blocked": sorted({PROBABLE_GOOD[r["mint"]] for r in blk if r["mint"] in PROBABLE_GOOD})}
    return best, len(have)


def age_gate(rows):
    out = {}
    for r in rows:
        for x, e in (r["fe"].get("age_gate") or {}).items():
            o = out.setdefault(x, {"n": 0, "skipped": 0, "crash": 0, "crash_avoided": 0, "pnl": [], "pnl_d": []})
            o["n"] += 1
            o["pnl"].append(r["pnl"])
            crash = is_crash(r)
            o["crash"] += crash
            if e.get("skipped"):
                o["skipped"] += 1
                o["crash_avoided"] += crash
                o["pnl_d"].append(0.0)   # trade ritardato mai partito: 0
            else:
                pd = fnum(e.get("pnl_delayed_pct"))
                o["pnl_d"].append(pd if pd is not None else r["pnl"])
                o["crash_avoided"] += crash and pd is not None and pd > -50
    return out


def avg(xs):
    return round(sum(xs) / len(xs), 2) if xs else None


def main(paths):
    rows = load(paths)
    crash = [r for r in rows if is_crash(r)]
    print("# Anti-dump dopo la migrazione (compito 10)\n")
    print(f"Righe valide: {len(rows)} (crash <= -50%: {len(crash)}, vincenti: {sum(1 for r in rows if is_win(r))}). "
          f"File: {', '.join(paths)}\n")
    les = [r for r in rows if r["mint"] in LESSONS]
    if les:
        print("Lezioni negative presenti (riportate a parte, solo come casi da bloccare): "
              + ", ".join(f"{LESSONS[r['mint']]} {r['pnl']:+.1f}%" for r in les) + "\n")
    print("## Ingresso ritardato (age_gate)\n")
    ag = age_gate(rows)
    if not ag:
        print("Nessuna riga con age_gate (servono shadow/trade aperti dopo il commit del compito 10).\n")
    else:
        print("| eta min | righe | non partiti | crash | crash evitati | pnl medio attuale | pnl medio ritardato |")
        print("|---|---|---|---|---|---|---|")
        for x in sorted(ag, key=lambda s: int(s)):
            o = ag[x]
            print(f"| {x} | {o['n']} | {o['skipped']} | {o['crash']} | {o['crash_avoided']} | {avg(o['pnl'])} | {avg(o['pnl_d'])} |")
        print()
    print("## Soglie per feature (controlli positivi mai bloccati)\n")
    print("| feature | blocca se | soglia | righe con dato | bloccati | crash bloccati | vincenti persi | pnl tolto | probabili buoni bloccati |")
    print("|---|---|---|---|---|---|---|---|---|")
    prop = {}
    for k, d in list(FEATURES.items()) + [(BS5_HIGH, "ge")]:
        best, n = scan(rows, k, d)
        if best is None:
            print(f"| {k} | {'>=' if d == 'ge' else '<='} | n/d | {n} | - | - | - | - | - |")
            continue
        print(f"| {k} | {'>=' if d == 'ge' else '<='} | {best['t']:g} | {n} | {best['blocked']} | "
              f"{best['crash_blocked']}/{best['crash_tot']} | {best['win_blocked']}/{best['win_tot']} | {best['gain']} | "
              f"{', '.join(best['probable_good_blocked']) or '-'} |")
        if best["score"] > 0 and best["gain"] > 0:
            prop[f"ANTIDUMP_{k.upper()}_{'MAX' if d == 'ge' else 'MIN'}"] = best["t"]
    print("\n## Proposta (da rivedere, NON attiva)\n")
    print("Da copiare in `overrides.example.json` solo dopo la review; il codice che le applica non esiste ancora.\n")
    print("```json\n" + json.dumps({"_proposta_compito10": prop}, indent=2) + "\n```")
    return prop


if __name__ == "__main__":
    main(sys.argv[1:] or ["/tmp/shadow_trades.csv"])
