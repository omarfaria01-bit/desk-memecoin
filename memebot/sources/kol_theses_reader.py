"""
kol_theses_reader.py - legge kol_theses.json (lo scrive SOLO Radar, il collega che monitora X).
Questo modulo non chiama mai X. Sola lettura, mai eccezioni.

Schema di ogni voce (lista JSON):
  mint        (obbligatorio) mint Solana
  kol_handle  (obbligatorio) handle X senza @ (deve combaciare con "handle" o "x" in kol_whitelist.json)
  tweet_url   (obbligatorio) link al post
  ts          (obbligatorio) ISO-8601 con fuso, oppure epoch secondi
  kind        opzionale: "thesis" | "shill" | "exit" ("exit" = il KOL dice che esce -> nessun bonus)
  text, added_by  opzionali
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
FILE = HERE / "kol_theses.json"
_c = {"mtime": None, "rows": []}


def _ts(v):
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def load():
    try:
        mt = FILE.stat().st_mtime
        if mt != _c["mtime"]:
            rows = json.loads(FILE.read_text())
            _c["rows"] = [r for r in rows if isinstance(r, dict) and r.get("mint") and r.get("kol_handle")
                          and r.get("tweet_url") and _ts(r.get("ts")) is not None]
            _c["mtime"] = mt
    except (OSError, ValueError):
        pass
    return _c["rows"]


def theses_for(mint, max_age_h=6.0):
    now = time.time()
    return [r for r in load() if r["mint"] == mint and now - _ts(r["ts"]) <= max_age_h * 3600]


def thesis_bonus(mint, tier_of, cfg, weight_of=None):
    """tier_of(handle) -> "S"|"A"|"B"|"off"|None (whitelist + KolLedger). S 1.25x, A pieno, B meta', altri 0."""
    full = float(cfg.get("KOL_THESIS_BONUS", 0.02))
    best, tags = 0.0, []
    for r in theses_for(mint, float(cfg.get("KOL_THESIS_MAX_AGE_H", 6))):
        h = r["kol_handle"].lstrip("@")
        if r.get("kind") == "exit":
            tags.append(f"kol_thesis_exit:{h}")
            continue
        mult = {"S": 1.25, "A": 1.0, "B": 0.5}.get(tier_of(h) or "", 0.0) * (weight_of(h) if weight_of else 1.0)
        if mult:
            best = max(best, full * mult)
            tags.append(f"kol_thesis:{h}")
    if any(t.startswith("kol_thesis_exit") for t in tags):
        best = 0.0
    return best, tags
