# watch-pause (compito 8)

1. **Cosa:** `watch_pause_shift(st, a, b)`: quando `host_pause_check` vede una pausa [a, b], i `seen` della watchlist vanno avanti della parte di pausa non ancora compensata. `host_pause_check` ora gira anche nel ciclo del Filter, prima della scadenza `WATCH_TTL_MIN`.
2. **Perché:** al risveglio la watchlist scadeva in blocco ("volume insufficiente nel periodo di osservazione"), com'è successo la notte del 30/10. Il Filter poteva scaricarla prima ancora che i cicli Risk o Shadow si accorgessero della pausa.
3. **File:** `desk.py` (`watch_pause_shift`, `host_pause_check`, `filter_agent`); test `tests/test_watch_pause.py`.
4. **Prove:** orologio finto. Pausa di 40 min: un token visto 100 min prima resta in watchlist (senza fix: 141 min, scaduto). La stessa pausa vista da tre cicli non sposta mai due volte; un token aggiunto dopo la pausa non viene spostato. Su `main` il test fallisce; suite come su `main`.
5. **Rischi:** sovracompensazione al massimo di un giro di ciclo (≤ 60 s su 120 min di TTL). `host_pause_check` nel Filter può registrare l'evento di pausa per primo: stesso Telegram e stessi azzeramenti di prima, con i doppioni già filtrati entro 60 s.
