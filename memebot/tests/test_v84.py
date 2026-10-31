"""v8.4: Birdeye (piano gratuito): mascheramento chiave, budget CU e degrado, ATR primario con fallback,
trending come famiglia in piu, controllo incrociato. Offline: sessione HTTP finta, chiave finta."""
import asyncio, json, os, sys, time
FAKE = "FAKEKEY_test_1234567890"
os.environ["BIRDEYE_API_KEY"] = FAKE          # chiave FINTA: nessuna rete in questo test
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import desk as D
from sources import birdeye as B, trending_agg as T

assert "be" in D.SRC
B.STATE_FILE = B.Path("/tmp/_test_birdeye_usage.json")
T.STATE_FILE = T.Path("/tmp/_test_trending_agg_state.json")
for f in ("/tmp/_test_birdeye_usage.json",):
    if os.path.exists(f):
        os.remove(f)

# ---- 1. mascheramento
assert FAKE not in B.mask(f"errore url ... X-API-KEY: {FAKE} altro")
assert "***" in B.mask("headers {'X-API-KEY': 'abcdefgh123'}") and "abcdefgh123" not in B.mask("{'X-API-KEY': 'abcdefgh123'}")
print("mask OK")

# ---- sessione finta
NOW = time.time()
class Resp:
    def __init__(self, status, data):
        self.status, self.data = status, data
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False
    async def json(self, content_type=None):
        return self.data
class Sess:
    def __init__(self):
        self.calls, self.mode = [], {}
    def get(self, url, params=None, headers=None):
        assert headers["X-API-KEY"] == FAKE and headers["x-chain"] == "solana"
        path = url.replace(B.BASE, "")
        self.calls.append(path)
        st = self.mode.get(path, 200)
        if st != 200:
            return Resp(st, {"success": False})
        if path == "/utils/v1/credits":
            return Resp(200, {"success": True, "data": {"start_time": NOW - 86400, "end_time": NOW + 9 * 86400,
                                                         "usage": {"total": 100}, "remaining": {"total": 11500}}})
        if path == "/defi/ohlcv":
            t0 = int(NOW // 60) * 60 - 60 * 30
            items = [{"unixTime": t0 + 60 * i, "o": 1.0, "h": 1.05, "l": 0.95, "c": 1.0, "vUsd": 100} for i in range(30)]
            return Resp(200, {"success": True, "data": {"items": items}})
        if path == "/defi/token_trending":
            return Resp(200, {"success": True, "data": {"tokens": [{"address": M1, "rank": 1, "symbol": "AAA"},
                                                                   {"address": M9, "rank": 2, "symbol": "ZZZ"}]}})
        if path == "/defi/token_overview":
            return Resp(200, {"success": True, "data": {"liquidity": 3000, "holder": 40, "uniqueBuyer5m": 2,
                                                        "buy1h": 50, "sell1h": 0, "lastTradeUnixTime": NOW}})
        return Resp(404, None)
M1, M9 = "A" * 43 + "1", "Z" * 43 + "9"
logs = []
cfg = dict(D.CFG, BIRDEYE_MIN_GAP_SEC=0.0)
S = Sess()
be = B.Birdeye(S, cfg, logs.append, state_file=B.STATE_FILE)

# ---- 2. ATR da Birdeye + consumo ufficiale + budget del giorno
rows = asyncio.run(be.ohlcv_1m(M1, prio=True))
assert rows and len(rows) == 30 and rows[0][4] == 1.0
assert S.calls[:2] == ["/utils/v1/credits", "/defi/ohlcv"], S.calls
# budget: (11500 - 1500 riserva) / 9 giorni = ~1111 CU/giorno
assert 1100 < be.st["day_budget"] < 1120, be.st["day_budget"]
assert be.st["spent_day"] == {"credits": 1, "ohlcv": 25}
v = D.vol_from_candles(rows)
assert v and abs(v["atr"] - 10.0) < 0.01
assert asyncio.run(be.ohlcv_1m(M1)) is rows and S.calls.count("/defi/ohlcv") == 1   # cache 90 s
print("ohlcv/budget OK")

# ---- 3. quote, risparmio, riserva
be.st["spent_day"]["ohlcv"] = 0.5 * be.st["day_budget"]            # quota ATR finita
ok, why = be.allow("ohlcv", prio=False)
assert not ok and "quota" in why, why
assert be.allow("ohlcv", prio=True)[0]                              # i trade paper veri passano
be.st["spent_day"]["ohlcv"] = 0.85 * be.st["day_budget"]           # oltre l'80%: solo prioritarie
assert be.allow("overview", prio=False) == (False, "risparmio (solo prioritarie)") and be.level == "risparmio"
assert be.allow("overview", prio=True)[0]
be.st["spent_day"]["ohlcv"] = be.st["day_budget"]                  # budget del giorno finito
assert not be.allow("ohlcv", prio=True)[0] and be.level == "stop"
be.st["spent_day"] = {}
be.st["since_sync"] = 11500 - 1500 - 10                             # riserva mensile
assert be.allow("ohlcv", prio=True) == (False, "riserva mensile")
be.st["since_sync"] = 0
assert be.allow("ohlcv")[0]
# nuovo giorno: contatori azzerati, storico tenuto
be.st["day"] = "2000-01-01"; be.st["spent_day"] = {"ohlcv": 50}
be._roll_day(time.time())
assert be.st["spent_day"] == {} and be.st["history"][-1] == ["2000-01-01", 50]
print("degrado OK")

# ---- 4. 429 e 403 -> fallback senza chiamate
S.mode["/defi/ohlcv"] = 429
assert asyncio.run(be.ohlcv_1m("Q" * 44)) is None and be.cool_until > time.time()
n = len(S.calls)
assert asyncio.run(be.ohlcv_1m("R" * 44)) is None and len(S.calls) == n       # in pausa: nessuna chiamata
be.cool_until = 0; S.mode["/defi/ohlcv"] = 403
assert asyncio.run(be.ohlcv_1m("Q" * 44)) is None and "/defi/ohlcv" in be.denied
assert be.allow("ohlcv")[1] == "endpoint non incluso nel piano"
be.denied.clear(); S.mode.clear()
print("429/403 OK")

# ---- 5. entry_features: fonte ATR registrata, fallback a tick
D.BE_REF[0] = be
class H:
    s = S
    last429 = {}
    async def get(self, url, headers=None):
        return None
st = D.State()
D.ST_REF[0] = st
m = {"symbol": "AAA", "pair": "PAIRX", "liq": 50000, "price": 1.0, "chg_m5": 2.0, "vol_m5": 1000, "vol_h1_main": 9000}
be.cache.clear()
fe = asyncio.run(D.entry_features(st, H(), M1, m, "", prio=True))
assert fe["atr_src"] == "birdeye_candles_1m" and abs(fe["atr_1m_10"] - 10.0) < 0.01 and fe["atr_birdeye"] == "ok", fe
be.cache.clear(); be.st["spent_day"] = {"ohlcv": be.st["day_budget"]}   # budget finito -> fallback
now = time.time()
D.TICKS[M1] = D.deque([(now - 300 + 20 * i, 1.0 + 0.01 * (i % 3), 50000) for i in range(15)], maxlen=200)
fe2 = asyncio.run(D.entry_features(st, H(), M1, m, "", prio=True))
assert fe2["atr_src"] == "ticks" and fe2["atr_birdeye"] == "budget/errore", fe2
assert be.st["fallback_day"].get("ohlcv:budget del giorno", 0) >= 1
be.st["spent_day"] = {}
print("entry_features OK")

# ---- 6. trending Birdeye come famiglia in piu (conta per il multi-fonte)
async def get(url, headers=None):
    if "token-boosts/top" in url:
        return [{"chainId": "solana", "tokenAddress": M1, "totalAmount": 100}]
    if "tokens/v1" in url:
        return []
    return []
agg = T.TrendingAgg(birdeye_fetch=be.trending)
out = asyncio.run(agg.refresh(get))
by = {t["mint"]: t for t in out}
assert by[M1]["families"] == ["birdeye", "dex"] and by[M1]["n_fam"] == 2, by[M1]
assert by[M9]["families"] == ["birdeye"] and agg.last["counts"]["birdeye"] == 2
k = S.calls.count("/defi/token_trending")
asyncio.run(agg.refresh(get))
assert S.calls.count("/defi/token_trending") == k                 # cache 4 ore: nessun CU in piu
print("trending OK")

# ---- 7. controllo incrociato: mai filtro duro
ov = asyncio.run(be.overview(M1, prio=True))
fl = B.xcheck(ov, {"liq": 50000})
assert set(fl) == {"be_liq_min", "be_liq_mismatch", "be_holders_low", "be_buyers_low", "be_no_sells"}, fl
assert B.xcheck({"liquidity": 60000, "holder": 900, "uniqueBuyer5m": 30, "buy1h": 50, "sell1h": 40,
                 "lastTradeUnixTime": time.time()}, {"liq": 50000}) == []
assert B.xcheck(None, {}) == []
print("xcheck OK")

# ---- 8. la chiave non finisce mai su file o log
be.save()
txt = open(B.STATE_FILE).read()
assert FAKE not in txt and FAKE not in " ".join(logs)
for lim in ("BIRDEYE_XCHECK_SIZE_MULT", "BIRDEYE_TREND_SEC", "BIRDEYE_RESERVE_CU"):
    assert lim in D.OVR_LIMITS and lim in D.OVR_GLOBAL_MAP
assert D.CFG["BIRDEYE_XCHECK_MODE"] == "shadow" and D.CFG["BIRDEYE_MONTHLY_CU"] == 30000

# ---- 9. budget mensile Birdeye: 80% -> niente trending ne chiamate non prioritarie, 95% -> stop
be2 = B.Birdeye(S, dict(cfg), logs.append, state_file=B.Path("/tmp/_test_birdeye_usage2.json"))
be2.st.update(remaining=30000 * 0.15, remaining_ts=time.time(), since_sync=0, cycle_end=time.time() + 5 * 86400 - 60)
assert be2.cycle_level() == "risparmio"
assert not be2.allow("trending")[0] and not be2.allow("ohlcv", prio=False)[0]
be2.st.update(remaining=30000 * 0.04)
assert be2.cycle_level() == "stop" and not be2.allow("ohlcv", prio=True)[0] and not be2.allow("overview", prio=True)[0]
st2 = be2.status()
assert st2["cycle_pct"] >= 95 and st2["limit"] == 30000 and st2["days_left"] == 5, st2
print("budget mensile Birdeye OK")

# ---- 10. budget mensile Helius: ritmo = residuo / giorni, 80% solo core, 95% niente Helius
from sources import helius as H
os.environ["HELIUS_API_KEY"] = "FAKEHELIUS_abcdef123456"
H.USAGE_PATH = None
day0 = time.strftime("%Y-%m-%d")
H.U.update(day=day0, credits=0, cycle_start=day0, cycle_credits=0, day_start_cycle=0, official=None, hours={})
assert H.days_left() == 30 and abs(H.day_budget() - 1_000_000 * 0.95 / 30) < 1
H.U.update(day_start_cycle=500_000)
assert abs(H.day_budget() - 450_000 / 30) < 1
H.U.update(cycle_credits=810_000)
assert H.level() == "risparmio" and H.allow("getAccountInfo", "core") and not H.allow("getTransaction", "bulk")
H.U.update(cycle_credits=960_000)
assert H.level() == "stop" and not H.allow("getAccountInfo", "core")
H.U.update(cycle_start=time.strftime("%Y-%m-%d", time.localtime(time.time() - 31 * 86400)))
H._roll()
assert H.U["cycle_credits"] == 0 and H.level() == "ok"
assert H.apply_official({"creditsUsed": 123456, "creditCycle": {"start": day0, "end": "2099-01-01"},
                         "subscriptionDetails": {"creditsLimit": 1000000}})
assert H.U["cycle_credits"] == 123456 and H.summary()["official"]
H.U.update(official=None, cycle_start=day0, cycle_credits=0, day_start_cycle=0)
assert "FAKEHELIUS" not in H.mask("https://mainnet.helius-rpc.com/?api-" + "key=FAKEHELIUS_abcdef123456")
print("budget mensile Helius OK")

# ---- 11. Telegram: token pulito e mascherato, non bloccante, raggruppamento, max 1 msg/s
from sources import notify_telegram as N
TOK = "1234567890:" + "A" * 20 + "b_c-" + "Z" * 11
os.environ["TELEGRAM_BOT_TOKEN"] = TOK[:5] + " " + TOK[5:] + "\n"
assert N._token() == TOK
assert TOK not in N.mask(f"https://api.telegram.org/bot{TOK}/sendMessage errore")
chat = "/tmp/_test_tg_chat"
open(chat, "w").write("424242\n")
sent = []
def fake_sender(tok, chat_id, text, timeout):
    sent.append((time.time(), chat_id, text))
    return True, "", None
n = N.Notifier(enabled=True, chat_file=chat, min_gap=0.3, sender=fake_sender, logf=logs.append)
t0 = time.time()
for i in range(3):
    n.send(f"msg {i}")
assert time.time() - t0 < 0.1           # mai bloccante
for i in range(5):
    n.group("hard", f"TOK{i}: cluster", every=0.5, title="Rifiuti hard:")
time.sleep(2.5)
n.flush(5)
time.sleep(0.5)
assert sent and all(c == "424242" for _, c, _ in sent)
txt = "\n".join(x for _, _, x in sent)
assert "msg 0" in txt and "msg 2" in txt and "TOK0" in txt and "TOK4" in txt, txt
assert txt.count("Rifiuti hard:") == 1      # 5 rifiuti -> un solo messaggio
gaps = [b[0] - a[0] for a, b in zip(sent, sent[1:])]
assert all(g >= 0.29 for g in gaps), gaps
def bad_sender(tok, chat_id, text, timeout):
    raise RuntimeError(f"boom https://api.telegram.org/bot{tok}/sendMessage")
n2 = N.Notifier(enabled=True, chat_file=chat, min_gap=0.05, retries=1, sender=bad_sender, logf=logs.append)
n2.send("x")
time.sleep(3.5)
assert n2.status()["failed"] == 1 and "boom" in n2.status()["last_err"]
assert TOK not in " ".join(logs) and TOK not in n2.status()["last_err"]
assert not N.Notifier(enabled=True, chat_file="/tmp/_non_esiste_chat").enabled   # senza chat id: spento in silenzio
print("telegram OK")

# ---- 12. desk: avvisi 50/80/95 una volta per ciclo, riepilogo con budget
D.TG_REF[0] = None
rows = [{"name": "Birdeye", "unit": "CU", "used": 25000, "limit": 30000, "pct": 83.3, "today": 900, "proj": 29000,
         "pace": 800, "days_left": 4, "level": "risparmio", "cycle": "birdeye:1", "official": True}]
tgs = {}
out = D.budget_alerts(tgs, rows)
assert len(out) == 1 and "80%" in out[0] and tgs["alerts"]["birdeye:1"] == [50, 80]
assert D.budget_alerts(tgs, rows) == []
rows[0]["pct"] = 96.0
out = D.budget_alerts(tgs, rows)
assert len(out) == 1 and "95%" in out[0] and D.budget_alerts(tgs, rows) == []
rows[0]["cycle"] = "birdeye:2"; rows[0]["pct"] = 51
assert len(D.budget_alerts(tgs, rows)) == 1          # nuovo ciclo: si riparte
D.BE_REF[0] = be2
rt = D.tg_recap_text(st)
assert "Equity" in rt and "Posizioni aperte" in rt and "Birdeye:" in rt and "Helius:" in rt and "proiezione" in rt, rt
assert FAKE not in rt and TOK not in rt
assert D.CFG["TELEGRAM_ENABLED"] is True and D.CFG["HELIUS_MONTHLY_CREDITS"] == 1_000_000
print("avvisi budget e riepilogo OK")
D.BE_REF[0] = None

# ---- 13. cluster v8.4: hub burst + stima estesa (PILLSEY), CEX-fresh (GLITCH), combo (CATE), multi-gruppo, ricevuti
from sources import cluster_v2 as C
C._infra_cache.update(t=time.time() + 10**6, d={"HUBxx": "hub (>=1000 tx/h)", "CEXmexc": "MEXC"})
t0 = 1_800_000_000
rows = []
for i in range(60):   # 60 wallet nuovi finanziati dall'hub, comprano entro 5 min, quote uguali
    rows.append({"o": f"w{i}", "pct": 0.18, "ntx": 6, "src": "HUBxx", "ts": t0 + i * 10,
                 "acq": {"ts": t0 + i * 10 + 120, "slot": 1000 + i * 30, "swap": True}})
for i in range(90):   # stesso schema, funder non letto (oltre WALLET_TOP_N)
    rows.append({"o": f"u{i}", "pct": 0.16, "acq": {"ts": t0 + 100 + i * 6, "slot": 5000 + i * 40, "swap": True}})
r = C.analyze(rows)
assert r["hub_burst"] and r["max_group_wallets"] == 60 and r["est_group_wallets"] == 150, r
assert r["level"] == "hard" and "stimat" in r["why"], r["why"]
r0 = C.analyze(rows, cfg={"HUB_BURST_MIN": 999, "UNIFORM_HARD_WALLETS": 999, "UNIFORM_HARD_PCT": 999,
                              "SINGLE_FUNDER_MIN": 999})  # senza le regole
assert r0["level"] != "hard" and not r0["hub_burst"]
# GLITCH: 5 wallet nuovi da MEXC poco prima di comprare al lancio, quote 1.3-2.9% = 11.1%
g = [{"o": "L0", "pct": 0.5, "ntx": 50, "acq": {"ts": t0, "slot": 1, "swap": True}}]
for i, pc in enumerate((2.93, 2.47, 2.40, 2.05, 1.29)):
    g.append({"o": f"m{i}", "pct": pc, "ntx": 30, "src": "CEXmexc", "ts": t0 - 3600 * (i % 4) - 600,
              "acq": {"ts": t0 + 600 + i * 400, "slot": 100 + i * 1000, "swap": True}})
rg = C.analyze(g)
assert rg["level"] == "hard" and "MEXC" in rg["why"] and abs(rg["cexf_best"][0] - 11.14) < 0.01, rg
rg2 = C.analyze([dict(x, ts=(x.get("ts") or 0) - 86400 * 3) if x.get("src") else x for x in g])  # fondi vecchi
assert rg2["cexf_pct"] == 0 and rg2["level"] != "hard"
# v8.4c: quote sintetiche naturali/decrescenti (150 quote identiche = sciame tipo BNB, ora hard)
# controllo pulito sintetico: tanti holder piccoli, scollegati, wallet vecchi = pulito (DESSA NON e piu un esempio pulito)
dd = [{"o": f"d{i}", "pct": 0.3 / (i + 1) ** 0.5, "ntx": 100, "acq": {"ts": t0 + i * 977, "slot": i * 5000, "swap": True}} for i in range(150)]
assert C.analyze(dd)["level"] == "none"
# v8.4b: wallet nuovi (ntx controllato solo sui primi 60 di 150): 52 nuovi su 60 controllati (tipo DESSA, cattivo) = soft
dn = [dict(x, ntx=(5 if i < 52 else 150) if i < 60 else None) for i, x in enumerate(dd)]
rn = C.analyze(dn)
assert rn["level"] == "soft" and "nuovi" in rn["why"] and rn["fresh_share"] >= C.CFG["FRESH_SOFT"], rn
# HYPERCHAN (v8.4e: RUG il 31/10, -99.84%; 6a lezione negativa, MAI controllo pulito/vincente):
# un solo finanziatore per 59 dei 60 holder controllati = HARD (regola v8.4e 'un solo finanziatore >= 50%')
hy = [{"o": f"h{i}", "pct": 0.17 * (i + 1) ** -0.2, "ntx": 5, "src": "HubX", "ts": t0 + i * 3000,
       "acq": {"ts": t0 + i * 3000 + 7200 + i * 37, "slot": i * 9000, "swap": True}} for i in range(59)]
hy += [{"o": f"z{i}", "pct": 0.05, "ntx": None, "acq": {"ts": t0 + 900000 + i * 977, "slot": 9e6 + i * 5000, "swap": True}}
       for i in range(91)]
rh = C.analyze(hy)
assert rh["level"] == "hard" and "un solo finanziatore" in rh["why"], rh
assert rh["single_funder"]["wallets"] == 59 and rh["single_funder"]["checked"] == 59, rh["single_funder"]
# regola combinata v8.4b (8% / 12%, prima 10% / 15%): tipo CATE piu debole = hard; sotto soglia = soft
_cex = "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhLEfqhAF"   # Binance (INFRA_FUNDERS)
def _combo(each, ncex):
    cb = [{"o": f"t{g}{j}", "pct": each, "ntx": 500, "src": f"Fund{g}", "ts": t0 + g * 9999 + j * 500}
          for g in range(3) for j in range(3)]
    return cb + [{"o": f"x{j}", "pct": 1.0, "ntx": 500, "src": _cex, "ts": t0 + 90000 + j * 700} for j in range(ncex)]
_saved_infra = dict(C._infra_cache)
_NOUNI = {"UNIFORM_HARD_WALLETS": 999, "UNIFORM_HARD_PCT": 999}   # v8.4c: quote sintetiche identiche, regola a parte
C._infra_cache.update(t=9e12, d={_cex: "Binance"})   # lista exchange fissata per il test
rcb = C.analyze(_combo(0.95, 13), cfg=_NOUNI)      # collegati 8.55%, exchange/app 13%
assert rcb["level"] == "hard" and "insieme" in rcb["why"], rcb
assert C.analyze(_combo(0.95, 11), cfg=_NOUNI)["level"] == "soft"     # exchange/app 11% < 12%
assert C.analyze(_combo(0.80, 13), cfg=_NOUNI)["level"] == "soft"     # collegati 7.2% < 8%
C._infra_cache.clear(); C._infra_cache.update(_saved_infra)
# multi-gruppo: 3 gruppi >= 2% che fanno >= 20% = hard
mg = []
for k, pc in enumerate((8.0, 7.0, 6.0)):
    for j in range(3):
        mg.append({"o": f"g{k}{j}", "pct": pc / 3, "ntx": 5, "src": f"F{k}", "ts": t0 + k * 9999 + j * 500})
rm = C.analyze(mg)
assert rm["multi_groups"] == 3 and rm["level"] == "hard" and "gruppi collegati" in rm["why"], rm
# token ricevuti senza comprare: soft > 15%, hard > 30%
rc = [{"o": f"r{i}", "pct": 2.0, "ntx": 100, "acq": {"ts": t0 + i * 999, "slot": i * 9000, "from": f"S{i}"}} for i in range(9)]
assert C.analyze(rc)["level"] == "soft" and C.analyze(rc)["recv_pct"] == 18.0
rc += [{"o": f"q{i}", "pct": 2.0, "ntx": 100, "acq": {"ts": t0 + 50000 + i * 999, "slot": 99999 + i * 9000, "from": f"T{i}"}} for i in range(7)]
assert C.analyze(rc)["level"] == "hard"
C._infra_cache.update(t=0, d={})
print("cluster v8.4 OK")

# ---- 14. GoPlus: verdetti, errore = unknown, cache
from sources import goplus as GP
ok_r = {"balance_mutable_authority": {"authority": [], "status": "0"}, "closable": {"status": "0"},
        "freezable": {"status": "0"}, "mintable": {"status": "0"}, "transfer_hook": [], "transfer_fee": {},
        "metadata_mutable": {"status": "0"}, "default_account_state": "1", "non_transferable": "0", "trusted_token": 0}
assert GP.verdict(ok_r) == ([], [], [], 0.0)
bad = dict(ok_r, balance_mutable_authority={"status": "1"}, closable={"status": "1"}, freezable={"status": "1"},
           transfer_hook=[{"address": "x"}], non_transferable="1", default_account_state="2")
h, sft, fl, _ = GP.verdict(bad)
assert len(h) == 6 and "transfer_hook" in fl, h
h, sft, fl, fee = GP.verdict(dict(ok_r, metadata_mutable={"status": "1"}, transfer_fee={"fee_rate": "0.02"}))
assert not h and len(sft) == 2 and fee == 2.0
h, _, _, fee = GP.verdict(dict(ok_r, transfer_fee={"fee_rate": "0.12"}))
assert h and fee == 12.0
h, _, _, _ = GP.verdict(dict(ok_r, transfer_fee_upgradable={"status": "1"}))
assert h and "modificabile" in h[0]
assert GP.verdict(dict(ok_r, metadata_mutable={"status": "1"}, trusted_token=1))[1] == []

class GPResp:
    def __init__(self, st, body): self.status, self.body = st, body
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def json(self, content_type=None):
        if isinstance(self.body, Exception): raise self.body
        return self.body
class GPSess:
    def __init__(self, seq): self.seq, self.n = list(seq), 0
    def get(self, url, params=None, timeout=None):
        self.n += 1
        return GPResp(*self.seq.pop(0))
GP.CFG.update(MIN_GAP=0.0)
GP._cache.clear()
s1 = GPSess([(200, {"code": 1, "result": {"MINTx": ok_r}})])
assert asyncio.run(GP.check(s1, "MINTx"))["level"] == "ok"
assert asyncio.run(GP.check(s1, "MINTx"))["cached"] and s1.n == 1          # cache 10 min
s2 = GPSess([(200, ValueError("vuoto")), (500, {})])
r2 = asyncio.run(GP.check(s2, "MINTy"))
assert r2["level"] == "unknown" and s2.n == 2 and "MINTy" not in GP._cache   # errore != ok, nessuna cache
assert asyncio.run(GP.check(None, "MINTz"))["level"] == "unknown"
print("goplus OK")

# ---- 15. fee di trasferimento: soft sotto il 10%, rigida >= 10% o se il dev puo cambiarla
from sources import honeypot as HP
def mint_parsed(bps, auth=None, hook=None):
    ext = [{"extension": "transferFeeConfig", "state": {"transferFeeConfigAuthority": auth,
            "newerTransferFee": {"transferFeeBasisPoints": bps}, "olderTransferFee": {"transferFeeBasisPoints": bps}}}]
    return {"info": {"mintAuthority": None, "freezeAuthority": None, "decimals": 6, "extensions": ext}}
h, sft, info = HP.mint_flags(mint_parsed(125), {"MAX_TRANSFER_FEE_BPS": D.CFG["HONEYPOT_MAX_FEE_BPS"]})
assert not h and sft and info["fee_bps"] == 125
h, _, _ = HP.mint_flags(mint_parsed(999), {"MAX_TRANSFER_FEE_BPS": D.CFG["HONEYPOT_MAX_FEE_BPS"]})
assert not h
h, _, _ = HP.mint_flags(mint_parsed(1000), {"MAX_TRANSFER_FEE_BPS": D.CFG["HONEYPOT_MAX_FEE_BPS"]})
assert h
h, _, _ = HP.mint_flags(mint_parsed(100, auth="DevAuth1111"), {"MAX_TRANSFER_FEE_BPS": D.CFG["HONEYPOT_MAX_FEE_BPS"]})
assert h and "authority" in h[0]
print("fee di trasferimento OK")

# ---- 16. penalita eta
mk = lambda d, vl=1.0, ch=15.0: {"age_min": d * 1440, "vol_h1": 100000 * vl, "liq": 100000, "mcap": 1e7, "chg_h1": ch}  # noqa: E731
assert D.age_penalty(mk(0.5))[0] == 0.0
assert D.age_penalty(mk(2))[0] == D.CFG["AGE_PEN_1_3D"] and D.age_penalty(mk(2, 0.01, 0))[1] is None
p5, rj, _ = D.age_penalty(mk(5), atr=1.5)
assert p5 == D.CFG["AGE_PEN_3_7D"] and rj is None
assert D.age_penalty(mk(14, 0.1, 3.0), atr=0.4)[1]                           # vecchio e fermo: scartato
p14, rj, inf = D.age_penalty(mk(14, 3.0, 40.0), atr=2.5)
assert rj is None and inf.get("eased") and abs(p14 - D.CFG["AGE_PEN_7_30D"] * D.CFG["AGE_EASE_MULT"]) < 1e-9
assert D.age_penalty(mk(60), atr=1.5)[0] == D.CFG["AGE_PEN_30D"]
assert D.age_penalty({"age_min": None})[0] == 0.0
print("penalita eta OK")

# ---- 17. lezioni
ids = {x["id"]: x for x in D.LABEL_SEEDS}
assert {"beast-2026-10-30", "dessa-2026-10-30", "dessa-2026-10-30-cattivo", "pillsey-2026-10-30",
        "glitch-2026-10-30"} <= set(ids)
assert ids["dessa-2026-10-30-cattivo"]["label"] == "cluster" and "CATTIVO" in ids["dessa-2026-10-30-cattivo"]["note"]
assert ids["dessa-2026-10-30-cattivo"]["pnl_pct"] is None
assert ids["pillsey-2026-10-30"]["label"] == "cluster" and "SOSPETTO" in ids["glitch-2026-10-30"]["note"]
print("lezioni OK")

# ---- 18. v8.4c: honeypot sconosciuto = niente acquisto, shadow + nuovo tentativo; GoPlus con la sessione vera
import inspect
assert D.CFG["HONEYPOT_UNKNOWN_ACTION"] == "skip"
assert 'SRC["goplus"].check(getattr(http, "s", http), mint)' in inspect.getsource(D.honeypot_check)

assert 'soft_size(st, "honeypot_unknown")' not in open(D.__file__).read()   # niente piu size ridotta
st18 = D.State()
calls = []
_orig_spawn = D.spawn_shadow
D.spawn_shadow = lambda *a, **k: calls.append((a[4], k.get("tag")))
mintx = "HPunk1111111111111111111111111111111111pump"
st18.processed[mintx] = time.time()
m18 = {"symbol": "HPX", "price": 1.0, "liq": 50000}
for i in range(D.CFG["HONEYPOT_RETRY_MAX"]):
    assert D.honeypot_unknown_skip(st18, None, mintx, m18, {"score": 0.6}, {"why": "GoPlus non disponibile"})
    assert mintx not in st18.processed and 0 < st18.cooldown[mintx] - time.time() <= D.CFG["HONEYPOT_RETRY_MIN"] * 60 + 1
    st18.processed[mintx] = time.time()
assert not D.honeypot_unknown_skip(st18, None, mintx, m18, {"score": 0.6}, {"why": "x"})   # oltre il massimo
assert mintx in st18.processed
assert calls and all(c == (["honeypot_unknown"], "honeypot_unknown") for c in calls)
D.spawn_shadow = _orig_spawn
print("honeypot sconosciuto = skip + shadow + retry OK")

# ---- 19. v8.4c: ticker impostori
assert D.ticker_impostor("BNB", "EYqygbhZjP7EbM4fSn8devctnksAvPAD1Bt57XBFpump") == "BNB"
assert D.ticker_impostor("$btc", "ra4qZ6xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxpump") == "BTC"
assert D.ticker_impostor("HYPE", "Dn9ucMxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxpump") == "HYPE"
assert D.ticker_impostor("HYPE", "98sMhvDwXj1RQi5c5Mndm3vPe9cBqPrbLaufMXFNMh5g") is None
assert D.ticker_impostor("BNB", "9gP2kCy3wA1ctvYWQk75guqXuHfrEomqydHLtcTCqiLa") is None
assert D.ticker_impostor("USDC", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v") is None
for ok_sym in ("HYPERCHAN", "Bonnie", "DESSA", "BNBX", "PILLSEY", "BEAST"):
    assert D.ticker_impostor(ok_sym, "Zzzz1111111111111111111111111111111111pump") is None, ok_sym
assert D.CFG["TICKER_IMPOSTOR_ACTION"] == "block"
print("ticker impostori OK")

# ---- 20. v8.4c: cluster v2, quote uniformi (sciame BNB) e cluster a stella
def _rows(pcts, src=None):
    return [{"o": f"W{i:04d}", "pct": p, "ntx": None, **({"src": src[i]} if src and src[i] else {})}
            for i, p in enumerate(pcts)]
bnb_like = _rows([0.1035 - 0.00008 * i for i in range(150)])          # 150 holder ~0.10% (BNB su Bubblemaps)
r = C.analyze(bnb_like, {}, {}, {})
assert r["level"] == "hard" and "uniformi" in r["why"] and r["uniform"]["wallets"] >= 100, r
natural = _rows([4.0 / (i + 1) ** 0.9 for i in range(150)])           # distribuzione naturale (legge di potenza)
r = C.analyze(natural, {}, {}, {})
assert r["level"] != "hard" and r["uniform"]["wallets"] < C.CFG["UNIFORM_SOFT_WALLETS"], r
dev_then_swarm = _rows([5.0, 3.0] + [0.2] * 30 + [0.01] * 100)        # dev/CEX in cima, sciame sotto
assert C.analyze(dev_then_swarm, {}, {}, {})["level"] == "hard"
few = _rows([3.0, 2.0] + [0.5] * 9 + [0.05 / (i + 1) for i in range(100)])   # 9 uguali: sotto soglia
assert C.uniform_plateau(few)[0] < C.CFG["UNIFORM_HARD_WALLETS"] and C.analyze(few, {}, {}, {})["level"] != "hard"
srcs = [f"F{i // 10}" if i < 30 else None for i in range(150)]         # 3 stelle da 10 wallet
r = C.analyze(_rows([4.0 / (i + 1) ** 0.9 for i in range(150)], srcs), {}, {}, {})
assert r["level"] == "hard" and "stella" in r["why"] and len(r["stars"]) == 3, r
srcs1 = [("F0" if i < 10 else None) for i in range(150)]               # una sola stella: soft
r = C.analyze(_rows([4.0 / (i + 1) ** 0.9 for i in range(150)], srcs1), {}, {}, {})
assert "stella" in r["why"] and len(r["stars"]) == 1
print("quote uniformi e cluster a stella OK")
ids = {x["id"]: x for x in D.LABEL_SEEDS}
assert ids["bnb-2026-10-30"]["label"] == "cluster" and ids["bnb-2026-10-30"]["pnl_pct"] is None
assert "CATTIVO" in ids["bnb-2026-10-30"]["note"] and "MAI esempio pulito" in ids["bnb-2026-10-30"]["note"]
print("lezione BNB OK")

# ---- 21. v8.4d: nomi per esteso dei grandi asset (Bitcoin HytpiyuR..pump passato come analyst_momentum)
FAKE_M = "HytpiyuR3sVXGB5UqoqrnThCPH2X1tWxxxxxxxxpump"
for sym, name in (("Bitcoin", "Bitcoin"), ("BTCX", "Bitcoin"), ("ETHEREUM", "x"), ("Solana", None), ("BINANCE", None),
                  ("Tether", None), ("tok", "Wrapped Bitcoin"), ("SOLCOIN", None), ("x", "The Ethereum Token"),
                  ("Dogecoin", None), ("XRP", None), ("cat", "Binance Coin"), ("usdcoin", None)):
    assert D.ticker_impostor(sym, FAKE_M, name), (sym, name)
for sym, name in (("PUMPCAT", "Pump Cat"), ("BITCAT", "Bitcoin Cat"), ("SOLCAT", "Solana Cat"), ("HYPERCHAN", "HyperChan"),
                  ("Bonnie", "Bonnie"), ("ETHAN", "Ethan"), ("TRONALD", "Tronald Dump"), ("AAPL", "Apple"),
                  ("Apple", "Apple Inc"), ("NVDA", "Nvidia")):
    assert D.ticker_impostor(sym, FAKE_M, name) is None, (sym, name)
assert D.ticker_impostor("Bitcoin", "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh", "Wrapped Bitcoin") is None   # ufficiale
assert D.ticker_impostor("USDC", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "USD Coin") is None
# azioni: NON bloccate, solo annotate
assert D.stock_ticker("AAPL") == "AAPL" and D.stock_ticker("Apple") == "APPLE" and D.stock_ticker("x", "Nvidia") == "NVIDIA"
assert D.stock_ticker("PUMPCAT", "Pump Cat") is None
_ev = []
_ow = D.write_event
D.write_event = lambda *a, **k: _ev.append(a)
assert D.note_stock_outcome("shadow", "AAPL", "5Afz2m", 38.15, "trailing") == "AAPL" and _ev and _ev[0][1] == "ticker_azione"
assert D.note_stock_outcome("shadow", "PUMPCAT", "x", 1.0, "y") is None and len(_ev) == 1
D.write_event = _ow
print("nomi impostori e ticker azioni OK")

# ---- 22. v8.4d: "nessun prezzo" dopo una pausa del processo e con seconda fonte
st22 = D.State()
D.LOOP_LAST.clear()
t_ = time.time()
a_ = {"sym": "A", "last_ok": t_ - 1600}
assert D.stall_reset(st22, "shadow", [a_], now=t_ - 1500) == 0.0          # primo giro: nessun confronto
assert D.stall_reset(st22, "shadow", [a_], now=t_) >= 1400 and a_["last_ok"] == t_   # 25 min di pausa: riparte
assert D.stall_reset(st22, "shadow", [a_], now=t_ + 30) == 0.0
class FakeHttp:
    def __init__(self, resp): self.resp, self.urls = resp, []
    async def get(self, url, headers=None):
        self.urls.append(url); return self.resp
def _sh(): return {"sym": "S", "last_ok": t_ - 1000, "last_price": 1.0}
alive, dead = _sh(), _sh()
fh = FakeHttp({"MA": {"usdPrice": 2.5}})
vd = asyncio.run(D.verify_no_price(st22, fh, {"MA": alive, "MD": dead}, now=t_))
# review Claude (compito 4): l'assenza dal prezzo Jupiter da sola non basta piu per 'dead' (serve il preventivo
# di vendita 'no_route'); senza preventivo verificabile e attesa
assert vd == {"MA": "alive", "MD": "wait"} and alive["last_price"] == 2.5 and alive["last_ok"] == t_, vd
assert "lite-api.jup.ag/price/v3" in fh.urls[0] and len(fh.urls) == 1
_orc = D.noprice_route_check
D.noprice_route_check = lambda *_a, **_k: asyncio.sleep(0, result="no_route")
dead = _sh()
assert asyncio.run(D.verify_no_price(st22, fh, {"MD": dead}, now=t_)) == {"MD": "dead"}   # prezzo assente + no_route
D.noprice_route_check = _orc
w = _sh()
for i in range(D.CFG["NOPRICE_MAX_WAIT"] - 1):
    assert asyncio.run(D.verify_no_price(st22, FakeHttp(None), {"MW": w}, now=t_)) == {"MW": "wait"}
    assert t_ - w["last_ok"] < 900                                        # nessuna chiusura in attesa
assert asyncio.run(D.verify_no_price(st22, FakeHttp(None), {"MW": w}, now=t_)) == {"MW": "invalid"}
# chiusura come DATI NON VALIDI: non rug, al prezzo noto, nessun esito al Learner
base = {"t": t_ - 3600, "sym": "S", "reason": "r", "keys": ["analyst_momentum"], "flags": [], "score": 0.0, "thr": 0.5,
        "price": 1.0, "liq_entry": 5e4, "liq": 5e4, "rt": 2.0, "peak_pct": 0.0, "last_ok": t_ - 1000, "stop_hits": 0,
        "tokens": 100.0, "tokens_entry": 100.0, "cost": 100.0, "realized": 0.0, "last_price": 0.9,
        "entry_mark": 1.0, "tp_stage": 0}
sh_inv = dict(base, np_invalid=True)
dec = D.shadow_decide(sh_inv, t_)
assert dec[0].startswith("DATI NON VALIDI") and dec[2] is False and dec[3] is False, dec
_rec = []
_oro, _ors, _ocsv = D.record_outcome, D.record_signals, D.write_csv_row
D.record_outcome = lambda *a, **k: _rec.append(a)
D.record_signals = lambda *a, **k: _rec.append(a)
_rows22 = []
D.write_csv_row = lambda path, row: _rows22.append(row)
D._close_shadow_row(st22, "MINV", sh_inv, -10.0, False, dec[0], t_)
assert not _rec and _rows22[-1]["valido"] == 0 and _rows22[-1]["rug"] == 0
sh_dead = dict(base, np_src="jupiter")
dd_ = D.shadow_decide(sh_dead, t_)
assert dd_[3] is True and "confermato" in dd_[0]
D._close_shadow_row(st22, "MDEAD", sh_dead, -100.0, True, dd_[0], t_)
assert _rec and _rows22[-1]["valido"] == 1 and _rows22[-1]["rug"] == 1
D.record_outcome, D.record_signals, D.write_csv_row = _oro, _ors, _ocsv
assert "valido" in D.SHADOW_FIELDS
# giro completo del loop shadow dopo 25 min di pausa: DexScreener vuoto, Jupiter vivo -> nessuna chiusura a -100
st23 = D.State()
st23.shadow_trades["MLIVE"] = dict(base, t=time.time() - 1700, last_ok=time.time() - 1600, last_price=1.0)
st23.shadow_trades["MLIVE2"] = dict(base, t=time.time() - 1700, last_ok=time.time() - 1600, last_price=1.0)
D.LOOP_LAST.clear()
D.LOOP_LAST["shadow"] = time.time() - 1600
_calls = {"n": 0}
_osleep, _odb, _osave = D.asyncio.sleep, D.dex_batch, D.save_state
async def _sl(_s):
    _calls["n"] += 1
    if _calls["n"] > 2:
        raise SystemExit
async def _db(http, mints): return {}
D.asyncio.sleep, D.dex_batch, D.save_state = _sl, _db, (lambda st: None)
_closed = []
_ocl = D._close_shadow_row
D._close_shadow_row = lambda st, mint, sh, pnl, rug, why, now=None: _closed.append((mint, pnl, why))
try:
    asyncio.run(D.shadow_trade_agent(st23, FakeHttp({"MLIVE": {"usdPrice": 1.01}})))
except SystemExit:
    pass
D.asyncio.sleep, D.dex_batch, D.save_state, D._close_shadow_row = _osleep, _odb, _osave, _ocl
assert not _closed, _closed
assert set(st23.shadow_trades) == {"MLIVE", "MLIVE2"} and time.time() - st23.shadow_trades["MLIVE2"]["last_ok"] < 120   # stall_reset
print("nessun prezzo: pausa + seconda fonte + dati non validi OK")

print("TUTTI I TEST v8.4 OK")
