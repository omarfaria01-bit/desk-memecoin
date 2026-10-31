"""v8.5h: uscita per liquidita crollata = parte non venduta a 0 (rug); PnL > +1000% = dato non valido;
salto di prezzo x20 in un giro non preso; correzione retroattiva una volta sola. SOLO su una copia in /tmp."""
import asyncio, csv, json, os, time
import desk as D

assert D.DATA_DIR.startswith("/tmp"), "test solo su copia in /tmp"
D.tg_send = lambda *_a, **_k: None
D.log = lambda *_a, **_k: None
D.save_state = lambda *_a, **_k: None
D.CFG["SHADOW_ROUTE_CHECK"] = False
D.HOST_PAUSE["last_resume"] = 0.0
for f in (D.SHADOW_CSV, os.path.join(D.DATA_DIR, "trades.csv"), D.LIQ_RETRO_PATH):
    if os.path.exists(f):
        os.remove(f)
D.ensure_shadow_csv()

# ---- 1. salto di prezzo: x20 scartato, la liquidita piu bassa invece si prende
o = {"last_price": 1e-5, "last_ok": 100.0, "liq": 45000.0}
assert D.price_spike(1e-5, 0.3768) and not D.price_spike(1e-5, 1.5e-4) and not D.price_spike(0, 1.0)
assert D.apply_market_tick(o, {"price": 0.3768, "liq": 272751251.0, "liq_known": True}, 200.0) is False
assert o["last_price"] == 1e-5 and o["last_ok"] == 100.0 and o["liq"] == 45000.0   # liq gonfiata ignorata
assert D.apply_market_tick(o, {"price": 0.3768, "liq": 246.0, "liq_known": True}, 230.0) is False
assert o["liq"] == 246.0 and o["last_price"] == 1e-5 and o["spike_n"] == 2       # liq crollata presa
assert D.apply_market_tick(o, {"price": 1.2e-5, "liq": 300.0, "liq_known": True}, 260.0) is True
assert o["last_price"] == 1.2e-5 and o["last_ok"] == 260.0

# ---- 2. shadow: liquidita crollata -> azzera, rug, -100% (senza vendite precedenti)
D.CFG["LIQ_CRASH_CONFIRM"] = 1   # qui si prova l'azzeramento; la conferma su 2 letture e in test_exit_price_robust.py
st = D.State()
def mk(sym, liq_now, realized=0.0, tokens=1000.0):
    now = time.time()
    return {"t": now - 600, "sym": sym, "reason": "x", "keys": ["rugcheck_down"], "flags": [], "score": 0.5,
            "thr": 0.6, "price": 1e-5, "last_price": 4e-5, "liq": liq_now, "liq_entry": 45000.0, "last_ok": now,
            "cost": 10.0, "tokens": tokens, "tokens_entry": 1000.0, "entry_mark": 0.01, "realized": realized,
            "tp_stage": 0, "peak_pct": 0.0, "stop_hits": 0, "rt": 2.0, "src": "formula", "dec": None,
            "tag": "rugcheck_down", "signals": []}
sh = mk("NEX", 246.0)
dec = D.shadow_decide(sh)
assert dec[0].startswith("liquidita crollata") and dec[2] is True and dec[3] is True, dec
st.shadow_trades["S1"] = sh
pnl = asyncio.run(D.shadow_apply(st, None, "S1", sh, dec))
row = list(csv.DictReader(open(D.SHADOW_CSV)))[-1]
assert pnl == -100.0 and row["pnl_pct"] == "-100.0" and row["rug"] == "1" and row["valido"] == "1", row
assert st.learner_shadow["rugcheck_down"]["hist"][-1][:2] == [-100.0, 1]
# con una presa di profitto gia fatta: il realizzato resta, il resto vale 0
sh = mk("SAFU", 246.0, realized=6.5, tokens=500.0)
st.shadow_trades["S2"] = sh
pnl = asyncio.run(D.shadow_apply(st, None, "S2", sh, D.shadow_decide(sh)))
assert abs(pnl - (-35.0)) < 1e-9, pnl

# ---- 3. PnL > +1000% -> valido=0, rug=1, fuori dal Learner e dai bonus
n0 = len(st.learner_shadow["rugcheck_down"]["hist"])
b0 = len(st.learner_bonus.get("hist", []))
D._close_shadow_row(st, "S3", dict(mk("Grok", 0.0), signals=["kol:x"]), 183181.39, True, "liquidita crollata del 100%")
row = list(csv.DictReader(open(D.SHADOW_CSV)))[-1]
assert row["valido"] == "0" and row["invalid_for_learning"] == "pnl_implausibile" and row["rug"] == "1", row
assert len(st.learner_shadow["rugcheck_down"]["hist"]) == n0 and len(st.learner_bonus.get("hist", [])) == b0

# ---- 4. posizione vera paper: liquidita crollata con zero -> -100%, rug, creatore in lista nera
class PB(D.PaperBroker):
    def __init__(self):
        self.realistic, self.http, self.mode = False, None, "paper"
st4 = D.State()
pos = {"symbol": "X", "tokens": 1000.0, "cost": 10.0, "realized": 0.0, "opened": time.time() - 300, "peak_pct": 5.0,
       "last_price": 4e-5, "liq": 200.0, "liq_entry": 45000.0, "creator": "CREA"}
st4.positions["M"] = dict(pos)
asyncio.run(D._sell_position(st4, PB(), "M", "liquidita crollata del 99%", zero=True))
t = st4.closed[-1]
assert t["pnl_pct"] == -100.0 and t["valido"] == 1 and "CREA" in st4.blacklist
# PnL implausibile su un trade vero: registrato valido=0, nessun esempio al modello, creatore comunque in lista nera
st4.positions["M2"] = dict(pos, realized=5000.0, creator="CREA2")
nm, no = st4.model.n, len(st4.outcomes)
asyncio.run(D._sell_position(st4, PB(), "M2", "liquidita crollata del 99%", zero=True))
t = st4.closed[-1]
assert t["valido"] == 0 and t["invalid_for_learning"] == "pnl_implausibile" and st4.model.n == nm
assert "CREA2" in st4.blacklist and len(st4.outcomes) == no
tr = list(csv.DictReader(open(os.path.join(D.DATA_DIR, "trades.csv"))))
assert tr[-1]["valido"] == "0"

# ---- 5. retroattivo: una volta sola, su righe sintetiche
for f in (D.SHADOW_CSV, D.LIQ_RETRO_PATH):
    if os.path.exists(f):
        os.remove(f)
D.ensure_shadow_csv()
st5 = D.State()
t0 = int(time.time()) - 3000
fmt = lambda ts: time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
base = {k: "" for k in D.SHADOW_FIELDS}
base.update(aperto=fmt(t0 - 600), chiuso=fmt(t0), valido="1", rug="1", chiavi="rugcheck_down")
rows = [dict(base, mint="BIG", symbol="NEX", pnl_pct="405686.16", peak_pct="1349998.53", uscita="liquidita crollata del 99%"),
        dict(base, mint="NOTP", symbol="HI", pnl_pct="-99.22", peak_pct="10", uscita="liquidita crollata del 100%"),
        dict(base, mint="SPK", symbol="NEX2", pnl_pct="89.03", peak_pct="89.03", uscita="liquidita crollata del 100%"),
        dict(base, mint="NOPATH", symbol="B", pnl_pct="-2.17", peak_pct="374", uscita="liquidita crollata del 94%"),
        dict(base, mint="OK", symbol="C", pnl_pct="12.0", peak_pct="20", uscita="trailing 15%")]
for r in rows:
    D.write_csv_row(D.SHADOW_CSV, r)
os.makedirs(D.PATH_DIR, exist_ok=True)
with open(os.path.join(D.PATH_DIR, "SPK.jsonl"), "w") as f:
    for d in ({"t": t0 - 590, "k": "shadow", "p": 3.77e-5, "l": 54074, "pnl": 0.0, "ev": "entry"},
              {"t": t0 - 300, "k": "shadow", "p": 4.67e-5, "l": 60292, "pnl": 12.73},
              {"t": t0 - 10, "k": "shadow", "p": 0.4212, "l": 229, "pnl": 89.03}):
        f.write(json.dumps(d) + "\n")
e = D._ls(st5, "rugcheck_down")
e["hist"] += [[405686.16, 1, "shadow", t0 + 20], [-99.22, 1, "shadow", t0 + 20], [12.0, 0, "shadow", t0 + 20]]
assert D.apply_liq_crash_retro(st5) == 4
got = {r["mint"]: r for r in csv.DictReader(open(D.SHADOW_CSV))}
assert got["BIG"]["valido"] == "0" and got["BIG"]["invalid_for_learning"] == "pnl_implausibile"
assert got["NOTP"]["pnl_pct"] == "-100.00" and got["NOTP"]["valido"] == "1"
assert got["SPK"]["pnl_pct"] == "-100.00", got["SPK"]   # il +89% era il salto di prezzo, ignorato
assert got["NOPATH"]["valido"] == "0" and got["NOPATH"]["invalid_for_learning"].startswith("liq_crash_retro")
assert got["OK"]["pnl_pct"] == "12.0" and got["OK"]["valido"] == "1"
h = st5.learner_shadow["rugcheck_down"]["hist"]
assert [405686.16, 1, "shadow", t0 + 20] not in h and [-100.0, 1, "shadow", t0 + 20] in h and [12.0, 0, "shadow", t0 + 20] in h
assert D.apply_liq_crash_retro(st5) == 0   # una volta sola
print("test_v85h_liq: OK")
