P = "/workspace/memebot/desk.py"
s = open(P).read()
def rep(old, new, cnt=1):
    global s
    n = s.count(old)
    assert n == cnt, (n, old[:90])
    s = s.replace(old, new)

# ---- sources import
rep('''import aiohttp
''', '''import aiohttp
''' if False else '''import aiohttp
''')
rep('''HARD_STOP_PATH = os.path.join(DATA_DIR, "HARD_STOP")
''', '''HARD_STOP_PATH = os.path.join(DATA_DIR, "HARD_STOP")
# v7: moduli opzionali in sources/ (solo lettura). Se mancano il bot gira come prima, senza bonus.
SRC = {}
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from sources import fomo_trending as _fomo, kol_holders as _kol, kol_theses_reader as _theses, \\
        momentum as _mom  # noqa: E402
    _kol.CALL_GAP = max(getattr(_kol, "CALL_GAP", 0.15), 0.5)   # RPC pubblica condivisa: piano
    SRC = {"fomo": _fomo, "kol": _kol, "theses": _theses, "mom": _mom}
except Exception as _e:  # noqa: BLE001
    SRC = {"error": f"{type(_e).__name__}: {_e}"[:160]}
''')

# ---- State
rep('''        self.shadow_trades = {}    # v6: operazioni ipotetiche aperte (nessun denaro)
        # non salvati''', '''        self.shadow_trades = {}    # v6: operazioni ipotetiche aperte (nessun denaro)
        self.learner_bonus = {}    # v7: esiti per segnale/bonus + correzioni apprese
        self.labels = dict(CFG.get("LABELLED_CASES") or {})  # v7: casi etichettati (mint -> etichetta)
        # non salvati
        self.fomo_rank = {}''')
rep('''            "learner_shadow": self.learner_shadow, "shadow_trades": self.shadow_trades,
        }''', '''            "learner_shadow": self.learner_shadow, "shadow_trades": self.shadow_trades,
            "learner_bonus": self.learner_bonus, "labels": self.labels,
        }''')
rep('''    ls = d.get("learner_shadow")
    st.learner_shadow = ls if isinstance(ls, dict) else {}
''', '''    ls = d.get("learner_shadow")
    st.learner_shadow = ls if isinstance(ls, dict) else {}
    for old, new in (("lp", "lp_unlocked"), ("lp_grave", "lp_unlocked_grave")):  # v7: nome nuovo del segnale LP
        if old in st.learner_shadow and new not in st.learner_shadow:
            st.learner_shadow[new] = st.learner_shadow.pop(old)
    lb = d.get("learner_bonus")
    st.learner_bonus = lb if isinstance(lb, dict) else {}
    lab = d.get("labels")
    if isinstance(lab, dict):
        st.labels.update(lab)
''')

# ---- Filter: liq_max + src
rep('''                m = summarize(pairs)
                if m["vol_h1"] < CFG["MIN_VOLUME_H1"]:
                    continue
                st.watch.pop(mint, None)''', '''                m = summarize(pairs)
                w = st.watch[mint]
                w["liq_max"] = max(fnum(w.get("liq_max")), m["liq"])  # v7: per vedere una rimozione LP in corso
                m["liq_max"], m["src"] = w["liq_max"], w.get("src", "")
                if m["vol_h1"] < CFG["MIN_VOLUME_H1"]:
                    continue
                st.watch.pop(mint, None)''')

# ---- Analyst: shadow on soft rejects
for why in ('"momento"',):
    pass
s = s.replace('''                so or add_shadow(st, mint, m, "momento")
''', '''                so or add_shadow(st, mint, m, "momento")
                so or analyst_shadow(st, http, mint, m, c, "momento/momentum")
''')
rep('''                so or add_shadow(st, mint, m, "grafico")
''', '''                so or add_shadow(st, mint, m, "grafico")
                so or analyst_shadow(st, http, mint, m, c, "grafico assente")
''')

# ---- Analyst: bonuses + cluster
rep('''            flags = list((c.get("sec") or {}).get("soft") or [])
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
            near = CFG["SHADOW_NEAR_SCORE"]''', '''            flags = list((c.get("sec") or {}).get("soft") or [])
            pen = sum(soft_pen(st, fl) for fl in flags)
            smult = 1.0
            for fl in flags:
                smult *= soft_size(st, fl)
            # v7: segnali extra (Fomo, KOL, tesi KOL, momentum giovane): solo qui, dopo TUTTI i filtri HARD;
            # la somma ha un tetto (TOTAL_BONUS_CAP) e un bonus non salva mai uno scarto HARD.
            signals = [f"src:{m.get('src') or '?'}"]
            bonus = 0.0
            if CFG["SIGNALS_ENABLED"] and not so and score - pen >= st.threshold - bonus_eff(st, "TOTAL_BONUS_CAP"):
                try:
                    bonus, btags = await compute_bonus(st, mint, m)
                    signals += btags
                except Exception as e:  # noqa: BLE001
                    log(st, "Analyst", "warn", f"bonus segnali non calcolati ({type(e).__name__})")
            score += bonus
            score_raw = score
            score -= pen
            # v7: controllo cluster on-chain (gratis, RPC pubblica) solo sui token che passerebbero
            cl = None
            if CFG["CLUSTER_CHECK"] and not so and score >= st.threshold:
                try:
                    cl = await cluster_scan(st, http, mint, c.get("sec") or {})
                except Exception as e:  # noqa: BLE001
                    log(st, "Analyst", "warn", f"controllo cluster non riuscito ({type(e).__name__})")
                if cl:
                    signals.append(f"cluster:{cl['level']}:{cl['networks']}n/{cl['wallets']}w/"
                                   f"{cl['net_pct']:.1f}%/fresh{cl['fresh_share']:.2f}")
                    log(st, "Analyst", "cluster", f"{m['symbol']} cluster {cl['level']}: {cl['why'] or 'nessuno'}",
                        mint=mint, **{k: cl[k] for k in ("level", "networks", "net_pct", "wallets", "fresh_share",
                                                         "scanned")})
                if cl and cl["level"] == "hard":
                    reason = f"cluster estremo di wallet collegati: {cl['why']}"
                    rej(reason, agent="Analyst", cat="cluster")
                    if CFG["SHADOW_TRADES"]:  # la soglia estrema non viene mai allentata: si misura soltanto
                        spawn_shadow(st, http, mint, m, ["cluster_hard"], reason, score, flags + ["cluster"],
                                     tag="cluster", signals=signals)
                    continue
                if cl and cl["level"] == "soft" and "cluster" not in flags:
                    flags.append("cluster")
                    pen += soft_pen(st, "cluster")
                    score -= soft_pen(st, "cluster")
                    smult *= soft_size(st, "cluster")
            if bonus:
                signals.append(f"bonus:{bonus:.3f}")
            detail = f"punteggio {score:.2f} (soglia {st.threshold:.2f})" + \\
                (f", grafico assente -{CFG['NO_CHART_PENALTY']:.2f}" if ch is None else "") + \\
                (f", narrativa {nb:+.3f}" if nb != 0 else "") + \\
                (f", segnali {bonus:+.3f}" if bonus else "") + \\
                (f", segnali soft {'/'.join(flags)} -{pen:.2f}" if flags else "")
            near = CFG["SHADOW_NEAR_SCORE"]''')
rep('''                    spawn_shadow(st, http, mint, m, c.get("shadow_keys") or ["multi"], f"scartato: {so}", score,
                                 flags)''', '''                    spawn_shadow(st, http, mint, m, c.get("shadow_keys") or ["multi"], f"scartato: {so}", score,
                                 flags, signals=signals)''')
rep('''                    spawn_shadow(st, http, mint, m, flags or ["score"], detail, score, flags)''',
    '''                    spawn_shadow(st, http, mint, m, flags or ["score"], detail, score, flags, signals=signals)''')
rep('''                      "soft": flags, "size_mult": round(smult, 4), "score_raw": round(score_raw, 4),''',
    '''                      "soft": flags, "size_mult": round(smult, 4), "score_raw": round(score_raw, 4),
                      "signals": "|".join(signals), "cluster": cl,''')

# ---- Executor: position fields
rep('''        "soft": c.get("soft") or [], "size_mult": c.get("size_mult", 1.0),''',
    '''        "soft": c.get("soft") or [], "size_mult": c.get("size_mult", 1.0),
        "signals": c.get("signals", ""),  # v7
        "max_hold_min": CFG["LP_SOFT_MAX_HOLD_MIN"] if "lp_unlocked" in (c.get("soft") or []) else None,''')

# ---- Risk: max hold per position + rug event
rep('''                                    "max_hold": CFG["MAX_HOLD_MIN"], "time_stop": CFG["TIME_STOP_MIN"]}''',
    '''                                    "max_hold": pos_max_hold(p), "time_stop": CFG["TIME_STOP_MIN"]}''')
rep('''                        elif held > CFG["MAX_HOLD_MIN"]:
                            await sell_position(st, broker, mint, f"tempo massimo {CFG['MAX_HOLD_MIN']} min")''',
    '''                        elif held > pos_max_hold(p):
                            await sell_position(st, broker, mint, f"tempo massimo {pos_max_hold(p):g} min" +
                                                (" (LP sbloccata)" if pos_max_hold(p) < CFG["MAX_HOLD_MIN"] else ""))''')

# ---- trade row + outcomes
rep('''         "soft_flags": "|".join(p.get("soft") or []), "size_mult": p.get("size_mult", 1.0)}''',
    '''         "soft_flags": "|".join(p.get("soft") or []), "size_mult": p.get("size_mult", 1.0),
         "signals": p.get("signals", "")}''')
rep('''    learn_from_trade(st, p, pnl, reason)
    if p.get("soft"):  # v6: anche i trade veri con segnali soft alimentano le statistiche per segnale
        record_outcome(st, p["soft"], t["pnl_pct"], zero or "rug" in reason or "invendibile" in reason, "real")''',
    '''    learn_from_trade(st, p, pnl, reason)
    rug = bool(zero or "rug" in reason or "invendibile" in reason or is_rug_event(reason, t["minutes"]))
    if rug and reason.startswith("liquidita crollata"):
        log(st, "Risk", "rug_event", f"{p['symbol']}: {reason} dopo {t['minutes']} min", mint=mint,
            soft=p.get("soft") or [], tag="lp_unlocked" if "lp_unlocked" in (p.get("soft") or []) else "")
    if p.get("soft"):  # v6: anche i trade veri con segnali soft alimentano le statistiche per segnale
        record_outcome(st, p["soft"], t["pnl_pct"], rug, "real")
    record_signals(st, mint, p.get("signals", ""), t["pnl_pct"], rug, shadow=False)
    label = st.labels.pop(mint, None)
    if label:  # v7: caso etichettato (es. il cluster visto su Bubblemaps): esempio per il Learner + lezione
        record_outcome(st, [label], t["pnl_pct"], rug, "real-label")
        st.lessons.append(f"{datetime.now(TZ):%d/%m %H:%M} caso etichettato '{label}' chiuso {t['pnl_pct']:+.1f}%"
                          f" in {t['minutes']} min ({reason[:60]})")
        log(st, "Learner", "label_outcome", f"caso '{label}' chiuso {t['pnl_pct']:+.1f}%", mint=mint, label=label,
            pnl_pct=t["pnl_pct"], rug=rug)''')

# ---- shadows
rep('''def spawn_shadow(st, http, mint, m, keys, reason, score, flags):
    if mint in st.shadow_trades or mint in st.positions:
        return
    t = asyncio.ensure_future(open_shadow(st, http, mint, m, keys, reason, score, flags))''',
    '''def shadow_tag(keys, flags):
    ks = list(keys) + list(flags or [])
    for k, tag in (("cluster", "cluster"), ("lp_unlocked", "lp_unlocked"), ("analyst_momentum", "analyst_momentum")):
        if any(x.startswith(k) for x in ks):
            return tag
    return "score" if list(keys) == ["score"] else "soft"


def spawn_shadow(st, http, mint, m, keys, reason, score, flags, tag=None, signals=None):
    if mint in st.shadow_trades or mint in st.positions:
        return
    t = asyncio.ensure_future(open_shadow(st, http, mint, m, keys, reason, score, flags,
                                          tag=tag or shadow_tag(keys, flags), signals=signals))''')
rep('''           "rug": int(bool(rug or pnl <= -50))}
    write_csv_row(SHADOW_CSV, row)
    st.stats["shadow_closed"] += 1
    record_outcome(st, sh["keys"], pnl, rug, "shadow", now)''',
    '''           "rug": int(bool(rug or pnl <= -50)), "tag": sh.get("tag", ""), "signals": "|".join(sh.get("signals") or [])}
    write_csv_row(SHADOW_CSV, row)
    st.stats["shadow_closed"] += 1
    record_outcome(st, sh["keys"], pnl, rug, "shadow", now)
    record_signals(st, mint, row["signals"], pnl, rug, shadow=True)''')
rep('''async def open_shadow(st, http, mint, m, keys, reason, score, flags, now=None):
    """Apre uno shadow trade. Ritorna lo shadow, 'rug' se non c'e percorso di vendita, None se non aperto."""
    if not CFG["SHADOW_TRADES"] or mint in st.shadow_trades or mint in st.positions or m["price"] <= 0:
        return None
    if len(st.shadow_trades) >= CFG["SHADOW_MAX_OPEN"]:
        st.stats["shadow_full"] += 1
        return None''', '''async def open_shadow(st, http, mint, m, keys, reason, score, flags, now=None, tag=None, signals=None):
    """Apre uno shadow trade. Ritorna lo shadow, 'rug' se non c'e percorso di vendita, None se non aperto."""
    if not CFG["SHADOW_TRADES"] or mint in st.shadow_trades or mint in st.positions or m["price"] <= 0:
        return None
    tag = tag or shadow_tag(keys, flags)
    n_an = sum(1 for v in st.shadow_trades.values() if v.get("tag") == "analyst_momentum")
    if tag == "analyst_momentum":  # v7: tetto a parte, cosi non tolgono posto agli shadow dei segnali soft
        if n_an >= CFG["SHADOW_MAX_OPEN_ANALYST"]:
            st.stats["shadow_full"] += 1
            return None
    elif len(st.shadow_trades) - n_an >= CFG["SHADOW_MAX_OPEN"]:
        st.stats["shadow_full"] += 1
        return None
    signals = list(signals or [])
    max_hold = CFG["LP_SOFT_MAX_HOLD_MIN"] if any(str(f).startswith("lp_unlocked") for f in list(flags) + list(keys)) \\
        else None''')
rep('''                sh = {"t": now, "sym": m["symbol"], "reason": reason, "keys": list(keys), "flags": list(flags),
                      "score": round(score, 4), "thr": round(st.threshold, 4), "price": m["price"],
                      "liq_entry": m["liq"], "rt": None, "src": "jupiter", "peak_pct": 0.0}''',
    '''                sh = {"t": now, "sym": m["symbol"], "reason": reason, "keys": list(keys), "flags": list(flags),
                      "score": round(score, 4), "thr": round(st.threshold, 4), "price": m["price"],
                      "liq_entry": m["liq"], "rt": None, "src": "jupiter", "peak_pct": 0.0, "tag": tag,
                      "signals": signals}''')
rep('''          "stop_hits": 0, "rt": round(rt, 2), "src": src, "dec": dec}''',
    '''          "stop_hits": 0, "rt": round(rt, 2), "src": src, "dec": dec, "tag": tag, "signals": signals,
          "max_hold": max_hold}''')
rep('''    write_event("Learner", "shadow_open", f"shadow {m['symbol']} aperto ({'|'.join(keys)}): {reason[:120]}",
                {"mint": mint, "keys": list(keys), "score": round(score, 4), "rt_cost_pct": round(rt, 2)})''',
    '''    write_event("Learner", "shadow_open", f"shadow {m['symbol']} aperto ({'|'.join(keys)}): {reason[:120]}",
                {"mint": mint, "keys": list(keys), "score": round(score, 4), "rt_cost_pct": round(rt, 2), "tag": tag})''')
rep('''        drop = (1 - sh["liq"] / sh["liq_entry"]) * 100
        return f"liquidita crollata del {drop:.0f}%", 1.0, False, drop >= 80''',
    '''        drop = (1 - sh["liq"] / sh["liq_entry"]) * 100
        why = f"liquidita crollata del {drop:.0f}%"
        return why, 1.0, False, is_rug_event(why, held)''')
rep('''    if held > CFG["MAX_HOLD_MIN"]:
        return f"tempo massimo {CFG['MAX_HOLD_MIN']} min", 1.0, False, False
    return None''', '''    mh = min(CFG["MAX_HOLD_MIN"], sh.get("max_hold") or CFG["MAX_HOLD_MIN"])
    if held > mh:
        return f"tempo massimo {mh:g} min", 1.0, False, False
    return None''')
open(P, "w").write(s)
print("ok part2")
