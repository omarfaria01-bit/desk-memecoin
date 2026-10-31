# tests-cluster-rules

1. **Cosa:** nuovo `tests/test_cluster_rules.py` con fixture sintetiche (indirizzi finti, nessuna rete) sui bordi di quote uniformi (20 wallet; 10 wallet con 15%; soft da 12; rapporto 1,10; partenza entro il rango 10; polvere ed `ext` esclusi), stella (7/8 wallet, 2/3 stelle), finanziatore unico (10/20 = 50%, 10/21, 9/18, 11/21, `ext` e ntx non letti fuori) e ticker impostore (mint ufficiali, nomi per esteso, "Bitcoin Cat" che passa, azioni solo annotate).
2. **Perché:** le regole v8.4c/e erano provate solo su casi lontani dalle soglie; un cambio di soglia o di confronto (`>=` contro `>`) passava inosservato.
3. **File:** solo `tests/test_cluster_rules.py` (nessuna modifica al codice del bot).
4. **Prove:** test verde su copia in /tmp. Controllo inverso: spostando di 1 ognuna di UNIFORM_HARD_WALLETS, UNIFORM_HARD_PCT, UNIFORM_SOFT_WALLETS, UNIFORM_RATIO, STAR_MIN_WALLETS, STAR_HARD_N, SINGLE_FUNDER_MIN e SINGLE_FUNDER_SHARE, il test fallisce (8 su 8).
5. **Rischi:** nessuno sul bot. Nessun token reale nelle fixture, quindi nessuna lezione negativa usata come esempio pulito.
