"""
kol_holders.py - quali KOL (whitelist) hanno comprato un mint, quando, quanto, e se hanno gia' venduto.
PAPER / sola lettura. Solo stdlib. RPC pubblica gratuita. Nessun wallet/chiave/.env.

Come funziona (per mint):
  1) getProgramAccounts (Token + Token-2022) filtrato per mint, con dataSlice -> owner+amount di
     TUTTI i token account del mint (1-2 chiamate). Interseca con i wallet della whitelist.
  2) Solo per i KOL trovati (max KOL_MAX_TX_KOLS): getSignaturesForAddress sul loro token account e
     getTransaction (max KOL_TX_PER_KOL) -> primo acquisto, SOL spesi, quantita' venduta.
  3) Stato: holding / partial_sold / sold (account aperto con saldo 0).
LIMITE NOTO: un KOL che ha venduto TUTTO e CHIUSO il token account non e' piu' visibile (per noi
"assente"). Per questo, se un KOL whitelisted e' gia' presente nella lista "seen" del modulo e poi
sparisce, viene marcato sold_closed (vale solo se lo avevamo visto prima).

Uso:
    from sources.kol_holders import kol_check, kol_bonus, KolLedger
    r = kol_check(mint)            # mai eccezioni, cache 10 min per mint
    b = kol_bonus(r, cfg)          # (bonus, tags)
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
WHITELIST = HERE / "kol_whitelist.json"
LEDGER = HERE / "kol_ledger.json"
SEEN = HERE / "kol_seen.json"
RPC = os.environ.get("KOL_RPC", "https://api.mainnet-beta.solana.com")
PROGRAMS = ("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
TIMEOUT = 20
CACHE_SEC = 600
MAX_TX_KOLS = 5          # KOL per mint di cui leggiamo lo storico
TX_PER_KOL = 8           # transazioni max per KOL
CALL_GAP = 0.15          # pausa tra chiamate RPC (rispetta il limite della RPC pubblica)
MAX_HOLDERS = 60000      # oltre: token troppo grande/vecchio, salto (risposta enorme)
INCLUDE_PROPOSED = os.environ.get("KOL_INCLUDE_PROPOSED") == "1"
TIERS = ["S", "A", "B", "off"]                       # retrocessione: S -> A -> B -> off
TIER_MULT = {"S": 1.25, "A": 1.0, "B": 0.5}          # x KOL_BONUS / KOL_THESIS_BONUS (poi tetto TOTAL_BONUS_CAP)

_cache: dict = {}
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    s = ""
    while n:
        n, r = divmod(n, 58)
        s = _B58[r] + s
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + s


def _rpc(method, params, _retry=2):
    time.sleep(CALL_GAP)
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(RPC, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            d = json.loads(r.read())
        return d.get("result"), d.get("error")
    except Exception as e:  # noqa: BLE001
        if "429" in str(e) and _retry > 0:   # RPC pubblica condivisa col bot: aspetta e riprova
            time.sleep(2.5 * (3 - _retry))
            return _rpc(method, params, _retry - 1)
        return None, str(e)[:120]


def _jload(p, default):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


def _jsave(p, obj):
    try:
        tmp = Path(p).with_suffix(".tmp")
        tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False))
        tmp.replace(p)
    except OSError:
        pass


def load_whitelist() -> dict:
    """wallet -> {handle, tier(effettivo dopo il Learner), status}"""
    wl = _jload(WHITELIST, {"kols": []})
    led = KolLedger()
    out = {}
    for k in wl.get("kols", []):
        st = k.get("status", "active")
        if st == "proposed" and not INCLUDE_PROPOSED:
            continue
        if st not in ("active", "proposed"):
            continue
        tier = led.effective_tier(k["handle"], k.get("tier", "B"))
        for w in k.get("wallets", []):
            out[w["address"]] = {"handle": k["handle"], "tier": tier, "base_tier": k.get("tier"),
                                 "verified": bool(w.get("verified")), "weight": float(k.get("weight", 1.0))}
    return out


def _holders(mint):
    """owner -> (token_account, raw_amount)"""
    res, errs = {}, []
    for prog in PROGRAMS:
        r, e = _rpc("getProgramAccounts", [prog, {"encoding": "base64", "dataSlice": {"offset": 32, "length": 40},
                                                  "filters": [{"memcmp": {"offset": 0, "bytes": mint}}]}])
        if e:
            errs.append(f"{prog[:5]}: {e}")
            continue
        for a in r or []:
            raw = base64.b64decode(a["account"]["data"][0])
            if len(raw) < 40:
                continue
            res[b58(raw[:32])] = (a["pubkey"], int.from_bytes(raw[32:40], "little"))
        if len(res) > MAX_HOLDERS:
            break
    return res, errs


def _history(owner, token_acc, mint):
    sigs, e = _rpc("getSignaturesForAddress", [token_acc, {"limit": 40}])
    if e or not sigs:
        return {"error": e}
    sigs = [s for s in sigs if not s.get("err")]
    buys, sells, first = [], [], None
    # i piu' vecchi per primi (contengono l'acquisto), poi i piu' recenti (vendite)
    pick = sigs[-(TX_PER_KOL // 2):] + sigs[:TX_PER_KOL - TX_PER_KOL // 2]
    seen = set()
    for s in pick:
        if s["signature"] in seen:
            continue
        seen.add(s["signature"])
        tx, e = _rpc("getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
        if e or not tx or not tx.get("meta"):
            continue
        m = tx["meta"]

        def tok(lst):
            return sum(float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
                       for b in lst or [] if b.get("owner") == owner and b.get("mint") == mint)
        dt = tok(m.get("postTokenBalances")) - tok(m.get("preTokenBalances"))
        keys = [k["pubkey"] if isinstance(k, dict) else k for k in tx["transaction"]["message"]["accountKeys"]]
        dsol = 0.0
        if owner in keys:
            i = keys.index(owner)
            dsol = (m["postBalances"][i] - m["preBalances"][i]) / 1e9
        ev = {"t": s.get("blockTime"), "tokens": round(dt, 2), "sol": round(dsol, 4), "sig": s["signature"]}
        if dt > 0:
            buys.append(ev)
        elif dt < 0:
            sells.append(ev)
    if buys:
        first = min(b["t"] or 0 for b in buys)
    return {"first_buy_ts": first, "sol_spent": round(-sum(b["sol"] for b in buys if b["sol"] < 0), 4),
            "tokens_bought": round(sum(b["tokens"] for b in buys), 2),
            "tokens_sold": round(-sum(x["tokens"] for x in sells), 2), "n_sigs": len(sigs),
            "sampled": len(seen), "truncated": len(sigs) > TX_PER_KOL}


def kol_check(mint: str, with_history: bool = True) -> dict:
    now = time.time()
    c = _cache.get(mint)
    if c and now - c["fetched_at"] < CACHE_SEC:
        return c
    out = {"mint": mint, "fetched_at": now, "kols": [], "error": None, "holders_total": 0}
    try:
        wl = load_whitelist()
        hold, errs = _holders(mint)
        out["holders_total"] = len(hold)
        if not hold and errs:
            out["error"] = "; ".join(errs)
            return out
        sup, _ = _rpc("getTokenSupply", [mint])
        supply = int((sup or {}).get("value", {}).get("amount") or 0)
        dec = int((sup or {}).get("value", {}).get("decimals") or 0)
        seen = _jload(SEEN, {})
        mseen = seen.setdefault(mint, {})
        n_hist = 0
        for owner, meta in wl.items():
            if owner in hold:
                acc, amt = hold[owner]
                status = "holding" if amt > 0 else "sold"
                row = {"handle": meta["handle"], "tier": meta["tier"], "weight": meta["weight"], "wallet": owner,
                       "verified": meta["verified"],
                       "token_account": acc, "amount": amt / 10 ** dec if dec else amt,
                       "pct_supply": round(100 * amt / supply, 3) if supply else None, "status": status}
                if with_history and n_hist < MAX_TX_KOLS:
                    n_hist += 1
                    h = _history(owner, acc, mint)
                    row.update(h)
                    # account a saldo 0 senza vendita visibile e mai visto in holding: e' vuoto (spam/dust), NON exit
                    if status == "sold" and not h.get("tokens_sold") and owner not in mseen:
                        row["status"] = "empty"
                    if status == "holding" and h.get("tokens_sold", 0) > 0:
                        row["status"] = "partial_sold"
                        row["sold_frac"] = round(h["tokens_sold"] / max(h.get("tokens_bought") or 0, h["tokens_sold"] + row["amount"]), 2)
                elif status == "sold" and owner not in mseen:
                    row["status"] = "empty"  # senza storico non possiamo dire che abbia venduto
                if amt > 0:
                    mseen[owner] = now
                out["kols"].append(row)
            elif owner in mseen:  # visto prima, ora sparito -> ha venduto e chiuso l'account
                out["kols"].append({"handle": meta["handle"], "tier": meta["tier"], "wallet": owner,
                                    "weight": meta["weight"], "status": "sold_closed", "amount": 0})
        if len(seen) > 2000:  # pulizia
            for k in sorted(seen, key=lambda m: max(seen[m].values() or [0]))[:500]:
                seen.pop(k, None)
        _jsave(SEEN, seen)
        if errs:
            out["error"] = "; ".join(errs)
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"[:160]
    _cache[mint] = out
    return out


async def akol_check(mint: str, with_history: bool = True) -> dict:
    return await asyncio.to_thread(kol_check, mint, with_history)


def kol_bonus(r: dict, cfg: dict) -> tuple[float, list[str]]:
    """Tier S = 1.25x, A = bonus pieno, B = meta', non in lista = 0. KOL che ha venduto = niente bonus;
    se TUTTI i KOL whitelisted presenti hanno gia' venduto/stanno vendendo -> piccola penalita'."""
    full = float(cfg.get("KOL_BONUS", 0.03))
    pen = float(cfg.get("KOL_SOLD_PENALTY", 0.02))
    best, tags, sellers, holders = 0.0, [], 0, 0
    for k in r.get("kols", []):
        if k["status"] in ("sold", "sold_closed") or (k["status"] == "partial_sold" and k.get("sold_frac", 1) >= 0.5):
            sellers += 1
            tags.append(f"kol_sold:{k['handle']}")
            continue
        mult = TIER_MULT.get(k.get("tier"), 0.0) * float(k.get("weight", 1.0))   # es. rasmr 0.75
        # anti-airdrop: i wallet dei KOL ricevono token spam gratis. Conta solo un ACQUISTO vero.
        if k["status"] == "empty":
            continue
        if (k.get("sol_spent") or 0) < float(cfg.get("KOL_MIN_SOL", 0.1)):
            # storico campionato (truncated): l'acquisto potrebbe essere piu' vecchio -> tag diverso, comunque 0 bonus
            tags.append(f"kol_{'buyUnknown' if k.get('truncated') else 'noBuy'}:{k['handle']}")
            continue
        if mult:
            holders += 1
            tags.append(f"kol:{k['handle']}:{k['tier']}")
            best = max(best, full * mult)
    if not holders and sellers:
        return -pen, tags
    return best, tags


def _base_info(handle):
    for k in _jload(WHITELIST, {"kols": []}).get("kols", []):
        if k.get("handle") == handle:
            return k.get("tier", "B"), bool(k.get("promotable") or k.get("user_recommended"))
    return "B", False


class KolLedger:
    """Il Learner registra l'esito dei trade (anche ombra) taggati con un KOL. Retrocessione automatica:
    dopo >=MIN_N esiti, winrate < 35% o PnL medio < -5%  ->  scende di un tier (S -> A -> B -> off).
    Ripromozione solo con evidenza: >=MIN_N esiti successivi con winrate >= 55% e PnL medio > 0.
    I KOL consigliati dall'utente in tier B ("promotable") possono salire fino ad A con la stessa regola;
    nessuno sale a S automaticamente."""
    MIN_N = 6
    WINDOW = 20

    def __init__(self, path=LEDGER):
        self.path = path
        self.d = _jload(path, {})

    def record(self, handle, pnl_pct, mint=None, shadow=False):
        e = self.d.setdefault(handle, {"hist": [], "demote": 0})
        base, promotable = _base_info(handle)
        bi = TIERS.index(base) if base in TIERS else len(TIERS) - 1
        # promozione oltre il tier base: solo KOL "promotable", e mai sopra A (S lo decide solo l'utente)
        min_demote = (TIERS.index("A") - bi) if (promotable and bi > TIERS.index("A")) else 0
        e["hist"] = (e["hist"] + [{"t": int(time.time()), "pnl": round(float(pnl_pct), 2), "mint": mint,
                                   "shadow": bool(shadow)}])[-self.WINDOW:]
        h = [x["pnl"] for x in e["hist"]]
        if len(h) >= self.MIN_N:
            wr = sum(p > 0 for p in h) / len(h)
            avg = sum(h) / len(h)
            if (wr < 0.35 or avg < -5) and e["demote"] < len(TIERS) - 1:
                e["demote"] += 1
                e["hist"] = []          # nuovo periodo di prova dopo ogni cambio
                e["last_change"] = f"retrocesso (wr {wr:.0%}, media {avg:+.1f}%)"
            elif wr >= 0.55 and avg > 0 and e["demote"] > min_demote:
                e["demote"] -= 1
                e["hist"] = []
                e["last_change"] = f"ripromosso (wr {wr:.0%}, media {avg:+.1f}%)"
        _jsave(self.path, self.d)
        return e

    def effective_tier(self, handle, base):
        lvl = TIERS.index(base) if base in TIERS else len(TIERS) - 1
        return TIERS[max(0, min(lvl + (self.d.get(handle) or {}).get("demote", 0), len(TIERS) - 1))]


if __name__ == "__main__":
    import sys
    m = sys.argv[1] if len(sys.argv) > 1 else "3ApVr1xphYMpi9dn3rpqU4hwvpmCQDVwxET1rdCDpump"
    t = time.time()
    r = kol_check(m)
    print(json.dumps(r, indent=1)[:3000])
    print("bonus:", kol_bonus(r, {}), f"{time.time() - t:.1f}s")


def tier_of(handle: str) -> str | None:
    """handle X (con o senza @) -> tier effettivo ("S"/"A"/"B"/"off") o None se non in whitelist attiva."""
    h = (handle or "").lstrip("@").lower()
    led = KolLedger()
    for k in _jload(WHITELIST, {"kols": []}).get("kols", []):
        if k.get("status", "active") == "proposed" and not INCLUDE_PROPOSED:
            continue
        names = {str(k.get("handle", "")).lower(), str(k.get("x") or "").lstrip("@").lower()}
        names |= {str(a).split(" ")[0].lstrip("@").lower() for a in k.get("aka", [])}
        if h in names:
            return led.effective_tier(k["handle"], k.get("tier", "B"))
    return None


def weight_of(handle: str) -> float:
    """Peso extra del KOL (default 1.0; es. rasmr 0.75 'user: less confident')."""
    h = (handle or "").lstrip("@").lower()
    for k in _jload(WHITELIST, {"kols": []}).get("kols", []):
        names = {str(k.get("handle", "")).lower(), str(k.get("x") or "").lstrip("@").lower()}
        names |= {str(a).split(" ")[0].lstrip("@").lower() for a in k.get("aka", [])}
        if h in names:
            return float(k.get("weight", 1.0))
    return 0.0
