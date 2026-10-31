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

## 1) Review del ripiego "RugCheck giù" — PRIORITÀ MASSIMA
- **Contesto:** dalle 02:09 del 31/10 RugCheck risponde 400 ("unable to generate report") a tutti i token, quindi
  il bot non compra più nulla. Sta arrivando su `main` (a breve, fai `git pull` prima di iniziare) un ripiego:
  se RugCheck è giù si può comprare **a mezza size** solo se **tutti** gli altri controlli sono puliti
  (on-chain, GoPlus, honeypot, cluster_v2, LP, creator history, ticker impostore...).
- **Da fare:** review avversaria. Cerca i casi in cui un token rischioso passa grazie al ripiego: controlli che
  restituiscono "sconosciuto"/errore e vengono letti come puliti, eccezioni silenziose, cache vecchie, LP non
  verificabile, mint/freeze authority non lette, token che RugCheck avrebbe bloccato (bundle, insider, LP) e che
  nessun altro controllo copre, ripiego attivo anche quando RugCheck risponde ma con dati parziali.
- **File:** `desk.py` (Filter/Analyst/Executor, parte RugCheck e il nuovo ripiego), `sources/goplus.py`,
  `sources/honeypot.py`, `sources/cluster_v2.py`, `sources/creator_history.py`, `CHANGES.md`.
- **Fatto quando:** elenco dei buchi trovati con gravità; per ognuno un test che lo riproduce (fixture in `/tmp`
  o mock, nessuna rete obbligatoria) e, se grave, un fix piccolo su `claude/rugcheck-fallback-review`.
  Le 6 lezioni negative, passate dal ripiego con RugCheck simulato giù, devono restare tutte scartate.

## 2) Chiudere il buco BEAST / DESSA nelle regole cluster
- **Contesto:** in `sources/cluster_v2.py` BEAST e DESSA (token cattivi) oggi escono solo **soft**
  (DESSA: wallet nuovi 0.35–0.39, 3 stelle KuCoin; BEAST: 5 stelle/funder, uniformi 4). Vanno resi **hard** senza
  bloccare i controlli positivi Bonnie e Dacity (`nxPqZ4F7ux8Vph5NFu25rEP6Kx9Gr1U6Fmj36Rjpump`; Dacity oggi è soft:
  stella 17 wallet 13.1%, gruppo 16.1%).
- **Da fare:** proporre una regola (o combinazione di segnali soft → hard) con soglie in `CFG`/overrides.
- **File:** `sources/cluster_v2.py` (`analyze`, blocco hard/soft ~righe 490–510), `docs/kiwi_controls.md`,
  dati registrati del bot (copie in `/tmp`), test in `tests/`.
- **Fatto quando:** tabella con esito della regola su: BEAST, DESSA, PILLSEY, GLITCH, BNB falso, HYPERCHAN
  (tutti devono essere **hard**) e su Bonnie, Dacity + gli altri casi di `docs/kiwi_controls.md` (Bonnie e Dacity
  **non** hard; gli altri riportati). Test unitario con fixture. Branch `claude/cluster-beast-dessa`.
  Nessun token cattivo usato come esempio pulito.

## 3) Test unitari con fixture per uniform-share, star, single-funder, impostor-ticker
- **Contesto:** regole v8.4c/e: uniform-share (≥20 wallet entro ±5%, o ≥10 wallet con ≥15% dell'offerta = hard),
  star (≥3 stelle = hard, 1–2 = soft), single-funder (un finanziatore per ≥50% dei wallet controllati, con ≥10
  wallet = hard), ticker impostore (`TICKER_IMPOSTOR_ACTION` = block).
- **File:** `sources/cluster_v2.py` (`uniform_plateau`, `star_clusters`, `single_funder`, `analyze`),
  `desk.py` (`ticker_impostor`), nuovo `tests/test_cluster_rules.py`.
- **Fatto quando:** fixture sintetiche (indirizzi finti, niente rete) che coprono i bordi di ogni soglia
  (appena sotto / esattamente / appena sopra), casi infrastruttura/exchange esclusi, ticker ufficiale vs impostore.
  Tutti i test verdi. Branch `claude/tests-cluster-rules`.

## 4) Prezzo in uscita più robusto (niente −100 falsi)
- **Contesto:** v8.4d ha corretto le righe −100 "nessun prezzo" (marcate `np_invalid`, seconda fonte,
  `MARK_NOROUTE_CONFIRM` = 3 in v8.5). Verificare che **nessun** percorso di uscita (posizione o shadow, SL, trailing,
  timeout, chiusura per rug) scriva −100 o pnl ~0-prezzo quando la fonte prezzo fallisce/timeout/risponde vuoto.
- **File:** `desk.py` (verifica "nessun prezzo" ~righe 1590–1665, chiusura shadow ~5490–5510 e ~5598–5670,
  posizioni ~5219), sorgenti prezzo (`sources/birdeye.py`, `sources/helius.py`, quote Jupiter in `desk.py`).
- **Fatto quando:** test che simula fonte prezzo in errore/None/0 e controlla che il risultato sia "attesa" o
  "dati non validi", mai −100/rug; un rug vero (confermato da due fonti) resta rug. Branch `claude/exit-price-robust`.

## 5) Backtest offline: SL/TP dinamici vs statici
- **Contesto:** gli SL/TP dinamici da ATR sono solo in shadow (colonne `atr_1m_10`, `dyn_stop_shadow`, `dyn_tp1`,
  `dyn_trail_act`, `dyn_trail_dist` in `shadow_trades.csv`). Statici attuali: SL −12, TP1 +30 (vende 30%),
  trailing da +40 con distanza 15.
- **File:** nuovo `backtest/dyn_vs_static.py`, input = **copia** di `shadow_trades.csv` in `/tmp` (sola lettura),
  eventuali candele da GeckoTerminal come in `backtest/exit_bt.py`.
- **Fatto quando:** lo script gira su `/tmp/shadow_trades.csv` e produce `backtest/results_dyn_vs_static.md` con
  n trade, win rate, pnl medio/mediano, peggior trade, per statico vs dinamico; esclude righe `valido=0`
  e le 6 lezioni negative le riporta a parte. Nessuna scrittura sui file reali. Branch `claude/bt-dyn-vs-static`.
