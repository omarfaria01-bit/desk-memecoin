"""Compito 7: test unitari del codice v8.6/v8.7 (Token-2022, pnl_cpmm, confirm2, smart_trades, p_prior). Offline,
solo su copia in /tmp."""
import asyncio, time
import desk as D
from sources import honeypot as HP
from sources import smart_trades as STX

assert D.DATA_DIR.startswith("/tmp"), "test solo su copia in /tmp"
D.tg_send = lambda *_a, **_k: None


# ---- Token-2022 (v8.6 B)
def flags(*exts):
    return HP.mint_flags({"info": {"mintAuthority": None, "freezeAuthority": None, "extensions": list(exts)}})


h, s, info = flags({"extension": "transferHook", "state": {"programId": "Hook111", "authority": None}})
assert any("transfer hook attivo" in x for x in h) and info["t22_flags"] == ["transfer_hook"]
h, s, info = flags({"extension": "transferHook", "state": {"programId": None, "authority": "Dev111"}})
assert any("attivabile dal dev" in x for x in h), (h, s)              # fix: prima era solo soft
assert info["t22_flags"] == ["transfer_hook_auth"]
h, s, info = flags({"extension": "transferHook", "state": {}})
assert not h and info["t22_flags"] == ["transfer_hook_off"]
h, s, _ = flags({"extension": "pausableConfig", "state": {"authority": "A", "paused": False}})
assert any("pausabile" in x for x in h)
h, s, _ = flags({"extension": "pausableConfig", "state": {}})
assert not h and not s
h, s, _ = flags({"extension": "transferFeeConfig", "state": {"newerTransferFee": {"transferFeeBasisPoints": 100},
                                                              "transferFeeConfigAuthority": "A"}})
assert any("puo essere alzata" in x for x in h) and any("fee di trasferimento 1.00%" in x for x in s)
h, s, _ = flags({"extension": "permanentDelegate", "state": {"delegate": "D"}}, {"extension": "nonTransferable"})
assert len(h) == 2

# ---- pnl_cpmm (v8.6 A)
v1, out1 = D.cpmm_exit(1000, 1.0, 20000)
v2, out2 = D.cpmm_exit(1000, 1.0, 20000, drained=out1)
assert 0 < v2 < v1 < 1000 and out2 < out1                               # pool gia drenata: si prende meno
assert D.cpmm_exit(0, 1.0, 1e4) == (0.0, 0.0)
assert abs(D.tp_cap_price(1.5, 50.0, 30.0) - 1.5 * 1.3 / 1.5) < 1e-12
assert D.tp_cap_price(1.0, 20.0, 30.0) is None and D.tp_cap_price(1.0, -99.5, 30.0) is None
o = {"realized": 5.0}                                                   # shadow aperto prima della v8.6
D.cpmm_track(o, 100, 1.0, 1e5, just_realized=5.0)                       # 'realized' include gia questa vendita
assert 0 < o["realized_cpmm"] < 100 and o["drained_cpmm"] > 0           # niente doppio conteggio dei 5
r0 = o["realized_cpmm"]
D.cpmm_track(o, 100, 1.0, 1e5, zero=True)
assert o["realized_cpmm"] == r0                                         # uscita a zero: nulla in piu
o2 = {"realized_cpmm": 0.0, "drained_cpmm": 0.0}
D.cpmm_track(o2, 100, 2.0, 1e5, cap_price=1.5)
assert abs(o2["realized_cpmm"] - D.cpmm_exit(100, 1.5, 1e5)[0]) < 1e-9 # limite del gradino applicato


# ---- _cpmm_cap non resta dopo una vendita fallita (fix)
class FailBroker:
    mode, realistic, http = "paper", False, None

    async def sell(self, *_a, **_k):
        return {"ok": False, "error": "finto"}


st = D.State()
st.positions["P1"] = {"symbol": "P", "tokens": 100.0, "cost": 10.0, "realized": 0.0, "last_price": 0.15, "liq": 5e4,
                      "_cpmm_cap": 0.13, "opened": time.time(), "entry_mark": 0.1}
asyncio.run(D._sell_position(st, FailBroker(), "P1", "presa di profitto 1/2 a +50%", fraction=0.3))
assert "_cpmm_cap" not in st.positions["P1"] and st.positions["P1"]["sell_fails"] == 1

# ---- smart_trades (v8.7 E)
def tr(ms, w, side, amt, tx=0):
    return {"blockTimeMs": ms, "txIndex": tx, "trader": {"address": w}, "side": side,
            "baseAmount": {"raw": str(int(amt * 1e6)), "decimals": 6}}


T0 = 1_700_000_000_000
trades = [tr(T0, "DEV", "buy", 50e6), tr(T0 + 500, "S1", "buy", 20e6), tr(T0 + 2900, "S2", "buy", 10e6),
          tr(T0 + 3100, "late", "buy", 5e6), tr(T0 + 60000, "S1", "sell", 10e6), tr(T0 + 90000, "DEV", "sell", 1e6)]
r = STX.compute(list(reversed(trades)), creator="DEV", created_ms=T0)  # ordine qualsiasi
assert r["sniper_pct"] == 3.0 and r["dev_first_sell_dt"] == 90.0 and r["creator_known"] == 1, r
assert r["first20_sold_pct"] == round(10e6 / 35e6 * 100, 2), r          # dev escluso dai primi 20
r_unk = STX.compute(trades, creator=None, created_ms=T0)
assert r_unk["sniper_pct"] == 8.0 and r_unk["creator_known"] == 0      # senza creatore il dev conta: segnalato
assert STX.compute([], "DEV")["n_trades"] == 0


async def run_fetch(pages):
    calls = []

    async def get(url):
        calls.append(url)
        return pages[len(calls) - 1] if len(calls) <= len(pages) else None
    return await STX.fetch(get, "MINT", "DEV", T0, {"MAX_PAGES": 3}), calls


full = [{"trades": [tr(T0 + i, f"w{i}", "buy", 1e6) for i in range(100)], "cursor": "c1"},
        {"trades": [tr(T0 + 1000 + i, f"x{i}", "buy", 1e6) for i in range(30)], "cursor": None}]
res, calls = asyncio.run(run_fetch(full))
assert res["ok"] and res["pages"] == 2 and res["n_trades"] == 130 and "cursor=c1" in calls[1]
big = [{"trades": [tr(T0, "w", "buy", 1)] * 100, "cursor": "c"}] * 3
res, _ = asyncio.run(run_fetch(big))
assert res["ok"] is False and "incompleto" in res["why"]
res, _ = asyncio.run(run_fetch([None]))
assert res["ok"] is False and res["why"] == "risposta non valida"

# ---- p_prior (v8.7 F): solo ranking, mai eccezioni
_mp = dict(D.MODEL_PRIOR)
D.MODEL_PRIOR.clear()
assert D.prior_prob({"x": 1}) is None
D.MODEL_PRIOR.update({"w": {"x": 2.0, "y": -1.0}, "b": 0.0})
assert D.prior_prob({"x": 0, "y": 0}) == 0.5 and D.prior_prob({"x": 1e9}) == 1.0 and D.prior_prob({"x": "a"}) is None
D.PRIOR_LAST.clear()
for i in range(3005):
    D.prior_note(f"m{i}", {"x": 0})
assert len(D.PRIOR_LAST) <= 3000 and "m3004" in D.PRIOR_LAST
D.MODEL_PRIOR.clear(); D.MODEL_PRIOR.update(_mp)

# ---- confirm2 (v8.6 C): solo misura, errori = ok None
D.CFG["ENTRY_CONFIRM_SEC"] = 0
m = {"symbol": "C", "price": 1.0, "liq": 1e5, "chg_m5": 5.0}
_db = D.dex_batch


async def no_data(*_a, **_k):
    return {}
D.dex_batch = no_data
fe = {}
asyncio.run(D.confirm2_task(st, object(), "C1", m, fe, "shadow"))
assert fe["confirm2"]["ok"] is None


async def drained(*_a, **_k):
    return {"C1": [{"priceUsd": "0.5", "liquidity": {"usd": 2e4}, "pairAddress": "p", "baseToken": {},
                    "txns": {"m5": {"buys": 3, "sells": 9}}, "priceChange": {"m5": -40}}]}
D.dex_batch = drained
fe = {}
asyncio.run(D.confirm2_task(st, object(), "C1", m, fe, "shadow"))
assert fe["confirm2"]["ok"] is False and "liq" in fe["confirm2"]["why"] and "prezzo" in fe["confirm2"]["why"], fe
D.dex_batch = _db
print("test_review_v87 OK")

# ---- stx_task: mai piu di una alla volta (stesso host pump.fun dello storico creatore, che l'Analyst aspetta)
CONC = {"now": 0, "max": 0}


class FakeSTX:
    @staticmethod
    async def fetch(get, mint, creator, created, cfg):
        CONC["now"] += 1
        CONC["max"] = max(CONC["max"], CONC["now"])
        await asyncio.sleep(0.01)
        CONC["now"] -= 1
        return {"ok": True, "sniper_pct": 1.0}


class H:
    async def get(self, *_a, **_k):
        return None


_old_stx = D.SRC.get("stx")
D.SRC["stx"] = FakeSTX
D.STX_STATE["sem"] = None


async def three():
    fes = [{}, {}, {}]
    await asyncio.gather(*(D.stx_task(st, H(), f"S{i}", {"symbol": "S"}, fes[i], "C") for i in range(3)))
    return fes
fes = asyncio.run(three())
assert CONC["max"] == 1 and all(f.get("sniper_pct") == 1.0 for f in fes), (CONC, fes)
D.SRC["stx"] = _old_stx
print("stx una alla volta OK")
