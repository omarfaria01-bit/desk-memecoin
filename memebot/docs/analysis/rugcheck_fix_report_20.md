# Compito 20 — RugCheck giu: diagnosi e fix

## Diagnosi: API o client?

Ho provato a mano (dal terminale, 31/10 pomeriggio) l'endpoint pubblico `GET
https://api.rugcheck.xyz/v1/tokens/{mint}/report`, incluso su mint reali visti dal bot nello snapshot
(GTA6, STONK, Dacity): risponde **200 con dati corretti**, nessuna chiave richiesta, header
`x-rate-limit-limit: 15` / `x-rate-limit-remaining`. Una raffica di 20 richieste senza pause non ha
mai prodotto 429 dalla mia posizione. **L'API di RugCheck, almeno ora, funziona.** Non posso escludere
un guasto reale più corto o un blocco Cloudflare specifico per IP nelle ore in cui Kiwi l'ha osservato,
ma i dati dicono che il problema vero è nel **client**, non in RugCheck:

1. **429 trattato come guasto, senza riprova** (`Http.get`, `desk.py`): su `status == 429` il codice
   dormiva 20 s e restituiva `None`, che `security_check` passava a `rc_health(False)` come un fallimento
   vero. `RC_DOWN_FAILS=5` errori di fila (anche solo rallentamenti di frequenza) bastavano a far scattare
   lo stato "giù" per ore, anche se RugCheck stava solo rispondendo "più piano, riprova".
2. **Il ripiego on-chain (`rc_fallback`) sa calcolare lo stato della LP solo in un caso strettissimo**:
   mint che finisce in "pump" **e** dex esattamente "pumpswap" **e** un parsing byte-a-byte dell'account
   della pool che va a buon fine. Per **tutti gli altri casi** (Raydium, Meteora, qualsiasi token non
   nato o non ancora migrato su pump.fun) `markets` resta senza `lpLockedPct`: `lp_status` finisce
   sempre "unknown", che diventa un dubbio bloccante in `security_check` (mai un acquisto vero, solo
   shadow `rugcheck_down`).

### Numeri (snapshot 31/10, ~18h39 osservate)

Sulle 225 righe shadow: **84 sono `rugcheck_down`**, di cui **82 (98%)** hanno come unico motivo
`"LP non verificata bruciata/bloccata (stato unknown)"` — esattamente il buco del punto 2. Solo **4/84**
mint finiscono in "pump" (la maggioranza non è nemmeno un token pump.fun/PumpSwap): il ripiego attuale
non ha **nessun** modo di verificare la LP per quasi tutti i token che lo attraversano, a prescindere
dal problema di RugCheck. È anche la causa più probabile del cluster di Brilliant non visto (compito 18):
col dubbio LP il Filter scartava prima che l'Analyst arrivasse a usare cluster v2.

## Fix (branch `claude/rugcheck-fix`)

1. **`Http.get`** (`desk.py`): un 429 ora fa un backoff (usa `Retry-After` se c'è, altrimenti 5/10 s
   crescente) e riprova fino a 2 volte prima di arrendersi; stesso backoff su errori 5xx/di rete.
   Un singolo limite di frequenza non conta più come fallimento verso `rc_health`.
2. **Stato LP via GoPlus Solana** (`sources/goplus.py` + `desk.py`): quando RugCheck è giù e il ripiego
   on-chain non ha un `lpLockedPct`, prima di arrendersi a "unknown" si prova
   `GET https://api.gopluslabs.io/api/v1/solana/token_security` (gratis, **nessuna chiave**, provato a
   mano: risponde con `dex[].burn_percent` reale per pool Raydium/Orca). Nuova funzione pura
   `goplus.lp_pct_from_dex()`: il massimo `burn_percent` tra le pool note, **mai un valore se GoPlus non
   ce l'ha** (nessun "ok" per un dato assente). Il resto della logica (soglie `MIN_LP_LOCKED_PCT`,
   `LP_ZERO_MAX_PCT`, size ridotta, blocco se 0%) non cambia: GoPlus è solo una seconda fonte per lo
   stesso numero, usata **solo** quando serve (RugCheck giù **e** il ripiego non ha già un dato), zero
   chiamate extra quando RugCheck è su. Mint/freeze/transfer-fee/rug/top10/cluster restano come prima:
   **nessuna soglia di sicurezza toccata**.
3. Nuovo contatore `rc_down_lp_goplus` (`st.stats`): quante volte GoPlus risolve lo stato LP mentre
   RugCheck è giù, per misurare il "dopo" reale una volta in esecuzione (vedi sotto).

### Prima / dopo

- **Prima**: 82/84 (98%) degli scarti `rugcheck_down` nello snapshot bloccati solo per "LP stato
  unknown"; il ripiego non ha mai un'alternativa per i token non-PumpSwap.
- **Dopo (atteso, da confermare in esecuzione col nuovo contatore)**: ogni volta che GoPlus conosce un
  `burn_percent` per la pool (confermato a mano per STONK: `burn_percent: 0`, coerente con "LP bloccata
  solo al 0%" già visto da RugCheck lo stesso giorno) lo scarto diventa un acquisto vero (se LP ok/
  parziale) o resta bloccato ma con un dato reale invece di "sconosciuto" (se LP è 0%). Non posso
  rilanciare il bot da qui per contare i numeri reali: il contatore `rc_down_lp_goplus` dà a Kiwi il
  confronto diretto dopo un giorno di paper.
- **Non risolto**: se anche GoPlus non ha dati per quella pool (token nuovo/poco seguito), `lp_status`
  resta "unknown" come prima — nessuna regressione, solo meno casi bloccati a vuoto.

## Cosa NON ho fatto (fuori budget/rischio)

- Non ho scritto un parser binario per le pool Raydium (serve la struct `AmmInfo`, non verificabile da
  qui senza un vettore di test ufficiale: un offset sbagliato darebbe un falso "LP ok", rischio di
  sicurezza). GoPlus copre lo stesso bisogno senza quel rischio.
- Non ho toccato `insiderNetworks`/`topHolders` nel ripiego: nello snapshot non sono mai stati la causa
  del blocco (0 occorrenze), quindi non c'era un'evidenza per cambiarli.
- Birdeye/Helius DAS: non aggiunti. GoPlus da solo chiude il 98% dei casi osservati; se servisse ancora
  copertura si può aggiungere come terza fonte con lo stesso schema (prova solo se le altre due non hanno
  un dato).

## Test

`tests/test_rugcheck_fix.py`: `goplus.lp_pct_from_dex` (puro), `security_check` con RugCheck giù su un
mint Raydium (senza dati su nessuna fonte -> ancora bloccato; con GoPlus che conferma LP bruciata -> passa;
con GoPlus che dice 0% -> resta bloccato), `Http.get` con 429-poi-200 (una riprova, nessun fallimento) e
429 ripetuto (si arrende dopo i tentativi). Verde su copia `/tmp`; verde anche `test_rc_fallback_review.py`,
`test_v85b/c.py`, `test_v6/v6_soft.py`, `test_v82.py`, `test_v85e.py` (nessuna regressione sui test
esistenti del ripiego/cluster/bundle). Nessuna delle 6 lezioni negative è usata come esempio buono
(fixture sintetiche, mint finti).
