# ======================================================================= prontezza al live (solo controlli, inerte)
READINESS_PATH = os.path.join(DATA_DIR, "live_readiness.json")


async def readiness_check(st, http, broker):
    """Controlla all'avvio cosa e pronto per il live e scrive live_readiness.json. Non legge chiavi, non firma,
    non invia nulla: verifica solo che i servizi pubblici rispondano e che i limiti siano impostati."""
    checks = []

    def add(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    add("modalita paper", CFG["MODE"] == "paper", f"MODE={CFG['MODE']}")
    add("blocco DESK_PAPER_ONLY", os.environ.get("DESK_PAPER_ONLY") == "1", "DESK_PAPER_ONLY=1 rifiuta dry/live")
    t0 = time.time()
    q, err = await jup_quote(http, WSOL, USDC, 10 ** 8, 50)
    ms = (time.time() - t0) * 1000
    solp = int(q["outAmount"]) / 1e5 if q else 0.0  # 0.1 SOL -> USDC (6 decimali)
    if solp > 1:
        SOL_USD[0], SOL_USD[1] = solp, time.time()
    add("Jupiter quote API", q is not None, f"{CFG['JUP_BASE']} in {ms:.0f} ms, SOL ${solp:,.2f}" if q else f"errore {err}")
    t0 = time.time()
    d = await http.post(CFG["RPC_URL"], {"jsonrpc": "2.0", "id": 1, "method": "getSlot"})
    ms = (time.time() - t0) * 1000
    add("RPC Solana", isinstance(d, dict) and "result" in d, f"slot {d.get('result') if isinstance(d, dict) else '-'} "
                                                           f"in {ms:.0f} ms")
    add("RPC privato/veloce", "api.mainnet-beta.solana.com" not in CFG["RPC_URL"],
        "RPC pubblico: ok per paper, per il live serve un RPC dedicato (Helius/Triton/QuickNode free tier)")
    ok = await refresh_prio_fee(http, st, force=True)
    add("priority fee dinamica", ok, f"normale {prio_fee_sol():.6f} SOL, urgente {prio_fee_sol(True):.6f} SOL"
        if ok else "RPC senza dati: uso la fee fissa")
    try:
        import importlib.util
        has_solders = importlib.util.find_spec("solders") is not None
    except Exception:  # noqa: BLE001
        has_solders = False
    add("libreria solders (firma)", has_solders, "installata" if has_solders else "manca: pip install solders")
    add("file .env presente", os.path.exists(os.path.join(DATA_DIR, ".env")), "contenuto non letto dal controllo")
    add("kill switch STOP", True, "touch STOP vende tutto e blocca gli acquisti")
    add("limite perdita giornaliera", CFG["DAILY_LOSS_LIMIT_PCT"] < 0, f"{CFG['DAILY_LOSS_LIMIT_PCT']}%")
    add("limite perdita totale (HARD_STOP)", CFG["MAX_TOTAL_LOSS_PCT"] < 0, f"{CFG['MAX_TOTAL_LOSS_PCT']}%")
    add("size minuscola per il primo live", CFG["LIVE_MAX_TRADE_USD"] <= 10, f"LIVE_MAX_TRADE_USD=${CFG['LIVE_MAX_TRADE_USD']}")
    add("Jito / invio protetto da MEV", bool(CFG["LIVE_USE_JITO"]), "non implementato")
    add("dry run riuscito", bool(CFG["LIVE_DRY_RUN_DONE"]), "da fare con MODE=dry e wallet dedicato")
    add("simulazione realistica attiva", getattr(broker, "realistic", False), "preventivi Jupiter, latenza, fallimenti")
    n_ok = sum(1 for c in checks if c["ok"])
    rep = {"ts": datetime.now(TZ).isoformat(timespec="seconds"), "ok": n_ok, "total": len(checks), "checks": checks}
    with open(READINESS_PATH, "w") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    missing = [c["check"] for c in checks if not c["ok"] and c["check"] not in ("file .env presente",)]
    log(st, "Logger", "readiness", f"prontezza al live {n_ok}/{len(checks)}; manca: {', '.join(missing) or 'niente'}",
        readiness=rep)
    return rep
