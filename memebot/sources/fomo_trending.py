"""
fomo_trending.py - fonte "trending su Fomo" per lo Scout di Desk Memecoin (PAPER).

Modulo standalone, sola lettura, nessuna dipendenza esterna (solo stdlib).
NON tocca wallet, chiavi di trading o .env.

Backend, in ordine:
  1) "fomo_trending"  -> board trending di fomo.family tramite FOMO API (fomoapi.io),
     API INDIPENDENTE/NON UFFICIALE. Serve una chiave gratuita (250k crediti/mese =
     ~1000 chiamate, 250 crediti per chiamata). Si attiva SOLO se esiste la variabile
     d'ambiente FOMOAPI_KEY o il file ~/.config/desk/fomoapi_key (mai in .env del bot).
     Con il piano free il modulo rispetta un budget: max FOMO_DAILY_CAP chiamate/giorno
     (default 30) e intervallo minimo FOMO_POLL_SEC (default 2880 s = 48 min).
     Usa ETag/If-None-Match: un 304 non consuma crediti.
  2) "dex_boosts_substitute" -> SOSTITUTO, NON e' Fomo: token Solana con boost attivi su
     DexScreener (API pubblica gratuita, senza chiave). I boost sono promozioni PAGATE dai
     team: e' un segnale di attenzione, non di qualita'. Cache 180 s.

Uso:
    from sources.fomo_trending import get_trending
    res = get_trending()            # sincrono, mai eccezioni
    res = await aget_trending()     # versione async (thread)
    res -> {"source": "fomo_trending" | "dex_boosts_substitute" | "none",
            "fetched_at": epoch, "captured_at": str|None, "stale": bool, "error": str|None,
            "tokens": [{"mint", "rank", "symbol", "name", "price_usd", "mcap_usd",
                        "change24h", "holders", "source"}...]}
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

FOMO_URL = "https://api.fomoapi.io/v2/leaderboard/tokens/trending?limit={limit}"
DEX_BOOST_TOP = "https://api.dexscreener.com/token-boosts/top/v1"
DEX_BOOST_LATEST = "https://api.dexscreener.com/token-boosts/latest/v1"

TIMEOUT = 10
UA = "DeskMemecoin-paper/1.0 (+read-only scout)"
SOL_NETWORK_IDS = {"solana", "sol", "1399811149"}
B58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

STATE_FILE = Path(__file__).with_name("fomo_trending_state.json")
FOMO_POLL_SEC = int(os.environ.get("FOMO_POLL_SEC", "2880"))   # free tier: ~30/giorno
FOMO_DAILY_CAP = int(os.environ.get("FOMO_DAILY_CAP", "30"))
SUB_POLL_SEC = max(120, min(300, int(os.environ.get("FOMO_SUB_POLL_SEC", "180"))))

_mem: dict = {}


def _api_key() -> str | None:
    k = os.environ.get("FOMOAPI_KEY")
    if k:
        return k.strip()
    p = Path.home() / ".config" / "desk" / "fomoapi_key"
    try:
        return p.read_text().strip() or None
    except OSError:
        return None


def _load_state() -> dict:
    if _mem:
        return _mem
    try:
        _mem.update(json.loads(STATE_FILE.read_text()))
    except (OSError, ValueError):
        pass
    _mem.setdefault("fomo", {})
    _mem.setdefault("sub", {})
    return _mem


def _save_state() -> None:
    try:
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_mem))
        tmp.replace(STATE_FILE)
    except OSError:
        pass


def _get(url: str, headers: dict | None = None):
    h = {"User-Agent": UA, "Accept": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, dict(r.headers), json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), None
    except Exception as e:  # noqa: BLE001  timeout, DNS, JSON...
        return 0, {}, str(e)[:120]


def _num(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ backend 1: Fomo (via fomoapi.io)
def _parse_fomo(data: dict) -> list[dict]:
    out = []
    for t in (data or {}).get("tokens") or []:
        tok = t.get("token") or {}
        mint = str(tok.get("address") or "")
        net = str(t.get("network") or "").lower()
        if net not in SOL_NETWORK_IDS or not B58.match(mint):
            continue  # solo Solana
        out.append({
            "mint": mint, "rank": t.get("rank"), "symbol": tok.get("symbol"), "name": tok.get("name"),
            "price_usd": _num(t.get("priceUsd")), "mcap_usd": _num(t.get("marketCapUsd")),
            "change24h": _num(t.get("change24h")), "holders": t.get("holders"), "source": "fomo_trending",
        })
    return out


def _fomo(key: str, limit: int) -> dict:
    s = _load_state()["fomo"]
    now = time.time()
    day = time.strftime("%Y-%m-%d")
    if s.get("day") != day:
        s["day"], s["calls"] = day, 0
    fresh = s.get("tokens") is not None and now - s.get("fetched_at", 0) < FOMO_POLL_SEC
    if fresh or s.get("calls", 0) >= FOMO_DAILY_CAP or now < s.get("backoff_until", 0):
        if s.get("tokens") is not None:
            return {"source": "fomo_trending", "fetched_at": s["fetched_at"], "captured_at": s.get("captured_at"),
                    "stale": not fresh, "error": None, "tokens": s["tokens"]}
        return {"source": "fomo_trending", "error": "budget/backoff, nessun dato in cache", "tokens": []}
    hdr = {"Authorization": f"Bearer {key}"}
    if s.get("etag"):
        hdr["If-None-Match"] = s["etag"]
    code, headers, data = _get(FOMO_URL.format(limit=limit), hdr)
    s["calls"] = s.get("calls", 0) + 1
    if code == 304 and s.get("tokens") is not None:
        s["fetched_at"] = now
        _save_state()
        return {"source": "fomo_trending", "fetched_at": now, "captured_at": s.get("captured_at"),
                "stale": False, "error": None, "tokens": s["tokens"]}
    if code == 200 and isinstance(data, dict) and data.get("available", True):
        toks = _parse_fomo(data)
        s.update(tokens=toks, fetched_at=now, captured_at=data.get("capturedAt"),
                 etag=headers.get("ETag") or headers.get("etag"), upstream=data.get("source"))
        _save_state()
        return {"source": "fomo_trending", "fetched_at": now, "captured_at": data.get("capturedAt"),
                "stale": data.get("source") == "captured", "error": None, "tokens": toks}
    # 401/402/429/5xx/rete: backoff (402 = crediti finiti -> stop fino a domani)
    wait = 86400 if code == 402 else (int(headers.get("Retry-After", 0) or 0) or 600)
    s["backoff_until"] = now + wait
    _save_state()
    return {"source": "fomo_trending", "error": f"HTTP {code} {data if isinstance(data, str) else ''}".strip(),
            "tokens": s.get("tokens") or [], "stale": True, "fetched_at": s.get("fetched_at")}


# ------------------------------------------------------------------ backend 2: SOSTITUTO DexScreener boosts
def _substitute(limit: int) -> dict:
    s = _load_state()["sub"]
    now = time.time()
    if s.get("tokens") is not None and now - s.get("fetched_at", 0) < SUB_POLL_SEC:
        return {"source": "dex_boosts_substitute", "fetched_at": s["fetched_at"], "captured_at": None,
                "stale": False, "error": None, "tokens": s["tokens"]}
    if now < s.get("backoff_until", 0):
        return {"source": "dex_boosts_substitute", "error": "backoff", "tokens": s.get("tokens") or [],
                "stale": True, "fetched_at": s.get("fetched_at")}
    seen, out, errs = set(), [], []
    for url in (DEX_BOOST_TOP, DEX_BOOST_LATEST):
        code, _, data = _get(url)
        if code != 200 or not isinstance(data, list):
            errs.append(f"{url.rsplit('/', 2)[-2]} HTTP {code}")
            continue
        for t in data:
            mint = str(t.get("tokenAddress") or "")
            if t.get("chainId") != "solana" or not B58.match(mint) or mint in seen:
                continue
            seen.add(mint)
            out.append({"mint": mint, "rank": len(out) + 1, "symbol": None, "name": None,
                        "price_usd": None, "mcap_usd": None, "change24h": None, "holders": None,
                        "boost_total": t.get("totalAmount"), "source": "dex_boosts_substitute"})
    if not out:
        s["backoff_until"] = now + 300
        _save_state()
        return {"source": "dex_boosts_substitute", "error": "; ".join(errs) or "vuoto",
                "tokens": s.get("tokens") or [], "stale": True, "fetched_at": s.get("fetched_at")}
    out = out[:limit]
    s.update(tokens=out, fetched_at=now)
    _save_state()
    return {"source": "dex_boosts_substitute", "fetched_at": now, "captured_at": None, "stale": False,
            "error": "; ".join(errs) or None, "tokens": out}


def get_trending(limit: int = 25, allow_substitute: bool = True) -> dict:
    """Mai solleva eccezioni. Ritorna sempre un dict con 'tokens' (lista, eventualmente vuota)."""
    try:
        key = _api_key()
        if key:
            r = _fomo(key, limit)
            if r.get("tokens") or not allow_substitute:
                return r
            sub = _substitute(limit)
            sub["error"] = f"fomo: {r.get('error')} | {sub.get('error') or 'ok'}"
            return sub
        if allow_substitute:
            r = _substitute(limit)
            r["note"] = "nessuna FOMOAPI_KEY: uso il SOSTITUTO DexScreener boosts, non Fomo"
            return r
        return {"source": "none", "error": "nessuna FOMOAPI_KEY", "tokens": []}
    except Exception as e:  # noqa: BLE001
        return {"source": "none", "error": f"{type(e).__name__}: {e}"[:160], "tokens": []}


async def aget_trending(limit: int = 25, allow_substitute: bool = True) -> dict:
    return await asyncio.to_thread(get_trending, limit, allow_substitute)


if __name__ == "__main__":
    r = get_trending()
    print(json.dumps({k: v for k, v in r.items() if k != "tokens"}, indent=1))
    for t in r["tokens"][:10]:
        print(t["rank"], t["mint"], t.get("symbol"), t["source"])
    print("totale:", len(r["tokens"]))
