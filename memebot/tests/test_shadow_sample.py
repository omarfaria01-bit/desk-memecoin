"""Compito 11: campione degli scarti in shadow. 200 scarti finti: tetto mai superato, nessuna chiamata, RAM stabile.
Offline, solo su copia in /tmp."""
import asyncio, csv, os, time, tracemalloc
import desk as D

assert D.DATA_DIR.startswith("/tmp"), "test solo su copia in /tmp"
D.tg_send = lambda *_a, **_k: None
D.CFG.update({"SHADOW_TRADES": True, "SHADOW_REJECT_SAMPLE": 0.5, "SHADOW_SAMPLE_MAX_OPEN": 30, "SHADOW_SAMPLE_SEED": 7,
              "COST_GATE_MULT": 0.0, "ENTRY_CONFIRM_MODE": "on"})
CALLS = []


class NoNet:   # qualsiasi chiamata di rete fa fallire il test
    def __getattr__(self, name):
        CALLS.append(name)
        raise AssertionError(f"chiamata di rete: {name}")


_c2 = D.spawn_confirm2
D.spawn_confirm2 = lambda *a, **k: CALLS.append("confirm2")
st = D.State()


def m(i):
    return {"symbol": f"T{i}", "price": 1e-4, "liq": 2e4, "dex": "pumpswap", "src": "test", "mcap": 1e5}


# scarti duri di sicurezza e token senza liquidita: mai campionati
assert not D.sample_reject(st, None, "H1", m(0), "mint non revocata", "mint non revocata")
assert not D.sample_reject(st, None, "H2", m(0), "honeypot: x", "honeypot")
assert not D.sample_reject(st, None, "H3", dict(m(0), liq=0.0), "prefiltro: liq", "prefiltro liq")
# deterministico: stesso seed + mint = stessa scelta
picks = [D.random.Random(f"7:M{i}").random() < 0.5 for i in range(200)]


async def flood():
    tracemalloc.start()
    taken = 0
    for i in range(200):
        taken += D.sample_reject(st, NoNet(), f"M{i}", m(i), f"prefiltro: volume (T{i})", "prefiltro volume")
        await asyncio.sleep(0)
    await asyncio.gather(*list(D.SHADOW_TASKS), return_exceptions=True)
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return taken, peak


taken, peak = asyncio.run(flood())
samples = [k for k, v in st.shadow_trades.items() if v.get("tag") == "sample"]
assert 0 < len(samples) <= D.CFG["SHADOW_SAMPLE_MAX_OPEN"], len(samples)          # tetto mai superato
assert all(st.shadow_trades[k]["shadow_reason"] == "sample" for k in samples)
assert all(picks[int(k[1:])] for k in samples)                                      # solo i mint estratti
assert not D.SAMPLE_PENDING and not CALLS, (D.SAMPLE_PENDING, CALLS)               # nessuna chiamata, nessun resto
assert peak < 20 * 1024 * 1024, peak                                               # RAM stabile (< 20 MB di picco)
assert st.stats["shadow_sample_full"] > 0                                           # oltre il tetto: scartati
# i campioni non tolgono posto agli shadow normali
D.CFG["SHADOW_MAX_OPEN"] = 1
D.spawn_confirm2 = lambda *a, **k: None


async def one_normal():
    return await D.open_shadow(st, None, "NORMALE", m(999), ["top10"], "x", 0.5, [], tag="top10")
D.BROKER_REF[0] = None
sh = asyncio.run(one_normal())
assert sh is not None and sh["tag"] == "top10"
# 80% dei posti occupati: quota dimezzata
assert D.sample_rate(st) <= 0.25 + 1e-9
# riga CSV con shadow_reason
k = samples[0]
D._close_shadow_row(st, k, st.shadow_trades.pop(k), -5.0, False, "stop loss a -5%", time.time())
rows = list(csv.DictReader(open(D.SHADOW_CSV)))
assert rows[-1]["shadow_reason"] == "sample" and "sample" in D.MEASURE_ONLY_KEYS
D.spawn_confirm2 = _c2
print("test_shadow_sample OK", len(samples), "campioni, picco RAM", peak // 1024, "KB")
