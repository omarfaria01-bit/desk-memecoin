# Dashboard web (sola lettura)
- Avvio/riavvio: `bash /workspace/memebot/web/start_web.sh` (riavvia solo server.py e il tunnel, mai il bot)
- Locale: http://localhost:8077  ·  Pubblico: vedi URL.txt
- API: `/` (pagina), `/api/snapshot` (JSON), `/api/stream` (SSE, evento `snap` quando cambia, `ping` ogni 5 s). Nient'altro viene servito.
- Log: server.log, tunnel.log · PID: server.pid, tunnel.pid
- Nota: dal box il tunnel Cloudflare non si collega (porta 7844 bloccata), la pagina passa da sola al polling di /api/snapshot se SSE non arriva.

Aggiornamento 30/10 20:25: posizioni aperte in cima (card con P&L grande, link DexScreener/Birdeye, grafico live DexScreener apribile),
"Andamento generale" (equity % dall'inizio, 1h/6h/tutto, marcatori acquisti/vendite, P&L realizzato cumulato),
pannello "Trade ombra" (state.json shadow_trades + shadow_trades.csv + learner_shadow). Backup in web/backup/.
Riavvio solo del server (senza toccare ngrok): kill $(cat server.pid) e rilancio server.py; NON usare start_web.sh (avvia anche cloudflared).
