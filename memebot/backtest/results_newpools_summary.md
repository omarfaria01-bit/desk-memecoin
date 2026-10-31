# Backtest newpools (senza bias di sopravvivenza) - riepilogo 31/10/2026 00:10 (ora di Roma)

**In breve:** su 100 pool nuove campionate, solo 3 producono un segnale di ingresso e tutte e 3 escono in stop: A -11%, B -13%, B-live -15%, C15 -18% per trade, win rate 0%. 68 pool smettono di scambiare entro 30 minuti e 29 non danno mai segnale. Il vantaggio degli stop larghi o dinamici visto sul campione survivor NON si conferma: con size fissa, più largo lo stop, più grande la perdita. Però 3 trade non bastano per concludere nulla.

## Dati
- 100 pool campionate dallo snapshot new_pools, tutte create in una finestra di circa 4 minuti (dex: pump-fun 73, meteora-damm-v2 9, pumpswap 7, meteora-dbc 6, altri 5).
- no_ohlcv 0, file OHLCV vuoti 0 (il bug del 429 non si è presentato; in exit_bt.py l'ho comunque corretto: un fetch fallito non salva più nulla). Pool "cucite" su migrazione: 0. Pool morte durante l'hold: 0.
- Conteggi: 68 corte (meno di 30 minuti di scambi), 29 senza segnale, 3 trade.
- Oggi solo 1 delle 100 pool ha una riserva di almeno $8k. 2 token hanno un'altra pool con almeno $8k: sono possibili migrazioni non seguite, perché la pool originale è morta dopo 1 candela, prima di qualsiasi segnale.

## Regole di uscita, newpools (3 trade)
| Set | Win | Medio = mediana | Somma = MaxDD | Compost. 10% = DD |
|---|---|---|---|---|
| A (rif.) | 0% | -11.0% | -33% | -3.3% |
| B | 0% | -13.0% | -39% | -3.8% |
| B-live (SL -12, time stop 240) | 0% | -15.0% | -45% | -4.4% |
| C15 | 0% | -18.0% | -54% | -5.3% |
- Tutte le uscite sono stop. Con la sensibilità a -90% sulle pool morte i numeri sono identici, perché nessuna pool è morta durante l'hold.
- Con il filtro di liquidità allo snapshot (almeno $8k) ci sono 0 trade: le 3 pool erano sotto $8k a pochi minuti di vita.
- Run survivor di confronto (43 trade): A +9.7% medio, win 53%, compost. +49.3%, DD -7.5%; B +4.5%, 33%, +19.0%, -16.2%; B-live +6.5%, 49%, +29.3%, -10.8%; C15 +6.1%, 51%, +27.5%, -13.2%.

## Test extra di Kiwi (su survivor, 43 trade; su newpools tutte le varianti danno circa -15%)
1) Trailing dopo il TP1: con il 10% il medio scende a +4.6% (compost. +20.4%), contro +6.5% (+29.3%) con il 15%. Attivare il trailing già dal TP1 (+30%) non cambia nulla (+4.6%). Conviene tenere il 15%.
   Max hold per LP non bloccata: lo stato della LP viene da GeckoTerminal (locked_liquidity_percentage), ma è il valore di oggi, non quello all'ingresso. Su 40 trade su 43 è noto: 39 hanno la LP bloccata e 1 no (sui 3 newpools, tutte bloccate), quindi il confronto per stato della LP non si può fare davvero. Hold a 60 minuti solo per la LP non bloccata: +5.9% (DD -12.6%). Hold a 60 minuti per tutti: +3.6%, win 37%, compost. +14.8%, DD -17.4%, contro +6.5% e -10.8% con 180 minuti. Conviene tenere 180.
2) Young momentum: tutti gli ingressi arrivano tra 9 e 227 minuti dopo la nascita della pool, quindi lo split sotto/sopra 6 ore dà 43 contro 0 trade e non serve. Split alternativo a 30 minuti: 16 trade sotto i 30 minuti (win 56%, medio +7.7%, mediana +3.1%, DD compost. -3.0%) contro 27 sopra (44%, +5.7%, mediana -15.0%, DD -10.1%). Gli ingressi più precoci sembrano più puliti, ma il campione è piccolo.

## Stop loss (sl_bt.py newpools, 3 trade; dettagli in results_sl_newpools.md)
- Size fissa (expectancy; stop scattati/poi rimbalzati): -8: -14.8% (3/2); -12: -17.8% (3/2); -15: -17.8% con conf1 e -16.5% con conf2/3; -20: -21.7%; -25: -25.5%; -30: -36.0%; senza SL: -52.5%. Win rate 0% ovunque, MaxDD uguale alla somma.
- Size scalata sul rischio: tutto tra -12% e -14% (-25: -12.0%, -15: -13.9%, -8: -14.1%; senza SL: -12.7%).
- Conferma 1/2/3: nessun effetto sistematico (conf2/3 un po' meglio a -15, peggio a -20/-25).
- Stop dinamico 3×ATR10 (limiti -8..-30, size scalata, conf2): -16.1%, 3 stop, 0 rimbalzi. La taratura in-sample su newpools sceglie 2.5×ATR±3: -15.3%.
- Run survivor di confronto: -8: +0.9%; -15: -1.3%; -25 conf2: +5.0%; -30: +7.1% (DD -216%); dinamico 3×ATR: +6.9% (compost. +32.1%, DD -10.9%).
- Combinato newpools più 3 trade del bot (6 trade): sempre negativo.

## Risposta e raccomandazione
- Il vantaggio degli stop larghi o dinamici non sopravvive quando si includono token morti e rug: con size fissa, più largo lo stop, più grande la perdita. Il campione però è troppo piccolo per dire che lo smentisce.
- Raccomandazione: tenere lo stop attuale (-12, comunque non oltre -15). Uno stop dinamico o largo va valutato solo con la size scalata.
- ATTENZIONE: il miglior stop sul survivor (-30, e il dinamico che arriva fino a -30) è oltre -15, quindi richiederebbe di cambiare OVR_LIMITS e CRASH_EXIT_PCT nel codice. Con questi dati non lo consiglio.
- La lezione vera: 97 pool nuove su 100 non arrivano mai a un ingresso, quindi è il filtro d'ingresso a proteggere il desk, più delle regole di uscita.

## Limiti
Unica finestra di creazione di circa 4 minuti, solo 3 trade newpools, stato della LP preso a posteriori, liquidità nel tempo stimata con un proxy, possibili 2 migrazioni non seguite. Serve ripetere con più snapshot distribuiti nella giornata. desk.py, overrides.json e il bot non sono stati toccati; nessun wallet.
File: backtest/results_newpools.md/.json, results_survivor.md/.json (con i test extra), results_sl_newpools.md/.json, results_newpools_summary.md; exit_bt.py ha la nuova opzione --extra (copia della versione precedente in exit_bt_v2.py).
