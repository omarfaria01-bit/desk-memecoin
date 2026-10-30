# Memebot desk – trading simulato (solo PAPER) di memecoin Solana

Il bot simula operazioni su memecoin Solana con soldi finti. **Non ha wallet, chiavi né `.env`**:
non può fare ordini reali.

## Avvio su un server (Linux, Python 3.11 o superiore)
```bash
cd memebot
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
# avvio in background, solo paper:
DESK_PAPER_ONLY=1 DESK_HEADLESS=1 PYTHONUNBUFFERED=1 \
  setsid nohup ./venv/bin/python desk.py --headless > run.log 2>&1 < /dev/null &
tail -f run.log                          # log in diretta
./venv/bin/python export_dashboard.py    # crea dashboard_data.txt con lo stato
touch STOP                               # arresto ordinato (oppure: kill <PID>)
```
- Per vedere la dashboard interattiva nel terminale, avvia `./venv/bin/python desk.py` senza `--headless`.
- Lo stato si salva in `state.json` e viene ripreso al riavvio. Per ripartire da zero, cancellalo.

## File
| File | Contenuto |
|---|---|
| `desk.py` | Il bot: tutti gli agenti in un solo file, con la configurazione `CFG` in alto |
| `export_dashboard.py` | Esporta lo stato (equity, posizioni, ultimi eventi) in testo |
| `config.json` | `{"MODE": "paper"}`: lasciarlo così |
| `overrides.example.json` | Esempio di override esterni. Copialo in `overrides.json` per usarlo; vengono applicati entro limiti di sicurezza (tabella in `CHANGES.md`, sezione O) |
| `narratives.example.json` | Formato del file `narratives.json` scritto dall'assistente Radar (temi caldi su X) |
| `CHANGES.md` | Storico di tutte le modifiche, con i valori originali |
| `LIVE_READINESS.md` | Checklist di cosa c'è e cosa manca prima del live |
| `sources/` | (v7) moduli dei segnali extra: `gmgn_trending.py`, `fomo_trending.py`, `kol_holders.py`, `kol_theses_reader.py`, `momentum.py`, whitelist KOL; documentazione in `SCORING_PROPOSAL.md` e `FOMO_INTEGRATION.md` |
| `tests/` | Test offline con la rete simulata. Ogni test va lanciato in una cartella vuota: `mkdir /tmp/t && cp desk.py config.json tests/test_v6.py /tmp/t && (cd /tmp/t && $OLDPWD/venv/bin/python test_v6.py)` |
| Creati a runtime | `state.json`, `events.jsonl` (eventi degli agenti), `trades.csv` (operazioni chiuse con feature), `shadow_trades.csv` (operazioni ipotetiche sugli scartati), `equity.csv`, `run.log`, `live_readiness.json` (controllo all'avvio) |

## Gli agenti (dentro `desk.py`)
1. **Scout**: segue le nuove pool (GeckoTerminal) e i nuovi token pump.fun (websocket).
2. **Filter**: legge i dati di mercato (DexScreener) e i controlli anti-rug (RugCheck):
   - **scarti HARD** (mai allentati): autorità di mint e freeze non revocate, LP non verificabile o (v7) quasi tutta
     sbloccata e in mano al creatore, rimozione della LP iniziata,
     insider, creatore, pochi holder, rischi gravi RugCheck, liquidità minima, nessun percorso di vendita
   - **segnali SOFT graduati** (v6): bundle (quota delle reti di wallet collegati), wallet con la stessa
     quota, top 10 tra 35% e 50%, LP bloccata tra 25% e 50%. Un segnale soft = −0,05 sul punteggio e size
     ×0,5; oltre la soglia grave (bundle ≥ 25%, quote identiche ≥ 10% insieme, top 10 > 50%, LP < 25%) o con
     3 segnali insieme il token viene scartato (tabella in `CHANGES.md`)
   - v7: LP sbloccata è un segnale soft (`lp_unlocked`, tenuta massima 60 min), non più uno scarto
3. **Analyst**:
   - controlla il momentum: acquisti/vendite in 5 min almeno 1.3, volume 5 min sopra i 5 min precedenti,
     prezzo sopra la VWAP dei 5 min
   - legge il grafico OHLCV e calcola un punteggio
   - applica le narrative di Radar (tra -0.08 e +0.08): bonus per le narrative giovani in crescita,
     penalità per quelle tardive e per le copie di un token già noto
   - toglie la penalità dei segnali soft e decide se il punteggio supera la soglia
   - v7: per i candidati sopra soglia, controllo **cluster di wallet** on-chain (RPC pubblica): soft (−0,08, size ×0,4)
     o, solo nei casi estremi, scarto `cluster`
   - v7: bonus dei segnali extra in `sources/` (trending GMGN con chiave personale, KOL, tesi KOL, momentum giovane),
     tetto 0,10, solo dopo tutti i filtri HARD; colonna `signals` nei CSV
   - **shadow trade** (v6, nessun denaro): per i token scartati per un segnale soft grave o con un punteggio
     fino a 0,05 sotto la soglia apre un'operazione ipotetica, seguita con le stesse regole d'uscita del Risk;
     gli esiti vanno in `shadow_trades.csv`
4. **Executor** (`PaperBroker`, v6 "simula il live"): non invia nulla, ma esegue come farebbe in live:
   - prezzo d'acquisto e di vendita dai **preventivi reali di Jupiter** alla size vera (API gratuita)
   - prima di comprare chiede anche il preventivo di **rivendita**: senza percorso di vendita (honeypot)
     o con una rivendita che costa più del 15% il token viene scartato
   - latenza di 1–3 s: il fill è al prezzo del momento dell'esecuzione, non del segnale
   - slippage massimo 3% come in live, transazioni che possono fallire (pagando la fee) e nuovi tentativi
   - **priority fee dinamica** letta dalla rete Solana ogni 3 minuti (con minimo e tetto)
   - tiene da parte 0.03 SOL per le fee e l'affitto dei conti token
   - se Jupiter non risponde usa la formula v5 (1% + impatto + 2 × size/liquidità + fee)

   Il bot scarta l'acquisto se 5 volte il costo andata+ritorno (misurato sui preventivi) supera il TP1.
   La size è 3% × (punteggio / 0.6), tra il 2% e il 5% dell'equity e al massimo l'1% della pool.
   Al massimo 8 posizioni e il 30% dell'equity investito.
5. **Risk** (ogni 15 s): valuta le posizioni al valore netto di uscita. Le regole:
   - stop -10% su 2 controlli, uscita immediata a -15%
   - TP1: vende il 30% a +30%, poi lo stop sale a +3% netto
   - TP2: vende il 30% a +80%
   - trailing sul resto da +40%, con distanza 15%, che scende all'8% dopo +150%
   - stop a tempo dopo 30 min se non almeno +5%, tempo massimo 3 h
   - stop giornaliero a -10%, azzerato a mezzanotte (Europe/Rome)
   - limite di perdita totale -30%: crea il file `HARD_STOP` (cancellalo per riattivare gli acquisti)
6. **Learner / Recap**: aggiornano il modello dei pesi dalle operazioni chiuse e scrivono i riepiloghi.
   Dal v6 il Learner impara anche dagli shadow trade: per ogni segnale soft, con almeno 15 esiti e al
   massimo un passo all'ora, allenta o stringe penalità, size e soglia grave entro limiti fissi (i rug pesano
   molto: un rug vale −100%). Ogni passo è un evento `learn_adjust`. Gli scarti HARD non cambiano mai.

## Sicurezza
- Il bot gira **solo in paper**. Con `DESK_PAPER_ONLY=1`, rifiuta qualsiasi altra modalità.
  Non aggiungere wallet, chiavi o `.env`.
- Gli override e le narrative possono solo regolare parametri entro limiti fissi. Non possono saltare
  i controlli anti-rug e non possono attivare la modalità live.
- Un token citato su X non è per questo "ufficiale" né sicuro. Il bonus narrativa arriva solo dopo
  che tutti i filtri sono stati superati.
- I risultati in paper sono **indicativi**. Dalla v6 slippage, ritardi, fee e transazioni fallite sono
  simulati con dati reali, ma MEV, rug improvvisi e congestione estrema in live possono andare peggio. Questo non è un consiglio finanziario.
