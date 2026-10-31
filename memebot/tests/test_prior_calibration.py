"""Compito 9: script offline di calibrazione di p_prior. Dati sintetici, nessuna rete, scrive solo in /tmp."""
import contextlib, csv, io, json, math, os, random, sys, tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "docs", "analysis"))
import calibrate_prior as C  # noqa: E402

# AUC e Brier di base
assert C.auc([0.1, 0.4, 0.35, 0.8], [0, 0, 1, 1]) == 0.75
assert C.auc([0.5, 0.5], [0, 1]) == 0.5 and C.auc([0.2], [1]) is None
assert C.brier([1.0, 0.0], [1, 0]) == 0.0
# isotonica: monotona crescente
xs, ys = C.fit_isotonic([0.1, 0.2, 0.3, 0.4, 0.5], [0, 1, 0, 1, 1])
assert all(ys[i] <= ys[i + 1] for i in range(len(ys) - 1)) and xs[-1] == 0.5
# dati sintetici: p_prior ordina bene ma e "spostato" (dice 0,9 quando la frequenza vera e ~0,3) -> calibrare aiuta
random.seed(7)
d = tempfile.mkdtemp()
rows = []
for i in range(400):
    true = random.random() * 0.6
    p = min(0.999, 0.5 + true)                 # sovrastima sistematica
    y = int(random.random() < true)
    rows.append({"aperto": f"2026-10-{1 + i // 20:02d} {i % 24:02d}:00", "mint": f"m{i}", "pnl_pct": 10 if y else -10,
                 "valido": "1", "invalid_for_learning": "", "entry_features": json.dumps({"p_prior": p})})
rows.append({"aperto": "2026-10-30 00:00", "mint": "bad", "pnl_pct": 50, "valido": "0", "invalid_for_learning": "x",
             "entry_features": json.dumps({"p_prior": 0.1})})
p = os.path.join(d, "shadow_trades.csv")
with open(p, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    doc = C.main(["--out", d, p])
assert doc and doc["n_train"] + doc["n_test"] == 400                     # riga non valida esclusa
assert doc["split"]["train"][1] <= doc["split"]["test"][0]               # split temporale
assert doc["brier_test"][doc["method"]] < doc["brier_test"]["raw"]      # la calibrazione migliora il Brier
assert doc["auc_test"] > 0.6
saved = json.load(open(os.path.join(d, "model_prior_calibrated.json")))
assert saved["method"] in ("platt", "isotonic") and "nessun effetto" in saved["note"]
# troppo poche righe: nessun file
d2 = tempfile.mkdtemp()
with contextlib.redirect_stdout(io.StringIO()):
    assert C.main(["--out", d2, p + ".mancante"]) is None
assert not os.path.exists(os.path.join(d2, "model_prior_calibrated.json"))
print("test_prior_calibration OK")
