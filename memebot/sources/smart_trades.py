"""v8.7 (E) - feature dei primi scambi di un token pump.fun dall'endpoint pubblico dei trade (0 crediti Helius).

  https://frontend-api-v3.pump.fun/trades/solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp/{mint}?limit=100[&cursor=..]
  (dal piu recente al piu vecchio, 100 per pagina). Per arrivare ai PRIMI scambi bisogna sfogliare tutto: oltre
  MAX_PAGES pagine il token e troppo attivo/vecchio e le feature restano None ('incompleto').

Feature (solo registrate, nessun effetto sulle decisioni):
  sniper_pct        % dell'offerta comprata nei primi SNIPER_SEC secondi da wallet diversi dal creatore
  first20_sold_pct  quota (in token) gia rivenduta dai primi 20 compratori (creatore escluso)
  dev_first_sell_dt secondi dalla nascita alla prima vendita del creatore (None = non ha venduto)
Idea da bot open source (sniper/early-buyer analysis), logica riscritta da zero."""
from urllib.parse import quote

CHAIN = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
API = f"https://frontend-api-v3.pump.fun/trades/{CHAIN}"
CFG = {"MAX_PAGES": 3, "SNIPER_SEC": 3.0, "FIRST_N": 20, "SUPPLY": 1e9}


def _amt(x):
    try:
        b = x.get("baseAmount") or {}
        return int(b.get("raw") or 0) / 10 ** int(b.get("decimals") or 6)
    except (TypeError, ValueError, AttributeError):
        return 0.0


def compute(trades, creator=None, created_ms=None, cfg=None):
    """Funzione pura sui trade (qualsiasi ordine). Ritorna il dict delle feature."""
    c = dict(CFG, **(cfg or {}))
    tr = sorted((t for t in trades or [] if isinstance(t, dict) and t.get("blockTimeMs")),
                key=lambda t: (int(t["blockTimeMs"]), int(t.get("txIndex") or 0), int(t.get("eventIndex") or 0)))
    if not tr:
        return {"sniper_pct": None, "first20_sold_pct": None, "dev_first_sell_dt": None, "n_trades": 0}
    t0 = float(created_ms) if created_ms else float(tr[0]["blockTimeMs"])
    cr = str(creator or "")
    snip, bought, sold, order, dev_sell = 0.0, {}, {}, [], None
    for t in tr:
        w = str((t.get("trader") or {}).get("address") or "")
        a, side, ts = _amt(t), t.get("side"), float(t["blockTimeMs"])
        if w == cr and cr:
            if side == "sell" and dev_sell is None:
                dev_sell = round((ts - t0) / 1000.0, 1)
            continue
        if side == "buy":
            if w not in bought:
                order.append(w)
                if ts - t0 <= c["SNIPER_SEC"] * 1000:
                    snip += a
                bought[w] = 0.0
            elif ts - t0 <= c["SNIPER_SEC"] * 1000:
                snip += a
            bought[w] += a
        elif side == "sell":
            sold[w] = sold.get(w, 0.0) + a
    first = order[:int(c["FIRST_N"])]
    fb = sum(bought[w] for w in first)
    fs = sum(min(sold.get(w, 0.0), bought[w]) for w in first)
    return {"sniper_pct": round(snip / c["SUPPLY"] * 100, 3),
            "first20_sold_pct": round(fs / fb * 100, 2) if fb > 0 else None,
            "dev_first_sell_dt": dev_sell, "dev_sold": int(dev_sell is not None) if cr else None,
            "n_trades": len(tr), "span_s": round((float(tr[-1]["blockTimeMs"]) - t0) / 1000.0, 1)}


async def fetch(get, mint, creator=None, created_ms=None, cfg=None):
    """get = coroutine url -> json|None (client http del desk con HOST_GAPS). Mai eccezioni."""
    c = dict(CFG, **(cfg or {}))
    out, cur, pages = [], None, 0
    try:
        while pages < int(c["MAX_PAGES"]):
            url = f"{API}/{mint}?limit=100" + (f"&cursor={quote(cur, safe='')}" if cur else "")
            d = await get(url)
            pages += 1
            if not isinstance(d, dict) or not isinstance(d.get("trades"), list):
                return {"ok": False, "why": "risposta non valida", "pages": pages}
            out += d["trades"]
            cur = d.get("cursor")
            if len(d["trades"]) < 100 or not cur:
                r = compute(out, creator, created_ms, c)
                r.update(ok=True, pages=pages)
                return r
        return {"ok": False, "why": f"incompleto (> {pages * 100} scambi)", "pages": pages, "n_trades": len(out)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "why": f"errore {type(e).__name__}", "pages": pages}
