"""v8.2: cluster v2 (sciame, infrastruttura, distributore, 'unknown' mai ok), honeypot, etichette rug. Offline."""
import asyncio, json, os, time
import desk as D
from sources import cluster_v2 as CV, honeypot as HP
D.CFG["SIGNALS_ENABLED"] = False; D.CFG["PROJECT_CHECK"] = False

A = CV.analyze
NOUNI = {"UNIFORM_HARD_WALLETS": 999, "UNIFORM_HARD_PCT": 999, "STAR_HARD_N": 999}   # v8.4c: regole a parte
KRAKEN = "6LY1" + "x" * 36 + "zkzF"          # indirizzo che corrisponde a un pattern CEX noto (Kraken)


def row(i, pct, **k):
    r = {"o": f"W{i}", "pct": pct, "ntx": k.pop("ntx", 500), "src": None, "ts": None, "slot": None}
    r.update(k)
    return r


# ---- 1) BEAST (calibrazione): sciame di saldi identici al 76% -> RIGIDO anche con gruppi veri piccoli
rows = [row(i, 0.039, swarm=True, acq={"payer": "DIST", "from": "DIST", "slot": 100 + i}) for i in range(60)]
rows += [row(100 + i, 0.5) for i in range(20)]
sw = {"pct": 76.4, "wallets": 1997, "values": [(0.03902, 1604), (0.03303, 310)], "owners": {r["o"] for r in rows[:60]}}
r = A(rows, infra={"DIST": "hub (>=1000 tx/h)"}, swarm=sw, distributors={"DIST": 60})
assert r["level"] == "hard" and "saldo identico" in r["why"], r
assert r["swarm_in_group"] and r["cluster_pct"] == 76.4, r
assert r["reasons"].get("common_token_sender") == 60, r["reasons"]       # il distributore NON e infrastruttura
# anche se il distributore e finito per sbaglio nella lista infra imparata, chi CEDE i token resta un legame vero
r2 = A(rows, infra={"DIST": "hub (>=1000 tx/h)"}, swarm=None, distributors=None)
assert r2["reasons"].get("common_token_sender") == 60 and r2["max_group_wallets"] >= 60, r2
print("BEAST: sciame + distributore -> rigido OK")

# ---- 2) un solo gruppo vero >= 20% -> rigido; gruppo enorme (50+ wallet, >= 15%) -> rigido
rows = [row(i, 1.1, ntx=5, src="F1") for i in range(20)] + [row(50 + i, 0.2) for i in range(30)]
r = A(rows)
assert r["level"] == "hard" and r["max_group_pct"] >= 20 and "gruppo di 20 wallet" in r["why"], r
rows = [row(i, 0.32, ntx=5, src="F2") for i in range(55)]
r = A(rows)
assert r["level"] == "hard" and "gruppo enorme" in r["why"], r
assert "uniformi" in r["why"]                      # v8.4c: 55 quote identiche = anche sciame (quote uniformi)
r = A(rows, cfg={"GROUP_HARD_PCT": 20.0, "BIG_GROUP_WALLETS": 80, **NOUNI})
assert r["level"] == "soft", r                       # 17.6% senza gruppo enorme: soft (>= 8%)
print("gruppo >= 20% / gruppo enorme OK")
# v8.4c: da qui in poi le quote sintetiche sono identiche per costruzione: quote uniformi e stelle sono
# testate in test_v84 (calibrate sui dati reali), qui restano spente per isolare le regole v8.2
A = lambda *a, **k: CV.analyze(*a, **{**k, "cfg": {**NOUNI, **(k.get("cfg") or {})}})  # noqa: E731

# ---- 3) falsi legami: stesso hot wallet CEX / fee payer di app -> non insider, ma loggato a parte
rows = [row(i, 0.3, src=KRAKEN, ntx=300) for i in range(30)]
r = A(rows)
assert r["max_group_pct"] == 0 and r["cluster_infra_pct"] == 9.0 and r["level"] != "hard", r
assert any("Kraken" in x for x in r["infra_used"]), r
# prudenza: gruppo via infrastruttura >= 25% -> rigido comunque; >= 20% con wallet nuovi -> rigido
rows = [row(i, 0.9, src=KRAKEN, ntx=300) for i in range(30)]
assert A(rows)["level"] == "hard"
rows = [row(i, 0.7, src=KRAKEN, ntx=5) for i in range(30)]          # 21%, tutti wallet nuovi
assert A(rows)["level"] == "hard"
rows = [row(i, 0.7, src=KRAKEN, ntx=300) for i in range(30)]        # 21%, wallet vecchi: soft, non rigido
assert A(rows)["level"] == "soft"
print("infrastruttura (CEX/app) OK")

# ---- 4) wallet nuovi finanziati nello stesso minuto da exchange diversi = legame vero
rows = [row(i, 0.5, src=KRAKEN if i % 2 else "BmFd" + "y" * 36 + "ymy6", ntx=3, ts=1000 + i, slot=10 + i)
        for i in range(10)]
r = A(rows)
assert r["reasons"].get("same_minute") == 10 and r["max_group_wallets"] == 10, r
print("stesso minuto OK")

# ---- 5) 'unknown' non vale mai come ok
D.CFG["CLUSTER2_WAIT_SEC"] = 0.2


async def slow():
    await asyncio.sleep(5)
    return {"level": "none"}


async def t_wait():
    D.CL2["cache"].clear()
    t = asyncio.ensure_future(slow())
    res = await D.cluster2_wait(D.State(), None, "MX", {}, t)
    t.cancel()
    return res
res = asyncio.run(t_wait())
assert res["level"] == "unknown" and "tempo scaduto" in res["why"], res


async def boom(*a, **k):
    raise RuntimeError("rpc giu")
D.SRC["cluster2"] = type("M", (), {"scan": staticmethod(boom)})


async def t_err():
    D.CL2["cache"].clear()
    return await D.cluster2_scan(D.State(), None, "MY", {})
res = asyncio.run(t_err())
assert res["level"] == "unknown" and "MY" not in D.CL2["cache"], res      # l'errore non resta in cache
D.SRC["cluster2"] = CV
print("unknown mai ok OK")

# ---- 6) Analyst: hard -> scarto + shadow 'cluster' + etichetta; unknown -> segnale soft e size ridotta
async def fake_candles(http, pool): return None
D.fetch_candles = fake_candles
opened = []


async def fake_open(st, http, mint, m, keys, reason, score, flags, now=None, tag=None, signals=None):
    opened.append((mint, keys, tag, signals)); st.shadow_trades[mint] = {"tag": tag, "signals": list(signals or [])}
D.open_shadow = fake_open
RES = {"HARD1": {"level": "hard", "why": "1997 wallet con saldo identico", "cluster_pct": 76.4},
       "UNK1": {"level": "unknown", "why": "tempo scaduto"},
       "OK1": {"level": "none", "why": "", "cluster_pct": 0.4}}


async def fake_scan2(st, http, mint, sec, sym="?", rt=False):
    await asyncio.sleep(0.01)
    return RES[mint]
D.cluster2_scan = fake_scan2
D.CFG["CLUSTER2_WAIT_SEC"] = 2
mk = {"symbol": "S", "name": "s", "price": 1.0, "liq": 500_000.0, "chg_h1": 10.0, "chg_m5": 2.0, "buys_m5": 30,
      "sells_m5": 10, "vol_m5": 50_000.0, "vol_h1_main": 100_000.0, "buys_h1": 300, "sells_h1": 100,
      "vol_h1": 100_000.0, "twitter": 1, "telegram": 1, "website": 1, "pair": "P", "url": "", "src": "pump.fun"}


async def run_analyst(cands, st):
    qi, qo = asyncio.Queue(), asyncio.Queue()
    for c in cands:
        await qi.put(c)
    t = asyncio.ensure_future(D.analyst_agent(st, None, qi, qo))
    await asyncio.sleep(0.5)
    t.cancel()
    out = []
    while not qo.empty():
        out.append(qo.get_nowait())
    await asyncio.sleep(0.05)
    return out
D.CL2["cache"].clear(); D.CL2["tasks"].clear()
st = D.State(); st.threshold = 0.0
out = asyncio.run(run_analyst([{"mint": k, "m": dict(mk), "sec": {"soft": []}, "filter_ok": True}
                               for k in ("HARD1", "UNK1", "OK1")], st))
mints = [o["mint"] for o in out]
assert mints == ["UNK1", "OK1"], mints
u = out[0]
assert u["soft"] == ["cluster_unknown"] and abs(u["size_mult"] - D.CFG["CLUSTER_UNKNOWN_SIZE_MULT"]) < 1e-9, u
assert "cluster2:unknown" in u["signals"] and out[1]["soft"] == [] and "cluster2:none:0-5" in out[1]["signals"]
assert opened[-1][0] == "HARD1" and opened[-1][1] == ["cluster_hard"] and opened[-1][2] == "cluster"
assert st.labels.get("HARD1") == "bad_cluster" and st.reject_reasons.get("cluster") == 1
# 'skip': un cluster non verificabile non si compra
D.CL2["cache"].clear(); D.CL2["tasks"].clear()
D.CFG["CLUSTER_UNKNOWN_ACTION"] = "skip"
st = D.State(); st.threshold = 0.0
out = asyncio.run(run_analyst([{"mint": "UNK1", "m": dict(mk), "sec": {"soft": []}, "filter_ok": True}], st))
assert out == [] and st.reject_reasons.get("cluster non verificato") == 1
D.CFG["CLUSTER_UNKNOWN_ACTION"] = "soft"
# scarto per momentum (come BEAST): la scansione parte lo stesso e i dati finiscono sullo shadow
D.CL2["cache"].clear(); D.CL2["tasks"].clear()
D.cluster2_scan = D.__dict__["cluster2_scan"]
print("analyst OK")

# ---- 7) honeypot: estensioni Token-2022 e preventivo di vendita
beast = {"info": {"decimals": 6, "freezeAuthority": None, "mintAuthority": None,
                  "extensions": [{"extension": "metadataPointer", "state": {}},
                                 {"extension": "tokenMetadata", "state": {}}]}, "type": "mint"}
h, s, _ = HP.mint_flags(beast)
assert h == [] and s == []
bad = {"info": {"mintAuthority": "X", "freezeAuthority": "Y", "extensions": [
    {"extension": "transferHook", "state": {"programId": "Hook111", "authority": "Z"}},
    {"extension": "transferFeeConfig", "state": {"newerTransferFee": {"transferFeeBasisPoints": 1000},
                                                 "olderTransferFee": {"transferFeeBasisPoints": 0}}},
    {"extension": "permanentDelegate", "state": {"delegate": "D1"}},
    {"extension": "defaultAccountState", "state": {"accountState": "frozen"}},
    {"extension": "nonTransferable", "state": {}}]}}
h, s, _ = HP.mint_flags(bad)
assert len(h) == 7, h
h, s, _ = HP.mint_flags({"info": {"extensions": [{"extension": "transferFeeConfig", "state": {
    "newerTransferFee": {"transferFeeBasisPoints": 100}}}]}})
assert h == [] and s and "1.00%" in s[0]
assert HP.sell_verdict(None, "no_route", 10**8)[0].startswith("nessun percorso")
assert "impatto" in HP.sell_verdict({"priceImpactPct": "0.31", "outAmount": "90000000"}, None, 10**8)[0]
assert HP.sell_verdict({"priceImpactPct": "0.019", "outAmount": "96000000"}, None, 10**8)[0] is None   # BEAST
assert "perderebbe" in HP.sell_verdict({"priceImpactPct": "0.01", "outAmount": "50000000"}, None, 10**8)[0]
why, info = HP.sell_verdict(None, "http 429", 10**8)
assert why is None and info.get("sell_quote") == "non disponibile"


class FakeHttp:
    def __init__(self, parsed): self.parsed = parsed
    async def post(self, url, body):
        return {"result": {"value": {"owner": "TokenzQd", "data": {"parsed": self.parsed}}}}


QUOTES = {}


async def fake_jup(http, inp, out, amount, slip_bps=None):
    return QUOTES[(inp == D.WSOL)]
D.jup_quote = fake_jup
D.SOL_USD[:] = [200.0, time.time()]
QUOTES.update({True: ({"outAmount": "1000000"}, None), False: ({"outAmount": str(int(0.15e9 * 0.96)),
                                                                 "priceImpactPct": "0.019"}, None)})
D.CFG["GOPLUS_CHECK"] = False   # v8.4: GoPlus ha il suo test offline in test_v84
hp = asyncio.run(D.honeypot_check(D.State(), FakeHttp(beast), "BEAST", 30.0, {}))
assert hp["level"] == "ok" and hp["sell_impact_pct"] == 1.9, hp
QUOTES[False] = (None, "no_route")
hp = asyncio.run(D.honeypot_check(D.State(), FakeHttp(beast), "HP", 30.0, {}))
assert hp["level"] == "hard" and "honeypot" in hp["why"], hp
QUOTES[False] = (None, "http 429")
hp = asyncio.run(D.honeypot_check(D.State(), FakeHttp(beast), "HP", 30.0, {}))
assert hp["level"] == "unknown", hp                                    # mai 'ok' senza preventivo di vendita
QUOTES[False] = ({"outAmount": "140000000", "priceImpactPct": "0.01"}, None)
hp = asyncio.run(D.honeypot_check(D.State(), FakeHttp(bad), "HP", 30.0, {}))
assert hp["level"] == "hard" and "transfer hook" in hp["why"], hp
hp = asyncio.run(D.honeypot_check(D.State(), None, "HP", 30.0, {}))
assert hp["level"] == "unknown"
print("honeypot OK")

# ---- 8) etichette: 'suspected_rug' -> 'rug' con -50% dal massimo (dump) o liquidita -50% (rimozione LP)
st = D.State()
st.labels["B1"] = "suspected_rug"
t0 = 1_000_000
assert D.label_watch_tick(st, "B1", 1.0, 50_000, t0) is None
assert D.label_watch_tick(st, "B1", 4.0, 68_000, t0 + 60) is None          # sale: nessuna promozione
ev = D.label_watch_tick(st, "B1", 0.01, 1_800, t0 + 240)
assert ev and st.labels["B1"] == "rug" and ev["how"].startswith("dump") and ev["drop_from_peak_pct"] > 99, ev
assert ev["window"] == [t0 + 60, t0 + 240] and ev["peak"] == 4.0
st.labels["B2"] = "bad_cluster"
D.label_watch_tick(st, "B2", 1.0, 50_000, t0)
ev = D.label_watch_tick(st, "B2", 0.9, 20_000, t0 + 30)
assert ev and ev["how"] == "rimozione LP", ev
st.labels["B3"] = "cluster"                                               # etichette non sospette: ignorate
assert D.label_watch_tick(st, "B3", 1.0, 1, t0) is None and "B3" not in st.label_watch
assert any("diventato 'rug'" in x for x in st.lessons)
print("etichette rug OK")

# ---- 9) casi seminati (BEAST, DESSA): una sola volta
st = D.State()
D.apply_label_seeds(st)
n = len(st.label_log)
D.apply_label_seeds(st)
assert n == len(D.LABEL_SEEDS) == len(st.label_log)
assert st.learner_shadow["cluster_hard"]["hist"][-1][1] == 1                # BEAST = esito rug
assert any("BEAST" in x for x in st.lessons)
print("casi seminati OK")

# ---- 10) posizione aperta che diventa 'hard' -> uscita al prossimo giro del Risk
async def hard_scan(st, http, mint, sec, sym="?", rt=False):
    return {"level": "hard", "why": "gruppo al 30%"}
D.cluster2_scan = hard_scan
st = D.State()
st.positions["P1"] = p = {"symbol": "P", "cl_sec": {"exclude": [], "token_program": None}}
asyncio.run(D.cluster2_held(st, None, "P1", p))
assert p["cluster_exit"] == "gruppo al 30%"
print("ricontrollo posizioni OK")

# ---- 11) override: le soglie rigide si possono solo stringere
r = D.parse_overrides(json.dumps({"global": {"CLUSTER_GROUP_HARD_PCT": 40, "HONEYPOT_SELL_IMPACT_PCT": 60,
                                             "CLUSTER_UNKNOWN_ACTION": "ok"}}), D.OVR.values(), {}, set())
g = r["globals"]
assert g["CLUSTER_GROUP_HARD_PCT"] == 20 and g["HONEYPOT_SELL_IMPACT_PCT"] == 25, g
assert g["CLUSTER_UNKNOWN_ACTION"] == "soft", g
print("override OK")
print("TUTTI I TEST v8.2 OK")

# ---------------------------------------------------------------- v8.2: Helius (chiave finta, nessuna rete)
import os as _os
from sources import helius as H
_old_key = _os.environ.get("HELIUS_API_KEY")
_os.environ["HELIUS_API_KEY"] = "FAKEKEY123456"
H.USAGE_PATH = None
assert H.on() and H.url().endswith("FAKEKEY123456")
_u = H.BASE + "FAKEKEY123456"   # URL costruito a runtime (niente chiavi scritte nei file)
assert "FAKEKEY" not in H.mask("errore su " + _u + "&x=1")
assert H.mask(_u).endswith("api-key=***")
H.U.update(day="", credits=0)
H._roll()
assert H.allow("getProgramAccounts", "core")
import datetime as _dt
_noon = _dt.datetime.now().replace(hour=12, minute=0, second=0).timestamp()
H.U["credits"] = int(H.pace_budget(_noon)) + 10     # a mezzogiorno oltre il ritmo: bulk e low al pubblico, core si
assert not H.allow("getTransaction", "bulk", _noon) and not H.allow("getTransaction", "low", _noon)
assert H.allow("getAccountInfo", "core", _noon)
H.U["credits"] = int(H.day_budget())                 # budget del giorno finito: nemmeno core
assert not H.allow("getAccountInfo", "core")
H.U["credits"] = 0
H.note("getProgramAccounts", 200, True)
assert H.U["credits"] == 10 and H.U["by_method"]["getProgramAccounts"] == 10
H.note("getSlot", 429, False)
assert not H.allow("getSlot", "core")               # pausa dopo un 429
H._cool[0] = 0


class _Resp:
    def __init__(self, status, data):
        self.status, self.data = status, data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self, content_type=None):
        return self.data

    async def text(self):
        return json.dumps(self.data)


class _Sess:
    def __init__(self):
        self.urls = []

    def post(self, url, json=None, headers=None):
        self.urls.append(url)
        if "helius" in url:
            return _Resp(200, {"jsonrpc": "2.0", "result": {"value": "da-helius"}})
        return _Resp(200, {"jsonrpc": "2.0", "result": {"value": "da-pubblico"}})


async def _route():
    s = _Sess()
    http = D.Http(s)
    d = await http.post("https://api.mainnet-beta.solana.com", {"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo", "params": ["x"]})
    assert d["result"]["value"] == "da-helius", d
    _os.environ.pop("HELIUS_API_KEY")
    d = await http.post("https://api.mainnet-beta.solana.com", {"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo", "params": ["x"]})
    assert d["result"]["value"] == "da-pubblico", d
    _os.environ["HELIUS_API_KEY"] = "FAKEKEY123456"
    H.U["credits"] = int(H.day_budget())             # budget finito -> pubblico
    d = await D._cl2_post(http, "https://solana-rpc.publicnode.com", {"jsonrpc": "2.0", "id": 1, "method": "getTransaction", "params": []}, "bulk")
    assert d["result"]["value"] == "da-pubblico", d
    H.U["credits"] = 0
asyncio.run(_route())
print("helius: instradamento e budget OK")


async def _das():
    calls = []

    async def rpc(method, params, url=None):
        calls.append(method)
        if method == "getProgramAccounts":
            return None
        if method == "getTokenAccounts":
            if params["page"] == 1:
                return {"token_accounts": [{"owner": f"W{i}", "address": f"A{i}", "amount": 1000} for i in range(1000)]}
            return {"token_accounts": [{"owner": "BIG", "address": "AB", "amount": 1_000_000}]}
        return None
    rows, n, sw = await CV.top_holders(rpc, "MINT", top_n=5)
    assert rows[0][0] == "BIG" and n == 1001 and round(rows[0][1]) == 50, rows[:2]
    assert sw["wallets"] >= 1000, sw["wallets"]           # 1000 wallet con saldo identico = sciame
asyncio.run(_das())
print("helius: ripiego DAS getTokenAccounts OK")
if _old_key is None:
    _os.environ.pop("HELIUS_API_KEY", None)
else:
    _os.environ["HELIUS_API_KEY"] = _old_key
print("TUTTI I TEST v8.2 + Helius OK")
