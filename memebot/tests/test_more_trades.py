"""Compito 15: script offline m5_momentum_report.py su dati sintetici (indirizzi finti).
Offline, solo lettura; CSV scritti in una cartella temporanea (tempfile), mai su dati reali."""
import csv, json, os, sys, tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "docs", "analysis"))
import m5_momentum_report as R  # noqa: E402

# ---- classify(): distingue le sotto-regole esatte dal messaggio di reject
assert R.classify("BOAR scartato: momento sbagliato: -16% in 5 min") == "calo_5m_raw_meno8"
assert R.classify("iCat scartato: momento sbagliato: prezzo in calo a 5 min (-3.0% da candele, minimo +0%)") \
    == "calo_5m_candele_zero"
assert R.classify("LUMI scartato: momento sbagliato: -28% in 1h") == "chg_h1_fuori_banda"
assert R.classify("HI scartato: momentum non confermato: acq/vend 5m 0.95") == "bs5_debole"
assert R.classify("FOO scartato: momentum non confermato: volume 5m 0.80x vs media 1h") == "volume_accel_debole"
assert R.classify("X scartato: prezzo sotto la VWAP 5 min (-2.3%)") == "sotto_vwap5"

# ---- stats(): esclude lezioni negative dal conteggio solo come segnalazione, mai come esempio buono
rows = [
    {"pnl_cpmm": "", "pnl_pct": "20.0", "rug": "0", "symbol": "FOO"},
    {"pnl_cpmm": "-10.0", "pnl_pct": "-10.0", "rug": "0", "symbol": "BAR"},
    {"pnl_cpmm": "", "pnl_pct": "-100.0", "rug": "1", "symbol": "HYPERCHAN"},  # lezione negativa: mai buona
]
s = R.stats(rows)
assert s["n"] == 3 and s["rug"] == 1 and s["lezioni_negative"] == ["HYPERCHAN"]
assert s["avg_pnl"] == round((20.0 - 10.0 - 100.0) / 3, 1)

# ---- end-to-end su un CSV/eventi sintetici: la fascia 0%/-8% deve emergere come unica positiva
t0 = datetime(2026, 10, 30, 20, 0)
mints = [f"M{i:02d}" for i in range(20)]
events = []
shadow = []
header = ["aperto", "chiuso", "symbol", "mint", "motivo_scarto", "chiavi", "segnali_soft", "score", "soglia",
          "prezzo_entrata", "liq_entrata", "rt_cost_pct", "quote_src", "pnl_pct", "peak_pct", "minuti", "uscita",
          "rug", "tag", "signals", "valido", "invalid_for_learning", "atr_1m_10", "stop_applied", "dyn_stop_shadow",
          "size_mult", "dyn_tp1", "dyn_trail_act", "dyn_trail_dist", "entry_features", "pnl_cpmm", "stop_level",
          "exit_fill", "exit_lag_s"]
for i, mint in enumerate(mints):
    t = t0 + timedelta(minutes=5 * i)
    # meta': calo profondo (< -8%, taglio grezzo), esito storicamente brutto
    # meta': calo lieve (0/-8%, gate candele), esito storicamente buono
    deep = i % 2 == 0
    drop = -15.0 if deep else -3.0
    pnl = -30.0 if deep else 25.0
    msg = (f"T{i} scartato: momento sbagliato: {drop:.0f}% in 5 min" if deep else
           f"T{i} scartato: momento sbagliato: prezzo in calo a 5 min ({drop:.1f}% da candele, minimo +0%)")
    events.append({"ts": t.isoformat(), "agent": "Analyst", "kind": "reject", "msg": msg, "mint": mint})
    row = {k: "" for k in header}
    row.update(aperto=t.strftime("%Y-%m-%d %H:%M"), chiuso=t.isoformat(), symbol=f"T{i}", mint=mint,
               motivo_scarto="Analyst: momento/momentum", pnl_pct=str(pnl), rug="0", tag="analyst_momentum",
               valido="1", invalid_for_learning="")
    shadow.append(row)

d = tempfile.mkdtemp()
ev_path = os.path.join(d, "events.jsonl")
sh_path = os.path.join(d, "shadow_trades.csv")
with open(ev_path, "w") as f:
    for e in events:
        f.write(json.dumps(e) + "\n")
with open(sh_path, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=header)
    w.writeheader()
    w.writerows(shadow)

shadow_rows = R.load_shadow(sh_path)
evs = R.load_events(ev_path)
buckets = R.bucket_momentum(shadow_rows, evs)
raw = R.stats(buckets["calo_5m_raw_meno8"])
candele = R.stats(buckets["calo_5m_candele_zero"])
assert raw["n"] == 10 and candele["n"] == 10
assert raw["avg_pnl"] < 0 and candele["avg_pnl"] > 0        # la fascia lieve e' quella in utile
assert "lezioni_negative" in raw and raw["lezioni_negative"] == []
print("test_more_trades: ok")
