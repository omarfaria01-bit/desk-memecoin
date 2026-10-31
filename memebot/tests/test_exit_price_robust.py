"""Compito 4: fonte prezzo in errore / None / 0 -> 'attesa' o 'dati non validi', mai -100/rug.
Un rug vero (nessun prezzo + nessun percorso di vendita) resta rug. Offline, solo su copia in /tmp."""
import asyncio, time
import desk as D

assert D.DATA_DIR.startswith("/tmp"), "test solo su copia in /tmp"
D.tg_send = lambda *_a, **_k: None


class H:   # http finto: basta che abbia .s (jup_quote e sostituito sotto)
    s = object()

    async def get(self, *_a, **_k):
        return None


Q = {"res": (None, "rete (Timeout)")}
PX = {"res": {}}


async def fake_quote(_h, _i, _o, _a, *_x, **_k):
    return Q["res"]


async def fake_second(_h, mints):
    return PX["res"]


D.jup_quote, D.second_price = fake_quote, fake_second
st = D.State()
now = time.time()


def item():
    return {"sym": "X", "tokens": 1000.0, "dec": 6, "last_ok": now - 1000, "last_price": 0.01}


def verdict(px, q, o=None):
    PX["res"], Q["res"] = px, q
    o = o or item()
    return asyncio.run(D.verify_no_price(st, H(), {"M": o}, now))["M"], o


# Jupiter price risponde senza il token (token piccolo/nuovo): NON basta piu per 'dead'
v, o = verdict({}, ({"outAmount": "123"}, None))          # percorso di vendita presente: token vivo
assert v == "wait" and o["last_ok"] > now - 900 and o["np_route"] == "route", (v, o)
v, o = verdict({}, (None, "rete (Timeout)"))               # preventivo in errore: attesa
assert v == "wait" and o["np_route"] == "unknown", v
v, o = verdict({}, (None, "http 429"))
assert v == "wait", v
o = item(); o["dec"] = None                                # decimali ignoti e RPC giu: attesa, mai dead
D.token_decimals = lambda *_a, **_k: asyncio.sleep(0, result=None)
v, o = verdict({}, (None, "no_route"), o)
assert v == "wait", v
# rug vero: nessun prezzo E nessun percorso di vendita -> dead
v, o = verdict({}, (None, "no_route"))
assert v == "dead" and o["np_src"] == "jupiter", v
# seconda fonte giu (None): attesa, poi dati non validi (mai rug)
o = item()
for i in range(int(D.CFG.get("NOPRICE_MAX_WAIT", 15)) - 1):
    o["last_ok"] = now - 1000
    assert verdict(None, (None, "no_route"), o)[0] == "wait"
o["last_ok"] = now - 1000
assert verdict(None, (None, "no_route"), o)[0] == "invalid"
# prezzo dalla seconda fonte: vivo
assert verdict({"M": 0.02}, (None, "no_route"))[0] == "alive"


# ---- shadow: dati non validi = non rug; chiusura con un solo no_route = resta aperto, due = rug
def shadow(**kw):
    sh = {"t": now - 3600, "sym": "X", "reason": "r", "keys": [], "flags": [], "score": 0.5, "thr": 0.5,
          "price": 0.01, "liq_entry": 5e4, "liq": 5e4, "rt": 2.0, "src": "jupiter", "peak_pct": 0.0, "tag": "",
          "signals": [], "cost": 10.0, "tokens": 1000.0, "tokens_entry": 1000.0, "realized": 0.0,
          "last_price": 0.0095, "last_ok": now, "stop_hits": 1, "dec": 6, "entry_mark": 0.01, "tp_stage": 0,
          "max_hold": None, "links": {}, "url": ""}
    sh.update(kw)
    return sh


sh = shadow(last_ok=now - 1000, np_invalid=True)
why, frac, zero, rug = D.shadow_decide(sh, now)
assert not rug and not zero and why.startswith("DATI NON VALIDI"), why
D.CFG["SHADOW_ROUTE_CHECK"] = True
Q["res"] = (None, "no_route")
sh = shadow()
st.shadow_trades["S1"] = sh
dec = ("stop loss a -5.0%", 1.0, False, False)
assert asyncio.run(D.shadow_apply(st, H(), "S1", sh, dec, now)) is None and "S1" in st.shadow_trades   # 1 no_route
n_conf = max(2, int(D.CFG.get("MARK_NOROUTE_CONFIRM", 3)))
for _ in range(n_conf - 2):
    assert asyncio.run(D.shadow_apply(st, H(), "S1", sh, dec, now)) is None
pnl = asyncio.run(D.shadow_apply(st, H(), "S1", sh, dec, now))   # confermato -> rug
assert pnl is not None and pnl <= -99 and "S1" not in st.shadow_trades, pnl
# preventivo valido al secondo giro: chiusura normale al prezzo, non rug
Q["res"] = (None, "no_route")
sh = shadow(); st.shadow_trades["S2"] = sh
assert asyncio.run(D.shadow_apply(st, H(), "S2", sh, dec, now)) is None
Q["res"] = ({"outAmount": "95000000"}, None)
pnl = asyncio.run(D.shadow_apply(st, H(), "S2", sh, dec, now))
assert pnl is not None and pnl > -50, pnl

# ---- prefiltro: prezzo mancante a 60 min = nessun esito (riprova), mai -100
if "pf" in D.SRC:
    D.CFG["PF_LEARN"] = False
    rec = {"mint": "PF1", "t": now - 4000, "fails": [], "m": {"price": 0.01}}
    D.SRC["pf"].load_pending = lambda done=None, **_k: [rec] if (rec["mint"], rec["t"]) not in (done or set()) else []
    D.dex_batch = lambda *_a, **_k: asyncio.sleep(0, result={})
    st2, done = D.State(), set()
    asyncio.run(D.pf_learn(st2, H(), done))
    assert not done and not st2.learner_bonus.get("pf_hist"), (done, st2.learner_bonus.get("pf_hist"))
    D.dex_batch = lambda *_a, **_k: asyncio.sleep(0, result={"PF1": [{"priceUsd": "0.012", "liquidity": {"usd": 1e4},
                                                                       "pairAddress": "p", "baseToken": {}}]})
    asyncio.run(D.pf_learn(st2, H(), done))
    assert done and abs(st2.learner_bonus["pf_hist"][-1][1] - 20.0) < 0.01, st2.learner_bonus["pf_hist"]
else:
    print("prefiltro non caricato: parte pf_learn saltata")
print("test_exit_price_robust OK")
