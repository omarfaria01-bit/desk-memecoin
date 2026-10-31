import json, os, time, asyncio, csv
import desk as D
D.CFG["CLUSTER_V2"] = False; D.CFG["HONEYPOT_CHECK"] = False  # v8.2: provati a parte in test_v82.py
B = D.narrative_bonus
def nar(**k):
    d = {"theme": "T", "tickers": ["AAA"], "mints": [], "keywords": ["robotics"], "strength": 0.5, "trend": "rising",
         "velocity": 2}
    d.update(k); return d
def P(*ns): return D.parse_narratives(json.dumps({"updated_at": "2026-10-30T18:00:00Z", "narratives": list(ns),
                                                   "blocklist_mints": ["BAD"]}))
_, n, bl = P(nar(velocity=None)); assert B(n, "M", "AAA", "")[0] == 0.0                # null velocity: no bonus
_, n, bl = P(nar(velocity=0)); assert B(n, "M", "AAA", "")[0] == 0.0                   # velocity 0, strength<0.7
_, n, bl = P(nar(strength=0.8)); assert B(n, "M", "AAA", "")[0] == 0.0                 # crowded but still moving
_, n, bl = P(nar(strength=0.8, velocity=-1)); assert B(n, "M", "AAA", "")[0] == -0.05  # late
_, n, bl = P(nar(strength=0.8, velocity=None)); assert B(n, "M", "AAA", "")[0] == 0.0  # null vel: no late penalty
_, n, bl = P(nar(trend="fading", strength=0.2)); assert B(n, "M", "AAA", "")[0] == -0.05
_, n, bl = P(nar(mints=["REAL"], mint_confidence={"REAL": 0.9}))
assert B(n, "COPY", "AAA", "")[0] == -0.05 and B(n, "REAL", "AAA", "")[0] == 0.05       # copycat vs real
_, n, bl = P(nar(mints=["REAL"], mint_confidence={"REAL": 0.5})); assert B(n, "COPY", "AAA", "")[0] == 0.05
_, n, bl = P(nar(trend="fading", mints=["REAL"], mint_confidence={"REAL": 0.9}))
assert B(n, "COPY", "AAA", "")[0] == -0.08                                                # -0.10 capped at -0.08
_, n, bl = P(nar(), nar(theme="T2"), nar(theme="T3")); assert B(n, "M", "AAA", "")[0] == 0.08
_, n, bl = P(nar(trusted_mentions=3)); assert B(n, "M", "QQ", "robotics inc")[0] == 0.045
assert B(n, "BAD", "AAA", "", bl)[0] == 0.0 and "BAD" in bl
print("narratives v5 OK")

# costs
D.SOL_USD[0] = 100.0
prio = 0.1
v = D.exit_value(100, 1.0, 10000)
exp = 100 * (1 - 100 / 5100) * (1 - 0.02) * 0.99 - prio; assert abs(v - exp) < 1e-9, (v, exp)
tok, fill, imp, fee = D.buy_tokens(100, 1.0, 10000); net = 100 - 1 - prio
assert abs(tok - net / ((1 + net / (5000 + net)) / (1 - 2 * net / 10000))) < 1e-9
rt = D.roundtrip_cost_pct(30, 8000); print("rt $30 @ $8k liq = %.2f%%" % rt, " @ $20k = %.2f%%" % D.roundtrip_cost_pct(30, 20000))
print("costs OK")

# window5 / vwap
now = 1_000_000_000.0; last = now - 30
cs = [(last - 60 * i, 1, 1.1, 0.9, 1.0, 10.0) for i in range(30, 5, -1)] + \
     [(last - 60 * i, 1, 1.3, 1.1, 1.2, 50.0) for i in range(4, -1, -1)]
w = D.window5(sorted(cs), now); assert w["vol_now"] == 250 and w["vol_prev"] == 40 and abs(w["vwap"] - 1.2) < 1e-9
assert D.window5(sorted(cs), now + 3600) is None
print("window5 OK")

# sizing + cost gate + exposure
async def sizing():
    st = D.State(); st.cash = 1000.0
    def c(score): return {"filter_ok": True, "analyst_ok": True, "score": score, "thesis": "t", "features": {}, "sec": {"lp_status": "ok"}}
    m = {"symbol": "S", "price": 1.0, "liq": 200000.0, "url": ""}
    await D._execute_buy(st, D.PaperBroker(), c(0.6), "A", m); assert abs(st.positions["A"]["cost"] - 30) < 1e-6
    await D._execute_buy(st, D.PaperBroker(), c(1.2), "B", m); assert abs(st.positions["B"]["cost"] - 0.05 * D.equity(st)) < 2
    await D._execute_buy(st, D.PaperBroker(), c(0.3), "C", m); assert abs(st.positions["C"]["cost"] - 0.02 * D.equity(st)) < 1
    await D._execute_buy(st, D.PaperBroker(), c(0.6), "D", dict(m, liq=2500.0))              # 1% of pool = $25 ...
    assert "D" not in st.positions                                                          # ... but costs too high
    await D._execute_buy(st, D.PaperBroker(), c(0.6), "E", dict(m, liq=60000.0))
    assert "E" in st.positions and st.positions["E"]["rt_cost_pct"] * 5 <= 30
    for i in range(20): await D._execute_buy(st, D.PaperBroker(), c(0.9), f"Z{i}", m)
    assert len(st.positions) <= 8, len(st.positions)
    exp = sum(D.mark_value(p) for p in st.positions.values()); assert exp <= 0.30 * D.equity(st) + 1, exp
    return st
st2 = asyncio.run(sizing()); print("sizing OK", len(st2.positions), [round(p["cost"], 1) for p in st2.positions.values()])

# exits
st = D.State(); D.CFG["RISK_CHECK_SEC"] = 0
def mkpos(price=1.0, age_min=0):
    st.positions["M"] = {"symbol": "TST", "tokens": 100.0, "tokens_entry": 100.0, "last_price": price, "entry_mark": 1.0,
        "liq": 1e9, "liq_entry": 1e9, "peak_pct": 0.0, "stop_hits": 0, "opened": time.time() - age_min * 60,
        "last_ok": time.time() + 1e7, "tp_done": False, "tp_stage": 0, "realized": 0.0, "cost": 100.0}
    st.sec_checked["M"] = time.time() + 1e7
    return st.positions["M"]
async def fb(http, mints): return {}
D.dex_batch = fb
async def run(n=2):
    t = asyncio.create_task(D.risk_agent(st, None, D.PaperBroker()))
    for _ in range(n): await asyncio.sleep(0.005)
    t.cancel()
D.SOL_USD[0] = 0.0001  # neutralise priority fee for exact thresholds
p = mkpos(1.33); asyncio.run(run(2)); assert p["tp_stage"] == 1 and abs(p["tokens"] - 70) < 1e-6, p["tokens"]
assert p["eff"]["stop"] == 3.0
p["last_price"] = 1.84; asyncio.run(run(2)); assert p["tp_stage"] == 2 and abs(p["tokens"] - 40) < 1e-6, p["tokens"]
p["last_price"] = 2.6; asyncio.run(run(2)); assert "M" in st.positions and p["eff"]["trail_dist"] == 8.0
p["last_price"] = 2.6 * 0.95; asyncio.run(run(2)); assert "M" not in st.positions                    # 8% trail
p = mkpos(1.33); asyncio.run(run(2)); assert p["tp_stage"] == 1
p["last_price"] = 1.04; asyncio.run(run(3)); assert "M" not in st.positions  # breakeven stop
p = mkpos(1.5); asyncio.run(run(1)); p["last_price"] = 1.35; asyncio.run(run(1))
assert "M" in st.positions and p["eff"]["trail_dist"] == 15                                         # peak ~+49, 15% trail
p["last_price"] = 1.3; asyncio.run(run(2)); assert "M" not in st.positions                         # +29 <= 49-15
mkpos(1.04, 31); asyncio.run(run(2)); assert "M" not in st.positions                                # time stop
p = mkpos(1.07, 31); asyncio.run(run(2)); assert "M" in st.positions; st.positions.clear()           # >= +5%
mkpos(1.03, 29); asyncio.run(run(2)); assert "M" in st.positions; st.positions.clear()
mkpos(0.91); asyncio.run(run(3)); assert "M" in st.positions                                        # -9% no SL
st.positions.clear(); mkpos(0.895)
asyncio.run(run(3)); assert "M" not in st.positions                                                 # -10% x2
mkpos(0.84); asyncio.run(run(1)); assert "M" not in st.positions                                    # crash -15
mkpos(1.02, 181); D.CFG["TIME_STOP_MIN"] = 500; asyncio.run(run(2)); assert "M" not in st.positions  # max hold 180
D.CFG["TIME_STOP_MIN"] = 30
# overrides on top
open(D.OVERRIDES_PATH, "w").write(json.dumps({"updated_at": "a", "global": {"TP1_PCT": 20, "TP1_FRACTION": 0.5,
     "BREAKEVEN_PCT": 1, "TIME_STOP_MIN": 10, "TRAIL_TIGHT_DIST_PCT": 1}}))
p = mkpos(1.22); asyncio.run(run(2)); assert p["tp_stage"] == 1 and abs(p["tokens"] - 50) < 1e-6 and p["eff"]["stop"] == 1
assert D.CFG["TRAIL_TIGHT_PCT"] == 2 and D.CFG["TIME_STOP_MIN"] == 10
st.positions.clear(); mkpos(1.0, 11); asyncio.run(run(2)); assert "M" not in st.positions          # time stop 10 min
os.remove(D.OVERRIDES_PATH); asyncio.run(run(2))
assert (D.CFG["TP1_PCT"], D.CFG["TP1_FRACTION"], D.CFG["BREAKEVEN_PCT"], D.CFG["TIME_STOP_MIN"]) == (30, 0.3, 3, 30)
print("exits OK")

# daily stop resets at Rome midnight
st = D.State(); st.cash = 880.0; st.day_start_equity = 1000.0; st.day_key = D.datetime.now(D.TZ).strftime("%Y-%m-%d")
asyncio.run(run(2)); assert st.paused
st.day_key = "2000-01-01"; asyncio.run(run(2)); assert not st.paused and st.day_start_equity == 880.0
print("daily stop OK")
r = list(csv.DictReader(open("trades.csv")))[-1]; assert "rt_cost_pct" in r and "vwap_gap" in r
print("trades.csv OK")
