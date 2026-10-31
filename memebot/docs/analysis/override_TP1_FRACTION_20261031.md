# Cambio override: TP1_FRACTION 0.30 -> 0.50 (solo paper)

- Scritto in overrides.json: 2026-10-31 08:22:13 (Europe/Rome, UTC+1)
- Letto dal bot (run.log): 2026-10-31 08:22:28 — "TP1_FRACTION 0.3->0.5"
- Backup precedente: overrides.json.bak-20261031-082208
- Invariati: STOP_LOSS_PCT -12, TP1_PCT +30, TRAIL_START_PCT 30, TRAIL_DIST_PCT 15, TIME_STOP_MIN 240, hold max 180
- desk.py non toccato, bot non riavviato.
- Motivo: studio uscite su 11 trade ombra con massimo >= +30% (replay su price_paths, script /tmp/exitstudy/sim.py): attuale +34,7% medio (10/11 win), TP1 50% +38,0% (11/11).
- Limiti: campione di una notte, buchi di 22-29 min nei percorsi, trade ombra da $30.
- Confronto: Mark stasera alle 23:00. Ripetere lo studio con almeno 25 trade sopra +30% e dati senza buchi.
