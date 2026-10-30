import json, os, asyncio, time
import desk as D
P = D.parse_overrides
base = {k: D.CFG[k] for k in D.OVR_LIMITS}
H = {"MINTA"}
# invalid JSON
r = P("{not json", base, {}, H); assert not r["ok"] and r["globals"] == base and r["notes"][0][0] == "warn"
r = P("[1,2]", base, {}, H); assert not r["ok"]
r = P("", base, {}, H); assert not r["ok"]
# clamps
r = P(json.dumps({"global": {"STOP_LOSS_PCT": -40, "TRAIL_DIST_PCT": 1, "TRAIL_START_PCT": 500, "TP_PCT": 1,
      "TP_SELL_FRACTION": 2, "MAX_HOLD_MIN": 10000}}), base, {}, H)
g = r["globals"]; assert g["STOP_LOSS_PCT"] == -30 and g["TRAIL_DISTANCE_PCT"] == 3 and g["TRAIL_ACTIVATE_PCT"] == 100
assert g["TAKE_PROFIT_PCT"] == 5 and g["TAKE_PROFIT_FRACTION"] == 1.0 and g["MAX_HOLD_MIN"] == 720
assert any("piu largo" in n for _, n in r["notes"])
r = P(json.dumps({"global": {"STOP_LOSS_PCT": -1}}), base, {}, H); assert r["globals"]["STOP_LOSS_PCT"] == -3
r = P(json.dumps({"global": {"STOP_LOSS_PCT": 6}}), base, {}, H); assert r["globals"]["STOP_LOSS_PCT"] == -6
# wrong types / missing keep last good
prev = dict(base, STOP_LOSS_PCT=-5.0)
r = P(json.dumps({"global": {"STOP_LOSS_PCT": "x", "TP_PCT": True, "FOO": 1}}), prev, {}, H)
assert r["ok"] and r["globals"] == prev and sum(1 for l, _ in r["notes"] if l in ("clamp", "warn")) == 3
r = P(json.dumps({"global": {"STOP_LOSS_PCT": None}}), prev, {}, H); assert r["globals"]["STOP_LOSS_PCT"] == -10.0
r = P(json.dumps({"global": [1]}), prev, {}, H); assert r["globals"] == prev
r = P(json.dumps({"global": {"STOP_LOSS_PCT": float("nan")}}).replace("NaN", "NaN"), prev, {}, H)
assert r["globals"] == prev
# positions
r = P(json.dumps({"positions": {"MINTA": {"stop_loss_pct": -4, "trail_dist_pct": 50, "action": "sell_half", "reason": "r"},
      "OTHER": {"action": "sell_all"}}}), base, {}, H)
pa = r["positions"]["MINTA"]; assert pa["STOP_LOSS_PCT"] == -4 and pa["TRAIL_DISTANCE_PCT"] == 25 and pa["action"] == "sell_half"
assert "OTHER" not in r["positions"]
r = P(json.dumps({"positions": {"MINTA": {"action": "dump"}}}), base, {}, H); assert r["positions"]["MINTA"]["action"] == "hold"
# new keys + clamps; SL warning now vs -10
r = P(json.dumps({"global": {"TP1_PCT": 1, "TP1_FRACTION": 0.9, "TP2_PCT": 900, "TP2_FRACTION": 0.01, "BREAKEVEN_PCT": 50,
      "TIME_STOP_MIN": 2.6, "TIME_STOP_MIN_PCT": -40, "TRAIL_TIGHT_AFTER_PCT": 5, "TRAIL_TIGHT_DIST_PCT": 30}}), base, {}, H)
g = r["globals"]
assert (g["TP1_PCT"], g["TP1_FRACTION"], g["TAKE_PROFIT_PCT"], g["TAKE_PROFIT_FRACTION"], g["BREAKEVEN_PCT"]) == (5, 0.6, 500, 0.1, 15)
assert (g["TIME_STOP_MIN"], g["TIME_STOP_MIN_PCT"], g["TRAIL_TIGHT_AFTER_PCT"], g["TRAIL_TIGHT_PCT"]) == (5, -10, 20, 15)
assert isinstance(g["TIME_STOP_MIN"], int)
r = P(json.dumps({"global": {"TP_PCT": 60, "TP2_PCT": 70}}), base, {}, H); assert r["globals"]["TAKE_PROFIT_PCT"] == 70
r = P(json.dumps({"global": {"STOP_LOSS_PCT": -10}}), base, {}, H)
assert r["globals"]["STOP_LOSS_PCT"] == -10 and not any("piu largo" in n for _, n in r["notes"])
r = P(json.dumps({"global": {"STOP_LOSS_PCT": -12}}), base, {}, H)
assert r["globals"]["STOP_LOSS_PCT"] == -12 and any("piu largo di -10%" in n for _, n in r["notes"])
print("parse/clamp OK")

# runtime: file reload, action once, delete reverts
st = D.State(); D.CFG["RISK_CHECK_SEC"] = 0
st.positions["MINTA"] = {"symbol": "TST", "tokens": 1000.0, "last_price": 1.0, "entry_mark": 1.0, "liq": 1e6,
    "liq_entry": 1e6, "peak_pct": 0.0, "stop_hits": 0, "opened": time.time(), "last_ok": time.time() + 10**6,
    "tp_done": False, "realized": 0.0, "cost": 1000.0}
st.cash = 0.0
st.sec_checked["MINTA"] = time.time() + 10**6
def w(d):
    open(D.OVERRIDES_PATH, "w").write(json.dumps(d) if isinstance(d, dict) else d); t = time.time_ns() + 10**9 * len(open(D.OVERRIDES_PATH).read()); os.utime(D.OVERRIDES_PATH, ns=(t, t))
class Http: pass
async def fake_batch(http, mints): return {}
D.dex_batch = fake_batch
async def cycles(n):
    t = asyncio.create_task(D.risk_agent(st, Http(), D.PaperBroker()))
    for _ in range(n): await asyncio.sleep(0.01)
    t.cancel()
w({"updated_at": "v1", "global": {"STOP_LOSS_PCT": -12, "TP_PCT": 60}, "positions": {"MINTA": {"action": "sell_half", "reason": "test"}}})
asyncio.run(cycles(5))
assert D.CFG["STOP_LOSS_PCT"] == -12 and D.CFG["TAKE_PROFIT_PCT"] == 60
assert abs(st.positions["MINTA"]["tokens"] - 500) < 1e-6, st.positions["MINTA"]["tokens"]  # sold once only
assert st.positions["MINTA"]["tp_done"] is False
w("{broken")
asyncio.run(cycles(3)); assert D.CFG["STOP_LOSS_PCT"] == -12
w({"updated_at": "v2", "positions": {"MINTA": {"action": "sell_half", "reason": "again"}}})
asyncio.run(cycles(5)); assert abs(st.positions["MINTA"]["tokens"] - 250) < 1e-6
open(D.STOP_PATH, "w").close()
w({"updated_at": "v3", "positions": {"MINTA": {"action": "sell_half"}}})
asyncio.run(cycles(3)); assert "MINTA" not in st.positions  # STOP sells all first
os.remove(D.STOP_PATH)
os.remove(D.OVERRIDES_PATH)
asyncio.run(cycles(3)); assert D.CFG["STOP_LOSS_PCT"] == -10.0 and D.CFG["TAKE_PROFIT_PCT"] == 80.0
print("runtime OK")
