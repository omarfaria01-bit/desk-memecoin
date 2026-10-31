"""Stella nella coda: holder piccoli fuori dai top analizzati finanziati dallo stesso wallet. Offline, RPC finta."""
import asyncio
from sources import cluster_v2 as C

BIN = "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhLEfqhAF"   # exchange noto
v = C.tail_star_verdict(["HUB"] * 12 + ["A", "B", None], {}, {})
assert v["hit"] and v["same"] == 12 and v["n"] == 14
assert not C.tail_star_verdict(["HUB"] * 7 + ["A"], {}, {})["hit"]          # meno di TAIL_HARD_N
assert not C.tail_star_verdict([BIN] * 15, {}, {})["hit"]                    # exchange: ritiri di persone diverse
assert not C.tail_star_verdict(["HUB"] * 8 + list("ABCDEFGHIJ"), {}, {})["hit"]   # 8/18 < 50%


async def fake_rpc(method, params, **k):
    if method == "getSignaturesForAddress":
        return [{"signature": "s_" + params[0]}]          # wallet nuovo (1 transazione)
    return None


async def run():
    C._funding_orig = C._funding

    async def fund(rpc, o, sig):
        return ("HUB" if o.startswith("t") else "X" + o, 0, 0)
    C._funding = fund
    full = {**{f"t{i}": 0.002 for i in range(300)}, **{f"top{i}": 1.0 for i in range(10)}, "dust": 0.0000001}
    rows = {f"top{i}": {} for i in range(10)}
    r = await C.tail_star(fake_rpc, "MINT", rows, full, set(), {}, dict(C.CFG))
    C._funding = C._funding_orig
    return r
r = asyncio.run(run())
assert r["sampled"] == 20 and r["tail_size"] == 300 and r["hit"] and r["same"] == 20, r
assert C.CFG["TAIL_MODE"] == "shadow"
print("test_tail_star OK")
