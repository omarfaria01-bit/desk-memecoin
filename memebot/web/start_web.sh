#!/usr/bin/env bash
# Avvia (o riavvia) la dashboard web in sola lettura + il tunnel Cloudflare pubblico.
# Non tocca desk.py ne il bot.
set -u
WEB="$(cd "$(dirname "$0")" && pwd)"
BOT="$(dirname "$WEB")"
PORT="${DESK_WEB_PORT:-8077}"
CF="$(command -v cloudflared || echo "$HOME/.local/bin/cloudflared")"
cd "$WEB"
# ferma solo le istanze precedenti di questa dashboard
[ -f server.pid ] && kill "$(cat server.pid)" 2>/dev/null
[ -f tunnel.pid ] && kill "$(cat tunnel.pid)" 2>/dev/null
pkill -f "$WEB/server.py" 2>/dev/null
pkill -f "cloudflared tunnel --url http://localhost:$PORT" 2>/dev/null
sleep 1
DESK_WEB_PORT="$PORT" setsid nohup "$BOT/venv/bin/python" "$WEB/server.py" > "$WEB/server.log" 2>&1 < /dev/null &
echo $! > server.pid
sleep 2
: > tunnel.log
setsid nohup "$CF" tunnel --no-autoupdate --url "http://localhost:$PORT" > "$WEB/tunnel.log" 2>&1 < /dev/null &
echo $! > tunnel.pid
URL=""
for i in $(seq 1 40); do
  URL="$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' tunnel.log | head -1)"
  [ -n "$URL" ] && break
  sleep 1
done
OK=""
if [ -n "$URL" ]; then
  for i in $(seq 1 25); do grep -q "Registered tunnel connection" tunnel.log && OK=1 && break; sleep 1; done
fi
if [ -z "$OK" ]; then
  kill "$(cat tunnel.pid)" 2>/dev/null; echo "-" > tunnel.pid
  URL="NON DISPONIBILE: cloudflared non riesce a collegarsi (porta 7844 bloccata dalla rete del box), vedi tunnel.log"
fi
{
  echo "URL pubblico: ${URL:-non ancora disponibile, vedi tunnel.log}"
  echo "URL locale:   http://localhost:$PORT"
  echo "PID server:   $(cat server.pid)"
  echo "PID tunnel:   $(cat tunnel.pid)"
  echo "Avviato:      $(date '+%d/%m/%Y %H:%M:%S %Z')"
  echo "Riavvio:      bash $WEB/start_web.sh   (il link trycloudflare cambia a ogni riavvio del tunnel)"
} > URL.txt
cat URL.txt
