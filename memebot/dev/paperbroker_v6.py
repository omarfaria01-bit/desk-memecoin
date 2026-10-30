class PaperBroker:
    """Paper. Senza http (test) usa la formula v5. Con http e SIM_REALISTIC simula il live: preventivi Jupiter
    reali alla size vera, latenza 1-3 s, transazioni che falliscono (pagando comunque la fee), slippage
    massimo come in live, priority fee dinamica e SOL di riserva. NON firma e NON invia nulla."""
    mode = "paper"

    def __init__(self, http=None):
        self.http = http
        self.realistic = http is not None and bool(CFG.get("SIM_REALISTIC"))
        if CFG.get("SIM_SEED") is not None:
            SIM["rng"].seed(CFG["SIM_SEED"])

    async def setup(self, st):
        return None

    async def cash_usd(self, st):
        if not self.realistic:
            return st.cash
        # v6: come in live restano sempre SOL_RESERVE SOL per le fee + l'affitto dei conti token aperti
        locked = (CFG["SOL_RESERVE"] + CFG["SIM_ATA_RENT_SOL"] * len(st.positions)) * sol_usd()
        return max(0.0, st.cash - locked)

    # ---------------------------------------------------------------- formula v5 (fallback)
    def _buy_formula(self, st, m, size_usd, src="formula", why=None):
        # (v4) fee 1% + impatto; (v5) anche slippage 2 x size/liq e priority fee (vedi buy_tokens)
        tokens, fill, impact, fee = buy_tokens(size_usd, m["price"], m["liq"])
        if fill <= 0 or tokens <= 0:
            return {"ok": False, "error": "prezzo non valido"}
        st.cash -= size_usd
        return {"ok": True, "cost": size_usd, "tokens": tokens, "fill": fill, "impact": impact, "fee": fee,
                "src": src, "prio_fee_sol": prio_fee_sol(), "note": f"formula ({why})" if why else None}

    def _sell_formula(self, st, p, fraction, urgent=False):
        tokens = p["tokens"] * fraction
        value = exit_value(tokens, p["last_price"], p["liq"])  # stessi costi del valore di mercato
        st.cash += value
        return {"ok": True, "value": value, "tokens": tokens, "src": "formula", "prio_fee_sol": prio_fee_sol(urgent)}

    def _sol_in(self, size_usd):
        net = size_usd * (1 - CFG["SIM_QUOTE_EXTRA_FEE_PCT"] / 100) - prio_fee_usd()
        return max(0.0, min(net / sol_usd(), CFG["MAX_SOL_PER_TRADE"]))

    def _fail_fee(self, st, urgent=False):
        """Una transazione fallita on-chain paga comunque base fee + priority fee."""
        usd = prio_fee_usd(urgent)
        st.cash -= usd
        st.stats["fee_failed_usd"] = round(st.stats.get("fee_failed_usd", 0.0) + usd, 6)
        st.stats["fill_failed"] += 1
        return usd

    def _latency(self):
        lo, hi = CFG["SIM_LATENCY_MIN_SEC"], max(CFG["SIM_LATENCY_MIN_SEC"], CFG["SIM_LATENCY_MAX_SEC"])
        return SIM["rng"].uniform(lo, hi)

    # ---------------------------------------------------------------- controllo prima dell'acquisto
    async def precheck(self, st, mint, m, size_usd):
        """Preventivo d'acquisto alla size reale + preventivo di rivendita dei token ottenuti.
        Ritorna {"ok": False, "why", "cat"} se va scartato, None se i preventivi non sono disponibili."""
        if not (self.realistic and CFG.get("SIM_USE_QUOTES")):
            return None
        dec = await token_decimals(self.http, mint)
        if dec is None:
            return None
        sol_in = self._sol_in(size_usd)
        lam = int(sol_in * 1e9)
        q, err = await jup_quote(self.http, WSOL, mint, lam)
        if err == "no_route":
            return {"ok": False, "why": "nessun percorso d'acquisto su Jupiter", "cat": "nessun percorso Jupiter"}
        if not q:
            return None
        raw_out = int(q["outAmount"])
        tokens = raw_out / 10 ** dec
        expected = sol_in * sol_usd() / m["price"] if m["price"] > 0 else 0.0
        impact = (1 - tokens / expected) * 100 if expected > 0 else None
        pre = {"ok": True, "src": "jupiter", "dec": dec, "lam": lam, "sol_in": sol_in, "raw_out": raw_out,
               "tokens": tokens, "quote_impact": round(impact, 3) if impact is not None else None,
               "jup_impact_pct": round(fnum(q.get("priceImpactPct")) * 100, 4), "rt_pct": None}
        q2, err2 = await jup_quote(self.http, mint, WSOL, raw_out)
        if err2 == "no_route":
            return {"ok": False, "why": "nessun percorso di vendita su Jupiter (possibile honeypot)",
                    "cat": "non rivendibile"}
        if q2:
            back = int(q2["outAmount"]) / 1e9 * sol_usd() * (1 - CFG["SIM_QUOTE_EXTRA_FEE_PCT"] / 100) - prio_fee_usd()
            rt = (1 - back / size_usd) * 100 if size_usd > 0 else 100.0
            pre["rt_pct"] = round(rt, 3)
            if rt > CFG["SELLCHECK_MAX_RT_PCT"]:
                return {"ok": False, "why": f"rivendita immediata perderebbe il {rt:.1f}% (> "
                                            f"{CFG['SELLCHECK_MAX_RT_PCT']:g}%)", "cat": "rivendita troppo cara"}
        return pre

    # ---------------------------------------------------------------- acquisto
    async def buy(self, st, mint, m, size_usd, pre=None):
        if not pre or pre.get("src") != "jupiter":
            return self._buy_formula(st, m, size_usd, why=None if not self.realistic else "preventivo non disponibile")
        fails, fee_lost, lat_tot = 0, 0.0, 0.0
        for _ in range(1 + max(0, int(CFG["SIM_BUY_RETRIES"]))):
            lat = self._latency()
            lat_tot += lat
            await asyncio.sleep(lat)
            if stop_active():
                return {"ok": False, "error": "arresto di emergenza attivo, ordine non inviato", "fill_failed": fails}
            q, err = await jup_quote(self.http, WSOL, mint, pre["lam"])
            if err == "no_route":
                return {"ok": False, "error": "percorso Jupiter sparito al momento del fill", "fill_failed": fails + 1,
                        "fee_failed_usd": fee_lost}
            if not q:  # rete: il preventivo del segnale resta il migliore dato disponibile
                q = {"outAmount": pre["raw_out"]}
            raw = int(q["outAmount"])
            slip = (1 - raw / pre["raw_out"]) * 100  # >0 = prezzo peggiorato durante la latenza
            if slip * 100 > CFG["SLIPPAGE_BPS"]:
                fails += 1
                fee_lost += self._fail_fee(st)
                log(st, "Executor", "fill_failed", f"acquisto simulato fallito: prezzo salito del {slip:.1f}% in "
                    f"{lat:.1f}s (> slippage {CFG['SLIPPAGE_BPS']} bps)", mint=mint, fill_failed=True,
                    latency_slip=round(slip, 3))
                continue
            if SIM["rng"].random() < CFG["SIM_FAIL_PROB_BUY"]:
                fails += 1
                fee_lost += self._fail_fee(st)
                log(st, "Executor", "fill_failed", "acquisto simulato fallito: transazione non confermata",
                    mint=mint, fill_failed=True)
                continue
            tokens = raw / 10 ** pre["dec"]
            st.cash -= size_usd
            expected = pre["sol_in"] * sol_usd() / m["price"] if m["price"] > 0 else 0.0
            impact = (1 - tokens / expected) if expected > 0 else 0.0
            return {"ok": True, "cost": size_usd, "tokens": tokens, "fill": size_usd / tokens, "impact": impact,
                    "fee": size_usd * CFG["SIM_QUOTE_EXTRA_FEE_PCT"] / 100 + prio_fee_usd(), "dec": pre["dec"],
                    "src": "jupiter", "quote_impact": round(impact * 100, 3), "latency_slip": round(slip, 3),
                    "prio_fee_sol": round(prio_fee_sol() * (1 + fails), 8), "fill_failed": fails,
                    "fee_failed_usd": round(fee_lost, 6), "latency_sec": round(lat_tot, 2),
                    "note": f"preventivo Jupiter, latenza {lat_tot:.1f}s" + (f", {fails} tentativi falliti" if fails else "")}
        return {"ok": False, "error": f"{fails} transazioni simulate fallite", "fill_failed": fails,
                "fee_failed_usd": fee_lost}

    # ---------------------------------------------------------------- vendita
    async def sell(self, st, mint, p, fraction, urgent=False):
        if not (self.realistic and CFG.get("SIM_USE_QUOTES")):
            return self._sell_formula(st, p, fraction, urgent)
        dec = p.get("dec")
        if dec is None:
            dec = await token_decimals(self.http, mint)
        if dec is None:
            return self._sell_formula(st, p, fraction, urgent)
        tokens = p["tokens"] * fraction
        raw = int(tokens * 10 ** dec)
        if raw <= 0:
            return {"ok": True, "value": 0.0, "tokens": tokens, "src": "jupiter"}
        slip_bps = CFG["SLIPPAGE_BPS"] if p.get("sell_fails", 0) < 2 else CFG["PANIC_SLIPPAGE_BPS"]
        q0, err = await jup_quote(self.http, mint, WSOL, raw, slip_bps)
        if err == "no_route":  # come in live: senza percorso non si vende, il Risk riprova
            return {"ok": False, "error": "nessun percorso di vendita su Jupiter", "no_route": True, "fill_failed": 1}
        if not q0:
            return self._sell_formula(st, p, fraction, urgent)
        lat = self._latency()
        await asyncio.sleep(lat)
        q1, err1 = await jup_quote(self.http, mint, WSOL, raw, slip_bps)
        if err1 == "no_route":
            return {"ok": False, "error": "percorso di vendita sparito durante la latenza", "no_route": True,
                    "fill_failed": 1}
        out0 = int(q0["outAmount"])
        out1 = int(q1["outAmount"]) if q1 else out0
        slip = (1 - out1 / out0) * 100
        prio = prio_fee_sol(urgent)
        if slip * 100 > slip_bps:
            fee = self._fail_fee(st, urgent)
            return {"ok": False, "error": f"prezzo sceso del {slip:.1f}% in {lat:.1f}s (> slippage {slip_bps} bps)",
                    "fill_failed": 1, "fee_failed_usd": fee, "prio_fee_sol": prio, "latency_slip": round(slip, 3)}
        pfail = CFG["SIM_FAIL_PROB_SELL_FAST"] if urgent else CFG["SIM_FAIL_PROB_SELL"]
        if SIM["rng"].random() < pfail:
            fee = self._fail_fee(st, urgent)
            return {"ok": False, "error": "transazione simulata non confermata" + (" (rete congestionata)" if urgent else ""),
                    "fill_failed": 1, "fee_failed_usd": fee, "prio_fee_sol": prio}
        gross = out1 / 1e9 * sol_usd()
        value = max(0.0, gross * (1 - CFG["SIM_QUOTE_EXTRA_FEE_PCT"] / 100) - prio * sol_usd())
        disp = tokens * p["last_price"]
        st.cash += value
        return {"ok": True, "value": value, "tokens": tokens, "src": "jupiter",
                "quote_impact": round((1 - gross / disp) * 100, 3) if disp > 0 else None,
                "latency_slip": round(slip, 3), "prio_fee_sol": round(prio, 8), "fill_failed": 0, "latency_sec": round(lat, 2)}

    # ---------------------------------------------------------------- valore di mercato realistico
    async def refresh_mark(self, mint, p):
        if not (self.realistic and CFG.get("SIM_USE_QUOTES")) or p.get("tokens", 0) <= 0 or p.get("last_price", 0) <= 0:
            return
        q = p.get("qmark") or {}
        if time.time() - q.get("ts", 0) < CFG["MARK_QUOTE_SEC"]:
            return
        dec = p.get("dec")
        if dec is None:
            dec = await token_decimals(self.http, mint)
            if dec is None:
                return
            p["dec"] = dec
        qq, err = await jup_quote(self.http, mint, WSOL, int(p["tokens"] * 10 ** dec))
        if err == "no_route":
            p["qmark"] = {"ts": time.time(), "ratio": 1e-9, "no_route": True}  # invendibile: valore ~0
            return
        if not qq:
            return
        gross = int(qq["outAmount"]) / 1e9 * sol_usd()
        disp = p["tokens"] * p["last_price"]
        p["qmark"] = {"ts": time.time(), "ratio": clamp(gross / disp, 1e-9, 2.0), "impact_pct": round((1 - gross / disp) * 100, 3)}
