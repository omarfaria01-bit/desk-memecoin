"""Compito 21: scarto solo per eta = rimandare (watchlist prio + ricontrollo completo) e shadow age_only.
Offline, solo su copia in /tmp."""
import asyncio, time
import desk as D

assert D.DATA_DIR.startswith("/tmp"), "test solo su copia in /tmp"
D.tg_send = lambda *_a, **_k: None
EV, SH = [], []
D.write_event = lambda agent, kind, msg, data=None: EV.append((kind, msg, data or {}))
D.spawn_shadow = lambda st, http, mint, m, keys, reason, score, flags, tag=None, signals=None, **k: SH.append((mint, tag, signals))
st = D.State()
now = 1_000_000.0
m = {"symbol": "DOUGH", "age_min": 1.4, "liq": 50_000.0, "vol_h1": 300_000.0, "chg_m5": 725.0, "src": "pump.fun"}
st.processed["D1"] = now
assert D.age_requeue(st, None, "D1", m, {}, now)
w = st.watch["D1"]
min_age = D.pf_cfg(st)["PF_MIN_AGE_MIN"]
assert w["prio"] and w["age_recheck"] == 1 and "D1" not in st.processed
assert abs(w["not_before"] - (now + (min_age - 1.4) * 60 + D.CFG["AGE_RECHECK_PAD_SEC"])) < 1e-6
assert SH[-1][1] == "age_only" and "age_vol:vol100-500k" in SH[-1][2] and "age_m5:m5>100" in SH[-1][2]
assert EV[-1][0] == "age_requeue"
# al massimo AGE_RECHECK_MAX ricontrolli: poi solo shadow, niente rientro
w2 = {"age_recheck": D.CFG["AGE_RECHECK_MAX"]}
st.watch.pop("D1")
assert not D.age_requeue(st, None, "D1", m, w2, now) and "D1" not in st.watch
assert "age_only" in D.MEASURE_ONLY_KEYS

# shadow age_only: PnL a 5/15/60 min dai prezzi gia letti
sh = {"tag": "age_only", "price": 1.0, "t": now}
for dt, px in ((60, 1.1), (301, 1.5), (400, 2.0), (901, 0.8), (3601, 0.5)):
    D.age_ckpt_tick(sh, px, now + dt)
assert sh["ckpt"] == {"5": 50.0, "15": -20.0, "60": -50.0}, sh["ckpt"]
sh2 = {"tag": "altro", "price": 1.0, "t": now}
D.age_ckpt_tick(sh2, 2.0, now + 4000)
assert "ckpt" not in sh2

# ciclo del Filter: il token in attesa non viene ricontrollato prima dell'eta minima, poi si
CALLS = {"n": 0}
_sleep = asyncio.sleep


async def one_round(_s):
    CALLS["n"] += 1
    if CALLS["n"] > 1:
        raise asyncio.CancelledError
    await _sleep(0)


async def fake_batch(_http, mints):
    return {k: [{"priceUsd": "1", "liquidity": {"usd": 50_000}, "volume": {"h1": 300_000}, "pairAddress": "p",
                 "baseToken": {"symbol": "DOUGH"}, "pairCreatedAt": (time.time() - 600) * 1000}] for k in mints}


async def deny_security(*a, **k):
    return False, "test", "test", None
D.dex_batch, D.security_check = fake_batch, deny_security
D.asyncio.sleep = one_round
D.CFG["PF_ENABLED"] = False


def run_filter():
    CALLS["n"] = 0
    try:
        asyncio.run(D.filter_agent(st, None, asyncio.Queue()))
    except asyncio.CancelledError:
        pass


st.watch["D2"] = {"seen": time.time(), "sym": "DOUGH", "src": "age_recheck", "prio": True,
                  "not_before": time.time() + 600, "age_recheck": 1}
run_filter()
assert "D2" in st.watch and "D2" not in st.processed                       # ancora in attesa
st.watch["D2"]["not_before"] = time.time() - 1
EV.clear()
run_filter()
D.asyncio.sleep = _sleep
assert "D2" not in st.watch and "D2" in st.processed                        # rivalutato (e passato alla sicurezza)
assert any(k == "age_recheck" for k, _, _ in EV), EV
print("test_age_recheck OK")
