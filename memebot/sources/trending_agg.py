"""
trending_agg.py - v8.3: aggregatore GRATUITO di token Solana in tendenza (PAPER, sola lettura, nessuna chiave).

Fonti (famiglie):
  dex   (PRINCIPALE)  DexScreener API pubblica: token-boosts/top, token-boosts/latest, token-profiles/latest,
                      ads/latest. La pagina web "trending" di DexScreener e dietro Cloudflare (403): il ranking
                      viene RICOSTRUITO dai dati di /tokens/v1 (volume, transazioni, accelerazione 5m vs 1h).
  gecko (secondaria)  GeckoTerminal trending_pools con duration 5m, 1h, 6h (1 pagina ciascuna, ogni 5 minuti).
  pump  (secondaria)  pump.fun frontend-api-v3 /coins/currently-live (pubblico, senza Cloudflare); su 429 pausa 15 min.
                      (king-of-the-hill / top-runners oggi rispondono 404.)
  gmgn  (inattiva)    solo se c'e la chiave personale GMGN (funzione passata dal bot); oggi assente.
  birdeye (v8.4)      /defi/token_trending (mode=selective, 1h) dal piano gratuito Birdeye, tramite la funzione
                      passata dal bot (budget CU e cache sono in sources/birdeye.py); conta per il bonus multi-fonte.

Tutte le richieste passano dalla funzione `get(url, headers)` del bot (Http.get di desk.py): i limiti per sito
(GeckoTerminal 1 chiamata ogni 6 s) sono quindi CONDIVISI con il resto del bot. Mai eccezioni verso il chiamante.
Il trending NON salta nessun filtro: serve solo a decidere COSA analizzare per primo.
"""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

B58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
STATE_FILE = Path(__file__).with_name("trending_agg_state.json")

DEX_LISTS = {
    "dex_boost_top": "https://api.dexscreener.com/token-boosts/top/v1",
    "dex_boost_latest": "https://api.dexscreener.com/token-boosts/latest/v1",
    "dex_profile": "https://api.dexscreener.com/token-profiles/latest/v1",
    "dex_ads": "https://api.dexscreener.com/ads/latest/v1",
}
GECKO_TREND = "https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page=1&duration={d}"
GECKO_DURATIONS = ("5m", "1h", "6h")
PUMP_LIVE = "https://frontend-api-v3.pump.fun/coins/currently-live?limit=50&offset=0&includeNsfw=false"
DEX_TOKENS = "https://api.dexscreener.com/tokens/v1/solana/"
GECKO_HDR = {"Accept": "application/json;version=20230302"}

FAMILY = {"dex_boost_top": "dex", "dex_boost_latest": "dex", "dex_profile": "dex", "dex_ads": "dex",
          "gecko_5m": "gecko", "gecko_1h": "gecko", "gecko_6h": "gecko", "pump_live": "pump", "gmgn": "gmgn",
          "birdeye": "birdeye"}


def _f(x, d=0.0):
    try:
        v = float(x)
        return v if math.isfinite(v) else d
    except (TypeError, ValueError):
        return d


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def parse_dex_list(data, name):
    """Liste DexScreener (boost/profili/ads): [(mint, rank, extra)] solo Solana, primo rank per mint."""
    out, seen = [], set()
    if not isinstance(data, list):
        return out
    for it in data:
        if not isinstance(it, dict) or str(it.get("chainId", "")).lower() != "solana":
            continue
        m = str(it.get("tokenAddress") or "")
        if not B58.match(m) or m in seen:
            continue
        seen.add(m)
        ex = {}
        if name.startswith("dex_boost"):
            ex["boost"] = _f(it.get("totalAmount") or it.get("amount"))
        if name == "dex_profile":
            ex["profile"] = True
            ex["links"] = len(it.get("links") or [])
        out.append((m, len(out) + 1, ex))
    return out


def parse_gecko(data):
    out, seen = [], set()
    items = data.get("data", []) if isinstance(data, dict) else []
    for p in items:
        rel = p.get("relationships") or {}
        bid = (((rel.get("base_token") or {}).get("data")) or {}).get("id") or ""
        m = bid.split("_", 1)[1] if bid.startswith("solana_") else bid
        if not B58.match(m) or m in seen:
            continue
        seen.add(m)
        name = str((p.get("attributes") or {}).get("name", "?")).split("/")[0].strip()
        out.append((m, len(out) + 1, {"name": name}))
    return out


def parse_pump(data, now=None):
    """pump.fun live: solo token con scambi negli ultimi 15 min e market cap >= $15k, ordinati per market cap."""
    now = now or time.time()
    rows = []
    if not isinstance(data, list):
        return []
    for c in data:
        if not isinstance(c, dict):
            continue
        m = str(c.get("mint") or "")
        lt = _f(c.get("last_trade_timestamp")) / 1000
        mc = _f(c.get("usd_market_cap"))
        if B58.match(m) and mc >= 15000 and now - lt < 900:
            rows.append((mc, m, str(c.get("symbol") or "?")[:12]))
    rows.sort(reverse=True)
    return [(m, i + 1, {"symbol": s, "mcap": mc}) for i, (mc, m, s) in enumerate(rows)]


def pair_metrics(pairs):
    """Coppia principale (liquidita max) da /tokens/v1 -> metriche per score e prefiltro."""
    pairs = [p for p in (pairs or []) if isinstance(p, dict)]
    if not pairs:
        return None
    p = max(pairs, key=lambda x: _f((x.get("liquidity") or {}).get("usd")))
    tx = p.get("txns") or {}
    vol = p.get("volume") or {}
    chg = p.get("priceChange") or {}
    info = p.get("info") or {}
    h1, m5 = tx.get("h1") or {}, tx.get("m5") or {}
    created = _f(p.get("pairCreatedAt")) / 1000
    socials = {str(s.get("type", "")).lower() for s in (info.get("socials") or []) if isinstance(s, dict)}
    return {"symbol": str((p.get("baseToken") or {}).get("symbol", "?"))[:12],
            "liq": _f((p.get("liquidity") or {}).get("usd")),
            "vol_m5": _f(vol.get("m5")), "vol_h1": sum(_f(((x.get("volume") or {}).get("h1"))) for x in pairs),
            "vol_h1_main": _f(vol.get("h1")), "vol_h6": _f(vol.get("h6")),
            "buys_m5": int(_f(m5.get("buys"))), "sells_m5": int(_f(m5.get("sells"))),
            "buys_h1": int(_f(h1.get("buys"))), "sells_h1": int(_f(h1.get("sells"))),
            "chg_m5": _f(chg.get("m5")), "chg_h1": _f(chg.get("h1")), "chg_h6": _f(chg.get("h6")),
            "mcap": _f(p.get("marketCap") or p.get("fdv")),
            "age_min": (time.time() - created) / 60 if created else None,
            "boost_active": int(_f((p.get("boosts") or {}).get("active"))),
            "social": bool(socials & {"twitter", "x", "telegram"}) or bool(info.get("websites")),
            "pair": str(p.get("pairAddress", ""))}


def trending_score(n_fam, ranks, mx):
    """0..1: fonti (famiglie) che lo citano, rank medio, volume, transazioni, accelerazione volume e tx (5m vs 1h)."""
    s_src = _clamp(n_fam / 3)
    s_rank = sum(_clamp(1 - (r - 1) / 50) for r in ranks) / len(ranks) if ranks else 0.0
    if not mx:
        return round(0.25 * s_src + 0.10 * s_rank, 4)
    vh1 = max(mx["vol_h1_main"], 1.0)
    tx1 = mx["buys_h1"] + mx["sells_h1"]
    tx5 = mx["buys_m5"] + mx["sells_m5"]
    s_vol = _clamp((math.log10(vh1) - 4) / 2)              # $10k -> 0, $1M -> 1
    s_tx = _clamp((math.log10(max(tx1, 1)) - 2) / 1.7)     # 100 -> 0, 5000 -> 1
    act = min(1.0, 3 * s_vol)                               # accelerazione conta solo con volume vero
    s_acc = _clamp((mx["vol_m5"] * 12 / vh1 - 0.5) / 1.5) * act   # 1x la media oraria -> 0.33
    s_txa = _clamp((tx5 * 12 / max(tx1, 1) - 0.5) / 1.5) * act
    return round(0.25 * s_src + 0.10 * s_rank + 0.25 * s_vol + 0.15 * s_tx + 0.15 * s_acc + 0.10 * s_txa, 4)


class TrendingAgg:
    """refresh(get, cooldown=f): f(host) -> True se quel sito ha dato 429 da poco (allora si usa solo la cache)."""
    def __init__(self, gmgn_fetch=None, gecko_every=300, pump_every=300, enrich_max=90, birdeye_fetch=None):
        self.gmgn_fetch = gmgn_fetch      # async () -> [(mint, rank, extra)] oppure None (nessuna chiave)
        self.birdeye_fetch = birdeye_fetch  # v8.4: async () -> [(mint, rank, extra)] (cache e budget nel chiamato)
        self.gecko_every, self.pump_every, self.enrich_max = gecko_every, pump_every, enrich_max
        self.cache = {}                   # nome lista -> (ts, [(mint, rank, extra)])
        self.pump_pause_until = 0.0
        self.last = {"ts": 0, "tokens": [], "counts": {}}

    async def _list(self, get, name, url, every, parser, headers=None):
        c = self.cache.get(name)
        if c and time.time() - c[0] < every:
            return c[1]
        try:
            data = await get(url, headers=headers)
        except Exception:  # noqa: BLE001
            data = None
        rows = parser(data) if data is not None else None
        if rows is None:
            return c[1] if c and time.time() - c[0] < 3 * every else []
        self.cache[name] = (time.time(), rows)
        return rows

    async def refresh(self, get, now=None, cooldown=None):
        """Un giro: ritorna lista ordinata per trending_score [{mint, symbol, sources, families, n_fam, ranks,
        trending_score, boost, profile, metrics}]. Mai eccezioni."""
        now = now or time.time()
        lists = {}
        for name, url in DEX_LISTS.items():
            lists[name] = await self._list(get, name, url, 110, lambda d, n=name: parse_dex_list(d, n))
        for d in GECKO_DURATIONS:
            if cooldown is not None and cooldown("api.geckoterminal.com"):   # 429 recente: solo cache, nessuna chiamata
                c = self.cache.get(f"gecko_{d}")
                lists[f"gecko_{d}"] = c[1] if c and now - c[0] < 3 * self.gecko_every else []
                continue
            lists[f"gecko_{d}"] = await self._list(get, f"gecko_{d}", GECKO_TREND.format(d=d), self.gecko_every,
                                                   parse_gecko, GECKO_HDR)
        if now >= self.pump_pause_until:
            before = self.cache.get("pump_live")
            lists["pump_live"] = await self._list(get, "pump_live", PUMP_LIVE, self.pump_every, parse_pump)
            if not lists["pump_live"] and (before is None or now - before[0] >= self.pump_every):
                self.pump_pause_until = now + 900   # 429 / errore: 15 minuti di pausa
        else:
            lists["pump_live"] = (self.cache.get("pump_live") or (0, []))[1]
        if self.gmgn_fetch is not None:
            try:
                lists["gmgn"] = await self.gmgn_fetch() or []
            except Exception:  # noqa: BLE001
                lists["gmgn"] = []
        if self.birdeye_fetch is not None:
            try:
                lists["birdeye"] = await self.birdeye_fetch() or []
            except Exception:  # noqa: BLE001
                lists["birdeye"] = []
        agg = {}
        for name, rows in lists.items():
            for m, rk, ex in rows:
                a = agg.setdefault(m, {"mint": m, "sources": {}, "boost": 0.0, "profile": False, "symbol": None})
                a["sources"][name] = rk
                a["boost"] = max(a["boost"], _f(ex.get("boost")))
                a["profile"] = a["profile"] or bool(ex.get("profile"))
                a["symbol"] = a["symbol"] or ex.get("symbol") or ex.get("name")
        # arricchimento DexScreener (dati pubblici, 30 mint per chiamata): prima i piu citati
        order = sorted(agg.values(), key=lambda a: (-len({FAMILY[s] for s in a["sources"]}),
                                                    min(a["sources"].values())))
        mints = [a["mint"] for a in order[: self.enrich_max]]
        mx = {}
        for i in range(0, len(mints), 30):
            ch = mints[i:i + 30]
            try:
                data = await get(DEX_TOKENS + ",".join(ch))
            except Exception:  # noqa: BLE001
                data = None
            by = {}
            for p in (data if isinstance(data, list) else (data or {}).get("pairs") or []):
                if isinstance(p, dict):
                    b = (p.get("baseToken") or {}).get("address")
                    if b in ch:
                        by.setdefault(b, []).append(p)
            for b, ps in by.items():
                mx[b] = pair_metrics(ps)
        out = []
        for a in agg.values():
            fams = sorted({FAMILY[s] for s in a["sources"]})
            ranks = list(a["sources"].values())
            m = mx.get(a["mint"])
            out.append({"mint": a["mint"], "symbol": (m or {}).get("symbol") or a["symbol"] or "?",
                        "sources": sorted(a["sources"]), "families": fams, "n_fam": len(fams),
                        "ranks": a["sources"], "boost": a["boost"],
                        "profile": a["profile"], "paid": a["boost"] > 0 or a["profile"] or "dex_ads" in a["sources"],
                        "trending_score": trending_score(len(fams), ranks, m), "metrics": m, "t": now})
        out.sort(key=lambda x: -x["trending_score"])
        counts = {k: len(v) for k, v in lists.items()}
        counts["unici"] = len(out)
        counts["multi_fonte"] = sum(1 for x in out if x["n_fam"] >= 2)
        counts["arricchiti"] = len(mx)
        self.last = {"ts": now, "tokens": out, "counts": counts}
        try:
            STATE_FILE.write_text(json.dumps({"ts": now, "counts": counts, "top": out[:40]}, default=str))
        except OSError:
            pass
        return out
