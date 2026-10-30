"""v8.3: aggregatore trending, priorita watchlist, prefiltro DexScreener, bonus multi-fonte, override PF_*. Offline."""
import asyncio, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import desk as D
from sources import trending_agg as T, dex_prefilter as P

assert "tagg" in D.SRC and "pf" in D.SRC
assert D.HOST_GAPS["api.geckoterminal.com"] >= 6.0
T.STATE_FILE = T.Path("/tmp/_test_trending_agg_state.json")
P.LOG_PATH = "/tmp/_test_prefilter_log.jsonl"
# ---- aggregatore con risposte finte (nessuna rete)
M1, M2, M3 = "A" * 43 + "1", "B" * 43 + "2", "C" * 43 + "3"
def pair(m, vol, tx):
    return {"baseToken": {"address": m, "symbol": m[:3]}, "liquidity": {"usd": 50000}, "volume": {"m5": vol / 6, "h1": vol},
            "txns": {"m5": {"buys": tx // 6, "sells": tx // 12}, "h1": {"buys": tx, "sells": tx // 2}},
            "priceChange": {"m5": 3, "h1": 20}, "marketCap": 300000, "pairCreatedAt": (time.time() - 7200) * 1000,
            "pairAddress": "P" + m[:5], "priceUsd": "0.001", "info": {"socials": [{"type": "twitter"}]}}
calls = []
async def get(url, headers=None):
    calls.append(url)
    if "token-boosts/top" in url:
        return [{"chainId": "solana", "tokenAddress": M1, "totalAmount": 500}, {"chainId": "ethereum", "tokenAddress": M3}]
    if "token-profiles" in url:
        return [{"chainId": "solana", "tokenAddress": M2, "links": [1]}]
    if "trending_pools" in url:
        return {"data": [{"relationships": {"base_token": {"data": {"id": "solana_" + M1}}}, "attributes": {"name": "X / SOL"}}]}
    if "pump.fun" in url:
        return None   # 429 simulato
    if "tokens/v1" in url:
        return [pair(M1, 400000, 3000), pair(M2, 20000, 120)]
    return []
agg = T.TrendingAgg()
out = asyncio.run(agg.refresh(get))
by = {t["mint"]: t for t in out}
assert set(by) == {M1, M2}, by.keys()            # ethereum ignorato
assert by[M1]["families"] == ["dex", "gecko"] and by[M1]["n_fam"] == 2 and by[M1]["paid"]
assert by[M2]["n_fam"] == 1 and by[M2]["profile"]
assert out[0]["mint"] == M1 and by[M1]["trending_score"] > by[M2]["trending_score"]
assert agg.pump_pause_until > time.time()          # pump.fun in pausa dopo l'errore
n = len(calls); asyncio.run(agg.refresh(get))
assert not any("trending_pools" in u for u in calls[n:])   # GeckoTerminal in cache (5 min)
assert sum("trending_pools" in u for u in calls) == 3      # 5m, 1h, 6h
print("aggregatore OK")

# ---- watchlist: trending prioritari, nuove pool con limite e sfratto solo fra le non prioritarie
st = D.State()
D.CFG["WATCH_NEW_MAX"], D.CFG["WATCH_MAX"] = 3, 5
for i in range(4):
    D.add_watch(st, f"N{i}" + "x" * 40, "n", "nuova pool")
    st.watch[f"N{i}" + "x" * 40]["seen"] -= 100 - i
assert len([k for k, v in st.watch.items() if not v["prio"]]) == 3 and "N0" + "x" * 40 not in st.watch
D.add_watch(st, M1, "t", "trending_agg", prio=True); D.add_watch(st, M2, "t", "trending_agg", prio=True)
D.add_watch(st, "N9" + "x" * 40, "n", "nuova pool")
assert M1 in st.watch and M2 in st.watch and len(st.watch) == 5
D.add_watch(st, "N1" + "x" * 40, "n", "trending_agg", prio=True)   # gia in watch -> diventa prioritario
assert st.watch["N1" + "x" * 40]["prio"]
D.CFG["WATCH_NEW_MAX"], D.CFG["WATCH_MAX"] = 1200, 2000
print("watchlist OK")

# ---- prefiltro
m = D.summarize([pair(M1, 400000, 3000)])
f, snap = P.evaluate(m, D.pf_cfg(st), paid=True)
assert f == [], (f, snap)
bad = dict(m, liq=4000, vol_m5=10, buys_m5=1, sells_m5=1, chg_m5=-40, mcap=5000, age_min=1)
f, _ = P.evaluate(bad, D.pf_cfg(st))
for k in ("PF_MIN_LIQ_USD", "PF_MIN_VOL_M5", "PF_MIN_TXNS_M5", "PF_MIN_BUYS_M5", "PF_MIN_CHG_M5", "PF_MIN_MCAP", "PF_MIN_AGE_MIN"):
    assert k in f, (k, f)
f, _ = P.evaluate(m, dict(D.pf_cfg(st), PF_REQUIRE_PAID=1.0), paid=False); assert f == ["PF_REQUIRE_PAID"]
r = D.parse_overrides(json.dumps({"global": {"PF_MIN_LIQ_USD": 1, "PF_MIN_TXNS_H1": 99999, "TREND_WATCH_TOP": 500}}),
                      D.OVR.values(), {}, set())
g = r["globals"]
assert g["PF_MIN_LIQ_USD"] == 3000 and g["PF_MIN_TXNS_H1"] == 3000 and g["TREND_WATCH_TOP"] == 150, g
outc = [([], 0.0)] * 12 + [(["PF_MIN_TXNS_M5"], 30.0)] * 12 + [(["PF_MIN_CHG_M5"], -1.0)] * 12
res = P.learn_step(outc, D.pf_cfg(st))
assert list(res) == ["PF_MIN_TXNS_M5"] and res["PF_MIN_TXNS_M5"][1] < res["PF_MIN_TXNS_M5"][0], res
res = P.learn_step([([], 10.0)] * 12 + [(["PF_MIN_BSR_H1"], -30.0)] * 12, D.pf_cfg(st))
assert res["PF_MIN_BSR_H1"][1] > res["PF_MIN_BSR_H1"][0]   # scartati peggio -> stringe (alza)
st.learner_bonus.setdefault("pf_d", {})["PF_MIN_TXNS_M5"] = -500
assert D.pf_cfg(st)["PF_MIN_TXNS_M5"] == 0.0   # mai fuori dai limiti
st.learner_bonus["pf_d"] = {}
# registro + esito a 60 minuti
rec = P.log_eval(M1, "AAA", "trending_agg", ["PF_MIN_TXNS_M5"], dict(snap, price=0.001), now=time.time() - 4000)
pend = P.load_pending()
assert any(r["mint"] == M1 for r in pend)
class H:
    async def get(self, url, headers=None):
        return [dict(pair(M1, 1, 1), priceUsd="0.0015")]
asyncio.run(D.pf_learn(st, H(), set()))
assert st.learner_bonus["pf_hist"][-1][:2] == [["PF_MIN_TXNS_M5"], 50.0], st.learner_bonus["pf_hist"][-1]
os.remove(P.LOG_PATH)
print("prefiltro OK")

# ---- bonus multi-fonte dentro il tetto, e mai su una sola fonte
D.TREND_INFO[M1] = {"trending_score": 0.7, "families": ["dex", "gecko"], "n_fam": 2, "sources": [], "ranks": {}, "paid": True, "boost": 1, "t": time.time()}
D.TREND_INFO[M2] = dict(D.TREND_INFO[M1], families=["dex"], n_fam=1)
saved = dict(D.SRC); [D.SRC.pop(k, None) for k in ("mom", "theses", "kol", "proj")]
b1, t1 = asyncio.run(D.compute_bonus(st, M1, m)); b2, t2 = asyncio.run(D.compute_bonus(st, M2, m))
D.SRC.clear(); D.SRC.update(saved)
assert abs(b1 - 0.02) < 1e-9 and "trend_multi:2" in t1 and b2 == 0.0 and not any(x.startswith("trend_multi") for x in t2)
assert D.bonus_eff(st, "TREND_MULTI_BONUS") <= 0.04 and D.BONUS_LIMITS["TOTAL_BONUS_CAP"][1] == 0.10
fe = asyncio.run(D.entry_features(st, None, M1, dict(m, src="trending_agg"), allow_fetch=False))
assert fe["trending_score"] == 0.7 and fe["trend_families"] == ["dex", "gecko"] and fe["src"] == "trending_agg", fe
print("bonus/feature OK")
print("TUTTI I TEST v8.3 OK")
