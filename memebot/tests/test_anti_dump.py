"""Compito 10: eta dalla migrazione e ingresso ritardato (solo misura) + script offline. Offline, copia in /tmp."""
import csv, io, json, os, sys, tempfile, contextlib
import desk as D

assert D.DATA_DIR.startswith("/tmp"), "test solo su copia in /tmp"
MINT = "M" * 40 + "pump"
# ---- eta dalla migrazione: evento PumpPortal > eta della pool > None in curva / non pump.fun
D.MIGRATED.clear()
assert D.min_since_migration(MINT, {"dex": "pumpfun"}) == (None, "in curva")
assert D.min_since_migration("X" * 44, {"dex": "raydium", "age_min": 30}) == (None, "non pump.fun")
assert D.min_since_migration(MINT, {"dex": "pumpswap", "age_min": 7.5}) == (7.5, "pool")
D.mig_note(MINT, now=1000.0)
assert D.min_since_migration(MINT, {"dex": "pumpswap", "age_min": 99}, now=1000.0 + 300) == (5.0, "ws")

# ---- ingresso ritardato: prezzo all'eta X, minimo prima di X, pnl alla chiusura
fe = {"min_since_migration": 5.0, "mig_t0": 0.0}
path = [(60, 1.0), (240, 0.4), (330, 0.5), (600, 0.6), (900, 0.7), (1300, 0.8)]   # crollo a 4 min dall'ingresso
for t, px in path:
    D.age_gate_tick(fe, px, now=t)
g = fe["age_gate"]
assert set(g) == {"10", "15", "20"}
assert g["10"]["px"] == 0.5 and g["10"]["low"] == 0.4                  # eta 10,5 alla lettura di t=330
assert g["15"]["px"] == 0.6 and g["20"]["px"] == 0.7                    # t=600 -> 15 min, t=900 -> 20 min
D.age_gate_close(fe, 0.9, 1.0)
assert g["10"]["pre_low_pct"] == -60.0 and g["10"]["pnl_delayed_pct"] == 80.0 and g["20"]["skipped"] is False
fe2 = {"min_since_migration": 12.0, "mig_t0": 0.0}
D.age_gate_tick(fe2, 1.0, now=30)
assert set(fe2["age_gate"]) == {"15", "20"}                              # gia oltre 10 all'ingresso: non conta
D.age_gate_close(fe2, 0.0, 1.0)                                          # chiuso prima dei 15 min (rug)
assert fe2["age_gate"]["15"]["skipped"] is True and fe2["age_gate"]["15"]["pnl_delayed_pct"] is None
fe3 = {"min_since_migration": None, "mig_t0": 0.0}
D.age_gate_tick(fe3, 1.0, now=10)
assert "age_gate" not in fe3                                             # token in curva: niente
# apply_market_tick aggiorna anche l'age_gate (stesso prezzo gia letto, zero chiamate)
o = {"last_price": 1.0, "liq": 5e4, "fe": {"min_since_migration": 9.0, "mig_t0": 0.0}}
D.apply_market_tick(o, {"price": 1.1, "liq": 5e4, "liq_known": True}, 120)
assert o["fe"]["age_gate"]["10"]["px"] == 1.1

# ---- script offline su CSV sintetico (indirizzi finti; un controllo positivo e una lezione negativa marcati)
sys.path.insert(0, os.path.join(os.path.dirname(D.__file__), "backtest"))
import anti_dump_report as R  # noqa: E402

rows = []
for i in range(40):
    crash = i % 3 == 0
    fe = {"top10": 60 if crash else 20 + i % 10, "bs5": 1.5, "min_since_migration": 4 if crash else 25,
          "age_gate": {"10": {"skipped": crash, "pnl_delayed_pct": None if crash else 5.0}}}
    rows.append({"mint": f"T{i}", "symbol": f"T{i}", "pnl_pct": -70 if crash else (15 if i % 2 else -5),
                 "valido": "1", "invalid_for_learning": "", "entry_features": json.dumps(fe)})
rows.append({"mint": "nxPqZ4F7ux8Vph5NFu25rEP6Kx9Gr1U6Fmj36Rjpump", "symbol": "Dacity", "pnl_pct": 40, "valido": "1",
             "invalid_for_learning": "", "entry_features": json.dumps({"top10": 62, "min_since_migration": 30})})
rows.append({"mint": "Z", "symbol": "Z", "pnl_pct": -100, "valido": "0", "invalid_for_learning": "x",
             "entry_features": json.dumps({"top10": 90})})                 # riga non valida: esclusa
p = os.path.join(tempfile.mkdtemp(), "shadow_trades.csv")
with open(p, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    prop = R.main([p])
out = buf.getvalue()
assert "Righe valide: 41" in out, out[:200]
# top10 >= 60 bloccherebbe Dacity (62): il controllo positivo vieta quella soglia -> soglia piu alta o nessuna
best, _ = R.scan(R.load([p]), "top10", "ge")
assert best is None or best["t"] > 62
best, _ = R.scan(R.load([p]), "min_since_migration", "le")
assert best and best["t"] == 4 and best["crash_blocked"] == 14 and best["win_blocked"] == 0, best
assert prop.get("ANTIDUMP_MIN_SINCE_MIGRATION_MIN") == 4
assert "| 10 | 40 | 14 | 14 | 14 |" in out, out
print("test_anti_dump OK")
