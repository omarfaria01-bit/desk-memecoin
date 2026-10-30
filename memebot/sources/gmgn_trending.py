"""
gmgn_trending.py - token Solana in trending su GMGN per lo Scout di Desk Memecoin (PAPER, sola lettura).
Stessa interfaccia di fomo_trending.py: get_trending() / aget_trending() -> {"source", "tokens", ...}. Solo stdlib.

Fonte: API UFFICIALE GMGN OpenAPI  GET https://openapi.gmgn.ai/v1/market/rank  (chain=sol)
  - NON il sito gmgn.ai: le sue API web (/defi/quotation/..., /api/v1/rank/...) sono dietro una challenge
    Cloudflare ("Just a moment...", 403). Non la aggiriamo.
  - Auth: header X-APIKEY + timestamp (unix s) + client_id (UUID v4 nuovo a ogni richiesta).
  - Chiave: se esiste GMGN_API_KEY (env) o ~/.config/desk/gmgn_api_key, usa quella (chiave personale
    gratuita, creata su gmgn.ai/ai caricando una chiave pubblica Ed25519 - serve login GMGN).
    Altrimenti usa la chiave PUBBLICA read-only documentata da GMGN per agenti/demo
    (gmgn_solbscbaseethmonadtron). E' condivisa da tutti: rate limit basso (X-RateLimit-Limit: 5),
    pensata per test, puo' essere revocata in qualsiasi momento.
  - Solo IPv4 (lo dice la doc GMGN): forziamo la risoluzione IPv4.
Polling: cache GMGN_POLL_SEC (default 180 s, clamp 120-300), backoff su 401/403/429/5xx.
Fallback (etichettato SOSTITUTO): GeckoTerminal trending_pools Solana (gratis, senza chiave), cache 300 s.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

URL = "https://openapi.gmgn.ai/v1/market/rank"
GECKO = "https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page=1"
PUBLIC_KEY = "gmgn_solbscbaseethmonadtron"
TIMEOUT = 10
POLL = max(120, min(300, int(os.environ.get("GMGN_POLL_SEC", "180"))))
SUB_POLL = 300
INTERVAL = os.environ.get("GMGN_INTERVAL", "1h")          # 1m | 5m | 1h | 6h | 24h
FILTERS = ("not_honeypot", "renounced")                    # pre-filtro GMGN (i nostri filtri restano tutti)
B58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
STATE_FILE = Path(__file__).with_name("gmgn_trending_state.json")
UA = "DeskMemecoin-paper/1.0 (+read-only scout)"
_mem: dict = {}


def _key():
    k = os.environ.get("GMGN_API_KEY")
    if k:
        return k.strip(), "personal"
    try:
        k = (Path.home() / ".config" / "desk" / "gmgn_api_key").read_text().strip()
        if k:
            return k, "personal"
    except OSError:
        pass
    return PUBLIC_KEY, "public_demo"


def _state():
    if not _mem:
        try:
            _mem.update(json.loads(STATE_FILE.read_text()))
        except (OSError, ValueError):
            pass
        _mem.setdefault("gmgn", {})
        _mem.setdefault("sub", {})
    return _mem


def _save():
    try:
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_mem))
        tmp.replace(STATE_FILE)
    except OSError:
        pass


_orig_gai = socket.getaddrinfo


def _get(url, headers=None, ipv4=False):
    h = {"User-Agent": UA, "Accept": "application/json"}
    h.update(headers or {})
    if ipv4:   # GMGN accetta solo IPv4
        def _v4(host, port, family=0, type=0, proto=0, flags=0):
            return _orig_gai(host, port, socket.AF_INET, type, proto, flags)
        socket.getaddrinfo = _v4
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=TIMEOUT) as r:
            return r.status, dict(r.headers), json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), None
    except Exception as e:  # noqa: BLE001
        return 0, {}, str(e)[:120]
    finally:
        socket.getaddrinfo = _orig_gai


def _f(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _parse(d):
    data = (d or {}).get("data") or {}
    if isinstance(data, dict) and "rank" not in data:
        data = data.get("data") or {}
    out = []
    for i, t in enumerate((data or {}).get("rank") or []):
        mint = str(t.get("address") or "")
        if t.get("chain", "sol") != "sol" or not B58.match(mint):
            continue
        out.append({
            "mint": mint, "rank": i + 1, "symbol": t.get("symbol"), "name": t.get("name"),
            "price_usd": _f(t.get("price")), "mcap_usd": _f(t.get("market_cap")),
            "change24h": None, "change1h": _f(t.get("price_change_percent1h")),
            "holders": t.get("holder_count"), "liquidity_usd": _f(t.get("liquidity")),
            "volume_usd": _f(t.get("volume")), "age_h": round((time.time() - t["open_timestamp"]) / 3600, 2)
            if t.get("open_timestamp") else None,
            # metadati GMGN: SOLO informativi/tag, non sostituiscono i nostri filtri
            "gmgn": {k: t.get(k) for k in ("hot_level", "smart_degen_count", "renowned_count", "bundler_rate",
                                            "rat_trader_amount_rate", "top_10_holder_rate", "is_wash_trading",
                                            "sniper_count", "launchpad_platform", "dexscr_ad")},
            "source": "gmgn_trending",
        })
    return out


def _gmgn(limit):
    s = _state()["gmgn"]
    now = time.time()
    if s.get("tokens") is not None and now - s.get("fetched_at", 0) < POLL:
        return {"source": "gmgn_trending", "fetched_at": s["fetched_at"], "captured_at": None, "stale": False,
                "error": None, "key": s.get("key"), "tokens": s["tokens"][:limit]}
    if now < s.get("backoff_until", 0):
        return {"source": "gmgn_trending", "error": "backoff", "tokens": (s.get("tokens") or [])[:limit],
                "stale": True, "fetched_at": s.get("fetched_at")}
    key, kind = _key()
    q = [("chain", "sol"), ("interval", INTERVAL), ("limit", str(min(100, max(limit, 20)))),
         ("order_by", "default"), ("direction", "desc")] + [("filters", f) for f in FILTERS] + \
        [("timestamp", str(int(now))), ("client_id", str(uuid.uuid4()))]
    code, hdr, d = _get(URL + "?" + urllib.parse.urlencode(q), {"X-APIKEY": key}, ipv4=True)
    if code == 200 and isinstance(d, dict) and d.get("code", 0) == 0:
        toks = _parse(d)
        if toks:
            s.update(tokens=toks, fetched_at=now, key=kind, ratelimit_remaining=hdr.get("x-ratelimit-remaining"))
            s.pop("fails", None)
            _save()
            return {"source": "gmgn_trending", "fetched_at": now, "captured_at": None, "stale": False,
                    "error": None, "key": kind, "tokens": toks[:limit]}
    s["fails"] = s.get("fails", 0) + 1
    ra = int(hdr.get("Retry-After") or hdr.get("retry-after") or 0)
    s["backoff_until"] = now + (ra or min(1800, 60 * 2 ** min(s["fails"], 5)))
    _save()
    err = f"HTTP {code}" + (f" {d}" if isinstance(d, str) else (f" code={d.get('code')} {d.get('message') or d.get('error')}" if isinstance(d, dict) else ""))
    return {"source": "gmgn_trending", "error": err[:160], "tokens": (s.get("tokens") or [])[:limit],
            "stale": True, "fetched_at": s.get("fetched_at"), "key": kind}


def _gecko(limit):
    s = _state()["sub"]
    now = time.time()
    if s.get("tokens") is not None and now - s.get("fetched_at", 0) < SUB_POLL:
        return {"source": "gecko_trending_substitute", "fetched_at": s["fetched_at"], "stale": False,
                "error": None, "tokens": s["tokens"][:limit]}
    if now < s.get("backoff_until", 0):
        return {"source": "gecko_trending_substitute", "error": "backoff", "tokens": (s.get("tokens") or [])[:limit],
                "stale": True}
    code, _, d = _get(GECKO, {"Accept": "application/json;version=20230302"})
    out = []
    for p in ((d or {}).get("data") or []) if isinstance(d, dict) else []:
        rid = (((p.get("relationships") or {}).get("base_token") or {}).get("data") or {}).get("id", "")
        mint = rid.split("_", 1)[-1]
        if B58.match(mint) and mint not in {o["mint"] for o in out}:
            a = p.get("attributes") or {}
            out.append({"mint": mint, "rank": len(out) + 1, "symbol": (a.get("name") or "").split(" / ")[0],
                        "name": a.get("name"), "price_usd": _f(a.get("base_token_price_usd")),
                        "mcap_usd": _f(a.get("market_cap_usd") or a.get("fdv_usd")), "change24h": None,
                        "holders": None, "source": "gecko_trending_substitute"})
    if not out:
        s["backoff_until"] = now + 300
        _save()
        return {"source": "gecko_trending_substitute", "error": f"HTTP {code}", "tokens": (s.get("tokens") or [])[:limit],
                "stale": True}
    s.update(tokens=out, fetched_at=now)
    _save()
    return {"source": "gecko_trending_substitute", "fetched_at": now, "stale": False, "error": None, "tokens": out[:limit]}


def get_trending(limit: int = 25, allow_substitute: bool = True) -> dict:
    """Mai eccezioni. Se GMGN non risponde e non c'e' cache, usa il SOSTITUTO GeckoTerminal (bonus 0)."""
    try:
        r = _gmgn(limit)
        if r.get("tokens") or not allow_substitute:
            return r
        sub = _gecko(limit)
        sub["error"] = f"gmgn: {r.get('error')} | {sub.get('error') or 'ok'}"
        return sub
    except Exception as e:  # noqa: BLE001
        return {"source": "none", "error": f"{type(e).__name__}: {e}"[:160], "tokens": []}


async def aget_trending(limit: int = 25, allow_substitute: bool = True) -> dict:
    return await asyncio.to_thread(get_trending, limit, allow_substitute)


if __name__ == "__main__":
    r = get_trending()
    print(json.dumps({k: v for k, v in r.items() if k != "tokens"}, indent=1))
    for t in r["tokens"][:10]:
        print(t["rank"], t["mint"], t.get("symbol"), t.get("age_h"), "h", t.get("mcap_usd"), (t.get("gmgn") or {}).get("smart_degen_count"), t["source"])
    print("totale:", len(r["tokens"]))
