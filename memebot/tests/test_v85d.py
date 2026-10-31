"""v8.5d: pause dell'host (VM congelata). Da lanciare SOLO su una copia in /tmp."""
import csv, json, os, time
import desk as D

assert D.DATA_DIR.startswith("/tmp"), "test solo su copia in /tmp"
sent = []
D.tg_send = lambda t: sent.append(t)
st = D.State()
st.positions = {"M1": {"symbol": "A", "last_ok": 100.0, "np_wait": 2, "qmark": {"ts": 1}, "mark_noroute": 2, "noroute_fails": 1}}
st.shadow_trades = {"S1": {"sym": "B", "last_ok": 100.0, "np_src": "x"}}
assert D.host_pause_check(st, "risk", now=1000.0, mono=50.0) == 0
assert D.host_pause_check(st, "risk", now=1015.0, mono=65.0) == 0
# VM congelata: il monotonic resta quasi fermo, l'orologio salta di 25 min
g = D.host_pause_check(st, "risk", now=2515.0, mono=80.0)
assert g == 1500.0 and D.HOST_PAUSE["last_resume"] == 2515.0 and D.HOST_PAUSE["hold"]["risk"]
p = st.positions["M1"]
assert p["last_ok"] == 2515.0 and "np_wait" not in p and "qmark" not in p and p["mark_noroute"] == 0 and p["noroute_fails"] == 0
assert st.shadow_trades["S1"]["last_ok"] == 2515.0 and "np_src" not in st.shadow_trades["S1"]
assert len(sent) == 1 and "Host in pausa" in sent[0]
pp = [json.loads(x) for x in open(os.path.join(D.PATH_DIR, "M1.jsonl"))]
assert pp[-1]["k"] == "gap_host" and pp[-1]["gap"] == 1500
# l'altro ciclo vede la stessa pausa: niente doppio evento
D.LOOP_MONO["shadow"] = (1000.0, 50.0)
D.host_pause_check(st, "shadow", now=2520.0, mono=85.0)
assert D.HOST_PAUSE["n"] == 1 and len(sent) == 1
# salto del solo monotonic (wall regolare)
D.host_pause_check(st, "risk", now=2600.0, mono=200.0)
D.host_pause_check(st, "risk", now=2615.0, mono=300.0)   # wall 15 s, mono 100 s -> salto
assert D.HOST_PAUSE["n"] == 2 and len(sent) == 1, "avviso al massimo ogni 30 min: la seconda pausa resta in coda"
D.host_pause_check(st, "risk", now=2515.0 + 1801, mono=2000.0)
D.host_pause_check(st, "risk", now=2515.0 + 1816, mono=2015.0)
assert len(sent) >= 2 and "Host in pausa" in sent[1], sent
# chiusura shadow entro 5 min dalla ripresa = non valida per il Learner
D.HOST_PAUSE["last_resume"] = time.time() - 60
D.ensure_shadow_csv()
sh = {"t": time.time() - 3600, "sym": "C", "reason": "x", "keys": ["score"], "flags": [], "score": 0.5, "thr": 0.6,
      "price": 1.0, "liq_entry": 1e5, "rt": 2.0, "tag": "score", "signals": []}
before = len((st.learner_shadow.get("score") or {}).get("hist") or [])
D._close_shadow_row(st, "S9", sh, -40.0, False, "crollo a -40%")
row = list(csv.DictReader(open(D.SHADOW_CSV)))[-1]
assert row["valido"] == "0" and row["invalid_for_learning"] == "host_pause" and row["uscita"].startswith("DATI NON VALIDI")
assert len((st.learner_shadow.get("score") or {}).get("hist") or []) == before
D.HOST_PAUSE["last_resume"] = time.time() - 900
sh.pop("np_invalid", None); sh.pop("inv_why", None)
D._close_shadow_row(st, "S8", dict(sh, sym="D"), -40.0, False, "crollo a -40%")
row = list(csv.DictReader(open(D.SHADOW_CSV)))[-1]
assert row["valido"] == "1" and row["invalid_for_learning"] == ""
# retroattivo
t0 = int(time.time()) - 5000
D.write_csv_row(D.SHADOW_CSV, dict(row, mint="R1", symbol="AgenC", chiuso=time.strftime("%Y-%m-%d %H:%M", time.localtime(t0)),
                                   valido="1", invalid_for_learning="", pnl_pct="-23.5"))
e = D._ls(st, "score"); e["hist"].append([-23.5, 0, "shadow", t0]); e["hist"].append([5.0, 0, "shadow", t0])
json.dump({"closes": [["R1", t0, -23.5]]}, open(D.HOST_RETRO_PATH, "w"))
assert D.apply_host_pause_retro(st) == 1
r = [x for x in csv.DictReader(open(D.SHADOW_CSV)) if x["mint"] == "R1"][0]
assert r["valido"] == "0" and r["invalid_for_learning"] == "host_pause_retro"
assert [-23.5, 0, "shadow", t0] not in st.learner_shadow["score"]["hist"] and [5.0, 0, "shadow", t0] in st.learner_shadow["score"]["hist"]
assert D.apply_host_pause_retro(st) == 0   # una volta sola
# lezione negativa (GLITCH): esito positivo mai al Learner
GL = "8Xg89xT8Ve34sKiYqYuMs46mycREsCA2F362UscRpump"
assert GL in D.neg_lesson_mints() and "nxPqZ4F7ux8Vph5NFu25rEP6Kx9Gr1U6Fmj36Rjpump" not in D.neg_lesson_mints()
D.HOST_PAUSE["last_resume"] = 0.0
nb = len(st.learner_shadow["score"]["hist"])
D._close_shadow_row(st, GL, {**sh, "sym": "GLITCH"}, 28.3, False, "trailing")
row = list(csv.DictReader(open(D.SHADOW_CSV)))[-1]
assert row["valido"] == "0" and row["invalid_for_learning"] == "lezione_negativa" and len(st.learner_shadow["score"]["hist"]) == nb

# (A) watchlist curva: aggiunta, ancora in curva, migrato -> rientra una volta, tetto orario, scadenza 6 h
D.CURVE["d"] = {"watch": {}, "done": {}}
def pairs(dex, liq):
    return [{"dexId": dex, "pairAddress": "PA", "priceUsd": "0.001", "liquidity": {"usd": liq}, "baseToken": {"symbol": "X", "name": "X"},
             "volume": {}, "priceChange": {}, "txns": {}}]
mints = [f"C{i}" + "x" * 38 + "pump" for i in range(15)]
for k in mints:
    assert D.curve_watch_add(k, {"dex": "pumpfun", "liq": 0, "symbol": "X"}, now=1000.0)
assert not D.curve_watch_add(mints[0], {"dex": "pumpfun", "liq": 0}, now=1001.0)
assert not D.curve_watch_add("Zxx", {"dex": "pumpfun", "liq": 0}, now=1001.0)   # non pump.fun
st2 = D.State(); st2.processed[mints[0]] = 999.0
assert D.curve_watch_step(st2, {mints[0]: pairs("pumpfun", 0)}, now=2000.0) == []
D.CFG["CURVE_RECHECK_MAX_H"] = 12
out = D.curve_watch_step(st2, {k: pairs("pumpswap", 50000) for k in mints}, now=3000.0)
assert len(out) == 12 and st2.watch[mints[0]]["src"] == "curve_recheck" and mints[0] not in st2.processed
assert not D.curve_watch_add(mints[0], {"dex": "pumpfun", "liq": 0}, now=3001.0)   # una volta sola
out2 = D.curve_watch_step(st2, {k: pairs("pumpswap", 50000) for k in mints}, now=3500.0)
assert out2 == [], "tetto orario"
out3 = D.curve_watch_step(st2, {k: pairs("pumpswap", 50000) for k in mints}, now=3000.0 + 3601)
assert len(out3) == 3
D.curve_watch_add("Dx" + "y" * 38 + "pump", {"dex": "pumpfun", "liq": 0}, now=1000.0)
D.curve_watch_step(st2, {}, now=1000.0 + 6 * 3600 + 1)
assert not D.CURVE["d"]["watch"]

# (B) narrative_relax: solo shadow, solo con fonte attendibile
D.NARR.current = lambda st: [{"theme": "ai agents", "tickers": {"AGNT"}, "mints": set(), "keywords": ["agentic"],
                               "mint_conf": {}, "trusted_mentions": 2, "trend": "rising", "strength": 0.3, "velocity": 1},
                              {"theme": "cats", "tickers": {"MEOW"}, "mints": set(), "keywords": [], "mint_conf": {},
                               "trusted_mentions": 0, "trend": "rising", "strength": 0.3, "velocity": 1}]
D.NARR.blocklist = set()
got = []
D.spawn_shadow = lambda st, http, mint, m, keys, reason, score, flags, tag=None, signals=None: got.append((tag, keys))
assert D.narr_relax_shadow(st, None, "M", {"symbol": "AGNT", "name": "x"}, {}, "momentum")
assert got[-1] == ("narrative_relax", ["narrative_relax"])
assert not D.narr_relax_shadow(st, None, "M", {"symbol": "MEOW", "name": "x"}, {}, "momentum")   # fonte non attendibile
assert D.narr_relax_match(st, "M", {"symbol": "ZZZ", "name": "the agentic one"})[1] == "parola"
assert "narrative_relax" in D.MEASURE_ONLY_KEYS and D.learn_shadow_step(st, "narrative_relax") is None
print("test_v85d OK")
