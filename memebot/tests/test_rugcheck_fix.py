"""Compito 20: ripiego RugCheck giu piu completo (GoPlus Solana per lo stato LP, indipendente da RugCheck)
e client Http con backoff/retry su 429 prima di contare un fallimento. Offline, su copia in /tmp.
Fixture sintetiche (indirizzi finti). Le 6 lezioni negative non compaiono qui (nessun esempio reale)."""
import asyncio, base64, time
import desk as D
import sources.goplus as goplus

assert D.DATA_DIR.startswith("/tmp"), "test solo su copia in /tmp"
D.tg_send = lambda *_a, **_k: None
D.CFG["SHADOW_TRADES"] = False
D.CFG["GOPLUS_CHECK"] = True
D.SRC["goplus"] = goplus

# ---- 1) lp_pct_from_dex: puro, nessuna rete
assert goplus.lp_pct_from_dex(None) is None
assert goplus.lp_pct_from_dex([]) is None
assert goplus.lp_pct_from_dex([{"burn_percent": None}, {"burn_percent": None}]) is None   # dato assente, mai 0
assert goplus.lp_pct_from_dex([{"burn_percent": 0}, {"burn_percent": 97.5}]) == 97.5       # il massimo noto

# ---- 2) security_check con RugCheck giu e dex Raydium (il gap: rc_fallback non sa leggere la LP Raydium
# via byte-parsing; prima del compito 20 restava sempre "unknown" qui, ora GoPlus puo risolverlo)
MINT = "A" * 40 + "pump"
PAIR = "P" * 43


async def fake_rpc(_http, method, params):
    if method == "getAccountInfo" and params[0] == MINT:
        return {"value": {"owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "data": {"parsed": {"info": {
            "mintAuthority": None, "freezeAuthority": None, "supply": "1000000000000000", "decimals": 6}}}}}
    if method == "getTokenLargestAccounts":
        return {"value": [{"address": "V" * 43, "uiAmount": 200e6}]
                + [{"address": f"T{i}" + "x" * 41, "uiAmount": 15e6 - i * 7e5} for i in range(15)]}
    if method == "getMultipleAccounts" and params[1].get("encoding") == "jsonParsed":
        return {"value": [{"data": {"parsed": {"info": {"owner": PAIR if a.startswith("V") else "W" + a[1:]}}}}
                          for a in params[0]]}
    if method == "getMultipleAccounts":
        return {"value": [{"owner": D.SYSTEM_PROGRAM} for _ in params[0]]}
    return None   # niente pool pumpswap da leggere: dex e' raydium, non pumpswap


D._rpc_core = fake_rpc
M = {"symbol": "TST", "dex": "raydium", "pair": PAIR, "pairs_all": [PAIR], "age_min": 600, "mcap": 4e5,
     "liq": 8e4, "liq_max": 8e4, "price": 1e-4}
GP = {"burn": None}   # None = GoPlus non ha un burn_percent (non disponibile/non trovato)


class FakeGoplusResp:
    """goplus.check() usa la sessione aiohttp grezza: get() sincrono -> context manager async."""
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    status = 200

    async def json(self, content_type=None):
        dex = [{"dex_name": "raydium", "burn_percent": GP["burn"]}] if GP["burn"] is not None else []
        return {"code": 1, "message": "ok", "result": {MINT: {"dex": dex, "mintable": {"status": "0"},
                "freezable": {"status": "0"}, "balance_mutable_authority": {"status": "0"},
                "closable": {"status": "0"}, "transfer_fee": {}, "default_account_state": "1"}}}


class FakeRawSession:
    """sessione aiohttp 'grezza', solo per goplus.check() (getattr(http, 's', http))."""
    def get(self, url, **_k):
        return FakeGoplusResp()


class FakeHttp:
    s = FakeRawSession()

    async def get(self, url, **_k):
        if url.endswith("/report"):
            return None   # RugCheck giu
        if url.endswith("/insiders/networks"):
            return [{"size": 4, "currentHolding": 10e12}]
        return None


goplus._cache.clear()
st = D.State()
# senza alcuna fonte per la LP (GoPlus non trovata) -> resta un dubbio, come prima del compito 20
ok, why, cat, sec = asyncio.run(D.security_check(st, FakeHttp(), MINT, M, {}))
assert not ok and sec["severe"] == "rugcheck_down" and "unknown" in why, (why, sec)

# GoPlus conferma LP bruciata per quella pool -> passa (ok, lp_status 'ok'), anche se RugCheck e' giu
goplus._cache.clear()
GP["burn"] = 97.0
st = D.State()
ok, why, cat, sec = asyncio.run(D.security_check(st, FakeHttp(), MINT, M, {}))
assert ok and sec["rc_down"] and sec["lp_status"] == "ok", (why, sec)

# GoPlus dice LP per niente bruciata (0%) -> resta bloccato comunque (mai un acquisto vero, ne' col ripiego
# generico 'rugcheck_down' ne' con lp_block se arriva fin li'): 'zero' non e' mai tra gli stati che passano.
GP["burn"] = 0.0
goplus._cache.clear()
st = D.State()
ok, why, cat, sec = asyncio.run(D.security_check(st, FakeHttp(), MINT, M, {}))
assert not ok and sec["lp_status"] == "zero", (why, sec)

# ---- 3) Http.get(): un 429 isolato non e' un fallimento se la riprova va a buon fine (backoff breve nel test)
D.HOST_GAPS["unit.test"] = 0.0


class FakeResp:
    def __init__(self, status, payload=None):
        self.status, self._payload, self.headers = status, payload, {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self, content_type=None):
        return self._payload


class FakeSession:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = 0

    def get(self, url, headers=None):
        self.calls += 1
        st = self.statuses.pop(0)
        return FakeResp(st, {"ok": True} if st == 200 else None)


sleeps = []
_real_sleep = asyncio.sleep
asyncio.sleep = lambda s: (sleeps.append(s), _real_sleep(0))[1]
try:
    sess = FakeSession([429, 200])
    h = D.Http(sess)
    out = asyncio.run(h.get("http://unit.test/x"))
    assert out == {"ok": True} and sess.calls == 2, (out, sess.calls)   # riprovato una volta, poi andato a buon fine

    sess2 = FakeSession([429, 429, 429])
    h2 = D.Http(sess2)
    out2 = asyncio.run(h2.get("http://unit.test/y", retries=2))
    assert out2 is None and sess2.calls == 3   # tre tentativi (il default), poi si arrende
finally:
    asyncio.sleep = _real_sleep

print("test_rugcheck_fix: ok")
