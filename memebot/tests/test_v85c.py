"""v8.5c: ripiego quando RugCheck e giu, avvisi Telegram (max 1/ora), bundle soft solo per token affermati.
Da lanciare SOLO su una copia in /tmp."""
import asyncio, base64, time
import desk as D

assert D.DATA_DIR.startswith("/tmp"), "test solo su copia in /tmp"
sent = []
D.tg_send = lambda t: sent.append(t)
# 1) salute RugCheck: giu dopo 5 errori, un solo avviso, ritorno avvisato solo dopo un'ora
h = D.RC_HEALTH
for i in range(4):
    assert D.rc_health(False, now=10000.0 + i) is False
assert D.rc_health(False, now=10005.0) is True and len(sent) == 1 and "NON disponibile" in sent[0]
D.rc_health(True, now=10100.0); assert h["down"] is False and len(sent) == 1   # meno di un'ora: niente avviso
for i in range(5):
    D.rc_health(False, now=10500.0 + i)
D.rc_health(True, now=14000.0); D.rc_health(True, now=14700.0)   # passata l'ora: avviso di ritorno
assert len(sent) == 2 and "di nuovo disponibile" in sent[1], sent

# 2) ripiego on-chain finto
MINT = "A" * 40 + "pump"
PAIR = "P" * 43
LPM = "L" * 43
raw = bytearray(301)
raw[43:75] = bytes(32)
dec = lambda s: int.from_bytes(bytes(32), "big")
def b58dec(s):
    n = 0
    for ch in s:
        n = n * 58 + D._B58.index(ch)
    return n.to_bytes(32, "big")
raw[43:75] = b58dec(MINT); raw[107:139] = b58dec(LPM)
assert D._b58(bytes(raw[43:75])) == MINT
state = {"lp_supply": "0", "mint_auth": None}
async def fake_rpc(http, method, params):
    if method == "getAccountInfo" and params[0] == MINT:
        return {"value": {"owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "data": {"parsed": {"info": {
            "mintAuthority": state["mint_auth"], "freezeAuthority": None, "supply": "1000000000000000", "decimals": 6}}}}}
    if method == "getAccountInfo":
        return {"value": {"owner": D.PUMP_AMM, "data": [base64.b64encode(bytes(raw)).decode(), "base64"]}}
    if method == "getTokenSupply":
        return {"value": {"amount": state["lp_supply"]}}
    if method == "getTokenLargestAccounts":
        return {"value": [{"address": "V" * 43, "uiAmount": 200e6}] + [{"address": f"T{i}" + "x" * 41, "uiAmount": 15e6 - i * 7e5} for i in range(15)]}
    if method == "getMultipleAccounts" and params[1].get("encoding") == "jsonParsed":
        return {"value": [{"data": {"parsed": {"info": {"owner": PAIR if a.startswith("V") else "W" + a[1:]}}}} for a in params[0]]}
    if method == "getMultipleAccounts":
        return {"value": [{"owner": D.SYSTEM_PROGRAM} for _ in params[0]]}
D._rpc_core = fake_rpc
class FakeHttp:
    async def get(self, url, **kw):
        if url.endswith("/report"):
            return None   # RugCheck giu
        if url.endswith("/insiders/networks"):
            return [{"size": 4, "currentHolding": 50e12, "activityType": "transfer"}]
        return None
m = {"symbol": "TST", "dex": "pumpswap", "pair": PAIR, "pairs_all": [PAIR], "age_min": 300, "mcap": 4e5, "liq": 8e4,
     "liq_max": 8e4, "price": 1e-4}
st = D.State()
ok, why, cat, sec = asyncio.run(D.security_check(st, FakeHttp(), MINT, m, {}))
assert ok and sec["rc_down"] and sec["lp_status"] == "protocol", (why, sec and sec.get("lp_status"))
assert sec["top10"] < 30 and "ripiego" in why, why          # la pool (200M) e esclusa dal top 10
# LP non bruciata -> dubbio -> shadow rugcheck_down
state["lp_supply"] = "12345"
ok, why, cat, sec = asyncio.run(D.security_check(st, FakeHttp(), MINT, m, {}))
assert not ok and sec["severe"] == "rugcheck_down" and sec["shadow_keys"] == ["rugcheck_down"], why
# mint authority viva -> scarto duro
state["lp_supply"], state["mint_auth"] = "0", "Z" * 43
ok, why, cat, sec = asyncio.run(D.security_check(st, FakeHttp(), MINT, m, {}))
assert not ok, why
# token in curva (dex pumpfun) -> dubbio
state["mint_auth"] = None
ok, why, cat, sec = asyncio.run(D.security_check(st, FakeHttp(), MINT, dict(m, dex="pumpfun"), {}))
assert not ok and sec and sec.get("severe") == "rugcheck_down", why
assert D.shadow_tag(["rugcheck_down"], []) == "rugcheck_down"

# 3) bundle: hard per i token giovani, soft (bundle_established) solo per i token affermati
def rep(holders):
    return {"mintAuthority": None, "freezeAuthority": None, "token": {"supply": "1000000000000000", "decimals": 6},
            "markets": [{"marketType": "pump_amm", "pubkey": PAIR, "lp": {"lpLockedPct": 100.0}}],
            "launchpad": {"name": "Pump.Fun"}, "knownAccounts": {PAIR: {"type": "AMM"}},
            "topHolders": [{"address": f"T{i}" + "x" * 41, "owner": f"O{i}" + "y" * 41, "uiAmount": 1e7 + i * 3e5, "pct": 1.0 + i * 0.03,
                            "insider": False} for i in range(20)],
            "graphInsidersDetected": 1, "insiderNetworks": [{"size": 5, "tokenAmount": 400e12}],
            "totalHolders": holders, "risks": []}
D.CFG["REJECT_INSIDER_NETWORKS"] = True
young = dict(m, age_min=600, mcap=8e5)
old = dict(m, age_min=8 * 1440, mcap=9e6)
ok, why, cat, sec = asyncio.run(D.security_check(st, None, MINT, young, {MINT: (time.time(), rep(9000))}))
assert not ok and "bundle" in why, why
ok, why, cat, sec = asyncio.run(D.security_check(st, None, MINT, old, {MINT: (time.time(), rep(3000))}))
assert not ok, "meno di 5k holder resta hard"
ok, why, cat, sec = asyncio.run(D.security_check(st, None, MINT, old, {MINT: (time.time(), rep(9000))}))
assert sec and sec["bundle_established"] and "affermato" in why, why
print("test_v85c OK")
