"""
discord_calls.py - v8.8 (SHADOW): il token e' stato chiamato in Discord "Noah Knows Trades" #general-chat?
PAPER / sola lettura. Solo stdlib. ZERO chiamate di rete, ZERO chiavi, ZERO crediti: legge solo
sources/discord_calls.json (scritto offline da /workspace/discord_calls/label_calls.py, sincronizzato a mano o dalla routine).

Campi registrati (mai nello score, mai in size/acquisti):
  discord_call = 1 se il mint e' nel file
  discord_call_age_min = minuti tra la call e il momento in cui il bot vede il token (negativo = bot prima della call)
L'esito della call (label, max_x) e' informazione FUTURA: NON viene mai esposto come feature d'ingresso.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

DATA = Path(os.environ.get("DISCORD_CALLS_FILE") or Path(__file__).with_name("discord_calls.json"))
_cache = {"mtime": None, "calls": {}, "chk": 0.0}


def load(path: Path | None = None, recheck_sec: float = 60.0) -> dict:
    """mint -> {"call_ts", "author", ...}; controlla il file al massimo ogni 60 s e ricarica solo se cambia. Mai eccezioni."""
    p = Path(path or DATA)
    now = time.time()
    if path is None and now - _cache["chk"] < recheck_sec and _cache["mtime"] is not None:
        return _cache["calls"]
    if path is None:
        _cache["chk"] = now
    try:
        mt = p.stat().st_mtime
    except OSError:
        if path is None:
            _cache.update(mtime=None, calls={})
        return {}
    if path is None and _cache["mtime"] == mt:
        return _cache["calls"]
    try:
        d = json.loads(p.read_text())
        calls = d.get("calls", d) if isinstance(d, dict) else {}
        calls = {str(k): v for k, v in calls.items() if isinstance(v, dict) and v.get("call_ts")}
    except (OSError, ValueError, AttributeError):
        return _cache["calls"] if path is None else {}
    if path is None:
        _cache.update(mtime=mt, calls=calls)
    return calls


def info(mint, now: float | None = None, calls: dict | None = None) -> dict:
    """{} se non chiamato; altrimenti {"discord_call": 1, "discord_call_age_min": float}."""
    try:
        c = (calls if calls is not None else load()).get(str(mint))
        if not c:
            return {}
        age = ((now or time.time()) - float(c["call_ts"])) / 60.0
        return {"discord_call": 1, "discord_call_age_min": round(age, 1)}
    except Exception:  # noqa: BLE001
        return {}
