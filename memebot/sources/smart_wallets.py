"""
smart_wallets.py - v8.5s (SHADOW): quanti wallet "smart money" della lista di Kiwi tengono un token.
PAPER / sola lettura. Solo stdlib. ZERO chiamate di rete: incrocia la lista con gli holder che cluster_v2 ha GIA'
scaricato (getProgramAccounts) e con il creatore gia' letto da RugCheck/ripiego. Nessuna chiave, nessun .env.

Il risultato e' SOLO registrato (eventi cluster2, dati shadow, tag 'smart:') e NON cambia punteggio, size o acquisti.
I wallet 'dev' sono separati: un dev noto come creatore o holder puo' essere buono o cattivo -> solo log.

Uso:
    from sources import smart_wallets as SW
    SW.match(owner_pct)                 # owner_pct: {owner: % offerta} (il dict `full` di cluster_v2)
    SW.creator_info(creator_address)    # None o {"name", "category"}
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

DATA = Path(os.environ.get("SMART_WALLETS_FILE") or Path(__file__).with_name("smart_wallets.json"))
CATS = ("trader", "dev", "unknown")
MAX_NAMES = 6
_cache = {"mtime": None, "by_addr": {}, "ts": 0.0}


def load(path: Path | None = None) -> dict:
    """address -> {"name", "category", "subtype", "group"}; ricarica solo se il file cambia. Mai eccezioni."""
    p = Path(path or DATA)
    try:
        mt = p.stat().st_mtime
    except OSError:
        return {}
    if path is None and _cache["mtime"] == mt:
        return _cache["by_addr"]
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        return _cache["by_addr"] if path is None else {}
    rows = d.get("wallets", d) if isinstance(d, dict) else d
    out = {}
    for w in rows or []:
        if not isinstance(w, dict):
            continue
        a = str(w.get("address") or "").strip()
        if not a or w.get("status") == "off":
            continue
        cat = w.get("category") if w.get("category") in CATS else "unknown"
        out[a] = {"name": str(w.get("name") or "?")[:40], "category": cat,
                  "subtype": w.get("subtype"), "group": w.get("group") or str(w.get("name") or "?").lower(),
                  "edge": bool(w.get("edge"))}   # vantaggio storico (corpus pump.fun giu-lug 2026)
    if path is None:
        _cache.update(mtime=mt, by_addr=out, ts=time.time())
    return out


def match(owner_pct, wl: dict | None = None) -> dict:
    """owner_pct: dict {owner: pct} oppure iterabile di owner (pct = 0). Ritorna sempre un dict compatto:
    {"n": trader+dev+unknown, "trader": n, "dev": n, "unknown": n, "edge": wallet con vantaggio storico,
    "trader_groups": persone distinte (alt
    dello stesso trader contati una volta), "trader_pct": % offerta tenuta dai trader, "names": [...]}"""
    wl = load() if wl is None else wl
    if not isinstance(owner_pct, dict):
        owner_pct = {o: 0.0 for o in (owner_pct or [])}
    out = {"n": 0, "trader": 0, "dev": 0, "unknown": 0, "trader_groups": 0, "trader_pct": 0.0, "edge": 0, "names": [],
           "list_size": len(wl)}
    if not wl or not owner_pct:
        return out
    groups, hits = set(), []
    for o, pct in owner_pct.items():
        w = wl.get(o)
        if not w:
            continue
        out[w["category"]] += 1
        out["n"] += 1
        out["edge"] += 1 if w.get("edge") else 0
        try:
            p = float(pct or 0.0)
        except (TypeError, ValueError):
            p = 0.0
        if w["category"] == "trader":
            groups.add(w["group"])
            out["trader_pct"] += p
        hits.append((w["category"] != "trader", -p, f"{w['name']}" + ("" if w["category"] == "trader"
                                                                         else f"({w['category']})")))
    out["trader_groups"] = len(groups)
    out["trader_pct"] = round(out["trader_pct"], 3)
    out["names"] = [h[2] for h in sorted(hits)[:MAX_NAMES]]
    return out


def creator_info(creator: str | None, wl: dict | None = None):
    """Il creatore del token e' un wallet della lista? (tipicamente un 'dev'). Solo informativo."""
    wl = load() if wl is None else wl
    w = wl.get(str(creator or "").strip()) if creator else None
    return {"name": w["name"], "category": w["category"]} if w else None


def signal(sm: dict | None) -> str | None:
    """Tag per i dati shadow/Learner: 'smart:<trader_groups>' (+ ':dev' se c'e un dev). None se nessuno."""
    if not sm or not sm.get("n"):
        return None if not (sm or {}).get("creator_dev") else "smart:0:creator_dev"
    t = f"smart:{int(sm.get('trader_groups') or 0)}"
    if sm.get("edge"):
        t += f":edge{int(sm['edge'])}"
    if sm.get("dev"):
        t += ":dev"
    if sm.get("creator_dev"):
        t += ":creator_dev"
    return t
