"""Compito 19: avviso Telegram per ogni ingresso/uscita shadow (nessun acquisto reale). Offline, su copia
in /tmp. Nessun token/chat id reale: TG_REF e' un finto notifier che registra solo le chiamate a group()."""
import asyncio, time
import desk as D

assert D.DATA_DIR.startswith("/tmp"), "test solo su copia in /tmp"
D.CFG["CLUSTER_V2"] = False
D.CFG["HONEYPOT_CHECK"] = False
D.SOL_USD[0], D.SOL_USD[1] = 100.0, time.time()


class FakeNotifier:
    def __init__(self):
        self.calls = []

    def group(self, key, line, every=600, title=None):
        self.calls.append((key, line, every, title))


# ---- helper puri: contenuto del messaggio, mai il token (il finto notifier non ha nemmeno un campo token)
fn = FakeNotifier()
D.TG_REF[0] = fn
MINT = "A" * 40 + "pump"
D.shadow_tg_open(MINT, "FOO", "Analyst: momento/momentum", ["analyst_momentum"], 0.52, 0.50, 125000.0, 0.0042)
assert len(fn.calls) == 1
key, line, every, title = fn.calls[0]
assert key == "shadow_open" and every == D.CFG["SHADOW_TG_GROUP_SEC"]
assert title == "👻 SHADOW aperti (nessun acquisto reale)"
assert "FOO" in line and MINT in line and "momento/momentum" in line and "analyst_momentum" in line
assert "0.520" in line and "0.50" in line and "125,000" in line
assert f"https://dexscreener.com/solana/{MINT}" in line and f"https://gmgn.ai/sol/token/{MINT}" in line

fn.calls.clear()
D.shadow_tg_close(MINT, "FOO", -12.3, "stop loss")
assert len(fn.calls) == 1
key, line, every, title = fn.calls[0]
assert key == "shadow_close" and title == "👻 SHADOW chiusi"
assert "FOO" in line and MINT in line and "-12.3%" in line and "stop loss" in line

# ---- SHADOW_TG_NOTIFY=False: nessuna chiamata
fn.calls.clear()
D.CFG["SHADOW_TG_NOTIFY"] = False
D.shadow_tg_open(MINT, "FOO", "x", [], 0.5, 0.5, 1.0, 1.0)
D.shadow_tg_close(MINT, "FOO", 1.0, "x")
assert fn.calls == []
D.CFG["SHADOW_TG_NOTIFY"] = True

# ---- integrazione vera: open_shadow()/_close_shadow_row() chiamano davvero i due avvisi
fn.calls.clear()
st = D.State()
m = {"symbol": "S", "price": 1.0, "liq": 500_000.0}
sh = asyncio.run(D.open_shadow(st, None, "SH1", m, ["bundle"], "punteggio basso", 0.47, ["bundle"]))
assert sh is not None
opens = [c for c in fn.calls if c[0] == "shadow_open"]
assert len(opens) == 1 and "SH1" in opens[0][1] and "S" in opens[0][1]
now = sh["t"]
sh["last_price"], sh["last_ok"] = 1.5, now + 60
asyncio.run(D.shadow_apply(st, None, "SH1", sh, D.shadow_decide(sh, now + 60), now + 60))
sh["last_price"], sh["last_ok"] = 1.0, now + 120
D.shadow_decide(sh, now + 120)
asyncio.run(D.shadow_apply(st, None, "SH1", sh, D.shadow_decide(sh, now + 150), now + 150))
closes = [c for c in fn.calls if c[0] == "shadow_close"]
assert len(closes) == 1 and "SH1" in closes[0][1]

# ---- anti-flood: 3 apertures nello stesso minuto -> lo stesso gruppo 'shadow_open' (una riga per ognuna,
# un solo invio quando il thread Telegram svuota il gruppo, non uno a messaggio)
fn.calls.clear()
for i in range(3):
    asyncio.run(D.open_shadow(st, None, f"SH{10 + i}", dict(m, symbol=f"S{i}"), ["score"], "x", 0.5, []))
opens = [c for c in fn.calls if c[0] == "shadow_open"]
assert len(opens) == 3 and all(c[2] == D.CFG["SHADOW_TG_GROUP_SEC"] for c in opens)  # stessa chiave/every: raggruppate

D.TG_REF[0] = None
print("test_shadow_telegram: ok")

# ---- review Claude: un gruppo lungo (molti shadow insieme) va in piu messaggi, mai troncato
import queue as _q, threading as _th  # noqa: E402
from sources import notify_telegram as _NT  # noqa: E402
n = object.__new__(_NT.Notifier)
n.enabled, n.glock, n.groups, n.q, n.dropped, n.onflush = True, _th.Lock(), {}, _q.Queue(), 0, (lambda: None)
for i in range(40):
    n.group("shadow_open", f"TOK{i} | CA: {'X' * 44} | motivo {'y' * 120} | https://dexscreener.com/solana/{'X' * 44}",
            every=0, title="SHADOW aperti")
n._flush_groups(force=True)
msgs = [n.q.get_nowait() for _ in range(n.q.qsize())]
assert len(msgs) > 1 and all(len(x) <= _NT.MAX_LEN for x in msgs)
assert all(f"TOK{i} |" in "".join(msgs) for i in range(40))   # nessuna riga persa
print("test_shadow_telegram (messaggi divisi): ok")
