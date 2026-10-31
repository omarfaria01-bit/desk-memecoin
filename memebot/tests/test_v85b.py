"""v8.5b: CLUSTER_UNKNOWN_ACTION=skip (shadow cluster_unknown) e caso aperto Leverhedge (solo osservazione).
Da lanciare SOLO su una copia in /tmp (scrive file in DATA_DIR)."""
import asyncio, json, os, time
import desk as D

assert D.DATA_DIR.startswith("/tmp"), "test solo su copia in /tmp"
# 1) default skip, override non puo riallentare
assert D.CFG["CLUSTER_UNKNOWN_ACTION"] == "skip"
r = D.parse_overrides(json.dumps({"global": {"CLUSTER_UNKNOWN_ACTION": "soft"}}), D.OVR.values(), {}, set())
assert r["globals"].get("CLUSTER_UNKNOWN_ACTION", "skip") == "skip", r
# 2) caso aperto: etichetta neutra al posto di bad_cluster, niente label_watch, file di stato
LEV = "FWatMYieKhRuJ1yuTcmVG1njRhZWwqHwjR11pBympump"
for f in (D.CASE_WATCH_PATH, D.CASE_WATCH_LOG):
    if os.path.exists(f):
        os.remove(f)
st = D.State()
st.labels[LEV] = "bad_cluster"
st.label_watch[LEV] = {"label": "bad_cluster", "pk": 1.0}
d = D.apply_case_watch(st, now=1000.0)
assert st.labels[LEV] == "caso_stelle_aperto" and LEV not in st.label_watch
c = d["leverhedge-2026-10-31"]
assert c["status"] == "aperto" and abs(c["until"] - (1000.0 + 7 * 86400)) < 1
d2 = D.apply_case_watch(st, now=5000.0)   # riavvio: non riparte da capo
assert d2["leverhedge-2026-10-31"]["t0"] == 1000.0
# 3) passi: massimo, poi -50% (non rug), poi -85% = rug
c = dict(c)
r1 = D.case_watch_step(c, {"price": 1.0, "liq": 100000.0, "liq_known": True}, 2000.0)
assert r1["status"] == "aperto"
r2 = D.case_watch_step(c, {"price": 0.5, "liq": 60000.0, "liq_known": True}, 3000.0)
assert r2["status"] == "aperto" and r2["from_peak_pct"] == -50.0
r3 = D.case_watch_step(c, {"price": 0.15, "liq": 50000.0, "liq_known": True}, 4000.0)
assert r3["status"] == "rug" and c["status"] == "rug", r3
# liquidita assente non vale come 0
c2 = {"sym": "X", "until": 1e12, "pk": 1.0, "lpk": 100000.0, "n": 3, "last_l": 90000.0}
assert D.case_watch_step(c2, {"price": 0.9, "liq": 0.0, "liq_known": False}, 10.0)["status"] == "aperto"
# 7 giorni
c3 = {"sym": "X", "until": 100.0, "pk": 1.0, "lpk": 1e5, "n": 3}
assert D.case_watch_step(c3, {"price": 1.0, "liq": 1e5, "liq_known": True}, 200.0)["status"] == "chiuso_7g"
# 4) mai acquisto: Executor
rej = []
D.reject = lambda st, mint, sym, reason, agent="Filter", cat=None, quiet=False: rej.append(cat)
asyncio.run(D._execute_buy(st, None, {"candles": None}, LEV, {"symbol": "Leverhedge"}))
assert rej == ["caso aperto"], rej
print("test_v85b OK")
