"""v8.8: call Discord NKT (SHADOW). Nessuna rete, nessun effetto su score/size/acquisti. Solo su copia in /tmp."""
import asyncio, json, os, tempfile, time
import desk as D
from sources import discord_calls as DC

assert D.DATA_DIR.startswith("/tmp"), "test solo su copia in /tmp"
now = time.time()
tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
json.dump({"calls": {"MINTcalled111pump": {"call_ts": int(now - 600), "author": "x", "label": "win", "max_x_1h": 2.0}}}, tmp)
tmp.close()
calls = DC.load(tmp.name)
assert "MINTcalled111pump" in calls
i = DC.info("MINTcalled111pump", now, calls)
assert i == {"discord_call": 1, "discord_call_age_min": 10.0}, i
assert set(i) == {"discord_call", "discord_call_age_min"}   # mai label/max_x (informazione futura)
assert DC.info("altro", now, calls) == {}
assert DC.load("/tmp/non_esiste_dc.json") == {}
open(tmp.name, "w").write("{rotto")
assert DC.load(tmp.name) == {}   # file rotto: nessuna eccezione

# file globale via env/DATA: punta al file di test
DC.DATA = __import__("pathlib").Path(tmp.name)
json.dump({"calls": {"MINTcalled111pump": {"call_ts": int(now - 600)}}}, open(tmp.name, "w"))
DC._cache.update(mtime=None, chk=0.0)
assert D.SRC.get("discord") is DC, "modulo non caricato"
t = D.discord_tag("MINTcalled111pump")
assert t.get("discord_call") == 1 and 9 <= t["discord_call_age_min"] <= 11, t
assert D.discord_tag("nope") == {}
D.CFG["DISCORD_CALLS_MODE"] = "off"
assert D.discord_tag("MINTcalled111pump") == {}
D.CFG["DISCORD_CALLS_MODE"] = "shadow"

# score invariato: final_score/rule_score non conoscono discord_call
import inspect
for fn in (D.rule_score, D.final_score):
    assert "discord" not in inspect.getsource(fn)
assert not any("discord" in str(v) for v in D.BONUS_KEYS.values())

# reject(): il tag finisce nell'evento
seen = []
orig = D.write_event
D.write_event = lambda a, k, m, ex=None, **kw: seen.append((k, ex))
class S:  # stato minimo
    reject_reasons = __import__("collections").Counter(); stats = __import__("collections").Counter()
D.reject(S(), "MINTcalled111pump", "TST", "prova", quiet=True)
D.write_event = orig
assert seen and seen[0][1].get("discord_call") == 1, seen
os.unlink(tmp.name)
print("test_v88_discord OK")
