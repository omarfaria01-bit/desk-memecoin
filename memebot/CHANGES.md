# Correzioni a desk.py (rispetto a desk_original.py)

I parametri della strategia (stop, take profit, trailing, filtri, soglie) sono **invariati**.
I numeri di riga si riferiscono a `desk.py` (tra parentesi la riga dell'originale).

1. **Prezzo d'ingresso dal fill reale** – riga 1082-1086 (orig. 1045).
   `entry_mark` ora è il costo reale per token (`cost / tokens`): in paper include commissione 1% e
   impatto sulla pool, in live viene dagli importi effettivi dello swap (SOL spesi / token ricevuti).
   Il prezzo visto prima dell'acquisto resta salvato come `quote_price`.
2. **Vendita live solo della posizione del bot** – righe 1016-1022, 1037 (orig. 990-993).
   Vende `min(quantità registrata × frazione, saldo del wallet)`, mai token extra/airdrop.
   La quantità restituita è quella effettivamente venduta.
3. **Divisione per zero** – riga 182-184 (orig. 526): se `MAX_TOP10_PCT` è 0 niente crash.
   Aggiunta `safe_div` (riga 157) usata anche nel recap (riga 1291) e per il prezzo d'ingresso;
   controllo `liq_entry > 0` nel Risk (riga 1203); preventivo Jupiter con 0 token (riga 969) e
   `got <= 0` dopo lo swap (riga 996); prezzo/fill non validi nel PaperBroker (riga 814).
4. **STOP ricontrollato** – riga 1074 (subito prima di eseguire l'acquisto), riga 979
   (live/dry: dopo aver preparato e firmato la transazione, prima dell'invio), riga 1198
   (Risk: controllato di nuovo per ogni posizione, non solo a inizio ciclo). Funzione `stop_active()` riga 153.
5. **Lock acquisti/vendite** – `TRADE_LOCK = asyncio.Lock()` (riga 151). Acquisto (`_execute_buy`, riga 1051/1057),
   vendita (`_sell_position`, riga 1126/1130) e aggiornamento della cassa live (riga 1226) non si sovrappongono più.
6. **Altri bug**
   - riga 646: `knownAccounts` di RugCheck con valore non-dizionario faceva crashare il controllo sicurezza.
   - riga 148 / 1386 / 319: senza terminale (nohup, servizio) il pannello `rich` non si aggiornava:
     con `--headless` o `DESK_HEADLESS=1` stampa i log e una riga di stato al minuto su stdout.
   - riga 1418: con `DESK_PAPER_ONLY=1` il bot rifiuta di partire in dry/live (sicurezza).

Nessuna modifica a: logica di Scout/Filter/Analyst, punteggi, modello che impara, recap (salvato
in file locale, nessuna credenziale necessaria).

---

## Aggiornamento 30/10/2026 – filtri allentati e override esterni

> I numeri di riga delle sezioni sopra si riferiscono alla versione precedente, salvata in
> `desk_before_overrides.py`. Quelli qui sotto si riferiscono all'attuale `desk.py`.
> Resta tutto in **PAPER**: nessun wallet, nessuna chiave, nessuna modifica al codice live.

### A. Filtri d'ingresso allentati (righe 74-95, valori originali nei commenti)
Restano **invariati** i controlli anti-rug duri: mint revocata, freeze, rug segnalato, tassa di
trasferimento, reti insider/bundle, quota insider, wallet con quote identiche, creatore in lista nera,
quota del creatore, rischi "danger" di RugCheck.

| Parametro | Prima | Ora |
|---|---|---|
| `SCORE_THRESHOLD` (soglia Analyst) | 0.60 | **0.50** |
| `MIN_LP_LOCKED_PCT` (LP bloccata o bruciata) | 90% | **50%** (0% sempre scartata, riga 650) |
| `MIN_LIQUIDITY_USD` | 15000 $ | **8000 $** ($0 sempre scartata, riga 734) |
| `MAX_TOP10_PCT` | 25% | **35%** |
| `MIN_HOLDERS` | 150 | **100** |
| `MIN_VOLUME_H1` | 40000 $ | **25000 $** |
| `MIN_CHANGE_H1_PCT` | -10% | **-15%** |
| `MIN_CHANGE_M5_PCT` | -5% | **-8%** |
| `RSI_MIN` / `RSI_MAX` (solo punteggio) | 50 / 78 | **45 / 80** |
| `REQUIRE_CHART` | True | **False** |

- **Grafico assente** (righe 778-790): non è più uno scarto. I 5 segnali di grafico
  (`CHART_FEATURES`, riga 186) valgono 0.5 (neutro, `NO_CHART_NEUTRAL`) e il punteggio perde
  `NO_CHART_PENALTY` = 0.05. Nel motivo compare "grafico assente -0.05".
- **Soglia salvata** (righe 263, 283-289): `state.json` conservava la soglia 0.60. Ora lo stato
  memorizza `threshold_base`. Se la soglia di partenza nel codice cambia, la soglia riparte da quella
  nuova (0.60 -> 0.50), sempre dentro `THRESHOLD_MIN`–`THRESHOLD_MAX`.

### B. Override esterni da `overrides.json` (righe 1120-1337)
Un consulente esterno può scrivere `/workspace/memebot/overrides.json`. Un esempio è
`overrides.example.json`; il file vero non viene creato dal bot.
- Il Risk lo controlla a ogni ciclo (riga 1427) e lo rilegge solo quando cambiano mtime o dimensione.
- File mancante, JSON non valido, campi assenti o di tipo sbagliato: avviso nel log, poi restano gli
  ultimi valori validi. Il bot non si ferma mai per questo. Con `null` un campo globale torna al default.
- **Limiti di sicurezza fissi** (`OVR_LIMITS`, riga 1132). I valori fuori limite vengono riportati
  dentro e il fatto viene loggato:
  - stop loss da -3% a -15% (negativo; un valore positivo viene invertito)
  - trailing distance 3–15%
  - trailing start 3–100%
  - TP sell fraction 0.1–1.0
  - TP 5–500%
  - max hold 5–720 min

  Uno stop più largo dell'originale -8% genera un avviso "ATTENZIONE".
- **Globali**: cambiano solo i valori in memoria. I default originali restano in `OVR_DEFAULTS`:
  se cancelli il file si torna a quelli, e anche questo viene loggato.
- **Per posizione**: valgono solo per i mint posseduti in quel momento; gli altri vengono ignorati.
  - `stop_loss_pct`, `take_profit_pct`, `tp_sell_fraction` e `trail_dist_pct` si sommano ai globali
    (righe 1441-1444).
  - `sell_all` e `sell_half` (righe 1446-1462) passano dalla normale vendita paper sotto `TRADE_LOCK`.
  - Ogni azione viene eseguita **una sola volta** per versione (`updated_at`, oppure `action_id`
    se presente). Il marcatore `ovr_action_done` viene salvato nella posizione, quindi l'azione non
    si ripete neanche dopo un riavvio.
  - STOP ha la precedenza. Le azioni valgono solo in paper.
  - `sell_half` non consuma la presa di profitto (`mark_tp`, righe 1364-1382).
- Ogni override applicato, ogni limite e ogni ritorno ai default finisce in `events.jsonl` con
  `kind: "override"` e il motivo, e compare anche in `run.log`.
- `export_dashboard.py` mostra stop, TP e trailing effettivi di ogni posizione (campo `eff`).

---

## Aggiornamento 30/10/2026 (sera) – narrative da X e strategia v4

> Righe riferite all'attuale `desk.py`. Le versioni precedenti sono `desk_before_narratives.py` e
> `desk_before_v4.py`. Sempre e solo **PAPER**. I controlli anti-rug sono invariati.

### C. Bonus narrativa da `narratives.json` (Radar) – righe 779-906 (modulo), 963-975 (Analyst)
- Il file viene riletto solo se cambiano mtime o dimensione. Se è assente o non valido, compare un solo
  avviso e il bonus è zero.
- Se `updated_at` è più vecchio di 3 ore, o manca, i dati sono considerati vecchi e non danno bonus.
- **Corrispondenza forte** (peso 1.0): il simbolo è uguale a un ticker (senza `$`, maiuscole o minuscole
  indifferenti), oppure il mint è nella lista `mints`. Per un mint il peso è moltiplicato per
  `mint_confidence` (0–1). Un mint citato su X **non** vuol dire token "ufficiale".
- **Corrispondenza debole** (peso 0.5): il nome contiene una parola chiave di almeno 5 caratteri.
- Esclusi sempre: i mint di USDC, WSOL/SOL e USDT e i ticker di stablecoin o SOL.
  I mint in `blocklist_mints` non ricevono mai bonus e vengono **scartati** dall'Analyst.
  La blocklist resta valida anche se le narrative sono vecchie.
- Bonus = forza × peso × trend (rising 1.0, stable 0.6, fading 0), sommato su tutte le narrative,
  con un **tetto di +0.08**.
  - Con la formula richiesta, quasi ogni corrispondenza arriva al tetto: per ridurlo c'è `NARR_SCALE`.
  - `velocity` (null = neutro) e `trusted_mentions` vengono letti ma non cambiano il bonus.
- Il bonus si applica solo **dopo** Filter, anti-rug e controlli di momento, e non salta mai un filtro.
  Ogni bonus finisce in `events.jsonl` (Analyst, `kind: "narrative"`) e nel contatore `stats.narrative`.

### D. Contabilità paper realistica – righe 995-1013
- `mark_value` = valore netto dopo impatto sulla pool e commissione d'uscita, con la stessa formula del bot
  Terminus.
- `mark_pnl_pct` confronta quel valore con il costo reale della quantità ancora in mano.
- Equity, stop, trailing, prese di profitto e stop a tempo usano tutti il valore netto
  (prima: solo prezzo, senza impatto).

### E. LP dei token pump.fun – righe 665-676
- La regola della LP bloccata viene saltata per i token sulla curva pump.fun.
- Viene saltata anche per i token migrati da pump.fun su PumpSwap o Raydium (LP bruciata dal protocollo).
  In questo caso serve un mint che finisce in `pump` **e** il launchpad Pump.Fun, oppure un mercato
  `pump_fun_amm` nel report RugCheck. Un mint "…pump" da solo si può generare apposta.
- Per tutti gli altri token resta la regola LP ≥ 50%.

### F. Conferma del momentum all'ingresso – righe 936-946
- Negli ultimi 5 minuti gli acquisti devono essere almeno 1.2 volte le vendite (`MIN_BUYSELL_M5`).
- Il volume degli ultimi 5 minuti deve superare la media di 5 minuti dell'ultima ora
  (volume 1h / 12; `VOL_ACCEL_MIN` = 1.0).
- Rimosso il tetto ai rialzi in 1h: `MAX_CHANGE_H1_PCT` 200 → nessuno (riga 100, 926).

### G. Uscite – righe 1655-1702
| | Prima | Ora |
|---|---|---|
| Prese di profitto | 50% a +100% | **25% a +40%**, **25% a +100%** (della quantità iniziale), trailing sul resto |
| Trailing | +15% / 10% | +15% / 10%, **7% dopo un massimo di +100%** |
| Stop a tempo | – | **esce dopo 45 min se non è sopra +5%** (solo prima del primo gradino) |
| Stop loss / max hold | -8% (2 controlli) / 360 min | invariati |

Gli override si applicano anche qui:
- `TP_PCT` / `TP_SELL_FRACTION` regolano il secondo gradino; il primo gradino non lo supera mai.
- `TRAIL_DIST_PCT` è il trailing normale. Il 7% dopo +100% vale solo se è più stretto.
- I limiti di sicurezza degli override restano tutti attivi.

### H. Dimensione delle posizioni – righe 1284-1296
| | Prima | Ora |
|---|---|---|
| Size | 3–10% equity secondo la fiducia, max 2% della pool | **min(5% equity, 1.5% della pool)** |
| Posizioni contemporanee | 20 | **6** |
| Esposizione totale | – | **max 30% dell'equity** |
| Pausa per perdita giornaliera | -20% | **-15%** dall'equity di inizio giornata (riga 80) |

### I. Registro per l'apprendimento – righe 365-379, 1600-1607
- `trades.csv` ora contiene anche:
  - tutte le feature d'ingresso (`f_trend`, `f_macd`, …), il punteggio, `narr_bonus` e `narr_themes`
  - il gradino TP raggiunto, `bs5`, `accel`, `liq_entry` e il motivo di uscita
- Se l'intestazione cambia, il file vecchio viene rinominato in `trades_old_<ts>.csv`.

### J. Limiti di frequenza – riga 161
- GeckoTerminal: 2.2 → **3.0 s**. RugCheck: 1.2 → **2.5 s**.

---

## Aggiornamento 30/10/2026 (19:30) – "Decisioni finali" (v5)

> Queste decisioni **sostituiscono** i valori della v4 (sezioni D–J qui sopra) dove sono in conflitto.
> La versione precedente è salvata come `desk_before_v5.py`. Sempre e solo **PAPER**.
> I controlli anti-rug sono invariati.

### K. Dimensione delle posizioni – righe 60-67, 1419-1436
| | Originale | v4 | **v5** |
|---|---|---|---|
| Size | 3–10% per fiducia, max 2% pool | min(5% equity, 1.5% pool) | **3% equity × (punteggio / 0.6)**, limitata tra **2% e 5%**, mai oltre l'**1% della liquidità** |
| Posizioni aperte | 20 | 6 | **8** |
| Esposizione totale | – | 30% | **30%** |
| Stop giornaliero | -20% dal recap | -15% dal recap | **-10% dall'equity a mezzanotte Europe/Rome** |

Il tetto dell'1% sulla pool vince sul minimo del 2%: su una pool piccola la size può scendere sotto il 2%.

Lo stop giornaliero ora ha una base propria (`day_key`, `day_start_equity` in `state.json`):
- viene azzerato a mezzanotte (Europe/Rome);
- il recap delle 8 non toglie più la pausa;
- al primo avvio dopo questo aggiornamento, la base è l'equity dell'ultimo recap.

### L. Costi simulati – righe 1073-1131
Ogni lato (acquisto e vendita) paga ora, oltre alla commissione dell'1% e all'impatto sulla pool:
- **slippage extra = 2 × size / liquidità**;
- **priority fee di 0.001 SOL**, al prezzo SOL preso da GeckoTerminal ogni 10 minuti
  (se GeckoTerminal non risponde, si usa un prezzo prudente di $150).

Questi costi valgono sia per il valore delle posizioni (stop, trailing, prese di profitto) sia per le vendite.

### M. Ingresso – righe 996-1029, 1429-1435
| | v4 | **v5** |
|---|---|---|
| Acquisti/vendite in 5 min | ≥ 1.2 | **≥ 1.3** |
| Volume 5 min | > volume 1h / 12 | **> volume dei 5 min precedenti** (dalle candele da 1 minuto); senza candele resta la regola v4 |
| VWAP 5 min | – | **prezzo sopra la VWAP dei 5 min**, se ci sono le candele |
| Filtro sui costi | – | **scarta se 5 × costo stimato andata+ritorno > TP1_PCT** |
| Tetto ai rialzi in 1h | nessuno | nessuno (invariato) |

Esempio con SOL a circa $94: per $30 il costo andata+ritorno è circa 5.5% su una pool da $8k
e circa 3.8% su una pool da $20k.

### N. Uscite – righe 1800-1860
| | Originale | v4 | **v5** |
|---|---|---|---|
| Stop loss | -8% (2 controlli) | -8% | **-10% (2 controlli)**; uscita immediata a -15% invariata |
| TP1 | – | 25% a +40% | **30% a +30%**, poi lo stop sale a **pareggio +3% netto** |
| TP2 | 50% a +100% | 25% a +100% | **30% a +80%** |
| Trailing (sul resto) | da +15%, 10% | da +15%, 10%; 7% dopo +100% | **da +40%, 15%; 8% dopo un massimo di +150%** |
| Stop a tempo | – | 45 min se ≤ +5% | **30 min se non almeno +5%** (solo prima del TP1) |
| Tempo massimo | 360 min | 360 min | **180 min** |

Le frazioni del TP sono calcolate sulla quantità iniziale. Lo stop di pareggio non supera mai il TP1 e
non scende mai sotto lo stop loss.

### O. Override – righe 1470-1503
Nuove chiavi globali, con questi limiti:

| Chiave | Limiti |
|---|---|
| `TP1_PCT` | 5 – 200 |
| `TP1_FRACTION` | 0.05 – 0.6 |
| `TP2_PCT` | 5 – 500 |
| `TP2_FRACTION` | 0.1 – 1.0 |
| `BREAKEVEN_PCT` | -5 – 15 |
| `TIME_STOP_MIN` | 5 – 240 |
| `TIME_STOP_MIN_PCT` | -10 – 30 |
| `TRAIL_TIGHT_AFTER_PCT` | 20 – 1000 |
| `TRAIL_TIGHT_DIST_PCT` | 2 – 15 |

- `TP2_PCT` e `TP2_FRACTION` sono gli stessi valori di `TP_PCT` e `TP_SELL_FRACTION`, che restano
  validi. Se nel file ci sono entrambi, vince `TP2_*`.
- Stop loss: limiti tra -15 e -3 (invariati). L'avviso "più largo" ora scatta sotto **-10%**
  (prima era sotto l'originale -8%).
- Il primo gradino non supera mai il secondo, anche con gli override.

### P. Narrative – righe 811-922
| Caso | v4 | **v5** |
|---|---|---|
| Bonus | forza × peso × trend (di solito +0.08 pieno) | solo se trend **rising**, **velocity > 0** e **strength < 0.7** |
| Corrispondenza forte (ticker o mint) | | +0.05 (per un mint, moltiplicato per `mint_confidence`) |
| Corrispondenza debole (parola chiave) | | +0.025 |
| Menzioni affidabili | | +0.02 se `trusted_mentions > 0` |
| Velocity null | neutro | **niente bonus** |
| Penalità tardiva | – | **-0.05** se strength ≥ 0.7 e velocity ≤ 0, oppure se il trend è fading |
| Penalità copia | – | **-0.05** se il ticker è uguale ma il mint non è tra quelli della narrativa, e la narrativa ha un mint con confidenza ≥ 0.7 |
| Tetto | +0.08 | **tra -0.08 e +0.08** |

- La blocklist di Radar scarta ancora il token.
- Le penalità vengono registrate in `events.jsonl` (`kind: "narrative"`) e contate in
  `stats.narrative_penalty`.

### Q. Altro
- `trades.csv`: nuove colonne `rt_cost_pct` (costo andata+ritorno stimato) e `vwap_gap`
  (% sopra la VWAP all'ingresso).
- `export_dashboard.py`: mostra i gradini TP ("[fatto] TP1 30% a +30% | TP2 30% a +80% | resto: trailing;
  stop a pareggio +3%").
  - Il valore e il P&L netti vengono scritti dal Risk (`mark_net`, `pnl_net`).
- `overrides.example.json` è aggiornato con le nuove chiavi.

---

## Aggiornamento 30/10/2026 (19:47) – v6: il paper simula il live

> Copia della versione precedente: `desk_before_v6.py`. Resta tutto **PAPER**: nessun wallet, nessuna
> chiave, nessuna transazione inviata, `DESK_PAPER_ONLY=1` sempre attivo. Filtri anti-rug HARD **invariati** (i 4 segnali soft sono graduati: vedi l'aggiornamento delle 20:02 qui sotto).
> Fonti gratuite: Jupiter quote API (`lite-api.jup.ag`, senza chiave) e RPC pubblico Solana.

| Cosa | Prima (v5) | Ora (v6) |
|---|---|---|
| Prezzo d'acquisto | formula: 1% + impatto + 2×size/liq + 0.001 SOL | **preventivo Jupiter reale** alla size vera (SOL→token), + margine 0.3% (MEV/deriva) + priority fee; formula solo se Jupiter non risponde |
| Prezzo di vendita | stessa formula | preventivo Jupiter reale (token→SOL) dopo la latenza; formula solo se Jupiter non risponde |
| Costo andata+ritorno nel filtro costi | stimato con la formula | **misurato**: preventivo d'acquisto + preventivo di rivendita dei token ottenuti |
| Controllo rivendibilità | nessuno | prima di comprare: se Jupiter non ha un percorso di vendita → scarto "non rivendibile" (anti-honeypot); se rivendere subito costa > 15% → scarto "rivendita troppo cara" |
| Priority fee | fissa 0.001 SOL | **dinamica** da `getRecentPrioritizationFees` (PumpSwap + Raydium) ogni 3 min: 75° percentile × 300k CU, minimo 0.0001 SOL, tetto `PRIORITY_MAX_LAMPORTS`, + base fee 5000 lamport; vendite in crollo: 90° percentile × 1.5. Dati vecchi → torna alla fissa |
| Latenza | nessuna (fill al prezzo del segnale) | 1–3 s casuali, poi nuovo preventivo: il fill è al prezzo **del momento dell'esecuzione** |
| Slippage massimo | non simulato | come in live: se nella latenza il prezzo peggiora oltre `SLIPPAGE_BPS` (3%) la transazione fallisce; dopo 2 vendite fallite usa `PANIC_SLIPPAGE_BPS` (15%) |
| Transazioni fallite | mai | acquisto 3%, vendita 5%, vendita in crollo 15%. Una transazione fallita **paga comunque la fee**. Acquisto: 1 nuovo tentativo; vendita: il Risk riprova ogni 15 s |
| Token invendibili | — | dopo 20 vendite senza percorso Jupiter la posizione viene azzerata (in paper) |
| Valore delle posizioni | formula | preventivo di vendita reale ogni 60 s (formula se manca) |
| Cassa disponibile | tutta | meno 0.03 SOL di riserva fee e 0.00204 SOL di affitto conto token per posizione aperta |
| Size massima | — | anche `MAX_SOL_PER_TRADE` (0.5 SOL), come in live |
| Limite di perdita totale | solo giornaliero | anche totale −30%: crea il file `HARD_STOP` e blocca gli acquisti; si riattiva cancellando il file (nuova base = equity attuale) |
| Controllo prontezza al live | — | all'avvio: scrive `live_readiness.json` e una riga nel log (Jupiter, RPC, fee, solders, limiti, cosa manca) |
| Codice live | invariato | aggiunto solo un limite: in live la size non supera `LIVE_MAX_TRADE_USD` (5 $) |

**Nuovi campi** in `trades.csv` (il file vecchio viene rinominato `trades_old_<ts>.csv`) e negli eventi
`buy`/`sell`/`fill_failed`/`prio_fee` di `events.jsonl`:
`quote_src` (jupiter/formula), `quote_impact` (% tra prezzo DexScreener e preventivo, acquisto),
`sell_quote_impact`, `latency_slip` (% di peggioramento nella latenza, acquisto), `sell_latency_slip`,
`prio_fee_sol` (somma di tutti i lati), `fill_failed` (n. transazioni fallite), `fee_failed_usd`.
Nota: con i preventivi la commissione `FEE_PCT` 1% non si applica più (le fee delle pool sono già nel
preventivo); resta solo nella formula di riserva.

**Nuove chiavi di configurazione** (in `CFG` o `config.json`): `SIM_REALISTIC`, `SIM_USE_QUOTES`,
`SIM_LATENCY_MIN_SEC`/`MAX_SEC`, `SIM_FAIL_PROB_BUY`/`SELL`/`SELL_FAST`, `SIM_BUY_RETRIES`,
`SIM_QUOTE_EXTRA_FEE_PCT`, `SIM_BASE_FEE_LAMPORTS`, `SIM_ATA_RENT_SOL`, `SIM_SEED`, `SELLCHECK_MAX_RT_PCT`,
`SELL_NOROUTE_WRITE_OFF`, `MARK_QUOTE_SEC`, `PRIO_DYNAMIC`, `PRIO_REFRESH_SEC`, `PRIO_PERCENTILE`,
`PRIO_PERCENTILE_URGENT`, `PRIO_URGENT_MULT`, `SWAP_COMPUTE_UNITS`, `PRIO_FLOOR_LAMPORTS`, `PRIO_ACCOUNTS`,
`MAX_TOTAL_LOSS_PCT`, `LIVE_MAX_TRADE_USD`, `LIVE_CONFIRM_TIMEOUT_SEC`, `LIVE_USE_JITO`, `LIVE_JITO_TIP_SOL`,
`LIVE_DRY_RUN_DONE`. Nessuna nuova chiave di esecuzione in `overrides.json` (le chiavi nuove dei segnali soft sono nell'aggiornamento delle 20:02).

Uso API in più: circa 3 preventivi Jupiter per acquisto, 2 per vendita, 1 al minuto per posizione aperta,
2 chiamate RPC ogni 3 minuti. Test offline (rete simulata) in `tests/test_v6.py`; anche
`tests/test_v5.py`, `test_ovr.py`, `test_narr.py` passano. La checklist per il live è in `LIVE_READINESS.md`.

## Aggiornamento 30/10/2026 (20:02) – v6: segnali soft graduati, shadow trade, Learner sugli shadow

> Motivo: zero trade. Nell'ultima ora prima del cambio (19:02–20:02) i 102 scarti erano soprattutto
> LP bloccata 29, bundle 23, liquidità bassa 19, top 10 13, mint non revocata 9, quote identiche 3.

**Verifica falsi positivi.** Le pool PumpSwap dei token usciti da pump.fun (mercato `pump_fun_amm`, LP 100%
bruciata) passavano già: gli scarti "LP 0%" erano pool con un solo fornitore di LP (chi l'ha creata può
ritirarla) o pool Meteora non verificabili → restano HARD. Falsi positivi trovati e corretti:
1. **bundle**: bastava *una* rete di wallet collegati qualsiasi (`graphInsidersDetected > 0`); su token con
   10–400 mila holder c'è quasi sempre. Ora conta la **quota dell'offerta** nelle reti (≤ 100 wallet: le reti
   enormi sono exchange/airdrop).
2. **top 10**: se tutte le quote erano ≤ 1% venivano moltiplicate ×100 (falsi "top 10 al 99%" sui token molto
   distribuiti). Ora quota = `uiAmount / offerta`.
3. **quote identiche**: con quote piccolissime (0,1–0,2%) l'arrotondamento crea coppie per caso; ora conta
   quanto tengono **insieme**.
Rigiocando 22 report RugCheck di token scartati: 11 ora superano il Filter (quasi tutti con un segnale soft),
i 6 con mint non revocata restano scartati.

**Segnali SOFT graduati** (penalità sul punteggio + size ridotta; scarto solo oltre la soglia grave):

| Segnale | Nessun segnale | Soft (penalità 0,05, size ×0,5) | Grave = scarto |
|---|---|---|---|
| bundle (reti collegate ≤ 100 wallet) | < 3% dell'offerta | 3–25% (o quota non calcolabile) | ≥ 25% |
| quote identiche (≥ 4 wallet) | — | insieme < 10% | insieme ≥ 10% |
| top 10 holder | ≤ 35% | 35–50% | > 50% |
| LP bloccata (token non pump.fun) | ≥ 50% | 25–50% | < 25% (0% sempre) |
| 3 o più segnali soft insieme | | | scarto |

Penalità e size si sommano/moltiplicano (2 segnali = −0,10 e ×0,25). Restano **HARD e invariati**: mint e
freeze non revocate, rug, tassa, LP 0% o non verificabile, insider > 10%, creatore > 5% o in lista nera,
< 100 holder, rischi "danger" di RugCheck (tranne "Top 10 holders" e "LP unlocked", già graduati), nessun
percorso di vendita Jupiter, liquidità sotto il minimo, costi.

**Shadow trade** (nessun denaro): per i token scartati per un segnale soft grave (se l'Analyst li avrebbe
promossi) e per quelli con punteggio fino a 0,05 sotto la soglia, il bot apre un'operazione ipotetica da 30 $
e la segue con le **stesse regole d'uscita** (crollo, stop a 2 controlli, TP1/TP2, trailing, stop a tempo,
tempo massimo, liquidità crollata, niente prezzo per 15 min = rug). Costo andata+ritorno misurato con i
preventivi Jupiter (solo preventivi); se Jupiter non ha un percorso di vendita, all'apertura o alla chiusura,
l'esito è un rug (−100%). Esiti in `shadow_trades.csv` (motivo dello scarto, segnali, punteggio, P&L netto,
picco, minuti, uscita, rug). Massimo 60 aperti, controllo ogni 30 s (una chiamata DexScreener per 30 token).

**Il Learner impara dagli shadow** (stato `learner_shadow` in `state.json`): per ogni segnale (bundle, equal,
top10, lp = fascia soft; `*_grave` = oltre la soglia grave; `score` = quasi promossi) tiene gli ultimi 50 esiti
(anche i trade veri con segnali soft). Con almeno 15 esiti e al massimo un passo all'ora:
- **allenta** se media netta ≥ +5%, win rate ≥ 45% e rug/−50% ≤ 10% (un rug vale −100% e conta 3 volte nel win
  rate); **stringe** se media < 0 o rug > 20%;
- fascia soft: penalità ±0,01 (limiti 0,02–0,10), size ±0,05 (limiti 0,3–0,8);
- soglie gravi: bundle ±2 (15–35%), quote identiche ±1 (6–14%), top 10 ±1 (45–55%), LP ∓2,5 (15–40%);
- `score`: soglia −0,005 solo se i quasi promossi sono in utile (media ≥ +5%, win ≥ 50%, rug ≤ 10%); mai sotto
  `THRESHOLD_MIN` (oggi 0,50 = soglia attuale, quindi per ora non può scendere).
Gli scarti HARD non vengono mai toccati. Ogni passo è un evento `learn_adjust` (con n, win, media, rug) e una
riga nelle lezioni della dashboard.

**Nuove chiavi `overrides.json`** (sezione `global`, con limiti fissi): `TOP10_HARD_PCT` (40–60),
`EQUAL_HARD_PCT` (5–20), `BUNDLE_SOFT_PCT` (1–10), `BUNDLE_HARD_PCT` (10–40), `LP_HARD_PCT` (10–50),
`SOFT_PENALTY` (0–0,15), `SOFT_SIZE_MULT` (0,2–1), `SOFT_MAX_FLAGS` (2–4), `SHADOW_NEAR_SCORE` (0–0,10).
Nuove chiavi `CFG`: le precedenti + `EQUAL_MIN_PCT`, `BUNDLE_MAX_NET_SIZE`, `SHADOW_TRADES`, `SHADOW_MAX_OPEN`,
`SHADOW_CHECK_SEC`, `SHADOW_SIZE_USD`, `SHADOW_ROUTE_CHECK`, `LEARN_SHADOW_MIN_N`, `LEARN_SHADOW_WINDOW`,
`LEARN_STEP_SEC`, `LEARN_PEN_STEP`, `LEARN_SIZE_STEP`, `LEARN_LOOSEN_AVG`/`WIN`/`RUG`, `LEARN_TIGHTEN_RUG`,
`LEARN_THR_STEP`, `LEARN_THR_WIN`.
Nuovi campi in `trades.csv`: `soft_flags`, `size_mult`; eventi `shadow_open`, `shadow_close`, `learn_adjust`.
Test offline: `tests/test_v6_soft.py`.

Correzione (20:13): se la vendita parziale di una presa di profitto falliva (transazione fallita simulata),
il gradino veniva segnato come fatto senza vendere; ora resta da fare e il Risk riprova al controllo dopo.

## Aggiornamento 30/10/2026 (v7) – cluster di wallet, LP sbloccata soft, shadow dell'Analyst, segnali extra

Tutto resta **solo paper** (`DESK_PAPER_ONLY=1`): nessun wallet, nessuna chiave, nessuna transazione.
Gli scarti HARD di sicurezza (mint/freeze, honeypot/nessun percorso di vendita, liquidità minima, creatore,
LP non verificabile...) non cambiano e non vengono mai allentati da Learner o bonus.

### 1. Perché un token con una bubble map "a grappoli" era passato
RugCheck per quel token non aveva dati sui cluster (`graphInsidersDetected` 0, nessuna rete, grafo insider vuoto):
il controllo bundle v6 non aveva nulla da misurare. L'API di Bubblemaps è a pagamento, quindi non si usa.

### 2. Nuovo controllo cluster on-chain (gratuito, RPC pubblica, sola lettura)
- Solo per i candidati che **supererebbero** la soglia (dopo tutti i filtri HARD), al massimo uno alla volta
  (`RPC_HEAVY_LOCK`), cache 30 min.
- Top holder da `getProgramAccounts` (Token e Token-2022; pool, curve e quote ≥ 10% escluse; ripiego sui top holder
  di RugCheck); per ognuno `getSignaturesForAddress` (< 100 transazioni = wallet "nuovo") e, solo per i nuovi,
  `getTransaction` della prima transazione per trovare chi l'ha finanziato.
  Rete = almeno `BUNDLE_NET_MIN_WALLETS` (3) wallet con lo stesso finanziatore; si aggiungono le reti RugCheck
  (quelle con più di 100 wallet e poca offerta restano escluse: exchange/airdrop).
- Costo: fino a ~80 chiamate (`CLUSTER_TOP_N` 40), circa 30–40 s prima dell'acquisto; con 6 errori (429) si ferma e
  non dà verdetto.
- **Graduato** (richiesta "non renderlo troppo severo però impara da questo errore"):

| Livello | Quando | Effetto |
|---|---|---|
| nessuno | nulla di quanto sotto | – |
| SOFT `cluster` | ≥ 2 reti, o reti ≥ 5% dell'offerta, o ≥ 50% dei top holder nuovi, o un solo finanziatore per ≥ 80% dei wallet con < 10% dell'offerta (probabile servizio/relayer) | penalità `CLUSTER_PENALTY` 0,08 e size × `CLUSTER_SIZE_MULT` 0,4 |
| HARD (estremo) | (≥ `BUNDLE_MAX_NETWORKS` 4 reti **e** ≥ `BUNDLE_TOTAL_HARD_PCT` 20%) o ≥ `BUNDLE_MAX_INSIDER_WALLETS` 30 wallet collegati o (≥ `CLUSTER_FRESH_HARD` 75% wallet nuovi **e** ≥ 2 reti) | scarto `cluster` + shadow trade con tag `cluster` |

- Limiti override: BUNDLE_MAX_NETWORKS 3–8, BUNDLE_TOTAL_HARD_PCT 10–40, BUNDLE_MAX_INSIDER_WALLETS 20–60,
  CLUSTER_FRESH_HARD 0,7–1, CLUSTER_SERVICE_SHARE 0,6–1, CLUSTER_PENALTY 0,02–0,15, CLUSTER_SIZE_MULT 0,2–1
  (limiti del Learner più stretti: penalità 0,04–0,15, size 0,2–0,7). Solo un override manuale può cambiare le soglie estreme.
- Learner: sposta penalità e size del segnale soft `cluster` nei due versi (entro i limiti) dagli esiti veri e shadow;
  la soglia estrema (`cluster_hard`) **non viene mai allentata**.
- **Caso etichettato**: il token della segnalazione è in `LABELLED_CASES` come caso "cluster". Alla chiusura il suo
  esito entra nelle statistiche del Learner (`real-label`) e in una lezione; evento `label_outcome`.
- Ricalcolo sui candidati dell'ultima ora (11 passati dal Filter, 3 acquisti): il token segnalato finisce **appena
  sopra il limite HARD** (78% di top holder nuovi con 2 reti, limite 75%); degli altri 2 acquisti uno sarebbe soft
  (finanziatore comune tipo servizio), uno pulito. Sugli 11 candidati: 1 hard, 4 soft, 6 puliti.
  Limite noto: un finanziatore molto attivo può essere un relayer di un'app, non un bundler.

### 3. LP non bloccata/bruciata: da HARD a SOFT
- Segnale soft `lp_unlocked` (prima `lp`): penalità e size ridotta, tenuta massima `LP_SOFT_MAX_HOLD_MIN` 60 min;
  sotto `LP_HARD_PCT` diventa `lp_unlocked_grave` (penalità e taglio doppi), non più scarto.
- Restano HARD: LP bloccata ≤ `LP_EXTREME_LOCKED_PCT` 5% **e** LP in mano al creatore per ≥ 50%; rimozione della LP già
  iniziata (liquidità −30% dal massimo visto); LP non verificabile.
- I token pump.fun migrati (LP bruciata) non cambiano.
- Rug = liquidità −50% entro `RUG_WINDOW_MIN` 60 min (o −80% in qualsiasi momento): evento `rug_event`; il Learner
  regola `LP_HARD_PCT` (Learner 15–40, override 10–50) e la penalità LP dal tasso di rug reale. Shadow con tag `lp_unlocked`.
- Nell'ultima ora 25 token erano stati scartati per "LP bloccata solo al 0%": ora passerebbero come soft grave,
  salvo creatore con la LP o rimozione in corso.

### 4. Shadow sugli scarti soft dell'Analyst
"momentum non confermato", momento, VWAP e grafico dopo i controlli di sicurezza aprono uno shadow con
reason/tag `analyst_momentum` (max `SHADOW_MAX_OPEN_ANALYST` 25 aperti, a parte). `shadow_trades.csv` viene creato con
l'intestazione all'avvio; nuove colonne `tag` e `signals` (il file con la vecchia intestazione viene rinominato).

### 5. Segnali extra (`sources/`) e colonna `signals`
- Trending: fonte principale **GMGN** (`gmgn_trending.py`, OpenAPI ufficiale) **solo con chiave personale**
  (`GMGN_API_KEY` nell'ambiente o `~/.config/desk/gmgn_key` / `gmgn_api_key`, mai nel `.env`). La chiave pubblica demo
  condivisa non viene mai usata. Senza chiave: sostituto gratuito DexScreener boosts (`dex_boosts_substitute`, via
  `fomo_trending.py`; con `FOMOAPI_KEY` la vera board Fomo), **bonus 0**. Poll ogni 180 s, nessun bypass dei filtri.
- Bonus **solo dopo** tutti i filtri HARD e solo per candidati vicini alla soglia:
  `GMGN_TREND_BONUS` 0,03 (0–0,08), `FOMO_TREND_BONUS` 0,03, `FOMO_SUB_BONUS` 0 (0–0,03), `KOL_BONUS` 0,03 × tier
  (S 1,25, A 1, B 0,5, × weight), `KOL_THESIS_BONUS` 0,02, `MOMENTUM_YOUNG_BONUS` 0,03; somma tra −`KOL_SOLD_PENALTY`
  0,02 e `TOTAL_BONUS_CAP` 0,10. KOL `proposed` inattivi.
- `kol_check` gira sulla RPC pubblica condivisa con il controllo cluster (un controllo pesante alla volta, pausa 0,5 s).
- Colonna `signals` in `trades.csv` (il vecchio file viene rinominato `trades_old_*`) e `shadow_trades.csv`, es.
  `src:gmgn_trending|gmgn_rank:3|kol:unipcs:S|kol_thesis:theunipcs|momentum_young:0.82|bonus:0.100`.
- Learner dei bonus: per ogni chiave confronta gli esiti con e senza il segnale (≥ 15 ciascuno), un passo all'ora
  (0,01; 0,005 per la tesi), entro i limiti, fino a 0. Aggiorna `kol_ledger.json` (retrocessione/promozione KOL).

### Test
`tests/test_v7.py` (nuovo) e `tests/test_v6_soft.py` aggiornato (LP soft); tutti i test passano.

## Aggiornamento 30/10/2026 (v8) – qualità del progetto e analisi approfondite (Mark)

Sempre **solo paper** (`DESK_PAPER_ONLY=1`): nessun wallet, nessuna chiave, nessuna transazione. Nessuno scarto
nuovo: questo controllo non blocca mai un trade da solo, e se va in timeout non ha nessun effetto.
Versione precedente salvata in `desk_before_v8.py`; patch in `dev/patch_v8.py`.

### 1. Controllo veloce del progetto (`sources/project_check.py`)
- Gira **solo** sui candidati che hanno già superato tutti i filtri HARD e sono vicini alla soglia (stesso punto
  degli altri bonus), **in parallelo** al controllo KOL. Ha un tetto di `PROJECT_TIMEOUT_SEC` (5 s) ed è
  di solito tra 0,5 e 2 s; con l'IPFS lento arriva a circa 4,5 s.
- Fonti gratuite: link DexScreener (siti e social, ora salvati da `summarize` in `m["links"]`), metadati on-chain
  dal report RugCheck già scaricato (`tokenMeta`: name, symbol, uri, mutable) e il JSON dei metadati (per pump.fun
  contiene website, twitter, telegram e description). A questi si aggiungono il sito stesso (max 250 KB), l'RDAP
  (rdap.org) per l'età del dominio e l'API pubblica di GitHub senza chiave.
- Controlli:
  - il sito risponde?
  - è una pagina parcheggiata o un template ("domain for sale", "coming soon", nginx di default, lorem ipsum…)?
  - è troppo piccolo? Una pagina SPA React/Next non viene considerata vuota.
  - è fatto con un builder (carrd, wix…) o su un hosting gratuito (vercel.app, github.io…)?
  - il dominio ha meno di 14 giorni, oppure più di 60?
  - ci sono X e Telegram?
  - c'è GitHub (link diretto o trovato nel sito)? Commit negli ultimi 30 giorni: sono al massimo 2 chiamate, con
    cache di 6 ore anche su disco (`sources/project_check_cache.json`), e il controllo si ferma quando restano
    ≤ 5 chiamate nell'ora;
  - le parole chiave sono da utility o da puro meme?
  - i metadati sono coerenti, cioè il symbol on-chain, quello del JSON e quello di DexScreener coincidono? I
    metadati restano modificabili (contano solo per i token non pump)?
- Uscita: `project_score` 0–1 e tag nella colonna `signals`: `proj_site`, `proj_site_down`, `proj_template`, `proj_small`,
  `proj_nosite`, `proj_spa`, `proj_builder`, `proj_freehost`, `proj_domain_new:<giorni>`, `proj_domain_old`, `proj_x`,
  `proj_tg`, `proj_nosocials`, `proj_empty`, `proj_github`, `proj_github_active`, `proj_github_stale`,
  `proj_github_ratelimited`, `proj_utility`, `proj_meme`, `proj_meta_ok`, `proj_meta_mismatch`, `proj_meta_mutable`,
  `proj_partial`, `proj_timeout`, `proj_score:<x>`, `proj_good:<x>` (quando il bonus è stato dato).
- Pesi: sito vero 0,30, X 0,15, Telegram 0,08, GitHub attivo 0,20 (fermo 0,05), dominio vecchio 0,10, utility 0,10,
  metadati coerenti 0,05 (non pump). Metadati incoerenti −0,15, modificabili −0,05.

### 2. Bonus e penalità (dentro il tetto `TOTAL_BONUS_CAP` 0,10)
| Chiave | Default | Limiti | Effetto |
|---|---|---|---|
| `PROJECT_BONUS` | 0,02 | 0–0,06 | bonus = chiave × project_score se score ≥ `PROJECT_MIN_SCORE` |
| `PROJECT_MIN_SCORE` | 0,5 | 0,3–0,8 | soglia del bonus |
| `PROJECT_EMPTY_PENALTY` | 0,01 | 0–0,03 | penalità lieve se non c'è né sito né X né Telegram (`proj_empty`) |

Il Learner regola `PROJECT_BONUS` come gli altri bonus: tag `proj_good:`, passo 0,01. Regola anche
`PROJECT_EMPTY_PENALTY`, ma al contrario: se gli esiti con `proj_empty` sono peggiori di almeno 2 punti la alza di
0,005, altrimenti la abbassa fino a 0. Tutto avviene entro i limiti, con al massimo un passo all'ora.
Il minimo della somma dei segnali ora è −(`KOL_SOLD_PENALTY` + `PROJECT_EMPTY_PENALTY`).

### 3. Coda per l'analisi approfondita (Mark)
- `sources/deep_review_queue.jsonl`: una riga quando il bot **apre** una posizione paper (`reason: opened`) e
  quando un trade vero o shadow arriva a ≥ `DEEP_QUEUE_PEAK_PCT` (+50%) (`real_peak:+NN%` / `shadow_peak:+NN%`).
  Ogni mint entra una sola volta per motivo, anche dopo un riavvio. Campi: mint, symbol, chain, links (DexScreener e
  progetto), dexscreener, reason, project_score, project_tags, bot_score, ts. CATE e DESSA sono già in coda
  con `opened_before_v8`.
- Mark scrive `sources/deep_reviews.json` come `{mint: {score 0-100, verdict, thesis, risks, ts}}` (esempio in
  `sources/deep_reviews.example.json`). Il bot lo rilegge solo quando cambia; se il file è rotto tiene gli ultimi
  valori validi.
- Effetto sulle posizioni aperte (solo trade veri):
  - voto ≥ `DEEP_HOLD_MIN_SCORE` (70): tempo massimo e stop a tempo più lunghi di `DEEP_HOLD_EXTRA_MIN`
    (60 min; limiti 0–180; tetto assoluto 720 min). Non vale per le posizioni con LP sbloccata, che restano a 60 min;
  - voto ≤ `DEEP_LOW_SCORE` (30): trailing più stretto di `DEEP_TRAIL_TIGHTEN_PCT` punti (3; limiti 0–8; mai sotto il 3%).
- Override con limiti: `DEEP_QUEUE_PEAK_PCT` 20–200, `DEEP_HOLD_MIN_SCORE` 50–95, `DEEP_HOLD_EXTRA_MIN` 0–180,
  `DEEP_LOW_SCORE` 0–50, `DEEP_TRAIL_TIGHTEN_PCT` 0–8. Il campo `eff` della posizione mostra `deep_score`.

### Test
`tests/test_v8.py` (nuovo, offline): analisi HTML, punteggio, bonus e timeout, Learner, override, coda, verdetti e
Risk. Passano anche tutti i test precedenti (eseguiti su una copia della cartella, perché alcuni test cancellano
`state.json` e `shadow_trades.csv`).

### Limiti noti
- L'età del dominio con RDAP non c'è per alcuni TLD (es. `.ag`, molti ccTLD): in quel caso conta zero, non è una
  penalità.
- I siti costruiti solo in JavaScript (SPA) non si possono "leggere": il testo non viene visto, quindi le parole
  chiave di utility mancano.
- GitHub senza chiave consente 60 chiamate all'ora per IP. Oltre quel limite il tag è `proj_github_ratelimited`, senza
  effetto.
- Un "sito" può essere anche la pagina di un altro progetto o un articolo. I link a social, pump.fun e DexScreener
  sono esclusi, gli altri no.
- Un punteggio alto non dice niente sulla sicurezza on-chain: quella resta ai filtri HARD.

### Correzione v8b (30/10/2026, 21:33)

- **Bug trovato dopo il riavvio delle 21:29**: GeckoTerminal rispondeva 429, quindi il prezzo di SOL restava al
  fallback di $150 (reale ~$95). Il broker paper convertiva i preventivi Jupiter (in SOL) in dollari a $150 e il
  valore delle posizioni risultava gonfiato del ~58%: CATE e DESSA sembravano a +62% / +57% (in realta +2.7% / +0.5%)
  e il bot ha eseguito due prese di profitto finte (CATE $7.89, DESSA $9.34). Bug preesistente (v6), emerso solo
  perche GeckoTerminal era in rate limit al riavvio.
- **Correzione nel codice**: `refresh_sol_usd` prova GeckoTerminal, poi Jupiter price v3, poi DexScreener (SOL/USDC)
  e se fallisce riprova dopo 60 s. Nuovo `sol_known()`: finche il prezzo di SOL non e reale, il broker paper usa la
  formula (niente preventivi in SOL) per mark, acquisti e vendite. Un preventivo di vendita oltre il 10% sopra il
  prezzo mostrato viene ignorato.
- **Correzione dello stato (manuale)**: annullate le due vendite parziali (token, cassa, realizzato, gradino TP e
  picchi ripristinati: cassa di nuovo $953.70), tolte dalla coda deep review le due voci `real_peak` finte, aggiunto
  un evento di correzione in events.jsonl. Log del periodo col bug: `run_v8_bug.log`.
- Test nuovo in `tests/test_v8.py` (`test_v8b_no_quote_mark_without_real_sol_price`).

## Aggiornamento 30/10/2026 (v8.1) – stop/TP dinamici in shadow, feature d'ingresso, percorsi di prezzo

Il live resta disattivato (`DESK_PAPER_ONLY=1`). **Le uscite reali non cambiano**: stop -12 (override), TP1 +30,
trailing da +30 con distanza 10. Tutto il nuovo è solo registrato (modo "shadow").

1. **Limiti override**: `STOP_LOSS_PCT` ora fra -30 e -3 (prima -15). `TRAIL_DISTANCE_PCT` fino a 25 (prima 15).
2. **Crollo relativo allo stop**: `crash_level(stop)` = `CRASH_EXIT_PCT` (-15) se lo stop è più stretto di -15,
   altrimenti 1.5 x stop (regola `crash_for` del backtest di Mark). Con stop -12 resta -15, identico a prima;
   con -20 diventa -30. (La formula letterale min(-15, 1.5 x stop) avrebbe dato -18 con lo stop attuale.)
3. **Stop dinamico** (`DYN_STOP_MODE` off|shadow|on, default **shadow**; `DYN_STOP_ATR_MULT` 3, `DYN_STOP_MIN` -8,
   `DYN_STOP_MAX` -30, `DYN_STOP_SIZE_SCALE` sì): stop = -clamp(3 x ATR, 8, 30); size_mult = |stop base| / |stop dyn|,
   **mai sopra 1** (regola dell'Executor "mai maggiorata"). In shadow viene calcolato a ogni ingresso (reale e
   shadow) ma si esce con lo stop attuale; "on" lo userebbe davvero (NON attivato).
   - ATR: media (max-min)/chiusura x 100 sulle ultime 10 candele 1m **chiuse** (GeckoTerminal, con cache e pausa
     di 180 s dopo un 429; al massimo 2 richieste extra al minuto).
   - Fallback (Mark): candele disponibili se almeno 3 (`atr_src` candles_partial), altrimenti volatilità dai tick
     dei controlli già fatti prima dell'ingresso (`ticks`), altrimenti `none` -> ATR null e stop dinamico = stop base.
     `atr_bars_used` registra quante candele sono state usate. DexScreener non ha OHLCV pubblico: i tick vengono
     dalle chiamate DexScreener già esistenti (nessuna chiamata in più).
4. **TP1 e trailing dinamici** (`DYN_TP_MODE` off|shadow, default shadow): TP1 = 6 x ATR (+15…+100), attivazione
   trailing = 6 x ATR (+15…+100), distanza trailing = 2 x ATR (5…25). Multipli e limiti modificabili da override
   (`DYN_TP1_*`, `DYN_TRAIL_ACT_*`, `DYN_TRAIL_DIST_*`). Senza ATR = valori attuali. Solo registrati.
5. **Feature d'ingresso** in `trades.csv` e `shadow_trades.csv`: colonne `atr_1m_10`, `stop_applied`,
   `dyn_stop_shadow`, `size_mult`, `dyn_tp1`, `dyn_trail_act`, `dyn_trail_dist` e `entry_features` (JSON con
   atr_bars_used, atr_src, std dei rendimenti 1m e 5m, ret_5m, accelerazione volume, liquidità, narrativa,
   gmgn_trend, kol_count, kol_thesis, project_score, deep_review, segnali soft, valori base e dinamici, trough).
   I CSV vecchi vengono migrati sul posto (colonne nuove vuote per le righe vecchie).
6. **Percorsi di prezzo**: `price_paths/<mint>.jsonl` con prezzo, liquidità e pnl a ogni controllo (Risk 15 s,
   shadow 30 s), ruotati oltre 300 KB, massimo 3000 file. Dopo l'uscita il prezzo viene seguito per 180 minuti
   (stessa chiamata batch; senza posizioni aperte una al minuto) e il riassunto finisce in
   `price_paths/_post_exit_summary.jsonl` (massimo/minimo rispetto all'uscita, rimbalzo sopra l'entrata).
7. **Test**: `tests/test_v81.py` (limiti override, crollo relativo, stop e TP dinamici con fallback null,
   colonne CSV, percorsi di prezzo). Patch: `dev/patch_v81.py`, `dev/patch_v81_tp.py`.

## Aggiornamento 30/10/2026 (v8.2) – cluster su TUTTI i candidati, controllo honeypot, casi BEAST e DESSA

**Perché**: BEAST non è mai stato controllato per i cluster: il controllo v7 partiva solo dopo i filtri
momentum/punteggio, e BEAST era stato scartato prima (momentum, 21:47) e messo in shadow. DESSA era stata
comprata (20:14) prima che il controllo cluster esistesse, e le posizioni aperte non venivano mai ricontrollate.

1. **Cluster v2 (`sources/cluster_v2.py`) su ogni candidato che passa il Filter**, anche quelli che
   diventano shadow. Parte in sottofondo subito dopo il Filter (max `CLUSTER2_MAX_QUEUE` in coda) e
   l'Analyst lo aspetta al massimo `CLUSTER2_WAIT_SEC` (90 s).
   - Legge i primi 150 holder e calcola la **% di supply** dei wallet collegati (stesso finanziatore,
     stesso fee payer, stesso mittente dei token, stesso minuto/slot) e lo **sciame** di wallet con saldo identico.
   - **Rifiuto rigido** se un solo gruppo ha ≥ `CLUSTER_GROUP_HARD_PCT` (20%, si può solo abbassare),
     se c'è un gruppo grande (50+ wallet) o uno sciame enorme. Etichetta `bad_cluster` + shadow.
   - **Errore o timeout = mai "ok"**: `CLUSTER_UNKNOWN_ACTION` "soft" (penalità 0.05 e size ×0.5) oppure "skip".
2. **Falsi collegamenti**: i finanziatori di infrastruttura (exchange/CEX, fomo, pump.fun, Photon, ecc.)
   non contano come insider; la loro quota è registrata a parte (`cluster_infra_pct`). Un wallet che manda
   token a 3+ holder è un **distributore** e non è mai considerato infrastruttura.
3. **Controllo honeypot (`sources/honeypot.py`) prima di ogni acquisto**:
   - estensioni Token-2022 (transfer hook, commissione > 5%, permanent delegate, account congelati, ecc.),
     mint authority e freeze authority → rifiuto rigido;
   - **solo preventivo** Jupiter di vendita (mai uno swap) alla nostra size: nessun percorso o impatto > 25% → rifiuto rigido;
   - RugCheck "rugged" → rifiuto. Se il preventivo non è disponibile (429) → `HONEYPOT_UNKNOWN_ACTION` "soft" (size ridotta).
4. **Posizioni aperte ricontrollate** ogni `CLUSTER_HELD_RECHECK_MIN` (20 min): se il cluster diventa rigido → uscita.
5. **Etichette e promozione a rug**: `bad_cluster`/`suspected_rug` diventano `rug` se il prezzo scende ≥ 50% dal
   massimo o la liquidità cala ≥ 50%; vengono salvati quando, come (dump o rimozione LP) e il caso finisce in
   `sources/rug_forensics_queue.jsonl` per vedere chi ha venduto.
6. **Lezioni salvate**: `beast-2026-10-30` (sciame di 1997 wallet con saldo identico = 76% della supply, distributore
   dedicato, nessun honeypot; dump coordinato alle 22:19 → promosso a `rug`) e `dessa-2026-10-30` (comprata senza controllo cluster;
   oggi nessun cluster pericoloso).
7. Test: `tests/test_v82.py` (BEAST deve uscire come rifiuto rigido). I test vecchi girano con v8.2 spenta.
   Il bot resta **solo paper**.

## v8.3 — Trending come fonte principale + prefiltro stile DexScreener (PAPER)

Richiesta: "analizza token in trending come token principali da analizzare". La chiave GMGN (~100 €) non verra presa:
DexScreener diventa la fonte trending principale, GeckoTerminal e pump.fun secondarie, GMGN resta nel codice ma spento.

- **Nuovo `sources/trending_agg.py`** (aggregatore gratuito, senza chiavi). Tutte le chiamate passano da `http.get` del bot,
  quindi i limiti di frequenza sono condivisi.
  - DexScreener: token-boosts/top, token-boosts/latest, token-profiles/latest, ads (cache 110 s).
  - GeckoTerminal trending_pools 5m / 1h / 6h (cache 300 s; se il dominio ha avuto un 429 negli ultimi 180 s usa solo la cache).
  - pump.fun `coins/currently-live` (mcap >= 15k$, trade negli ultimi 15 min; pausa 15 min se errore). Gli altri endpoint
    pump.fun (king-of-the-hill, top-runners, featured, advanced-api) oggi danno 404/530.
  - GMGN solo con chiave personale: oggi inattivo.
  - La pagina web trending di DexScreener e dietro Cloudflare (403): la classifica e ricostruita dai dati API.
  - Unione per mint, arricchimento con `/tokens/v1` e `trending_score` (0-1) = numero fonti, posizione, volume, transazioni,
    accelerazione di volume e transazioni (l'accelerazione conta solo se c'e volume vero). Flag `paid` = boost/profilo/ads.
- **Priorita in watchlist**: ogni `TREND_SCOUT_SEC`=120 s i primi `TREND_WATCH_TOP`=60 token trending entrano con priorita alta
  (non vengono mai espulsi dalle pool nuove; un token gia in lista viene promosso). Le pool nuove hanno un tetto
  `WATCH_NEW_MAX`=1200. Il Filter analizza prima i prioritari, poi per trending_score, poi per eta.
- **GeckoTerminal**: gap minimo 6 s tra chiamate (condiviso con il resto del bot); `scout_gecko` non chiama piu trending
  (lo fa l'aggregatore). pump.fun gap 5 s.
- **Prefiltro locale stile DexScreener** (`sources/dex_prefilter.py`), applicato dopo volume e liquidita e prima dei controlli
  costosi (cluster RPC, RugCheck, honeypot). Soglie in config e override con limiti:
  liquidita >= 8000$, vol 5m >= 500$, txns 5m >= 10, txns 1h >= 150, acquisti 5m >= 5 (DexScreener da il numero di acquisti,
  non di acquirenti unici), rapporto buy/sell 1h >= 0.5 e 5m >= 0.3, eta pool 3 min - 2160 h, variazione 5m >= -25%,
  1h tra -50% e +10000%, mcap/FDV 15k$ - 500M$, social e boost/profilo pagato non obbligatori (0).
  Rifiuto con categoria `prefiltro <chiave>`, statistica `prefilter_reject`; ogni valutazione va in `prefilter_log.jsonl`
  con l'elenco dei filtri falliti.
- **Calibrazione** su 52 casi storici (22 vincenti, 30 perdenti): con i default liquidita, vol 5m ed eta non tagliano nessun
  vincente; variazione 5m -25% e 1h -50% tagliano solo 1 perdente ciascuna. Alternative scartate perche tagliano vincenti:
  liquidita 15k (2 vincenti), vol 5m 5000 (9 vincenti), eta min 30 min (8 vincenti), max var 1h 2000% (5 vincenti).
  Transazioni, acquisti, buy/sell, mcap e social non sono calibrabili dalle candele: default prudenti.
- **Learner del prefiltro** (`PF_LEARN`): ogni 10 min guarda i token valutati 1-3 ore prima; se una soglia scarta token poi
  saliti la allenta, se lascia passare token poi crollati la stringe, un passo alla volta dentro i limiti (`learn_prefilter`).
- **Bonus multi-fonte**: +0.02 (`TREND_MULTI_BONUS`, limiti 0-0.04) solo per token trending su 2 o piu famiglie di fonti,
  sempre dentro il tetto 0.10. I filtri duri restano identici per tutti.
- **Log**: `src` in tutti gli eventi (rifiuti, pass, segnali); tag `src:trending_agg`, `trend_score`, `trend_src` nei segnali;
  `src`, `trending_score`, `trend_families`, `trend_sources`, `trend_paid` in entry_features.
- Test: `tests/test_v83.py` + tutti i test precedenti passano. Live resta spento; wallet, chiavi e .env non toccati.

## Aggiornamento 30/10/2026 (v8.2, seconda parte) – Helius come RPC principale (PAPER)

- Nuovo `sources/helius.py`. La chiave si legge **solo** dalla variabile d'ambiente `HELIUS_API_KEY` e l'URL si
  costruisce mentre il bot gira: non è mai scritta in file, log, eventi o export. Ogni testo che contiene
  `api-key=` viene mascherato (`***`). Senza la chiave tutto torna agli RPC pubblici, come prima.
- Le chiamate JSON-RPC che prima andavano agli RPC pubblici (mainnet-beta, PublicNode) provano prima Helius:
  `RPC_URL`, `CLUSTER_RPC`, `RPC_GPA` in `cluster_v2`, controllo honeypot e `KOL_RPC` (kol_holders).
  Se Helius dà 429 o errore, si usa il pubblico.
- Cluster: la lista completa degli holder arriva da getProgramAccounts su Helius (10 crediti, circa 1 s anche con
  15.000 holder). Se non funziona, si usa il DAS `getTokenAccounts` (1000 account per pagina), quindi anche gli
  holder oltre i primi 40. L'endpoint `funded-by` di Helius è solo per i piani a pagamento (risponde 403), quindi
  il finanziatore si cerca ancora con getSignaturesForAddress + getTransaction.
- Limiti del piano gratuito: 6 richieste/s RPC, 1,5/s DAS, 4/s getProgramAccounts e 8 s di pausa dopo un 429.
  Budget: 85% di 1M crediti al mese, cioè circa 28.300 al giorno, spesi a ritmo uniforme durante la giornata.
  - "core" (holder, mint, honeypot): sempre su Helius, fino al 97% del budget del giorno.
  - "bulk" (analisi per holder): su Helius solo se siamo entro il ritmo e Helius è libero, altrimenti va al pubblico.
  - "low" (KOL): su Helius solo sotto il 60% del ritmo.
  - In più, "bulk" e "low" hanno un tetto orario di circa 1.530 crediti (1,3 × budget giornaliero / 24), così
    la sera non si brucia il resto del budget del giorno.
- Contatore dei crediti (oggi, ora corrente, media per ora, mese, 429) in `helius_usage.json`, che contiene solo
  numeri e non va su GitHub. Una riga "Helius:" nel log ogni 30 minuti.
- `tools/sync_github.sh`: lo scan dei segreti blocca gli URL Helius con la chiave dentro e la chiave stessa letta
  dall'ambiente, senza stamparla.
- Test: `tests/test_v82.py`, sezione Helius (instradamento, budget, maschera, DAS) con una chiave finta, senza rete.

## v8.4 — 30/10/2026 (sera) – Birdeye, Telegram, budget mensile, cluster PILLSEY/GLITCH, GoPlus, fee, età (PAPER)

Sempre e solo PAPER: live spento, nessun wallet, chiave o `.env` toccato. Le chiavi (Birdeye, Helius, Telegram)
si leggono SOLO da `os.environ`, non sono mai scritte in file/log/export, e ogni messaggio d'errore passa da `mask()`.

**Birdeye (piano Standard gratuito, `sources/birdeye.py`)** – 30.000 CU/mese, 1 rps (gap 1,2 s), riserva 1.500 CU.
Endpoint: `/defi/ohlcv` 1m (60 candele = 25 CU, ATR primario per stop/TP dinamici; fallback GeckoTerminal/tick),
`/defi/token_trending` (30 CU ogni 4 h = 180 CU/giorno, fonte trending), `/defi/token_overview` (18 CU, controllo
incrociato prima dell'ingresso: liquidità, holder, acquirenti), `/utils/v1/credits` (1 CU/h, consumo ufficiale).
Budget del giorno = residuo del ciclo / giorni rimanenti (~950 CU/giorno), diviso ATR 50% / overview 30% / trending 20%.

**Telegram (`sources/notify_telegram.py`)** – token da `TELEGRAM_BOT_TOKEN` ripulito con `re.sub(r'\s','',…)`,
chat id da `.telegram_chat_id` (600, escluso dal sync). Invio in thread non bloccante, max 1 msg/s, 2 tentativi.
Messaggi: avvio con PID, acquisti, vendite, scarti rigidi raggruppati ogni 10 min, errori gravi, riepilogo alle 23:00 (Roma),
avvisi budget.

**Budget mensile Helius + Birdeye** – avvisi al 50/80/95% del ciclo; dall'80% niente chiamate non essenziali
(Helius: bulk/KOL al pubblico; Birdeye: niente trending/overview non prioritari); dal 95% solo fonti gratuite.
Helius Free: 1M crediti/mese, 10 rps RPC (usati 6), 2 rps DAS (1,5), GPA 5 rps (4).

**Cluster (`sources/cluster_v2.py`)** – nuove regole rigide/soft:
- somma dei gruppi collegati ≥ 25% = rigido; ≥ 3 gruppi da ≥ 2% con somma ≥ 20% = rigido;
- *hub burst*: hub non-exchange che finanzia ≥ 10 holder, ≥ 80% nuovi, acquisto mediano ≤ 1 h dopo il
  finanziamento, quote simili (CV ≤ 0,5) = distributore insider, non infrastruttura;
- *stima estesa*: holder senza funder noto nella stessa finestra (±300 s) e fascia di quota (0,8–1,25×) aggiunti in
  proporzione; regole 25% e "≥ 50 wallet" applicate al gruppo stimato;
- *CEX-fresh*: wallet nuovi finanziati dallo stesso exchange ≤ 6 h prima, acquisto ≤ 3 h dal primo, quote 1–5%,
  n ≥ 4, somma ≥ 10%, CV ≤ 0,5 = rigido; tutti gli exchange insieme ≥ 6% = soft;
- *combo*: collegati ≥ 10% + gruppo con infrastruttura ≥ 15% che aggiunge ≥ 5 punti = rigido;
- *quota ricevuta non comprata* (proxy "phishing"): soft ≥ 15%, rigido ≥ 30%.
Calibrazione: PILLSEY rigido (hub 9Ja2iw 60 wallet 10,7%, stima ~150 wallet 25,0%), GLITCH rigido (MEXC 5 wallet 11,1%),
CATE rigido (combo: collegati 10,6%, infra 15,7%), DESSA pulito (gruppo max 0,35%, collegati 1,04%).

**GoPlus (`sources/goplus.py`)** – token_security Solana senza chiave, cache 10 min, gap 2 s, timeout 12 s.
Rigido: balance mutable, closable, freeze, mint, transfer hook, fee ≥ 10% o modificabile, default state frozen,
non transferable. Soft: metadata mutable. Errore = sconosciuto (mai "ok"). `goplus_flags` nel log e nelle feature.

**Fee di trasferimento** – "Taxes 1.25%" di GMGN è la fee della pool: non si scarta. Fee Token-2022 < 10%: soft,
penalità 0,02 di punteggio per 1%, size −7% per 1% (min 0,3×), `rt_cost_pct += 2 × fee`. Rigido a ≥ 10% o se il dev
può cambiarla (authority attiva). Hook, freeze, balance mutable, closable restano rigidi.

**Penalità età** (CFG `AGE_*`) – < 24 h 0; 1–3 g 0,02; 3–7 g 0,05; 7–30 g 0,10; > 30 g 0,20.
Sopra 3 giorni servono attività (vol 1h/liq ≥ 0,3 o vol 1h/mcap ≥ 3%) E volatilità (ATR 1m ≥ 0,7% o |var 1h| ≥ 8%),
altrimenti scarto "token vecchio poco attivo" (shadow "eta"). Con attività molto alta (vol/liq ≥ 2 e ATR ≥ 2% o
|var 1h| ≥ 25%) la penalità si dimezza. Calibrata su 513 segnali simulati (GeckoTerminal 1m, 50 token): sopra 3 giorni
taglia 395 segnali su 443 (win 11%, medio −2,8%) e tiene 48 (win 23%, medio −1,7%); vincenti (picco ≥ +20%) tagliati 7 su 23.
CATE (95 g, vol/liq 0,02) viene scartato. `PF_MAX_AGE_H` del prefiltro resta 90 giorni.

**Lezioni** – `pillsey-2026-10-30` e `glitch-2026-10-30` (etichetta "cluster"; GLITCH "SOSPETTO").

**Sync GitHub** – esclusi `.telegram*`, `telegram_state*`, `birdeye_usage*`, `trending_agg_state*`, `infra_funders*`;
scansione segreti con pattern token bot Telegram, header/chiave Birdeye e ricerca letterale dei valori in env.

## v8.4b — 30/10/2026 (23:50) – DESSA è un token cattivo: verifica delle regole senza il vincolo "DESSA pulito" (PAPER)

Kiwi: DESSA (npz2Fh…pump) **non** è un buon token. Non è più usato come esempio "deve restare pulito".
- **Lezione**: nuovo seed `dessa-2026-10-30-cattivo` (etichetta "cluster", confermata la lezione originale: comprata
  alle 20:14 oltre il limite cluster, v7 78% nuovi + 2 reti; chiusa alle 23:15 per tempo massimo a +1,92%, esito
  positivo ma token cattivo; `pnl_pct` None per non contare due volte il trade). Nota del seed originale corretta.
- **Controlli puliti nuovi** (scansione Helius, ~1.156 crediti): HYPERCHAN (trade +6,4%, picco +46%: soft, collegati 6,5%,
  59 wallet nuovi, stima 148 wallet al 14,3%) e Bonnie (percorso +50%: soft, collegati 5,9%). Jason: scansione incompleta.
- **Wallet nuovi** (`cluster_v2.FRESH_SOFT` 0,5 → 0,3): il conteggio ntx copre solo i primi 60 holder su 150, quindi la
  quota non superava mai 0,40 e la regola soft non scattava mai. 0,3 ≈ 75% dei controllati. DESSA (87% dei controllati) ora soft;
  HYPERCHAN era già soft. Un "hard" sui wallet nuovi NON è stato aggiunto: HYPERCHAN (vincente) ha il 100% di wallet nuovi.
- **Regola combinata** (`COMBO_LINKED_PCT` 10 → 8, `COMBO_INFRA_PCT` 15 → 12, extra 5 invariato): sugli eventi storici
  colpisce solo CATE e GLITCH; HYPERCHAN, Bonnie, AgenC, PILLSEY (eventi) restano fuori.
- **Età/attività**: invariata. Già scarta DESSA (4 g, vol 1h/liq 0,11); la variante più stretta taglierebbe 13 vincenti su 23 invece di 7.
- **CEX-fresh, stima estesa, gruppo enorme**: invariate (non erano state ammorbidite per DESSA; abbassare BIG_GROUP/EST
  colpirebbe HYPERCHAN, stima 14,3%).
- Test: `tests/test_v84.py` (wallet nuovi tipo DESSA = soft, controllo HYPERCHAN = soft mai hard, combo 8/12, seed).

## v8.4c — 31/10/2026 (00:0x) – BNB: honeypot sconosciuto = niente acquisto, ticker impostori, quote uniformi (PAPER)

Kiwi: il paper buy di BNB (EYqygb…pump, "Broke Nigga Baddie") è un token cattivo. 5ª lezione negativa (dopo DESSA, BEAST,
PILLSEY, GLITCH): seed `bnb-2026-10-30`, etichetta "cluster", mai esempio pulito. La posizione aperta NON è stata chiusa a
mano: la gestiscono le regole d'uscita normali (incluso il ricontrollo cluster delle posizioni ogni 20 min, `CLUSTER_HELD_ACTION` "exit").
- **Bug GoPlus**: `honeypot_check` passava il wrapper `Http` invece della sessione aiohttp → TypeError a ogni chiamata →
  honeypot sempre "sconosciuto" (così è passato BNB). Ora `getattr(http, "s", http)`.
- **Honeypot sconosciuto = skip** (`HONEYPOT_UNKNOWN_ACTION` "skip", unico valore ammesso): niente più acquisto a size ridotta.
  Shadow con tag `honeypot_unknown`; nuovo tentativo dopo `HONEYPOT_RETRY_MIN` 5 min, al massimo `HONEYPOT_RETRY_MAX` 3 volte
  (poi il blocco normale di 6 h).
- **Ticker impostori** (`TICKER_IMPOSTOR_ACTION` "block"): ticker di grandi asset/exchange (BNB, BTC, ETH, SOL, USDC, USDT,
  XRP, DOGE, HYPE, JUP, PUMP, TRUMP, BINANCE, …) con mint non ufficiale → scartato nel Filter prima di ogni chiamata a
  pagamento, shadow `ticker_impostor`. Storico: impostori HYPE (shadow −92,6%), BTC ×2, ADA, TRUMP (liq 0–2,6k), BNB (cattivo);
  nessun impostore vincente. Anche i ticker ufficiali (HYPE, JUP, PUMP, WETH) nei segnali simulati: 40 segnali, win 2%.
- **cluster_v2, quote uniformi** (HARD): altopiano dal rango ≤ 10 di holder consecutivi con quota[s]/quota[k] ≤ 1,10 (≈ ±5%);
  hard con ≥ 20 wallet, oppure ≥ 10 wallet che insieme fanno ≥ 15%; soft da 12. Calibrazione (top 150): BNB 125 wallet/12,3%,
  PILLSEY 56/9,9% (cattivi) → hard; HYPERCHAN 7, Bonnie 4, DESSA 4, CATE 4, GLITCH 5, BEAST 4. HYPERCHAN e Bonnie restano soft.
- **cluster_v2, cluster a stella**: finanziatore comune (non exchange noto) di ≥ 8 holder controllati = stella; ≥ 3 stelle = hard,
  1–2 = segnale soft. Nello storico nessun token ha ≥ 2 stelle; HYPERCHAN ha 1 stella da 59 (soft).
- Perché v2 aveva lasciato passare BNB: la regola sciame vuole saldi IDENTICI (BNB: quote 0,092–0,104%, non identiche);
  i 60 holder controllati erano tutti finanziati dall'hub 36MESj (gruppo 6,1%, sotto le soglie 8/20/25%); la stima estesa
  (147 wallet, 14,4%) era sotto il 15% del "gruppo enorme"; le stelle da 8–12 di Bubblemaps non si vedono col solo primo
  finanziatore dei primi 60. Honeypot "sconosciuto" per il bug GoPlus.
- Test: `tests/test_v84.py` §18–20; `tests/test_v82.py` e §13 di test_v84 con quote sintetiche identiche adattati.

## Aggiornamento 31/10/2026 (mattina) – v8.4d: "nessun prezzo" dopo le pause, nomi dei grandi asset

**Cosa era successo.** Alle 01:04 17 shadow (9 aperti dopo mezzanotte + 8 più vecchi) sono stati chiusi
tutti insieme a -100% come "nessun prezzo da 15 min, possibile rug". Su DexScreener erano tutti vivi
(liquidità $17k–$3,5M); HYPERCHAN in quel momento valeva circa -43% (il rug è arrivato dopo).
Causa nostra: il processo/la macchina si ferma per ~20-30 min a intervalli regolari (buchi nel
heartbeat di run.log); alla ripresa `last_ok` era vecchio di oltre 15 min e il primo `dex_batch`
vuoto bastava a chiudere a zero.

**Correzioni.**
- `stall_reset`: se un loop (shadow o risk) è rimasto fermo più di `STALL_RESET_SEC` (180 s),
  il conteggio dei 15 min riparte da zero (warn nel log).
- `verify_no_price`: prima di scrivere -100/rug il prezzo viene riverificato con una seconda fonte
  gratuita (Jupiter price v3, a blocchi di 50). Vivo → prezzo aggiornato; assente su Jupiter → chiusura
  "(confermato da Jupiter)"; Jupiter non risponde → attesa (al massimo `NOPRICE_MAX_WAIT` = 15 giri),
  poi lo shadow chiude come **DATI NON VALIDI** al prezzo noto, con rug=0 e senza passare dal Learner.
  Le posizioni (paper) non vengono mai vendute a zero senza conferma.
- Nuova colonna `valido` in shadow_trades.csv. Le 17 righe delle 01:04 sono state marcate `valido=0`,
  `rug=0`, uscita con prefisso "DATI NON VALIDI (pausa processo, token vivo)"; dallo state sono stati tolti
  i loro esiti nel Learner (17 in learner_shadow, 16 in learner_bonus).
- Blocco ticker impostori esteso ai **nomi** e ai simboli scritti per esteso dei grandi asset (Bitcoin,
  Ethereum, Solana, Binance, Tether, USD Coin, ... anche "Wrapped …", "The … Token", "...coin"): solo match
  esatto, quindi "Bitcoin Cat" o "Solana Cat" passano. I mint ufficiali passano.
- Ticker di azioni (AAPL/Apple, NVDA, TSLA, ...) **non bloccati**: il loro esito viene solo annotato
  (evento Learner `ticker_azione`), sia per gli shadow sia per i trade paper.
- HYPERCHAN è in revisione: nei commenti dei test non è più indicato come controllo pulito.
- Test: tests/test_v84.py §21–22.

## Aggiornamento 31/10/2026 (mattina) – v8.4e: HYPERCHAN rug, un solo finanziatore, storico creatore, LP 0%

**HYPERCHAN (3ApVr1xp…pump) = RUG**, 6ª lezione negativa (seed `hyperchan-2026-10-31-rug`, etichetta `rug`,
pnl None: trade paper +6,4% e shadow già registrati, niente doppio conteggio). GMGN 07:02: -99,84%, liquidità
$1,33k, mcap $2,58k, storico creatore "Rug pull" 51,73%. Schema: 59 dei 60 holder controllati finanziati da un solo
wallet, BXTDKd (gruppo stimato ~148 wallet al 14,3%). Lo shadow delle 00:04 chiuso a -100 alle 01:04 resta **non
valido**: GeckoTerminal (5m) dava -55% alle 00:45 e ~-43% alle 01:00–01:05; il rug completo è arrivato dopo.
HYPERCHAN non è più un controllo pulito/vincente da nessuna parte (test §13 ora si aspetta HARD).

- **cluster_v2, un solo finanziatore** (HARD): il finanziatore diretto più frequente (exchange noti esclusi, hub
  inclusi) copre ≥ 50% dei holder controllati e almeno 10 wallet. Calibrazione sui 60 controllati: BNB 60/60
  (hub 36MESj), PILLSEY 60/60 (hub 9Ja2iw), HYPERCHAN 59/60 (BXTDKd) → hard; Bonnie massimo 1 wallet per
  finanziatore (1 solo wallet nuovo con finanziatore), DESSA 1 (+3 KuCoin esclusi), CATE 5, GLITCH 5 (+7 MEXC
  esclusi), BEAST 5 → la regola non scatta (CATE e GLITCH restano hard per le regole v8.4). Il "pulito" sintetico resta.
- **Storico del creatore** (`sources/creator_history.py`, API pubblica pump.fun: `coins-v2/{mint}` e
  `coins-v2/user-created-coins/{creator}`, 2 chiamate per creatore nuovo, cache 12 h in
  `sources/creator_history_cache.json`, escluso dal sync; niente Helius). Solo per i candidati a un acquisto vero.
  Precedenti = token creati prima, vecchi ≥ 24 h, che hanno davvero tradato (ATH ≥ $15k o migrati); morti/rug =
  oggi < 10% dell'ATH o bannati. HARD con ≥ 5 precedenti e ≥ 70% morti/rug (shadow `creator_serial`); soft
  graduale dal 50% (penalità fino a 0,04, pesata per n/5). I lanci mai partiti (~90% per i deployer seriali,
  anche quelli di Bonnie e iCoin, vincenti) sono contati a parte e non pesano: con il conteggio "grezzo" Bonnie
  (46/49 morti) e iCoin sarebbero stati hard. Storico su 45 creatori degli shadow: nessuno hard; Bonnie 2/3 (soft
  ~0,02), iCoin 8/14, PILLY/TYGR 10/17, SSI 5/13. BEAST, PILLSEY, GLITCH, DESSA: creatori al primo token (nessun
  effetto). HYPERCHAN, BNB, AgenC: sconosciuti a pump.fun → `unknown`, nessun effetto.
- **LP per niente bloccata/bruciata (Kiwi 07:07)**: LP < 0,5% ("0%") → MAI acquisto vero, solo shadow con tag
  `lp_unlocked_zero`; LP non verificabile (nessun dato LP) → come honeypot sconosciuto: shadow `lp_unknown`, nessun
  acquisto (prima: scarto senza shadow). Il Filter apre lo shadow direttamente (senza Analyst); in più una
  barriera finale `lp_gate` nell'Executor, prima di ogni ordine, indipendente da punteggio, segnali soft, size,
  narrativa/KOL, deep review e override. Il Learner non modifica nulla con questi esiti (`LP_GATE_KEYS`), le soglie
  sono costanti fuori da CFG/override. Tra 0% e 50% nulla cambia (soft `lp_unlocked`/`lp_unlocked_grave`, estremi
  creatore/rimozione hard). Storico: 10 token con LP 0% erano passati come soft (nessuno comprato; 3 shadow: STONK -0,5/-0,7%, TREND -40,9%).
- Test: `tests/test_v84e.py` (nuovo); `test_v7`/`test_v6_soft` (LP 0% ora shadow-only), `test_v82`/`test_v84 §13`
  (regola un solo finanziatore disattivata dove si provano altre regole; HYPERCHAN sintetico = hard).

## Aggiornamento 31/10/2026 (mattina) – v8.4f: correzioni dalla review Claude (PAPER)
Fonte: `docs/claude_knowledge_review.md`. Secondo riavvio (SIGTERM) dopo quello della v8.4e.

**Applicate**
1. **GoPlus, tassa reale** (`sources/goplus.py`, `_fee_pct`): `maximum_fee` (tetto in unità grezze) non viene più
   letta come percentuale. Si leggono solo `*_bps`/basis point e `fee_rate`/rate/percent (frazione o %); chiavi con
   max/epoch/amount/authority/time ignorate. REMARC (`fee_rate 0.01`, `maximum_fee 1e15`) = **1%** (prima 1e15%,
   scarto rigido). Tassa < 10% resta soft, ≥ 10% rigida (`HARD_FEE_PCT` 10), "fee modificabile dal dev" resta rigida.
2. **Niente acquisti col prezzo in calo**: `MIN_CHANGE_M5_PCT` da −8 a **0**, override limitato a [−8, +5].
   Il gate (`m5_gate`) usa lo stesso dato di `ret_5m` (candele 1m se ci sono, altrimenti chg_m5) e vale per tutti
   i percorsi: Analyst dopo le candele e barriera all'inizio di `_execute_buy` (nessuna eccezione KOL, narrativa,
   trending). Il taglio grossolano a −8 prima delle candele resta (stesse chiamate di prima).
3. **Esclusioni holder**: nel Filter incinerator/System Program (`sources/solkeys.py`, `DEAD_ADDRS`) ed exchange
   noti (hot wallet statici di cluster_v2) fuori dal top 10, quote insider e creatore; mostrati a parte
   (`cex_pct`, `cex_names`, `dead_pct`, "exchange a parte X%" nel motivo). In cluster_v2 (`_add`) fuori solo
   `exclude` (pool/curve/locker RugCheck), indirizzi morti e owner PDA (fuori dalla curva ed25519, verifica locale);
   nel vecchio cluster v7 (riserva) tolto "quota ≥ 10% = pool". Una balena con un wallet normale resta.
   Nota: nel cluster_v2 attuale la soglia del 10% non c'era più; c'era solo nel cluster v7 di riserva.
   Ricontrollo sugli snapshot: BEAST soft, DESSA soft, PILLSEY hard, GLITCH hard (8 PDA tolti), BNB hard,
   HYPERCHAN hard, Bonnie soft (5.9%), CATE hard: **nessun esito cambiato** (nessun holder ≥ 10% negli snapshot).

**Solo shadow** (in `entry_features`, nessun effetto sulle decisioni, zero crediti Helius/Birdeye)
- `pool_supply_pct`, `top1_pct`, `top3_pct`, `imp_top1_pct`, `imp_top3_pct` (impatto = 1 − (f/(f+s))²), tag `thin_pool`
  (impatto top1 ≥ 50% o top3 ≥ 75%).
- `wick_entry`, `range_pos_30m`, `ret_15m`, tag `mid_range` (0.33–0.66) e `falling_knife` (ret_15m ≤ −10%), dalle
  candele già scaricate.
- `btc_1h/4h/24h`, `sol_1h/4h` (CoinGecko gratis, 2 chiamate ogni 15 min, in background), tag `risk_off`
  (BTC 4h ≤ −3% o 24h ≤ −5%) e `rel_weak` (token in calo con SOL 1h ≥ +1%). Tag in `v84f_tags`.
- `supply_entry` + `supply_20m`/`supply_up_pct` nel ricontrollo RugCheck delle posizioni dopo 20 min (tag `supply_up`
  sopra +0.1%; nessuna uscita).
- `alt_base`, `alt_tp3x` (TP3 +200% vende il 33%), `alt_moon15` (15% senza trailing fino al tempo massimo, al
  crollo della liquidità o all'uscita reale), simulati sul price path alla chiusura.
- Scarti GoPlus/honeypot (rigidi e "soft sotto soglia") sempre in shadow: tag `goplus_hard`, `honeypot_hard`,
  `honeypot_soft`, più `fee_auth_known` se l'authority della fee è un PDA/multisig. Learner fermo su questi tag.

Test: nuovo `tests/test_v84f.py`; tutte le 13 suite OK. Inclusi nel commit `CLAUDE.md`, `docs/` e l'esclusione
`claude_report*` in `tools/sync_github.sh`.

## Aggiornamento 31/10/2026 (mattina) – v8.5: correzioni di Claude + cluster sconosciuto = niente acquisto (PAPER)

**Crediti: Claude** (commit 8dd05f1..68011c5, revisionati e adattati; patch `apply.patch`).
- Correzioni di Claude sul percorso live (inerte in PAPER): `live_pending_tx.json` per le tx in sospeso,
  `MARK_NOROUTE_CONFIRM` = 3 (override 2-6, intero), liquidità assente su DexScreener ≠ liquidità 0
  (`liq_known` in `summarize`), correzioni in `sources/cluster_v2.py`, test `tests/test_v85.py`.
- Nostra aggiunta alla patch: `sell()` risolve una vendita live precedente arrivata dopo il timeout
  (`resolved_prev`), `_sell_position` la tratta come parziale se restano token; `live_pending_tx.json*` in BAD del sync.
- **Decisione Kiwi:** `CLUSTER_UNKNOWN_ACTION` passa da `soft` a `skip`: cluster non verificabile = niente acquisto,
  shadow con tag `cluster_unknown`, contatore `stats.cluster_unknown_skip` (riga nel recap). Override: ammesso solo
  `skip` (non si può riallentare). Storico events.jsonl: 15 token "unknown" su 43 scansioni cluster v2; uno solo
  (CATE, 30/10 20:14) era stato comprato con la vecchia regola soft.
- **Casi aperti (`CASE_WATCH`)**: Leverhedge (FWatMY…Bympump) = `caso_stelle_aperto`, né buono né cattivo
  (Bubblemaps ~14 cluster a stella, GMGN rischio rug 46.9%, top10 4.76%, +25.000% 24 h). Controllo DexScreener
  gratuito ogni ora (prezzo, liquidità, mcap) in `case_watch.jsonl`/`case_watch.json` fino al rug (prezzo o
  liquidità −80% dal massimo, liquidità < $1k o nessuna coppia) o a 7 giorni. Etichetta automatica `bad_cluster`
  sostituita, niente promozione a 'rug' dal label_watch, nessun effetto sul Learner. Mai acquisto (Filter ed
  Executor lo scartano con "caso aperto").
- Casi Kiwi 31/10 in `LABEL_SEEDS` (pnl None, nessun effetto sul Learner): Dacity = controllo positivo confermato;
  GTA6, STONK, FLOAT, neet, LUMI = probabile buono, da confermare. Vedi `docs/kiwi_controls.md`.

Test (solo su copia in /tmp): 14 suite su 15 OK compreso il nuovo `tests/test_v85b.py`; `test_narr` fallisce anche
senza patch (websocket pump.fun). ATTENZIONE: le suite scrivono e cancellano file in DATA_DIR, mai lanciarle nel live.

## v8.5c (31/10/2026, decisioni di Kiwi delle 08:08)
- RugCheck: l'API non e cambiata (stessi endpoint, nessuna auth nuova, limite 15); da stamattina il report risponde 400 "unable to generate report" per tutti i token. Ripiego on-chain `rc_fallback`: authority mint/freeze e Token-2022 dal conto del mint, top 10 da getTokenLargestAccounts con proprietari (pool, PDA di programmi e authority AMM esclusi), reti insider dall'endpoint gratuito /insiders/networks, LP verificata solo per pool PumpSwap del token con LP mint a offerta 0 (bruciata).
- Con il ripiego un token passa solo se: authority revocate, niente Token-2022 rischioso, LP bruciata/bloccata nota, top 10 calcolato, cluster v2 ok/soft (Analyst), honeypot ok + GoPlus ok + nessun segnale soft (Executor). Size x0.5, tag `rugcheck_down`. Ogni dubbio = niente acquisto, shadow `rugcheck_down`.
- Avviso Telegram alla caduta (5 errori di fila) e al ritorno di RugCheck, al massimo uno all'ora.
- Bundle RugCheck soft solo per token affermati (>=7 giorni, mcap >=$5M, >=5k holder) e con cluster v2 ok/soft; per tutti gli altri resta hard. Col ripiego gli holder totali non sono noti, quindi il bundle resta hard.
- LP 0% su DLMM/CLMM: la concentrazione delle posizioni non si verifica gratis (Meteora non espone l'elenco posizioni per pool, CLMM = NFT), quindi resta solo shadow. Eta/attivita e neet invariati.
- Test: tests/test_v85c.py (solo su copia in /tmp).

## v8.5d (31/10/2026, stesso giro)
- Pause dell'host (VM congelata, 'virtual machine fork' + timeout ngrok): `host_pause_check` a ogni giro dei cicli Risk e Shadow. Se tra due giri passano piu di 120 s di time.time() o c'e un salto rispetto a time.monotonic(): evento host_pause, Telegram al massimo ogni 30 min (pause raggruppate), timer 'nessun prezzo'/no_route azzerati per posizioni e shadow, primo giro dopo la ripresa solo nuova quotazione (nessuna chiusura), riga gap_host nei price_paths, chiusure shadow entro 5 min dalla ripresa = valido 0 / invalid_for_learning=host_pause.
- Retroattivo una volta (host_pause_retro.json): 14 pause tra 00:14 e 06:50 ricavate dai buchi nei price_paths; 33 chiusure shadow entro 5 min dalla ripresa (17 gia DATI NON VALIDI, compresa AgenC, + 16 nuove) e l'esito positivo di GLITCH (lezione negativa) tolti dal Learner; righe CSV marcate.
- GLITCH e le altre lezioni negative: un loro esito positivo non va MAI al Learner (invalid_for_learning=lezione_negativa).
- Nuova colonna shadow_trades.csv: invalid_for_learning. Dashboard: le righe valido=0 restano visibili ma escluse da conteggi, win, rug e media.
- Watchlist curva (curve_watch.json): token scartati per liquidita $0 ancora sulla curva pump.fun -> ricontrollo UNA volta dopo la migrazione, entro 6 ore, max 12 all'ora; rientrano dalla pipeline completa senza eccezioni (DexScreener gratis per il controllo).
- narrative_relax (solo misura): scartato SOLO per momentum/VWAP o punteggio (senza segnali soft) e legato a una narrativa fresca con trusted_mentions > 0 -> shadow tag narrative_relax. Nessun acquisto; il Learner non fa passi su narrative_relax/rugcheck_down/bundle_established.
- Test: tests/test_v85d.py (solo su copia in /tmp).

## v8.5e (31/10, analisi di Mark /tmp/filter_an/report.md) — codice pronto, non ancora in esecuzione
- (D) Fascia di punteggio 0.45 ≤ score < soglia (0.50): acquisto **solo PAPER**, size ×0.5, tag `score_band`
  (soft + segnale `score_band:<score>`). Solo con sicurezza tutta passata: Filter senza scarti gravi,
  cluster v2 eseguito e ok/none/soft (hard e unknown = niente), storico creatore, ripiego RugCheck,
  honeypot ok con GoPlus ok e nessun segnale soft (altrimenti shadow `score_band`), HONEYPOT_CHECK attivo.
  I controlli prima dell'acquisto ora partono da 0.45 (`gate_thr`). Con RugCheck giù le riduzioni si sommano (×0.25).
- (E) Acquisti/vendite 5m tra 1.20 e 1.30 con sicurezza ok: SOLO shadow `buysell_relax`, mai acquisto.
- `score_band` e `buysell_relax` in MEASURE_ONLY_KEYS (il Learner non allenta nulla da soli).
- Fix: il cancello v8.5 RugCheck giù / bundle affermato scartava anche il cluster v2 pulito (livello `none`); ora accetta ok/none/soft.
- Config: SCORE_BAND, SCORE_BAND_LOW, SCORE_BAND_SIZE_MULT, BUYSELL_RELAX_SHADOW, BUYSELL_RELAX_LOW.
- Test: tests/test_v85e.py (copia in /tmp).

## v8.5f (31/10, review di Claude Code integrata da Kiwi bot) — solo PAPER
- **Ripiego RugCheck giù (compito 1, branch `claude/rugcheck-fallback-review`)**, attivo solo quando RugCheck è giù:
  reti insider non lette = dubbio (prima il controllo bundle veniva saltato); holder totali da cluster v2 (`holders_total`)
  >= MIN_HOLDERS, altrimenti dubbio; creatore dallo storico pump.fun controllato contro la lista nera (creatore
  sconosciuto = dubbio); con HONEYPOT_CHECK spento o modulo honeypot assente niente acquisto. `crh` azzerato a ogni token.
  Conflitti risolti tenendo v8.5e: cluster v2 `none` (pulito) resta accettato anche in `rc_down_doubts`, fascia score_band invariata.
- **Cluster BEAST/DESSA (compito 2)**: regola "famiglie soft" (>= 3 famiglie = hard) solo in SHADOW (`CLUSTER_FAM_MODE`=shadow,
  nessun effetto sugli acquisti); `cluster2_brief` registra uniform/single_funder/stars/cexf/recv/combo per calibrarla.
  Fix: override `MARK_NOROUTE_CONFIRM` ora davvero applicabile (non impostato in overrides.json, nessun cambio).
- **Test (compito 3)**: `tests/test_cluster_rules.py` (bordi uniform/star/single-funder/ticker impostore),
  `tests/test_rc_fallback_review.py`, `tests/test_cluster_beast_dessa.py`; `tests/test_v85e.py` aggiornato al ripiego più severo.
- Con RugCheck su: nessun cambio di comportamento. Controlli positivi (Dacity, Bonnie, LUMI) invariati.

## Web app – stream a basso consumo (31/10/2026, 09:29 ora di Roma)

Solo `web/server.py` e `web/index.html`; bot, `.env` e modalità PAPER non toccati.

- `/api/stream`: snapshot completo **solo alla connessione**; poi controllo ogni 3 s e evento `patch`
  **solo se i dati cambiano**, con le sole sezioni cambiate (per le sezioni-oggetto, solo le sottochiavi
  cambiate). `files_age` da solo non genera patch. Heartbeat `hb` (~20 byte) ogni 12 s senza patch.
- Il punto "ora" del grafico e i contatori "min" di posizioni/ombra avanzano a passi di 60 s
  (prima cambiavano a ogni giro e rimandavano tutto ogni 1,5 s).
- Tolto `no-transform` dallo stream: Caddy ora lo comprime in gzip (eventi consegnati subito, verificato).
- `/api/snapshot` con `ETag` → 304 se invariato; polling di riserva ogni 5 s (prima 2 s).
- `index.html`: unisce le patch in `D`, `hb` conta come "vivo"; layout mobile invariato.
- Banda per scheda aperta, misurata su https://desk.linkgox.com per 60 s: **5.040.517 B → 73.673 B** (−98,5%).
- Ripristino: `/opt/desk/backups/web-20261031-092806/` (server.py, index.html, Caddyfile) →
  copiare in `/opt/desk/web/` e `systemctl restart desk-web`.

## v8.5g (31/10/2026, ora di Roma) – avvisi Telegram senza doppioni dopo un riavvio — solo PAPER
- **Stato anti-doppioni su file** `alert_state.json` (accanto a `state.json`, scrittura atomica tmp + `os.replace`):
  RugCheck (`alerted`, `alert_t`, `down`, `down_since`, contatori ripiego), pausa host (`alert_t`), messaggio di avvio,
  ultimo invio dei gruppi (`hard`, `err`). Solo orari e chiavi: mai token né chat id. Caricato all'avvio; file assente
  o rotto = come prima. Orari nel futuro ignorati.
- Limiti invariati: RugCheck max 1 avviso/ora, pausa host max 1 ogni 30 min, gruppi secondo il loro intervallo.
  Se RugCheck era giù prima del riavvio, il ripiego (più severo) resta attivo finché non arriva un report riuscito:
  niente nuovo avviso "NON disponibile" e niente falso "di nuovo disponibile".
- **Messaggio "Desk avviato"** saltato se un altro è partito meno di 10 min prima (`TG_START_DEDUP_SEC`=600): riga nel log.
- **Riga di log a invio riuscito** (`Telegram: messaggio inviato (n. N, X caratteri): <inizio testo mascherato>`).
- `sources/notify_telegram.py`: `okf`, `onflush`, `group_state()`, `restore_groups()`. Token sempre ripulito dagli spazi.
- Mirror (`/workspace/desk_migration/mirror_from_server.sh`): esclusi anche `.env*`, `.telegram*`, `venv`, `backups`
  (Caddyfile con hash), `*.tmp*`; "file changed as we read it" non segna più FALLITO. `alert_state.json` escluso dal deploy.
- Test: `tests/test_v85g_alerts.py` (copia in /tmp). Tutti i 22 test passano (test_narr richiede una copia senza
  narratives.json/events.jsonl, come già prima).

## v8.5h (31/10/2026, ora di Roma) – PnL assurdi a pool svuotata (segnalato da ticher) — solo PAPER
Causa: quando la pool viene svuotata o sostituita, DexScreener dà un prezzo x10.000 (es. 2,6e-5 → 0,3768) con una
liquidità falsa; lo shadow faceva la presa di profitto a quel prezzo e chiudeva a +405.686% (NEX Ai 5qHwzQ…),
+183.181% (Grok Ev9GTH…), +8.128% (NEX Ai 9vaJCv…), tutti valido=1.
- **Uscita per liquidità crollata** (≥ `LIQ_DROP_EXIT_PCT` 50%): la parte non ancora venduta vale 0, sempre rug
  (shadow: `shadow_decide` → azzera; posizione vera: `zero=True` solo in paper, in live resterebbe la vendita vera).
  Le prese di profitto già fatte restano. `LIQ_CRASH_ZERO` (default true) per tornare al comportamento di prima.
- **PnL > `PNL_IMPLAUSIBLE_PCT` (1000%)**: shadow `valido=0`, `invalid_for_learning=pnl_implausibile`, rug=1, fuori da
  learner_shadow, learner_bonus e learn_adjust. Trade vero: `trades.csv` con nuove colonne `valido`/`invalid_for_learning`,
  nessun esempio al modello/soglia/segnali; il creatore va comunque in lista nera se l'uscita è per liquidità crollata.
- **Salto di prezzo** oltre `PRICE_SPIKE_MULT` (20×) in un solo aggiornamento: prezzo non preso (si prende solo una
  liquidità più bassa = segnale di rug), `last_ok` non avanza → dopo 15 min decide la seconda fonte (Jupiter), come per un
  prezzo mancante. Nei price_paths resta una riga `ev=spike_ignored`.
- **Correzione retroattiva una sola volta all'avvio** (`apply_liq_crash_retro`, esito in `liq_crash_retro.json`, dato
  runtime escluso dal deploy): righe shadow valido=1 con PnL > 1000% → valido=0; righe valido=1 uscite per liquidità
  crollata → PnL rifatto con la regola nuova (prese di profitto ricostruite dai price_paths ignorando i salti, mai più
  alto di prima, rug=1); senza percorso prezzi ricostruibile e con massimo ≥ TP → valido=0
  (`liq_crash_retro_percorso_prezzi_assente`). Stessi esiti corretti/tolti in learner_shadow e learner_bonus.
- Test: `tests/test_v85h_liq.py` (copia in /tmp, `PYTHONPATH=.`). Tutti i 23 test passano.
- Rollback: `desk_before_v85h.py` (locale) e `/opt/desk/backups/v85h-<ora>/` sul server (codice + CSV + state).

## v8.6 (31/10/2026, ora di Roma) – idee dai bot open source (A+B+C) + smart wallets v8.5s — solo PAPER, solo misura
Nessun effetto su acquisti, punteggio, size o uscite, tranne B (pausabile con authority = scarto rigido).
Nessun codice scaricato eseguito: logica riscritta da zero leggendo i sorgenti.
- **A – PnL CPMM realistico** (idea hoangduong `exit_fill`): `cpmm_exit` = x·y=k esatto sulla metà della pool che
  resta dopo le nostre vendite precedenti (drain-aware), slippage 2×size/liq, fee della pool per dex
  (`CPMM_POOL_FEE_PCT`: pumpfun 1,25%, pumpswap 0,30%, raydium/meteora 0,25%; altrimenti FEE_PCT) e priority fee; la
  presa di profitto si riempie come LIMITE al gradino (`tp_cap_price`), mai al prezzo oltre. Colonna nuova
  `pnl_cpmm` in trades.csv e shadow_trades.csv accanto a `pnl_pct` (che resta la formula v8.5h, invariata). Con
  l'uscita a zero v8.5h la parte residua vale 0 anche qui. Posizioni aperte prima del deploy: partono dal realizzato.
- **A – latenza d'uscita** (backtest Mark: stop a -12 chiude a mediana -19,6%): per ogni uscita stop/crollo/liquidità,
  vera e shadow, colonne `stop_level` (livello teorico), `exit_fill` (fill netto % sull'ingresso della vendita finale),
  `exit_lag_s` (secondi dal primo controllo sotto lo stop alla vendita); in `entry_features` anche `exit_gap_prev_s`
  (intervallo prima del primo controllo sotto), `exit_mark_pct`, `chk_gap_avg_s`. Frequenza dei controlli INVARIATA.
- **B – Token-2022** (honeypot): `pausableConfig` con authority = rigido (prima soft). Permanent delegate, transfer hook
  con programma e account congelati di default erano GIÀ rigidi e restano rigidi (non allentati). Nuovo
  `t22_flags` (transfer_hook, transfer_hook_auth, default_frozen, permanent_delegate, pausable…) nell'esito del controllo
  e evento `Executor/t22_flags`.
- **C – seconda conferma** (idea warp-id CONSECUTIVE_FILTER_MATCHES): `ENTRY_CONFIRM_MODE` shadow,
  `ENTRY_CONFIRM_SEC` 45: dopo ogni acquisto paper e ogni shadow aperto, 1 chiamata DexScreener: liquidità ≥ 85%,
  prezzo non sotto -8%, momentum 5m ok, acquisti ≥ vendite 5m. Esito in `entry_features.confirm2` {ok, why, dp, dl},
  evento `Analyst/confirm2`, contatori `confirm2_ok/ko`. Mai blocca.
- **v8.5s smart wallets** (patch /workspace/kol_wallets/patch, applicata con apply_patch.py): solo log, 0 chiamate in più.
- Test: `tests/test_v86.py`, `tests/test_v85s_smart.py`. Tutti i 25 test passano (copia in /tmp, `PYTHONPATH=.`).
- Rollback: `/opt/desk/backups/v86-<ora>/` (desk.py, sources/honeypot.py, sources/cluster_v2.py, CHANGES.md):
  ricopiarli in /opt/desk e `systemctl restart desk-memecoin`. Per spegnere solo C: `ENTRY_CONFIRM_MODE`=off.

## v8.7 (31/10/2026, ora di Roma) – dataset Slinky21 + primi scambi pump.fun (D+E+F) — solo PAPER, solo SHADOW

Nessun effetto su acquisti, score, soglie o Learner: solo campi in `entry_features` (trades.csv / shadow_trades.csv).

- **D – seme creatori** (`sources/creator_seed.json`, 47.946 creatori dal corpus Slinky21 CC BY 4.0, 5 giu–14 lug 2026,
  costruito sul box con `docs/analysis/build_creator_seed.py`): `[lanci, graduati, rug, falliti_dopo_grad]`, letto
  da `creator_history` prima delle API. Campi: `creator_seed`, `serial_launcher` (>= 10 lanci, 0 graduati: 6.958
  creatori). Nota: graduated_at quasi assente prima del 19 giu (graduati sottostimati). ~20 MB di RAM.
- **E – primi scambi** (`sources/smart_trades.py`, endpoint pubblico trade di pump.fun, niente Helius): in background
  all'ingresso (trade paper e shadow), solo mint `...pump`, max `STX_MAX_PER_H`=30 token/ora e `STX_PAGES`=3 pagine
  da 100 (oltre: `incompleto`). Campi: `sniper_pct`, `first20_sold_pct`, `dev_first_sell_dt` (+ `stx`), evento
  `Analyst/early_trades`. Spegnere: `STX_MODE: "off"`.
- **F – prior pre-addestrato** (`sources/model_prior.json` da `docs/analysis/pretrain_slinky.py`): `p_prior` =
  sigmoid(b + Σ w·f) sulle 12 feature dell'Analyst. Test fuori campione (dal 4 lug): AUC 0,700 vs regola 0,651;
  precisione top 10% 13,8% vs base 10,4%. Calibrazione spostata (base 0,25% train vs 10% test): solo ranking.
- Fix A: `pnl_cpmm` degli shadow aperti prima della v8.6 contava due volte la vendita in corso (es. NEX Ai 111% invece di ~57%): corretto (`just_realized`). Le righe chiuse tra 10:33 e 10:45 con `pnl_cpmm` > `pnl_pct` di molto sono da ignorare.
- Fix: evento `confirm2` usa `pos_kind` (prima `kind` sovrascriveva il tipo evento).
- Test: `tests/test_v87.py` + suite completa OK.
- Rollback: `cp /opt/desk/backups/v87-*/desk.py /opt/desk/ && cp .../sources/creator_history.py /opt/desk/sources/
  && systemctl restart desk-memecoin` (i file nuovi possono restare: senza hook non vengono letti).

## v8.8 (31/10/2026, 11:13 ora di Roma) - call Discord "Noah Knows Trades" (SHADOW)
- `sources/discord_calls.py` legge `sources/discord_calls.json` (mint -> call_ts, author, mc_at_call, label, max_x),
  scritto offline da `/workspace/discord_calls/label_calls.py` (GeckoTerminal/DexScreener, mai Helius).
- `discord_tag(mint)`: `discord_call=1`, `discord_call_age_min` (minuti call -> vista del bot; negativo = bot prima della call)
  negli eventi `reject`, in `entry_features` (trade e shadow), sugli shadow (`sh`, evento `shadow_open`, tag `discord:nkt`).
- Peso nello score 0, nessuna chiamata di rete, nessun credito. Label/max_x (esito futuro) MAI esposti come feature.
- `DISCORD_CALLS_MODE`: 'shadow' (default) / 'off'. Test: `tests/test_v88_discord.py`.

## v8.9 (31/10/2026, ora di Roma) - revisione e integrazione dei branch di Claude — solo PAPER

Branch uniti (branch `integration/v8.9`): `claude/watch-pause` (compito 8), `claude/exit-price-robust` (compito 4),
`claude/review-v86-v87` (compito 7), `claude/merge-6-10` (compiti 6 + 10, conflitto gia risolto da Claude),
`claude/prior-calibration` (compito 9, solo offline).
- Watchlist: dopo una pausa dell'host i token non scadono piu in blocco. Fix nostro: la pausa nel ciclo del Filter
  si misura solo sullo sleep (un giro lento non e una pausa dell'host, niente falsi avvisi).
- Uscite: "nessun prezzo" = rug solo se anche il preventivo di vendita dice `no_route`; `no_route` confermato su due
  giri negli shadow; "liquidita crollata" (azzeramento/rug) solo dopo `LIQ_CRASH_CONFIRM` = 2 letture distinte;
  prezzo mancante nel prefiltro = nessun esito (prima -100%).
- Token-2022: transfer hook spento ma con authority attiva = scarto rigido (approvato da Kiwi).
- `_cpmm_cap` tolto dopo una vendita fallita; `creator_known` nella feature E; una sola `stx_task` alla volta.
- Anti-dump (SHADOW): `min_since_migration`, `mig_src`, `bs5`, `top10` e `age_gate` 10/15/20 min registrati in
  `entry_features`. Nessun ingresso bloccato. Script offline `backtest/anti_dump_report.py`.
- Uscite veloci (SHADOW, `FAST_EXIT_MODE`): SL/TP1/trailing a ogni prezzo (trade PumpPortal + polling DexScreener
  ~12/min), campi `fast_exit_*` alla chiusura. Le uscite vere NON cambiano. Nessuna chiamata Helius/Birdeye.
- Offline: `docs/analysis/calibrate_prior.py`. Fix nostro (anche in `anti_dump_report.py`): rug, lezioni negative e scarti rigidi mai
  esempi positivi, anche con pnl > 0.
Saltati perche gia in `main`: `rugcheck-fallback-review`, `cluster-beast-dessa`, `tests-cluster-rules`,
`amazing-goodall-nfn3me` (v8.5). Non ancora rivisti: `bug-hunt-2`, `shadow-sample`, `retro-label`.
