import re
P = "/workspace/memebot/desk.py"
s = open(P).read()
def rep(old, new, cnt=1):
    global s
    n = s.count(old)
    assert n == cnt, (n, old[:80])
    s = s.replace(old, new)

# ---- CFG
rep('''    "SOFT_MAX_FLAGS": 3,              # 3 o piu segnali soft insieme = scarto
''', '''    "SOFT_MAX_FLAGS": 3,              # 3 o piu segnali soft insieme = scarto
    # v7: cluster di wallet collegati (tipo Bubblemaps) da dati on-chain GRATUITI (RPC pubblica Solana), solo sui
    # candidati gia approvati dall'Analyst. Graduato: segnale soft forte; scarto solo nei casi estremi.
    "CLUSTER_CHECK": True,
    "CLUSTER_RPC": "https://api.mainnet-beta.solana.com",
    "CLUSTER_TOP_N": 30,              # holder analizzati (esclusi pool/curve/locker)
    "CLUSTER_FRESH_TX": 100,          # wallet "nuovo" = meno di 100 transazioni in tutto
    "CLUSTER_MAX_HOLDERS_GPA": 60000, # oltre: si usano solo i top holder di RugCheck
    "CLUSTER_CACHE_SEC": 1800,
    "BUNDLE_NET_MIN_WALLETS": 3,      # una rete conta se ha almeno 3 wallet collegati (stesso finanziatore)
    "BUNDLE_MAX_NETWORKS": 4,         # ESTREMO: >= 4 reti...
    "BUNDLE_TOTAL_HARD_PCT": 20.0,    # ...che insieme tengono >= 20% dell'offerta
    "BUNDLE_MAX_INSIDER_WALLETS": 30, # ESTREMO: >= 30 wallet collegati in totale (reti >100 wallet escluse se tengono poco)
    "CLUSTER_FRESH_HARD": 0.80,       # ESTREMO: >= 80% dei top holder sono wallet nuovi E almeno 2 reti
    "CLUSTER_SOFT_NETWORKS": 2,       # soft: >= 2 reti...
    "CLUSTER_SOFT_PCT": 5.0,          # ...o reti insieme >= 5% dell'offerta...
    "CLUSTER_FRESH_SOFT": 0.50,       # ...o >= 50% dei top holder wallet nuovi
    "CLUSTER_PENALTY": 0.08,          # penalita del segnale soft 'cluster' (piu forte degli altri)
    "CLUSTER_SIZE_MULT": 0.4,         # size x0.4
    # v7: LP sbloccata = segnale SOFT (penalita, size ridotta, tenuta massima piu corta). HARD solo se estremo.
    "LP_EXTREME_LOCKED_PCT": 5.0,     # HARD: LP bloccata/bruciata <= 5%...
    "LP_CREATOR_HOLD_PCT": 50.0,      # ...e almeno il 50% dei token LP in mano al creatore
    "LP_REMOVAL_DROP_PCT": 30.0,      # HARD: liquidita gia scesa del 30% da quando lo osserviamo (rimozione iniziata)
    "LP_SOFT_MAX_HOLD_MIN": 60,       # trade con LP sbloccata: tenuta massima 60 min
    "RUG_WINDOW_MIN": 60,             # rug = liquidita -50% entro 60 min dall'ingresso (o -80% in qualsiasi momento)
    # v7: shadow anche sugli scarti "soft" dell'Analyst (momentum/momento/VWAP/grafico) dopo i controlli di sicurezza
    "SHADOW_ANALYST_REJECTS": True,
    "SHADOW_MAX_OPEN_ANALYST": 25,    # al massimo 25 shadow aperti di questo tipo (non tolgono posto agli altri)
    # v7: segnali extra (sources/): bonus SOLO dopo tutti i filtri HARD, somma con tetto
    "SIGNALS_ENABLED": True,
    "FOMO_TREND_BONUS": 0.03,         # solo dalla vera board Fomo (serve FOMOAPI_KEY), non dal sostituto
    "FOMO_SUB_BONUS": 0.0,            # sostituto DexScreener boosts: 0 finche il Learner non vede che rende
    "KOL_BONUS": 0.03,
    "KOL_THESIS_BONUS": 0.02,
    "MOMENTUM_YOUNG_BONUS": 0.03,
    "TOTAL_BONUS_CAP": 0.10,
    "KOL_SOLD_PENALTY": 0.02,
    "KOL_MIN_SOL": 0.1,
    "MOMENTUM_MIN_SCORE": 0.5,
    "FOMO_SCOUT_SEC": 180,
    "LEARN_BONUS_MIN_N": 15,          # esiti minimi CON e SENZA il segnale
    "LEARN_BONUS_WINDOW": 150,
    "LEARN_BONUS_EDGE": 2.0,          # +step solo se la media con il segnale supera quella senza di >= 2 punti
    # v7: casi etichettati: alla chiusura l'esito diventa un esempio per il Learner (chiave = etichetta)
    "LABELLED_CASES": {"npz2FhoufQ7pKqhGpaipYWacrAkidyW2yDpVWSvpump": "cluster"},
''')

# ---- SOFT_FLAGS
rep('''    "lp": {"label": "LP bloccata in parte", "hard": "LP_HARD_PCT", "dir": -1, "step": 2.5, "lim": (15.0, 40.0)},
}''', '''    # v7: LP sbloccata e soft; sotto LP_HARD_PCT si aggiunge 'lp_unlocked_grave' (penalita e size doppie).
    # Il Learner sposta LP_HARD_PCT (esiti 'lp_unlocked_grave') e penalita/size (esiti 'lp_unlocked').
    "lp_unlocked": {"label": "LP sbloccata", "hard": "LP_HARD_PCT", "dir": -1, "step": 2.5, "lim": (15.0, 40.0)},
    # v7: cluster graduato. 'hard': None = il Learner regola solo penalita e size (nei due versi, entro i limiti);
    # la soglia ESTREMA (chiave 'cluster_hard') non viene mai allentata dal Learner.
    "cluster": {"label": "cluster di wallet collegati", "hard": None, "dir": 1, "step": 0.0, "lim": (0.0, 0.0),
                "pen": "CLUSTER_PENALTY", "size": "CLUSTER_SIZE_MULT", "pen_lim": (0.04, 0.15),
                "size_lim": (0.2, 0.7)},
}''')
rep('''def soft_pen(st, flag):
    return _learned(CFG["SOFT_PENALTY"], (st.learner_shadow.get(flag) or {}).get("pen_d", 0.0), LEARN_PEN_LIM)


def soft_size(st, flag):
    return _learned(CFG["SOFT_SIZE_MULT"], (st.learner_shadow.get(flag) or {}).get("size_d", 0.0), LEARN_SIZE_LIM)


def soft_hard(st, flag):
    spec = SOFT_FLAGS[flag]
    return _learned(CFG[spec["hard"]], (st.learner_shadow.get(flag + "_grave") or {}).get("hard_d", 0.0), spec["lim"])
''', '''def _pen_base(flag):
    spec = SOFT_FLAGS.get(flag) or {}
    return CFG[spec["pen"]] if spec.get("pen") else CFG["SOFT_PENALTY"], spec.get("pen_lim", LEARN_PEN_LIM)


def _size_base(flag):
    spec = SOFT_FLAGS.get(flag) or {}
    return CFG[spec["size"]] if spec.get("size") else CFG["SOFT_SIZE_MULT"], spec.get("size_lim", LEARN_SIZE_LIM)


def soft_pen(st, flag):
    b, lim = _pen_base(flag)
    return _learned(b, (st.learner_shadow.get(flag) or {}).get("pen_d", 0.0), lim)


def soft_size(st, flag):
    b, lim = _size_base(flag)
    return _learned(b, (st.learner_shadow.get(flag) or {}).get("size_d", 0.0), lim)


def soft_hard(st, flag):
    spec = SOFT_FLAGS[flag]
    if not spec.get("hard"):
        return None
    return _learned(CFG[spec["hard"]], (st.learner_shadow.get(flag + "_grave") or {}).get("hard_d", 0.0), spec["lim"])


def base_flags(flags):
    """Segnali distinti (lp_unlocked + lp_unlocked_grave contano come uno solo per SOFT_MAX_FLAGS)."""
    return sorted({f[:-6] if f.endswith("_grave") else f for f in flags})
''')
rep('''    elif key.endswith("_grave") and key[:-6] in SOFT_FLAGS:
        flag = key[:-6]''', '''    elif key.endswith("_grave") and key[:-6] in SOFT_FLAGS and SOFT_FLAGS[key[:-6]].get("hard"):
        flag = key[:-6]''')
rep('''    elif key in SOFT_FLAGS:
        op, osz = soft_pen(st, key), soft_size(st, key)
        bp, bs = CFG["SOFT_PENALTY"], CFG["SOFT_SIZE_MULT"]
        np_ = clamp(op - d * CFG["LEARN_PEN_STEP"], min(LEARN_PEN_LIM[0], bp), max(LEARN_PEN_LIM[1], bp))
        ns = clamp(osz + d * CFG["LEARN_SIZE_STEP"], min(LEARN_SIZE_LIM[0], bs), max(LEARN_SIZE_LIM[1], bs))''',
'''    elif key in SOFT_FLAGS:
        op, osz = soft_pen(st, key), soft_size(st, key)
        (bp, pl), (bs, sl) = _pen_base(key), _size_base(key)
        np_ = clamp(op - d * CFG["LEARN_PEN_STEP"], min(pl[0], bp), max(pl[1], bp))
        ns = clamp(osz + d * CFG["LEARN_SIZE_STEP"], min(sl[0], bs), max(sl[1], bs))''')

# ---- LP in security_check
rep('''    if m["dex"] != "pumpfun" and not pump_migrated:
        locked = [fnum((mk.get("lp") or {}).get("lpLockedPct")) for mk in markets if mk.get("lp")]
        if not locked:
            return False, "LP non verificabile", "LP non verificabile", None
        # lpLockedPct di RugCheck conta sia la LP bloccata sia quella bruciata
        lpmax = max(locked)
        if lpmax <= 0:  # HARD: LP tutta in mano a chi l'ha creata
            return False, f"LP bloccata solo al {lpmax:.0f}%", "LP non bloccata", None
        if lpmax < CFG["MIN_LP_LOCKED_PCT"]:  # v6: LP in parte bloccata = soft; sotto LP_HARD_PCT = scarto
            lh = soft_hard(st, "lp")
            if lpmax < lh:
                severe.append(("lp", f"LP bloccata solo al {lpmax:.0f}% (< {lh:g}%)", "LP non bloccata"))
            else:
                soft.append(("lp", f"LP bloccata al {lpmax:.0f}%"))
''', '''    if m["dex"] != "pumpfun" and not pump_migrated:
        lps = [mk.get("lp") for mk in markets if isinstance(mk, dict) and isinstance(mk.get("lp"), dict)]
        if not lps:
            return False, "LP non verificabile", "LP non verificabile", None
        # lpLockedPct di RugCheck conta sia la LP bloccata sia quella bruciata
        lpmax = max(fnum(lp.get("lpLockedPct")) for lp in lps)
        if lpmax < CFG["MIN_LP_LOCKED_PCT"]:
            # v7: LP sbloccata = SOFT. HARD solo se estremo: quasi tutta sbloccata E in mano al creatore,
            # oppure rimozione della liquidita gia iniziata.
            creator = str(r.get("creator") or "")
            creator_lp = max((sum(fnum(h.get("pct")) for h in (lp.get("holders") or []) if isinstance(h, dict)
                                  and creator and creator in (h.get("owner"), h.get("address"))) for lp in lps),
                             default=0.0)
            if lpmax <= CFG["LP_EXTREME_LOCKED_PCT"] and creator_lp >= CFG["LP_CREATOR_HOLD_PCT"]:
                return False, (f"LP bloccata solo al {lpmax:.0f}% e il {creator_lp:.0f}% dei token LP e del creatore"), \\
                    "LP in mano al creatore", None
            liq_max = fnum(m.get("liq_max"))
            drop = (1 - m["liq"] / liq_max) * 100 if liq_max > 0 and m["liq"] > 0 else 0.0
            if drop >= CFG["LP_REMOVAL_DROP_PCT"]:
                return False, f"rimozione della liquidita gia iniziata (-{drop:.0f}% da quando lo osserviamo)", \\
                    "rimozione LP", None
            lh = soft_hard(st, "lp_unlocked")
            soft.append(("lp_unlocked", f"LP bloccata/bruciata solo al {lpmax:.0f}%"))
            if lpmax < lh:  # fascia pesante: penalita e size doppie, il Learner sposta LP_HARD_PCT
                soft.append(("lp_unlocked_grave", f"LP sotto il {lh:g}%"))
''')
rep('''    sec = {"top10": round(top10, 2), "holders": total_holders or len(holders), "creator": creator,
           "insider_pct": round(insider_pct, 2), "soft": [f for f, _ in soft], "soft_txt": [t for _, t in soft]}''',
'''    sec = {"top10": round(top10, 2), "holders": total_holders or len(holders), "creator": creator,
           "insider_pct": round(insider_pct, 2), "soft": [f for f, _ in soft], "soft_txt": [t for _, t in soft],
           # v7: per il controllo cluster (solo sui candidati): esclusi pool/locker, holder RugCheck, reti RugCheck
           "exclude": sorted(exclude)[:200], "token_program": r.get("tokenProgram"),
           "rc_holders": [(h.get("owner") or h.get("address"), round(p, 4)) for h, p in zip(holders, pcts)][:20],
           "rc_networks": rc_networks(r)}''')
rep('''    if len(soft) >= CFG["SOFT_MAX_FLAGS"]:''', '''    if len(base_flags(f for f, _ in soft)) >= CFG["SOFT_MAX_FLAGS"]:''')

# ---- rc_networks + cluster functions after bundle_pct
rep('''def shadow_stats(hist):''', '''def rc_networks(r):
    """v7: reti RugCheck [(wallet, % offerta)]; quelle >BUNDLE_MAX_NET_SIZE (exchange/airdrop) solo se tengono molto."""
    supply = fnum((r.get("token") or {}).get("supply"))
    out = []
    for n in r.get("insiderNetworks") or []:
        if not isinstance(n, dict) or supply <= 0:
            continue
        size, pct = int(fnum(n.get("size"))), fnum(n.get("tokenAmount")) / supply * 100
        if size > CFG["BUNDLE_MAX_NET_SIZE"] and pct < CFG["BUNDLE_SOFT_PCT"]:
            continue
        if 0 <= pct <= 100:
            out.append((size, round(pct, 3)))
    return out


def cluster_metrics(rows, rc_nets=None):
    """v7: rows = [{o: owner, pct, ntx, src}] dei top holder (pool esclusi). Reti = wallet con lo stesso
    finanziatore (prima transazione del wallet). Ritorna metriche + livello 'none' | 'soft' | 'hard'."""
    groups = {}
    for r in rows:
        if r.get("src"):
            groups.setdefault(r["src"], []).append(r)
    nmin = int(CFG["BUNDLE_NET_MIN_WALLETS"])
    nets = [(len(v), sum(fnum(x.get("pct")) for x in v)) for v in groups.values() if len(v) >= nmin]
    for size, pct in rc_nets or []:
        if size >= nmin:
            nets.append((size, pct))
    nets.sort(reverse=True)
    n_nets = len(nets)
    net_pct = round(sum(p for _, p in nets), 2)
    wallets = sum(n for n, _ in nets if n <= CFG["BUNDLE_MAX_NET_SIZE"] or True)
    scanned = len(rows)
    fresh = sum(1 for r in rows if r.get("ntx") is not None and r["ntx"] < CFG["CLUSTER_FRESH_TX"])
    fresh_share = round(fresh / scanned, 3) if scanned else 0.0
    why = []
    if n_nets >= CFG["BUNDLE_MAX_NETWORKS"] and net_pct >= CFG["BUNDLE_TOTAL_HARD_PCT"]:
        why.append(f"{n_nets} reti collegate con il {net_pct:.1f}% dell'offerta")
    if wallets >= CFG["BUNDLE_MAX_INSIDER_WALLETS"]:
        why.append(f"{wallets} wallet collegati")
    if scanned >= 10 and fresh_share >= CFG["CLUSTER_FRESH_HARD"] and n_nets >= 2:
        why.append(f"{fresh_share * 100:.0f}% dei top holder sono wallet nuovi e {n_nets} reti")
    level = "hard" if why else "none"
    if level == "none":
        if n_nets >= CFG["CLUSTER_SOFT_NETWORKS"]:
            why.append(f"{n_nets} reti collegate ({net_pct:.1f}%)")
        if net_pct >= CFG["CLUSTER_SOFT_PCT"]:
            why.append(f"reti collegate al {net_pct:.1f}%")
        if scanned >= 10 and fresh_share >= CFG["CLUSTER_FRESH_SOFT"]:
            why.append(f"{fresh_share * 100:.0f}% top holder wallet nuovi")
        level = "soft" if why else "none"
    return {"level": level, "why": "; ".join(why), "networks": n_nets, "net_pct": net_pct, "wallets": wallets,
            "fresh_share": fresh_share, "scanned": scanned, "nets": nets[:8]}


_B58A = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58(b):
    n, out = int.from_bytes(b, "big"), ""
    while n:
        n, r = divmod(n, 58)
        out = _B58A[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\\0"))) + out


RPC_HEAVY_LOCK = asyncio.Lock()   # v7: un solo lavoro RPC pesante alla volta (cluster, KOL): RPC pubblica condivisa
CLUSTER_CACHE = {}


async def _crpc(http, method, params):
    d = await http.post(CFG["CLUSTER_RPC"], {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    return d.get("result") if isinstance(d, dict) else None


async def cluster_scan(st, http, mint, sec):
    """v7: top holder (getProgramAccounts) -> per ogni wallet: n. transazioni e finanziatore della prima.
    Solo lettura, RPC pubblica, al massimo ~2*CLUSTER_TOP_N+1 chiamate, cache 30 min. None se non riesce."""
    hit = CLUSTER_CACHE.get(mint)
    if hit and time.time() - hit[0] < CFG["CLUSTER_CACHE_SEC"]:
        return hit[1]
    excl = set(sec.get("exclude") or [])
    owners = []
    prog = sec.get("token_program") or "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
    async with RPC_HEAVY_LOCK:
        if int(fnum(sec.get("holders"))) <= CFG["CLUSTER_MAX_HOLDERS_GPA"]:
            flt = [{"memcmp": {"offset": 0, "bytes": mint}}]
            if prog.startswith("Tokenkeg"):
                flt.append({"dataSize": 165})
            acc = await _crpc(http, "getProgramAccounts", [prog, {"encoding": "base64", "filters": flt,
                                                                  "dataSlice": {"offset": 32, "length": 40}}])
            if isinstance(acc, list) and acc:
                rows = []
                for a in acc:
                    try:
                        d = base64.b64decode(a["account"]["data"][0])
                        rows.append((int.from_bytes(d[32:40], "little"), _b58(d[:32]), a.get("pubkey")))
                    except Exception:  # noqa: BLE001
                        continue
                tot = sum(x[0] for x in rows) or 1
                rows.sort(reverse=True)
                for amt, owner, acc_k in rows:
                    if owner in excl or acc_k in excl or amt / tot * 100 >= 10:  # pool/curva/locker
                        continue
                    owners.append((owner, amt / tot * 100))
                    if len(owners) >= CFG["CLUSTER_TOP_N"]:
                        break
        if not owners:
            owners = [(o, p) for o, p in sec.get("rc_holders") or [] if o and o not in excl][:CFG["CLUSTER_TOP_N"]]
        if not owners:
            return None
        out = []
        for owner, pct in owners:
            sigs = await _crpc(http, "getSignaturesForAddress", [owner, {"limit": int(CFG["CLUSTER_FRESH_TX"])}])
            if not isinstance(sigs, list):
                continue
            rec = {"o": owner, "pct": pct, "ntx": len(sigs), "src": None}
            if sigs and len(sigs) < CFG["CLUSTER_FRESH_TX"]:
                tx = await _crpc(http, "getTransaction", [sigs[-1]["signature"], {
                    "encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
                try:
                    msg = tx["transaction"]["message"]
                    ins = list(msg.get("instructions") or [])
                    for ii in (tx.get("meta") or {}).get("innerInstructions") or []:
                        ins += ii.get("instructions") or []
                    for x in ins:
                        p = x.get("parsed") if isinstance(x, dict) else None
                        if isinstance(p, dict) and p.get("type") == "transfer" and \\
                                (p.get("info") or {}).get("destination") == owner and "lamports" in p["info"]:
                            rec["src"] = p["info"].get("source")
                            break
                    payer = msg["accountKeys"][0]["pubkey"] if isinstance(msg["accountKeys"][0], dict) \\
                        else msg["accountKeys"][0]
                    if rec["src"] is None and payer != owner:
                        rec["src"] = payer
                except Exception:  # noqa: BLE001
                    pass
            out.append(rec)
    if len(out) < 5:
        return None
    res = cluster_metrics(out, sec.get("rc_networks"))
    CLUSTER_CACHE[mint] = (time.time(), res)
    for k in [k for k, v in CLUSTER_CACHE.items() if time.time() - v[0] > CFG["CLUSTER_CACHE_SEC"]]:
        CLUSTER_CACHE.pop(k, None)
    return res


def shadow_stats(hist):''')
open(P, "w").write(s)
print("ok part1")
