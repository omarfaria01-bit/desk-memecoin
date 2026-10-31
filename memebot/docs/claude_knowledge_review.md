# Verifica della relazione di Claude sui dati del bot

31/10/2026 07:20 (ora di Roma). Solo analisi: desk.py, overrides.json e il bot non sono stati toccati.
Relazione: `docs/claude_knowledge.md`. Script: `docs/analysis/ck_shadow.py`, `docs/analysis/ck_backtest.py` (output in `ck_backtest_out.md`).

## Dati usati e limiti
- **Shadow del bot:** 65 trade shadow validi con prezzo e liquidità d'ingresso, di cui 60 su mint pump; 52 hanno un `price_path` utilizzabile.
- **Trade veri (paper):** 4, cioè HYPERCHAN (+6,4%), CATE (−1,2%), DESSA (+1,9%) e BNB (+0,5%). Sono troppo pochi per qualsiasi statistica.
- **Backtest GeckoTerminal:** 43 trade survivor (ingressi tra il 28/10 23:25 e il 30/10 15:20, ora di Roma) e 3 trade newpools.
  Il campione survivor ha un bias di sopravvivenza.
- **Simulatore:** applica le uscite di oggi, cioè SL −12, crollo −15, TP1 +30 (vende il 30%) con stop a pareggio +3, TP2 +80 (vende il 30%),
  trailing da +30 al 15% (8% sopra +150) e hold di 180 minuti. Il time stop è a 240 minuti, quindi spento.
  - Sul survivor riproduce il risultato B-live di Mark: +6,5% per trade.
  - Sui percorsi shadow dà −4,6% contro −7,1% registrato dal bot: è un po' ottimista, perché ci sono buchi nei tick e alcuni trade sono stati chiusi con regole precedenti.
- **Chiamate esterne:** solo API gratuite, una chiamata ciascuna (GeckoTerminal SOL 15m, CoinGecko BTC 1h, DexScreener, GoPlus).
  **Zero crediti Helius e Birdeye**, quindi helius_usage.json e birdeye_usage.json non sono cambiati.
- **Limite generale:** sono circa 10 ore di dati del bot e circa 40 ore di survivor. Ogni conclusione è provvisoria.

## Riepilogo
| # | Idea di Claude | Verdetto | Numero chiave |
|---|---|---|---|
| a | Pool sottile rispetto alla supply | solo shadow | pool sotto il 3% della supply: n=5, medio −11,6% ma mediana +1,3%; sopra il 3%: medio −7,4%. Nessun segnale |
| b | Conferma su candela chiusa e non sull'ombra | solo shadow | ombra +6,6% contro chiusura +6,1% (DD −120 contro −83); le conferme più severe peggiorano (+5,0/+5,3%) |
| c | Niente lama che cade e niente ingressi a metà range | **applicare** (ritorno 5 min) e shadow (range) | shadow con ritorno 5 min < 0: n=21, medio −18,8%, win 10%; con ritorno ≥ 0: n=28, +2,9%, win 36%. Survivor: −2,8% contro +6,0% |
| d | Regime di mercato (BTC/SOL, crisi) | solo shadow | finestra del panico Binance: n=26, −1,3% contro +13,4% fuori; ma con SOL 4h ≤ −2% si ha +9,3%. Risultati contraddittori |
| e | Market cap che cresce per aumento della supply | già coperto, più una guardia a costo zero | mint authority attiva = scarto; i mint pump hanno supply fissa di 1 miliardo; 0 casi |
| f | Escludere pool, curva, dead address, locker ed exchange | **applicare** (2 buchi) | top10 senza dead address statico né exchange; cluster v2 salta ogni conto con almeno il 10% della supply |
| g | GoPlus come indizio e non come verdetto | **applicare** (bug) | 1 solo scarto GoPlus (REMARC), ma con la tassa letta come 1e+15% invece dell'1% |
| h | Riprendere il capitale a 3-5x o lasciare una moonbag | solo shadow | capitale a 3x: +3,5 punti sugli shadow (solo grazie a BEAST) e −1,1 sul survivor; moonbag: −0,5 sugli shadow e +2,4 sul survivor |
| i | Sell the news ed eventi | scartare (non automatizzabile) | nessuna fonte di calendario eventi per i memecoin |

---

## (a) Pool sottile rispetto alla supply
**Claude:** su GANG la pool conteneva il 2,9% della supply. Con il prodotto costante, un wallet con l'1% che vende tutto fa scendere il prezzo di circa il 45%; uno con il 5%, di circa l'85%.

**Dati:**
- Ho stimato la quota della pool come f = (liquidità/2) / (prezzo × 10⁹), sui 60 shadow con mint pump.
- L'impatto di una vendita pari a una quota s della supply è 1 − (f/(f+s))².

| Quota della pool sulla supply | n | Medio | Mediana | Win | Rug | Impatto di una vendita dell'1% |
|---|---|---|---|---|---|---|
| < 2% | 2 | −42,6% | −42,6% | 50% | 1 (CHIPCR) | 86% |
| 2–4% | 3 | +9,0% | +1,3% | 67% | 0 | 51% |
| 4–6% | 11 | −10,3% | −13,9% | 9% | 0 | 31% |
| 6–10% | 13 | +24,6% | −3,7% | 46% | 1 | 22% |
| ≥ 10% | 31 | −19,9% | −18,3% | 16% | 4 | 12% |

(La colonna dell'impatto è la mediana. Sotto il 3%: n=5, medio −11,6%, mediana +1,3%, win 60%. Dal 3% in su: n=55, medio −7,4%, mediana −15,2%, win 22%.)

- 5 dei 6 rug su mint pump avevano una pool "normale", tra l'8% e il 20% della supply, cioè graduazioni pump.fun standard. Il sesto è CHIPCR, con lo 0,04%.
- La pool sottile da sola non separa i buoni dai cattivi. Nel nostro universo (token giovani) il caso GANG, cioè un token vecchio con la pool al 2,9%, è raro: solo 7 trade su 60 sono sotto il 4%.
- L'impatto se vendesse tutto il top 10 è sopra il 75% quasi ovunque, quindi non discrimina.
- Il top 1 e il top 3 all'ingresso non sono registrati, quindi la versione di Claude (vende il top holder) non si può testare ora.

**Osservazione a margine, da passare al worker delle quote uniformi:** 6 rug su 7 (BEAST ×2, HYPE, JOLLY, CHIPCR, TOT) avevano il top 10 (pool esclusa) tra il 4% e il 15%: in quella fascia ci sono 6 rug su 11 trade. Invece c'è 1 rug su 39 dal 15% in su (TREND, non pump) e 0 su 13 sotto il 4% (medio −6,7%). Tutte le lezioni negative "a sciame" (HYPERCHAN, BNB, PILLSEY, DESSA) hanno il top 10 sotto il 3,5%. Il campione è piccolo, quindi è solo un indizio.

**Verdetto:** solo shadow.

**Modifica proposta** (zero crediti: i dati arrivano già da RugCheck in `sec.rc_holders`):
- Registrare in `entry_features` i campi `pool_supply_pct`, `top1_pct`, `top3_pct`, `imp_top1_pct` e `imp_top3_pct`, con impatto = 1 − (f/(f+s))².
- Aggiungere lo shadow tag `thin_pool` quando `imp_top1_pct` ≥ 50% oppure `imp_top3_pct` ≥ 75%.
- Rivalutare con almeno 30 trade taggati. Se il tag si conferma, diventa una penalità soft (−0,05) e uno scarto solo sopra `imp_top1_pct` ≥ 70%.

## (b) Conferma su candela chiusa invece che sull'ombra
**Claude:** un livello conta se la candela chiude oltre, non se lo tocca per un attimo. Il suo errore: comprare su un'ombra verde poi ricaduta.

**Dati** (survivor + newpools, 1m GeckoTerminal; segnale = accelerazione del volume più prezzo sopra il VWAP5, uscite di oggi):

| Ingresso | n | Win | Medio | Mediana | MaxDD (somma) |
|---|---|---|---|---|---|
| basta l'ombra (high > VWAP) | 43 | 49% | +6,6% | −3,4% | −120% |
| candela chiusa (close > VWAP) | 43 | 49% | +6,1% | −3,4% | −83% |
| chiusura forte (verde, ombra alta ≤ 50%) | 43 | 49% | +5,3% | −15,0% | −83% |
| chiusura più conferma della candela dopo | 41 | 46% | +5,0% | −15,0% | −151% |

- La chiusura non migliora il rendimento medio (−0,5 punti), ma riduce il drawdown (−83 contro −120).
- Le conferme più severe arrivano in ritardo e peggiorano.
- Il bot non entra su candele ma su tick DexScreener (chg_m5, acq/vend), più lo stop confermato su due controlli.

**Verdetto:** solo shadow. La regola di Claude è pensata per un trader manuale; sui nostri dati la differenza è dentro il rumore.

**Modifica proposta:**
- Registrare in `entry_features` il campo `wick_entry`: 1 se l'ultima candela 1m chiusa ha un'ombra alta > 50% del range o chiude sotto l'open. Si usano le candele già scaricate per l'ATR, quindi a costo zero.
- Se dopo 30 trade quelli con `wick_entry`=1 rendono almeno 5 punti meno degli altri, diventa una penalità soft.

## (c) Non prendere la lama che cade, non comprare a metà range
**Claude:** aspettare un recupero del livello o un picco di volume con ombra lunga in basso. Comprare a metà range dà il peggior rapporto rischio/rendimento.

**Dati del bot** (shadow con mint pump, `ret_5m` all'ingresso):

| ret_5m all'ingresso | n | Medio | Trade vincenti | Quali |
|---|---|---|---|---|
| < −8% | 12 | −22,4% | 1 | Bonnie |
| da −8% a 0 | 9 | −14,1% | 1 | PINGU |
| da 0 a +5% | 8 | −0,4% | 3 | AgenC, Leverhedge, DESSA |
| ≥ +5% | 20 | +4,2% | 7 | iCoin ×2, AAPLCAT, PUMPCAT, BNB, AAPL, BEAST (3 rug in questa fascia) |

- In totale, con ret_5m < 0: n=21, medio −18,8%, win 10%, somma −395%. Con ret_5m ≥ 0: n=28, +2,9%, win 36%, somma +80%.
- **Survivor:** ritorno 5 min < 0: n=5, −2,8%; ≥ 0: n=41, +6,0%. Lama, cioè −10% negli ultimi 15 min: n=5, −0,5% contro +5,7%. La direzione è la stessa.
- **Posizione nel range di 30 minuti** (survivor): basso n=4, +17,5%; metà n=6, −5,7%; alto n=36, +5,5%. La direzione dà ragione a Claude, ma il campione è minuscolo.
- **Gate di oggi:** l'Analyst scarta solo con `chg_m5` < −8% (`MIN_CHANGE_M5_PCT`). Restano aperti gli ingressi tra −8% e 0, che negli shadow rendono −14,1% (1 vincente su 9).
- **Costo per il controllo positivo:** Bonnie ha vinto una volta (+44,5%) con ret_5m −10,4%, ma ha perso le altre due entrate con ret_5m negativo (−22,6% e −17,5%).

**Verdetto:** applicare la soglia sul ritorno a 5 minuti; per il range solo shadow.

**Modifica proposta:**
1. **Subito:** `MIN_CHANGE_M5_PCT` da −8 a **0**, via overrides con clamp, per esempio [−8, +5]. Niente acquisto se il prezzo a 5 minuti è in calo.
   - Il gate deve usare lo stesso dato registrato in `ret_5m` (candele se presenti, altrimenti chg_m5), così il Learner valuta quello che filtra.
   - Nessuna eccezione da bonus KOL, narrativa o trending.
2. **Shadow:** registrare `range_pos_30m` = (prezzo − minimo 30m) / (massimo 30m − minimo 30m) e `ret_15m`. Tag `mid_range` se è tra 0,33 e 0,66, tag `falling_knife` se `ret_15m` ≤ −10%. Si decide con almeno 30 casi per gruppo.

## (d) Correlazione e regime di mercato
**Claude:** se BTC sale e il token scende, il problema è il token. L'hack di Binance del 29/10 (BTC fino a circa 78,3k) ha colpito tutto il mercato.

**Dati** (survivor + newpools, SOL a 15 minuti da GeckoTerminal, BTC orario da CoinGecko):
- **Panico** (finestra del 29/10 19:00 – 30/10 13:00, ora di Roma, con BTC da circa 80,8k a circa 77,3k): n=26, win 38%, medio −1,3%. Fuori dalla finestra: n=20, win 55%, +13,4%.
- **SOL nelle 4 ore prima:** ≤ −2%: n=18, +9,3%; tra −2% e +2%: n=23, +1,8%; ≥ +2%: n=5, +4,5%.
- **BTC nelle 4 ore prima:** ≤ −1,5%: n=15, +7,8%; sopra: n=31, +3,8%.
- **Il contrasto:** la finestra del panico peggiora molto i risultati, ma i filtri meccanici su SOL o BTC a 4 ore non lo catturano, anzi danno il segno opposto. La finestra l'ho scelta a posteriori, quindi c'è un rischio di bias.
- **Shadow del bot:** "token in calo a 5 minuti mentre SOL sale a 1 ora" dà n=20, −18,0%, ma il caso "SOL giù" ha solo 2 trade e non c'è confronto. Durante il periodo del bot SOL a 4 ore è rimasto tra −0,9% e +1,8%, cioè nessun regime estremo osservato.

**Verdetto:** solo shadow. L'idea è sensata, ma i dati non dicono ancora quale misura usare.

**Modifica proposta:**
- Registrare `btc_1h`, `btc_4h`, `btc_24h`, `sol_1h` e `sol_4h` a ogni ingresso, shadow e veri.
  - Fonti: CoinGecko gratis (1 chiamata ogni 15 minuti, in cache) e il pool SOL/USDC di GeckoTerminal. Zero crediti Helius e Birdeye.
- Tag `risk_off` se BTC 4h ≤ −3% oppure BTC 24h ≤ −5%. Proposta futura: size ×0,5 e soglia del punteggio +0,05.
- Tag `rel_weak` se il token è in calo mentre SOL a 1 ora sale di almeno l'1%.
- Il "mercato in crisi" per notizie (hack) non è rilevabile in automatico: resta il tag di prezzo più una pausa manuale di Kiwi.

## (e) Market cap che cresce per aumento della supply
**Claude:** su $JAGENT la capitalizzazione saliva perché si mintavano nuovi token: bisogna guardare il prezzo per token.

**Dati e codice:**
- Il bot calcola PnL, stop e TP sul **prezzo**, non sulla capitalizzazione.
- La mint authority attiva è uno scarto (RugCheck più GoPlus/honeypot), quindi la supply non può crescere. I mint pump.fun hanno una supply fissa di 10⁹ (60 shadow su 65).
- La capitalizzazione si usa solo nella regola sull'età (vol 1h/mcap ≥ 3%) e nel prefiltro (`PF_MAX_MCAP`).
- Casi osservati: 0.

**Verdetto:** già coperto.

**Modifica proposta a costo zero:** salvare la `supply` all'ingresso (dal report RugCheck) e ricontrollarla nel recheck delle posizioni ogni 20 minuti, dove RugCheck viene già chiamato. Se la supply sale di più dello 0,1%, uscita immediata e etichetta `bad`. Priorità bassa.

## (f) Esclusione di pool, curva, dead address, locker ed exchange dai calcoli degli holder
**Claude:** vanno esclusi pool, curva, indirizzo morto, locker ed exchange. Il "phishing 38,92%" di GMGN su GANG includeva gli exchange.

**Cosa fa oggi il bot** (letto nel codice):
- **Filter (top10, quote identiche, creatore, insider):** esclude gli indirizzi dei `markets` RugCheck (pool, LP, vault) e i `knownAccounts` di tipo AMM, LOCKER, BURN e PROGRAM.
  - Non esclude un elenco statico del dead address o dell'incinerator `1nc1nerator11111111111111111111111111111111`, se RugCheck non lo etichetta BURN.
  - Non esclude gli hot wallet degli exchange: l'elenco esiste in `cluster_v2` ma il Filter non lo usa.
- **Cluster v2:** esclude `exclude` e **qualsiasi conto con almeno il 10% della supply**, perché lo considera una pool.
  - È un buco: una balena vera con il 10% o più sparisce dall'analisi dei cluster e del finanziatore comune.
  - Il top10 del Filter la vede comunque, ma il gruppo collegato a cui appartiene risulta più piccolo.
- **Exchange:** nel cluster sono già separati come INFRA (`cluster_infra_pct`), quindi va bene.
- **Phishing:** il bot non usa la percentuale "phishing" di GMGN come regola (solo la regola CEX-fresh di cluster_v2). L'errore di GANG non può ripetersi.
- **Caso dei dati:** PUMP, scartato per "top 10 al 60,5%", con ogni probabilità include wallet di exchange o custodia. Nello shadow ha fatto +0,9% in 180 minuti, quindi un falso positivo innocuo (lo scarta anche il prefiltro sull'età).

**Verdetto:** applicare. È una correzione senza effetto sui crediti.

**Modifica proposta:**
1. Il Filter aggiunge a `exclude` un elenco statico: incinerator e dead address noti, più gli hot wallet degli exchange di `cluster_v2` (stesso file, una sola fonte).
   - La quota degli exchange va mostrata a parte (`cex_pct`) e non nel top10.
2. In `cluster_v2`, "≥ 10% = pool" va sostituito con "escluso solo se è in `exclude`, oppure ha come owner un programma (PDA, conto fuori dalla curva ed25519)". Una balena con un wallet normale resta nell'analisi.
3. Test di regressione: le 6 lezioni negative e Bonnie devono dare lo stesso verdetto di prima.

## (g) GoPlus e avvisi automatici come indizi, non verdetti
**Claude:** GoPlus, GMGN e QuickIntel sono indizi. Su GANG "Transfer pausable: Yes" era un falso positivo.

**Dati:**
- In tutti i log c'è **1 solo scarto GoPlus**: REMARC (FGrGAF…), il 31/10 alle 00:08.
  - Motivo registrato: "fee di trasferimento 1e+15%" più "fee di trasferimento modificabile dal dev" (`transfer_fee_upgradable` con authority attiva).
- La risposta GoPlus (riletta oggi) contiene `transfer_fee.current_fee_rate = {"fee_rate": "0.01", "maximum_fee": "1000000000000000"}`.
  - **Bug in `sources/goplus.py` `_fee_pct`:** la chiave `maximum_fee` contiene "fee", quindi il tetto in unità grezze viene letto come percentuale (1e15%). La tassa vera è l'1%.
  - Con questo bug ogni token Token-2022 con una tassa anche piccola diventa uno scarto rigido "fee ≥ 10%". È proprio il caso che Kiwi ha detto di non scartare (tasse usate per buyback e burn).
- Esito di REMARC (DexScreener, ora): −21% in 6 ore e −48% in 24 ore. Lo scarto è stato giusto, ma per il motivo "fee modificabile" (un potere vero), non per la tassa.
- Gli altri controlli GoPlus rigidi (mint, freeze, saldi modificabili, transfer hook, account congelati, non trasferibile, fee modificabile) sono "poteri veri", in linea con la distinzione di Claude. Nessuno ha scartato token poi buoni.

**Verdetto:** applicare la correzione del bug e tenere i poteri veri come scarti rigidi.

**Modifica proposta:**
1. In `_fee_pct`, ignorare le chiavi che contengono `max`, `maximum`, `epoch` o `amount`. Leggere `fee_rate` (frazione) e `*_bps` (punti base). Test con il JSON di REMARC: atteso 1%.
2. Tassa fissa < 10% = soft; ≥ 10% = rigido (`HARD_FEE_PCT` resta 10).
3. "Fee modificabile dal dev" resta rigido, salvo un'eccezione futura se l'authority è un multisig o un programma noto del launchpad (da raccogliere in shadow: tag `fee_auth_known`).
4. Registrare in shadow ogni scarto GoPlus o honeypot (oggi non sempre succede) per misurare i falsi positivi.

## (h) Piano d'uscita: capitale a 3-5x o moonbag
**Claude:** decidere l'uscita prima di entrare e riprendere il capitale a 3-5x. Su GANG, a circa 5x, metà del guadagno è sparita in un giorno.

**Dati:**
- Il picco entro 180 minuti, su 95 trade (survivor più bot), supera +100% in 24 casi, +200% (3x) in 12 e +400% (5x) in 4.

| Piano | Percorsi shadow del bot (52) | Survivor (43) | Newpools (3) |
|---|---|---|---|
| attuale (TP1 +30/30%, TP2 +80/30%, trailing 15/8) | −4,6% (win 25%) | +6,5% (win 49%) | −15,0% |
| più TP3 +200 che vende il 33% (capitale a 3x) | −1,1% | +5,4% | −15,0% |
| più TP3 +400 che vende il 33% (5x) | −1,4% | +6,5% | −15,0% |
| moonbag 15% senza trailing (fino a hold o rug) | −5,1% | +8,9% | −15,0% |
| moonbag 15% con trailing al 40% | −5,2% | +9,0% | −15,0% |
| solo TP a +200 (50%) più trailing al 25% | −11,8% | +12,6% | −15,0% |

- Il guadagno del "capitale a 3x" sugli shadow (+3,5 punti) viene quasi tutto da **BEAST**: salito oltre +400%, poi rug.
  - Lì vendere un terzo a 3x salva molto.
  - Va però ricordato un limite: nel percorso di BEAST c'è un buco nei tick (da +28% a +422%), quindi l'esecuzione simulata del TP è ottimistica.
- La moonbag funziona sul survivor, che per costruzione non contiene rug, e peggiora sugli shadow, che li contengono: è il bias di sopravvivenza.
- Il piano di oggi incassa già il 60% della posizione a +30 e +80 e stringe il trailing all'8% sopra +150. La regola di Claude è in buona parte già presente.

**Verdetto:** solo shadow. Nessun piano batte quello attuale su entrambi i campioni.

**Modifica proposta:**
- Per ogni trade (vero e shadow), registrare a fianco dei campi `dyn_*` il risultato simulato di due piani alternativi: `alt_tp3x` (TP3 +200 vende il 33%) e `alt_moon15` (15% senza trailing fino all'hold o al crollo di liquidità). I dati sono già nel price path, quindi a costo zero.
- Decidere con almeno 10 trade che arrivano a +200%.
- Se si adotta `alt_tp3x`, sarebbe un solo gradino in più in overrides: `TP3_PCT` 200 e `TP3_FRACTION` 0,33, con clamp [150, 500] e [0,2, 0,5].

## (i) Sell the news ed eventi
**Claude:** il prezzo sale in attesa di un evento (V3, listing) e scende quando arriva. Non comprare in anticipo i catalizzatori incerti.

**Valutazione:**
- Per i memecoin Solana giovani non c'è una fonte strutturata e gratuita di eventi programmati: annunci e listing sono su X e Telegram, in testo libero.
- Il radar KOL e le narrative danno solo un'attenzione generica.
- L'orizzonte del bot (hold di 180 minuti, token di poche ore) rende quasi assenti i casi di "evento annunciato".

**Verdetto:** scartare come regola automatica. Resta una lezione per il trading manuale di Kiwi.

**Unica traccia utile:** se in futuro una tesi KOL o una deep review contiene un evento datato (listing, V2), registrare `event_ts` e taggare `pre_event` o `post_event` in shadow. Oggi non è prioritario.

---

## Da applicare subito (per il worker di desk.py; nessuna modifica fatta qui)
1. **(g) Bug della tassa GoPlus** in `sources/goplus.py` `_fee_pct`: `maximum_fee` letto come percentuale (1e15%). Si legge solo `fee_rate` o `*_bps`, più un test con il JSON di REMARC.
2. **(c) Niente acquisto con il prezzo in calo a 5 minuti:** `MIN_CHANGE_M5_PCT` da −8 a 0 via overrides (clamp [−8, +5]), senza eccezioni.
   - Dati: −14,1% medio sugli shadow tra −8% e 0, e −18,8% su tutti quelli con ret_5m < 0.
   - Costo: 1 vincita su 3 entrate di Bonnie, che ha perso le altre 2.
3. **(f) Esclusioni corrette:**
   - Dead address e incinerator, più gli hot wallet degli exchange, fuori dal top10 del Filter (exchange mostrati a parte).
   - In `cluster_v2` non saltare più i conti con almeno il 10% se non sono pool, locker o programmi noti.

## Solo shadow (campi nuovi, tutti a costo zero o quasi)
- `pool_supply_pct`, `top1_pct`, `top3_pct`, `imp_top1_pct` e `imp_top3_pct`, con il tag `thin_pool` (a)
- `wick_entry` (b)
- `range_pos_30m`, `ret_15m`, con i tag `mid_range` e `falling_knife` (c)
- `btc_1h`, `btc_4h`, `btc_24h`, `sol_1h` e `sol_4h`, con i tag `risk_off` e `rel_weak` (d). CoinGecko e GeckoTerminal gratis, in cache per 15 minuti.
- `supply_entry` più il ricontrollo della supply (e, guardia)
- `alt_tp3x` e `alt_moon15` (h)
- scarti GoPlus e honeypot sempre in shadow, più il tag `fee_auth_known` (g)

Soglia di revisione: almeno 30 trade per gruppo, oppure almeno 10 trade a +200% per (h). Le lezioni negative (BEAST, DESSA, PILLSEY, GLITCH, BNB, HYPERCHAN) vanno sempre ricontrollate, insieme a Bonnie e a un caso sintetico pulito, prima di trasformare un tag in regola.
