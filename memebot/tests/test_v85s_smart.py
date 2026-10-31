"""v8.5s: lista smart money di Kiwi (SHADOW). Nessuna rete, nessun effetto su punteggio/size/acquisti.
Da lanciare SOLO su una copia in /tmp (come gli altri test)."""
import asyncio
import json
import os

import desk as D
from sources import smart_wallets as SW
from sources import cluster_v2 as CL

assert D.DATA_DIR.startswith("/tmp"), "test solo su copia in /tmp"

# --- 1) file dati vero: caricato, categorie valide, nessun campo sensibile
wl = SW.load()
assert len(wl) >= 400, len(wl)
assert {w["category"] for w in wl.values()} <= {"trader", "dev", "unknown"}
d = json.load(open(SW.DATA))
for w in d["wallets"]:   # solo indirizzo pubblico + nome + classificazione
    assert set(w) <= {"address", "name", "category", "subtype", "group", "status", "hist", "edge"}, set(w)
    assert 32 <= len(w["address"]) <= 44, w["address"]
raw = open(SW.DATA).read().lower()
for bad in ("private_key", "secret_key", "seed phrase", "api_key", "api-key", "bot_token"):
    assert bad not in raw, bad

# --- 2) match su una lista finta
fake = {"T1": {"name": "cupsey", "category": "trader", "group": "cupsey", "edge": True},
        "T2": {"name": "cupsey multi", "category": "trader", "group": "cupsey"},
        "T3": {"name": "orangie", "category": "trader", "group": "orangie"},
        "DV": {"name": "useless dev", "category": "dev", "group": "useless"},
        "IN": {"name": "binance", "category": "unknown", "group": "binance"}}
r = SW.match({"T1": 1.0, "T2": 0.5, "T3": 0.25, "DV": 2.0, "IN": 3.0, "XX": 50.0}, fake)
assert r["n"] == 5 and r["trader"] == 3 and r["dev"] == 1 and r["unknown"] == 1, r
assert r["trader_groups"] == 2, r            # alt dello stesso trader contati una volta
assert abs(r["trader_pct"] - 1.75) < 1e-9, r  # dev/exchange NON contano nella quota smart
assert r["names"][0] == "cupsey", r
assert SW.match({}, fake)["n"] == 0 and SW.match(["T3"], fake)["trader"] == 1
assert SW.creator_info("DV", fake) == {"name": "useless dev", "category": "dev"}
assert SW.creator_info(None, fake) is None and SW.creator_info("XX", fake) is None
assert r["edge"] == 1, r
assert sum(1 for w in wl.values() if w["edge"]) >= 1
assert SW.signal(r) == "smart:2:edge1:dev" and SW.signal(SW.match({}, fake)) is None
r2 = dict(SW.match({}, fake), creator_dev={"name": "useless dev", "category": "dev"})
assert SW.signal(r2) == "smart:0:creator_dev"

# --- 3) file mancante o rotto: nessuna eccezione, risultato vuoto
assert SW.load("/tmp/_non_esiste_smart.json") == {}
open("/tmp/_smart_rotto.json", "w").write("{rotto")
assert SW.load("/tmp/_smart_rotto.json") == {}

# --- 4) i tag 'smart:' NON sono bonus appresi dal Learner (nessun effetto su punteggio)
for k, (pref, _) in D.BONUS_KEYS.items():
    assert not "smart:3".startswith(pref) and not "smart:0:creator_dev".startswith(pref), k
for k, (pref, _) in D.PENALTY_KEYS.items():
    assert not "smart:3".startswith(pref), k
assert D.CFG["SMART_WALLETS_MODE"] == "shadow"
assert "smart" in D.SRC

# --- 5) cluster_v2.scan: 'smart' calcolato sugli holder gia scaricati, ZERO chiamate in piu
a = next(iter(wl))
calls = []


async def fake_post(url, body):
    calls.append(body.get("method"))
    return None   # tutto il resto fallisce -> verdetto 'unknown', ma lo smart resta


async def fake_top(rpc, mint, exclude=(), token_program=None, top_n=150, min_pct=0.0, das_pages=10, full=None):
    full.update({a: 1.5, "NonInLista111": 10.0})
    return [(a, 1.5, "acc1"), ("NonInLista111", 10.0, "acc2")], 2, {"owners": set()}

orig = CL.top_holders
CL.top_holders = fake_top
try:
    res = asyncio.run(CL.scan(fake_post, "MintFinto", cfg={"BUDGET_SEC": 1}))
finally:
    CL.top_holders = orig
assert res["level"] == "unknown" and res["smart"]["n"] == 1, res
assert res["smart"][wl[a]["category"]] == 1

# --- 6) desk: annotazione del creatore, brief e segnale; 'off' spegne tutto
res = {"level": "ok", "smart": SW.match({a: 1.0})}
D.smart_annotate(res, {"creator": a})
assert res["smart"]["creator_dev"]["name"] == wl[a]["name"]
assert "smart" in D.cluster2_brief(res)
assert D.smart_signal(res).startswith("smart:")
D.CFG["SMART_WALLETS_MODE"] = "off"
res2 = {"level": "ok", "smart": SW.match({a: 1.0})}
D.smart_annotate(res2, {"creator": a})
assert "smart" not in res2
D.CFG["SMART_WALLETS_MODE"] = "shadow"

# --- 7) soglie/punteggio invariati: nessun nuovo CFG oltre SMART_WALLETS_MODE
assert not any(k.startswith("SMART_") and k != "SMART_WALLETS_MODE" for k in D.CFG)
print("ok v8.5s smart wallets (shadow)")
