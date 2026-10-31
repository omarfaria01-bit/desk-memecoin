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
        # v8.4f (REMARC): 'maximum_fee' e un tetto in unita grezze (1e15), non una percentuale -> 1e+15%.
        # Si leggono solo le tasse: *_bps / basis (punti base), fee_rate / rate / percent (frazione o %).
        toks = set(ks.replace("-", "_").split("_"))
        if ("max" in ks or "epoch" in ks or "amount" in ks or "authority" in ks or toks & {"ts", "time", "timestamp"}) \
                and "bps" not in ks and "basis" not in ks:
            continue
        if "bps" in ks or "basis" in ks:
            best = max(best, f / 100.0)
        elif "rate" in ks or "percent" in ks or ks in ("fee", "transfer_fee"):
            best = max(best, f * 100.0 if f <= 1.0 else f)
    return best if best > 0 else 0.01   # fee presente ma senza un numero leggibile: la si conta comunque


def lp_pct_from_dex(dex_list, pair=None):
    """v8.10: LP bruciata (burn_percent) della pool che il bot usa. Review Claude: prima era il MASSIMO tra tutte le
    pool, quindi una pool secondaria bruciata faceva passare la principale sbloccata. Ora: la pool con id/indirizzo
    == pair; se non si riconosce, il valore PIU BASSO noto (prudente). None = nessun burn_percent (mai 0%)."""
    rows = [d for d in (dex_list or []) if isinstance(d, dict)]
    if pair:
        for d in rows:
            if pair in (d.get("id"), d.get("pair"), d.get("address"), d.get("pool")):
                return float(d["burn_percent"]) if d.get("burn_percent") is not None else None
    pcts = [float(d["burn_percent"]) for d in rows if d.get("burn_percent") is not None]
    return min(pcts) if pcts else None


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
        # v8.4f: authority multisig/programma (PDA, fuori curva) -> solo tag shadow per una futura eccezione
        auth = [a.get("address") for a in ((r.get("transfer_fee_upgradable") or {}).get("authority") or [])
                if isinstance(a, dict) and a.get("address")] if isinstance(r.get("transfer_fee_upgradable"), dict) else []
        try:
            from sources.solkeys import on_curve
        except Exception:  # noqa: BLE001
            on_curve = None
        if auth and on_curve and all(on_curve(a) is False for a in auth):
            flags.append("fee_auth_known")
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
    if not any(res.get(k) is not None for k in ("mintable", "freezable", "closable", "balance_mutable_authority")):
        # review Claude: voce presente ma campi chiave vuoti (token non ancora indicizzato) = sconosciuto, mai 'ok'
        return {"level": "unknown", "why": "GoPlus: token non ancora analizzato (campi vuoti)", "flags": []}
    hard, soft, flags, fee = verdict(res, c)
    out = {"level": "hard" if hard else "ok", "why": "; ".join(hard or soft), "hard": hard, "soft": soft,
           "flags": flags, "fee_pct": fee, "dex": res.get("dex")}
    _cache[mint] = (now, out)
    for k in [k for k, v in _cache.items() if now - v[0] > c["CACHE_SEC"]]:
        _cache.pop(k, None)
    return out
