"""v8.5: errori trattati come 'sconosciuto' (mai 'ok', mai rug) e percorso live con tx incerte. Offline, nessun invio."""
import asyncio, json, os, tempfile, time
import desk as D
from sources import cluster_v2 as C

D.SOL_USD[0], D.SOL_USD[1] = 100.0, time.time()
TMP = tempfile.mkdtemp()
D.PENDING_TX_PATH = os.path.join(TMP, "live_pending_tx.json")
D.CFG["LIVE_CONFIRM_TIMEOUT_SEC"] = 1
_sleep = asyncio.sleep
D.asyncio.sleep = lambda *_a, **_k: _sleep(0)   # niente attese reali nei test
D.tg_send = lambda *_a, **_k: None
D.log = lambda *_a, **_k: None

# ---- 1. summarize: campo liquidita assente != liquidita 0
pair = {"priceUsd": "0.01", "baseToken": {"symbol": "X"}, "volume": {"h1": 1000}}
assert D.summarize([pair])["liq_known"] is False
assert D.summarize([dict(pair, liquidity={"usd": 0})])["liq_known"] is True


# ---- 2. refresh_mark: un solo no_route non azzera piu la posizione
class PB(D.PaperBroker):
    def __init__(self):
        self.realistic, self.http = True, None


D.CFG["SIM_USE_QUOTES"] = True
seq = []


async def fake_quote(_h, _i, _o, _a, *_x, **_k):
    return seq.pop(0)
D.jup_quote = fake_quote
b = PB()
p = {"tokens": 1000.0, "last_price": 0.01, "dec": 6, "qmark": {}}
for i in range(2):
    seq.append((None, "no_route"))
    p["qmark"] = {}   # forza un nuovo preventivo
    asyncio.run(b.refresh_mark("M", p))
    assert not p["qmark"].get("no_route"), (i, p)
seq.append((None, "no_route"))
asyncio.run(b.refresh_mark("M", p))
assert p["qmark"].get("no_route") and p["mark_noroute"] == 3, p   # 3 di fila: invendibile
seq.append(({"outAmount": str(int(0.1 * 1e9))}, None))
p["qmark"] = {}
asyncio.run(b.refresh_mark("M", p))
assert p["mark_noroute"] == 0, p


# ---- 3. cluster_v2: controlli dei wallet falliti = 'unknown', non 'pulito'
async def fake_top(*_a, **_k):
    return [(f"O{i}", 0.5, f"A{i}") for i in range(60)], 60, None


async def fake_acq(*_a, **_k):
    return {"slot": 1, "ts": 1, "payer": None, "from": None, "sig": "s"}


async def post_429(_u, body):
    return None if body["method"] == "getSignaturesForAddress" else {"result": None}
C.top_holders, C._acquisition = fake_top, fake_acq
r = asyncio.run(C.scan(post_429, "MINT", rpc_urls=["u"], cfg={"CALL_GAP": 0}))
assert r["level"] == "unknown" and "wallet controllati" in r["why"], r


# ---- 4. SolanaBroker live: RPC finta
class FakeBroker(D.SolanaBroker):
    def __init__(self, script):
        super().__init__(None, "live")
        self.pub, self.script, self.sent = "PUB", script, 0

    async def rpc(self, method, params):
        if method == "sendTransaction":
            self.sent += 1
        f = self.script.get(method)
        return f(params) if callable(f) else (f if isinstance(f, tuple) else (None, D.RPC_NO_RESPONSE))

    @staticmethod
    def tx_sig(_b64):
        return "SIG123456789"


# token_raw: errore RPC = None, mai 0
fb = FakeBroker({"getTokenAccountsByOwner": (None, "429 Too Many Requests")})
assert asyncio.run(fb.token_raw("M")) is None
fb = FakeBroker({"getTokenAccountsByOwner": ({"value": []}, None)})
assert asyncio.run(fb.token_raw("M")) == 0

# vendita con saldo non verificabile: NON chiude la posizione a -100%
fb = FakeBroker({"getTokenAccountsByOwner": (None, "429")})
fb._solp = (time.time(), 100.0)
res = asyncio.run(fb.sell(D.State(), "M", {"tokens": 5.0, "dec": 6, "symbol": "X"}, 1.0))
assert res["ok"] is False and "non verificabile" in res["error"], res

# send: confermata
fb = FakeBroker({"sendTransaction": ("SIG123456789", None),
                 "getSignatureStatuses": ({"value": [{"confirmationStatus": "confirmed", "err": None}]}, None)})
assert asyncio.run(fb.send("tx", {"side": "buy", "mint": "M"})) == (True, "SIG123456789")
assert not os.path.exists(D.PENDING_TX_PATH)
# send: blockhash scaduto senza traccia = sicuramente fallita, nessun blocco
fb = FakeBroker({"sendTransaction": ("SIG123456789", None), "getSignatureStatuses": ({"value": [None]}, None),
                 "getBlockHeight": (999, None)})
fb.last_valid = 900
ok, why = asyncio.run(fb.send("tx", {"side": "buy", "mint": "M"}))
assert ok is False and not os.path.exists(D.PENDING_TX_PATH), why
# send: rifiutata dal preflight = fallita, nessun blocco
fb = FakeBroker({"sendTransaction": (None, "Transaction simulation failed")})
ok, why = asyncio.run(fb.send("tx", {"side": "buy", "mint": "M"}))
assert ok is False and not os.path.exists(D.PENDING_TX_PATH), why
# send: RPC muta = esito SCONOSCIUTO, firma salvata, acquisti bloccati
fb = FakeBroker({})   # nessuna risposta a nulla (timeout)
fb.last_valid = None
ok, why = asyncio.run(fb.send("tx", {"side": "buy", "mint": "M"}))
assert ok is None and fb.sent >= 2, (ok, why, fb.sent)   # rinviata (stessa firma)
pend = json.load(open(D.PENDING_TX_PATH))
assert pend["SIG123456789"]["status"] == "unknown" and pend["SIG123456789"]["mint"] == "M", pend
# la tx confermata dopo il timeout resta da riconciliare; quella scaduta viene tolta
fb = FakeBroker({"getSignatureStatuses": ({"value": [{"confirmationStatus": "finalized", "err": None}]}, None)})
out = asyncio.run(fb.resolve_pending(D.State()))
assert out == {"SIG123456789": "ok"} and json.load(open(D.PENDING_TX_PATH))["SIG123456789"]["status"] == \
    "confirmed_untracked"
fb = FakeBroker({"getSignatureStatuses": ({"value": [None]}, None), "getBlockHeight": (999, None)})
D.pending_tx_save({"S2": {"mint": "M", "side": "buy", "last_valid": 900}})
asyncio.run(fb.resolve_pending(D.State()))
assert not os.path.exists(D.PENDING_TX_PATH)
# file illeggibile = mai 'nessuna tx in attesa'
open(D.PENDING_TX_PATH, "w").write("{rotto")
assert D.pending_tx_load()
os.remove(D.PENDING_TX_PATH)

# vendita parziale arrivata dopo il timeout: registrata, non ripetuta
D.pending_tx_save({"S3": {"mint": "M", "side": "sell", "tokens": 1.5, "quote_sol": 0.02}})
fb = FakeBroker({"getSignatureStatuses": ({"value": [{"confirmationStatus": "confirmed", "err": None}]}, None)})
fb._solp = (time.time(), 100.0)
res = asyncio.run(fb.sell(D.State(), "M", {"tokens": 5.0, "dec": 6, "symbol": "X"}, 0.3))
assert res["ok"] and res["tokens"] == 1.5 and abs(res["value"] - 2.0) < 1e-9 and fb.sent == 0, res
assert not os.path.exists(D.PENDING_TX_PATH)

# ---- 5. setup live: basi dei limiti dal saldo vero, non dai 1000 $ di partenza
fb = FakeBroker({"getBalance": ({"value": int(0.55 * 1e9)}, None), "getSignatureStatuses": ({"value": [None]}, None)})
fb._solp = (time.time(), 100.0)
try:
    from solders.keypair import Keypair
    D.ENV["PRIVATE_KEY"] = json.dumps(list(bytes(Keypair())))   # chiave usa e getta, mai un wallet vero
    st = D.State()
    D.CFG["SOL_RESERVE"] = 0.05
    asyncio.run(fb.setup(st))
    assert abs(st.cash - 50.0) < 1e-6 and st.total_start == st.day_start_equity == st.period_start_equity == st.cash, \
        (st.cash, st.total_start, st.day_start_equity)
except ImportError:
    print("solders assente: test setup saltato")
finally:
    D.ENV.pop("PRIVATE_KEY", None)

print("test_v85 OK")
