# shadow-sample (compito 11)

1. **Cosa:** `sample_reject()` dopo gli scarti del prefiltro e dei filtri di sicurezza (non quelli duri): con probabilità `SHADOW_REJECT_SAMPLE` (0,5, deterministica su seed giornaliero + mint) il token viene seguito in shadow come campione (tag `sample`, colonna CSV `shadow_reason=sample`, motivo e categoria dello scarto nella riga).
2. **Perché:** più esempi etichettati per il learning, senza confonderli con i candidati veri. `sample` è in `MEASURE_ONLY_KEYS`.
3. **File:** `desk.py` (`sample_reject`, `sample_rate`, `spawn_shadow` e `open_shadow` con il parametro `sample`, tetti, colonna CSV, aggancio nel Filter); test `tests/test_shadow_sample.py`.
4. **Prove:** 200 scarti finti → 30 campioni (tetto `SHADOW_SAMPLE_MAX_OPEN`), nessuna chiamata di rete, picco RAM circa 120 KB, gli shadow normali non perdono posto, quota dimezzata oltre l'80% dei posti, riga CSV corretta. Suite come su `main`.
5. **Rischi:** circa 30 mint in più nel `dex_batch` del ciclo shadow (al massimo un blocco da 30 in più ogni 30 s, gratis). La quota scende a ×0,25 con un 429 di DexScreener. I campioni non hanno preventivi Jupiter né candele, quindi l'ATR e i costi sono quelli "da formula": confrontarli con gli shadow normali solo sull'esito.
