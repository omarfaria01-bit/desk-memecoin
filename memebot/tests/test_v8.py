"""v8: controllo progetto (sources/project_check.py), bonus/penalita, coda e verdetti deep review. Offline."""
import asyncio, json, os, time
import desk as D
from sources import project_check as P

# ---- analisi HTML
parked = P.analyze_html(b"<html><title>x</title><body>This domain is for sale! Buy this domain at Sedo</body></html>")
assert parked["parked"] and parked["small"], parked
spa = P.analyze_html(b"<html><head><title>APP</title><script src='/a.js'></script><script type=\"module\" src='/b.js'>"
                     b"</script></head><body><div id=\"root\"></div>" + b" " * 2000 + b"</body></html>")
assert spa["spa"] and not spa["small"], spa
real = P.analyze_html(("<html><title>Proj</title><body>" + "<p>Our protocol offers staking and a bridge. Read the docs "
                       "and whitepaper. github: https://github.com/acme/core </p>" * 20 + "</body></html>").encode())
assert not real["parked"] and not real["small"] and real["github"] == ["acme/core"], real
print("html OK")

# ---- punteggio
site = {"ok": True, "parked": [], "small": False, "text_len": 3000, "spa": False}
gh_ok = {"exists": True, "pushed_days": 2, "commits_30d": 12, "fork": False}
s, t = P.score_project(site, {"x": True, "tg": True}, gh_ok, 400, {"checked": True}, real["text"], False)
assert s >= 0.9 and "proj_github_active" in t and "proj_utility" in t and "proj_domain_old" in t, (s, t)
s, t = P.score_project(None, {"x": False, "tg": False}, None, None, {"checked": True}, "just a meme", True)
assert "proj_empty" in t and "proj_nosite" in t and "proj_nosocials" in t and "proj_meme" in t and s == 0, (s, t)
s, t = P.score_project(dict(site, parked=["coming soon"]), {"x": True, "tg": False}, None, 3, None, "", True)
assert "proj_template" in t and "proj_domain_new:3" in t and "proj_site" not in t and s < 0.5, (s, t)
s, t = P.score_project(site, {"x": True}, {"exists": True, "pushed_days": 200, "fork": False}, None,
                       {"checked": True, "mismatch": True}, "", False)
assert "proj_github_stale" in t and "proj_meta_mismatch" in t, t
mc = P.meta_consistency({"symbol": "ABC", "mutable": True, "updateAuthority": "Upd111"}, {"symbol": "abc"},
                        {"symbol": "$ABC"}, "ABC", None)
assert mc["checked"] and not mc["mismatch"] and mc["mutable"], mc
assert P.meta_consistency({"symbol": "ABC"}, None, {"symbol": "XYZ"}, None, None)["mismatch"]
cfg = {"PROJECT_BONUS": 0.02, "PROJECT_EMPTY_PENALTY": 0.01, "PROJECT_MIN_SCORE": 0.5}
assert P.project_bonus({"score": 0.8, "tags": []}, cfg) == (0.016, ["proj_good:0.80"])
assert P.project_bonus({"score": 0.0, "tags": ["proj_empty"]}, cfg)[0] == -0.01
assert P.project_bonus({"score": 0.4, "tags": ["proj_site"]}, cfg)[0] == 0.0
assert P.project_bonus({"score": None, "tags": ["proj_timeout"]}, cfg)[0] == 0.0     # timeout: nessun effetto
print("score OK")

# ---- integrazione nel bonus (fonti esterne finte, nessuna rete)
st = D.State()
st.fomo_rank = {}
saved = dict(D.SRC)
D.SRC.clear()


class FakeProj:
    delay, res = 0.0, None
    project_bonus = staticmethod(P.project_bonus)

    @classmethod
    async def acheck(cls, mint, **kw):
        await asyncio.sleep(cls.delay)
        return dict(cls.res, ts=time.time())


D.SRC["proj"] = FakeProj
m = {"symbol": "TST", "name": "Test", "links": {"websites": [], "socials": []}, "url": "u"}
FakeProj.res = {"score": 0.9, "tags": ["proj_site", "proj_x", "proj_score:0.90"], "links": {"websites": ["w"]}}
b, tags = asyncio.run(D.compute_bonus(st, "M1", m, {}))
assert abs(b - 0.018) < 1e-9 and "proj_good:0.90" in tags, (b, tags)
FakeProj.res = {"score": 0.0, "tags": ["proj_empty", "proj_nosite", "proj_nosocials", "proj_score:0.00"]}
b, tags = asyncio.run(D.compute_bonus(st, "M2", m, {}))
assert abs(b + 0.01) < 1e-9 and "proj_empty" in tags, (b, tags)
D.CFG["PROJECT_TIMEOUT_SEC"] = 1.0
FakeProj.delay = 1.5
t0 = time.time()
b, tags = asyncio.run(D.compute_bonus(st, "M3", m, {}))
assert b == 0.0 and "proj_timeout" in tags and time.time() - t0 < 1.4, (b, tags)
FakeProj.delay = 0.0
D.CFG["PROJECT_TIMEOUT_SEC"] = 5.0
D.SRC.clear()
D.SRC.update(saved)
print("compute_bonus OK")

# ---- Learner: bonus progetto e penalita
st = D.State()
st.learner_bonus = {"hist": []}
now = time.time()
for i in range(20):
    st.learner_bonus["hist"].append([20.0, 0, ["src:x", "proj_good:0.80"], int(now), 1])
    st.learner_bonus["hist"].append([-10.0, 0, ["src:x", "proj_empty"], int(now), 1])
    st.learner_bonus["hist"].append([0.0, 0, ["src:x"], int(now), 1])
acts = D.learn_bonus_step(st, now)
assert "PROJECT_BONUS" in acts and "PROJECT_EMPTY_PENALTY" in acts, acts
assert abs(D.bonus_eff(st, "PROJECT_BONUS") - 0.03) < 1e-9
assert abs(D.bonus_eff(st, "PROJECT_EMPTY_PENALTY") - 0.015) < 1e-9
st.learner_bonus["d"]["PROJECT_BONUS"] = 1.0
assert D.bonus_eff(st, "PROJECT_BONUS") == 0.06          # limite 0-0.06
st.learner_bonus["d"]["PROJECT_EMPTY_PENALTY"] = 1.0
assert D.bonus_eff(st, "PROJECT_EMPTY_PENALTY") == 0.03  # limite 0-0.03
print("learner OK")

# ---- override con limiti
out = D.parse_overrides(json.dumps({"global": {"PROJECT_BONUS": 0.5, "PROJECT_EMPTY_PENALTY": -1,
                                               "DEEP_HOLD_EXTRA_MIN": 999, "DEEP_TRAIL_TIGHTEN_PCT": 4}}),
                        {}, {}, set())
g = out["globals"]
assert g["PROJECT_BONUS"] == 0.06 and g["PROJECT_EMPTY_PENALTY"] == 0.0 and g["DEEP_HOLD_EXTRA_MIN"] == 180 \
    and g["DEEP_TRAIL_TIGHTEN_PCT"] == 4.0, g
print("override OK")

# ---- deep review: coda e verdetti
for pth in (D.DEEP_QUEUE_PATH, D.DEEP_REVIEWS_PATH):
    if os.path.exists(pth):
        os.remove(pth)
D.DEEP.update(key=None, reviews={}, queued=None)
assert D.deep_queue_add("MINT1", "AAA", {"websites": ["https://a.xyz"]}, "opened", "url")
assert not D.deep_queue_add("MINT1", "AAA", {}, "opened")              # una volta sola
D.deep_queue_peak("MINT1", "AAA", 40.0, {}, "", "shadow")               # sotto +50%: no
D.deep_queue_peak("MINT1", "AAA", 55.0, {}, "", "shadow")
D.deep_queue_peak("MINT1", "AAA", 80.0, {}, "", "shadow")               # stesso motivo: no
q = [json.loads(x) for x in open(D.DEEP_QUEUE_PATH)]
assert [e["reason"] for e in q] == ["opened", "shadow_peak:+55%"] and q[0]["chain"] == "solana" and q[0]["ts"], q
D.DEEP["queued"] = None                                                  # riavvio: rilegge la coda
assert not D.deep_queue_add("MINT1", "AAA", {}, "opened")
json.dump({"GOOD": {"score": 82, "verdict": "buono"}, "BAD": {"score": 20}, "MID": {"score": 50},
           "JUNK": {"score": "x"}}, open(D.DEEP_REVIEWS_PATH, "w"))
assert D.deep_effect("GOOD")["hold_extra"] == 60 and D.deep_effect("BAD")["trail_tighten"] == 3.0
assert D.deep_effect("MID") == {"score": 50.0, "hold_extra": 0, "trail_tighten": 0.0}
assert D.deep_effect("JUNK")["score"] is None and D.deep_effect("NONE")["score"] is None
p = {"max_hold_min": None}
assert D.pos_max_hold(p, "GOOD") == D.CFG["MAX_HOLD_MIN"] + 60 and D.pos_max_hold(p, "MID") == D.CFG["MAX_HOLD_MIN"]
assert D.pos_max_hold({"max_hold_min": 60}, "GOOD") == 60               # LP sbloccata: niente estensione
open(D.DEEP_REVIEWS_PATH, "w").write("{rotto")
time.sleep(0.01)
os.utime(D.DEEP_REVIEWS_PATH)
assert D.deep_effect("GOOD")["hold_extra"] == 60                         # file rotto: ultimi valori validi
print("deep review OK")

# ---- Risk con verdetti: tenuta piu lunga (voto 82), trailing piu stretto (voto 20), coda a +50%
json.dump({"GOOD": {"score": 82}, "BAD": {"score": 20}}, open(D.DEEP_REVIEWS_PATH, "w"))
D.DEEP.update(key=None, reviews={}, queued=None)
st = D.State(); D.CFG["RISK_CHECK_SEC"] = 0; D.SOL_USD[0] = 0.0001


def mkpos(mint, price=1.0, age_min=0):
    st.positions[mint] = {"symbol": mint, "tokens": 100.0, "tokens_entry": 100.0, "last_price": price,
                          "entry_mark": 1.0, "liq": 1e9, "liq_entry": 1e9, "peak_pct": 0.0, "stop_hits": 0,
                          "opened": time.time() - age_min * 60, "last_ok": time.time() + 1e7, "tp_done": False,
                          "tp_stage": 0, "realized": 0.0, "cost": 100.0, "links": {"websites": ["https://g.io"]}}
    st.sec_checked[mint] = time.time() + 1e7
    return st.positions[mint]


async def fb(http, mints):
    return {}
D.dex_batch = fb


async def run(n=2):
    t = asyncio.create_task(D.risk_agent(st, None, D.PaperBroker()))
    for _ in range(n):
        await asyncio.sleep(0.005)
    t.cancel()
D.CFG["TIME_STOP_MIN"] = 500
mkpos("GOOD", 1.02, D.CFG["MAX_HOLD_MIN"] + 30); mkpos("MID", 1.02, D.CFG["MAX_HOLD_MIN"] + 30)
asyncio.run(run(2))
assert "GOOD" in st.positions and "MID" not in st.positions, list(st.positions)
assert st.positions["GOOD"]["eff"]["max_hold"] == D.CFG["MAX_HOLD_MIN"] + 60
mkpos("GOOD", 1.02, D.CFG["MAX_HOLD_MIN"] + 61); asyncio.run(run(2)); assert "GOOD" not in st.positions
D.CFG["TIME_STOP_MIN"] = 30
p = mkpos("GOOD", 1.0, 45); asyncio.run(run(2)); assert "GOOD" in st.positions and p["eff"]["time_stop"] == 90
st.positions.clear()
p = mkpos("BAD", 1.6); asyncio.run(run(2))
assert "BAD" in st.positions and p["eff"]["trail_dist"] == D.CFG["TRAIL_DISTANCE_PCT"] - 3, p["eff"]
q = [json.loads(x) for x in open(D.DEEP_QUEUE_PATH)]
assert any(e["mint"] == "BAD" and e["reason"].startswith("real_peak") for e in q), q
for pth in (D.DEEP_QUEUE_PATH, D.DEEP_REVIEWS_PATH):
    os.remove(pth)
print("risk deep OK")
print("TUTTI I TEST v8 OK")


def test_v8b_no_quote_mark_without_real_sol_price():
    """Bug 30/10: con SOL al fallback ($150) i preventivi gonfiavano il valore. Senza prezzo reale niente qmark."""
    import asyncio
    import desk as D
    old = list(D.SOL_USD)
    try:
        D.SOL_USD[0] = 0.0
        assert not D.sol_known()

        class H:
            async def get(self, url, **kw):
                raise AssertionError("non deve chiedere preventivi senza prezzo SOL")
        b = D.PaperBroker(http=None)
        b.http, b.realistic = H(), True
        p = {"tokens": 100.0, "last_price": 1.0, "dec": 6}
        asyncio.run(b.refresh_mark("M", p))
        assert "qmark" not in p

        class H2:
            async def get(self, url, **kw):
                if "geckoterminal" in url:
                    raise RuntimeError("429")
                if "price/v3" in url:
                    return {D.WSOL: {"usdPrice": 95.0}}
                return None
        D.SOL_USD[0], D.SOL_USD[1] = 0.0, 0.0
        asyncio.run(D.refresh_sol_usd(H2()))
        assert D.SOL_USD[0] == 95.0 and D.sol_known()
    finally:
        D.SOL_USD[:] = old

test_v8b_no_quote_mark_without_real_sol_price()
print("v8b OK")
