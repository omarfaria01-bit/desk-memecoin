"""Compito 17: filtro nomi di marchi famosi (shadow di default, nessun effetto su punteggio finche'
BRAND_FILTER_MODE non e' 'on'). Offline, su copia in /tmp."""
import asyncio, time
import desk as D
import sources.brand_filter as BF

assert D.DATA_DIR.startswith("/tmp"), "test solo su copia in /tmp"
D.CFG["CLUSTER_V2"] = False
D.CFG["CLUSTER_CHECK"] = False
D.CFG["HONEYPOT_CHECK"] = False
D.CFG["CREATOR_HISTORY"] = False
D.CFG["SIGNALS_ENABLED"] = False
D.SOL_USD[0], D.SOL_USD[1] = 100.0, time.time()

# ---- brand_tag(): puro, nessuna rete
assert BF.brand_tag("MrBeast") == "MRBEAST"
assert BF.brand_tag("BEAST") == "BEAST"
assert BF.brand_tag("AMAZ0N") == "AMAZON"          # leetspeak: 0 -> O
assert BF.brand_tag("GTA 6") == "GTA6"
assert BF.brand_tag("GTA6COIN") == "GTA6"          # suffisso COIN staccato
assert BF.brand_tag("Grok") == "GROK"
assert BF.brand_tag("Robinhood") == "ROBINHOOD"
assert BF.brand_tag("AAPLCAT") is None             # mashup, non un marchio esatto: nessun falso positivo
assert BF.brand_tag("Goyim") is None
assert BF.brand_tag(None, None) is None

# ---- integrazione nell'Analyst: 'shadow' (default) non tocca il punteggio, 'on' si'
mk = {"symbol": "MrBeast", "name": "MrBeast", "price": 1.0, "liq": 500_000.0, "chg_h1": 10.0, "chg_m5": 2.0,
      "buys_m5": 30, "sells_m5": 10, "vol_m5": 50_000.0, "vol_h1_main": 100_000.0, "buys_h1": 300, "sells_h1": 100,
      "vol_h1": 100_000.0, "twitter": 1, "telegram": 1, "website": 1, "pair": "P", "url": "", "src": "pump.fun",
      "dex": "pumpswap"}


async def fake_candles(http, pool):
    return None
D.fetch_candles = fake_candles


async def run_once(mint, m, st):
    qi, qo = asyncio.Queue(), asyncio.Queue()
    await qi.put({"mint": mint, "m": m, "sec": {"soft": []}, "filter_ok": True})
    t = asyncio.ensure_future(D.analyst_agent(st, None, qi, qo))
    await asyncio.sleep(0.3)
    t.cancel()
    out = []
    while not qo.empty():
        out.append(qo.get_nowait())
    return out


D.CFG["BRAND_FILTER_MODE"] = "shadow"
st = D.State()
st.threshold = 0.0
out = asyncio.run(run_once("BR1", dict(mk), st))
assert len(out) == 1, out
assert "brand" not in out[0]["soft"] and "brand:MRBEAST" in out[0]["signals"], out[0]
score_shadow = out[0]["score"]

D.CFG["BRAND_FILTER_MODE"] = "on"
st = D.State()
st.threshold = 0.0
out = asyncio.run(run_once("BR2", dict(mk), st))
assert len(out) == 1, out
assert "brand" in out[0]["soft"] and "brand:MRBEAST" in out[0]["signals"], out[0]
assert out[0]["score"] < score_shadow, (out[0]["score"], score_shadow)          # penalita' applicata
assert out[0]["size_mult"] < 1.0, out[0]["size_mult"]                            # size ridotta

D.CFG["BRAND_FILTER_MODE"] = "off"
st = D.State()
st.threshold = 0.0
out = asyncio.run(run_once("BR3", dict(mk), st))
assert len(out) == 1 and not any(s.startswith("brand:") for s in out[0]["signals"].split("|")), out

# ---- un nome qualsiasi non e' mai taggato
D.CFG["BRAND_FILTER_MODE"] = "on"
st = D.State()
st.threshold = 0.0
out = asyncio.run(run_once("OK1", dict(mk, symbol="Goyim", name="Goyim"), st))
assert len(out) == 1 and "brand" not in out[0]["soft"], out

print("test_brand_filter: ok")
