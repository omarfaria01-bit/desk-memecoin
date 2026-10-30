"""v8.4: GoPlus Security (API pubblica, senza chiave) per i token Solana, prima di ogni acquisto.

GET https://api.gopluslabs.io/api/v1/solana/token_security?contract_addresses=<mint>  -> code 1 = ok.
Rigido: authority attive pericolose (balance mutable, closable, freeze, mint), transfer hook, account che nascono
congelati, token non trasferibile, fee di trasferimento >= HARD_FEE_PCT o modificabile dal dev.
Soft: metadati modificabili, fee di trasferimento bassa (entra nel costo andata e ritorno), hook aggiornabile.
Cache 10 minuti per mint, al massimo 1 richiesta ogni MIN_GAP secondi, timeout breve. Errore = 'unknown' (mai ok).
"""
import asyncio
import time

URL = "https://api.gopluslabs.io/api/v1/solana/token_security"
CFG = {"CACHE_SEC": 600, "MIN_GAP": 2.0, "TIMEOUT": 12.0, "RETRIES": 1, "HARD_FEE_PCT": 10.0}
_cache = {}
_lock = [None]
_last = [0.0]


def _on(x):
    if isinstance(x, dict):
        return str(x.get("status", "0")) == "1"
    return str(x) == "1"


def _fee_pct(tf):
    """transfer_fee: {} se assente; altrimenti cerca un valore di fee (bps o %)."""
    if not tf:
        return 0.0
    best = 0.0
    items = tf.items() if isinstance(tf, dict) else enumerate(tf if isinstance(tf, list) else [])
    for k, v in items:
        if isinstance(v, (dict, list)):
            best = max(best, _fee_pct(v))
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        ks = str(k).lower()
        if "bps" in ks or "basis" in ks:
            best = max(best, f / 100.0)
        elif "rate" in ks or "fee" in ks or "percent" in ks:
            best = max(best, f * 100.0 if f <= 1.0 else f)
    return best if best > 0 else 0.01   # fee presente ma senza un numero leggibile: la si conta comunque


def verdict(r, cfg=None):
    """r = result[mint] di GoPlus. -> (hard[], soft[], flags[], fee_pct)."""
    c = dict(CFG, **(cfg or {}))
    hard, soft, flags = [], [], []
    trusted = str(r.get("trusted_token", "0")) == "1"
    for key, txt in (("balance_mutable_authority", "saldi modificabili dal dev"), ("closable", "account chiudibili"),
                     ("freezable", "freeze authority attiva"), ("mintable", "mint authority attiva")):
        if _on(r.get(key)):
            flags.append(key)
            hard.append(f"GoPlus: {txt}")
    if r.get("transfer_hook"):
        flags.append("transfer_hook")
        hard.append("GoPlus: transfer hook attivo")
    if _on(r.get("transfer_hook_upgradable")):
        flags.append("transfer_hook_upgradable")
        soft.append("GoPlus: transfer hook aggiornabile")
    fee = _fee_pct(r.get("transfer_fee"))
    if fee > 0:
        flags.append(f"transfer_fee:{fee:g}")
        (hard if fee >= c["HARD_FEE_PCT"] else soft).append(f"GoPlus: fee di trasferimento {fee:g}%")
    if _on(r.get("transfer_fee_upgradable")):
        flags.append("transfer_fee_upgradable")
        hard.append("GoPlus: fee di trasferimento modificabile dal dev")
    if str(r.get("default_account_state", "1")) == "2":
        flags.append("default_frozen")
        hard.append("GoPlus: i nuovi account nascono congelati")
    if _on(r.get("default_account_state_upgradable")):
        flags.append("default_account_state_upgradable")
        soft.append("GoPlus: stato iniziale degli account modificabile")
    if str(r.get("non_transferable", "0")) == "1":
        flags.append("non_transferable")
        hard.append("GoPlus: token non trasferibile")
    if _on(r.get("metadata_mutable")):
        flags.append("metadata_mutable")
        if not trusted:
            soft.append("GoPlus: metadati modificabili")
    if trusted:
        flags.append("trusted_token")
    return hard, soft, flags, fee


async def check(session, mint, cfg=None, now=None):
    """-> {level: ok|hard|unknown, why, hard, soft, flags, fee_pct, cached}. Non solleva eccezioni."""
    c = dict(CFG, **(cfg or {}))
    now = now or time.time()
    hit = _cache.get(mint)
    if hit and now - hit[0] < c["CACHE_SEC"]:
        return dict(hit[1], cached=True)
    if session is None:
        return {"level": "unknown", "why": "GoPlus: nessuna sessione", "flags": []}
    if _lock[0] is None:
        _lock[0] = asyncio.Lock()
    res = None
    err = "nessuna risposta"
    async with _lock[0]:
        for i in range(int(c["RETRIES"]) + 1):
            wait = c["MIN_GAP"] - (time.time() - _last[0])
            if wait > 0:
                await asyncio.sleep(wait)
            _last[0] = time.time()
            try:
                import aiohttp
                async with session.get(URL, params={"contract_addresses": mint},
                                       timeout=aiohttp.ClientTimeout(total=c["TIMEOUT"])) as r:
                    if r.status != 200:
                        err = f"http {r.status}"
                        continue
                    d = await r.json(content_type=None)
                if not isinstance(d, dict) or str(d.get("code")) != "1":
                    err = f"code {d.get('code') if isinstance(d, dict) else '?'}"
                    continue
                res = (d.get("result") or {}).get(mint)
                if not res:
                    err = "token non trovato"
                    continue
                break
            except Exception as e:  # noqa: BLE001
                err = type(e).__name__
    if not res:
        return {"level": "unknown", "why": f"GoPlus non disponibile ({err})", "flags": []}
    hard, soft, flags, fee = verdict(res, c)
    out = {"level": "hard" if hard else "ok", "why": "; ".join(hard or soft), "hard": hard, "soft": soft,
           "flags": flags, "fee_pct": fee}
    _cache[mint] = (now, out)
    for k in [k for k, v in _cache.items() if now - v[0] > c["CACHE_SEC"]]:
        _cache.pop(k, None)
    return out
