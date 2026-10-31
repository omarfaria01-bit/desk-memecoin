# Decisioni

Torna a [[Home]] · Stato attuale: [[Stato del bot]]

Orari in ora di Roma. Dettagli tecnici in `CHANGES.md`.

## 30/10/2026
- v4–v6: narrative da X, paper che simula il live, segnali soft graduati, shadow trade e Learner.
- v7–v8.2: cluster di wallet, qualità progetto (review di Mark), stop/TP dinamici **in shadow**, controllo honeypot. Casi BEAST e DESSA → [[Token cattivi e lezioni]].
- v8.4: Birdeye, Telegram, GoPlus, cluster PILLSEY/GLITCH.

## Notte 30–31/10
- **Nessun trade**: il computer dove girava il bot è andato in pausa 14 volte (00:14–06:50, ~5,5 h). I token osservati scadevano a ogni ripresa. Nessun bug né kill switch. Dati di quella notte esclusi (`valido=0`).
- v8.4c: BNB falso → honeypot sconosciuto = niente acquisto.

## 31/10/2026
- v8.4e: HYPERCHAN rug; LP 0% = solo shadow (decisione Kiwi 07:07); storico creatore.
- v8.5 / v8.5c: cluster non verificabile = niente acquisto; ripiego quando RugCheck è giù.
- **TP1_FRACTION 0.5**: al +30% si vende metà.
- **08:53 migrazione su server Hetzner**: il bot gira 24/7, niente più pause.
- 09:29 dashboard web a basso consumo (−98,5% dati), stesse decisioni del bot.
- **v8.5f** (09:32): ripiego RugCheck più severo (insider non letti, holder minimi, creatore in lista nera); regola "famiglie soft" solo shadow.
- **v8.5g**: avvisi Telegram senza doppioni dopo un riavvio.
- **In corso**: fix "liquidità crollata" = −100% e righe con PnL impossibili escluse dall'apprendimento (segnalato da ticher).
- Stop/TP dinamici restano in shadow fino a ~150 trade.
