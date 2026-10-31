"""Ricerca errori 2: dati mancanti letti come 'ok' o come rug. Offline, solo su copia in /tmp."""
import asyncio, time
import desk as D

assert D.DATA_DIR.startswith("/tmp"), "test solo su copia in /tmp"
D.tg_send = lambda *_a, **_k: None
SUP = 1_000_000_000 * 10 ** 6
MINT = "B" * 40 + "pump"


def rep(**kw):
    r = {"token": {"supply": SUP, "decimals": 6}, "creator": "CR", "totalHolders": 5000, "risks": [],
         "topHolders": [{"address": f"H{i}", "owner": f"O{i}", "pct": p, "uiAmount": p / 100 * 1e9, "insider": False}
                        for i, p in enumerate([20.0, 10.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.0, 1.9])],
         "markets": [{"marketType": "raydium", "pubkey": "MAIN", "lp": {"lpLockedPct": 100.0}}]}
    r.update(kw)
    return r


def check(r, m=None):
    m = m or {"dex": "raydium", "liq": 50_000.0, "pair": "MAIN"}
    return asyncio.run(D.security_check(D.State(), None, MINT, m, {MINT: (time.time(), r)}))


# decimali mancanti: prima top 10 = 0% (passava); ora si usa pct (66,9% = troppo alto)
ok, why, cat, sec = check(rep(token={"supply": SUP}))
assert not ok or (sec and sec["top10"] > 60), why
ok_ref, why_ref, _, sec_ref = check(rep())
assert sec_ref is None or sec_ref["top10"] > 60
# nessuna quota calcolabile -> scarto, mai 0%
hs = [{"address": f"H{i}", "owner": f"O{i}", "insider": False} for i in range(10)]
ok, why, cat, _ = check(rep(token={"supply": SUP}, topHolders=hs))
assert not ok and cat == "holder non disponibili", why

# LP: conta la pool usata (MAIN), non la migliore tra tutte
def holders_ok():
    return [{"address": f"H{i}", "owner": f"O{i}", "pct": 2.0, "uiAmount": 2e7 - i * 1e5, "insider": False} for i in range(10)]
mk = [{"marketType": "raydium", "pubkey": "MAIN", "lp": {}}, {"marketType": "raydium", "pubkey": "SIDE", "lp": {"lpLockedPct": 100.0}}]
ok, why, cat, sec = check(rep(markets=mk, topHolders=holders_ok()))
assert sec and sec["lp_status"] == "unknown", (why, sec and sec.get("lp_status"))
mk2 = [{"marketType": "raydium", "pubkey": "MAIN", "lp": {"lpLockedPct": 100.0}}, {"marketType": "x", "pubkey": "SIDE", "lp": {"lpLockedPct": 0.0}}]
ok, why, cat, sec = check(rep(markets=mk2, topHolders=holders_ok()))
assert ok and sec["lp_status"] == "ok", why
# pool Raydium di un token pump.fun: regola LP normale, non piu 'protocol'
mk3 = [{"marketType": "raydium", "pubkey": "MAIN", "lp": {"lpLockedPct": 0.0}}]
ok, why, cat, sec = check(rep(markets=mk3, topHolders=holders_ok(), launchpad={"name": "Pump.Fun"}))
assert sec and sec["lp_status"] == "zero", (why, sec and sec.get("lp_status"))
ok, why, cat, sec = check(rep(markets=[{"marketType": "pump_amm", "pubkey": "MAIN", "lp": {}}], topHolders=holders_ok(),
                              launchpad={"name": "Pump.Fun"}), m={"dex": "pumpswap", "liq": 5e4, "pair": "MAIN"})
assert sec and sec["lp_status"] == "protocol", why                     # PumpSwap resta com'era
# totalHolders assente nel report vero: segnato per la verifica con cluster v2
ok, why, cat, sec = check(rep(totalHolders=None, topHolders=holders_ok()))
assert ok and sec["holders_unknown"] is True, why
ok, why, cat, sec = check(rep(topHolders=holders_ok()))
assert sec["holders_unknown"] is False
assert "holders_unknown" in D.MEASURE_ONLY_KEYS

# honeypot richiesto ma modulo assente: nessun acquisto
rej = []
D.reject = lambda _st, _m, _s, why, **k: rej.append(why)
_hp = D.SRC.pop("honeypot", None)
D.CFG.update({"HONEYPOT_CHECK": True, "BIRDEYE_XCHECK_MODE": "off", "COST_GATE_MULT": 0.0})


class B:
    mode, realistic, http = "paper", False, None

    async def cash_usd(self, st):
        return st.cash

    async def buy(self, *_a, **_k):
        raise AssertionError("acquisto senza honeypot")


c = {"sec": {"lp_status": "ok", "soft": []}, "soft": [], "score": 0.9, "signals": "", "thesis": "", "features": {}}
asyncio.run(D._execute_buy(D.State(), B(), c, MINT, {"symbol": "T", "price": 1.0, "liq": 1e6, "url": "", "src": "t",
                                                     "dex": "pumpswap", "chg_m5": 5.0}))
assert any("modulo honeypot non caricato" in x for x in rej), rej
if _hp:
    D.SRC["honeypot"] = _hp

# casi aperti: un controllo senza coppia non e un rug; tre di fila si
case = {"sym": "LH", "mint": "LH", "until": 1e12, "status": "aperto"}
D.case_watch_step(case, {"price": 1.0, "liq": 1e5, "liq_known": True}, 1000.0)
for i in range(2):
    assert D.case_watch_step(case, None, 2000.0 + i)["status"] == "aperto" and case["last"] == 1.0
D.case_watch_step(case, {"price": 1.1, "liq": 1e5, "liq_known": True}, 3000.0)
assert case["miss"] == 0 and case["status"] == "aperto"
for i in range(3):
    r = D.case_watch_step(case, None, 4000.0 + i)
assert r["status"] == "rug" and "3 controlli" in case["end_why"], case
print("test_bug_hunt_2 OK")

# ---- lista nera del creatore: solo rug confermato, con motivo e scadenza
st = D.State()
assert not D.blacklist_creator(st, "C1", "crollo a -45.0%")
assert not D.blacklist_creator(st, "C1", "nuovo rischio RugCheck: Mutable metadata")
assert not D.blacklist_creator(st, "C1", "nessun prezzo da 15 min, possibile rug")          # senza conferma
assert "C1" not in st.blacklist
assert D.blacklist_creator(st, "C2", "liquidita crollata del 90%", "M2", now=1000.0)
assert D.blacklist_creator(st, "C3", "nessun prezzo da 15 min, possibile rug (confermato da Jupiter)", now=1000.0)
i2 = st.blacklist_info["C2"]
assert i2["why"].startswith("liquidita") and i2["mint"] == "M2" and i2["exp"] == 1000 + D.CFG["BLACKLIST_TTL_DAYS"] * 86400
assert D.creator_blacklisted(st, "C2", now=2000.0)
assert not D.creator_blacklisted(st, "C2", now=i2["exp"] + 1) and "C2" not in st.blacklist   # scaduta: tolta
st.blacklist.add("OLD")                                                                         # voce senza info
assert D.creator_blacklisted(st, "OLD")
print("lista nera OK")

# ---- ripiego RugCheck: quota del creatore dai top holder gia letti
st = D.State()
cl = {"level": "ok", "holders_total": 5000}
sec = {"rc_holders": [("CR1", 2.0), ("X", 1.0)]}
assert D.rc_down_doubts(st, cl, {"creator": "CR1"}, sec) == []
sec_big = {"rc_holders": [("CR1", D.CFG["MAX_CREATOR_PCT"] + 1), ("X", 1.0)]}
assert any("il creatore tiene" in x for x in D.rc_down_doubts(st, cl, {"creator": "CR1"}, sec_big))
print("quota creatore ripiego OK")
