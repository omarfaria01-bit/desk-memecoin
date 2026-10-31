"""v8.4e: regola LP 0%/sconosciuta (mai acquisto), un solo finanziatore, storico creatore, seed HYPERCHAN. Offline."""
import asyncio, time
import desk as D
from sources import cluster_v2 as C
from sources import creator_history as CH

D.CFG["CLUSTER_V2"] = False; D.CFG["HONEYPOT_CHECK"] = False; D.CFG["SIGNALS_ENABLED"] = False
D.CFG["BIRDEYE_XCHECK_MODE"] = "off"; D.CFG["DYN_STOP_MODE"] = "off"; D.CFG["PRICE_PATHS"] = False
D.SOL_USD[0], D.SOL_USD[1] = 100.0, time.time()
SUP = 1_000_000_000 * 10 ** 6


def rep(lp, mk="raydium"):
    lpd = {} if lp == "none" else {"lpLockedPct": lp, "holders": [{"owner": "OTHER", "pct": 99.0}]}
    return {"token": {"supply": SUP, "decimals": 6}, "creator": "CR",
            "topHolders": [{"address": f"H{i}", "owner": f"O{i}", "pct": p, "uiAmount": p / 100 * 1e9, "insider": False}
                           for i, p in enumerate([3.0, 2.6, 2.4, 2.2, 2.0, 1.8, 1.6, 1.4, 1.2, 1.0])],
            "markets": ([] if lp == "nomarket" else [{"marketType": mk, "pubkey": "POOL", "lp": lpd}]),
            "totalHolders": 5000, "risks": [{"name": "LP Unlocked", "level": "danger"}]}


def check(r, m=None):
    m = m or {"dex": "raydium", "liq": 50_000.0}
    return asyncio.run(D.security_check(D.State(), None, "XYZ", m, {"XYZ": (time.time(), r)}))


# ---- 1. security_check: 0% e sconosciuta -> shadow-only con tag dedicato; 0-50% invariato
for lp in (0, 0.0, 0.3):
    ok, why, cat, sec = check(rep(lp))
    assert not ok and sec and sec["lp_block"] == "lp_unlocked_zero" and sec["lp_status"] == "zero", (lp, why, sec)
    assert sec["shadow_keys"] == ["lp_unlocked_zero"] and "lp_unlocked" not in sec["soft"], sec
for bad in ("none", "nomarket"):
    ok, why, cat, sec = check(rep(bad))
    assert not ok and sec["lp_block"] == "lp_unknown" and sec["lp_status"] == "unknown", (bad, why, sec)
ok, why, cat, sec = check(rep(10))
assert ok and sec["soft"] == ["lp_unlocked", "lp_unlocked_grave"] and sec["lp_status"] == "partial", why
ok, why, cat, sec = check(rep(30))
assert ok and sec["soft"] == ["lp_unlocked"] and sec["lp_status"] == "partial", why
ok, why, cat, sec = check(rep(80))
assert ok and sec["lp_status"] == "ok" and sec["soft"] == [], why
ok, why, cat, sec = asyncio.run(D.security_check(D.State(), None, "XYZpump", {"dex": "pumpswap", "liq": 5e4},
                                                 {"XYZpump": (time.time(), rep(0, "pump_fun_amm"))}))
assert ok and sec["lp_status"] == "protocol", (why, sec)              # pump.fun migrato: LP del protocollo
assert D.shadow_tag(["lp_unlocked_zero"], ["lp_unlocked"]) == "lp_unlocked_zero"
assert D.shadow_tag(["lp_unknown"], []) == "lp_unknown"
assert D.shadow_tag(["lp_unlocked_grave"], ["lp_unlocked"]) == "lp_unlocked"
print("LP 0%/sconosciuta nel Filter OK")

# ---- 2. barriera finale: nessun percorso (score alto, narrativa/KOL, size, deep review, override) la aggira
for stt in ("zero", "unknown"):
    c = {"sec": {"lp_status": stt}, "score": 0.99, "size_mult": 1.0, "narr_bonus": 0.08, "soft": [],
         "signals": "kol:abc|narr:x", "filter_ok": True, "analyst_ok": True}
    assert D.lp_gate(c) == ("lp_unlocked_zero" if stt == "zero" else "lp_unknown")
assert D.lp_gate({"sec": {"lp_status": "partial", "lp_locked_pct": 0.0}}) == "lp_unlocked_zero"
assert D.lp_gate({"sec": {"lp_block": "lp_unknown"}}) == "lp_unknown"
assert D.lp_gate({}) == "lp_unknown"
for stt in ("ok", "partial", "curve", "protocol"):
    assert D.lp_gate({"sec": {"lp_status": stt, "lp_locked_pct": 30.0}}) is None, stt


class Broker:
    realistic, http = False, None
    def __init__(self): self.bought = []
    async def cash_usd(self, st): return 1000.0
    async def buy(self, st, mint, m, size, pre=None):
        self.bought.append(mint); return {"ok": False, "error": "test"}


_sp = []
_osp = D.spawn_shadow
D.spawn_shadow = lambda st, http, mint, m, keys, reason, score, flags, tag=None, signals=None: _sp.append((mint, keys, tag))
m0 = {"symbol": "ZERO", "liq": 80_000.0, "price": 1.0, "url": "", "src": "test"}
for stt, tag in (("zero", "lp_unlocked_zero"), ("unknown", "lp_unknown")):
    st, br = D.State(), Broker()
    D.CFG["LP_EXTREME_LOCKED_PCT"] = 10.0   # massimo consentito da override: non cambia la barriera
    c = {"sec": {"lp_status": stt, "creator": ""}, "score": 0.99, "size_mult": 1.0, "soft": [], "narr_bonus": 0.08,
         "signals": "kol:abc", "thesis": "", "features": {}}
    asyncio.run(D._execute_buy(st, br, c, "M" + stt, m0))
    assert br.bought == [] and _sp[-1] == ("M" + stt, [tag], tag), (br.bought, _sp)
st, br = D.State(), Broker()
asyncio.run(D._execute_buy(st, br, {"sec": {"lp_status": "partial", "lp_locked_pct": 30.0, "creator": ""}, "score": 0.9,
                                    "size_mult": 0.5, "soft": ["lp_unlocked"], "thesis": "", "features": {}}, "MP", m0))
assert br.bought == ["MP"], br.bought                       # 0-50%: percorso soft invariato (arriva all'ordine)
D.spawn_shadow = _osp
# Learner bloccato: esiti pessimi o ottimi su lp_unlocked_zero / lp_unknown non cambiano nulla
st = D.State()
base_lh = D.soft_hard(st, "lp_unlocked")
for k in ("lp_unlocked_zero", "lp_unknown"):
    for i in range(40):
        D.record_outcome(st, [k], 50.0, False, "shadow", now=time.time() + i * 7200)
    assert D.learn_shadow_step(st, k, now=time.time() + 10 ** 6) is None
assert D.soft_hard(st, "lp_unlocked") == base_lh and "lp_unlocked" not in st.learner_shadow
assert "lp_unlocked_zero" not in D.SOFT_FLAGS and "lp_unknown" not in D.SOFT_FLAGS
assert "LP_ZERO_MAX_PCT" not in D.CFG and all("LP_ZERO" not in v for v in D.OVR_GLOBAL_MAP.values())
print("barriera LP nell'Executor + Learner bloccato OK")

# ---- 3. cluster v2: un solo finanziatore per >= 50% dei holder controllati (>= 10 wallet)
def rows(n_checked, n_src, src="F1", n_tot=150):
    out = []
    for i in range(n_tot):
        r = {"o": f"W{i}", "pct": 2.0 / (i + 1) ** 0.6, "ntx": (5 if i < n_src else 300) if i < n_checked else None}
        if i < n_src:
            r.update(src=src, ts=time.time() - 86400 + i * 4000)
        out.append(r)
    return out
r = C.analyze(rows(60, 59, "BXTDKdxx"))                  # tipo HYPERCHAN
assert r["level"] == "hard" and "un solo finanziatore" in r["why"] and r["single_funder"]["share"] > 0.98, r["why"]
r = C.analyze(rows(60, 30, "HubY"))                      # 50% esatto -> hard
assert "un solo finanziatore" in r["why"] and r["level"] == "hard"
r = C.analyze(rows(60, 29, "HubY"))                      # 48% -> la regola non scatta
assert "un solo finanziatore" not in r["why"]
r = C.analyze(rows(12, 9, "HubY", n_tot=40))             # 9 wallet (< 10) -> la regola non scatta
assert "un solo finanziatore" not in r["why"]
binance = "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhLEfqhAF"
assert C.single_funder(rows(60, 40, binance))[1] == 0      # exchange noto: ritiri di persone diverse, escluso
assert C.single_funder([{"o": f"b{i}", "pct": 0.5, "ntx": 300, "src": (f"S{i}" if i < 5 else None)}
                        for i in range(60)])[1] == 1       # tipo Bonnie: finanziatori tutti diversi (max 1)
dd = [{"o": f"d{i}", "pct": 0.3 / (i + 1) ** 0.5, "ntx": 100} for i in range(150)]
assert C.analyze(dd)["level"] == "none"                    # il 'pulito' sintetico resta pulito
print("un solo finanziatore OK")

# ---- 4. storico del creatore
NOW = time.time()
def coin(i, ath, mc, age_h=48, banned=False, complete=False):
    return {"mint": f"C{i}", "created_timestamp": (NOW - age_h * 3600) * 1000, "ath_market_cap": ath,
            "usd_market_cap": mc, "is_banned": banned, "complete": complete}
dead6 = [coin(i, 80_000, 3_000) for i in range(5)] + [coin(9, 60_000, 50_000)]
r = CH.evaluate(dead6, "NEW", NOW * 1000, NOW)
assert r["level"] == "hard" and r["prev"] == 6 and r["dead"] == 5, r
r = CH.evaluate(dead6[:4], "NEW", NOW * 1000, NOW)        # 4 precedenti (< 5) -> mai hard
assert r["level"] == "soft" and 0 < r["pen"] <= CH.CFG["SOFT_PEN_MAX"], r
bonnie = [coin(i, 5_000, 4_000) for i in range(46)] + [coin(50, 90_000, 3_000), coin(51, 40_000, 2_000),
                                                       coin(52, 500_000, 400_000, complete=True)]
r = CH.evaluate(bonnie, "NEW", NOW * 1000, NOW)            # tipo Bonnie: 2/3 morti, 46 lanci mai partiti
assert r["level"] == "soft" and r["attempts"] == 46 and abs(r["pen"] - 0.02) < 0.005, r
r = CH.evaluate([coin(i, 5_000, 4_000) for i in range(50)], "NEW", NOW * 1000, NOW)
assert r["level"] == "none" and r["prev"] == 0             # solo lanci mai partiti: nessun effetto
r = CH.evaluate([coin(i, 80_000, 1_000, age_h=2) for i in range(8)], "NEW", NOW * 1000, NOW)
assert r["level"] == "none"                                # token troppo recenti (< 24 h): non contano
r = CH.evaluate([coin(i, 80_000, 70_000, banned=True) for i in range(6)], "NEW", NOW * 1000, NOW)
assert r["level"] == "hard"                                # bannati = rug
calls = []
async def get_none(url):
    calls.append(url); return None
CH.CACHE_PATH = "/tmp/_crh_test_cache.json"
CH._MEM.update(loaded=True, data={"mint": {}, "creator": {}})
r = asyncio.run(CH.lookup(get_none, "AAApump"))
assert r["level"] == "unknown" and r["pen"] == 0.0 and len(calls) == 1
async def get_ok(url):
    calls.append(url)
    if "user-created-coins" in url:
        return {"count": 6, "coins": dead6}
    return {"creator": "CRX", "created_timestamp": NOW * 1000}
calls.clear()
r1 = asyncio.run(CH.lookup(get_ok, "BBBpump"))
r2 = asyncio.run(CH.lookup(get_ok, "BBBpump"))
assert r1["level"] == r2["level"] == "hard" and len(calls) == 2, calls     # cache: 2 chiamate, non 4
async def get_err(url):
    raise RuntimeError("x")
assert asyncio.run(CH.lookup(get_err, "CCCpump"))["level"] == "unknown"
print("storico creatore OK")

# ---- 5. seed HYPERCHAN (6a lezione negativa)
ids = {x["id"]: x for x in D.LABEL_SEEDS}
h = ids["hyperchan-2026-10-31-rug"]
assert h["label"] == "rug" and h["pnl_pct"] is None and "BXTDKd" in h["note"] and "MAI controllo pulito" in h["note"]
assert sum(1 for x in D.LABEL_SEEDS if x["label"] in ("rug", "cluster")) >= 6
print("TUTTI I TEST v8.4e OK")
