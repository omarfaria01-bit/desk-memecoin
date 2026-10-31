"""Compito 12: ri-etichettatura offline. GeckoTerminal finto, file del bot finti in una cartella temporanea."""
import csv, json, os, sys, tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "tools"))
import retro_label as R  # noqa: E402

T0 = 1_800_000_000.0


def bars(path):   # path = [(minuto, prezzo)] -> candele 1m (o=h=l=c=prezzo) dal minuto 0 per 120 minuti
    out, px = [], path[0][1]
    pts = dict(path)
    for i in range(120):
        px = pts.get(i, px)
        out.append([int(T0 + i * 60), px, px, px, px, 100.0])
    return out


# ---- regole d'etichetta
assert R.label(bars([(0, 1.0), (10, 1.31)]), T0)["label"] == "win"
assert R.label(bars([(0, 1.0), (10, 0.87), (20, 1.5)]), T0)["label"] == "loss"           # SL prima del TP1
assert R.label(bars([(0, 1.0), (10, 1.0)]), T0)["label"] == "loss"                       # nessuno dei due
r = R.label(bars([(0, 1.0), (10, 1.4), (60, 0.05)]), T0)
assert r["label"] == "rug" and "dopo il TP1" in r["why"]                                   # TP1 poi rug: rug
assert R.label(bars([(0, 1.0)])[:10], T0)["label"] == "unknown"

# ---- giro completo con rete finta: idempotente e riprendibile
d = tempfile.mkdtemp()
seen_t = T0
ev = [{"ts": "2027-01-15T08:00:00+00:00", "kind": "reject", "mint": "GOOD", "msg": "prefiltro: volume", "cat": "prefiltro vol"},
      {"ts": "2027-01-15T08:00:00+00:00", "kind": "reject", "mint": "HP", "msg": "honeypot: x", "cat": "honeypot"},
      {"ts": "2027-01-15T08:00:00+00:00", "kind": "info", "mint": "IGN", "msg": "x"}]
with open(os.path.join(d, "events.jsonl"), "w") as f:
    f.write("\n".join(json.dumps(e) for e in ev) + "\n")
with open(os.path.join(d, "shadow_trades.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["aperto", "mint", "motivo_scarto", "entry_features"])
    w.writeheader()
    w.writerow({"aperto": "2027-01-15 09:00", "mint": "3ApVr1xphYMpi9dn3rpqU4hwvpmCQDVwxET1rdCDpump",
                "motivo_scarto": "x", "entry_features": json.dumps({"top10": 30})})
seen_ts = R._ts("2027-01-15T08:00:00+00:00")
CALLS = []


def fake_get(path, params=None):
    CALLS.append(path)
    if path.endswith("/pools"):
        mint = path.split("/tokens/")[1].split("/")[0]
        return {"data": [{"attributes": {"address": "POOL_" + mint[:6], "reserve_in_usd": "1000"}}]}
    t0 = int(params["before_timestamp"]) - 24 * 3600 - 60
    b = [[t0 + i * 60, 1.0, 1.35 if i == 30 else 1.0, 1.0, 1.0, 1.0] for i in range(200)]
    return {"data": {"attributes": {"ohlcv_list": b[::-1]}}}


R.get = fake_get
out = os.path.join(d, "data", "retro_labels.jsonl")
now = seen_ts + 30 * 3600
s1 = R.main(["--data-dir", d, "--out", out, "--now", str(now)])
rows = [json.loads(x) for x in open(out)]
by = {r["mint"]: r for r in rows}
assert s1["labeled_now"] == 3 and "IGN" not in by
assert by["GOOD"]["label"] == "win" and by["GOOD"]["positive"] is True
assert by["HP"]["bad"] is True and by["HP"]["positive"] is False                      # scarto duro: mai positivo
neg = by["3ApVr1xphYMpi9dn3rpqU4hwvpmCQDVwxET1rdCDpump"]
assert neg["bad"] is True and neg["positive"] is False and neg["features"] == {"top10": 30}   # lezione negativa
n_calls = len(CALLS)
s2 = R.main(["--data-dir", d, "--out", out, "--now", str(now)])
assert s2["labeled_now"] == 0 and len(CALLS) == n_calls                               # idempotente, nessuna chiamata
assert len(open(out).read().splitlines()) == 3
# fuori finestra (visto meno di 24 h fa): non etichettato
s3 = R.main(["--data-dir", d, "--out", os.path.join(d, "data", "x.jsonl"), "--now", str(seen_ts + 3600)])
assert s3["seen_in_window"] == 0
print("test_retro_label OK", s1)
