# Stato del bot

Torna a [[Home]] · Regole: [[Regole di Kiwi]] · Storia: [[Decisioni]]

| Voce | Valore |
|---|---|
| Versione | **v8.5g** (31/10/2026) |
| Modalità | **PAPER** (simula il live, nessun soldo vero) |
| Dove gira | server dedicato Hetzner, 24/7 (dal 31/10 08:53) |
| Dashboard | https://desk.linkgox.com (accesso protetto), aggiornamento live a basso consumo |
| Repo | GitHub `desk-memecoin`, sincronizzato ogni notte |

## Uscite attive (valori fissi)
- **Stop loss**: −12%
- **TP1**: +30%, vende il **50%** (`TP1_FRACTION` 0.5)
- **Trailing**: 15%

## In shadow (non decidono nulla)
- **Stop / TP / trailing dinamici** (dalla v8.1): oggi basati solo sulla volatilità. Da aggiungere: narrativa, momentum, qualità.
  - Attivazione solo se battono i fissi di ≥ 2 punti senza peggiorare le perdite, con stop massimo −15%, su ~150 trade.
- Regola cluster "famiglie soft" (`CLUSTER_FAM_MODE`=shadow).
- `score_band` e `buysell_relax`: solo misura.

## Controlli di sicurezza principali
Mint/freeze revocate, LP bloccata/bruciata, cluster di wallet (cluster v2), honeypot + GoPlus, storico creatore pump.fun,
ripiego on-chain quando RugCheck è giù (size ×0.5, più severo). Vedi [[Token cattivi e lezioni]].
