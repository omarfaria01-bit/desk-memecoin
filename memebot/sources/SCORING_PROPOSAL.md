# Proposta di scoring: nuovi segnali (paper; tutto trasferibile a live senza modifiche)

Tutti i moduli sono in `sources/`. Sono standalone e in sola lettura, non usano dipendenze esterne e non
sollevano mai eccezioni. **desk.py NON è stato modificato**: l'integrazione la fa chi ha desk.py in mano.

## Regola d'oro
I bonus si sommano allo score **solo dopo** che il token ha superato **tutti** i filtri HARD (rugcheck, bundle/cluster,
top10, LP, sell-route, mint/freeze...). Un bonus non può mai salvare un token scartato. I soft flag restano attivi.

## Override keys (overrides.json, con clamp)
| Chiave | Default | Clamp | Segnale |
|---|---|---|---|
| `GMGN_TREND_BONUS` | 0.03 | 0–0.08 | token nel trending Solana di GMGN (`gmgn_trending.py`; il sostituto Gecko prende 0) |
| `FOMO_TREND_BONUS` | 0.03 | 0–0.08 | (opzionale, messo da parte) trending di Fomo, solo con la chiave fomoapi |
| `KOL_BONUS` | 0.03 | 0–0.08 | KOL in whitelist che ha COMPRATO davvero e tiene ancora |
| `KOL_THESIS_BONUS` | 0.02 | 0–0.06 | tesi o post di un KOL in whitelist (da `kol_theses.json` di Radar), più recente di 6 h |
| `MOMENTUM_YOUNG_BONUS` | 0.03 | 0–0.08 | token di meno di 6 h con momentum ≥ 0.5 (bonus = chiave × score) |
| `TOTAL_BONUS_CAP` | 0.10 | 0–0.10 | tetto sulla **somma** dei bonus |
| `KOL_SOLD_PENALTY` | 0.02 | 0–0.05 | penalità se tutti i KOL whitelisted presenti hanno già venduto |
| `KOL_MIN_SOL` | 0.1 | 0.05–1 | SOL minimi spesi perché un KOL "conti" (protezione dagli airdrop spam) |
| `MOMENTUM_MIN_SCORE` | 0.5 | 0.4–0.8 | soglia del punteggio momentum |

Formula: `bonus = clamp(gmgn + fomo + kol + thesis + momentum, -KOL_SOLD_PENALTY, TOTAL_BONUS_CAP)`, poi `score += bonus`.
Il bonus KOL vale `KOL_BONUS × moltiplicatore del tier × weight` (prende il KOL migliore, i bonus non si sommano tra KOL).

## Whitelist KOL (`kol_whitelist.json`)
| Tier | Moltiplicatore | Chi |
|---|---|---|
| **S** | 1.25 × KOL_BONUS (sempre dentro il tetto 0.10) | **unipcs** (Bonk Guy, X @theunipcs), il migliore secondo l'utente |
| **A** | 1.0 × | **pointfarmcap** (point farm capital, scritto dall'utente "poinfarmcapital") |
| **B** | 0.5 × | frankdegods, orangie, ether_monk, tvmx, rowdy (@RowdyCrypto), rasmr (×0.75, "user: less confident") |
| non in lista / `proposed` | 0 | 9 candidati kolscan (PnL 30 giorni, winrate ≥ 55%), **inattivi** finché l'utente non li approva |

- I KOL consigliati dall'utente sono `active` subito.
- Wallet: tutti `verified: false`, perché nessun KOL ha pubblicato il proprio wallet. Il campo `confidence` dice quanto è solida l'attribuzione:
  - **alta**, con più tracker indipendenti che hanno fatto match on-chain sul profilo Fomo: frankdegods, ether_monk, tvmx, rasmr;
  - **medio-alta**: pointfarmcap (2 tracker, match 85%);
  - **media**, sola attribuzione kolscan: unipcs, orangie, rowdy.
- Tutti i wallet risultano attivi on-chain il 30/10/2026.
- **Learner** (`KolLedger`):
  - *Retrocessione*: S → A → B → off. Scatta dopo ≥ 6 esiti con winrate < 35% o PnL medio < −5%.
  - *Promozione*: B → A per i KOL consigliati dall'utente (`promotable`), dopo ≥ 6 esiti con winrate ≥ 55% e media > 0.
  - Il Learner non sale mai oltre A; **S lo decide solo l'utente**.
  - I candidati `proposed` non vengono mai promossi in automatico.

## KOL: stati e tag
- `holding` e acquisto vero (≥ `KOL_MIN_SOL`): bonus. Tag `kol:handle:tier`.
- `sold`, `sold_closed`, `partial_sold` ≥ 50%: nessun bonus. Tag `kol_sold:X`. Se tutti i KOL presenti sono così: −`KOL_SOLD_PENALTY`.
- `empty`: account a saldo 0, senza vendite visibili e mai visto in holding. Tipicamente spam o dust: nessun effetto.
- `kol_noBuy:X`: il KOL ha il token ma nello storico non c'è un acquisto. Di solito è un airdrop: 0 bonus.
- `kol_buyUnknown:X`: come sopra, ma lo storico è campionato (8 tx), quindi l'acquisto può essere più vecchio. Anche qui 0 bonus, per prudenza.
  Test live del 30/10/2026: frankdegods aveva l'1,5% di un pump token, ma l'acquisto non era tra le ultime 8 tx.

## KOL: caveat importante, "exit liquidity"
Spesso il KOL compra presto, lo annuncia e vende **ai follower**. Per questo:
- se il KOL ha venduto (`sold`, `sold_closed`, o `partial_sold` ≥ 50%) → **nessun bonus**;
- se **tutti** i KOL whitelisted presenti hanno venduto → **penalità** `-KOL_SOLD_PENALTY`;
- conta solo un acquisto reale (≥ `KOL_MIN_SOL` SOL spesi). I wallet dei KOL ricevono molti token spam in airdrop
  (lo abbiamo verificato: unipcs ha più di 4800 token account, quasi tutti polvere).
- una tesi con `kind: "exit"` azzera il bonus tesi.

## Tag per il Learner
Va aggiunta una colonna `signals` a `trades.csv` e a `shadow_trades.csv`, come lista separata da `|`. Esempi:
`src:gmgn_trending|gmgn_rank:3|kol:unipcs:S|kol_thesis:theunipcs|momentum_young:0.82|bonus:0.10`
e anche i tag "negativi" (`kol_sold:X`, `kol_noBuy:X`, `microtx_spam`), così si misura pure ciò che NON ha dato bonus.

## Come il Learner regola i bonus (stesse regole del v6)
- Per ogni segnale si confronta il PnL di trade + ombra *con* il segnale contro quelli *senza* (stessa finestra, almeno 15 esiti).
- Al massimo uno step all'ora per chiave; step 0.01 (0.005 per la tesi); sempre dentro i clamp.
- Se il segnale rende meglio → +step. Se rende peggio o è neutro → −step, fino a 0 (il segnale si spegne da solo).
- Il bonus non allenta mai gli scarti HARD.
- **Per KOL** (`KolLedger` in `kol_holders.py`, file `kol_ledger.json`): `record(handle, pnl_pct, mint, shadow)`.
  Dopo ≥ 6 esiti, se winrate < 35% o PnL medio < −5%, il KOL viene **retrocesso** di un gradino (S → A → B → off). Viene ripromosso solo con
  ≥ 6 nuovi esiti con winrate ≥ 55% e media > 0. I KOL B consigliati dall'utente possono salire fino ad A, mai a S. `load_whitelist()` e `tier_of()` usano già il tier effettivo.

## Moduli
| File | Funzione | Fonte | Gratis? |
|---|---|---|---|
| `gmgn_trending.py` | `get_trending/aget_trending` | GMGN OpenAPI ufficiale (chiave pubblica demo) oppure il sostituto GeckoTerminal | sì, senza login (chiave condivisa e revocabile) |
| `fomo_trending.py` | (opzionale) | fomoapi.io (chiave free) oppure il sostituto DexScreener | messo da parte su richiesta dell'utente |
| `kol_holders.py` | `kol_check(mint)`, `kol_bonus`, `KolLedger`, `tier_of`, `weight_of` | RPC pubblica Solana (getProgramAccounts, Token + Token-2022, più lo storico tx dei soli KOL trovati) | sì |
| `kol_theses_reader.py` | `thesis_bonus(mint, tier_of, cfg, weight_of)` | `kol_theses.json`, scritto da Radar (schema in `kol_theses.example.json`) | sì |
| `momentum.py` | `momentum`, `momentum_batch`, `momentum_bonus` | DexScreener (30 mint per chiamata) + attributi pool Gecko opzionali | sì |

Costi di chiamata: `kol_check` fa 3 chiamate RPC per mint, più 1 + 8 per ogni KOL trovato (al massimo 5 KOL), con cache di 10 min.
Va chiamato **solo** sui token che hanno già passato i filtri HARD, non su tutto il flusso. `momentum` fa 1 chiamata ogni 30 mint, con cache di 60 s.

## Limiti onesti
- **KOL venduto e account chiuso**: non è visibile, a meno che il modulo non l'avesse già visto prima (`sold_closed`).
- **Storico tx campionato**: 8 tx per KOL. Su KOL molto attivi su quel token, SOL spesi e venduti sono stime (`truncated: true`).
- **RPC pubblica condivisa col bot**: ci possono essere errori 429. C'è un retry, ma su volumi alti servirebbe una RPC privata gratuita (es. Helius free).
- **getProgramAccounts su token enormi** (> 60k holder) è pesante; il modulo si ferma. Va bene, perché il target sono i token giovani.
- **Buyers unici**: DexScreener non li fornisce. Sono reali solo se si passano gli attributi pool di GeckoTerminal; altrimenti
  si usa un proxy sul numero di buy (peso dimezzato, tag `buyers_proxy`).
- **Liquidità in crescita**: serve almeno uno snapshot di 5 minuti prima (la memoria è nel processo).
- **Lista KOL di kolscan**: è un leaderboard pubblico, ma le attribuzioni wallet→persona sono di kolscan, non verificate da noi.
  Lo snapshot va aggiornato ogni tanto (va bene 1 fetch al giorno di kolscan.io/leaderboard).
