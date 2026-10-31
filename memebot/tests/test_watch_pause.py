"""Compito 8: dopo una pausa del processo la watchlist non scade in blocco. Orologio finto, offline, copia in /tmp."""
import desk as D

assert D.DATA_DIR.startswith("/tmp"), "test solo su copia in /tmp"
D.tg_send = lambda *_a, **_k: None
D.path_write = lambda *_a, **_k: None
st = D.State()
D.LOOP_MONO.clear()
D.HOST_PAUSE.update({"last_resume": 0.0, "last_evt": 0.0, "pending": [], "hold": {}, "watch_shift_to": 0.0})
T0 = 1_000_000.0
st.watch = {"A": {"seen": T0 - 100 * 60, "sym": "A"}, "B": {"seen": T0 - 10 * 60, "sym": "B"}}
ttl = D.CFG["WATCH_TTL_MIN"] * 60
# giri normali ogni 60 s (wall e monotonic insieme): nessuno spostamento
assert D.host_pause_check(st, "filter", now=T0, mono=500.0) == 0.0
assert D.host_pause_check(st, "filter", now=T0 + 60, mono=560.0) == 0.0
assert st.watch["A"]["seen"] == T0 - 100 * 60
# pausa di 40 min: il giro dopo vede un salto -> 'seen' spostati in avanti della pausa
gap = D.host_pause_check(st, "filter", now=T0 + 60 + 2400, mono=620.0)
assert gap >= 2400
assert abs(st.watch["A"]["seen"] - (T0 - 100 * 60 + 2400)) < 1e-6, st.watch["A"]
now = T0 + 60 + 2400
assert now - st.watch["A"]["seen"] < ttl     # senza la correzione: 100 + 41 min > 120 -> scaduto in blocco
# lo stesso salto visto da un altro ciclo (risk) non sposta due volte
D.LOOP_MONO["risk"] = (T0 + 30, 590.0)
D.host_pause_check(st, "risk", now=now + 5, mono=625.0)
assert abs(st.watch["A"]["seen"] - (T0 - 100 * 60 + 2400)) < 6, st.watch["A"]   # al massimo i 5 s del giro
# anche se l'altro ciclo arriva piu di 60 s dopo (nuovo evento): niente doppio spostamento
D.LOOP_MONO["shadow"] = (T0 + 40, 600.0)
D.host_pause_check(st, "shadow", now=now + 90, mono=710.0)
assert abs(st.watch["A"]["seen"] - (T0 - 100 * 60 + 2400)) < 91, st.watch["A"]
# un token aggiunto dopo la pausa non viene spostato da quella pausa
st.watch["C"] = {"seen": now + 100, "sym": "C"}
D.host_pause_check(st, "filter", now=now + 60, mono=680.0)    # giro normale di 60 s dopo la ripresa
assert st.watch["C"]["seen"] == now + 100
# una seconda pausa vera (10 min) sposta tutti, compreso C, solo di quella pausa
D.host_pause_check(st, "filter", now=now + 60 + 600, mono=740.0)
_sh = st.watch["C"]["seen"] - (now + 100)
assert 600 - 90 <= _sh <= 600, _sh   # tolleranza: al massimo un giro di ciclo gia compensato dalla pausa prima
print("test_watch_pause OK")
