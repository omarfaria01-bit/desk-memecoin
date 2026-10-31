"""v8.4f (review Claude): fee GoPlus reale, gate 5 min a 0, esclusioni holder, campi solo shadow, scarti
GoPlus/honeypot in shadow. Offline."""
import asyncio, base64, json, os, tempfile, time
import desk as D
from sources import cluster_v2 as C
from sources import goplus as G
from sources import solkeys as K

D.CFG["CLUSTER_V2"] = False; D.CFG["HONEYPOT_CHECK"] = False; D.CFG["SIGNALS_ENABLED"] = False
D.CFG["BIRDEYE_XCHECK_MODE"] = "off"; D.CFG["DYN_STOP_MODE"] = "off"; D.CFG["PRICE_PATHS"] = False
D.SOL_USD[0], D.SOL_USD[1] = 100.0, time.time()

# ---- 1. GoPlus: maximum_fee non e una percentuale (REMARC -> 1%)
REMARC = {"transfer_fee": {"current_fee_rate": {"fee_rate": "0.01", "maximum_fee": "1000000000000000"}},
          "transfer_fee_upgradable": {"status": "0"}, "metadata_mutable": {"status": "0"}}
hard, soft, flags, fee = G.verdict(REMARC)
assert fee == 1.0 and hard == [] and soft == ["GoPlus: fee di trasferimento 1%"], (hard, soft, fee)
assert G._fee_pct({"newer_transfer_fee": {"epoch": "800", "transfer_fee_basis_points": 1000, "maximum_fee": "1"}}) == 10.0
assert G._fee_pct({"fee_rate": "0.12", "maximum_fee": "5"}) == 12.0
h2, s2, _, f2 = G.verdict({"transfer_fee": {"fee_rate": "0.12"}})
assert f2 == 12.0 and h2 == ["GoPlus: fee di trasferimento 12%"], h2          # >= 10% resta rigido
h3, _, fl3, _ = G.verdict(dict(REMARC, transfer_fee_upgradable={"status": "1", "authority": [
    {"address": "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1", "malicious_address": 0}]}))
assert "GoPlus: fee di trasferimento modificabile dal dev" in h3 and "fee_auth_known" in fl3, (h3, fl3)
h4, _, fl4, _ = G.verdict(dict(REMARC, transfer_fee_upgradable={"status": "1", "authority": [
    {"address": "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"}]}))
assert h4 and "fee_auth_known" not in fl4
assert G._fee_pct({}) == 0.0 and G._fee_pct({"x": "y"}) == 0.01
print("GoPlus fee reale (REMARC 1%) OK")

# ---- 2. gate 5 min: default 0, stesso dato di ret_5m, nessun percorso lo aggira, clamp override [-8, +5]
assert D.CFG["MIN_CHANGE_M5_PCT"] == 0.0
assert D.m5_gate({"chg_m5": -0.4}) and D.m5_gate({"chg_m5": 0.0}) is None and D.m5_gate({"chg_m5": 3}) is None
now = int(time.time() // 60) * 60
up = [(now - 60 * (40 - i), 1 + i * 0.01, 1.02 + i * 0.01, 0.99 + i * 0.01, 1 + i * 0.01, 10) for i in range(40)]
dn = [(t, 2 - o, 2 - l + 0.02, 2 - h + 0.02, 2 - c, v) for t, o, h, l, c, v in up]
assert D.m5_gate({"chg_m5": -3.0}, up) is None                 # candele in salita: vale il dato delle candele
assert "candele" in (D.m5_gate({"chg_m5": 4.0}, dn) or "")      # candele in calo: scarto anche se chg_m5 > 0
P = D.parse_overrides
base = {k: D.CFG[k] for k in D.OVR_LIMITS}
assert P(json.dumps({"global": {"MIN_CHANGE_M5_PCT": -20}}), base, {}, set())["globals"]["MIN_CHANGE_M5_PCT"] == -8
assert P(json.dumps({"global": {"MIN_CHANGE_M5_PCT": 9}}), base, {}, set())["globals"]["MIN_CHANGE_M5_PCT"] == 5


class Broker:
    realistic, http = False, None
    def __init__(self): self.bought = []
    async def cash_usd(self, st): return 1000.0
    async def buy(self, st, mint, m, size, pre=None):
        self.bought.append(mint); return {"ok": False, "error": "test"}


_sp = []
D.spawn_shadow = lambda st, http, mint, m, keys, reason, score, flags, tag=None, signals=None: \
    _sp.append((mint, keys, tag, list(flags), list(signals or [])))
st, br = D.State(), Broker()
c = {"sec": {"lp_status": "ok", "lp_locked_pct": 100.0, "creator": ""}, "score": 0.99, "size_mult": 1.0, "soft": [],
     "narr_bonus": 0.08, "signals": "kol:abc|src:gmgn_trending", "thesis": "", "features": {}}
asyncio.run(D._execute_buy(st, br, c, "MFALL", {"symbol": "FALL", "liq": 8e4, "price": 1.0, "url": "", "src": "t",
                                                 "chg_m5": -1.5}))
assert br.bought == [] and "calo a 5 min" in str(st.__dict__), "m5"
print("gate 5 min a 0 (tutti i percorsi, clamp [-8,+5]) OK")

# ---- 3. esclusioni holder: incinerator ed exchange fuori dal top 10 (exchange mostrato a parte)
SUP = 1_000_000_000 * 10 ** 6
INC = "1nc1nerator11111111111111111111111111111111"
BIN = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"
hs = [(INC, 30.0), (BIN, 12.0)] + [(f"O{i}", p) for i, p in enumerate([3.0, 2.6, 2.4, 2.2, 2.0, 1.8, 1.6, 1.4, 1.2, 1.0])]
rep = {"token": {"supply": SUP, "decimals": 6}, "creator": "CR",
       "topHolders": [{"address": f"A{i}", "owner": o, "pct": p, "uiAmount": p / 100 * 1e9, "insider": False}
                      for i, (o, p) in enumerate(hs)],
       "markets": [{"marketType": "raydium", "pubkey": "POOL", "lp": {"lpLockedPct": 100}}], "totalHolders": 5000,
       "risks": []}
ok, why, cat, sec = asyncio.run(D.security_check(D.State(), None, "XYZ", {"dex": "raydium", "liq": 5e4},
                                                 {"XYZ": (time.time(), rep)}))
assert ok and abs(sec["top10"] - 19.2) < 0.01 and sec["cex_pct"] == 12.0 and sec["dead_pct"] == 30.0, (why, sec)
assert "exchange a parte 12.0%" in why and "Binance 2" in sec["cex_names"], why
assert all(o not in (INC, BIN) for o, _ in sec["rc_holders"])
assert D.SEC_LAST["XYZ"]["supply"] == 1e9
print("incinerator/exchange fuori dal top 10 OK")

# ---- 4. cluster_v2: niente "quota >= 10% = pool"; fuori solo exclude, morti e owner PDA
PDA = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"
assert K.on_curve(PDA) is False and K.on_curve(BIN) is True
wallets = []
i = 0
while len(wallets) < 3:
    b = os.urandom(32)
    if K.on_curve(C._b58(b)):
        wallets.append(C._b58(b))
WHALE, W2, LOCKV = wallets


def acc(owner, amt):
    raw = K._b58dec(owner) + int(amt).to_bytes(8, "little")
    return {"pubkey": "TA" + owner[:6], "account": {"data": [base64.b64encode(raw).decode(), "base64"]}}


async def rpc(method, params, url=None):
    if method == "getProgramAccounts":
        return [acc(WHALE, 150), acc(W2, 50), acc(PDA, 600), acc(INC, 100), acc(LOCKV, 100)]
    return None
rows, n, _ = asyncio.run(C.top_holders(rpc, "MINT", exclude={LOCKV}, token_program="TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"))
own = [o for o, _, _ in rows]
assert own == [WHALE, W2] and abs(rows[0][1] - 15.0) < 1e-9, rows      # balena al 15% resta nell'analisi
print("cluster_v2: balena >= 10% analizzata, pool/locker/PDA/morti esclusi OK")

# ---- 5. campi solo shadow
D.SEC_LAST["TOKpump"] = {"rc_holders": [("a", 5.0), ("b", 3.0), ("c", 2.0)], "supply": 1e9, "ts": time.time()}
mk = {"btc_1h": -0.5, "btc_4h": -3.5, "btc_24h": -2.0, "sol_1h": 1.4, "sol_4h": 2.0}
knife = [(now - 60 * (40 - i), 2 - i * 0.03, 2.01 - i * 0.03, 1.96 - i * 0.03, 1.97 - i * 0.03, 10) for i in range(40)]
f = D.shadow_fields("TOKpump", {"price": 1e-5, "liq": 600.0}, knife, -2.0, mk)
assert abs(f["pool_supply_pct"] - 3.0) < 1e-6 and f["top1_pct"] == 5.0 and f["top3_pct"] == 10.0, f
assert f["imp_top1_pct"] == round((1 - (0.03 / 0.08) ** 2) * 100, 1) and "thin_pool" in f["v84f_tags"], f
assert f["wick_entry"] == 1 and f["ret_15m"] <= -10 and "falling_knife" in f["v84f_tags"]
assert "risk_off" in f["v84f_tags"] and "rel_weak" in f["v84f_tags"] and f["supply_entry"] == 1e9
f2 = D.shadow_fields("NOSEC", {"price": 1.0, "liq": 1e5}, None, 1.0, {})
assert f2["v84f_tags"] == [] and f2["btc_4h"] is None and "v84f_error" not in f2, f2
assert D._pct_back([(0, 100.0), (3600, 110.0)], 3600, 3600) == 10.0
print("campi shadow (thin_pool, wick, range/lama, BTC/SOL) OK")

# ---- 6. uscite alternative sul price path
D.PATH_DIR = tempfile.mkdtemp()
D.CFG.update(STOP_LOSS_PCT=-12.0, TP1_PCT=30.0, TP1_FRACTION=0.5, TRAIL_ACTIVATE_PCT=30.0, TRAIL_DISTANCE_PCT=15.0,
             MAX_HOLD_MIN=180)
pp = [1.0, 1.5, 2.5, 3.2, 3.6, 2.8, 2.0]
with open(os.path.join(D.PATH_DIR, "MOON.jsonl"), "w") as fh:
    for k, p in enumerate(pp):
        fh.write(json.dumps({"t": 1000 + 60 * k, "k": "shadow", "p": p, "l": 50000, **({"ev": "entry"} if k == 0 else {})}) + "\n")
    fh.write(json.dumps({"t": 2000, "k": "shadow", "p": 2.0, "l": 0, "ev": "exit:test"}) + "\n")
a = D.alt_exits("MOON", "shadow", 1000)
assert a["alt_bars"] == 7 and a["alt_tp3x"] > a["alt_base"] and a["alt_moon15"] != a["alt_base"], a
# base: TP1 a +50 vende 50% (+25); trailing dal picco +260: soglia 206 -> esce a +180 col 50% (+90) = 115
assert abs(a["alt_base"] - 115.0) < 0.01, a
print("alt_tp3x / alt_moon15 OK", a)

# ---- 7. scarti GoPlus/honeypot sempre in shadow, Learner fermo
st = D.State()
_sp.clear()
D.hp_shadow(st, None, "MHP", {"symbol": "HP", "price": 1.0, "liq": 1e4}, {"score": 0.7, "soft": ["x"], "signals": "a|b"},
            {"level": "hard", "goplus_level": "hard", "goplus_flags": ["transfer_fee_upgradable", "fee_auth_known"],
             "why": "GoPlus: fee modificabile"}, "hard")
assert _sp[-1][0] == "MHP" and _sp[-1][2] == "goplus_hard" and "fee_auth_known" in _sp[-1][3] \
    and "fee_auth_known" in _sp[-1][4], _sp
D.hp_shadow(st, None, "MHP2", {"symbol": "HP2", "price": 1.0, "liq": 1e4}, {"score": 0.7}, {"level": "hard",
            "why": "nessun percorso"}, "hard")
assert _sp[-1][2] == "honeypot_hard"
for k in D.HP_SHADOW_KEYS:
    assert D.learn_shadow_step(st, k) is None
print("scarti GoPlus/honeypot in shadow OK")
print("TUTTI I TEST v8.4f OK")
