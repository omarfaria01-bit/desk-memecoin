import asyncio, csv, json, os, time
import desk as D

D.CFG.update({"SIM_LATENCY_MIN_SEC": 0.0, "SIM_LATENCY_MAX_SEC": 0.0, "SIM_FAIL_PROB_BUY": 0.0,
              "SIM_FAIL_PROB_SELL": 0.0, "SIM_FAIL_PROB_SELL_FAST": 0.0, "SIM_QUOTE_EXTRA_FEE_PCT": 0.0})
D.SOL_USD[0], D.SOL_USD[1] = 100.0, time.time()

# ---- priority fee
assert D.percentile([1, 2, 3, 4, 5], 50) == 3 and D.percentile([], 75) == 0
assert abs(D.prio_sol_from_micro(0) - (100000 + 5000) / 1e9) < 1e-15            # floor
assert abs(D.prio_sol_from_micro(1e9) - (1000000 + 5000) / 1e9) < 1e-15         # cap PRIORITY_MAX_LAMPORTS
assert abs(D.prio_sol_from_micro(1e6) - (300000 + 5000) / 1e9) < 1e-15          # 1e6 uL x 300k CU
assert D.prio_fee_sol() == D.CFG["PRIORITY_FEE_SOL"]                              # no data -> fixed v5 fee
class RpcHttp:
    def __init__(self, fees): self.fees, self.calls = fees, 0
    async def post(self, url, body):
        self.calls += 1
        if body["method"] == "getRecentPrioritizationFees":
            return {"result": [{"slot": i, "prioritizationFee": f} for i, f in enumerate(self.fees)]}
        if body["method"] == "getTokenSupply":
            return {"result": {"value": {"decimals": 6}}}
        return {"result": 1}
h = RpcHttp([0] * 50 + [1_000_000] * 50)
assert asyncio.run(D.refresh_prio_fee(h, force=True)) and h.calls == 2
assert abs(D.prio_fee_sol() - 0.000305) < 1e-12 and abs(D.prio_fee_sol(True) - 0.000455) < 1e-12
assert abs(D.prio_sol_from_micro(0, 1.5) - (150000 + 5000) / 1e9) < 1e-15             # urgent floor x1.5
assert not asyncio.run(D.refresh_prio_fee(h)) and h.calls == 2                    # throttled
assert not asyncio.run(D.refresh_prio_fee(RpcHttp([]), force=True))              # empty -> keep
D.PRIO_DYN["ts"] = time.time() - 10_000; assert D.prio_fee_sol() == D.CFG["PRIORITY_FEE_SOL"]  # stale -> fixed
D.PRIO_DYN["ts"] = time.time()
print("prio fee OK", D.prio_fee_sol(), D.prio_fee_sol(True))

# ---- mocked Jupiter: pool with constant product, price 1 USD/token, 6 decimals
POOL = {"tok": 1_000_000.0, "usd": 1_000_000.0, "route": True, "sell_route": True, "drift": 0.0, "calls": 0}
async def fake_quote(http, inp, out, amount, slip_bps=None):
    POOL["calls"] += 1
    if not POOL["route"] or (out == D.WSOL and not POOL["sell_route"]):
        return None, "no_route"
    if inp == D.WSOL:   # buy: lamports -> token raw
        usd = amount / 1e9 * D.sol_usd()
        tok = POOL["tok"] * usd / (POOL["usd"] + usd) * (1 - POOL["drift"])
        return {"outAmount": str(int(tok * 1e6)), "priceImpactPct": "0.001"}, None
    tok = amount / 1e6
    usd = POOL["usd"] * tok / (POOL["tok"] + tok) * (1 - POOL["drift"])
    return {"outAmount": str(int(usd / D.sol_usd() * 1e9))}, None
D.jup_quote = fake_quote
async def fake_dec(http, mint): return 6
D.token_decimals = fake_dec
async def fb(http, mints): return {}
D.dex_batch = fb

br = D.PaperBroker(http=RpcHttp([1000] * 10)); assert br.realistic
assert D.PaperBroker().realistic is False
st = D.State(); st.cash = 1000.0
m = {"symbol": "S", "price": 1.0, "liq": 2_000_000.0, "url": ""}
def cand(): return {"filter_ok": True, "analyst_ok": True, "score": 0.6, "thesis": "t", "features": {}, "sec": {}}
# reserve: cash available excludes SOL_RESERVE (0.03 SOL = $3)
assert abs(asyncio.run(br.cash_usd(st)) - 997.0) < 1e-9

asyncio.run(D._execute_buy(st, br, cand(), "A", m))
p = st.positions["A"]
assert p["quote_src"] == "jupiter" and p["fill_failed"] == 0 and abs(p["cost"] - 30) < 1e-6
assert 0 < p["quote_impact"] < 0.01 and p["latency_slip"] == 0.0 and p["rt_cost_pct"] < 1, p
assert abs(st.cash - 970) < 1e-9
print("buy via quote OK", p["quote_impact"], p["rt_cost_pct"])

# mark from real sell quote
asyncio.run(br.refresh_mark("A", p)); assert 0.99 < p["qmark"]["ratio"] <= 1.0
assert abs(D.mark_value(p) - (p["tokens"] * p["qmark"]["ratio"] - D.prio_fee_usd())) < 1e-9

# honeypot: buy route ok, no sell route -> reject
POOL["sell_route"] = False
asyncio.run(D._execute_buy(st, br, cand(), "HP", m)); assert "HP" not in st.positions
assert st.reject_reasons["non rivendibile"] == 1
POOL["sell_route"] = True
# no buy route at all
POOL["route"] = False; asyncio.run(D._execute_buy(st, br, cand(), "NR", m)); POOL["route"] = True
assert st.reject_reasons["nessun percorso Jupiter"] == 1
# extreme round trip (tiny pool) -> reject by sell-check (pool share cap off for this test)
D.CFG["MAX_POOL_SHARE_PCT"] = 100.0
POOL.update(tok=100.0, usd=100.0); asyncio.run(D._execute_buy(st, br, cand(), "TH", {**m, "liq": 1e9}))
assert "TH" not in st.positions and st.reject_reasons["rivendita troppo cara"] == 1
POOL.update(tok=1_000_000.0, usd=1_000_000.0); D.CFG["MAX_POOL_SHARE_PCT"] = 1.0
print("sell-check OK")

# latency slippage beyond SLIPPAGE_BPS -> failed tx pays fee, retry also fails -> no position
calls = {"n": 0}
orig = fake_quote
async def drifting(http, inp, out, amount, slip_bps=None):
    calls["n"] += 1
    POOL["drift"] = 0.0 if calls["n"] <= 2 else 0.05   # 2 precheck quotes ok, fill quotes 5% worse
    return await orig(http, inp, out, amount, slip_bps)
D.jup_quote = drifting
cash0 = st.cash
asyncio.run(D._execute_buy(st, br, cand(), "LS", m))
assert "LS" not in st.positions and st.stats["fill_failed"] == 2
assert abs((cash0 - st.cash) - 2 * D.prio_fee_usd()) < 1e-9                       # two failed fees paid
POOL["drift"] = 0.0; D.jup_quote = orig
print("latency slippage fail OK")

# random failure then retry succeeds
seq = iter([0.0, 1.0])
class R:
    def random(self): return next(seq)
    def uniform(self, a, b): return a
D.SIM["rng"] = R(); D.CFG["SIM_FAIL_PROB_BUY"] = 0.5
asyncio.run(D._execute_buy(st, br, cand(), "RT", m)); assert st.positions["RT"]["fill_failed"] == 1
D.CFG["SIM_FAIL_PROB_BUY"] = 0.0
import random; D.SIM["rng"] = random.Random(1)
print("retry OK")

# sells: normal, urgent failure, no route, write-off
D.CFG["SIM_FAIL_PROB_SELL_FAST"] = 1.0
asyncio.run(D.sell_position(st, br, "A", "stop loss a -10.5%"))
assert "A" in st.positions and st.positions["A"]["sell_fails"] == 1 and st.positions["A"]["fill_failed"] == 1
D.CFG["SIM_FAIL_PROB_SELL_FAST"] = 0.0
asyncio.run(D.sell_position(st, br, "A", "stop loss a -10.5%"))
assert "A" not in st.positions
r = list(csv.DictReader(open("trades.csv")))[-1]
for k in ("quote_impact", "sell_quote_impact", "prio_fee_sol", "latency_slip", "fill_failed", "quote_src"):
    assert k in r, k
assert r["quote_src"] == "jupiter" and r["fill_failed"] == "1" and float(r["prio_fee_sol"]) > 0
POOL["sell_route"] = False; D.CFG["SELL_NOROUTE_WRITE_OFF"] = 3
for _ in range(3):
    asyncio.run(D.sell_position(st, br, "RT", "tempo massimo"))
assert "RT" not in st.positions and list(csv.DictReader(open("trades.csv")))[-1]["value"] == "0.0"
POOL["sell_route"] = True
print("sells OK")

# formula fallback when quotes fail for network reasons
async def down(http, inp, out, amount, slip_bps=None): return None, "rete (TimeoutError)"
D.jup_quote = down
asyncio.run(D._execute_buy(st, br, cand(), "FB", m)); assert st.positions["FB"]["quote_src"] == "formula"
asyncio.run(D.sell_position(st, br, "FB", "tempo massimo")); assert "FB" not in st.positions
D.jup_quote = orig
print("fallback OK")

# HARD_STOP total loss
st2 = D.State(); st2.cash = 650.0; st2.total_start = 1000.0; D.CFG["RISK_CHECK_SEC"] = 0
async def run(n=2):
    t = asyncio.create_task(D.risk_agent(st2, None, D.PaperBroker()))
    for _ in range(n): await asyncio.sleep(0.005)
    t.cancel()
asyncio.run(run()); assert st2.hard_tripped and os.path.exists(D.HARD_STOP_PATH)
asyncio.run(D._execute_buy(st2, D.PaperBroker(), cand(), "Z", m)); assert "Z" not in st2.positions
os.remove(D.HARD_STOP_PATH); asyncio.run(run()); assert not st2.hard_tripped and st2.total_start == 650.0
print("hard stop OK")

# readiness (mocked network)
class FullHttp(RpcHttp):
    pass
async def okq(http, inp, out, amount, slip_bps=None): return {"outAmount": "9400000"}, None
D.jup_quote = okq
D.PRIO_DYN["last_try"] = 0
rep = asyncio.run(D.readiness_check(D.State(), FullHttp([5000] * 20), br))
assert rep["total"] >= 10 and os.path.exists(D.READINESS_PATH) and abs(D.SOL_USD[0] - 94.0) < 1e-9
print("readiness OK", rep["ok"], "/", rep["total"])
print("ALL v6 OK")
