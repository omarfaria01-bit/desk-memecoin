# CLAUDE.md — Desk Memecoin (istruzioni per le sessioni di Claude Code)

## Cos'è
Desk Memecoin: bot di trading di memecoin Solana. Gira **solo in PAPER** (simula il live, nessun ordine reale).
La modalità live **non va mai attivata** senza l'ok esplicito di Kiwi (il proprietario).

## Regole fisse
- Nessuna chiave API, nessun `.env`, nessun wallet o chiave privata nel codice, nei log, negli export o su GitHub.
  Le chiavi arrivano solo da variabili d'ambiente. Su GitHub c'è solo `.env.example`.
- Le soglie non si cambiano a mano nel codice: vanno in `overrides.json` (esempio: `overrides.example.json`),
  che il bot legge con limiti (clamp) per ogni chiave.
- **Claude può correggere errori gravi** con commit piccoli e spiegati su branch `claude/*`, mai su `main`, senza merge. Non riavvia il bot e non lancia la sync: il deploy lo fa il reviewer dopo i test. Compiti in `docs/claude_tasks.md`.
  Kiwi (l'assistente che gestisce il bot) le verifica sui dati del bot e sui backtest prima di applicarle.
- Ogni proposta dice: quale regola, quale soglia, su quali dati è stata provata, e cosa succede ai controlli
  positivi e alle lezioni negative.

## Dove sono le cose
- `desk.py`: il bot (Scout, Filter, Analyst, Executor, Learner). `CFG` in testa al file.
- `sources/`: moduli esterni (cluster_v2, goplus, honeypot, helius, birdeye, momentum, trending, notify_telegram...).
- `tests/`: test (`python3 -m pytest tests` o i singoli `test_v*.py`).
- `backtest/`: `exit_bt.py` (uscite, GeckoTerminal), `sl_bt.py` (stop loss), risultati `results_*.md`.
- `CHANGES.md`: storico delle versioni e delle modifiche.
- `docs/claude_knowledge.md`: relazione di Claude sulle memecoin (senza dati personali).
- `docs/claude_knowledge_review.md`: verifica di ogni idea della relazione sui dati del bot, con verdetto.
- `docs/analysis/`: script delle analisi della review (solo lettura dei dati).

## Lezioni negative (mai usarle come esempi puliti)
BEAST, DESSA, PILLSEY, GLITCH, BNB (falso "BNB", mint non ufficiale), HYPERCHAN (rug −99,8%).
Sono token cattivi confermati: servono come casi da scartare nei test e nelle calibrazioni,
mai come esempi di token buoni o come "vincenti di controllo".
