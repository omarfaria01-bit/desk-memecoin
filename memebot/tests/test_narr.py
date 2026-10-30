import json, os, time
from datetime import datetime, timezone, timedelta
import desk as D
now = datetime.now(timezone.utc)
def doc(ts, narrs): return json.dumps({"updated_at": ts, "narratives": narrs})
N = [{"theme": "T-rise", "keywords": ["abc", "robotics"], "tickers": ["$ZZZ", "USDC"], "mints": ["MINT1", D.WSOL],
      "strength": 0.6, "trend": "rising", "velocity": 3, "trusted_mentions": 2},
     {"theme": "T-stable", "keywords": ["galaxy"], "tickers": ["YYY"], "mints": [], "strength": 0.3, "trend": "stable",
      "velocity": 5},
     {"theme": "T-fade", "keywords": [], "tickers": ["FFF"], "mints": [], "strength": 0.4, "trend": "fading"},
     {"theme": "T-low", "keywords": ["planet"], "tickers": [], "mints": [], "strength": 0.1, "trend": "rising",
      "velocity": 0.5},
     "garbage", {"theme": "T-bad", "strength": "x", "trend": "weird", "tickers": "notalist"}]
ts, n, _bl = D.parse_narratives(doc(now.isoformat().replace("+00:00", "Z"), N))
assert ts and len(n) == 5
B = D.narrative_bonus
assert B(n, "M", "zzz", "")[0] == 0.07                           # strong 0.05 + trusted 0.02
assert B(n, "MINT1", "QQQ", "")[0] == 0.07                       # mint -> strong (+trusted)
assert B(n, "M", "QQQ", "Big Planet")[0] == 0.025                # weak, velocity 0.5 > 0
assert B(n, "M", "QQQ", "abc token")[0] == 0.0                   # keyword < 5 chars ignored
assert B(n, "M", "YYY", "")[0] == 0.0                            # stable: no bonus, no penalty
assert B(n, "M", "FFF", "")[0] == -0.05                          # fading: late penalty
assert B(n, "M", "ZZZ", "planet")[0] == 0.08                     # 0.07 + 0.025 capped at 0.08
assert B(n, D.WSOL, "ZZZ", "")[0] == 0.0                         # excluded mint
assert B(n, "M", "USDC", "")[0] == 0.0 and B(n, "M", "sol", "robotics")[0] == 0.0   # stable/SOL ticker excluded
assert B(n, "M", "QQQ", "")[0] == 0.0
for bad in ("{oops", "[]", '{"narratives": 5}'):
    try: D.parse_narratives(bad); raise SystemExit("should fail")
    except ValueError: pass
print("matching OK")
# loader: missing -> warn once; stale; invalid; mtime reload
st = D.State()
assert D.NARR.current(st) == [] and D.NARR.current(st) == []
def w(t):
    open(D.NARR_PATH, "w").write(t); x = time.time_ns() + 10**9 * len(t); os.utime(D.NARR_PATH, ns=(x, x))
w(doc((now - timedelta(hours=4)).isoformat(), N[:1])); assert D.NARR.current(st) == [] and D.NARR.current(st) == []
w("{broken"); assert D.NARR.current(st) == [] and D.NARR.current(st) == []
w(doc(now.isoformat().replace("+00:00", "Z"), N[:2])); assert len(D.NARR.current(st)) == 2
w(json.dumps({"narratives": N[:1]})); assert D.NARR.current(st) == []
ev = [json.loads(l) for l in open("events.jsonl")]
warns = [e["msg"] for e in ev if e["kind"] == "warn"]
assert len(warns) == 4, warns
print("loader OK", warns)
