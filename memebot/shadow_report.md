# Relazione sui trade ombra — Desk Memecoin v6 (solo paper)

_Aggiornata: 30/10/2026 20:38 (ora di Roma) — a cura di ticher. v6 attiva dalle 20:02, quindi circa 35 minuti di dati._

## In breve
**Il campione è troppo piccolo per qualunque conclusione.** C'è 1 solo trade ombra, ancora aperto, e nessuno chiuso. `shadow_trades.csv` non esiste ancora perché il file si crea alla prima chiusura. Il learner non ha fatto aggiustamenti. Tutto quello che segue è descrittivo.

## Numeri
| Voce | Valore |
|---|---|
| Trade ombra aperti (`shadow_open`) | 1 |
| Trade ombra chiusi (`shadow_close`) | 0 |
| Aggiustamenti del learner (`learn_adjust`) | 0 |
| Trade veri chiusi in v6 | 1 |
| Scarti dalle 20:02 | 63 |

### PnL medio e win rate per motivo di scarto (trade ombra)
| Motivo | Aperti | Chiusi | PnL medio | Win rate |
|---|---|---|---|---|
| bundle (grave) | 0 | 0 | – | – |
| top10 (grave) | 1 | 0 | – | – |
| equal-share (grave) | 0 | 0 | – | – |
| LP (grave) | 0 | 0 | – | – |
| score basso / quasi-soglia | 0 | 0 | – | – |

L'unico trade ombra aperto è **PUMP** (`pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn`). È stato scartato per top 10 holder al 60,5% (soglia grave 50%), con punteggio 0,553 e costo andata e ritorno del 2,06%. Ha aperto alle 20:14 e l'esito non è ancora noto.

### Migliori e peggiori
Non ci sono ancora trade ombra chiusi da classificare. L'unico dato su un segnale soft viene da un trade **vero**: **HYPERCHAN** (`3ApVr1xphYMpi9dn3rpqU4hwvpmCQDVwxET1rdCDpump`), con segnale soft equal e size ×0,5. Ha chiuso a **+6,37%** (+1,29 $) in 2 minuti con il trailing al 15%, dopo un picco del +46,1%. Il learner l'ha già registrato in `learner_shadow.equal` (1 esito su 15 necessari).

## Quali filtri scartano vincenti o evitano perdenti
Al momento non si può dire: servono almeno 15 esiti per segnale, la stessa soglia che usa il learner. Si vede però già quanto poco i trade ombra coprono gli scarti. Sui 63 scarti v6, la maggior parte è HARD e quindi non viene mai seguita in ombra:
| Motivo di scarto (v6) | N |
|---|---|
| Liquidità troppo bassa (HARD) | 15 |
| LP bloccata 0% (HARD) | 10 |
| Momentum non confermato (Analyst) | 8 |
| LP bloccata < 25% (grave, graduato) | 8 |
| Mint non revocata (HARD) | 5 |
| Bundle ≥ 25% (grave) | 4 |
| Top 10 > 50% (grave) | 4 |
| Creatore tiene troppo (HARD) | 3 |
| Momento sbagliato, calo recente (Analyst) | 3 |
| Altri | 3 |

Ci sono 16 scarti gravi graduati (LP, bundle, top 10), ma solo 1 è diventato un trade ombra. Gli altri non sarebbero stati promossi dall'Analyst, quindi non entrano nel campione.

## Aggiustamenti del learner
Nessuno. `learner_shadow` contiene solo `equal` con 1 esito (+6,37%, trade vero) e tutti i delta a 0. Ogni passo richiede almeno 15 esiti.

## Proposte (2-3, prudenti visto il campione)
1. **Nessuna modifica a soglie e filtri per ora.** Va lasciato girare il sistema finché ogni segnale non ha almeno 15 esiti chiusi. Ritoccare `overrides.json` adesso confonderebbe il learner. Ricontrollo nelle analisi delle 22, delle 10 e delle 16.
2. **Seguire in ombra anche gli scarti "momentum non confermato"** (8 su 63, il motivo principale dell'Analyst). È una modifica al codice e la decide Kiwi. Sono candidati che hanno già superato i filtri di sicurezza, quindi sono i più informativi per capire se l'Analyst è troppo severo. Costo stimato: circa 8 shadow ogni 35 minuti, entro il limite di 60 aperti.
3. **Uscite (per Stewy, solo da monitorare):** HYPERCHAN ha restituito quasi tutto il picco del +46% con il trailing al 15%. Con n=1 non è una proposta. Se nei prossimi 20 trade il divario tra `peak_pct` e il PnL finale resta sopra i 25 punti, valuterò un trailing più stretto dopo il TP1.
