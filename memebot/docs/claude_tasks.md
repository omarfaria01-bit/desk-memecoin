# Compiti per Claude Code — Desk Memecoin (31/10/2026)

Leggi prima `CLAUDE.md` (in questa cartella `memebot/`). Le regole sotto valgono per **ogni** compito.

## Regole (richiamo di CLAUDE.md, valgono sempre)
- **Solo PAPER.** Mai attivare il live, mai toccare wallet, chiavi private, `.env` o la modalità di trading.
- **Niente chiavi.** Nessuna chiave API (Helius, Birdeye, Telegram...) nel codice, nei log, nei test, negli export,
  nei commit. Non leggere e non stampare `.env`. Su GitHub c'è solo `.env.example`.
- **Un fix = un commit piccolo su un branch `claude/<nome>`.** Mai commit o push su `main`.
  Il deploy lo fa il reviewer (Kiwi) dopo la verifica: tu non riavvii il bot e non lanci `tools/sync_github.sh`.
- **Test solo su copie in `/tmp`.** Mai scrivere su `state.json`, `trades.csv`, `shadow_trades.csv`, `equity.csv`
  o altri file di stato/dati reali del bot. Per i dati: `cp` in `/tmp/...` e lavora sulla copia, in sola lettura.
- Le soglie non si cambiano a mano nel codice: vanno in `overrides.json` (vedi `overrides.example.json`) con clamp.
- **Lezioni negative** (BEAST, DESSA, PILLSEY, GLITCH, BNB falso, HYPERCHAN): sono token cattivi confermati.
  Si usano solo come casi da scartare. **Mai** come esempi puliti, vincenti di controllo o fixture "buone".
- Ogni proposta dice: quale regola, quale soglia, su quali dati è provata, cosa succede ai controlli positivi
  (`docs/kiwi_controls.md`) e alle 6 lezioni negative.

## Come chiudere un compito
1. Branch `claude/<nome-compito>` con uno o pochi commit piccoli (uno per fix).
2. Test verdi: `python3 -m pytest tests` (o il test nuovo) eseguiti su copie in `/tmp`.
3. Nota di **5 righe** in `docs/claude_notes/<nome-compito>.md`: cosa, perché, file toccati, prove/test,
   rischi o effetti su controlli positivi e lezioni negative.
4. Non fare merge: il reviewer verifica e fa il deploy.

---

## Stato compiti precedenti
- 1, 2, 3: chiusi. 4 (prezzo in uscita): coperto in gran parte da v8.5h (`LIQ_CRASH_ZERO`, `PRICE_SPIKE_MULT`, `PNL_IMPLAUSIBLE_PCT`): verifica solo che non restino −100 falsi. 5 (backtest SL/TP dinamici): **annullato**, Mark ha già dimostrato che non danno guadagno.
- Codice di riferimento: v8.7 + tag Discord (vedi `CHANGES.md`, ultime voci). Lavora sull'ultimo `main`.

## 6) Uscite veloci: latenza dello stop sotto i 5 s — priorità media (dopo il 10)
Nota: il backtest di Mark (results.md §12) stima al massimo ~+2 punti/trade e solo con cali graduali; a ottobre i dump sono in una sola candela. Fallo dopo il compito 10, in modalità shadow.
Il problema principale del bot: lo stop a −12% chiude in media a −19,6% perché il controllo arriva tardi (posizioni vere ogni 15 s × 2 conferme, shadow ogni 30 s; `exit_lag_s` misurato ≈ 30 s).
- Token pump.fun/PumpSwap: usa il websocket PumpPortal **già aperto** dal bot; iscriviti ai trade dei mint in posizione (vere + shadow) e valuta SL/TP/trailing a ogni trade (prezzo da `solAmount/tokenAmount` o dalle riserve). Disiscrivi alla chiusura.
- Altri token: polling DexScreener ogni 5 s solo per i mint in posizione (resta sotto ~12 richieste/min; rispetta i limiti gratuiti; niente Helius).
- Sostituisci la regola "2 controlli di fila" con "2 prezzi ad almeno 3 s di distanza" (anti-spike, mantieni `PRICE_SPIKE_MULT`).
- Tutto dietro flag `FAST_EXIT_MODE` = `shadow|on|off` (default `shadow`: registra `fast_exit_fill`, `fast_exit_lag_s` accanto a quelli attuali senza cambiare le uscite).
- Test: replay di uno stream di trade finto da fixture; nessun crash se il websocket cade (riconnessione + fallback al polling).
- Nota: riporta la latenza stimata prima/dopo e l'effetto atteso sullo slippage.

## 7) Review del codice v8.6/v8.7 (scritto in fretta, va controllato)
File/funzioni: `pnl_cpmm` (simulazione x·y=k), controlli Token-2022, conferma a 45 s (`confirm2`), smart wallets v8.5s, `creator_seed.json`/`serial_launcher`, feature E (`sniper_pct`, `first20_sold_pct`, `dev_first_sell_dt` via API pump.fun), `p_prior`.
- Cerca bug, doppi conteggi (già trovato uno su `pnl_cpmm` negli shadow aperti prima di v8.6), eccezioni non gestite, chiamate di rete nel loop principale che possono bloccarlo, uso di RAM.
- Aggiungi test unitari dove mancano. Un fix = un commit.

## 8) Robustezza dopo una pausa
Se il processo resta fermo (pausa/sospensione), al risveglio `WATCH_TTL_MIN` fa scadere la watchlist in blocco (è successo la notte del 30/10). Sposta in avanti i tempi `seen` della durata della pausa (rileva il salto dell'orologio nel loop). Test con orologio finto.

## 9) Calibrazione di `p_prior` con i nostri dati
`p_prior` è addestrato su Slinky21 (giu–lug): ordina bene (AUC 0,70) ma le probabilità non sono calibrate. Scrivi uno script offline che lo ricalibra (isotonica o Platt) sulle righe shadow/trade valide (`valido=1`, escludi `invalid_for_learning`), con split temporale. Output `model_prior_calibrated.json` + report AUC/Brier. **Nessun** peso sullo score: solo il file e il report.

## 10) Anti-dump dopo la migrazione — PRIORITÀ MASSIMA
Dati di Mark (`/workspace/desk_backtest_hist/results.md` §12, OOS ottobre, 790 migrazioni): la strategia perde ~−20%/trade; il 44–52% dei trade crolla ≤−50% in una sola candela 1m, di solito 5–15 min dopo la migrazione; ~70% delle uscite sono crash exit (76–80% delle perdite).
- Aggiungi in shadow il campo `min_since_migration` all'ingresso e un gate in shadow `ENTRY_MIN_AGE_MIN` (test 10/15/20 min): registra cosa sarebbe successo entrando solo dopo l'età minima (riusa i prezzi già letti, zero chiamate extra).
- Script offline che, sulle righe shadow/trade valide, misura per i crash ≤−50%: concentrazione holder, rapporto buy/sell, `sniper_pct`, `first20_sold_pct`, `dev_first_sell_dt`, `serial_launcher`, `discord_call`, e trova le soglie che bloccano più crash perdendo meno vincenti (controlli positivi in `docs/kiwi_controls.md` mai bloccati, lezioni negative sempre bloccate).
- Output: report + proposta di soglie in `overrides.example.json`. Nessuna soglia attiva senza review.

## 11) Campionamento più ampio degli scarti in shadow — priorità alta (dopo il 10)
Per accelerare il learning: oggi solo una parte dei token scartati viene seguita in shadow, quindi abbiamo pochi esempi etichettati.
- Flag `SHADOW_REJECT_SAMPLE` (frazione 0–1, default 0.5) e `SHADOW_MAX_OPEN` (tetto alle posizioni shadow aperte, default 60) per seguire in shadow anche una quota casuale (seed registrato) dei token scartati da prefiltro/filtri (escludi honeypot, freeze/mint authority, Token-2022 pausable: quelli restano scarto duro senza shadow).
- Ogni riga ha `shadow_reason="sample"` + le chiavi di scarto, così il learning distingue "scartato ma seguito" da "candidato vero".
- Zero chiamate extra a Helius/Birdeye: riusa i prezzi già letti (PumpPortal/DexScreener) e rispetta i limiti gratuiti; se il budget richieste supera l'80% riduci il campionamento da solo.
- Test: carico con 200 scarti finti → nessun superamento del tetto, RAM stabile.

## 12) Ri-etichettatura notturna di tutti i token visti — priorità alta (dopo il 10)
- Script offline `tools/retro_label.py` (cron sul server alle 04:00 Rome, `nice`), che per ogni mint visto nelle ultime 24–48 h (watchlist, scartati, shadow, trade) prende le candele 1m da GeckoTerminal (rispetta il rate limit, cache su disco, riprendibile) e assegna l'etichetta con le convenzioni del bot: win = TP1 +30% prima di SL −12%; rug = ≤10% dell'entrata entro 24 h (anche dopo TP1, e allora **non** è positivo); loss; unknown.
- Output `data/retro_labels.jsonl` (idempotente) con le feature viste al momento + etichetta; nessun token rug o segnalato come cattivo è mai esempio positivo.
- Il learning lo legge solo come dati in più (flag `RETRO_LABELS_IN_LEARNING`, default off): il reviewer lo attiva dopo il controllo.
- Nota: quanti mint al giorno, tempo di esecuzione, richieste usate.

## 13) Errori trovati ma non corretti — priorità alta (dopo il 10)
Dalle tue note (issue #2, #5 e note dei branch). Un fix = un commit, in shadow o dietro flag dove cambia decisioni; controlli positivi mai bloccati, lezioni negative sempre bloccate.
- **Alta:** `CLUSTER_UNKNOWN_ACTION` = "soft" compra con cluster non verificabile → default `skip` con shadow (aggiorna `test_v82` §6).
- `MIN_HOLDERS` saltato se manca `totalHolders` (`if total_holders and ...`) anche con RugCheck su → campo mancante = solo shadow, o holder da cluster v2/GoPlus.
- `decimals` mancante in RugCheck: top10/insider/creatore a ~0% → usa `pct` o scarta.
- LP: valuta solo il mercato `m["pair"]`; senza dati = `lp_unknown`. `pump_migrated` salta il controllo LP solo se la pool è `pump_fun_amm`/PumpSwap.
- Import separati: honeypot e cluster v2 non devono spegnersi in silenzio se fallisce un altro import; controllo attivo + modulo assente = niente acquisti.
- Lista nera creatore solo per rug confermato, con motivo, data e scadenza.
- Feature E vuota sui migrati (`STX_PAGES`=3, >300 scambi = "incompleto"): sfoglia dalla fine o usa un'altra fonte dei primi scambi, così il compito 10 ha `sniper_pct` & co.
- Ripiego RugCheck: owner account di programma escluso dal top10 anche se non è un locker; quota creatore non calcolata; verifica unità di `currentHolding` di `/insiders/networks` appena RugCheck è raggiungibile (sul server).
- Bassa (un commit unico se piccoli): punti 11-18 della issue #2 (campi mancanti che passano i filtri, GoPlus vuoto = unknown, cache errori di `creator_history`, log dei ricontrolli saltati, liquidità su "alive", decimali di ripiego 6 per vanity, `lp_gate` senza `lp_status`, codice morto `sec.get("rugged")`).
- Non toccare: la parte live (issue #1, limiti in SOL) — il live non è approvato.
- Prima di iniziare: risolvi il conflitto tra i branch 6 e 10 sul loop `scout_pumpportal` (riga `mig_note` dentro il nuovo loop con `recv()`), in un branch `claude/merge-6-10`.

## 14) Watchlist più intelligente — priorità alta (piccolo, puoi farlo per primo)
Dati 31/10: watchlist delle fonti secondarie piena da ~12:30 (`WATCH_NEW_MAX` 1200, `WATCH_TTL_MIN` 120), ~450 sfratti "watchlist piena"/ora. `add_watch` sfratta sempre il **più vecchio** non prioritario, anche se si sta muovendo, mentre tiene pool appena nate già morte.
- **Sfratto per qualità:** prima i token "morti" (nessuno scambio/volume dall'ultimo batch DexScreener, liquidità < `MIN_LIQ`, prezzo fermo), poi il più vecchio. Usa solo i dati già letti dal Filter (salva in `st.watch[mint]` gli ultimi `liq`, `vol_m5`, `txns_m5`, `last_seen_active`), zero chiamate extra.
- **Filtro all'ingresso** (flag `WATCH_ENTRY_GATE` = `shadow|on|off`, default `shadow`): per le fonti "nuova pool"/pump.fun, se al primo dato disponibile la liquidità è ~0 o non ci sono acquisti, non entra (in shadow: entra lo stesso ma registra `would_skip_entry`).
- **Log degli sfratti:** evento con età in watchlist, ultima liq/volume, motivo (`dead` | `oldest`). Un campione (es. 10%) degli sfrattati `oldest` va seguito in shadow (`shadow_reason="evicted"`) per misurare se perdiamo token buoni.
- Mai sfrattare `prio` (trending) né token con call Discord recente (`discord_call`).
- Test: watchlist piena con mix morti/vivi → escono prima i morti; nessuna chiamata di rete in più; RAM stabile con 2000 token.
- Nota: sfratti per tipo prima/dopo su un replay e quanti sfrattati `oldest` avrebbero fatto TP1.
