"""v8.2 - controllo honeypot prima di ogni ingresso (solo letture, nessuno swap).

1) account del mint (getAccountInfo jsonParsed): estensioni Token-2022 pericolose + mint/freeze authority
2) preventivo Jupiter di VENDITA (solo quote) per la quantita che compreremmo: nessun percorso o
   impatto > soglia = scarto rigido
3) il report RugCheck viene gia letto dal filtro (security_check): qui si riusano i suoi rischi
"""

CFG = {
    "MAX_TRANSFER_FEE_BPS": 999,     # v8.4: fee Token-2022 >= 10% = rigido; sotto e soft (entra nel costo a/r)
    "SELL_IMPACT_HARD_PCT": 25.0,    # impatto del preventivo di vendita oltre il 25% = scarto
    "RT_HARD_PCT": 35.0,             # andata+ritorno immediato che perde oltre il 35% = scarto
}

SAFE_EXT = {"metadataPointer", "tokenMetadata", "interestBearingConfig", "groupPointer", "groupMemberPointer",
            "tokenGroup", "tokenGroupMember", "mintCloseAuthority", "scaledUiAmountConfig", "pausableConfig"}


def mint_flags(parsed, cfg=None):
    """parsed = result.value.data.parsed di getAccountInfo jsonParsed. Ritorna (hard[], soft[], info)."""
    c = dict(CFG, **(cfg or {}))
    info = (parsed or {}).get("info") or {}
    hard, soft, ext_names = [], [], []
    fee_bps = 0
    if info.get("mintAuthority"):
        hard.append("mint authority attiva (possono stampare token)")
    if info.get("freezeAuthority"):
        hard.append("freeze authority attiva (possono congelare i wallet)")
    for e in info.get("extensions") or []:
        name, s = e.get("extension"), e.get("state") or {}
        ext_names.append(name)
        if name == "transferHook" and s.get("programId"):
            hard.append(f"transfer hook attivo (programma {str(s.get('programId'))[:6]}.. puo bloccare le vendite)")
        elif name == "transferFeeConfig":
            bps = 0
            for k in ("newerTransferFee", "olderTransferFee"):
                bps = max(bps, int((s.get(k) or {}).get("transferFeeBasisPoints") or 0))
            if bps > c["MAX_TRANSFER_FEE_BPS"]:
                hard.append(f"fee di trasferimento {bps / 100:.1f}%")
            elif bps > 0:
                soft.append(f"fee di trasferimento {bps / 100:.2f}%")
            fee_bps = max(fee_bps, bps)
            if s.get("transferFeeConfigAuthority"):   # v8.4: il dev puo alzarla fino a farne un honeypot
                hard.append("la fee di trasferimento puo essere alzata dal dev (authority attiva)")
        elif name == "permanentDelegate" and s.get("delegate"):
            hard.append("permanent delegate (possono prendere i token da ogni wallet)")
        elif name == "defaultAccountState" and str(s.get("accountState", "")).lower() == "frozen":
            hard.append("i nuovi account nascono congelati")
        elif name == "nonTransferable":
            hard.append("token non trasferibile")
        elif name == "confidentialTransferMint":
            soft.append("trasferimenti confidenziali")
        elif name == "pausableConfig" and (s.get("paused") or s.get("authority")):
            (hard if s.get("paused") else soft).append("token pausabile" + (" (in pausa)" if s.get("paused") else ""))
        elif name not in SAFE_EXT:
            soft.append(f"estensione insolita {name}")
    return hard, soft, {"extensions": ext_names, "decimals": info.get("decimals"), "fee_bps": fee_bps}


def sell_verdict(sell_quote, sell_err, buy_lamports, cfg=None):
    """Giudica il preventivo di vendita. Ritorna (hard_reason|None, info)."""
    c = dict(CFG, **(cfg or {}))
    if sell_err == "no_route":
        return "nessun percorso di vendita su Jupiter (honeypot)", {}
    if not sell_quote:
        return None, {"sell_quote": "non disponibile", "err": sell_err}
    imp = abs(float(sell_quote.get("priceImpactPct") or 0)) * 100
    back = int(sell_quote.get("outAmount") or 0)
    rt = (1 - back / buy_lamports) * 100 if buy_lamports > 0 else None
    info = {"sell_impact_pct": round(imp, 3), "rt_pct": round(rt, 3) if rt is not None else None}
    if imp > c["SELL_IMPACT_HARD_PCT"]:
        return f"impatto di vendita {imp:.1f}% (> {c['SELL_IMPACT_HARD_PCT']:g}%)", info
    if rt is not None and rt > c["RT_HARD_PCT"]:
        return f"comprare e rivendere subito perderebbe il {rt:.1f}% (> {c['RT_HARD_PCT']:g}%)", info
    return None, info
