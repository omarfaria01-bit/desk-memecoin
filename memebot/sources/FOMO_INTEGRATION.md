# Trending GMGN (+ Fomo) -> Scout (nota di integrazione)

> **Aggiornamento 30/10/2026, 20:45:** l'utente preferisce **GMGN**. La fonte principale ora è `gmgn_trending.py`.
> Fomo/fomoapi.io è messo da parte: la domanda sulla chiave non serve più e `fomo_trending.py` resta solo come modulo opzionale.

## GMGN (fonte principale)
- **Sito gmgn.ai**: le API JSON interne (`/defi/quotation/v1/rank/sol/swaps/1h`, `/api/v1/rank/...`), e persino `robots.txt`,
  sono dietro una challenge Cloudflare ("Just a moment...", 403). **Dal box sono bloccate e non le aggiriamo.**
- **API ufficiale GMGN OpenAPI**: `GET https://openapi.gmgn.ai/v1/market/rank?chain=sol&interval=1h&order_by=default&filters=not_honeypot&filters=renounced`
  con l'header `X-APIKEY`, più `timestamp` e `client_id` (un UUID nuovo a ogni richiesta). Funziona solo su IPv4. **Testata: funziona.**
  - Senza chiave personale si usa la chiave **pubblica read-only** che GMGN pubblica per demo e agenti
    (`gmgn_solbscbaseethmonadtron`). È condivisa da tutti: il limite è basso (`X-RateLimit-Limit: 5`), è pensata
    per i test e GMGN può revocarla. Non serve login.
  - Opzione più robusta, gratuita ma con login GMGN: una chiave personale creata su gmgn.ai/ai caricando una chiave pubblica Ed25519.
    Va messa in `GMGN_API_KEY` o in `~/.config/desk/gmgn_api_key` (mai nel `.env` del bot). Per ora non serve.
- Polling ogni 180 s (`GMGN_POLL_SEC`, tra 120 e 300), con backoff esponenziale su 401/403/429/5xx e cache su disco `gmgn_trending_state.json`.
- Ogni token porta anche metadati GMGN (`hot_level`, `smart_degen_count`, `renowned_count`, `bundler_rate`, `rat_trader_amount_rate`,
  `top_10_holder_rate`, `is_wash_trading`, `sniper_count`...). Sono **solo tag o informazioni**: non sostituiscono i nostri filtri.
- Se GMGN non risponde e non c'è cache, il modulo passa al **SOSTITUTO** GeckoTerminal `trending_pools` (etichetta
  `gecko_trending_substitute`, bonus 0). Attenzione: Gecko è già usato dallo Scout e spesso risponde 429.

```python
from sources.gmgn_trending import aget_trending
res = await aget_trending(limit=25)   # source: "gmgn_trending" | "gecko_trending_substitute" | "none"
```
Integrazione nello Scout: identica a quella descritta sotto per Fomo. Tag `src=gmgn_trending`, bonus `GMGN_TREND_BONUS`
(default 0.03, clamp 0–0.08), regolato dal Learner, **nessun bypass** dei filtri.

---
# (Opzionale) Fomo

## Cos'è Fomo
fomo.family ("fomo – Never Miss Out", app iOS/Android + web) è un'app social di trading crypto (memecoin Solana,
e anche altre chain). La board "trending" mostra cosa stanno tradando gli utenti dell'app.
**Non c'è un'API pubblica ufficiale.** Il sito pubblico è solo una landing; la board si vede solo dentro l'app,
con il login. robots.txt vieta /token e /coin.

## Fonti verificate (30/10/2026)
| Fonte | Tipo | Login/chiave | Costo | Stato |
|---|---|---|---|---|
| `https://api.fomoapi.io/v2/leaderboard/tokens/trending` | API di terzi NON ufficiale (rispecchia la board di Fomo, con cache di 5 min) | chiave gratuita, da registrare su fomoapi.io/dashboard | free = 250k crediti al mese = 1000 chiamate (250 a chiamata) | testata: risponde 401 senza chiave, come atteso |
| `api.pumpfomo.com/v1/fomo/tokens/trending` | API di terzi, a pagamento | chiave | a crediti | non usata |
| DexScreener `token-boosts/top` + `token-boosts/latest` | **SOSTITUTO**, NON è Fomo | nessuna | gratis | testata, funziona |

Con il piano free di fomoapi.io si fanno circa 33 chiamate al giorno, quindi **non** si può interrogare ogni 2-5 minuti.
Il modulo limita le chiamate a una ogni 48 min, con un massimo di 30 al giorno (`FOMO_POLL_SEC`, `FOMO_DAILY_CAP`), e usa ETag
(un 304 non costa crediti). Per avere la fonte vera serve la chiave: l'utente deve registrarla (è gratuita) e salvarla
nella variabile d'ambiente `FOMOAPI_KEY` o nel file `~/.config/desk/fomoapi_key`. **Mai metterla nel .env del bot.**
Senza chiave il modulo usa il sostituto DexScreener e lo etichetta `dex_boosts_substitute`.
Attenzione: i boost di DexScreener sono **promozioni pagate dai team**. Sono un segnale di attenzione, non di qualità.

## API del modulo
```python
from sources.fomo_trending import aget_trending   # oppure get_trending() sincrono
res = await aget_trending(limit=25)
# res["source"]: "fomo_trending" | "dex_boosts_substitute" | "none"
# res["tokens"]: [{mint, rank, symbol, name, price_usd, mcap_usd, change24h, holders, source}]
```
Il modulo non solleva mai eccezioni. Ha timeout di 10 s, backoff su 401/402/429/5xx e una cache su disco in `fomo_trending_state.json`.

## Come lo usa lo Scout (da fare quando desk.py è libero; NON l'ho modificato)
1. Nuovo task `scout_fomo(st)`: ogni 180 s chiama `aget_trending()`. Per ogni token chiama
   `add_watch(st, mint, symbol, res["source"])` e salva `st.fomo_rank[mint] = rank`.
2. **Nessun bypass.** Il token passa da Filter, Risk, rugcheck, bundle/cluster, sell-route e soft flag come tutti gli altri.
3. Tag: `src` = `fomo_trending` (o `dex_boosts_substitute`) va scritto in `trades.csv` e in `shadow_trades.csv`, nella colonna
   `signals` (vedi SCORING_PROPOSAL.md), così il Learner confronta questa fonte con le altre.
4. Bonus: `+FOMO_TREND_BONUS` (default 0.03, clamp 0–0.08) solo se `source == "fomo_trending"`. Il sostituto riceve
   bonus 0 finché il Learner non dimostra che rende. Il bonus va contato nel tetto `TOTAL_BONUS_CAP`.
5. Il Learner regola `FOMO_TREND_BONUS` dai risultati, con le stesse regole degli altri bonus (vedi SCORING_PROPOSAL.md).
