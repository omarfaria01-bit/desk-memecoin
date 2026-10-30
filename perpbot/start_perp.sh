#!/usr/bin/env bash
# Avvia il Perp Desk in background, SOLO PAPER.
cd "$(dirname "$0")"
export PERP_PAPER_ONLY=1
unset PERP_LIVE_CONFIRM
[ -d venv ] || python3 -m venv venv
if [ -f perp.pid ] && kill -0 "$(cat perp.pid)" 2>/dev/null; then
  echo "Perp Desk gia attivo (PID $(cat perp.pid))"; exit 0
fi
[ -f run.log ] && [ "$(stat -c %s run.log)" -gt 5000000 ] && mv run.log run_prev.log
nohup ./venv/bin/python perp_main.py --headless >> stdout.log 2>&1 &
echo $! > perp.pid
echo "Perp Desk avviato (PID $!) in PAPER"
