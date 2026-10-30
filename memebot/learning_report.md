# Report di apprendimento — Desk Memecoin (v5, solo paper)

_Aggiornato: 30/10/2026 19:40 (ora di Roma) — a cura di ticher_

## Stato dati
- Trade chiusi v5: **0** (trades.csv non ancora creato). Nessuna proposta su punteggi, filtri o uscite finché non ci sono circa 20 trade chiusi.
- events.jsonl: 125 eventi (101 scarti, 7 passati, 15 info, 2 avvisi). Nota: molti eventi precedono il riavvio v5.

## Cosa adatta già il Learner interno (desk.py, `learn_from_trade`)
- Soglia del punteggio: +0.02 se il win rate recente è basso, −0.01 se alto, entro THRESHOLD_MIN/MAX.
- Mette in lista nera il creatore dopo certe perdite.
- Registra i segnali deboli all'entrata dei trade in perdita.
Quindi non proporrò ritocchi manuali della soglia che si sovrappongano a questa logica.

## Motivi di scarto principali (101 scarti)
| Agente | Motivo | N | % |
|---|---|---|---|
| Filter | LP bloccata troppo poco (spesso 0%) | 27 | 27% |
| Filter | Reti di wallet collegati (bundle) | 26 | 26% |
| Filter | Liquidità troppo bassa | 15 | 15% |
| Filter | Mint non revocata | 12 | 12% |
| Filter | Top holder troppo concentrati | 10 | 10% |
| Filter | Distribuzione coordinata (stesse quote) | 4 | 4% |
| Analyst | Punteggio sotto soglia / momentum / grafico | 7 | 7% |

Il 93% degli scarti avviene ai filtri di sicurezza, il 7% all'Analyst. Sono filtri anti-rug: per ora nessuna proposta di allentarli.

## Prossimi passi
Analisi automatica alle 10, 16 e 22 (ora di Roma). Prime proposte numeriche da ~20 trade chiusi.
