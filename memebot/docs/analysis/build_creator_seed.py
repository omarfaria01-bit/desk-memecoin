#!/usr/bin/env python3
"""v8.7 (D) - seme di reputazione creatori dal corpus Slinky21/Pumpfun_Memecoin_Corpus (CC BY 4.0, 5 giu - 14 lug 2026).
Gira sul box. Uso: python build_creator_seed.py <dir tokens.parquet> <postgard_outcomes.parquet> <out creator_seed.json>
Formato compatto: {"meta": {...}, "c": {creator: [n_lanci, n_graduati, n_rug, n_falliti_dopo_grad]}}
- n_graduati = graduated_at presente (affidabile solo dal ~19 giu: prima e quasi sempre vuoto)
- n_rug = postgard rug_detected; n_falliti = esito dead/pump_dump/slow_bleed dopo la graduazione
- solo creatori con >= 2 lanci o almeno una graduazione (gli altri non dicono nulla)"""
import json
import sys
import time

import duckdb

d, outc, out = sys.argv[1], sys.argv[2], sys.argv[3]
con = duckdb.connect()
rows = con.execute(f"""
  select t.creator, count(*) n, count(t.graduated_at) g,
         sum(case when o.rug_detected then 1 else 0 end) r,
         sum(case when o.outcome_label in ('dead','pump_dump','slow_bleed') then 1 else 0 end) f
  from '{d}/tokens.parquet' t left join '{outc}' o using(mint)
  where t.creator is not null and length(t.creator) between 32 and 44
  group by 1 having count(*) >= 2 or count(t.graduated_at) > 0""").fetchall()
c = {cr: [int(n), int(g), int(r), int(f)] for cr, n, g, r, f in rows}
meta = {"source": "Slinky21/Pumpfun_Memecoin_Corpus (CC BY 4.0)", "window": "2026-06-05..2026-07-14",
        "fields": ["n_lanci", "n_graduati", "n_rug", "n_falliti_dopo_grad"], "n_creators": len(c),
        "note": "graduated_at quasi assente prima del 19 giu: n_graduati sottostimato per i creatori di inizio giugno",
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
with open(out, "w") as fh:
    json.dump({"meta": meta, "c": c}, fh, separators=(",", ":"))
ser = sum(1 for v in c.values() if v[0] >= 10 and v[1] == 0)
print(f"creatori {len(c)}, serial (>=10 lanci, 0 graduati) {ser}")
