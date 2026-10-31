"""v8.6: (A) pnl_cpmm drain-aware + TP come limite + latenza d'uscita; (B) pausabile = rigido, flag Token-2022;
(C) seconda conferma solo registrata. SOLO su una copia in /tmp."""
import asyncio, csv, os, time
import desk as D
from sources import honeypot as HP

assert D.DATA_DIR.startswith("/tmp"), "test solo su copia in /tmp"
D.tg_send = lambda *_a, **_k: None
D.log = lambda *_a, **_k: None
D.save_state = lambda *_a, **_k: None
D.CFG["SHADOW_ROUTE_CHECK"] = False
D.HOST_PAUSE["last_resume"] = 0.0
D.prio_fee_usd = lambda *_a, **_k: 0.0
for f in (D.SHADOW_CSV, os.path.join(D.DATA_DIR, "trades.csv")):
    if os.path.exists(f):
        os.remove(f)
D.ensure_shadow_csv()

# ---- A1: cpmm_exit = x*y=k esatto sulla meta pool, drain-aware
v, out = D.cpmm_exit(1000.0, 0.01, 1000.0, 0.0, "raydium")   # gross 10 su riserva 500
assert abs(out - 500 * 10 / 510) < 1e-9
v2, out2 = D.cpmm_exit(1000.0, 0.01, 1000.0, 400.0, "raydium")   # riserva rimasta 100: piu impatto
assert out2 < out and abs(out2 - 100 * 10 / 110) < 1e-9
assert D.cpmm_exit(0, 1, 1000)[0] == 0.0
# a pool grande e size piccola cpmm ~ exit_value (stessa scala)
a = D.exit_value(100.0, 0.1, 1e6); b = D.cpmm_exit(100.0, 0.1, 1e6, 0.0, None)[0]
assert abs(a - b) / a < 0.01, (a, b)
# ---- A2: track con zero e cap TP
o = {"realized": 3.0}
D.cpmm_track(o, 100.0, 0.1, 1e6, zero=True)
assert o["realized_cpmm"] == 3.0 and o["drained_cpmm"] == 0.0
cap = D.tp_cap_price(0.2, 60.0, 30.0)   # pnl +60 con gradino +30: limite al +30
assert abs(cap - 0.2 * 1.3 / 1.6) < 1e-12 and D.tp_cap_price(0.2, 20.0, 30.0) is None
D.cpmm_track(o, 100.0, 0.2, 1e6, cap_price=cap)
assert o["realized_cpmm"] < 3.0 + 100 * 0.2 * 0.99 and o["drained_cpmm"] > 0

# ---- A3: stop_watch + exit_lag_fields
p = {"entry_mark": 0.01, "eff": {"stop": -10.0, "crash": -25.0}}
D.stop_watch(p, -5.0, -10.0, -25.0, now=1000.0)
D.stop_watch(p, -12.0, -10.0, -25.0, now=1015.0)
D.stop_watch(p, -18.0, -10.0, -25.0, now=1031.0)
assert p["stop_t0"] == 1015.0 and p["stop_t0_gap"] == 15.0 and p["stop_t0_pnl"] == -12.0
f, x = D.exit_lag_fields(p, "stop loss a -18.0%", 8.0, 1000.0, now=1033.0)
assert f == {"stop_level": -10.0, "exit_fill": -20.0, "exit_lag_s": 18.0}, f
assert x["exit_gap_prev_s"] == 15.0 and x["chk_gap_avg_s"] == 15.5
assert D.exit_lag_fields(p, "trailing 15%", 8.0, 1000.0)[0]["stop_level"] is None
D.stop_watch(p, 2.0, -10.0, -25.0, now=1050.0)
assert "stop_t0" not in p

# ---- A4: shadow con stop -> colonne nel CSV
st = D.State()
now = time.time()
sh = {"t": now - 600, "sym": "STP", "reason": "x", "keys": ["score"], "flags": [], "score": 0.5, "thr": 0.6,
      "price": 0.01, "last_price": 0.0088, "liq": 50000.0, "liq_entry": 50000.0, "last_ok": now, "cost": 10.0,
      "tokens": 1000.0, "tokens_entry": 1000.0, "entry_mark": 0.01, "realized": 0.0, "tp_stage": 0,
      "peak_pct": 0.0, "stop_hits": 0, "rt": 2.0, "src": "formula", "dec": None, "tag": "score", "signals": [],
      "dex": "pumpswap", "realized_cpmm": 0.0, "drained_cpmm": 0.0, "fe": {"x": 1}}
st.shadow_trades["S1"] = sh
assert D.shadow_decide(sh, now - 30) is None          # primo controllo sotto lo stop
dec = D.shadow_decide(sh, now)
assert dec and dec[0].startswith("stop loss"), dec
pnl = asyncio.run(D.shadow_apply(st, None, "S1", sh, dec, now))
row = list(csv.DictReader(open(D.SHADOW_CSV)))[-1]
assert row["stop_level"] == "-10.0" and float(row["exit_lag_s"]) == 30.0 and row["pnl_cpmm"] != "", row
assert abs(float(row["exit_fill"]) - pnl) < 0.01, (row["exit_fill"], pnl)
assert '"exit_gap_prev_s"' in row["entry_features"]

# ---- A5: posizione vera paper: TP parziale (cap) + chiusura a stop
class PB(D.PaperBroker):
    def __init__(self):
        self.realistic, self.http, self.mode = False, None, "paper"
    async def sell(self, st, mint, p, fraction, urgent=False):
        return self._sell_formula(st, p, fraction, urgent)
st2 = D.State()
st2.positions["M"] = {"symbol": "X", "tokens": 1000.0, "tokens_entry": 1000.0, "cost": 10.0, "realized": 0.0,
                      "opened": time.time() - 300, "peak_pct": 60.0, "last_price": 0.016, "liq": 40000.0,
                      "liq_entry": 40000.0, "entry_mark": 0.01, "dex": "pumpfun", "creator": "",
                      "_cpmm_cap": D.tp_cap_price(0.016, 58.0, 30.0)}
asyncio.run(D._sell_position(st2, PB(), "M", "presa di profitto 1/2 a +58%", fraction=0.3, mark_tp=False))
pm = st2.positions["M"]
assert pm["realized_cpmm"] < pm["realized"] and "_cpmm_cap" not in pm
pm.update(last_price=0.0082, eff={"stop": -10.0, "crash": -25.0}, stop_t0=time.time() - 20, stop_t0_gap=15.0)
asyncio.run(D._sell_position(st2, PB(), "M", "stop loss a -18.0%"))
t = st2.closed[-1]
assert t["stop_level"] == -10.0 and t["exit_fill"] < -10 and 19 <= t["exit_lag_s"] <= 25 and t["pnl_cpmm"] < t["pnl_pct"], t
tr = list(csv.DictReader(open(os.path.join(D.DATA_DIR, "trades.csv"))))
assert tr[-1]["pnl_cpmm"] != "" and tr[-1]["exit_lag_s"] != ""

# ---- B: pausabile con authority = rigido; flag Token-2022
mk = lambda *ex: {"info": {"extensions": list(ex), "decimals": 6}}
h, s, i = HP.mint_flags(mk({"extension": "pausableConfig", "state": {"authority": "AUTH", "paused": False}}))
assert any("pausabile" in x for x in h) and "pausable" in i["t22_flags"]
h, s, i = HP.mint_flags(mk({"extension": "pausableConfig", "state": {"authority": None, "paused": False}}))
assert not h
h, s, i = HP.mint_flags(mk({"extension": "permanentDelegate", "state": {"delegate": "D"}},
                           {"extension": "transferHook", "state": {"programId": "P", "authority": "A"}},
                           {"extension": "defaultAccountState", "state": {"accountState": "frozen"}}))
assert len(h) == 3 and set(i["t22_flags"]) == {"permanent_delegate", "transfer_hook", "default_frozen"}
h, s, i = HP.mint_flags(mk({"extension": "transferHook", "state": {"programId": None, "authority": "A"}}))
# review Claude (compito 7, da decidere da Kiwi): hook spento ma attivabile dal dev = rigido (prima: solo registrato)
assert any("attivabile dal dev" in x for x in h) and i["t22_flags"] == ["transfer_hook_auth"]

# ---- C: seconda conferma, solo registrata
D.CFG["ENTRY_CONFIRM_SEC"] = 0
async def fake_batch(http, mints):
    return {mints[0]: "X"}
D.dex_batch = fake_batch
D.summarize = lambda _x: {"price": 0.0095, "liq": 30000.0, "buys_m5": 10, "sells_m5": 20, "chg_m5": -3.0,
                          "symbol": "C"}
fe = {}
asyncio.run(D.confirm2_task(st, object(), "MC", {"price": 0.01, "liq": 40000.0, "symbol": "C"}, fe, "real"))
c2 = fe["confirm2"]
assert c2["ok"] is False and "liq 75%" in c2["why"] and "acq/vend" in c2["why"] and c2["dp"] == -5.0, c2
D.summarize = lambda _x: {"price": 0.0105, "liq": 40000.0, "buys_m5": 30, "sells_m5": 20, "chg_m5": 5.0,
                          "symbol": "C"}
fe = {}
asyncio.run(D.confirm2_task(st, object(), "MC", {"price": 0.01, "liq": 40000.0, "symbol": "C"}, fe, "shadow"))
assert fe["confirm2"]["ok"] is True, fe
print("test_v86: OK")
