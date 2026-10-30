# Passaggio al LIVE su Hyperliquid – cosa serve (NON fatto)

Oggi: solo paper. `PERP_PAPER_ONLY=1` è il default, `config.json` ha `"MODE": "paper"` e `"LIVE_ENABLED": false`.
Il live richiede **tutte** queste condizioni insieme: `MODE=live`, `LIVE_ENABLED=true`, `PERP_PAPER_ONLY=0`,
`PERP_LIVE_CONFIRM=YES`. Anche così oggi `HyperliquidLiveBroker` solleva un errore, perché non è implementato.

## 0. Prima di tutto: i numeri
Almeno 50–100 trade paper con profit factor > 1,2 dopo fee e funding, drawdown accettabile e risultati coerenti
con il backtest. Oggi il backtest fuori campione è negativo: **non è pronto per i soldi veri**.

## 1. Conto e chiavi (le fa l'utente, mai in chat)
- Wallet principale dell'utente con USDC depositati su Hyperliquid (solo il capitale che si accetta di perdere).
- Creare un **API wallet (agent)** da app.hyperliquid.xyz → API: può fare trading ma **non può prelevare**.
- La chiave dell'agent va solo nell'ambiente del computer del bot (es. `HL_AGENT_KEY`), mai su file condivisi o in chat.
  Serve anche l'indirizzo del conto principale (`HL_ACCOUNT_ADDRESS`), perché l'agent firma per conto del master.
- Consigliato un **sub-account** dedicato al bot.

## 2. Codice da scrivere in `perp/live_broker.py` (l'unico file da cambiare)
- Dipendenze: `pip install hyperliquid-python-sdk eth-account` (SDK ufficiale; si occupa della firma EIP-712).
- `from hyperliquid.exchange import Exchange`, `from hyperliquid.info import Info`,
  `Exchange(Account.from_key(HL_AGENT_KEY), constants.MAINNET_API_URL, account_address=HL_ACCOUNT_ADDRESS)`.
- `set_leverage` → `exchange.update_leverage(lev, coin, is_cross=False)` (margine isolato, `lev ≤ 10`).
- Ordine market → su Hyperliquid è un limit **IOC** con prezzo limite = mid ± slippage massimo
  (`exchange.order(coin, is_buy, sz, px, {"limit": {"tif": "Ioc"}})` oppure `exchange.market_open`).
  Arrotondare `sz` a `szDecimals` e il prezzo a max 5 cifre significative (regola tick di Hyperliquid).
- Stop e TP1 → ordini trigger reduce-only:
  `{"trigger": {"triggerPx": px, "isMarket": True, "tpsl": "sl"}}` (o `"tp"`), `reduce_only=True`.
  Lo stop trailing si aggiorna con `modify_order` o cancel+nuovo ordine.
- `cancel` → `exchange.cancel(coin, oid)`; `open_orders` → `info.frontend_open_orders(address)`.
- `positions`/`balance` → `info.user_state(address)` (`assetPositions`, `marginSummary`, `liquidationPx`).
- `funding` → `info.user_funding_history(address, start)`; con il live il funding lo applica l'exchange,
  quindi `apply_funding`/`on_tick` restano solo nel PaperBroker.
- Usare un `cloid` (client order id) per ogni ordine, per riconciliare dopo errori di rete.
- Gestire risposte `"error"`/`"resting"`/`"filled"` dell'API e fill parziali; ritentare solo con riconciliazione.
- All'avvio: **riconciliazione** posizioni/ordini aperti sull'exchange con `state.json`
  (se c'è una posizione senza stop, rimettere subito lo stop).

## 3. Sicurezza in più per il live
- Tetto in USD per trade e per giorno nel config, oltre al rischio dell'1%.
- Stop giornaliero −5% già presente; aggiungere stop sulla perdita totale (es. −20% → ferma tutto).
- Kill switch `STOP` già presente: chiude tutto a mercato e cancella gli ordini.
- Heartbeat: se il bot si ferma, gli stop restano sull'exchange (già così nel design).
- Rate limit API per indirizzo (Hyperliquid limita le azioni in base al volume scambiato): pochi ordini, niente spam.

## 4. Dry run (obbligatorio)
1. `LIVE_DRY_RUN=1`: il broker live costruisce e firma gli ordini ma **non li invia**; li scrive in `dry_run.jsonl`
   accanto al fill simulato del PaperBroker, per controllare prezzi, size, arrotondamenti e trigger.
2. Testnet Hyperliquid (`constants.TESTNET_API_URL`, fondi di prova) per qualche giorno.
3. Mainnet con importi minimi (es. rischio 0,1% per trade, minimo ordine $10) confrontando ogni trade con il paper.
4. Solo dopo, rischio pieno.

## 5. Cosa NON cambia
`strategy.py`, `risk.py`, `learning.py`, `engine.py`, i file di log e lo snapshot restano identici.
