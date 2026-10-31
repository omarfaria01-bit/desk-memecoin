"""Review del ripiego 'RugCheck giu' (v8.5c): buchi trovati e loro riproduzione. Offline, solo su copia in /tmp.
Fixture sintetiche (indirizzi finti). Le 6 lezioni negative compaiono SOLO come casi da scartare."""
import asyncio, base64, time
import desk as D

assert D.DATA_DIR.startswith("/tmp"), "test solo su copia in /tmp"
D.tg_send = lambda *_a, **_k: None
D.CFG["SHADOW_TRADES"] = False

MINT = "A" * 40 + "pump"
PAIR = "P" * 43
LPM = "L" * 43


def b58dec(s):
    n = 0
    for ch in s:
        n = n * 58 + D._B58.index(ch)
    return n.to_bytes(32, "big")


raw = bytearray(301)
raw[43:75], raw[107:139] = b58dec(MINT), b58dec(LPM)
W = {"nets": "ok", "exts": [], "prog_owner": None}


async def fake_rpc(_http, method, params):
    if method == "getAccountInfo" and params[0] == MINT:
        return {"value": {"owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "data": {"parsed": {"info": {
            "mintAuthority": None, "freezeAuthority": None, "supply": "1000000000000000", "decimals": 6,
            "extensions": W["exts"]}}}}}
    if method == "getAccountInfo":
        return {"value": {"owner": D.PUMP_AMM, "data": [base64.b64encode(bytes(raw)).decode(), "base64"]}}
    if method == "getTokenSupply":
        return {"value": {"amount": "0"}}
    if method == "getTokenLargestAccounts":
        return {"value": [{"address": "V" * 43, "uiAmount": 200e6}, {"address": "X" * 43, "uiAmount": 300e6}]
                + [{"address": f"T{i}" + "x" * 41, "uiAmount": 15e6 - i * 7e5} for i in range(15)]}
    if method == "getMultipleAccounts" and params[1].get("encoding") == "jsonParsed":
        return {"value": [{"data": {"parsed": {"info": {"owner": PAIR if a.startswith("V") else
                                                        ("Q" * 43 if a.startswith("X") else "W" + a[1:])}}}}
                          for a in params[0]]}
    if method == "getMultipleAccounts":   # Q = proprietario che e un account di programma (PDA, vault...)
        return {"value": [{"owner": W["prog_owner"] if (o.startswith("Q") and W["prog_owner"]) else D.SYSTEM_PROGRAM}
                          for o in params[0]]}


D._rpc_core = fake_rpc


class FakeHttp:
    async def get(self, url, **_k):
        if url.endswith("/report"):
            return None   # RugCheck giu
        if url.endswith("/insiders/networks"):
            return [{"size": 4, "currentHolding": 10e12}] if W["nets"] == "ok" else None
        return None


M = {"symbol": "TST", "dex": "pumpswap", "pair": PAIR, "pairs_all": [PAIR], "age_min": 300, "mcap": 4e5,
     "liq": 8e4, "liq_max": 8e4, "price": 1e-4}


def sec_check(**w):
    W.update({"nets": "ok", "exts": [], "prog_owner": None}, **w)
    return asyncio.run(D.security_check(D.State(), FakeHttp(), MINT, dict(M), {}))


# base: ripiego pulito -> passa il Filter (poi servono Analyst ed Executor)
ok, why, _, sec = sec_check()
assert ok and sec["rc_down"], why

# ---- BUCO 1 (alta, corretto): endpoint reti insider in errore -> il controllo bundle veniva saltato
ok, why, _, sec = sec_check(nets="err")
assert not ok and sec["severe"] == "rugcheck_down" and "reti insider" in why, why

# ---- BUCO 2 (alta, corretto): holder totali assenti -> MIN_HOLDERS saltato. Ora serve holders_total di cluster v2
st = D.State()
CR = {"creator": "C" * 43}
assert D.rc_down_doubts(st, {"level": "ok", "holders_total": 5000}, CR) == []
assert any("holder" in x for x in D.rc_down_doubts(st, {"level": "ok", "holders_total": 30}, CR))
assert any("holder" in x for x in D.rc_down_doubts(st, {"level": "soft"}, CR))
for lv in ("hard", "unknown", None):
    assert any("cluster v2" in x for x in D.rc_down_doubts(st, {"level": lv, "holders_total": 5000} if lv else None, CR))

# ---- BUCO 3 (media, corretto): creatore assente nel ripiego -> lista nera saltata
assert any("creatore non verificabile" in x for x in D.rc_down_doubts(st, {"level": "ok", "holders_total": 5000}, None))
st.blacklist.add("C" * 43)
assert any("lista nera" in x for x in D.rc_down_doubts(st, {"level": "ok", "holders_total": 5000}, CR))
st.blacklist.clear()


# ---- BUCO 4 (media, corretto): honeypot spento/non caricato -> requisito GoPlus+honeypot del ripiego saltato
rej = []
D.reject = lambda _st, _mint, _sym, why, **k: rej.append(why)
D.CFG["HONEYPOT_CHECK"] = False
D.CFG["BIRDEYE_XCHECK_MODE"] = "off"
D.CFG["COST_GATE_MULT"] = 0.0


class B:
    mode, realistic, http = "paper", False, None

    async def cash_usd(self, st):
        return st.cash

    async def buy(self, *_a, **_k):
        raise AssertionError("acquisto eseguito col ripiego senza honeypot")


c = {"sec": dict(sec_check()[3]), "soft": [], "score": 0.9, "signals": "", "thesis": "", "features": {}, "smult": 1.0}
mm = dict(M, url="", src="test")
asyncio.run(D._execute_buy(D.State(), B(), c, MINT, mm))
assert any("honeypot/GoPlus non disponibile" in x for x in rej), rej
D.CFG["HONEYPOT_CHECK"] = True

# ---- BUCO 5 (media, NON corretto, documentato): ogni proprietario che e un account di programma viene escluso dal
# top 10 come "PROGRAM". RugCheck distingue locker/AMM; il ripiego esclude qualsiasi PDA o vault di programma
# (es. un programma del team), che cosi sparisce dal calcolo della concentrazione.
t_sys = sec_check()[3]["top10"]
t_prog = sec_check(prog_owner="Dev1111Program1111111111111111111111111111")[3]["top10"]
assert t_prog < t_sys - 25, (t_sys, t_prog)   # 300M su 1B (30%) spariscono dal top 10: buco ancora aperto

# ---- Token-2022 con estensioni rischiose: dubbio (gia coperto dal ripiego, verificato)
ok, why, _, sec = sec_check(exts=[{"extension": "transferHook"}])
assert not ok and "Token-2022" in why, why
ok, why, _, sec = sec_check(exts=[{"extension": "permanentDelegate"}])
assert not ok and "Token-2022" in why, why

# ---- Lezioni negative con RugCheck simulato giu: tutte devono restare scartate.
# Verdetti cluster v2 come registrati in LABEL_SEEDS (regole v8.4c/e), dati di mercato dalle note. Solo casi da scartare.
def lesson_rejected(name, cl, m_over=None, exts=None, symbol="TST", name_tk=None):
    ok, why, _, sec = sec_check(exts=exts or [])
    if not ok:
        return f"Filter: {why}"
    if D.ticker_impostor(symbol, MINT, name_tk):
        return "ticker impostore"
    pen, age_rej, _ = D.age_penalty(dict(M, **(m_over or {})))
    if age_rej:
        return f"eta: {age_rej}"
    d = D.rc_down_doubts(D.State(), cl, CR)
    return f"ripiego: {d}" if d else None


cases = {
    "BEAST": dict(cl={"level": "hard", "holders_total": 2500}),          # sciame 1997 wallet identici 76.4%
    "PILLSEY": dict(cl={"level": "hard", "holders_total": 3665}),        # hub burst
    "BNB": dict(cl={"level": "hard", "holders_total": 3000}, symbol="BNB"),   # quote uniformi + ticker impostore
    "GLITCH": dict(cl={"level": "hard", "holders_total": 2000},          # CEX-fresh, Token-2022
                   exts=[{"extension": "transferFeeConfig", "state": {"newerTransferFee": {"transferFeeBasisPoints": 0}}}]),
    "HYPERCHAN": dict(cl={"level": "hard", "holders_total": 1500}),      # un solo finanziatore 59/60
    # DESSA: cluster v2 SOLO soft (wallet nuovi 87%): col ripiego la scarta la regola eta (4 g, vol 1h/liq 0.11).
    "DESSA": dict(cl={"level": "soft", "holders_total": 15600},
                  m_over={"age_min": 4 * 1440, "vol_h1": 0.11 * 8e4, "liq": 8e4, "mcap": 4e5, "chg_h1": 3}),
}
for name, kw in cases.items():
    r = lesson_rejected(name, **kw)
    assert r, f"{name} passa dal ripiego!"
    print(f"{name:10s} scartato -> {r[:90]}")
print("test_rc_fallback_review OK")
