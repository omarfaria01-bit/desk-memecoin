# Compito 17 — filtro "nomi di marchi famosi": verifica sui dati

Dati: `data-snapshot` 31/10 (225 shadow, 5 trade reali). `/workspace/desk_reviews/deep_reviews.json` citato
nel compito non esiste in questo ambiente: verifica fatta solo su `shadow_trades.csv`/`trades.csv`.
Script: `docs/analysis/brand_filter_report_17.py`, lista/matching in `sources/brand_filter.py`
(`brand_tag`, confronto esatto dopo normalizzazione + leetspeak `0→O 1→I 3→E 4→A 5→S 7→T 8→B`, stessi
suffissi ammessi di `ticker_impostor` già esistente in `desk.py`). Diverso dal controllo "ticker
impostore" già attivo (quello copre i grandi asset/exchange crypto, sempre scartato): questo copre
aziende/celebrità/piattaforme NON crypto, resta in shadow.

## Numeri (righe valide, `valido=1`)

| popolazione | n | pnl medio | pnl mediano | rug | crash ≤-50% |
|---|---|---|---|---|---|
| shadow con brand | 13 | -2.5% | -34.0% | 8/13 (62%) | 38% |
| shadow con brand (escluse le 6 lezioni negative) | 12 | -28.2% | -34.2% | 8/12 (67%) | 42% |
| shadow senza brand | 172 | -28.5% | -18.4% | 64/172 (37%) | 27% |
| trade reali | 4 (0 brand) | +1.9% | +1.2% | 0% | 0% |

La media grezza (-2.5%) è falsata da 3 valori estremi positivi (BEAST +306% — **lezione negativa, mai
esempio buono**, TSLA +130.9%, NVIDIA +133.2%): la mediana (-34%) e il rug rate (67% contro 37%) danno
il quadro vero. Righe: NIO -12.3%, MrBeast -34.3% (rug), Grok ×4 (-91.8%/-33.1%/-34.0%/-100.0%, tutte
rug), GTA 6 -100.0% (rug), Robinhood -97.7% (rug), SpaceX -100.0% (rug), GTA6 +0.5%, TSLA +130.9%,
NVIDIA +133.2%.

**Limite:** n=12 è piccolo per una regola fissa; 4/12 righe (NIO, GTA6, TSLA, NVIDIA) sono normali,
non rug. Il segnale (rug e mediana peggiori) è coerente con l'idea di Kiwi ma **non abbastanza solido**
per uno scarto rigido subito: resta `shadow` com'è richiesto, da rivedere quando ci sono più dati
(compito 11/12 aiuteranno).

## Implementazione

`sources/brand_filter.py`: lista chiusa (aziende, celebrità, piattaforme/exchange non crypto — le
azioni quotate restano anche in `STOCK_TICKERS`, qui aggiunte per un tag unico; le celebrità/marchi non
quotati come MrBeast, SpaceX, Grok, LEGO, Gemini sono nuovi). `brand_tag(symbol, name)` → marchio o
`None`, confronto esatto (mai substring libera: "AAPLCAT" non matcha "AAPL", niente scarti a vuoto).

`desk.py`: nuovo segnale soft `"brand"` in `SOFT_FLAGS` (`hard: None`, mai promosso a scarto rigido in
automatico dal Learner). `CFG["BRAND_FILTER_MODE"]`:
- **`shadow` (default)**: tag `brand:<nome>` sempre loggato nei `signals` (per misurare), **zero effetto**
  su punteggio/size.
- `on`: penalità (`BRAND_PENALTY`=0.05) e size ridotta (`BRAND_SIZE_MULT`=0.5), come gli altri segnali
  soft graduati — da attivare solo dopo la tua review.
- `off`: spento.

Nessuno scarto rigido: un token con nome da marchio ma sicurezza ok può ancora essere comprato (in
shadow: solo misurato; in `on`: punteggio/size ridotti, non bloccato).

## Test

`tests/test_brand_filter.py`: `brand_tag` puro (leetspeak, suffissi, nessun falso positivo su
"AAPLCAT"/"Goyim"), integrazione nell'Analyst (`shadow` non tocca punteggio/size, `on` sì, `off` niente
tag). Verde su copia `/tmp`; nessuna regressione sui test di cluster/bundle/narrativa esistenti
(controllato su copia pulita).
