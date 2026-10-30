# Desk Memecoin – codice completo (paper)
- memebot/desk.py: bot principale v8.1 (Scout, Filter, Analyst, Executor, Risk, Learner). Gira con DESK_PAPER_ONLY=1. v8.1: stop/TP/trailing dinamici dall'ATR solo registrati (shadow), feature d'ingresso nei CSV, percorsi di prezzo in price_paths/ (stato, non inclusi).
- memebot/sources/: trending (GMGN/DexScreener), KOL holders e whitelist, tesi KOL, momentum, project_check (v8: controllo rapido sito/social/GitHub/metadati, bonus piccolo).
- Deep review (v8): il bot scrive sources/deep_review_queue.jsonl (stato, non incluso); Mark scrive sources/deep_reviews.json nel formato di sources/deep_reviews.example.json.
- memebot/web/: dashboard (server.py + index.html).
- memebot/tests/: test. memebot/backtest/: backtest delle uscite.
- perpbot/: modulo perp separato in paper (Hyperliquid, leva max 10x).
- Leggi CHANGES.md, LIVE_READINESS.md e sources/SCORING_PROPOSAL.md.
Vincoli: niente live senza ok esplicito, nessuna chiave nel codice, ogni nuova soglia va in overrides con clamp.
