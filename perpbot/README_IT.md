# Perp Desk – paper trading su perpetual Hyperliquid

Modulo **separato** dal bot memecoin: cassa propria ($1000 finti), dati reali e gratuiti dall'API
pubblica di Hyperliquid, **solo paper**. Leva massima **10x** (tetto rigido nel codice).

## Avvio / stop
```
./start_perp.sh          # avvia in background (PERP_PAPER_ONLY=1), PID in perp.pid
./stop_perp.sh           # ferma il processo
touch STOP               # kill switch: chiude tutte le posizioni e blocca nuovi ingressi (rm STOP per riattivare)
touch PAUSE              # niente nuovi ingressi, le posizioni aperte restano protette dai loro stop
./venv/bin/python -m unittest discover -s tests -v     # test offline
./venv/bin/python backtest/perp_bt.py --no-fetch       # rilancia il backtest dalla cache
```

## Architettura (pronta per il live)
```
perp/hl_data.py      client sola lettura API /info (rate limit con budget 600/min su 1200 consentiti)
perp/exchange.py     interfaccia astratta Broker: place_order, cancel, open_orders, positions, balance, funding
perp/paper_broker.py PaperBroker: book L2 reale, fee, funding, margine isolato, liquidazione, latenza, rifiuti
perp/live_broker.py  HyperliquidLiveBroker: STUB, solleva errore, non firma nulla, nessuna chiave
perp/strategy.py     strategia pura (stessa funzione per bot e backtest)
perp/risk.py         sizing, leva dalla volatilità, limiti di portafoglio, correlazione
perp/learning.py     signals.csv e trade ombra (shadow_trades.csv)
perp/engine.py       ciclo: dati -> strategia -> rischio -> esecuzione via Broker -> log/snapshot
perp/config.py       config.json + overrides.json (con limiti)
```
Per il live si cambia solo il broker (vedi `LIVE_READINESS.md`): strategia e rischio non si toccano.

## Strategia
- Universo: le 18 perp con più volume 24h (min $20M), aggiornato ogni ora.
- Trend 1h: EMA50 sopra/sotto EMA200 di almeno 1 ATR e chiusura 1h oltre EMA50 (long e short).
- Regime: ATR 1h tra 0,25% e 3,5% del prezzo.
- Ingresso 15m: chiusura oltre il massimo/minimo delle 40 barre precedenti, volume ≥ media, non oltre 1,5 ATR15.
- Funding: niente long con funding > 0,05%/h (o short con < −0,05%/h). Mean reversion su funding estremo disponibile ma spenta.
- Uscite: stop 1,5 ATR 1h, TP1 vende il 50% a 1,5R e porta lo stop a pareggio, trailing 2,5 ATR da +1R, max 48 ore.
- Stop e TP1 sono ordini trigger reduce-only "sull'exchange", come si farebbe su Hyperliquid.

## Rischio
- 1% dell'equity a rischio allo stop (fee e slippage inclusi).
- Leva calcolata dalla volatilità: la più alta che tiene la liquidazione ad almeno 2 volte la distanza dello stop,
  massimo 10x (e mai sopra il massimo dell'asset). Dopo il fill si ricontrolla: se la liquidazione è troppo vicina si chiude subito.
- Max 4 posizioni, esposizione max 3x l'equity, margine max 30% per trade, stop giornaliero −5% (giorno di Roma).
- Correlazione: massimo 2 posizioni nella stessa direzione tra coin con correlazione 1h ≥ 0,7 (ultimi 7 giorni).

## Simulazione realistica
Fill camminando il book L2 reale al momento dell'ordine (dopo 150–600 ms di latenza simulata), fee
Hyperliquid tier base (taker 0,045%, maker 0,015%), 2% di ordini rifiutati, funding orario reale scalato
dal margine isolato, liquidazione con margine di mantenimento dai marginTables (metà del margine iniziale
alla leva massima). In liquidazione il paper perde tutto il margine (più prudente di Hyperliquid).

## File prodotti
`state.json`, `events.jsonl`, `run.log`, `equity.csv`, `trades.csv`, `signals.csv` (ogni segnale e quasi-segnale
con la decisione), `shadow_trades.csv` (trade ombra con il filtro che li ha bloccati e il risultato in R),
`perp_snapshot.json` (stato completo in sola lettura, aggiornato ogni ~5 s, per la dashboard).

## overrides.json (Stewy / ticher)
Sezioni `strategy` e `risk`, ricaricate al volo. Ogni valore viene limitato (es. leva ≤ 10, esposizione ≤ 3x,
stop giornaliero ≤ 5%, rischio per trade ≤ 2%). Le chiavi non previste vengono ignorate. Esempio in `overrides.example.json`.

## Backtest
Risultati in `backtest/results.md`. In breve: su 7 settimane nessun vantaggio dimostrato dopo i costi
(train +4,7%, test fuori campione −7,3%). Il modulo gira per raccogliere dati, non per il live.
