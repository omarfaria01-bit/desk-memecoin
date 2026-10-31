"""Compito 6: uscite veloci in SHADOW. Replay di trade finti, iscrizioni, polling di riserva, websocket che cade.
Offline, solo su copia in /tmp."""
import asyncio, json, time
import desk as D

assert D.DATA_DIR.startswith("/tmp"), "test solo su copia in /tmp"
D.tg_send = lambda *_a, **_k: None
D.SOL_USD[0], D.SOL_USD[1] = 100.0, time.time()
D.CFG.update({"FAST_EXIT_MODE": "shadow", "FAST_EXIT_CONFIRM_SEC": 3.0, "STOP_LOSS_PCT": -12.0, "TP1_PCT": 30.0,
              "TRAIL_ACTIVATE_PCT": 40.0, "TRAIL_DISTANCE_PCT": 15.0, "PRICE_SPIKE_MULT": 20.0})
st = D.State()
MINT = "F" * 40 + "pump"


def shadow(price=1.0):
    return {"price": price, "tp_stage": 0, "peak_pct": 0.0}


# ---- replay: dump con un picco finto in mezzo; stop confermato da 2 prezzi ad almeno 3 s
sh = shadow()
replay = [(0.0, 1.00), (1.0, 0.95), (2.0, 0.87), (3.5, 0.86), (4.0, 30.0),   # 30.0 = salto x35: ignorato
          (5.2, 0.80), (40.0, 0.70)]
for t, px in replay:
    D.fast_tick(sh, px, 1000 + t)
fx = sh["fast_exit"]
assert fx["why"] == "stop" and fx["ts"] == 1005.2 and fx["pnl"] == -20.0, fx   # 0,87 a t=2, conferma >= 3 s dopo
# un solo prezzo sotto lo stop seguito da un rimbalzo non conferma
sh2 = shadow()
for t, px in [(0, 1.0), (1, 0.87), (2, 0.95), (3.5, 0.87), (5.0, 0.95)]:
    D.fast_tick(sh2, px, t)
assert sh2.get("fast_exit") is None
# trailing: picco +60%, poi -15 punti confermati
sh3 = shadow()
for t, px in [(0, 1.0), (10, 1.6), (20, 1.44), (24, 1.43)]:
    D.fast_tick(sh3, px, t)
assert sh3["fast_exit"]["why"] == "trailing" and sh3["fast_tp1"]["pnl"] == 44.0, sh3   # TP1 al 2o prezzo (conferma)
# dopo il TP1 (tp_stage 1) lo stop sale a pareggio
sh4 = dict(shadow(), tp_stage=1)
assert D.fast_levels(sh4)[0] == min(D.CFG["BREAKEVEN_PCT"], 29)
# posizione vera: livello dallo stop effettivo (eff) se c'e
p = {"quote_price": 1.0, "eff": {"stop_base": -8.0, "tp1": 30.0, "trail_start": 40.0, "trail_dist": 15.0}}
for t, px in [(0, 0.91), (4, 0.91)]:
    D.fast_tick(p, px, t)
assert p["fast_exit"]["level"] == -8.0
# modo off: niente
D.CFG["FAST_EXIT_MODE"] = "off"
sh5 = shadow()
for t, px in [(0, 0.5), (5, 0.5)]:
    D.fast_tick(sh5, px, t)
assert "fast_exit" not in sh5
D.CFG["FAST_EXIT_MODE"] = "shadow"

# ---- campi alla chiusura: uscita vera 30 s dopo a -25% -> veloce 30 s prima e 5 punti meglio
f = D.fast_exit_fields(sh, close_ts=1035.2, close_pnl=-25.0)
assert f["fast_exit_lag_s"] == 30.0 and f["fast_exit_gain_pct"] == 5.0 and f["fast_exit_fill"] == -20.0, f
assert D.fast_exit_fields(shadow(), 1, -5) == {"fast_ticks_mode": "shadow"}

# ---- websocket: iscrizioni ai trade solo per i mint pump.fun seguiti; trade -> prezzo
st.shadow_trades = {MINT: shadow(price=0.001), "OTHER" + "x" * 39: shadow()}
add, rem = D.fast_subs_diff(st, set())
assert add == [MINT] and rem == []


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send(self, m):
        self.sent.append(json.loads(m))


ws = FakeWS()
D.FAST.update({"subs": set(), "last_sync": 0.0, "ws_ok": True})
asyncio.run(D.fast_ws_sync(ws, st, now=100.0))
assert ws.sent == [{"method": "subscribeTokenTrade", "keys": [MINT]}] and D.FAST["subs"] == {MINT}
msg = {"txType": "sell", "mint": MINT, "solAmount": 0.5, "tokenAmount": 50000.0}   # 0,5/50000 x 100 $ = 0,001
assert D.fast_ws_msg(st, msg, now=200.0) is True
assert st.shadow_trades[MINT]["_fx"]["last"] == 0.001 and D.FAST["ws_last"][MINT] == 200.0
assert D.fast_ws_msg(st, {"txType": "create", "mint": "NEW"}) is False      # token nuovo: va allo scouting
# chiusura: disiscrizione al giro dopo
st.shadow_trades.pop(MINT)
asyncio.run(D.fast_ws_sync(ws, st, now=106.0))
assert ws.sent[-1] == {"method": "unsubscribeTokenTrade", "keys": [MINT]} and D.FAST["subs"] == set()

# ---- polling di riserva: non pump.fun sempre; pump.fun se il websocket e giu o fermo
st.shadow_trades = {MINT: shadow(), "OTHER" + "x" * 39: shadow()}
D.FAST.update({"subs": {MINT}, "ws_ok": True, "ws_last": {MINT: 1000.0}})
assert D.fast_poll_targets(st, now=1010.0) == ["OTHER" + "x" * 39]
assert MINT in D.fast_poll_targets(st, now=1000.0 + D.CFG["FAST_WS_STALE_SEC"] + 1)   # websocket fermo
D.FAST["ws_ok"] = False
assert MINT in D.fast_poll_targets(st, now=1010.0)                                     # websocket caduto


# ---- il loop pump.fun non crolla se il websocket cade: riconnessione con attesa
class Boom:
    def __init__(self, *a, **k):
        raise ConnectionError("giu")


calls = {"n": 0}
_conn, _sleep = D.websockets.connect, D.asyncio.sleep


def fake_connect(*a, **k):
    calls["n"] += 1
    raise ConnectionError("giu")


async def short_sleep(_s):
    if calls["n"] >= 3:
        raise asyncio.CancelledError
    await _sleep(0)
D.websockets.connect, D.asyncio.sleep = fake_connect, short_sleep
try:
    asyncio.run(D.scout_pumpportal(st))
except asyncio.CancelledError:
    pass
D.websockets.connect, D.asyncio.sleep = _conn, _sleep
assert calls["n"] >= 3 and D.FAST["ws_ok"] is False and D.FAST["subs"] == set()


# ---- polling: una lettura DexScreener alimenta le uscite veloci
async def fake_batch(_http, mints):
    return {m: [{"priceUsd": "0.5", "liquidity": {"usd": 1e4}, "pairAddress": "p", "baseToken": {}}] for m in mints}
D.dex_batch = fake_batch
st.shadow_trades = {"OTHER" + "x" * 39: shadow()}
D.CFG["FAST_POLL_SEC"] = 0.0


async def one_poll():
    t = asyncio.ensure_future(D.fast_poll_agent(st, None))
    await _sleep(0.05)
    t.cancel()
asyncio.run(one_poll())
assert st.shadow_trades["OTHER" + "x" * 39]["_fx"]["last"] == 0.5
print("test_fast_exit OK")
