"""v8.5e: fascia di punteggio 0.45-0.50 (PAPER, size x0.5, tag score_band) e shadow buysell_relax (1.20-1.30).
Da lanciare SOLO su una copia in /tmp."""
import asyncio
import desk as D

assert D.DATA_DIR.startswith("/tmp"), "test solo su copia in /tmp"
D.CFG["MODE"] = "paper"
D.CFG["SIGNALS_ENABLED"] = False
D.NARR.current = lambda st: []
D.NARR.blocklist = set()
async def no_candles(http, pool): return None
D.fetch_candles = no_candles
opened = []
async def fake_open(st, http, mint, m, keys, reason, score, flags, now=None, tag=None, signals=None):
    opened.append((mint, keys, tag)); st.shadow_trades[mint] = {"tag": tag}
D.open_shadow = fake_open
CL = {"level": "none", "why": "", "cluster_pct": 0.0}
async def fake_scan2(st, http, mint, sec, sym="?", rt=False):
    return dict(CL)
D.cluster2_scan = fake_scan2
SC = {"v": 0.0}
D.final_score = lambda st, f: (SC["v"] + (D.CFG["NO_CHART_PENALTY"] if True else 0), "r", 0.5)
mk = {"symbol": "S", "name": "s", "price": 1.0, "liq": 500_000.0, "chg_h1": 10.0, "chg_m5": 2.0, "buys_m5": 30,
      "sells_m5": 10, "vol_m5": 50_000.0, "vol_h1_main": 100_000.0, "buys_h1": 300, "sells_h1": 100,
      "vol_h1": 100_000.0, "twitter": 1, "telegram": 1, "website": 1, "pair": "P", "url": "", "src": "pump.fun"}


HTTP = {"v": None}


async def run(cands, st):
    qi, qo = asyncio.Queue(), asyncio.Queue()
    for c in cands:
        await qi.put(c)
    t = asyncio.ensure_future(D.analyst_agent(st, HTTP["v"], qi, qo))
    await asyncio.sleep(0.5)
    t.cancel()
    out = []
    while not qo.empty():
        out.append(qo.get_nowait())
    return out


def one(mint, score, m=None, sec=None, so=None):
    D.CL2["cache"].clear(); D.CL2["tasks"].clear()
    SC["v"] = score
    st = D.State(); st.threshold = 0.50
    c = {"mint": mint, "m": dict(m or mk), "sec": sec or {"soft": [], "lp_status": "ok"}, "filter_ok": True}
    if so:
        c.update(shadow_only=so, shadow_keys=["x"])
    return asyncio.run(run([c], st)), st

# (D) fascia: 0.47 -> acquisto PAPER a meta size con tag score_band
out, st = one("B1", 0.47)
assert len(out) == 1 and "score_band" in out[0]["soft"] and abs(out[0]["size_mult"] - 0.5) < 1e-9, out
assert "score_band" in out[0]["signals"] and st.stats["score_band"] == 1
# sopra soglia: nessun tag
out, _ = one("B2", 0.55)
assert len(out) == 1 and "score_band" not in out[0]["soft"] and out[0]["size_mult"] == 1.0
# sotto la fascia: scarto
out, st = one("B3", 0.40)
assert out == [] and st.reject_reasons.get("punteggio basso") == 1
# sicurezza non passata (shadow_only dal Filter): mai acquisto
out, _ = one("B4", 0.47, so="grave")
assert out == []
# cluster hard / unknown: mai acquisto nemmeno nella fascia
CL.update(level="hard", why="x"); out, _ = one("B5", 0.47); assert out == []
CL.update(level="unknown", why="x"); out, _ = one("B6", 0.47); assert out == []
CL.update(level="none", why="")
# non paper: niente fascia
D.CFG["MODE"] = "live"; out, _ = one("B7", 0.47); D.CFG["MODE"] = "paper"
assert out == []
# fascia + ripiego RugCheck: le due riduzioni si sommano (x0.25), cluster v2 richiesto
# review Claude (rugcheck-fallback-review): col ripiego servono anche holder >= MIN_HOLDERS (da cluster v2) e creatore noto
out, _ = one("B8", 0.47, sec={"soft": [], "rc_down": True})
assert out == [], out   # holder e creatore non verificati = dubbio, niente acquisto
class _H:
    async def get(self, *_a, **_k): return None
CRH = {"creator": "C" * 43}
class _Crh:
    @staticmethod
    async def lookup(get, mint, creator=None, now=None, cfg=None):
        return {"level": "ok", "pen": 0.0, "why": "ok", "creator": CRH["creator"], "prev": 0, "dead": 0}
_old_crh = D.SRC.get("crh"); D.SRC["crh"] = _Crh; HTTP["v"] = _H()
CL.update(holders_total=500)
out, _ = one("B8" + "x" * 36 + "pump", 0.47, sec={"soft": [], "rc_down": True})
assert len(out) == 1 and abs(out[0]["size_mult"] - 0.25) < 1e-9 and "rugcheck_down" in out[0]["soft"], out
# creatore in lista nera col ripiego: niente acquisto
_st_init = D.State.__init__
def _init_bl(self, *a, **k):
    _st_init(self, *a, **k); self.blacklist = set(self.blacklist) | {CRH["creator"]}
D.State.__init__ = _init_bl
out, _ = one("B9" + "x" * 36 + "pump", 0.47, sec={"soft": [], "rc_down": True})
assert out == [], out
D.State.__init__ = _st_init
CL.pop("holders_total"); HTTP["v"] = None
if _old_crh is not None:
    D.SRC["crh"] = _old_crh
else:
    D.SRC.pop("crh", None)

# (E) acq/vend 5m 1.25 -> solo shadow buysell_relax; 1.10 -> vecchio shadow analyst_momentum
opened.clear()
out, st = one("E1", 0.60, m=dict(mk, buys_m5=25, sells_m5=20))
assert out == [] and opened and opened[-1][2] == "buysell_relax" and st.stats["buysell_relax"] == 1, opened
out, _ = one("E2", 0.60, m=dict(mk, buys_m5=22, sells_m5=20))
assert out == [] and opened[-1][0] == "E2" and opened[-1][2] == "analyst_momentum", opened
out, _ = one("E3", 0.60, m=dict(mk, buys_m5=25, sells_m5=20), so="grave")
assert out == [] and opened[-1][0] != "E3"   # sicurezza non ok: niente buysell_relax
assert D.shadow_tag(["buysell_relax"], []) == "buysell_relax"
assert {"buysell_relax", "score_band"} <= D.MEASURE_ONLY_KEYS
print("test_v85e OK")

# Executor: la fascia passa solo con honeypot ok, GoPlus ok, nessun soft, in PAPER
rej = []
D.reject = lambda st, mint, sym, reason, agent="Filter", cat=None, quiet=False: rej.append(cat)
D.CFG["DYN_STOP_MODE"] = "off"; D.CFG["PRICE_PATHS"] = False
class Br:
    realistic = False
    async def cash_usd(self, st): return 1000.0
HP = {"level": "ok", "why": "", "goplus_level": "warn", "soft": []}
async def fake_hp(st, http, mint, size, sec): return dict(HP)
D.honeypot_check = fake_hp
D.SRC["honeypot"] = object()
D.CFG["HONEYPOT_CHECK"] = True
cb = {"candles": None, "score": 0.47, "soft": ["score_band"], "size_mult": 0.5, "sec": {"soft": [], "lp_status": "ok"}, "signals": ""}
mm = dict(mk)
st = D.State()
asyncio.run(D._execute_buy(st, Br(), dict(cb), "XB1", mm))
assert rej[-1] == "fascia punteggio (dubbio)" and opened[-1][2] == "score_band", (rej, opened[-1])
D.CFG["HONEYPOT_CHECK"] = False
asyncio.run(D._execute_buy(st, Br(), dict(cb), "XB2", mm))
assert rej[-1] == "fascia punteggio (dubbio)" and len(rej) == 2
D.CFG["HONEYPOT_CHECK"] = True
HP.update(goplus_level="ok", soft=["Token-2022 x"])
asyncio.run(D._execute_buy(st, Br(), dict(cb), "XB3", mm))
assert rej[-1] == "fascia punteggio (dubbio)" and len(rej) == 3
print("test_v85e Executor OK")
