# Compito 15 — più trade in paper: cosa dicono i dati shadow

Dati: `data-snapshot` del 31/10 13:18 (18h39 osservate, bot avviato 30/10 18:40). 225 righe shadow,
121 scartate da "Analyst: momento/momentum", 5 trade reali. Script: `docs/analysis/m5_momentum_report.py`
(solo lettura, su copie `/tmp`; righe valide = `valido=1` e `invalid_for_learning` vuoto).

## Le soglie esaminate (volume, liquidità, età minima, momentum, punteggio)

| soglia | esito | perché |
|---|---|---|
| **MIN_CHANGE_M5_PCT** (momentum, calo a 5 min dalle candele) | **allentare 0% -> -8%** | unica soglia con segnale positivo pulito, vedi sotto |
| MIN_BUYSELL_M5, MIN_CHANGE_H1_PCT, VOL_ACCEL_MIN (altre sotto-regole momentum) | **non toccare** | tutte pnl medio negativo (-12% a -24%), rug 0-21%; contengono lezioni negative (BEAST, DESSA, PILLSEY, GLITCH, HYPERCHAN) |
| SCORE_THRESHOLD | **non toccare** | n=4 validi, pnl medio -39,5%, 1 rug/4: allentare peggiorerebbe |
| MIN_VOLUME_H1, MIN_LIQUIDITY_USD | **nessuna proposta** | il bot non segue in shadow i token scartati per volume/liquidità (vedi gap sotto): zero dati di esito, non si può provare un numero |
| età minima dall'ingresso/migrazione | **esclusa dal compito** | è l'anti-dump (compito 10, priorità massima): non toccata |

## La proposta: MIN_CHANGE_M5_PCT 0.0 -> -8.0

Il codice ha due controlli sul calo a 5 minuti, in sequenza:
1. un taglio grezzo sul tick (`chg_m5 < -8%`, riga 4491 di `desk.py`), **invariato** dalla proposta;
2. il gate vero `m5_gate` sulle candele (righe 5852-5857), soglia `MIN_CHANGE_M5_PCT` = **0%**: qualsiasi
   calo a 5 min, anche di pochi decimi, scarta.

Separando gli scarti "Analyst: momento/momentum" per sotto-regola esatta (dal messaggio di reject in
`events.jsonl`, agganciato per mint+minuto all'apertura dello shadow):

| sotto-regola | n validi | pnl medio | rug | win >=30% |
|---|---|---|---|---|
| gate candele (0% a -8%, quello proposto) | 9 | **+17.5%** | 0/9 | 4/9 (44%) |
| taglio grezzo tick (< -8%, invariato) | 26 | -24.0% | 3/26 (12%) | 2/26 |
| acq/vend 5m debole | 33 | -12.5% | 7/33 (21%) | 4/33 |
| calo 1h fuori banda | 11 | -20.4% | 1/11 (9%) | 0/11 |
| accelerazione volume debole | 11 | -12.9% | 0/11 | 0/11 |

Solo la fascia tra 0% e -8% (quella che la proposta libera) è in utile, con **zero rug** sul campione;
tutte le altre sotto-regole momentum restano negative e non vengono toccate. Nessuna delle 6 lezioni
negative (BEAST, DESSA, PILLSEY, GLITCH, BNB falso, HYPERCHAN) rientra nel campione n=9 usato come prova.
Il valore proposto (-8.0) è anche il limite massimo di allentamento già fissato in `OVR_LIMITS` nel
codice (riga 6151): non richiede un nuovo clamp, solo il valore in `overrides.json`.

**Controlli positivi** (`docs/kiwi_controls.md`): nessuno dei 6 casi Kiwi (Dacity, GTA6, STONK, FLOAT,
neet, LUMI) viene bloccato da `MIN_CHANGE_M5_PCT`; LUMI era bloccato da un calo più profondo (-9,6%/
-14%/-28% visti nei log), quindi resta scartato anche con la soglia a -8%. Nessuna regressione.

## Numeri attesi

- Candidati liberati da questo solo gate: ~9 in 18,6 h osservate, cioè **~11-12/giorno**.
- Devono ancora superare gli altri controlli dell'Analyst (acq/vend 5m, accelerazione volume, VWAP,
  punteggio) e i limiti di esposizione/cooldown: stima prudente **+2-5 trade paper reali/giorno**
  (il bot ne ha fatti solo 5 in tutta la finestra osservata, quindi anche un piccolo numero è rilevante
  per smuoverlo).
- Rischio atteso sul campione comparabile: pnl medio +17,5%, 0% rug — nessun aumento di rug rate atteso
  da questa soglia specifica.

## Gap scoperto (non in questo compito)

`MIN_VOLUME_H1` (righe 4243-4244 di `desk.py`) scarta in silenzio (nessun log, nessuno shadow) e
`MIN_LIQUIDITY_USD` (righe 4254-4258) viene loggato ma non seguito in shadow: **zero righe di esito**
per queste due soglie, nonostante siano tra le cause di scarto più frequenti (225 "liquidita bassa"
nel periodo, "volume insufficiente" altre 35 + probabilmente molte di più mai contate perché il primo
controllo è un `continue` silenzioso senza contatore). Senza dati non si può proporre un numero per
queste due soglie: la copertura più ampia degli scarti in shadow (compito 11) risolverebbe il problema
per la prossima iterazione.

## Riproducibilità

`python3 docs/analysis/m5_momentum_report.py /tmp/desk_data/data/shadow_trades.csv /tmp/desk_data/data/events.jsonl`
