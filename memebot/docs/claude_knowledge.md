# Relazione di Claude sulle memecoin (copia senza dati personali)

Fonte: relazione inviata da Kiwi il 31/10/2026. Sezione 8 e posizioni personali rimosse.

```text
Memoria memecoin: tutto quello che abbiamo
imparato
File da leggere insieme a CLAUDE.md. Riassume settimane di analisi fatte su claude.ai con
l'utente (italiano, solo iPhone,
usa fomo, GMGN, DexScreener, Bubblemaps, BscScan, Solscan, Blockscout). Periodo: fine
settembre - fine ottobre 2026.
Le cifre sono quelle viste al momento dell'analisi: oggi possono essere cambiate.




1. Principi generali

Il filtro delle tre domande (per ogni progetto)
 1. Ha qualcosa di unico?
 2. Qualcuno lo usa davvero (lanci, volumi, utenti, commissioni pagate)?
 3. Il token riceve qualcosa dall'uso (commissioni, riacquisti, burn, dividendi)?
    Tre "no" = non comprare. Un buon prodotto con un token che non riceve nulla resta un
    cattivo investimento (Borro, Socket, Prix, Scrooge).


Distinguere progetto e token
    Molti progetti seri non hanno token (Synthra, Rumi Labs, motion, Quoro, LittleBit): niente
    da comprare.
    "Token prima del prodotto" e un segnale negativo ricorrente (Cellumo, Wired, Noomie,
    Parabol, x7 con burn non attivo).
    Un meccanismo scritto nel contratto vale piu di una promessa ("il 50% delle fee lo dono
    come voglio" e una promessa, non un meccanismo).


Valutare un token di piattaforma
    Metodo usato: riacquisti/burn annuali stimati x 5-20 = capitalizzazione ragionevole.
    Esempio GANG a 4,9M: servivano circa 250.000-1.000.000 $ di burn l'anno, cioe 700-
    2.700 $ al giorno costanti.
    I burn rallentano la discesa ma non creano domanda: conta se il volume dagli altri lanci
    cresce (lezione Send It).
Segnali da ignorare
   Un nome famoso che segue, mette like o un emoji (Vlad, Zooko, Yi He). "Robinhood
   Army" e un account satirico della community, non Robinhood.
   KOL tra gli holder: indica attenzione, non qualita, e aumenta la dipendenza da poche
   persone (PonsVault con unipcs, iruletrenches, xbtpika).
   "Se CZ lo supporta fa x100", obiettivi di prezzo senza calcoli.
   Le guerre tra versioni dello stesso meme ("l'altro e la truffa, il vero e il mio"): chi lo scrive
   ha soldi sul suo (VRAX).
   Pool aperte da sconosciuti con nomi simili.


Limiti degli strumenti (da ricordare)
   Molti siti caricano tutto via JavaScript: una pagina "vuota" non vuol dire zero lanci
   (errore commesso su appshare/Hooked).
   X blocca la lettura automatica: usare ricerche e screenshot dell'utente.
   Blockscout e DexScreener vanno letti tramite screenshot dell'utente.
   Gli avvisi automatici (GoPlus, GMGN, QuickIntel) sono indizi, non verdetti: verificare sul
   contratto.




2. Checklist di sicurezza del contratto

EVM (BscScan, Blockscout)
   Codice verificato: "Exact Match" e piu forte di "Similar Match".
   Read Contract: owner (assente o indirizzo morto = bene), paused / locked , indirizzi
   speciali ( launchpad , minter , deployer ).
   Write Contract: cercare mint , pause , setLocked / lock , blacklist , setTax / setFee ,
   withdraw , rescue , sweep ,
   setPoolBlocked , burnFromPair , upgradeTo . Contano solo se chiamabili da un indirizzo
   privilegiato.
   Proxy aggiornabili: chi ha la chiave di aggiornamento? Multisig con timelock = meglio,
   wallet singolo = rischio.
   LP: token LP all'indirizzo morto o in un locker. Se li tiene un wallet, la liquidita si puo
   ritirare.
   Vesting del team: verificare che sia irrevocabile (Pangea lo ha reso irrevocabile
   passando l'ownership ai dead address).
Solana
   Mint authority e freeze authority revocate.
   Token-2022: transfer hook, tassa di trasferimento.
   Programmi aggiornabili dal wallet del team (Hooked e appshare lo ammettevano nei
   documenti).
   Curva pump.fun: niente LP da bloccare finche non gradua.


Casi di riferimento
   GANG (BNB): GoPlus diceva "Transfer pausable: Yes". Verificato: nessun owner ,
   locked = False , Write Contract solo
   approve/burn/transfer/transferFrom/unlock, nessuna funzione per ribloccare, launchpad
   = ufficiale. Falso positivo.
   $JAGENT (JagentTokenV2): mint , setPoolBlocked , burnFromPair , enableTrading ,
   initialize(minter_) ,
   blocksDisabled = False . burnFromPair puo gonfiare il prezzo togliendo token dalla
   pool; setPoolBlocked puo bloccare
   le vendite. Rischio vero se controllate da un indirizzo privilegiato (da verificare
   deployer / minter e i modificatori).

   BNB Veil: dev first buy max 5% della supply; il lock di 30 giorni e opzionale
   ( createTokenUnlocked lo salta, e si vede on-chain).
   Chop: poteri dell'admin pesanti, contratti non verificati: aspettare multisig e codice
   pubblico.
   Ballast: prima di depositare, controllare che il vault non abbia withdraw / rescue
   riservati all'owner.
   SCOPL: esempio positivo, l'owner non puo prelevare i fondi e la cancellazione degli
   ordini non e mai bloccabile.




3. Distribuzione e bubble map
   Firma della distribuzione coordinata: molti wallet con la stessa identica percentuale.
   Pangea: Ethereum 45 wallet al 1,23%
   (49,02%), Base 75 al 1,50% (29,14%), Solana 43 al 0,54% (17,93%), Robinhood 36 al
   0,47% (17,51%), BSC 15 wallet (25,47%).
   Finanziatore comune: wallet diversi riforniti dalla stessa fonte = stesso soggetto.
   Prima ora di pump.fun: normale vedere sniper e bundle (Vibers); conta se vendono o
   tengono.
  Cluster dichiarati: su Split il cluster del team corrispondeva alla quota dichiarata sul
  sito con i numeri esatti.
  Escludere dal calcolo: pool, curva, indirizzo morto, locker, wallet degli exchange.
  L'etichetta "Phishing wallet 38,92%"
  di GMGN su GANG includeva probabilmente MEXC e KuCoin dopo i listing.
  Il limite dell'1% per wallet su Gang vale solo sulla curva: dopo la graduazione superarlo
  e normale.
  Supply su piu chain: su Pangea totalSupply() sulle EVM era un dato riportato da
  Solana (SupplyReporter); la supply vera
  e localSupply() . Le percentuali di Bubblemaps tra chain diverse non sono
  confrontabili.
  Pool sottile rispetto alla supply: su GANG la pool conteneva solo il 2,9% della supply;
  un wallet con l'1% che vende tutto
  fa crollare il prezzo di circa il 45%, uno con il 5% di circa l'85% (prodotto costante).
  Strumento utile: debundle (screener per Pons con "organic score" 0-100 contro bundle
  e wash trading).




4. Progetti analizzati (verdetto sintetico)

Launchpad e token di piattaforma
  Split ($SPLIT, Robinhood Chain): il miglior progetto visto per qualita/dimensione. Fee
  split immutabili, fondo riacquisti
  che brucia, team dichiarato e mai venduto, ogni dichiarazione verificata on-chain. Il team
  ha implementato in poche ore i
  suggerimenti dell'utente: lista di esenzione dalla tassa anti-sniper opt-in e vesting per i
  Team Buy (contratti nuovi
  verificati "blindati"). Limiti: liquidita minuscola, adozione iniziale. [dettaglio personale rimosso] Esiste uno Split Growth Playbook (PDF) scritto per loro.
  Gang ($GANG, BNB): launchpad solo per Binance Agentic Wallet con Agent ID. Fee 1%
  sulla curva: 80% buyback e burn $GANG,
  20% vault. V3: token con tassa 1-5% e dividendi agli holder (>= 10.000 token), fair mint,
  GangBond (cauzione del creator
  bruciata se vende prima del termine). Buyback burner con owner multisig Safe. Dev
  @0xLazy/@gangdotfun (fondatore di CoinAnk).
  Listing MEXC Innovation Zone (28/10) e KuCoin Alpha. Storia del prezzo: ~2M -> ATH
  9,89M (28/10, "sell the news" sulla V3)
-> 5M -> rotture di 4,5M, 4M, 3M -> minimo ~1,65M durante il panico per l'hack di
Binance -> rimbalzo a ~2,7M.
Verifica fomo "in corso" secondo il dev (fomo potrebbe richiedere >5M MC). Burn:
0,55% della supply nei primi giorni.
Concorrenza: Four.meme ha copiato la modalita Agent ID, Flap ha ID Agent Curve.
Rotazione dei capitali verso $JAGENT.
Pangea: tecnicamente ben fatto (multi-chain), ma "abbastanza inutile" secondo
l'utente: il PvP non nasce dalle chain.
Controversia distribuzione (vedi sezione 3), vendita completa da 7,5M token. Docs
aggiornati: team ~19%, di cui 14,7% in vesting
24 mesi (Team Finance su ETH/RH/BSC, Streamflow su SOL), treasury 4,18%; lock resi
irrevocabili il 24/10. Incoerenza rimasta:
la sezione A.1 diceva "no team allocation". [dettaglio personale rimosso]
Hooked: regole nel token (transfer hook). 1% di ogni scambio alla piattaforma: 85%
buyback e burn $HOOKED, 15% team.
Con la curva standard l'hook viene rimosso alla graduazione. 1.129 token lanciati, 7%
della supply bruciata, integrazione GMGN.
A parita di MC (~2M) migliore di Gang sulla sostanza. Falso positivo di Google Safe
Browsing risolto in circa un'ora.
Froink: fee creator 70% X account / 20% buyback e burn / 10% "secret frog stash"
(quota del team dal nome ironico).
Burn rimasto bloccato ("awaiting review"), 0 burn finalizzati al controllo.
PonsVault: vault su pons v2 (Robinhood Chain) con staking, stake & burn, dividendo in
azioni. Piu solido di Froink.
$LAUNCH (uselaunch.fun): regole cambiate 3 volte in 3 giorni; promessa "no block 0"
smentita (7 wallet nello slot di
creazione), dev buy 2% che vende subito, documenti mai aggiornati. Non comprare.
STM (stonkmarket.net): wrapper su StonkFun, fee in buyback e burn, ma fee custodite
da un wallet gestito e destinatari
social senza consenso.
$FLOAT (floatlaunch.xyz): coin abbinati ad azioni; fee 35% burn / 35% top trader in
USDC / 15% holder / 10% treasury /
5% creator. Rischio wash trading per i premi a volume; chiave di aggiornamento al team.
x7pad: memecoin abbinati ad azioni; wallet dei riacquisti "not set yet" mentre $X7
valeva gia ~356k.
basepad: fork di OpenLaunch con una commissione in piu; contratti sicuri (merito di
OpenLaunch), token non legato ai ricavi.
Circuit vs Hooked: Circuit piu comodo per chi lancia, Hooked piu sano come token.
   Wired Protocol: "Hooks for pump.fun", ma per loro stessa ammissione e un'idea con un
   token.
   Hours.fun: scambi solo in orari di apertura (hook Token-2022). Narrativa simpatica,
   nicchia occupata.
   Pact (BNB): concetto Hooked su BNB, idea gia copiata su GitHub.
   Bags: launchpad affermato su Solana, riferimento per molti cloni.
   Nosh, OTC: tra i piu seri del primo gruppo (documentazione rigorosa, contratti pubblici,
   ricavi dichiarati).
   Pod (orcapod): numeri reali e fee pagate; dubbio su chi controlla la liquidita (NFT di
   posizione Orca).
   Veldora: raccolta di tutte le idee viste in una piattaforma; team reattivo.
   TAOLaunch, Curve (curvebond), cNFTs: troppo poco da valutare.
   Clone e scarti di BNB: pumpbut.fun, themorpheooor.fun (template non compilato),
   fomopad.name, cloni di gang/jager,
   app.fstswap.vip (probabile phishing: dominio non corrispondente).


Token BNB legati agli agenti
   BIBI (Flap): FlapTaxTokenV3, tassa 1% buy/sell fino al 22/10/2027, dividendi 100% agli
   holder, supply 1B fissa, bubble map
   pulita (cluster max 6,37%). GMGN aveva attribuito il creator ad AFeng per errore
   (smentito dal team Flap).
   $JAGENT (jager.live): mint a quote con Agent ID, prezzo da 1 a 8,07 $ per quota in 353
   scaglioni, 4,44M quote, 50M token
   per quota, MAX_SUPPLY ~444.000 miliardi (probabilmente meta per la liquidita), 20% di
   ogni pagamento alla treasury, referral,
   pool aperta a 10.000 quote, contratto migrato due volte ($JAGER -> JAGENT -> JAGENT
   V2). Tasse fisse: SELL_BURN_BPS 100 e
   SELL_KEEP_BPS 100 (2% sulle vendite). La capitalizzazione saliva anche perche
   cresceva la supply mintata (da 9.880 a 35.253
   quote in poche ore): guardare il prezzo per token, non la MC. Pagina llms.txt che
   istruisce gli agenti AI a pagare.
   Giudizio: struttura sfavorevole a chi arriva tardi; truffa non provata.
   4AGENT (Four.meme): bubble map ottima. Niu Lai (Flap): ATH dopo 4 giorni poi lunga
   discesa.


Creator fee girate ai social
   Tipped (TikTok), UsePaid (X Money), TeleMoney (Telegram, GRAM), Backers ("+10%"
   su prestiti via X Money):
   servizi custodial a script, valore perso nella conversione, persone coinvolte senza
   consenso, verifica difficile.
   Gift era il modello piu pulito (claim diretto in SOL).


Agenti AI e influencer AI
   auton: fai giocare i tuoi SOL a un'AI contro altre AI; piattaforma di poche ore.
   SENTIENTS: meglio progettato di auton, brucia il token a ogni trade, ma vuoto all'inizio.
   Unalyn: la versione piu curata dell'idea "solo agenti"; token che per loro ammissione
   non fa nulla.
   synths.lol, Dottieland, Kairo, Prix: Prix onesto e prudente ma token senza motivi per
   salire.
   Higgspad, EGO: influencer AI con sembianze di persone reali ($MRBEAST, $ELON...):
   rischio legale e ban. EGO in fondo.


DeFi e azioni tokenizzate (Robinhood Chain)
   Borro: prestiti su azioni tokenizzate (Morpho, Chainlink), tra i piu seri; token debole
   perche non riceve il successo del prodotto.
   Parabol: power perpetual, matematica corretta, solo testnet. Come infrastruttura Borro
   e piu solido.
   SCOPL: ordini con fondi sempre recuperabili dall'utente. Ballast: ribilanciamento
   portafogli, punti, niente token.
   Opcode: exchange di azioni tokenizzate su Solana; aspettare integrazione con Jupiter.
   Scrooge: aggregatore, token = buono sconto. motion: terminale tipo GMGN, nessun
   token. Quoro: punteggio 1-10 veloce.
   Lobby.cash: carta Visa con 4% di cashback in azioni; audit vero ma emittente della
   carta non chiarito, azienda di una settimana.
   Noomie: prestiti privati, tutto "Pending", token gia in circolazione. Scuro: privacy su RH
   Chain, onesto sui limiti.
   Synthra: DEX senza token ("No token, no nonsense"). PonStrategy: accumula $PONS,
   rendimento legato a PONS.


Privacy
   BNB Veil: privacy pool derivato da 0xbow (Privacy Pools) senza stravolgerlo; nessun
   audit; 1% delle fee al wallet del team
   senza legame con $BVeil; acquisti degli insider invisibili.
   DarkSwap: interfaccia sopra NEAR Intents con +0,40% di fee; documentazione onesta.


Meme puri e altro
   VRAX: nessun token ufficiale di Robinhood (solo un post); il migliore per dati era quello
   di Pons accoppiato a USDG.
   Send It ($SI, Base): meme onesto, solo hype. BUILDER (BNB): storia non confermata
   da fonti ufficiali.
   fly-42: cervello di moscerino simulato che decide i burn; storia ottima, fiducia nel dev.
   Fable: strumento anti-truffa ben fatto; il token aveva un ruolo da chiarire. Socket: AMM
   con hook su Solana, token senza ricavi.
   Cellumo: token in vendita prima che la rete esista. Zevio: sconto del 45% sui modelli AI
   di origine dubbia.
   PFWA: mercato di previsioni con oracoli centralizzati su token minuscoli (manipolabili).
   Minpentai: chi arriva dopo paga chi e arrivato prima. RAFS, Pixelpad, µToken, Rumi,
   Sicey, LittleBit, Collect.rip:
   poco o nulla da investire.
   claimer.network: link sospetto, non collegare il wallet.




5. Lezioni di trading
   Piano di uscita prima di entrare: riprendere il capitale a 3-5x. Esempio GANG: a ~5x meta del guadagno e sparita in un giorno.
   Stop rispettati: spostare lo stop "per vedere se rimbalza" e l'errore piu comune. Su
   GANG lo stop a 0,0045 e stato rotto
   e il prezzo e arrivato a 0,00165.
   Sell the news: il prezzo sale sull'attesa di un evento (V3, listing) e scende quando
   arriva.
   Catalizzatori incerti (verifica fomo, Binance Alpha): non comprare in anticipo; se non
   arrivano, il prezzo scende.
   Rotazione: token con la stessa narrativa competono per gli stessi compratori (Jager
   contro Gang).
   Correlazione: se BTC sale e il token scende, il problema e del token.
   Hack di Binance (29/10/2026): sospetto hack del wallet caldo da ~2,5-3,3 miliardi,
   oltre 2,3 miliardi di stablecoin
   congelati da Tether e Circle, 627M di liquidazioni in un'ora, BTC fino a ~78.300. Colpo
  forte ai token legati a Binance.
  Chiusure, non ombre: un livello conta se la candela chiude oltre, non se lo tocca per un
  attimo. L'utente ha comprato
  su un'ombra verde fino a 0,0068 che poi e ricaduta.
  Non prendere la lama che cade: aspettare un picco di volume con ombra lunga in
  basso, una divergenza RSI, il recupero di un livello.
  Lettura indicatori usata: medie in ordine ribassista + SAR sopra + MACD negativo =
  trend al ribasso; RSI < 30 e J del
  KDJ < 20 = ipervenduto (rimbalzo probabile ma non inversione); prezzo sotto la banda
  inferiore di Bollinger = estremo.
  Range medio: comprare a meta range e il rapporto rischio/rendimento peggiore.




6. Truffe e sicurezza personale
  Dopo ogni hack arrivano finti "supporto" e "rimborsi": mai cliccare link ricevuti, mai
  firmare transazioni di altri.
  Mai condividere la seed phrase. Wallet dedicati per trading e per bot.
  Domini simili a quelli ufficiali (fstswap, claimer.network): verificare sempre il contratto
  dal sito o dall'account X ufficiale.
  Nei server Discord crypto qualsiasi menzione di un altro launchpad viene trattata come
  shill: l'utente e stato bannato due volte.
  Sotto i post con campagne ("lascia il tuo indirizzo BSC") si moltiplicano i truffatori.




7. Come comunicare con i team (cosa ha funzionato)
  Proposte concrete e verificate, in ordine di importanza, in inglese; l'utente preferisce
  testi senza trattini lunghi.
  Offrire aiuto prima di chiedere token; chiedere una volta sola.
  [dettaglio personale rimosso]
  Con Gang il dev ha risposto che la verifica fomo era "in corso"; la domanda su CZ e
  Binance Alpha e rimasta senza risposta
  (normale: nessun team serio anticipa listing). Messaggio di scuse inviato con "non serve
  rispondere".
  Le thesis per fomo vanno in inglese, convincenti ma senza scrivere nulla che il team non
    abbia fatto davvero.




8. [Sezione rimossa: dati personali, non sincronizzare]


9. Cosa e gia nel bot e cosa manca
Gia nel bot (vedi CLAUDE.md): mint/freeze, LP bloccata, top 10 senza pool, wallet a quota
identica, insider, reti di wallet
collegati, creatore con troppa quota o in lista nera, pochi holder, rischi gravi RugCheck,
liquidita minima, indicatori,
social, tendenze, parole chiave ricorrenti, presa di profitto parziale, uscita su crollo di
liquidita, apprendimento.

Da aggiungere o migliorare:

    Distinguere avvisi falsi positivi (pausa a senso unico) da poteri veri (mint libero, blocco
    pool, burn dalla pool, withdraw).
    Finanziatore comune dei top holder (serve un indicizzatore o Bubblemaps).
    Storico del creatore (numero di lanci e percentuale di rug) quando la fonte lo permette.
    Capitalizzazione che cresce per aumento di supply (caso $JAGENT): usare il prezzo per
    token.
    Registrare i dati per un backtest prima di cambiare le regole.

```
