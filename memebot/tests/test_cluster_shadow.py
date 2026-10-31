"""Compito 18: il cluster v2 deve girare anche quando RugCheck e' giu (lo scarto va diretto allo shadow
senza mai passare dall'Analyst) e cluster_pct deve finire in entry_features su ogni riga shadow.
Caso Brilliant: shadow per rugcheck_down con un cluster al 31,85% mai visto prima di questo fix.
Offline, su copia in /tmp. Fixture sintetiche (indirizzi finti)."""
import asyncio, base64, csv, time
import desk as D

assert D.DATA_DIR.startswith("/tmp"), "test solo su copia in /tmp"
D.tg_send = lambda *_a, **_k: None
D.CFG["SHADOW_TRADES"] = True
D.CFG["CLUSTER_V2"] = True

MINT = "A" * 40 + "pump"
PAIR = "P" * 43


def b58dec(s):
    n = 0
    for ch in s:
        n = n * 58 + D._B58.index(ch)
    return n.to_bytes(32, "big")


raw = bytearray(301)
raw[43:75], raw[107:139] = b58dec(MINT), b58dec("L" * 43)


async def fake_rpc(_http, method, params):
    if method == "getAccountInfo" and params[0] == MINT:
        return {"value": {"owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "data": {"parsed": {"info": {
            "mintAuthority": None, "freezeAuthority": None, "supply": "1000000000000000", "decimals": 6}}}}}
    if method == "getAccountInfo":
        return {"value": {"owner": D.PUMP_AMM, "data": [base64.b64encode(bytes(raw)).decode(), "base64"]}}
    if method == "getTokenSupply":
        return {"value": {"amount": "999"}}   # LP NON bruciata -> lp_status "unknown"/dubbio (vedi sotto)
    if method == "getTokenLargestAccounts":
        return {"value": [{"address": "V" * 43, "uiAmount": 200e6}]
                + [{"address": f"T{i}" + "x" * 41, "uiAmount": 15e6 - i * 7e5} for i in range(15)]}
    if method == "getMultipleAccounts" and params[1].get("encoding") == "jsonParsed":
        return {"value": [{"data": {"parsed": {"info": {"owner": PAIR if a.startswith("V") else "W" + a[1:]}}}}
                          for a in params[0]]}
    if method == "getMultipleAccounts":
        return {"value": [{"owner": D.SYSTEM_PROGRAM} for _ in params[0]]}


D._rpc_core = fake_rpc


class FakeHttp:
    async def get(self, url, **_k):
        if url.endswith("/report"):
            return None   # RugCheck giu
        if url.endswith("/insiders/networks"):
            return [{"size": 4, "currentHolding": 10e12}]
        return None


# cluster2: finto, come in test_v82.py (RES per mint), ma qui serve solo UNO (Brilliant-style, 31.85%)
CALLED = []


async def fake_cluster_scan(post_fn, mint, exclude=None, token_program=None, cfg=None):
    CALLED.append(mint)
    await asyncio.sleep(0.01)
    return {"level": "hard", "why": "un gruppo di wallet collegati al 31.85%", "cluster_pct": 31.85,
            "max_group_pct": 31.85}
D.SRC["cluster2"] = type("M", (), {"scan": staticmethod(fake_cluster_scan)})

M = {"symbol": "Brilliant", "dex": "pumpswap", "pair": PAIR, "pairs_all": [PAIR], "age_min": 300, "mcap": 4e5,
     "liq": 8e4, "liq_max": 8e4, "price": 1e-4}
st = D.State()
D.CL2["cache"].clear()
D.CL2["tasks"].clear()
import os, json


async def scenario():
    # 1) security_check: RugCheck giu, LP non verificata -> dubbio -> scartato (mai un acquisto vero)
    ok, why, cat, sec = await D.security_check(st, FakeHttp(), MINT, M, {})
    assert not ok and sec["severe"] == "rugcheck_down" and "LP" in why, (why, sec)

    # 2) cluster2 DEVE essere stato avviato qui (prima del fix: mai, perche' questo scarto va diretto allo
    # shadow senza passare dall'Analyst, che e' l'unico altro punto che avvia cluster2_start). Il task e'
    # schedulato in background (asyncio.ensure_future, mai atteso qui): basta dare un giro di loop per vederlo
    # partire (CALLED si riempie al primo passo di fake_cluster_scan).
    await asyncio.sleep(0)
    assert MINT in CALLED, "cluster2 non avviato mentre RugCheck e' giu (bug da correggere)"

    # 3) come fa il Filter per ogni scarto 'rugcheck_down': apre lo shadow (mai un acquisto). Lo stesso loop
    # (nessun nuovo asyncio.run) fa proseguire anche il task di cluster2 avviato al passo 1.
    sh = await D.open_shadow(st, FakeHttp(), MINT, M, ["rugcheck_down"], why, 0.0, [])
    assert sh is not None and MINT in st.shadow_trades

    # 4) il task di cluster2 finisce e si aggancia allo shadow appena apparso in st.shadow_trades
    await asyncio.sleep(0.05)
    assert st.shadow_trades[MINT].get("cluster2", {}).get("cluster_pct") == 31.85, \
        st.shadow_trades[MINT].get("cluster2")

    # 5) alla chiusura, cluster_pct finisce in entry_features (JSON) della riga shadow
    if os.path.exists(D.SHADOW_CSV):
        os.remove(D.SHADOW_CSV)
    await D.shadow_apply(st, FakeHttp(), MINT, st.shadow_trades[MINT],
                         D.shadow_decide(st.shadow_trades[MINT], sh["t"] + 1000), sh["t"] + 1000)

asyncio.run(scenario())
rows = list(csv.DictReader(open(D.SHADOW_CSV)))
assert len(rows) == 1
fe = json.loads(rows[0]["entry_features"])
assert fe.get("cluster_pct") == 31.85 and fe.get("cluster_level") == "hard", fe

print("test_cluster_shadow: ok")
