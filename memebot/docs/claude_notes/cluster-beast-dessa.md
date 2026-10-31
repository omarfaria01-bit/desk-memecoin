# cluster-beast-dessa

1. **Cosa:** regola "famiglie soft" in `cluster_v2.combo_families`. Con ≥ 3 famiglie indipendenti insieme (wallet nuovi, wallet nuovi dallo stesso exchange, finanziatore/stella, quote uniformi, gruppo/collegati) il token è HARD, ma solo con `CLUSTER_FAM_MODE = on`. Il default è `shadow`: il risultato viene registrato in `combo`, senza effetti. In più `cluster2_brief` registra negli eventi uniform, single_funder, stars, cexf, recv e combo, e c'è un fix separato: `MARK_NOROUTE_CONFIRM` non era in `OVR_GLOBAL_MAP`.
2. **Perché shadow:** sui valori registrati DESSA ha 3 famiglie (hard) e Bonnie 2 (no). BEAST ha 2 famiglie note (wallet nuovi non registrati) e Dacity 2 note (stella 17, gruppo 16,1%); con wallet nuovi o uniformi ≥ 4, Dacity diventerebbe hard. Senza le feature complete negli eventi non si può dimostrare "BEAST hard e Dacity no".
3. **File:** `sources/cluster_v2.py`, `desk.py` (brief, cfg della scansione, overrides), `tests/test_cluster_beast_dessa.py`, `docs/analysis/cluster_combo_eval.py`.
4. **Prove:** suite su copia in /tmp tutta verde (`test_narr` fallisce anche su `main`); fixture costruite sui valori di CHANGES.md e LABEL_SEEDS. RPC Solana e RugCheck non sono raggiungibili da qui, quindi niente riscansione.
5. **Tabella** (regola con N = 3; "?" = valore mai registrato):

| Token | Stato | Famiglie note | Esito regola | Oggi |
|---|---|---|---|---|
| DESSA | cattivo | fresh, cex_fresh (3 KuCoin), uniform | **hard** | soft |
| BEAST | cattivo | funder 5, uniform 4 (+ fresh?, group?) | hard solo se c'è una 3ª famiglia | soft |
| PILLSEY, BNB, HYPERCHAN, GLITCH | cattivi | già hard per altre regole | hard | hard |
| Bonnie | controllo | uniform 4, group (5,9%); fresh 1/60 | non hard | soft |
| Dacity | controllo | funder/stella 17, group 16,1% (+ fresh?, uniform?) | non hard con i dati noti, **a rischio** | soft |
| GTA6, STONK, FLOAT, LUMI | probabili buoni | non registrate | ? | soft |
| neet | probabile buono | già hard (gruppo 33%, un finanziatore 42/60) | hard | hard |

Prossimo passo: lasciare girare il brief completo, poi `cluster_combo_eval.py` su una copia di `events.jsonl`; si passa a `on` solo se Dacity e Bonnie restano sotto N.
