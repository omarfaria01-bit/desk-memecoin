"""v8.1: limiti override, crollo relativo, stop dinamico (fallback), colonne CSV, price paths. Offline."""
import asyncio, csv, json, os, time
import desk as D

P = D.parse_overrides
base = D.OVR.values()
H = {"MINTA"}
# ---- limiti degli override
r = P(json.dumps({"global": {"STOP_LOSS_PCT": -45}}), base, {}, H); assert r["globals"]["STOP_LOSS_PCT"] == -30
r = P(json.dumps({"global": {"STOP_LOSS_PCT": -25}}), base, {}, H); assert r["globals"]["STOP_LOSS_PCT"] == -25
r = P(json.dumps({"positions": {"MINTA": {"stop_loss_pct": -50}}}), base, {}, H)
assert r["positions"]["MINTA"]["STOP_LOSS_PCT"] == -30
r = P(json.dumps({"global": {"DYN_STOP_MODE": "ON", "DYN_STOP_ATR_MULT": 20, "DYN_STOP_MIN": -1, "DYN_STOP_MAX": -60,
                             "DYN_STOP_SIZE_SCALE": False}}), base, {}, H)
g = r["globals"]
assert g["DYN_STOP_MODE"] == "on" and g["DYN_STOP_ATR_MULT"] == 6 and g["DYN_STOP_MIN"] == -3 and g["DYN_STOP_MAX"] == -30
assert g["DYN_STOP_SIZE_SCALE"] == 0, g
r = P(json.dumps({"global": {"DYN_STOP_MODE": "live", "DYN_STOP_MAX": 20}}), base, {}, H)
assert r["globals"]["DYN_STOP_MODE"] == base["DYN_STOP_MODE"] and r["globals"]["DYN_STOP_MAX"] == -20
assert any("non valido" in n for _, n in r["notes"])
r = P(json.dumps({"global": {"DYN_STOP_MODE": None}}), dict(base, DYN_STOP_MODE="on"), {}, H)
assert r["globals"]["DYN_STOP_MODE"] == "shadow"
assert D.CFG["DYN_STOP_MODE"] == "shadow"   # default attivo: solo registrato
print("override OK")

# ---- crollo relativo allo stop
assert D.crash_level(-12) == -15 and D.crash_level(-10) == -15 and D.crash_level(-3) == -15
assert D.crash_level(-20) == -30 and D.crash_level(-30) == -45 and D.crash_level(-15) == -22.5
sh = {"t": time.time() - 60, "last_ok": time.time(), "liq": 1e5, "liq_entry": 1e5, "tokens": 100.0, "tokens_entry": 100.0,
      "entry_mark": 1.0, "last_price": 0.84, "peak_pct": 0.0, "stop_hits": 0, "tp_stage": 0, "realized": 0.0, "cost": 100.0}
old = D.CFG["STOP_LOSS_PCT"]
D.CFG["STOP_LOSS_PCT"] = -12.0
pnl = D.mark_pnl_pct(sh)
dec = D.shadow_decide(dict(sh, last_price=0.80))  # circa -20%: crollo immediato come in v8 (stop -12 -> -15)
assert dec and dec[0].startswith("crollo"), dec
D.CFG["STOP_LOSS_PCT"] = -25.0
dec = D.shadow_decide(dict(sh, last_price=0.80))   # stop -25: a -20% niente crollo (scatterebbe a -37.5)
assert dec is None, dec
D.CFG["STOP_LOSS_PCT"] = old
print("crash OK")

# ---- stop dinamico
assert D.dyn_stop_calc(None, -12) == (-12, 1.0)
assert D.dyn_stop_calc(float("nan"), -12) == (-12, 1.0)
assert D.dyn_stop_calc(1.0, -12) == (-8.0, 1.0)            # 3 x 1 = 3 -> limitato a -8; size mai maggiorata
s, m = D.dyn_stop_calc(7.0, -12); assert s == -21.0 and abs(m - 12 / 21) < 1e-3
assert D.dyn_stop_calc(50.0, -12) == (-30.0, 0.4)
now = 1_800_000_000.0
cur = int(now // 60) * 60
mk = lambda k, rng: (cur - 60 * k, 1.0, 1.0 + rng, 1.0 - rng, 1.0, 100.0)
rows = [mk(k, 0.02) for k in range(12, 0, -1)] + [(cur, 1, 5, 0.1, 1, 1)]  # candela in corso esclusa
v = D.vol_from_candles(rows, now); assert v["bars"] == 10 and abs(v["atr"] - 4.0) < 1e-6 and v["src"] == "candles_1m", v
v = D.vol_from_candles([mk(3, 0.01), mk(2, 0.01), mk(1, 0.01)], now)   # solo 3 minuti di storia
assert v["bars"] == 3 and v["src"] == "candles_partial" and abs(v["atr"] - 2.0) < 1e-6, v
assert D.vol_from_candles([mk(2, 0.01), mk(1, 0.01)], now) is None   # meno di 3 candele
ticks = [(now - 50, 1.00, 1), (now - 35, 1.02, 1), (now - 20, 0.99, 1), (now - 5, 1.01, 1)]
v = D.vol_from_ticks(ticks, now); assert v and v["src"] == "ticks" and v["atr"] > 0, v
assert D.vol_from_ticks(ticks[:2], now) is None

st = D.State()
m = {"symbol": "TST", "pair": "POOLX", "price": 1.0, "liq": 50000.0, "chg_m5": 4.0, "vol_m5": 100.0, "vol_h1_main": 600.0}


class NoHttp:
    last429 = {}

    async def get(self, *a, **k):
        return None


D.TICKS.pop("MX", None)
fe = asyncio.run(D.entry_features(st, NoHttp(), "MX", m, "src:x|kol:unipcs:S|kol_thesis:unipcs|proj_score:0.7|"
                                  "src:gmgn_trending|gmgn_rank:4", soft=["cluster"], narr=0.02, size_mult=0.4,
                                  base_stop=-12))
assert fe["atr_1m_10"] is None and fe["atr_bars_used"] == 0 and fe["atr_src"] == "none"
assert fe["dyn_stop_shadow"] == -12 and fe["stop_applied"] == -12 and fe["size_mult"] == 0.4   # tutto mancante: stop base
assert fe["kol_count"] == 1 and fe["kol_thesis"] == 1 and fe["project_score"] == 0.7 and fe["gmgn_trend"] == 4
assert fe["soft_flags"] == ["cluster"] and fe["liq_entry"] == 50000 and fe["ret_5m"] == 4.0
D.TICKS["MX"] = D.deque([(time.time() - 40 + i * 10, 1 + 0.01 * (i % 2), 5e4) for i in range(4)])
fe = asyncio.run(D.entry_features(st, NoHttp(), "MX", m, "", base_stop=-12))
assert fe["atr_src"] == "ticks" and fe["atr_1m_10"] is not None and fe["stop_applied"] == -12   # shadow: base
D.CFG["DYN_STOP_MODE"] = "on"
fe_on = asyncio.run(D.entry_features(st, NoHttp(), "MX", m, "", base_stop=-12))
assert fe_on["stop_applied"] == fe_on["dyn_stop_shadow"]
D.CFG["DYN_STOP_MODE"] = "shadow"
D.CANDLE_CACHE["POOLC"] = (time.time(), [(int(time.time() // 60) * 60 - 60 * k, 1, 1.05, 0.95, 1.0, 10) for k in (3, 2, 1)])
fe = asyncio.run(D.entry_features(st, NoHttp(), "MC", dict(m, pair="POOLC"), "", base_stop=-12))
assert fe["atr_bars_used"] == 3 and abs(fe["atr_1m_10"] - 10.0) < 1e-6 and fe["dyn_stop_shadow"] == -30.0, fe
assert fe["stop_applied"] == -12
# ---- v8.1b: TP1 / trailing dinamici (solo registrati)
assert D.CFG["DYN_TP_MODE"] == "shadow"
assert D.dyn_tp_calc(None, 30, 30, 10) == (30, 30, 10)                 # senza ATR: valori usati davvero
assert D.dyn_tp_calc(5.0, 30, 30, 10) == (30.0, 30.0, 10.0)
assert D.dyn_tp_calc(1.0, 30, 30, 10) == (15.0, 15.0, 5.0)             # limiti bassi
assert D.dyn_tp_calc(40.0, 30, 30, 10) == (100.0, 100.0, 25.0)         # limiti alti
assert fe["dyn_tp1"] == 60.0 and fe["dyn_trail_dist"] == 20.0 and fe["tp1_base"] == D.CFG["TP1_PCT"], fe
fe0 = asyncio.run(D.entry_features(st, NoHttp(), "MZ", dict(m, pair="NOPE"), "", base_stop=-12))
assert fe0["dyn_tp1"] == D.CFG["TP1_PCT"] and fe0["dyn_trail_dist"] == D.CFG["TRAIL_DISTANCE_PCT"]
r = P(json.dumps({"global": {"DYN_TP_MODE": "on", "DYN_TRAIL_DIST_MAX": 40, "DYN_TP1_MIN": 1}}), base, {}, H)
assert r["globals"]["DYN_TP_MODE"] == "shadow" and r["globals"]["DYN_TRAIL_DIST_MAX"] == 25 and r["globals"]["DYN_TP1_MIN"] == 5
print("stop dinamico OK")

# ---- colonne CSV (migrazione: le righe vecchie restano)
tmp = "/tmp/v81_csvtest.csv"
with open(tmp, "w", newline="") as f:
    w = csv.writer(f); w.writerow(["a", "b"]); w.writerow([1, 2])
D.write_csv_row(tmp, dict({"a": 3, "b": 4}, **D.fe_columns(fe)))
rows = list(csv.DictReader(open(tmp)))
assert len(rows) == 2 and rows[0]["a"] == "1" and rows[0]["entry_features"] == "" and float(rows[1]["stop_applied"]) == -12
assert json.loads(rows[1]["entry_features"])["atr_bars_used"] == 3 and rows[1]["dyn_stop_shadow"] == "-30.0"
assert rows[1]["dyn_tp1"] == "60.0" and rows[1]["dyn_trail_dist"] == "20.0" and rows[1]["dyn_trail_act"] == "60.0"
os.remove(tmp)
assert all(k in D.SHADOW_FIELDS for k in ("atr_1m_10", "stop_applied", "dyn_stop_shadow", "size_mult", "dyn_tp1", "dyn_trail_act",
                                       "dyn_trail_dist", "entry_features"))
print("csv OK")

# ---- price paths e seguito dopo l'uscita
D.PATH_DIR = "/tmp/v81_paths"
import shutil; shutil.rmtree(D.PATH_DIR, ignore_errors=True)
D.path_tick("MP", "real", 1.0, 1000, 0.0, ev="entry")
st.post_exit = {}
D.post_exit_start(st, "MP", "real", "MP", 1.0, 0.88, "stop loss a -12%", -12, -21, now=1000.0)
pair = [{"baseToken": {"address": "MP", "symbol": "MP"}, "priceUsd": "1.10", "liquidity": {"usd": 900}}]
D.post_exit_update(st, {"MP": pair}, now=2000.0)
assert "MP" in st.post_exit
D.post_exit_update(st, {}, now=1000.0 + 181 * 60)
assert "MP" not in st.post_exit
lines = open(os.path.join(D.PATH_DIR, "MP.jsonl")).read().splitlines()
assert len(lines) == 3 and json.loads(lines[2])["k"] == "real_post"
summ = json.loads(open(os.path.join(D.PATH_DIR, "_post_exit_summary.jsonl")).read().splitlines()[-1])
assert summ["rebound_above_entry"] and summ["max_vs_exit_pct"] == 25.0, summ
shutil.rmtree(D.PATH_DIR, ignore_errors=True)
print("price paths OK")
print("TUTTI I TEST v8.1 OK")
