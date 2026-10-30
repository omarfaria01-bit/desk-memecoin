#!/usr/bin/env python3
"""Perp Desk - avvio. Solo PAPER: PERP_PAPER_ONLY=1 e il default.
  python3 perp_main.py --headless     ciclo continuo (log in run.log, stato in state.json)
  python3 perp_main.py --once         un solo ciclo (test)
  touch STOP   chiude tutte le posizioni e blocca nuovi ingressi;  touch PAUSE   solo niente nuovi ingressi
"""
import os, sys
os.environ.setdefault("PERP_PAPER_ONLY", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from perp.engine import Engine

if __name__ == "__main__":
    Engine(HERE).run(once="--once" in sys.argv)
