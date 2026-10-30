#!/usr/bin/env bash
cd "$(dirname "$0")"
[ -f perp.pid ] && kill "$(cat perp.pid)" 2>/dev/null && echo "fermato PID $(cat perp.pid)"; rm -f perp.pid
