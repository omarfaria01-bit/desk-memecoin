# integration/claude-31-10 — riepilogo per Kiwi

Base: `main` `ce20cf0`. Branch uniti, tutti già rivisti:

| Compito | Branch | Default |
|---|---|---|
| 13 errori aperti | `claude/bug-hunt-2` | attivi (dati mancanti = sconosciuto/scarto, mai "ok"); lista nera con scadenza 30 g |
| 11 campione scarti | `claude/shadow-sample` | `SHADOW_REJECT_SAMPLE` 0,5, tetto 30, solo shadow |
| 12 ri-etichettatura | `claude/retro-label` | script `tools/retro_label.py` per il cron; `RETRO_LABELS_IN_LEARNING` off |
| 21 età = rimandare | `claude/age-recheck` | attivo (ricontrollo completo) + shadow `age_only` |
| 14 watchlist | `claude/watch-smart` | sfratto per qualità attivo; `WATCH_ENTRY_GATE` shadow |
| stella nella coda | `claude/tail-star` | `TAIL_MODE` shadow |
| 15 (Terminus) + review | `claude/review-more-trades` | solo documenti: proposta **sospesa** |
| 17 (Terminus) + review | `claude/review-brand-filter` | `BRAND_FILTER_MODE` shadow; controlli positivi mai penalizzati |

**Conflitti risolti a mano** (tutti in `desk.py`):
- `MEASURE_ONLY_KEYS` è un solo insieme con `holders_unknown`, `sample`, `age_only` ed `evicted`;
- i nuovi override (`LIQ_CRASH_CONFIRM`, `BLACKLIST_TTL_DAYS`) sono tenuti tutti e due;
- uno scarto del prefiltro **solo per età** va a `age_requeue` (con il suo shadow `age_only`), gli altri al campione (`sample_reject`): mai due shadow per lo stesso token;
- nel ciclo del Filter prima `watch_note` / filtro all'ingresso (14), poi l'attesa dell'età minima (21).

**Test** (copia in /tmp): 37 OK. `test_narr` fallisce anche su `main`. Con i file di `data-snapshot` (model_prior, smart_wallets, creator_seed) passa anche `test_v85s_smart`. `test_v85d` e `test_v85h_liq` falliscono alla **stessa riga** su `main` e qui (usano file del server applicati una volta: `host_pause_retro.json`, `liq_crash_retro.json`).

**Prima del deploy:**
- `shadow_trades.csv` riceve la colonna nuova `shadow_reason` (migrata da `csv_migrate`, le righe vecchie restano vuote).
- `.gitignore`: `retro_cache/` e `retro_labels_summary.json`.
- Cron facoltativo per il compito 12: `CRON_TZ=Europe/Rome`, `0 4 * * * cd /opt/desk/memebot && nice -n 10 python3 tools/retro_label.py >> retro_label.log 2>&1`.
- Cambiano le decisioni (fail-closed) solo i fix del compito 13 e il ricontrollo del 21. Tutto il resto è shadow o solo misura.

**Non inclusi** (Terminus, non ancora rivisti): `claude/cluster-shadow` (18), `claude/shadow-telegram` (19), `claude/rugcheck-fix` (20). Esclusi anche i branch già entrati in `main` per contenuto: `cluster-beast-dessa`, `rugcheck-fallback-review`, `tests-cluster-rules`, `amazing-goodall-nfn3me`.
