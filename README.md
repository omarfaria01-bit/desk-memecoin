# Desk Memecoin

Bot di trading per memecoin Solana (più un modulo perp), sviluppato da Kiwi.
**Gira solo in paper trading** (`DESK_PAPER_ONLY=1`): simula il live, ma non usa fondi reali.

## Struttura
- `memebot/` – bot memecoin: `desk.py` (motore), `sources/` (GMGN, KOL, momentum, project check), `backtest/`, `tests/`, `web/` (dashboard)
- `perpbot/` – modulo perp paper (`perp_main.py`, `perp/`, `backtest/`, `tests/`)
- `LEGGIMI.md` – note per chi legge il codice (es. altre AI)

## Segreti
Chiavi, wallet, `.env`, stato e log **non sono inclusi**. I file `*.example.json` mostrano il formato.
Il repository è privato e si aggiorna con `memebot/tools/sync_github.sh`.
