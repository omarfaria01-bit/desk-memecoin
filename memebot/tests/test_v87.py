"""v8.7: (D) seme creatori Slinky21 + serial_launcher; (E) primi scambi pump.fun; (F) p_prior. Tutto SOLO shadow.
Nessuna rete: client finti. SOLO su una copia in /tmp."""
import asyncio
import desk as D
from sources import creator_history as CH, smart_trades as SX

assert D.DATA_DIR.startswith("/tmp"), "test solo su copia in /tmp"
D.write_event = lambda *_a, **_k: None

# ---- D: seme
CH.SEED.update(loaded=True, c={"SER1": [12, 0, 1, 0], "GOOD": [3, 2, 0, 1]})
assert CH.seed_info("SER1")["serial_launcher"] is True
assert CH.seed_info("GOOD")["serial_launcher"] is False and CH.seed_info("GOOD")["g"] == 2
assert CH.seed_info("NONE") is None and CH.seed_info(None) is None
calls = []


async def get_ok(url):
    calls.append(url)
    if "user-created-coins" in url:
        return {"count": 1, "coins": []}
    return {"creator": "SER1", "created_timestamp": 1000}

CH._MEM.update(loaded=True, data={"mint": {}, "creator": {}})
CH._save = lambda: None
r = asyncio.run(CH.lookup(get_ok, "M1pump"))
assert r["seed"]["serial_launcher"] is True and r["level"] == "none" and r["pen"] == 0.0, r   # nessun effetto
cr, sd = CH.seed_for_mint("M1pump")
assert cr == "SER1" and sd["n"] == 12
# il seme reale esiste ed e compatto
import json, os
sp = os.path.join(os.path.dirname(CH.__file__), "creator_seed.json")
if os.path.exists(sp):
    j = json.load(open(sp))
    assert j["meta"]["n_creators"] == len(j["c"]) > 1000

# ---- E: compute
def tr(ms, side, w, amt):
    return {"blockTimeMs": ms, "side": side, "trader": {"address": w}, "baseAmount": {"raw": str(int(amt * 1e6)),
                                                                                       "decimals": 6}}

T0 = 1_000_000
trades = [tr(T0, "buy", "DEV", 50e6), tr(T0 + 1000, "buy", "S1", 30e6), tr(T0 + 2000, "buy", "S2", 20e6),
          tr(T0 + 10000, "buy", "B3", 10e6), tr(T0 + 20000, "sell", "S1", 15e6), tr(T0 + 30000, "sell", "DEV", 50e6)]
f = SX.compute(list(reversed(trades)), creator="DEV", created_ms=T0)
assert f["sniper_pct"] == 5.0, f            # 50M di 1e9 comprati da S1+S2 entro 3 s (DEV escluso)
assert f["first20_sold_pct"] == 25.0, f     # 15M venduti su 60M comprati
assert f["dev_first_sell_dt"] == 30.0 and f["dev_sold"] == 1, f
assert SX.compute([])["sniper_pct"] is None
pages = []


async def get_pages(url):
    pages.append(url)
    if "cursor" not in url:
        return {"trades": [dict(trades[-1], txIndex=i) for i in range(100)], "cursor": "a/b+c"}
    return {"trades": trades[:-1], "cursor": None}

r = asyncio.run(SX.fetch(get_pages, "Xpump", "DEV", T0))
assert r["ok"] and r["pages"] == 2 and "cursor=a%2Fb%2Bc" in pages[1], (r, pages)


async def get_many(url):
    return {"trades": [trades[0]] * 100, "cursor": "x"}

r = asyncio.run(SX.fetch(get_many, "Xpump", cfg={"MAX_PAGES": 3}))
assert r["ok"] is False and "incompleto" in r["why"] and r["pages"] == 3


async def get_bad(url):
    raise RuntimeError("x")

assert asyncio.run(SX.fetch(get_bad, "Xpump"))["ok"] is False

# ---- F: p_prior
if D.MODEL_PRIOR:
    f0 = {k: 0.0 for k in D.MODEL_PRIOR["w"]}
    f1 = dict(f0, higher_lows=1.0, holders=1.0, concentration=1.0)
    p0, p1 = D.prior_prob(f0), D.prior_prob(f1)
    assert 0 < p0 < p1 < 1, (p0, p1)
    assert D.prior_note("PM", f1) == p1 and D.PRIOR_LAST["PM"] == p1

# ---- v87_fields: tutto nel dict fe, E in background con limite orario, nessuna eccezione
class H:
    async def get(self, url):
        return await get_pages(url)

pages.clear()
CH._MEM["data"]["mint"]["Y1pump"] = {"t": 0, "creator": "DEV", "created": T0}


async def run_fields():
    fe = {}
    D.v87_fields(None, H(), "Y1pump", {"symbol": "Y"}, fe)
    assert fe["stx"]["ok"] is None and fe["serial_launcher"] == 0
    await asyncio.sleep(0.05)
    return fe

fe = asyncio.run(run_fields())
assert fe["stx"]["ok"] and fe["sniper_pct"] == 5.0 and fe["dev_first_sell_dt"] == 30.0, fe
D.STX_STATE["t"] = [D.time.time()] * 30
fe2 = {}
D.v87_fields(None, H(), "Z1pump", {"symbol": "Z"}, fe2)
assert fe2["stx"]["why"] == "limite orario"
fe3 = {}
D.v87_fields(None, H(), "NOTPUMPmint", {"symbol": "N"}, fe3, allow_fetch=True)
assert "stx" not in fe3
D.CFG["STX_MODE"] = "off"
D.STX_STATE["t"] = []
fe4 = {}
D.v87_fields(None, H(), "W1pump", {"symbol": "W"}, fe4)
assert "stx" not in fe4
print("test_v87: OK")

# ---- fix v8.7: shadow aperto prima della v8.6 (senza realized_cpmm) non conta due volte la vendita in corso
D.CFG["SHADOW_ROUTE_CHECK"] = False
D.prio_fee_usd = lambda *_a, **_k: 0.0
D._close_shadow_row = lambda *_a, **_k: None
st = type("S", (), {})()
st.shadow_trades = {}
sh = {"cost": 30.0, "tokens": 497914.5, "realized": 19.97, "last_price": 5.0e-05, "liq": 57007.0, "dex": None,
      "price": 2.916e-05}
st.shadow_trades["L"] = sh
asyncio.run(D.shadow_apply(st, None, "L", sh, ("trailing 15%", 1.0, False, False)))
gross = 497914.5 * 5.0e-05
assert sh["realized_cpmm"] < 19.97 + gross + 1e-6 and abs(sh["realized_cpmm"] - sh["realized"]) < 2.0, sh
print("test_v87 fix legacy: OK")
