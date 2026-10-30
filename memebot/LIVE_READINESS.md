# Prontezza al live – checklist (v6, 30/10/2026)

Il bot gira **solo in paper** (`DESK_PAPER_ONLY=1`). Questa lista dice cosa è già pronto e cosa manca
prima di usare soldi veri. Ad ogni avvio il bot rifà i controlli automatici e li scrive in
`live_readiness.json` (riga "prontezza al live" nel log).

## Già pronto
- [x] **Esecuzione simulata come in live**: preventivi Jupiter reali alla size vera, latenza 1–3 s,
      slippage massimo 3% (15% dopo 2 vendite fallite), transazioni fallite che pagano la fee, nuovi tentativi.
- [x] **Controllo rivendibilità** prima di ogni acquisto (anti-honeypot): utile identico in live.
- [x] **Priority fee dinamica** dalla rete (getRecentPrioritizationFees), con minimo e tetto `PRIORITY_MAX_LAMPORTS`.
- [x] **Riserva SOL** per le fee (`SOL_RESERVE` 0.03 SOL) e affitto dei conti token considerato.
- [x] **Kill switch**: `touch STOP` vende tutto e blocca gli acquisti (ricontrollato subito prima di ogni invio).
- [x] **Limiti di perdita**: giornaliero −10% (fino a mezzanotte Europe/Rome) e totale −30% (`HARD_STOP`).
- [x] **Limiti di size**: max 5% equity, 1% della pool, `MAX_SOL_PER_TRADE` 0.5 SOL; in live anche
      `LIVE_MAX_TRADE_USD` 5 $ per i primi trade.
- [x] **Codice live esistente** (`SolanaBroker`, mai usato): preventivo → transazione dalla Jupiter Swap API →
      firma locale → invio → attesa conferma (60 s) → verifica dei saldi reali. Vende solo la quantità del bot.
- [x] **Modalità dry**: usa il wallet solo per preventivi e `simulateTransaction`, senza inviare nulla.
- [x] **Log per l'analisi** (ticher): `quote_impact`, `latency_slip`, `prio_fee_sol`, `fill_failed` in
      `trades.csv` ed `events.jsonl`.
- [x] **Filtri graduati + shadow trade** (v6): i segnali soft (bundle, quote identiche, top 10 35–50%, LP 25–50%)
      riducono punteggio e size invece di scartare; gli scartati vengono seguiti come operazioni ipotetiche
      (`shadow_trades.csv`) e il Learner corregge penalità/soglie gravi entro limiti fissi. Gli scarti HARD
      restano invariati e non vengono mai allentati.

## Note v7 (30/10/2026)
- Controllo cluster e `kol_check` usano la RPC pubblica gratuita (limiti condivisi, 429): in live serve una RPC
  privata, altrimenti il controllo cluster non dà verdetto e aggiunge 30–40 s prima dell'acquisto.
- LP sbloccata ora è soft con tenuta massima 60 min: verificare sugli shadow `lp_unlocked` il tasso di rug prima del live.
- Trending GMGN solo con una chiave personale; senza chiave il sostituto gratuito non dà bonus.

## Manca prima del live (in ordine di importanza)
1. [ ] **Wallet dedicato** con pochi SOL (mai il wallet principale), chiave solo in `.env` sul server,
       permessi `chmod 600`, mai condivisa né caricata nello zip.
2. [ ] **RPC dedicato** (Helius / Triton / QuickNode, piano gratuito): l'RPC pubblico va bene per il paper ma
       in live perde transazioni e risponde 429.
3. [ ] **Libreria `solders`** installata (`./venv/bin/pip install solders`) per firmare.
4. [ ] **Conferma e ritentativi robusti**: oggi l'invio aspetta 60 s e riprova al ciclo dopo. Mancano:
       blockhash nuovo ad ogni tentativo, controllo "transazione scaduta", rinvio con fee più alta.
5. [ ] **Jito / invio protetto da MEV** (`LIVE_USE_JITO`, `LIVE_JITO_TIP_SOL`): non implementato. Senza, i
       memecoin vengono spesso "sandwichati".
6. [ ] **Priority fee dinamica anche nell'invio live**: oggi il live usa il livello "high" di Jupiter con
       tetto `PRIORITY_MAX_LAMPORTS`; collegarlo alla stima v6 (normale / urgente nei crolli).
7. [ ] **Chiusura dei conti token** dopo la vendita totale (recupera ~0.002 SOL di affitto per token).
8. [ ] **Dry run** (`MODE=dry`, senza `DESK_PAPER_ONLY`) per almeno qualche ora: verifica preventivi,
       firma e simulazioni on-chain senza inviare. Poi impostare `LIVE_DRY_RUN_DONE: true`.
9. [ ] **Primo live minuscolo**: `LIVE_MAX_TRADE_USD` 5 $, massimo 2–3 posizioni, controllo a mano dei primi trade.
10. [ ] **Monitoraggio e avvisi**: notifica (es. Telegram/email) per acquisti, vendite fallite ripetute,
       `HARD_STOP`, bot fermo; controllo che il processo giri (systemd/watchdog).
11. [ ] **Abbastanza dati paper v6**: almeno 30–50 trade chiusi con i costi realistici e PnL positivo
       dopo le fee, analizzati da ticher.
12. [ ] **Verifica degli allentamenti appresi**: prima del live controllare gli eventi `learn_adjust` e
       `shadow_trades.csv` (almeno 15–30 esiti per segnale); se serve, fissare a mano i valori in `overrides.json`.

## Regola
Il passaggio a live lo decide solo l'utente. Nessun agente può attivarlo, creare wallet o toccare chiavi.
