"""Compito 14: watchlist piena -> escono prima i token morti; mai prio o call Discord; nessuna chiamata; RAM con 2000.
Offline, solo su copia in /tmp."""
import time, tracemalloc
import desk as D

assert D.DATA_DIR.startswith("/tmp"), "test solo su copia in /tmp"
EV, SH = [], []
D.write_event = lambda agent, kind, msg, data=None: EV.append((kind, data or {}))
D.spawn_shadow = lambda *a, **k: SH.append(k.get("tag"))
D.discord_tag = lambda mint, now=None: {"discord_call": 1} if mint == "DISC" else {}
D.CFG.update({"WATCH_NEW_MAX": 5, "WATCH_MAX": 2000, "WATCH_EVICT_SHADOW": 1.0, "SHADOW_TRADES": True})
st = D.State()
now = time.time()


def put(k, seen, dead=None, prio=False):
    st.watch[k] = {"seen": seen, "sym": k, "src": "nuova pool", "prio": prio}
    if dead is not None:
        D.watch_note(st.watch[k], {"liq": 1e3 if dead else 5e4, "vol_m5": 0 if dead else 900, "buys_m5": 0 if dead else 5,
                                   "sells_m5": 0 if dead else 3, "price": 1e-4, "dex": "pumpswap"}, now)


put("OLD_LIVE", now - 3000, dead=False)    # il piu vecchio, ma vivo
put("DISC", now - 4000, dead=True)         # morto ma con call Discord: mai sfrattato
put("DEAD1", now - 100, dead=True)
put("NEW", now - 50)                       # mai letto: non e 'morto'
put("LIVE2", now - 200, dead=False)
put("TREND", now - 9000, dead=True, prio=True)
D.add_watch(st, "X1", "X1", "nuova pool")
assert "DEAD1" not in st.watch and "OLD_LIVE" in st.watch and "DISC" in st.watch and "TREND" in st.watch
ev = lambda: [d for k, d in EV if k == "watch_evict"][-1]   # noqa: E731
assert ev()["why"] == "dead"
D.add_watch(st, "X2", "X2", "nuova pool")   # nessun altro morto non protetto: esce il piu vecchio non protetto
assert "OLD_LIVE" not in st.watch and ev()["why"] == "oldest" and SH and SH[-1] == "evicted"
assert st.stats["watch_evict_dead"] == 1 and st.stats["watch_evict_oldest"] == 1
assert "evicted" in D.MEASURE_ONLY_KEYS

# RAM e tempo con 2000 token (nessuna chiamata: add_watch non ha client http)
st2 = D.State()
D.CFG.update({"WATCH_NEW_MAX": 1200, "WATCH_EVICT_SHADOW": 0.0})
tracemalloc.start()
t0 = time.time()
for i in range(2000):
    D.add_watch(st2, f"M{i}", f"M{i}", "pump.fun")
    if i % 3 == 0:
        D.watch_note(st2.watch[f"M{i}"], {"liq": 0, "vol_m5": 0, "buys_m5": 0, "sells_m5": 0, "price": 1, "dex": "x"}, now)
_, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()
assert len(st2.watch) == 1200 and peak < 30 * 1024 * 1024, (len(st2.watch), peak)
print("test_watch_smart OK", f"{time.time() - t0:.2f}s", peak // 1024, "KB")
