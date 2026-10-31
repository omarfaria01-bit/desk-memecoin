"""v6: segnali soft graduati, shadow trade e Learner sugli shadow. Offline (rete simulata)."""
import asyncio, csv, json, os, time
import desk as D
D.CFG["CLUSTER_V2"] = False; D.CFG["HONEYPOT_CHECK"] = False  # v8.2: provati a parte in test_v82.py

D.SOL_USD[0], D.SOL_USD[1] = 100.0, time.time()
D.CFG["SIGNALS_ENABLED"] = False; D.CFG["CLUSTER_CHECK"] = False  # v7: test offline
SUP = 1_000_000_000 * 10 ** 6  # 1 mld di token, 6 decimali


def report(holders=None, nets=None, gid=0, lp=None, mtype="pump_fun_amm", risks=None, mint_auth=None):
    hs = holders if holders is not None else [3.0, 2.6, 2.4, 2.2, 2.0, 1.8, 1.6, 1.4, 1.2, 1.0]
    r = {"token": {"supply": SUP, "decimals": 6, "mintAuthority": mint_auth},
         "topHolders": [{"address": f"H{i}", "owner": f"O{i}", "pct": p, "uiAmount": p / 100 * 1e9, "insider": False}
                        for i, p in enumerate(hs)],
         "markets": [{"marketType": mtype, "pubkey": "POOL", "lp": {"lpLockedPct": 100 if lp is None else lp}}],
         "graphInsidersDetected": gid, "insiderNetworks": nets or [], "totalHolders": 5000, "risks": risks or []}
    return r


T40 = [6.4, 5.8, 5.2, 4.6, 4.0, 3.6, 3.2, 2.8, 2.4, 2.0]        # top 10 = 40%
T55 = [round(x * 1.375, 3) for x in T40]                        # top 10 = 55%


def check(r, mint="Xpump", dex="pumpswap", st=None):
    st = st or D.State()
    return asyncio.run(D.security_check(st, None, mint, {"dex": dex}, {mint: (time.time(), r)}))


def net(pct, size=6):
    return {"size": size, "tokenAmount": pct / 100 * SUP, "type": "transfer"}


# ---- segnali soft graduati
ok, why, cat, sec = check(report())
assert ok and sec["soft"] == [], (why, sec)
ok, why, cat, sec = check(report(nets=[net(5)], gid=6))
assert ok and sec["soft"] == ["bundle"], sec                           # bundle 5% -> soft
ok, why, cat, sec = check(report(nets=[net(30)], gid=6))
assert not ok and cat == "insider/bundle" and sec["severe"] == "bundle" and sec["shadow_keys"] == ["bundle_grave"]
ok, why, cat, sec = check(report(nets=[net(1)], gid=6))
assert ok and sec["soft"] == []                                        # rete piccola sotto il 3%: nessun segnale
ok, why, cat, sec = check(report(nets=[net(60, size=5000)], gid=5000))
assert ok and sec["soft"] == []                                        # rete enorme (exchange/airdrop) non e bundle
ok, why, cat, sec = check(report(holders=T40))
assert ok and sec["soft"] == ["top10"]                                 # 40% -> soft
ok, why, cat, sec = check(report(holders=T55))
assert not ok and cat == "top 10 troppo alti" and sec["severe"] == "top10"   # 55% -> grave
ok, why, cat, sec = check(report(holders=[3.0] + [0.2] * 5 + [0.15, 0.13]))
assert ok and sec["soft"] == ["equal"], sec                            # 5 quote identiche ma solo 1% insieme
ok, why, cat, sec = check(report(holders=[2.5] * 5 + [1.0]))
assert not ok and cat == "wallet identici" and sec["severe"] == "equal"     # 5 x 2.5% = 12.5% -> grave
ok, why, cat, sec = check(report(holders=[0.57] + [0.05] * 19))
assert ok and sec["top10"] < 2, sec                                    # fix v6: niente falso "top 10 al 99%"
ok, why, cat, sec = check(report(lp=30, mtype="raydium"), mint="XYZ", dex="raydium")
assert ok and sec["soft"] == ["lp_unlocked"], sec                      # LP 30% -> soft
ok, why, cat, sec = check(report(lp=10, mtype="raydium"), mint="XYZ", dex="raydium")
assert ok and sec["soft"] == ["lp_unlocked", "lp_unlocked_grave"], sec  # v7: LP 10% -> soft pesante
ok, why, cat, sec = check(report(lp=0, mtype="raydium"), mint="XYZ", dex="raydium")
assert not ok and sec["lp_block"] == "lp_unlocked_zero", sec            # v8.4e: LP 0% = solo shadow, mai acquisto
ok, why, cat, sec = check(report(mint_auth="AUTH", nets=[net(5)], gid=6))
assert not ok and cat == "mint non revocata" and sec is None           # HARD invariato
ok, why, cat, sec = check(report(risks=[{"name": "Single holder ownership", "level": "danger"}]))
assert not ok and cat == "rischi gravi RugCheck"                       # HARD invariato
ok, why, cat, sec = check(report(holders=T40, risks=[{"name": "Top 10 holders high ownership", "level": "danger"}]))
assert ok and sec["soft"] == ["top10"]                                 # gia graduato sopra
ok, why, cat, sec = check(report(holders=T40, nets=[net(5)], gid=6, lp=30, mtype="raydium"), mint="XYZ",
                          dex="raydium")
assert not ok and cat == "troppi segnali soft" and sec["shadow_keys"] == ["multi"]
print("segnali soft OK")

# ---- override con limiti
res = D.parse_overrides(json.dumps({"global": {"BUNDLE_HARD_PCT": 99, "SOFT_SIZE_MULT": 0.0, "SOFT_MAX_FLAGS": 3.4}}), {}, {}, set())
assert res["ok"] and res["globals"]["BUNDLE_HARD_PCT"] == 40.0 and res["globals"]["SOFT_SIZE_MULT"] == 0.2
assert res["globals"]["SOFT_MAX_FLAGS"] == 3
print("override OK")

# ---- size ridotta in Executor (broker formula)
br = D.PaperBroker()
async def go_buy(mult):
    st = D.State(); st.cash = 1000.0
    c = {"filter_ok": True, "analyst_ok": True, "score": 0.6, "thesis": "t", "features": {}, "sec": {},
         "soft": ["bundle"] if mult < 1 else [], "size_mult": mult}
    await D._execute_buy(st, br, c, "M", {"symbol": "S", "price": 1.0, "liq": 5_000_000.0, "url": ""})
    return st.positions["M"]
p1, p2 = asyncio.run(go_buy(1.0)), asyncio.run(go_buy(0.5))
assert abs(p2["cost"] - p1["cost"] * 0.5) < 1e-6 and p2["soft"] == ["bundle"], (p1["cost"], p2["cost"])
print("size soft OK", p1["cost"], p2["cost"])

# ---- shadow trade: apertura, presa di profitto, chiusura, csv
if os.path.exists(D.SHADOW_CSV):
    os.remove(D.SHADOW_CSV)
st = D.State()
m = {"symbol": "S", "price": 1.0, "liq": 500_000.0}
sh = asyncio.run(D.open_shadow(st, None, "SH1", m, ["bundle"], "punteggio basso", 0.47, ["bundle"]))
assert sh and "SH1" in st.shadow_trades and st.positions == {}
assert st.cash == D.CFG["START_BALANCE"]                                # nessun denaro
now = sh["t"]
sh["last_price"], sh["last_ok"] = 1.5, now + 60                         # +50%: TP1
assert asyncio.run(D.shadow_apply(st, None, "SH1", sh, D.shadow_decide(sh, now + 60), now + 60)) is None
assert sh["tp_stage"] == 1 and "SH1" in st.shadow_trades
sh["last_price"], sh["last_ok"] = 1.0, now + 120                        # torna a 1.0: stop a pareggio (2 controlli)
D.shadow_decide(sh, now + 120)
pnl = asyncio.run(D.shadow_apply(st, None, "SH1", sh, D.shadow_decide(sh, now + 150), now + 150))
assert pnl is not None and pnl > 0 and "SH1" not in st.shadow_trades, pnl
rows = list(csv.DictReader(open(D.SHADOW_CSV)))
assert len(rows) == 1 and rows[0]["chiavi"] == "bundle" and rows[0]["rug"] == "0" and "motivo_scarto" in rows[0]
assert st.learner_shadow["bundle"]["hist"][0][0] == round(pnl, 2)
# nessun prezzo da 15 min = rug (-100%)
sh = asyncio.run(D.open_shadow(st, None, "SH2", m, ["score"], "quasi", 0.48, []))
pnl = asyncio.run(D.shadow_apply(st, None, "SH2", sh, D.shadow_decide(sh, sh["t"] + 1000), sh["t"] + 1000))
assert pnl == -100.0 and st.learner_shadow["score"]["hist"][-1][1] == 1
# nessun percorso di vendita all'apertura = rug registrato subito
class NoSell:
    realistic = True
    async def precheck(self, st, mint, m, size):
        return {"ok": False, "why": "nessun percorso di vendita", "cat": "non rivendibile"}
D.BROKER_REF[0] = NoSell()
assert asyncio.run(D.open_shadow(st, None, "SH3", m, ["lp_unlocked_grave"], "LP", 0.6, ["lp_unlocked"])) == "rug"
assert st.learner_shadow["lp_unlocked_grave"]["hist"][-1][:2] == [-100.0, 1] and "SH3" not in st.shadow_trades
D.BROKER_REF[0] = None
# costi troppo alti (pool minuscola): nessuno shadow, come l'Executor
assert asyncio.run(D.open_shadow(st, None, "SH4", {"symbol": "S", "price": 1.0, "liq": 300.0}, ["score"], "x", 0.5, [])) is None
print("shadow trade OK")

# ---- Learner sugli shadow
st = D.State()
t0 = 1_000_000.0
for i in range(14):
    D.record_outcome(st, ["bundle"], 20.0 if i % 3 else -5.0, False, "shadow", t0 + i)
assert D.soft_pen(st, "bundle") == 0.05 and D.soft_size(st, "bundle") == 0.5   # n < 15: nessun passo
D.record_outcome(st, ["bundle"], 20.0, False, "shadow", t0 + 15)
assert abs(D.soft_pen(st, "bundle") - 0.04) < 1e-9 and abs(D.soft_size(st, "bundle") - 0.55) < 1e-9
D.record_outcome(st, ["bundle"], 20.0, False, "shadow", t0 + 16)
assert abs(D.soft_pen(st, "bundle") - 0.04) < 1e-9                    # max un passo all'ora
for k in range(1, 20):
    D.record_outcome(st, ["bundle"], 20.0, False, "shadow", t0 + 3601 * k + 20)
assert abs(D.soft_pen(st, "bundle") - 0.02) < 1e-9 and abs(D.soft_size(st, "bundle") - 0.8) < 1e-9   # limiti
# rug pesano: 2 rug su 15 bloccano l'allentamento anche con media positiva senza rug
st2 = D.State()
for i in range(15):
    D.record_outcome(st2, ["equal"], 40.0 if i >= 2 else -100.0, i < 2, "shadow", t0 + i)
s2 = D.shadow_stats(st2.learner_shadow["equal"]["hist"])
assert s2["rug"] > 0.1 and D.soft_pen(st2, "equal") == 0.05            # nessun allentamento
# stringe con molti rug
for i in range(5):
    D.record_outcome(st2, ["equal"], -100.0, True, "shadow", t0 + 3700 + i)
assert D.soft_pen(st2, "equal") > 0.05 and D.soft_size(st2, "equal") < 0.5
# soglia grave: bundle sale di 2, LP scende di 2.5 (allentare = verso il rischio, entro limiti fissi)
st3 = D.State()
for i in range(15):
    D.record_outcome(st3, ["bundle_grave", "lp_unlocked_grave"], 15.0, False, "shadow", t0 + i)
assert D.soft_hard(st3, "bundle") == 27.0 and D.soft_hard(st3, "lp_unlocked") == 22.5
for k in range(1, 30):
    D.record_outcome(st3, ["bundle_grave", "lp_unlocked_grave"], 15.0, False, "shadow", t0 + 3601 * k)
assert D.soft_hard(st3, "bundle") == 35.0 and D.soft_hard(st3, "lp_unlocked") == 15.0
# la soglia grave appresa entra davvero nel Filter
ok, why, cat, sec = check(report(nets=[net(30)], gid=6), st=st3)
assert ok and sec["soft"] == ["bundle"]
# HARD: chiavi sconosciute non cambiano nulla
before = dict(D.CFG)
for i in range(20):
    assert D.learn_shadow_step(st3, "mint non revocata", t0 + i * 4000) is None
    D.record_outcome(st3, ["multi"], 50.0, False, "shadow", t0 + i * 4000)
assert D.CFG == before
# quasi-promossi -> soglia: mai sotto THRESHOLD_MIN, -0.005 se in utile
st4 = D.State(); st4.threshold = 0.60
for i in range(15):
    D.record_outcome(st4, ["score"], 12.0, False, "shadow", t0 + i)
assert abs(st4.threshold - 0.595) < 1e-9
st5 = D.State()
for i in range(15):
    D.record_outcome(st5, ["score"], 12.0, False, "shadow", t0 + i)
assert st5.threshold == D.CFG["THRESHOLD_MIN"]
ev = [json.loads(l) for l in open(os.path.join(D.DATA_DIR, "events.jsonl")) if '"learn_adjust"' in l]
assert ev and all(k in ev[0] for k in ("n", "win", "avg", "rug", "flag"))
print("learner shadow OK", len(ev), "eventi")

# ---- persistenza
st3.shadow_trades["Q"] = {"t": 1, "last_ok": 1}
D.save_state(st3, force=True)
ld = D.load_state()
assert ld.learner_shadow["bundle_grave"]["hard_d"] == st3.learner_shadow["bundle_grave"]["hard_d"]
assert "Q" in ld.shadow_trades and ld.shadow_trades["Q"]["last_ok"] > 1
os.remove(D.STATE_PATH)
print("persistenza OK")

# ---- Analyst: penalita, size, shadow-only mai all'Executor
async def fake_candles(http, pool): return None
D.fetch_candles = fake_candles
mk = {"symbol": "S", "name": "s", "price": 1.0, "liq": 500_000.0, "chg_h1": 10.0, "chg_m5": 2.0, "buys_m5": 30,
      "sells_m5": 10, "vol_m5": 50_000.0, "vol_h1_main": 100_000.0, "buys_h1": 300, "sells_h1": 100,
      "vol_h1": 100_000.0, "twitter": 1, "telegram": 1, "website": 1, "pair": "P", "url": ""}
async def run_analyst(cands, st):
    qi, qo = asyncio.Queue(), asyncio.Queue()
    for c in cands:
        await qi.put(c)
    t = asyncio.ensure_future(D.analyst_agent(st, None, qi, qo))
    await asyncio.sleep(0.3)
    t.cancel()
    out = []
    while not qo.empty():
        out.append(qo.get_nowait())
    await asyncio.sleep(0.05)
    return out
st = D.State(); st.threshold = 0.0
sec_ok = {"top10": 20.0, "holders": 5000, "soft": ["bundle"], "creator": ""}
out = asyncio.run(run_analyst([{"mint": "A1", "m": dict(mk), "sec": sec_ok, "filter_ok": True}], st))
assert len(out) == 1 and out[0]["soft"] == ["bundle"] and out[0]["size_mult"] == 0.5
assert abs(out[0]["score_raw"] - out[0]["score"] - 0.05) < 1e-3
rej0 = st.stats["rejected"]
so = {"mint": "A2", "m": dict(mk), "sec": dict(sec_ok, severe="bundle"), "filter_ok": True,
      "shadow_only": "bundle grave", "shadow_keys": ["bundle_grave"]}
out = asyncio.run(run_analyst([so], st))
assert out == [] and st.stats["rejected"] == rej0 and "A2" in st.shadow_trades
assert st.shadow_trades["A2"]["keys"] == ["bundle_grave"]
# punteggio appena sotto la soglia -> shadow "score"; molto sotto -> nessuno shadow
st = D.State()
o = asyncio.run(run_analyst([{"mint": "B1", "m": dict(mk), "sec": {"top10": 20.0, "holders": 5000, "soft": []},
                              "filter_ok": True}], st))
sc = o[0]["score"] if o else None
st = D.State(); st.threshold = sc + 0.03
o = asyncio.run(run_analyst([{"mint": "B1", "m": dict(mk), "sec": {"top10": 20.0, "holders": 5000, "soft": []},
                              "filter_ok": True}], st))
assert o == [] and st.shadow_trades["B1"]["keys"] == ["score"]
st = D.State(); st.threshold = sc + 0.2
o = asyncio.run(run_analyst([{"mint": "B2", "m": dict(mk), "sec": {"top10": 20.0, "holders": 5000, "soft": []},
                              "filter_ok": True}], st))
assert o == [] and "B2" not in st.shadow_trades
# Executor rifiuta sempre un candidato shadow_only
async def run_exec():
    st = D.State(); q = asyncio.Queue()
    await q.put(dict(so, analyst_ok=True, score=0.9, thesis="t", features={}))
    t = asyncio.ensure_future(D.executor_agent(st, D.PaperBroker(), q))
    await asyncio.sleep(0.1); t.cancel()
    return st
assert asyncio.run(run_exec()).positions == {}
print("analyst/executor OK")
print("TUTTI I TEST v6 soft OK")
