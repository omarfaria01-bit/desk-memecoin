import re, sys
P = "/workspace/memebot/desk.py"
s = open(P).read()
def rep(old, new, count=1):
    global s
    n = s.count(old)
    assert n == count, (n, old[:80])
    s = s.replace(old, new)

rep("import os\nimport re\n", "import os\nimport random\nimport re\n")
rep('DESK MEMECOIN SOLANA v2\n', 'DESK MEMECOIN SOLANA v6 (paper che simula il live: preventivi Jupiter reali, fee dinamiche, latenza)\n')

# ---------------------------------------------------------------- config
rep('''    "SOL_RESERVE": 0.03,              # SOL lasciati sempre per le commissioni
    "MAX_SOL_PER_TRADE": 0.5,
}''', '''    "SOL_RESERVE": 0.03,              # SOL lasciati sempre per le commissioni
    "MAX_SOL_PER_TRADE": 0.5,
    # ---- v6: simulazione realistica del live (solo paper; nessuna transazione viene mai inviata)
    "SIM_REALISTIC": True,            # paper con preventivi Jupiter, latenza, fallimenti e fee dinamiche
    "SIM_USE_QUOTES": True,           # fill dai preventivi reali Jupiter (se falliscono: formula v5)
    "SIM_LATENCY_MIN_SEC": 1.0,       # ritardo simulato tra segnale e fill...
    "SIM_LATENCY_MAX_SEC": 3.0,       # ...il prezzo e quello del preventivo DOPO il ritardo
    "SIM_FAIL_PROB_BUY": 0.03,        # probabilita che una transazione d'acquisto fallisca
    "SIM_FAIL_PROB_SELL": 0.05,       # ...di vendita normale
    "SIM_FAIL_PROB_SELL_FAST": 0.15,  # ...di vendita durante un crollo (stop, crash, rug)
    "SIM_BUY_RETRIES": 1,             # tentativi extra d'acquisto dopo un fallimento
    "SIM_QUOTE_EXTRA_FEE_PCT": 0.3,   # margine prudente per lato sui preventivi (MEV, deriva del percorso)
    "SIM_BASE_FEE_LAMPORTS": 5000,    # commissione base Solana per firma
    "SIM_ATA_RENT_SOL": 0.00204,      # affitto conto token per posizione (bloccato finche aperta)
    "SIM_SEED": None,                 # seme del caso (None = casuale), utile per i test
    "SELLCHECK_MAX_RT_PCT": 15.0,     # prima di comprare: se rivendere subito costa oltre il 15% -> scarto
    "SELL_NOROUTE_WRITE_OFF": 20,     # dopo 20 vendite senza percorso Jupiter la posizione vale 0 (paper)
    "MARK_QUOTE_SEC": 60,             # valore delle posizioni dal preventivo di vendita reale ogni 60 s
    # priority fee dinamica da getRecentPrioritizationFees (RPC pubblico gratuito)
    "PRIO_DYNAMIC": True,
    "PRIO_REFRESH_SEC": 180,
    "PRIO_PERCENTILE": 75,            # vendita/acquisto normale
    "PRIO_PERCENTILE_URGENT": 90,     # vendite in crollo
    "PRIO_URGENT_MULT": 1.5,
    "SWAP_COMPUTE_UNITS": 300000,     # unita di calcolo stimate per uno swap
    "PRIO_FLOOR_LAMPORTS": 100000,    # minimo 0.0001 SOL di priority (tetto: PRIORITY_MAX_LAMPORTS)
    "PRIO_ACCOUNTS": ["pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",   # PumpSwap AMM
                      "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"],  # Raydium AMM v4
    # ---- v6: limiti per il futuro live (inerti in paper, nessuno invia fondi)
    "LIVE_MAX_TRADE_USD": 5.0,        # primo live: operazioni minuscole
    "MAX_TOTAL_LOSS_PCT": -30.0,      # perdita totale massima: crea il file HARD_STOP e blocca gli acquisti
    "LIVE_CONFIRM_TIMEOUT_SEC": 60,
    "LIVE_USE_JITO": False,           # non implementato: da fare prima del live
    "LIVE_JITO_TIP_SOL": 0.0001,
    "LIVE_DRY_RUN_DONE": False,       # da mettere a true solo dopo un dry run riuscito
}''')
rep('HOST_GAPS = {"api.dexscreener.com": 0.25, "api.geckoterminal.com": 3.0, "api.rugcheck.xyz": 2.5}',
    'HOST_GAPS = {"api.dexscreener.com": 0.25, "api.geckoterminal.com": 3.0, "api.rugcheck.xyz": 2.5,\n'
    '             "lite-api.jup.ag": 1.1, "api.jup.ag": 1.1, "api.mainnet-beta.solana.com": 0.5}  # v6: Jupiter e RPC\n'
    'HARD_STOP_PATH = os.path.join(DATA_DIR, "HARD_STOP")')

# state: total_start + hard_tripped
rep('''        self.equity_hist = deque(maxlen=3000)
        # non salvati''', '''        self.equity_hist = deque(maxlen=3000)
        self.total_start = CFG["START_BALANCE"]   # v6: base del limite di perdita totale
        self.hard_tripped = False
        # non salvati''')
rep('''            "blacklist": sorted(self.blacklist), "equity_hist": list(self.equity_hist)[-1000:],
        }''', '''            "blacklist": sorted(self.blacklist), "equity_hist": list(self.equity_hist)[-1000:],
            "total_start": self.total_start, "hard_tripped": self.hard_tripped,
        }''')
rep('''    st.equity_hist.extend(tuple(x) for x in d.get("equity_hist", []))
    return st''', '''    st.equity_hist.extend(tuple(x) for x in d.get("equity_hist", []))
    st.total_start = fnum(d.get("total_start"), st.period_start_equity or CFG["START_BALANCE"])
    st.hard_tripped = bool(d.get("hard_tripped", False))
    return st''')

# ---------------------------------------------------------------- cost model + sim
rep('''def prio_fee_usd():
    return CFG["PRIORITY_FEE_SOL"] * sol_usd()
''', '''# v6: priority fee dinamica (SOL per transazione, base fee inclusa). Se i dati sono vecchi o mancanti si usa
# la fee fissa PRIORITY_FEE_SOL della v5.
PRIO_DYN = {"sol": 0.0, "urgent": 0.0, "ts": 0.0, "last_try": 0.0, "micro": 0.0, "micro_urgent": 0.0}


def percentile(vals, pct):
    v = sorted(vals)
    if not v:
        return 0.0
    i = min(len(v) - 1, max(0, int(round(pct / 100 * (len(v) - 1)))))
    return float(v[i])


def prio_sol_from_micro(micro, mult=1.0):
    """micro-lamport per unita di calcolo -> SOL per transazione, con minimo, tetto e base fee."""
    lam = micro * CFG["SWAP_COMPUTE_UNITS"] / 1e6 * mult
    lam = clamp(lam, CFG["PRIO_FLOOR_LAMPORTS"], max(CFG["PRIO_FLOOR_LAMPORTS"], CFG["PRIORITY_MAX_LAMPORTS"]))
    return (lam + CFG["SIM_BASE_FEE_LAMPORTS"]) / 1e9


def prio_fee_sol(urgent=False):
    fresh = PRIO_DYN["ts"] and time.time() - PRIO_DYN["ts"] < 3 * max(60, CFG["PRIO_REFRESH_SEC"])
    if CFG.get("PRIO_DYNAMIC") and fresh:
        return PRIO_DYN["urgent" if urgent else "sol"]
    return CFG["PRIORITY_FEE_SOL"]


def prio_fee_usd(urgent=False):
    # (v5) return CFG["PRIORITY_FEE_SOL"] * sol_usd()
    return prio_fee_sol(urgent) * sol_usd()


async def refresh_prio_fee(http, st=None, force=False):
    """Legge getRecentPrioritizationFees (ultimi ~150 slot) per gli AMM usati dai memecoin."""
    if http is None or not CFG.get("PRIO_DYNAMIC"):
        return False
    if not force and time.time() - PRIO_DYN["last_try"] < CFG["PRIO_REFRESH_SEC"]:
        return False
    PRIO_DYN["last_try"] = time.time()
    norm, urg = [], []
    for acc in CFG["PRIO_ACCOUNTS"]:
        d = await http.post(CFG["RPC_URL"], {"jsonrpc": "2.0", "id": 1, "method": "getRecentPrioritizationFees",
                                             "params": [[acc]]})
        res = d.get("result") if isinstance(d, dict) else None
        if not isinstance(res, list) or not res:
            continue
        vals = [fnum(x.get("prioritizationFee")) for x in res if isinstance(x, dict)]
        norm.append(percentile(vals, CFG["PRIO_PERCENTILE"]))
        urg.append(percentile(vals, CFG["PRIO_PERCENTILE_URGENT"]))
    if not norm:
        return False
    PRIO_DYN.update({"micro": max(norm), "micro_urgent": max(urg), "ts": time.time(),
                     "sol": prio_sol_from_micro(max(norm)),
                     "urgent": prio_sol_from_micro(max(urg), CFG["PRIO_URGENT_MULT"])})
    write_event("Executor", "prio_fee", "priority fee aggiornata",
                {"prio_fee_sol": round(PRIO_DYN["sol"], 8), "prio_fee_sol_urgent": round(PRIO_DYN["urgent"], 8),
                 "p_micro": PRIO_DYN["micro"], "p_micro_urgent": PRIO_DYN["micro_urgent"]})
    return True


# v6: preventivi Jupiter (API gratuita, nessuna chiave) e decimali dei token
SIM = {"rng": random.Random(), "dec": {}}


async def jup_quote(http, inp, out, amount, slip_bps=None):
    """Ritorna (preventivo, errore). errore 'no_route' = Jupiter dice che non c'e percorso (come in live)."""
    amount = int(amount)
    if amount <= 0:
        return None, "importo nullo"
    url = (f"{CFG['JUP_BASE']}/quote?inputMint={inp}&outputMint={out}&amount={amount}"
           f"&slippageBps={int(slip_bps or CFG['SLIPPAGE_BPS'])}&restrictIntermediateTokens=true")
    await http._wait(url)
    try:
        async with http.s.get(url) as r:
            txt = await r.text()
            if r.status == 200:
                q = json.loads(txt)
                if isinstance(q, dict) and int(fnum(q.get("outAmount"))) > 0:
                    return q, None
                return None, "no_route"
            if r.status == 400 and "errorCode" in txt:
                return None, "no_route"
            return None, f"http {r.status}"
    except Exception as e:  # noqa: BLE001
        return None, f"rete ({type(e).__name__})"


async def token_decimals(http, mint):
    if mint in SIM["dec"]:
        return SIM["dec"][mint]
    d = await http.post(CFG["RPC_URL"], {"jsonrpc": "2.0", "id": 1, "method": "getTokenSupply", "params": [mint]})
    try:
        dec = int(d["result"]["value"]["decimals"])
    except Exception:  # noqa: BLE001
        return None
    SIM["dec"][mint] = dec
    return dec


def is_urgent(reason):
    r = str(reason).lower()
    return any(k in r for k in ("crollo", "stop loss", "liquidita crollata", "rug", "arresto", "nuovo rischio"))
''')

rep('''def mark_value(p):
    # (orig.) return p["tokens"] * p["last_price"] * (1 - CFG["FEE_PCT"] / 100)
    return exit_value(p["tokens"], p["last_price"], p.get("liq", 1.0))''', '''def mark_value(p):
    # (orig.) return p["tokens"] * p["last_price"] * (1 - CFG["FEE_PCT"] / 100)
    # v6: se c'e un preventivo di vendita Jupiter recente usa il suo rapporto di esecuzione reale
    q = p.get("qmark")
    if q and CFG.get("SIM_REALISTIC") and p.get("tokens", 0) > 0 and q.get("ratio", 0) > 0 \\
            and time.time() - q.get("ts", 0) < 3 * max(15, CFG["MARK_QUOTE_SEC"]):
        v = p["tokens"] * p["last_price"] * q["ratio"] * (1 - CFG["SIM_QUOTE_EXTRA_FEE_PCT"] / 100) - prio_fee_usd()
        return max(0.0, v)
    return exit_value(p["tokens"], p["last_price"], p.get("liq", 1.0))''')

# ---------------------------------------------------------------- PaperBroker
old_pb_start = s.index("class PaperBroker:")
old_pb_end = s.index("class SolanaBroker:")
s = s[:old_pb_start] + open("/workspace/memebot/dev/paperbroker_v6.py").read() + "\n\n" + s[old_pb_end:]

rep('''    async def sell(self, st, mint, p, fraction):
        solp = await self.sol_price()''', '''    async def sell(self, st, mint, p, fraction, urgent=False):
        solp = await self.sol_price()''')
# live safety: tiny trades on first live
rep('''        sol_amt = min(size_usd / solp, CFG["MAX_SOL_PER_TRADE"])
        lam = int(sol_amt * 1e9)''', '''        if self.mode == "live":  # v6: limite per i primi trade live (solo riduce il rischio)
            size_usd = min(size_usd, CFG["LIVE_MAX_TRADE_USD"])
        sol_amt = min(size_usd / solp, CFG["MAX_SOL_PER_TRADE"])
        lam = int(sol_amt * 1e9)''')

# ---------------------------------------------------------------- executor
rep('''    if st.paused:
        reject(st, mint, m["symbol"], "pausa per limite di perdita", agent="Executor", cat="pausa")
        return''', '''    if st.paused:
        reject(st, mint, m["symbol"], "pausa per limite di perdita", agent="Executor", cat="pausa")
        return
    if os.path.exists(HARD_STOP_PATH):  # v6: limite di perdita totale (si toglie cancellando HARD_STOP)
        reject(st, mint, m["symbol"], "HARD_STOP attivo: perdita totale massima", agent="Executor", cat="HARD_STOP")
        return''')
rep('''    size = min(eq * frac, m["liq"] * CFG["MAX_POOL_SHARE_PCT"] / 100, cash, room)
    pct = safe_div(size, eq)''', '''    size = min(eq * frac, m["liq"] * CFG["MAX_POOL_SHARE_PCT"] / 100, cash, room)
    if getattr(broker, "realistic", False):  # v6: come in live, mai oltre MAX_SOL_PER_TRADE
        size = min(size, CFG["MAX_SOL_PER_TRADE"] * sol_usd())
    pct = safe_div(size, eq)''')
rep('''    rt = roundtrip_cost_pct(size, m["liq"])
    if rt * CFG["COST_GATE_MULT"] > CFG["TP1_PCT"]:''', '''    rt = roundtrip_cost_pct(size, m["liq"])
    pre = None
    if getattr(broker, "realistic", False):
        # v6: preventivo reale d'acquisto + rivendita immediata (anti-honeypot, utile anche in live)
        pre = await broker.precheck(st, mint, m, size)
        if pre and not pre.get("ok", True):
            reject(st, mint, m["symbol"], pre["why"], agent="Executor", cat=pre.get("cat", "preventivo"))
            return
        if pre and pre.get("rt_pct") is not None:
            rt = pre["rt_pct"]  # costo andata+ritorno misurato sui preventivi, non stimato
    if rt * CFG["COST_GATE_MULT"] > CFG["TP1_PCT"]:''')
rep('''    res = await broker.buy(st, mint, m, size)
    if not res.get("ok"):''', '''    res = await broker.buy(st, mint, m, size, pre=pre) if pre is not None else await broker.buy(st, mint, m, size)
    if not res.get("ok"):''')
rep('''        "rt_cost_pct": round(rt, 2), "vwap_gap": c.get("vwap_gap"),
    }''', '''        "rt_cost_pct": round(rt, 2), "vwap_gap": c.get("vwap_gap"),
        # v6: dati di esecuzione per ticher
        "quote_src": res.get("src", "formula"), "quote_impact": res.get("quote_impact"),
        "latency_slip": res.get("latency_slip"), "prio_fee_sol": res.get("prio_fee_sol", 0.0) or 0.0,
        "fill_failed": res.get("fill_failed", 0), "fee_failed_usd": res.get("fee_failed_usd", 0.0),
        "sell_quote_impact": None, "sell_latency_slip": None, "latency_sec": res.get("latency_sec"),
    }''')
rep('''        f"{res['impact'] * 100:.1f}%, costo a/r stimato {rt:.1f}%{extra}). {c['thesis']}", mint=mint, size=round(res["cost"], 2),
        url=m["url"])''', '''        f"{res['impact'] * 100:.1f}%, costo a/r {'misurato' if pre and pre.get('rt_pct') is not None else 'stimato'} "
        f"{rt:.1f}%{extra}). {c['thesis']}", mint=mint, size=round(res["cost"], 2),
        url=m["url"], quote_src=res.get("src", "formula"), quote_impact=res.get("quote_impact"),
        latency_slip=res.get("latency_slip"), prio_fee_sol=res.get("prio_fee_sol"),
        fill_failed=res.get("fill_failed", 0), rt_cost_pct=round(rt, 2))''')

# ---------------------------------------------------------------- sells
rep('''    res = {"ok": True, "value": 0.0, "tokens": p["tokens"] * fraction} if zero else \\
        await broker.sell(st, mint, p, fraction)
    if not res.get("ok"):
        p["sell_fails"] = p.get("sell_fails", 0) + 1
        log(st, "Risk", "warn", f"vendita {p['symbol']} non riuscita ({res.get('error')}), riprovo")
        return''', '''    urgent = is_urgent(reason)
    res = {"ok": True, "value": 0.0, "tokens": p["tokens"] * fraction} if zero else \\
        await broker.sell(st, mint, p, fraction, urgent=urgent)
    if not res.get("ok"):
        p["sell_fails"] = p.get("sell_fails", 0) + 1
        p["fill_failed"] = p.get("fill_failed", 0) + res.get("fill_failed", 1)
        p["fee_failed_usd"] = p.get("fee_failed_usd", 0.0) + res.get("fee_failed_usd", 0.0)
        p["noroute_fails"] = p.get("noroute_fails", 0) + 1 if res.get("no_route") else 0
        log(st, "Risk", "fill_failed", f"vendita {p['symbol']} non riuscita ({res.get('error')}), riprovo", mint=mint,
            fill_failed=True, urgent=urgent, prio_fee_sol=res.get("prio_fee_sol"))
        if broker.mode == "paper" and p["noroute_fails"] >= CFG["SELL_NOROUTE_WRITE_OFF"]:
            log(st, "Risk", "warn", f"{p['symbol']}: {p['noroute_fails']} vendite senza percorso, valore azzerato")
            await _sell_position(st, broker, mint, "invendibile su Jupiter (nessun percorso)", zero=True)
        return
    p["noroute_fails"] = 0
    p["prio_fee_sol"] = (p.get("prio_fee_sol") or 0.0) + (res.get("prio_fee_sol") or 0.0)
    p["fill_failed"] = p.get("fill_failed", 0) + res.get("fill_failed", 0)
    p["fee_failed_usd"] = p.get("fee_failed_usd", 0.0) + res.get("fee_failed_usd", 0.0)
    if res.get("quote_impact") is not None:
        p["sell_quote_impact"] = res["quote_impact"]
    if res.get("latency_slip") is not None:
        p["sell_latency_slip"] = res["latency_slip"]''')
rep('''        log(st, "Risk", "sell", f"VENDE {fraction * 100:.0f}% di {p['symbol']} per ${res['value']:.2f}: {reason}",
            mint=mint)''', '''        log(st, "Risk", "sell", f"VENDE {fraction * 100:.0f}% di {p['symbol']} per ${res['value']:.2f}: {reason}",
            mint=mint, quote_src=res.get("src", "formula"), quote_impact=res.get("quote_impact"),
            latency_slip=res.get("latency_slip"), prio_fee_sol=res.get("prio_fee_sol"),
            fill_failed=res.get("fill_failed", 0))''')
rep('''         "rt_cost_pct": p.get("rt_cost_pct"), "vwap_gap": p.get("vwap_gap")}''', '''         "rt_cost_pct": p.get("rt_cost_pct"), "vwap_gap": p.get("vwap_gap"),
         # v6: esecuzione (impatti e slittamenti in %, fee in SOL sommate su tutti i lati)
         "quote_src": p.get("quote_src", "formula"), "quote_impact": p.get("quote_impact"),
         "sell_quote_impact": p.get("sell_quote_impact"), "latency_slip": p.get("latency_slip"),
         "sell_latency_slip": p.get("sell_latency_slip"), "prio_fee_sol": round(p.get("prio_fee_sol") or 0.0, 8),
         "fill_failed": p.get("fill_failed", 0), "fee_failed_usd": round(p.get("fee_failed_usd", 0.0), 4)}''')
rep('''    log(st, "Risk", "sell", f"CHIUDE {p['symbol']} {t['pnl_pct']:+.1f}% (${pnl:+.2f}): {reason}", mint=mint,
        pnl=t["pnl"])''', '''    log(st, "Risk", "sell", f"CHIUDE {p['symbol']} {t['pnl_pct']:+.1f}% (${pnl:+.2f}): {reason}", mint=mint,
        pnl=t["pnl"], quote_src=res.get("src", "formula"), quote_impact=res.get("quote_impact"),
        latency_slip=res.get("latency_slip"), prio_fee_sol=res.get("prio_fee_sol"), fill_failed=res.get("fill_failed", 0))''')

# ---------------------------------------------------------------- risk loop
rep('''                        if mint in market:
                            m = summarize(market[mint])
                            if m["price"] > 0:
                                p["last_price"], p["liq"], p["last_ok"] = m["price"], max(m["liq"], 1.0), time.time()
                        pnl = mark_pnl_pct(p)''', '''                        if mint in market:
                            m = summarize(market[mint])
                            if m["price"] > 0:
                                p["last_price"], p["liq"], p["last_ok"] = m["price"], max(m["liq"], 1.0), time.time()
                        if getattr(broker, "realistic", False):
                            try:  # v6: valore di uscita dal preventivo Jupiter reale (ogni MARK_QUOTE_SEC)
                                await broker.refresh_mark(mint, p)
                            except Exception:  # noqa: BLE001
                                pass
                        pnl = mark_pnl_pct(p)''')
rep('''            if http is not None:
                try:
                    await refresh_sol_usd(http)
                except Exception:  # noqa: BLE001
                    pass''', '''            if http is not None:
                try:
                    await refresh_sol_usd(http)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    await refresh_prio_fee(http, st)
                except Exception:  # noqa: BLE001
                    pass''')
rep('''            day_pct = (eq / st.day_start_equity - 1) * 100 if st.day_start_equity else 0.0''', '''            # v6: limite di perdita totale -> file HARD_STOP (cancellalo per ripartire da equity attuale)
            if st.hard_tripped and not os.path.exists(HARD_STOP_PATH):
                st.hard_tripped, st.total_start = False, eq
                log(st, "Risk", "pause", f"HARD_STOP rimosso: acquisti riattivati, nuova base ${eq:,.2f}")
            tot_pct = (eq / st.total_start - 1) * 100 if st.total_start else 0.0
            if not st.hard_tripped and tot_pct <= CFG["MAX_TOTAL_LOSS_PCT"]:
                st.hard_tripped = True
                with open(HARD_STOP_PATH, "w") as f:
                    f.write(f"perdita totale {tot_pct:.1f}% <= {CFG['MAX_TOTAL_LOSS_PCT']}% il "
                            f"{datetime.now(TZ):%d/%m %H:%M} (Europe/Rome)\\n")
                log(st, "Risk", "pause", f"perdita totale {tot_pct:.1f}%: HARD_STOP, nessun nuovo acquisto")
            day_pct = (eq / st.day_start_equity - 1) * 100 if st.day_start_equity else 0.0''')

# ---------------------------------------------------------------- main
rep('''        broker = PaperBroker() if mode == "paper" else SolanaBroker(http, mode)''',
    '''        broker = PaperBroker(http) if mode == "paper" else SolanaBroker(http, mode)''')
rep('''        save_state(st, force=True)
        q_an, q_ex = asyncio.Queue(), asyncio.Queue()''', '''        save_state(st, force=True)
        try:
            await readiness_check(st, http, broker)
        except Exception as e:  # noqa: BLE001
            log(st, "Logger", "warn", f"controllo di prontezza non riuscito ({type(e).__name__})")
        q_an, q_ex = asyncio.Queue(), asyncio.Queue()''')
rep('''# ======================================================================= avvio
async def supervise''', open("/workspace/memebot/dev/readiness_v6.py").read() + '''

# ======================================================================= avvio
async def supervise''')
open(P, "w").write(s)
print("patched")
