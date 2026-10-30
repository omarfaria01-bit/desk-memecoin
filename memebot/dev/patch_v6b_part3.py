P = "/workspace/memebot/desk.py"
s = open(P).read()

def rep(old, new, cnt=1):
    global s
    assert s.count(old) == cnt, (old[:90], s.count(old))
    s = s.replace(old, new)

# ---------------------------------------------------------------- Analyst
a0 = s.index("async def analyst_agent(st, http, q_in, q_out):")
a1 = s.index("# ======================================================================= Executor (broker)")
A = s[a0:a1]
A = A.replace('''            mint, m = c["mint"], c["m"]
            if not c.get("filter_ok"):''', '''            mint, m = c["mint"], c["m"]
            so = c.get("shadow_only")  # v6: scartato dal Filter per un segnale soft grave: solo shadow trade

            def rej(*a, _s=so, _mint=mint, _m=m, **k):
                if not _s:  # lo scarto e gia contato dal Filter
                    reject(st, _mint, _m["symbol"], *a, **k)
            if not c.get("filter_ok"):''', 1)
A = A.replace('reject(st, mint, m["symbol"], ', 'rej(')
A = A.replace('add_shadow(st, mint, m, ', 'so or add_shadow(st, mint, m, ')
old_tail = '''            detail = f"punteggio {score:.2f} (soglia {st.threshold:.2f})" + \\
                (f", grafico assente -{CFG['NO_CHART_PENALTY']:.2f}" if ch is None else "") + \\
                (f", narrativa {nb:+.3f}" if nb != 0 else "")
            if score < st.threshold:
                rej(detail, agent="Analyst", cat="punteggio basso")
                so or add_shadow(st, mint, m, "punteggio", f)
                continue'''
assert A.count(old_tail) == 1
A = A.replace(old_tail, '''            # v6: segnali soft graduati -> penalita sul punteggio e size ridotta (valori appresi entro limiti)
            flags = list((c.get("sec") or {}).get("soft") or [])
            pen = sum(soft_pen(st, fl) for fl in flags)
            smult = 1.0
            for fl in flags:
                smult *= soft_size(st, fl)
            score_raw = score
            score -= pen
            detail = f"punteggio {score:.2f} (soglia {st.threshold:.2f})" + \\
                (f", grafico assente -{CFG['NO_CHART_PENALTY']:.2f}" if ch is None else "") + \\
                (f", narrativa {nb:+.3f}" if nb != 0 else "") + \\
                (f", segnali soft {'/'.join(flags)} -{pen:.2f}" if flags else "")
            near = CFG["SHADOW_NEAR_SCORE"]
            if so:
                if CFG["SHADOW_TRADES"] and score >= st.threshold - near:
                    spawn_shadow(st, http, mint, m, c.get("shadow_keys") or ["multi"], f"scartato: {so}", score,
                                 flags)
                continue
            if score < st.threshold:
                rej(detail, agent="Analyst", cat="punteggio basso")
                add_shadow(st, mint, m, "punteggio", f)
                if CFG["SHADOW_TRADES"] and score_raw >= st.threshold - near:
                    spawn_shadow(st, http, mint, m, flags or ["score"], detail, score, flags)
                continue''')
A = A.replace('''            c.update({"analyst_ok": True, "features": f, "score": score, "narr_bonus": nb,''',
              '''            c.update({"analyst_ok": True, "features": f, "score": score, "narr_bonus": nb,
                      "soft": flags, "size_mult": round(smult, 4), "score_raw": round(score_raw, 4),''')
s = s[:a0] + A + s[a1:]

# ---------------------------------------------------------------- Executor
rep('''            if not (c.get("filter_ok") and c.get("analyst_ok")):
                log(st, "Executor", "block", f"{m['symbol']} bloccato: manca l'ok di Filter o Analyst")
                continue''', '''            if not (c.get("filter_ok") and c.get("analyst_ok")) or c.get("shadow_only"):
                log(st, "Executor", "block", f"{m['symbol']} bloccato: manca l'ok di Filter o Analyst")
                continue''')
rep('''    size = min(eq * frac, m["liq"] * CFG["MAX_POOL_SHARE_PCT"] / 100, cash, room)
''', '''    size = min(eq * frac, m["liq"] * CFG["MAX_POOL_SHARE_PCT"] / 100, cash, room)
    size *= clamp(fnum(c.get("size_mult"), 1.0), 0.05, 1.0)  # v6: segnali soft -> size ridotta (mai maggiorata)
''')
rep('''        "sell_quote_impact": None, "sell_latency_slip": None, "latency_sec": res.get("latency_sec"),
    }''', '''        "sell_quote_impact": None, "sell_latency_slip": None, "latency_sec": res.get("latency_sec"),
        "soft": c.get("soft") or [], "size_mult": c.get("size_mult", 1.0),
    }''')
rep('''        f"{rt:.1f}%{extra}). {c['thesis']}", mint=mint, size=round(res["cost"], 2),''',
    '''        f"{rt:.1f}%{extra})" + (f", segnali soft {'/'.join(c.get('soft'))} size x{c.get('size_mult', 1):.2f}"
                                if c.get("soft") else "") + f". {c['thesis']}", mint=mint, size=round(res["cost"], 2),
        soft=c.get("soft") or [], size_mult=c.get("size_mult", 1.0),''')

# ---------------------------------------------------------------- chiusura: trade reali con segnali soft -> learner
rep('''         "fill_failed": p.get("fill_failed", 0), "fee_failed_usd": round(p.get("fee_failed_usd", 0.0), 4)}
    t.update({f"f_{k}": (p.get("features") or {}).get(k) for k in FEATURES})''',
    '''         "fill_failed": p.get("fill_failed", 0), "fee_failed_usd": round(p.get("fee_failed_usd", 0.0), 4),
         "soft_flags": "|".join(p.get("soft") or []), "size_mult": p.get("size_mult", 1.0)}
    t.update({f"f_{k}": (p.get("features") or {}).get(k) for k in FEATURES})''')
rep('''    learn_from_trade(st, p, pnl, reason)
    save_state(st, force=True)''', '''    learn_from_trade(st, p, pnl, reason)
    if p.get("soft"):  # v6: anche i trade veri con segnali soft alimentano le statistiche per segnale
        record_outcome(st, p["soft"], t["pnl_pct"], zero or "rug" in reason or "invendibile" in reason, "real")
    save_state(st, force=True)''')

# ---------------------------------------------------------------- shadow trade book
rep('''# ======================================================================= recap
def build_recap(st):''', r'''# ======================================================================= v6: shadow trade
# Operazioni ipotetiche (nessun denaro, nessun ordine) sui token scartati per un segnale soft o con un punteggio
# appena sotto la soglia. Stesse regole d'uscita del Risk, costi con la formula del paper. Gli esiti vanno in
# shadow_trades.csv e nelle statistiche del Learner (learner_shadow).
SHADOW_CSV = os.path.join(DATA_DIR, "shadow_trades.csv")


def write_csv_row(path, row):
    fields = list(row.keys())
    if os.path.exists(path):
        with open(path, newline="") as f:
            head = next(csv.reader(f), [])
        if head != fields:
            os.replace(path, path.replace(".csv", f"_old_{int(time.time())}.csv"))
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow(row)


def spawn_shadow(st, http, mint, m, keys, reason, score, flags):
    if mint in st.shadow_trades or mint in st.positions:
        return
    t = asyncio.ensure_future(open_shadow(st, http, mint, m, keys, reason, score, flags))
    SHADOW_TASKS.add(t)
    t.add_done_callback(SHADOW_TASKS.discard)


def _close_shadow_row(st, mint, sh, pnl, rug, why, now=None):
    now = now or time.time()
    row = {"aperto": datetime.fromtimestamp(sh["t"], TZ).strftime("%Y-%m-%d %H:%M"),
           "chiuso": datetime.fromtimestamp(now, TZ).strftime("%Y-%m-%d %H:%M"), "symbol": sh["sym"], "mint": mint,
           "motivo_scarto": sh["reason"][:160], "chiavi": "|".join(sh["keys"]), "segnali_soft": "|".join(sh["flags"]),
           "score": sh["score"], "soglia": sh["thr"], "prezzo_entrata": sh["price"], "liq_entrata": round(sh["liq_entry"]),
           "rt_cost_pct": sh["rt"], "quote_src": sh.get("src", "formula"), "pnl_pct": round(pnl, 2),
           "peak_pct": round(sh.get("peak_pct", 0.0), 2), "minuti": int((now - sh["t"]) / 60), "uscita": why,
           "rug": int(bool(rug or pnl <= -50))}
    write_csv_row(SHADOW_CSV, row)
    st.stats["shadow_closed"] += 1
    record_outcome(st, sh["keys"], pnl, rug, "shadow", now)
    write_event("Learner", "shadow_close", f"shadow {sh['sym']} chiuso {pnl:+.1f}%: {why}",
                {"mint": mint, "keys": sh["keys"], "pnl_pct": round(pnl, 2), "rug": row["rug"]})


async def open_shadow(st, http, mint, m, keys, reason, score, flags, now=None):
    """Apre uno shadow trade. Ritorna lo shadow, 'rug' se non c'e percorso di vendita, None se non aperto."""
    if not CFG["SHADOW_TRADES"] or mint in st.shadow_trades or mint in st.positions or m["price"] <= 0:
        return None
    if len(st.shadow_trades) >= CFG["SHADOW_MAX_OPEN"]:
        st.stats["shadow_full"] += 1
        return None
    now = now or time.time()
    size = CFG["SHADOW_SIZE_USD"]
    rt = roundtrip_cost_pct(size, m["liq"])
    tokens, fill, _, _ = buy_tokens(size, m["price"], m["liq"])
    src, dec = "formula", None
    br = BROKER_REF[0]
    if CFG["SHADOW_ROUTE_CHECK"] and br is not None and getattr(br, "realistic", False):
        pre = await br.precheck(st, mint, m, size)  # solo preventivi, nessuna transazione
        if pre and not pre.get("ok", True):
            if pre.get("cat") in ("non rivendibile", "nessun percorso Jupiter"):
                sh = {"t": now, "sym": m["symbol"], "reason": reason, "keys": list(keys), "flags": list(flags),
                      "score": round(score, 4), "thr": round(st.threshold, 4), "price": m["price"],
                      "liq_entry": m["liq"], "rt": None, "src": "jupiter", "peak_pct": 0.0}
                _close_shadow_row(st, mint, sh, -100.0, True, pre["why"], now)
                return "rug"
            return None  # rivendita troppo cara: neanche il trade vero sarebbe partito
        if pre and pre.get("rt_pct") is not None:
            rt, src, dec = pre["rt_pct"], "jupiter", pre.get("dec")
            tokens = pre.get("tokens") or tokens
    if rt * CFG["COST_GATE_MULT"] > CFG["TP1_PCT"] or tokens <= 0:
        return None  # stesso filtro costi dell'Executor
    sh = {"t": now, "sym": m["symbol"], "reason": reason, "keys": list(keys), "flags": list(flags),
          "score": round(score, 4), "thr": round(st.threshold, 4), "price": m["price"], "last_price": m["price"],
          "liq": max(m["liq"], 1.0), "liq_entry": m["liq"], "last_ok": now, "cost": size, "tokens": tokens,
          "tokens_entry": tokens, "entry_mark": size / tokens, "realized": 0.0, "tp_stage": 0, "peak_pct": 0.0,
          "stop_hits": 0, "rt": round(rt, 2), "src": src, "dec": dec}
    st.shadow_trades[mint] = sh
    st.stats["shadow_open"] += 1
    write_event("Learner", "shadow_open", f"shadow {m['symbol']} aperto ({'|'.join(keys)}): {reason[:120]}",
                {"mint": mint, "keys": list(keys), "score": round(score, 4), "rt_cost_pct": round(rt, 2)})
    return sh


def shadow_decide(sh, now=None):
    """Stesse regole d'uscita del Risk. Ritorna (motivo, frazione, azzera, rug) o None."""
    now = now or time.time()
    pnl = mark_pnl_pct(sh)
    sh["peak_pct"] = max(sh["peak_pct"], pnl)
    sl, tp, tpf, tdist = CFG["STOP_LOSS_PCT"], CFG["TAKE_PROFIT_PCT"], CFG["TAKE_PROFIT_FRACTION"], CFG["TRAIL_DISTANCE_PCT"]
    stage = int(sh.get("tp_stage", 0))
    tp1 = min(CFG["TP1_PCT"], tp)
    rung_pct, rung_frac = (tp1, CFG["TP1_FRACTION"]) if stage == 0 else (tp, tpf)
    tdist_eff = min(tdist, CFG["TRAIL_TIGHT_PCT"]) if sh["peak_pct"] >= CFG["TRAIL_TIGHT_AFTER_PCT"] else tdist
    stop_eff = max(sl, min(CFG["BREAKEVEN_PCT"], tp1 - 1)) if stage >= 1 else sl
    sh["stop_hits"] = sh["stop_hits"] + 1 if pnl <= stop_eff else 0
    held = (now - sh["t"]) / 60
    if now - sh["last_ok"] > 900:
        return "nessun prezzo da 15 min, possibile rug", 1.0, True, True
    if sh["liq_entry"] > 0 and sh["liq"] < sh["liq_entry"] * (1 - CFG["LIQ_DROP_EXIT_PCT"] / 100):
        drop = (1 - sh["liq"] / sh["liq_entry"]) * 100
        return f"liquidita crollata del {drop:.0f}%", 1.0, False, drop >= 80
    if pnl <= CFG["CRASH_EXIT_PCT"]:
        return f"crollo a {pnl:.1f}%", 1.0, False, False
    if sh["stop_hits"] >= 2:
        return ("stop a pareggio dopo TP1, ora " if stage >= 1 else "stop loss a ") + f"{pnl:.1f}%", 1.0, False, False
    if stage < 2 and pnl >= rung_pct:
        frac = clamp(safe_div(rung_frac * sh["tokens_entry"], sh["tokens"], 1.0), 0.0, 1.0)
        sh["tp_stage"] = stage + 1
        return f"presa di profitto {stage + 1}/2 a {pnl:+.0f}%", frac, False, False
    if sh["peak_pct"] >= CFG["TRAIL_ACTIVATE_PCT"] and pnl <= sh["peak_pct"] - tdist_eff:
        return f"trailing {tdist_eff:g}%: massimo {sh['peak_pct']:+.1f}%, ora {pnl:+.1f}%", 1.0, False, False
    if stage == 0 and held > CFG["TIME_STOP_MIN"] and pnl < CFG["TIME_STOP_MIN_PCT"]:
        return f"stop a tempo: {held:.0f} min e solo {pnl:+.1f}%", 1.0, False, False
    if held > CFG["MAX_HOLD_MIN"]:
        return f"tempo massimo {CFG['MAX_HOLD_MIN']} min", 1.0, False, False
    return None


async def shadow_apply(st, http, mint, sh, dec, now=None):
    """Applica una decisione d'uscita allo shadow. Ritorna il pnl finale se chiuso, None se resta aperto."""
    if not dec:
        return None
    why, frac, zero, rug = dec
    if frac >= 0.999 and not zero and CFG["SHADOW_ROUTE_CHECK"] and http is not None and sh.get("dec") is not None:
        raw = int(sh["tokens"] * 10 ** int(sh["dec"]))
        _, err = await jup_quote(http, mint, WSOL, raw)  # solo preventivo: c'e ancora un percorso di vendita?
        if err == "no_route":
            why, zero, rug = why + "; nessun percorso di vendita su Jupiter", True, True
    sold = sh["tokens"] * frac
    sh["realized"] += 0.0 if zero else exit_value(sold, sh["last_price"], sh["liq"])
    sh["tokens"] = max(0.0, sh["tokens"] - sold)
    if frac < 0.999 and sh["tokens"] > 0:
        return None
    st.shadow_trades.pop(mint, None)
    pnl = (sh["realized"] / sh["cost"] - 1) * 100 if sh["cost"] else 0.0
    _close_shadow_row(st, mint, sh, pnl, rug, why, now)
    return pnl


async def shadow_trade_agent(st, http):
    while True:
        await asyncio.sleep(CFG["SHADOW_CHECK_SEC"])
        if not st.shadow_trades:
            continue
        try:
            market = await dex_batch(http, list(st.shadow_trades.keys()))
            now = time.time()
            for mint, sh in list(st.shadow_trades.items()):
                if mint in market:
                    m = summarize(market[mint])
                    if m["price"] > 0:
                        sh["last_price"], sh["liq"], sh["last_ok"] = m["price"], max(m["liq"], 1.0), now
                await shadow_apply(st, http, mint, sh, shadow_decide(sh, now), now)
            save_state(st)
        except Exception as e:  # noqa: BLE001
            log(st, "Learner", "error", f"shadow trade: errore ({type(e).__name__}), continuo")


# ======================================================================= recap
def build_recap(st):''')

# ---------------------------------------------------------------- main
rep('''        broker = PaperBroker(http) if mode == "paper" else SolanaBroker(http, mode)
''', '''        broker = PaperBroker(http) if mode == "paper" else SolanaBroker(http, mode)
        BROKER_REF[0] = broker  # v6: gli shadow trade usano solo i preventivi (precheck), mai buy/sell
''')
rep('''            "Learner": lambda: shadow_agent(st, http),
''', '''            "Learner": lambda: shadow_agent(st, http),
            "Shadow": lambda: shadow_trade_agent(st, http),
''')

# ---------------------------------------------------------------- override: nuove soglie con limiti
rep('''                  "TRAIL_TIGHT_DIST_PCT": "TRAIL_TIGHT_PCT"}''', '''                  "TRAIL_TIGHT_DIST_PCT": "TRAIL_TIGHT_PCT",
                  # v6: soglie dei segnali soft (le regole HARD non sono modificabili da override)
                  "TOP10_HARD_PCT": "TOP10_HARD_PCT", "EQUAL_HARD_PCT": "EQUAL_HARD_PCT",
                  "BUNDLE_SOFT_PCT": "BUNDLE_SOFT_PCT", "BUNDLE_HARD_PCT": "BUNDLE_HARD_PCT",
                  "LP_HARD_PCT": "LP_HARD_PCT", "SOFT_PENALTY": "SOFT_PENALTY", "SOFT_SIZE_MULT": "SOFT_SIZE_MULT",
                  "SOFT_MAX_FLAGS": "SOFT_MAX_FLAGS", "SHADOW_NEAR_SCORE": "SHADOW_NEAR_SCORE"}''')
rep('''              "TRAIL_TIGHT_AFTER_PCT": (20.0, 1000.0), "TRAIL_TIGHT_PCT": (2.0, 15.0)}
OVR_INT_KEYS = {"MAX_HOLD_MIN", "TIME_STOP_MIN"}''', '''              "TRAIL_TIGHT_AFTER_PCT": (20.0, 1000.0), "TRAIL_TIGHT_PCT": (2.0, 15.0),
              # v6: segnali soft
              "TOP10_HARD_PCT": (40.0, 60.0), "EQUAL_HARD_PCT": (5.0, 20.0), "BUNDLE_SOFT_PCT": (1.0, 10.0),
              "BUNDLE_HARD_PCT": (10.0, 40.0), "LP_HARD_PCT": (10.0, 50.0), "SOFT_PENALTY": (0.0, 0.15),
              "SOFT_SIZE_MULT": (0.2, 1.0), "SOFT_MAX_FLAGS": (2.0, 4.0), "SHADOW_NEAR_SCORE": (0.0, 0.10)}
OVR_INT_KEYS = {"MAX_HOLD_MIN", "TIME_STOP_MIN", "SOFT_MAX_FLAGS"}''')
open(P, "w").write(s)
print("ok")
