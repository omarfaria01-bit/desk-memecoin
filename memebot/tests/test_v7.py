"""v7: cluster graduato, LP sbloccata soft, shadow analyst_momentum, bonus dei segnali, casi etichettati. Offline."""
import asyncio, csv, json, os, time, types
import desk as D
D.CFG["CLUSTER_V2"] = False; D.CFG["HONEYPOT_CHECK"] = False  # v8.2: provati a parte in test_v82.py

D.SOL_USD[0], D.SOL_USD[1] = 100.0, time.time()
D.CFG["SIGNALS_ENABLED"] = False
D.CFG["CLUSTER_CHECK"] = False
SUP = 1_000_000_000 * 10 ** 6
before = {k: D.CFG[k] for k in ("BUNDLE_MAX_NETWORKS", "BUNDLE_TOTAL_HARD_PCT", "BUNDLE_MAX_INSIDER_WALLETS",
                                "CLUSTER_FRESH_HARD", "LP_EXTREME_LOCKED_PCT")}


def rows(n, fresh, groups=(), pct=0.5):
    out, i = [], 0
    for g, size in enumerate(groups):
        for _ in range(size):
            out.append({"o": f"W{i}", "pct": pct, "ntx": 5, "src": f"F{g}"}); i += 1
    while len(out) < n:
        out.append({"o": f"W{i}", "pct": pct, "ntx": 5 if sum(r["ntx"] < 100 for r in out) < fresh else 500,
                    "src": None}); i += 1
    return out


# ---- cluster: graduato
m = D.cluster_metrics(rows(30, 2))
assert m["level"] == "none", m
m = D.cluster_metrics(rows(30, 6, groups=(3, 4)))                 # 2 reti piccole -> soft
assert m["level"] == "soft" and m["networks"] == 2, m
m = D.cluster_metrics(rows(30, 16))                                # 53% wallet nuovi -> soft
assert m["level"] == "soft", m
m = D.cluster_metrics(rows(30, 24, groups=(4, 3)))                 # tipo DESSA: 80% nuovi + 2 reti -> hard
assert m["level"] == "hard" and m["fresh_share"] >= 0.8, m
m = D.cluster_metrics(rows(30, 22, groups=(4, 3)))                 # 73% nuovi -> solo soft
assert m["level"] == "soft", m
m = D.cluster_metrics(rows(30, 8, groups=(4, 4, 4, 4), pct=1.5))   # 4 reti con 24% -> hard
assert m["level"] == "hard" and m["networks"] == 4 and m["net_pct"] >= 20, m
m = D.cluster_metrics(rows(30, 8, groups=(4, 4, 4, 4), pct=0.5))   # 4 reti ma solo 8% -> soft
assert m["level"] == "soft", m
m = D.cluster_metrics(rows(30, 2), rc_nets=[(40, 6.0)])            # 40 wallet collegati -> hard
assert m["level"] == "hard", m
assert D.rc_networks({"token": {"supply": SUP}, "insiderNetworks": [
    {"size": 5000, "tokenAmount": 0.01 * SUP}, {"size": 6, "tokenAmount": 0.05 * SUP}]}) == [(6, 5.0)]
m = D.cluster_metrics(rows(30, 29, groups=(28,), pct=0.1))         # 1 finanziatore per 28/30, 2.8%: servizio -> soft
assert m["level"] == "soft" and m["service"] and m["networks"] == 0, m
m = D.cluster_metrics(rows(35, 34, groups=(32,), pct=0.5))         # 32 wallet con il 16%: non servizio -> hard
assert m["level"] == "hard", m
print("cluster OK")


# ---- LP sbloccata soft, estremi hard
def rep(lp, holders=None, creator="CR"):
    return {"token": {"supply": SUP, "decimals": 6}, "creator": creator,
            "topHolders": [{"address": f"H{i}", "owner": f"O{i}", "pct": p, "uiAmount": p / 100 * 1e9, "insider": False}
                           for i, p in enumerate([3.0, 2.6, 2.4, 2.2, 2.0, 1.8, 1.6, 1.4, 1.2, 1.0])],
            "markets": [{"marketType": "raydium", "pubkey": "POOL", "lp": {"lpLockedPct": lp, "holders": holders}}],
            "totalHolders": 5000, "risks": [{"name": "LP Unlocked", "level": "danger"}]}


def check(r, m=None, st=None):
    st = st or D.State()
    m = m or {"dex": "raydium", "liq": 50_000.0}
    return asyncio.run(D.security_check(st, None, "XYZ", m, {"XYZ": (time.time(), r)}))


ok, why, cat, sec = check(rep(30))
assert ok and sec["soft"] == ["lp_unlocked"], (why, sec)
ok, why, cat, sec = check(rep(0, holders=[{"owner": "OTHER", "pct": 99.0}]))
# v8.4e (Kiwi 07:07): LP 0% = MAI acquisto, solo shadow lp_unlocked_zero (prima: soft lp_unlocked + grave)
assert not ok and sec["lp_block"] == "lp_unlocked_zero" and sec["shadow_keys"] == ["lp_unlocked_zero"], why
ok, why, cat, sec = check(rep(10, holders=[{"owner": "OTHER", "pct": 99.0}]))
assert ok and sec["soft"] == ["lp_unlocked", "lp_unlocked_grave"], why   # tra 0% e 50%: invariato
ok, why, cat, sec = check(rep(2, holders=[{"owner": "CR", "address": "A", "pct": 95.0}]))
assert not ok and cat == "LP in mano al creatore" and sec is None, why
ok, why, cat, sec = check(rep(30), m={"dex": "raydium", "liq": 50_000.0, "liq_max": 100_000.0})
assert not ok and cat == "rimozione LP", why
ok, why, cat, sec = check(dict(rep(100), markets=[{"marketType": "pump_fun_amm", "pubkey": "P",
                                                   "lp": {"lpLockedPct": 100}}]), m={"dex": "pumpswap", "liq": 5e4})
assert ok and sec["soft"] == []                                    # pump.fun migrato (LP bruciata): invariato
assert len(D.base_flags(["lp_unlocked", "lp_unlocked_grave", "top10"])) == 2
assert D.is_rug_event("liquidita crollata del 55%", 20) and not D.is_rug_event("liquidita crollata del 55%", 200)
assert D.is_rug_event("liquidita crollata del 85%", 500) and not D.is_rug_event("stop loss a -10%", 5)
assert D.pos_max_hold({"max_hold_min": 60}) == 60 and D.pos_max_hold({}) == D.CFG["MAX_HOLD_MIN"]
sh = {"t": time.time() - 61 * 60, "last_ok": time.time(), "liq": 1e5, "liq_entry": 1e5, "cost": 30, "tokens": 30,
      "tokens_entry": 30, "entry_mark": 1.0, "realized": 0.0, "tp_stage": 0, "peak_pct": 0.0, "stop_hits": 0,
      "last_price": 1.02, "max_hold": 60}
D.CFG["TIME_STOP_MIN"] = 999
d = D.shadow_decide(sh)
assert d and d[0].startswith("tempo massimo 60"), d
sh2 = dict(sh, t=time.time() - 10 * 60, liq=4e4)
d = D.shadow_decide(sh2)
assert d and d[0].startswith("liquidita crollata") and d[3] is True, d   # rug: -60% in 10 min
print("LP OK")

# ---- shadow_trades.csv con intestazione all'avvio
if os.path.exists(D.SHADOW_CSV):
    os.remove(D.SHADOW_CSV)
D.ensure_shadow_csv()
assert next(csv.reader(open(D.SHADOW_CSV))) == D.SHADOW_FIELDS
st = D.State()
shr = dict(sh, sym="S", reason="r", keys=["analyst_momentum"], flags=[], score=0.0, thr=0.6, price=1.0, rt=1.0,
           tag="analyst_momentum", signals=["src:x", "reason:analyst_momentum"])
D._close_shadow_row(st, "M1", shr, 5.0, False, "test")
rows_ = list(csv.DictReader(open(D.SHADOW_CSV)))
assert rows_[-1]["tag"] == "analyst_momentum" and "reason:analyst_momentum" in rows_[-1]["signals"]
D.ensure_shadow_csv()
assert len(list(csv.DictReader(open(D.SHADOW_CSV)))) == 1          # header gia giusto: file intatto
print("shadow csv OK")


# ---- Analyst: momentum non confermato -> shadow analyst_momentum; cluster hard -> scarto 'cluster' + shadow
async def fake_candles(http, pool): return None
D.fetch_candles = fake_candles
opened = []
async def fake_open(st, http, mint, m, keys, reason, score, flags, now=None, tag=None, signals=None):
    opened.append((mint, keys, tag, signals)); st.shadow_trades[mint] = {"tag": tag}
D.open_shadow = fake_open
mk = {"symbol": "S", "name": "s", "price": 1.0, "liq": 500_000.0, "chg_h1": 10.0, "chg_m5": 2.0, "buys_m5": 30,
      "sells_m5": 10, "vol_m5": 50_000.0, "vol_h1_main": 100_000.0, "buys_h1": 300, "sells_h1": 100,
      "vol_h1": 100_000.0, "twitter": 1, "telegram": 1, "website": 1, "pair": "P", "url": "", "src": "pump.fun"}


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
weak = dict(mk, buys_m5=5, sells_m5=10)
out = asyncio.run(run_analyst([{"mint": "MO1", "m": weak, "sec": {"soft": []}, "filter_ok": True}], st))
assert out == [] and opened[-1][1] == ["analyst_momentum"] and opened[-1][2] == "analyst_momentum", opened
D.CFG["CLUSTER_CHECK"] = True
async def fake_scan(st, http, mint, sec):
    return D.cluster_metrics(rows(30, 24, groups=(4, 3))) if mint == "CL1" else D.cluster_metrics(rows(30, 16))
D.cluster_scan = fake_scan
st = D.State(); st.threshold = 0.0
out = asyncio.run(run_analyst([{"mint": "CL1", "m": dict(mk), "sec": {"soft": []}, "filter_ok": True},
                               {"mint": "CL2", "m": dict(mk), "sec": {"soft": []}, "filter_ok": True}], st))
assert [o["mint"] for o in out] == ["CL2"] and out[0]["soft"] == ["cluster"], out
assert abs(out[0]["size_mult"] - D.CFG["CLUSTER_SIZE_MULT"]) < 1e-9
assert abs(out[0]["score_raw"] - out[0]["score"] - D.CFG["CLUSTER_PENALTY"]) < 1e-3
assert "cluster:soft" in out[0]["signals"]
assert opened[-1][0] == "CL1" and opened[-1][1] == ["cluster_hard"] and opened[-1][2] == "cluster"
assert st.reject_reasons.get("cluster") == 1, st.reject_reasons
D.CFG["CLUSTER_CHECK"] = False
print("analyst OK")

# ---- Learner: cluster soft nei due versi entro limiti; soglia estrema mai allentata
st = D.State(); t0 = time.time()
for i in range(15):
    D.record_outcome(st, ["cluster"], 20.0, False, "shadow", t0 + i)
assert D.soft_pen(st, "cluster") < D.CFG["CLUSTER_PENALTY"] and D.soft_size(st, "cluster") > D.CFG["CLUSTER_SIZE_MULT"]
for k in range(30):
    D.record_outcome(st, ["cluster"], 80.0, False, "shadow", t0 + 3601 * (k + 1))
assert D.soft_pen(st, "cluster") >= 0.04 - 1e-9 and D.soft_size(st, "cluster") <= 0.7 + 1e-9
st2 = D.State()
for k in range(60):
    D.record_outcome(st2, ["cluster"], -60.0, True, "shadow", t0 + 3601 * k)
assert abs(D.soft_pen(st2, "cluster") - 0.15) < 1e-9 and abs(D.soft_size(st2, "cluster") - 0.2) < 1e-9
for k in range(60):
    D.record_outcome(st2, ["cluster_hard"], 90.0, False, "shadow", t0 + 3601 * k)
assert {k: D.CFG[k] for k in before} == before and st2.learner_shadow["cluster_hard"]["adj"] == 0
print("learner cluster OK")

# ---- bonus: tetto, sostituto senza bonus, penalita KOL venduto, Learner con/senza
D.CFG["SIGNALS_ENABLED"] = True
fake_kol = types.SimpleNamespace(
    akol_check=None, tier_of=lambda h: "A", KolLedger=None,
    kol_bonus=lambda r, cfg: (r["b"], r["t"]))
async def akc(mint): return {"b": 0.08 if mint == "K1" else -0.02, "t": ["kol:unipcs:A"] if mint == "K1" else ["kol_sold:x"]}
fake_kol.akol_check = akc
recorded = []
class Led:
    def record(self, h, pnl, mint, shadow): recorded.append((h, pnl, mint, shadow))
fake_kol.KolLedger = Led
async def amom(mint): return {"young": True, "score": 0.9, "n_components": 3}
fake_mom = types.SimpleNamespace(amomentum=amom, momentum_bonus=lambda m, cfg: (round(cfg["MOMENTUM_YOUNG_BONUS"] * 0.9, 4),
                                                                              ["momentum_young:0.90"]))
fake_th = types.SimpleNamespace(thesis_bonus=lambda mint, tier, cfg, w=None: (cfg["KOL_THESIS_BONUS"], ["kol_thesis:theunipcs"]))
D.SRC.clear(); D.SRC.update({"kol": fake_kol, "mom": fake_mom, "theses": fake_th})
st = D.State()
st.fomo_rank["K1"] = {"rank": 2, "source": "fomo_trending", "t": time.time()}
b, tags = asyncio.run(D.compute_bonus(st, "K1", {}))
assert b == 0.10 and "src:fomo_trending" in tags and "kol:unipcs:A" in tags, (b, tags)   # tetto 0.10
st.fomo_rank["K2"] = {"rank": 5, "source": "dex_boosts_substitute", "t": time.time()}
fake_th.thesis_bonus = lambda mint, tier, cfg, w=None: (0.0, [])
fake_mom.momentum_bonus = lambda m, cfg: (0.0, [])
b, tags = asyncio.run(D.compute_bonus(st, "K2", {}))
assert b == -0.02 and "src:dex_boosts_substitute" in tags and "kol_sold:x" in tags, (b, tags)
# Learner dei bonus
st = D.State()
for i in range(15):
    D.record_signals(st, f"W{i}", "src:pump|kol:unipcs:A|momentum_young:0.8", 25.0, False, shadow=True)
    D.record_signals(st, f"N{i}", "src:pump", -5.0, False, shadow=False)
assert D.bonus_eff(st, "KOL_BONUS") == round(D.CFG["KOL_BONUS"] + 0.01, 4)
assert D.bonus_eff(st, "MOMENTUM_YOUNG_BONUS") == round(D.CFG["MOMENTUM_YOUNG_BONUS"] + 0.01, 4)
assert recorded and recorded[0][0] == "unipcs" and recorded[0][3] is True
st.learner_bonus["d"]["KOL_BONUS"] = -0.025                      # 0.005: un passo in giu -> 0, mai negativo
st.learner_bonus["last"] = {k: time.time() for k in D.BONUS_KEYS}   # bloccato: un passo all'ora
for i in range(40):
    D.record_signals(st, f"L{i}", "src:pump|kol:unipcs:A", -30.0, False, shadow=True)
assert D.bonus_eff(st, "KOL_BONUS") == round(D.CFG["KOL_BONUS"] - 0.025, 4)
assert "KOL_BONUS" in D.learn_bonus_step(st, time.time() + 3700)
assert D.bonus_eff(st, "KOL_BONUS") == 0.0
assert D.bonus_eff(st, "FOMO_SUB_BONUS") == 0.0
st = D.State()
st.fomo_rank["G1"] = {"rank": 3, "source": "gmgn_trending", "t": time.time()}
b, tags = asyncio.run(D.compute_bonus(st, "G1", {}))
assert "src:gmgn_trending" in tags and "gmgn_rank:3" in tags and b == round(D.CFG["GMGN_TREND_BONUS"] - 0.02, 4), (b, tags)
# chiave GMGN: solo personale, mai la chiave pubblica demo
os.environ.pop("GMGN_API_KEY", None)
os.environ["HOME"] = os.path.abspath("fakehome")
assert D.gmgn_personal_key() is None
os.environ["GMGN_API_KEY"] = "gmgn_solbscbaseethmonadtron"
assert D.gmgn_personal_key() is None
os.environ["GMGN_API_KEY"] = "personale123"
assert D.gmgn_personal_key() == "personale123"
os.environ.pop("GMGN_API_KEY")
os.makedirs("fakehome/.config/desk", exist_ok=True)
open("fakehome/.config/desk/gmgn_key", "w").write("filekey\n")
assert D.gmgn_personal_key() == "filekey"
print("bonus OK")

# ---- caso etichettato: alla chiusura diventa un esempio 'cluster' + lezione
D.CFG["SIGNALS_ENABLED"] = False
st = D.State()
st.labels = {"LAB1": "cluster"}
st.positions["LAB1"] = {"symbol": "L", "cost": 30.0, "tokens": 30.0, "realized": 0.0, "opened": time.time() - 600,
                        "peak_pct": 0.0, "soft": [], "signals": "src:pump", "features": {}, "last_price": 0.9,
                        "liq": 1e5, "entry_mark": 1.0}
asyncio.run(D._sell_position(st, D.PaperBroker(), "LAB1", "stop loss a -10%", zero=True))
assert "LAB1" not in st.positions and "LAB1" not in st.labels
assert st.learner_shadow["cluster"]["hist"][-1][2] == "real-label"
assert any("caso etichettato 'cluster'" in x for x in st.lessons)
D.save_state(st, force=True)
assert D.load_state().labels.get("LAB1") is None
os.remove(D.STATE_PATH)
print("caso etichettato OK")

# ---- override con limiti
res = D.parse_overrides(json.dumps({"global": {"TOTAL_BONUS_CAP": 0.5, "BUNDLE_MAX_NETWORKS": 2,
                                               "CLUSTER_PENALTY": 0.1}}), {}, {}, set())
g = res["globals"]
assert g["TOTAL_BONUS_CAP"] == 0.10 and g["BUNDLE_MAX_NETWORKS"] == 3 and g["CLUSTER_PENALTY"] == 0.1, res
assert D.CFG["DESK_PAPER_ONLY"] if "DESK_PAPER_ONLY" in D.CFG else True
print("TUTTI I TEST v7 OK")
