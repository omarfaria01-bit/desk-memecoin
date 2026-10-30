import re
p = "/workspace/memebot/desk.py"
s = open(p).read()
n0 = len(s)

def rep(old, new, count=1):
    global s
    assert s.count(old) == count, (old[:80], s.count(old))
    s = s.replace(old, new)

# 1) CFG
rep('''    "MOMENTUM_MIN_SCORE": 0.5,
    "FOMO_SCOUT_SEC": 180,''', '''    "MOMENTUM_MIN_SCORE": 0.5,
    # v8: controllo veloce della qualita del progetto (sources/project_check.py): sito, social, GitHub, metadati
    "PROJECT_CHECK": True,
    "PROJECT_TIMEOUT_SEC": 5.0,       # oltre: nessun effetto (mai uno scarto per questo controllo)
    "PROJECT_BONUS": 0.02,            # bonus = PROJECT_BONUS x project_score se score >= PROJECT_MIN_SCORE
    "PROJECT_MIN_SCORE": 0.5,
    "PROJECT_EMPTY_PENALTY": 0.01,    # penalita lieve: niente sito e niente social
    # v8: analisi approfondita (Mark): coda sources/deep_review_queue.jsonl, verdetti sources/deep_reviews.json
    "DEEP_QUEUE_PEAK_PCT": 50.0,      # in coda anche trade/shadow arrivati a +50%
    "DEEP_HOLD_MIN_SCORE": 70.0,      # voto >= 70: tenuta massima (e stop a tempo) piu lunga...
    "DEEP_HOLD_EXTRA_MIN": 60,        # ...di 60 minuti
    "DEEP_LOW_SCORE": 30.0,           # voto <= 30: trailing piu stretto...
    "DEEP_TRAIL_TIGHTEN_PCT": 3.0,    # ...di 3 punti (mai sotto 3%)
    "FOMO_SCOUT_SEC": 180,''')

# 2) import
rep('''try:
    from sources import gmgn_trending as _gmgn  # noqa: E402
    SRC["gmgn"] = _gmgn
except Exception:  # noqa: BLE001
    pass
''', '''try:
    from sources import gmgn_trending as _gmgn  # noqa: E402
    SRC["gmgn"] = _gmgn
except Exception:  # noqa: BLE001
    pass
try:
    from sources import project_check as _proj  # noqa: E402  (v8)
    SRC["proj"] = _proj
except Exception:  # noqa: BLE001
    pass
''')

# 3) summarize: link completi (per project_check e coda deep review)
rep('''        "website": bool(info.get("websites")),
        "age_min": (time.time() - created) / 60 if created else None,
    }''', '''        "website": bool(info.get("websites")),
        "age_min": (time.time() - created) / 60 if created else None,
        # v8: link completi (siti e social) per il controllo progetto e la coda delle analisi approfondite
        "links": {"websites": [str(w.get("url")) for w in (info.get("websites") or [])
                               if isinstance(w, dict) and w.get("url")][:4],
                  "socials": [{"type": str(x.get("type", "")).lower(), "url": str(x.get("url", ""))}
                              for x in (info.get("socials") or []) if isinstance(x, dict)][:6]},
    }''')

# 4) sec: metadati on-chain per il controllo progetto
rep('''           "rc_networks": rc_networks(r)}
    if severe:''', '''           "rc_networks": rc_networks(r),
           # v8: metadati on-chain (RugCheck tokenMeta: name, symbol, uri, mutable) per il controllo progetto
           "token_meta": {k: (r.get("tokenMeta") or {}).get(k) for k in ("name", "symbol", "uri", "mutable",
                                                                           "updateAuthority")}
           if isinstance(r.get("tokenMeta"), dict) else None,
           "is_pump": bool(m["dex"] == "pumpfun" or pump_migrated)}
    if severe:''')

# 5) bonus keys / limits
rep('''              "MOMENTUM_YOUNG_BONUS": ("momentum_young:", 0.01)}
BONUS_LIMITS = {''', '''              "MOMENTUM_YOUNG_BONUS": ("momentum_young:", 0.01),
              "PROJECT_BONUS": ("proj_good:", 0.01)}  # v8
# v8: penalita apprese al contrario: se gli esiti CON il tag sono peggiori -> +passo, altrimenti -passo fino a 0
PENALTY_KEYS = {"PROJECT_EMPTY_PENALTY": ("proj_empty", 0.005)}
BONUS_LIMITS = {"PROJECT_BONUS": (0.0, 0.06), "PROJECT_EMPTY_PENALTY": (0.0, 0.03), "PROJECT_MIN_SCORE": (0.3, 0.8),
                ''')
rep('''    d = fnum(((st.learner_bonus or {}).get("d") or {}).get(key)) if key in BONUS_KEYS else 0.0''',
    '''    d = fnum(((st.learner_bonus or {}).get("d") or {}).get(key)) if key in BONUS_KEYS or key in PENALTY_KEYS else 0.0''')

# 6) compute_bonus con project check in parallelo
rep('''async def compute_bonus(st, mint, m):
    """Ritorna (bonus, tag). Mai negativo oltre -KOL_SOLD_PENALTY, mai oltre TOTAL_BONUS_CAP."""
    tags, parts = [], 0.0
    cfgb = {k: bonus_eff(st, k) for k in BONUS_LIMITS}''', '''PROJ_LAST = {}   # v8: mint -> ultimo risultato di project_check (link per la coda deep review)


async def project_eval(mint, m, sec):
    """v8: controllo progetto con scadenza PROJECT_TIMEOUT_SEC. Timeout/errore -> None (nessun effetto)."""
    if not CFG["PROJECT_CHECK"] or "proj" not in SRC:
        return None
    sec = sec or {}
    try:
        r = await asyncio.wait_for(SRC["proj"].acheck(
            mint, symbol=m.get("symbol"), name=m.get("name"), links=m.get("links") or None,
            token_meta=sec.get("token_meta"), is_pump=sec.get("is_pump"),
            deadline=max(1.0, CFG["PROJECT_TIMEOUT_SEC"] - 0.5)), timeout=CFG["PROJECT_TIMEOUT_SEC"])
    except Exception:  # noqa: BLE001  (timeout compreso)
        return {"score": None, "tags": ["proj_timeout"]}
    if r and r.get("score") is not None:
        PROJ_LAST[mint] = r
        for k in [k for k in PROJ_LAST if time.time() - fnum(PROJ_LAST[k].get("ts")) > 7200][:50]:
            PROJ_LAST.pop(k, None)
    return r


async def compute_bonus(st, mint, m, sec=None):
    """Ritorna (bonus, tag). Mai negativo oltre -(KOL_SOLD_PENALTY + PROJECT_EMPTY_PENALTY), mai oltre TOTAL_BONUS_CAP."""
    tags, parts = [], 0.0
    cfgb = {k: bonus_eff(st, k) for k in BONUS_LIMITS}
    proj_task = asyncio.ensure_future(project_eval(mint, m, sec)) if CFG["PROJECT_CHECK"] and "proj" in SRC else None''')
rep('''        if (kr or {}).get("error"):
            tags.append("kol_err")
    total = clamp(parts, -cfgb["KOL_SOLD_PENALTY"], cfgb["TOTAL_BONUS_CAP"])''', '''        if (kr or {}).get("error"):
            tags.append("kol_err")
    if proj_task is not None:  # v8: qualita del progetto (gia partito in parallelo; timeout = nessun effetto)
        try:
            pr = await proj_task
            b, t = SRC["proj"].project_bonus(pr or {}, cfgb)
            parts, tags = parts + b, tags + [x for x in t if x not in tags]
        except Exception:  # noqa: BLE001
            tags.append("proj_err")
    total = clamp(parts, -(cfgb["KOL_SOLD_PENALTY"] + cfgb["PROJECT_EMPTY_PENALTY"]), cfgb["TOTAL_BONUS_CAP"])''')
rep('''                    bonus, btags = await compute_bonus(st, mint, m)''',
    '''                    bonus, btags = await compute_bonus(st, mint, m, c.get("sec"))''')

# 7) learner penalita
rep('''        acts.append(key)
    return acts''', '''        acts.append(key)
    for key, (pref, step) in PENALTY_KEYS.items():  # v8: penalita (verso opposto)
        if now - fnum(last.get(key)) < CFG["LEARN_STEP_SEC"]:
            continue
        w = [(-100.0 if h[1] else h[0]) for h in hist if any(t.startswith(pref) for t in h[2])]
        wo = [(-100.0 if h[1] else h[0]) for h in hist if not any(t.startswith(pref) for t in h[2])]
        n = int(CFG["LEARN_BONUS_MIN_N"])
        if len(w) < n or len(wo) < n:
            continue
        aw, awo = sum(w) / len(w), sum(wo) / len(wo)
        old = bonus_eff(st, key)
        lim = BONUS_LIMITS[key]
        new = clamp(old + (step if aw <= awo - CFG["LEARN_BONUS_EDGE"] else -step), lim[0], lim[1])
        if abs(new - old) < 1e-9:
            continue
        d[key], last[key] = round(new - fnum(CFG.get(key)), 4), now
        msg = (f"penalita {key} {old:.3f} -> {new:.3f} (con il segnale {aw:+.1f}% su {len(w)}, senza {awo:+.1f}% "
               f"su {len(wo)})")
        st.lessons.append(f"{datetime.now(TZ):%d/%m %H:%M} {msg}")
        log(st, "Learner", "learn_bonus", msg, key=key, old=old, new=new, n_with=len(w), n_without=len(wo))
        acts.append(key)
    return acts''')

# 8) pos_max_hold + deep review
rep('''def pos_max_hold(p):
    """v7: tenuta massima della posizione (piu corta con LP sbloccata), mai oltre MAX_HOLD_MIN."""
    return min(CFG["MAX_HOLD_MIN"], fnum(p.get("max_hold_min")) or CFG["MAX_HOLD_MIN"])
''', '''def pos_max_hold(p, mint=None):
    """v7: tenuta massima della posizione (piu corta con LP sbloccata), mai oltre MAX_HOLD_MIN.
    v8: con voto deep review >= DEEP_HOLD_MIN_SCORE +DEEP_HOLD_EXTRA_MIN (non per LP sbloccata), max 720 min."""
    base = min(CFG["MAX_HOLD_MIN"], fnum(p.get("max_hold_min")) or CFG["MAX_HOLD_MIN"])
    if mint and not p.get("max_hold_min") and deep_effect(mint)["hold_extra"] > 0:
        return min(720, base + deep_effect(mint)["hold_extra"])
    return base


# ----------------------------------------------------------------------- v8: analisi approfondite (Mark)
DEEP_QUEUE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources", "deep_review_queue.jsonl")
DEEP_REVIEWS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources", "deep_reviews.json")
DEEP = {"key": None, "reviews": {}, "queued": None}


def deep_reviews():
    """Rilegge deep_reviews.json solo quando cambia. File assente/non valido: ultimi valori validi."""
    try:
        stt = os.stat(DEEP_REVIEWS_PATH)
        key = (stt.st_mtime_ns, stt.st_size)
    except OSError:
        return DEEP["reviews"]
    if key != DEEP["key"]:
        DEEP["key"] = key
        try:
            d = json.load(open(DEEP_REVIEWS_PATH))
            if isinstance(d, dict):
                DEEP["reviews"] = {k: v for k, v in d.items() if isinstance(v, dict)}
        except Exception:  # noqa: BLE001
            pass
    return DEEP["reviews"]


def deep_effect(mint):
    """{score, hold_extra (min), trail_tighten (punti)} dal verdetto di Mark (voto 0-100)."""
    rv = deep_reviews().get(mint) or {}
    sc = rv.get("score")
    out = {"score": None, "hold_extra": 0, "trail_tighten": 0.0}
    if isinstance(sc, (int, float)) and not isinstance(sc, bool) and math.isfinite(sc):
        sc = clamp(float(sc), 0.0, 100.0)
        out["score"] = sc
        if sc >= CFG["DEEP_HOLD_MIN_SCORE"]:
            out["hold_extra"] = int(CFG["DEEP_HOLD_EXTRA_MIN"])
        elif sc <= CFG["DEEP_LOW_SCORE"]:
            out["trail_tighten"] = float(CFG["DEEP_TRAIL_TIGHTEN_PCT"])
    return out


def deep_queue_add(mint, symbol, links, reason, url="", score=None, chain="solana"):
    """Accoda un token per l'analisi approfondita (una volta per mint e motivo). Mai eccezioni."""
    try:
        if DEEP["queued"] is None:
            DEEP["queued"] = set()
            if os.path.exists(DEEP_QUEUE_PATH):
                for line in open(DEEP_QUEUE_PATH):
                    try:
                        e = json.loads(line)
                        DEEP["queued"].add((e.get("mint"), str(e.get("reason", "")).split(":")[0]))
                    except ValueError:
                        pass
        k = (mint, reason.split(":")[0])
        if k in DEEP["queued"]:
            return False
        DEEP["queued"].add(k)
        pr = PROJ_LAST.get(mint) or {}
        lk = dict(links or {})
        if pr.get("links"):
            lk.setdefault("project", pr["links"])
        e = {"mint": mint, "symbol": symbol, "chain": chain, "links": lk, "dexscreener": url, "reason": reason,
             "project_score": pr.get("score"), "project_tags": pr.get("tags"), "bot_score": score,
             "ts": datetime.now(TZ).isoformat(timespec="seconds")}
        with open(DEEP_QUEUE_PATH, "a") as f:
            f.write(json.dumps(e, ensure_ascii=False) + "\\n")
        return True
    except Exception:  # noqa: BLE001
        return False


def deep_queue_peak(mint, sym, peak, links, url, kind):
    if peak >= CFG["DEEP_QUEUE_PEAK_PCT"]:
        deep_queue_add(mint, sym, links, f"{kind}_peak:+{peak:.0f}%", url)
''')

# 9) OVR map + limits
rep('''                                    "TOTAL_BONUS_CAP", "KOL_SOLD_PENALTY", "KOL_MIN_SOL", "MOMENTUM_MIN_SCORE")}}''',
    '''                                    "TOTAL_BONUS_CAP", "KOL_SOLD_PENALTY", "KOL_MIN_SOL", "MOMENTUM_MIN_SCORE",
                                    # v8: progetto e analisi approfondite
                                    "PROJECT_BONUS", "PROJECT_EMPTY_PENALTY", "PROJECT_MIN_SCORE",
                                    "DEEP_QUEUE_PEAK_PCT", "DEEP_HOLD_MIN_SCORE", "DEEP_HOLD_EXTRA_MIN",
                                    "DEEP_LOW_SCORE", "DEEP_TRAIL_TIGHTEN_PCT")}}''')
rep('''              "LP_SOFT_MAX_HOLD_MIN": (15.0, 180.0), "LP_EXTREME_LOCKED_PCT": (0.0, 10.0),
              **BONUS_LIMITS}''', '''              "LP_SOFT_MAX_HOLD_MIN": (15.0, 180.0), "LP_EXTREME_LOCKED_PCT": (0.0, 10.0),
              # v8
              "DEEP_QUEUE_PEAK_PCT": (20.0, 200.0), "DEEP_HOLD_MIN_SCORE": (50.0, 95.0),
              "DEEP_HOLD_EXTRA_MIN": (0.0, 180.0), "DEEP_LOW_SCORE": (0.0, 50.0), "DEEP_TRAIL_TIGHTEN_PCT": (0.0, 8.0),
              **BONUS_LIMITS}''')
rep('''"BUNDLE_MAX_INSIDER_WALLETS", "CLUSTER_SOFT_NETWORKS", "LP_SOFT_MAX_HOLD_MIN"}''',
    '''"BUNDLE_MAX_INSIDER_WALLETS", "CLUSTER_SOFT_NETWORKS", "LP_SOFT_MAX_HOLD_MIN", "DEEP_HOLD_EXTRA_MIN"}''')

# 10) _execute_buy: link e coda
rep('''        "max_hold_min": CFG["LP_SOFT_MAX_HOLD_MIN"] if "lp_unlocked" in (c.get("soft") or []) else None,
    }
    st.stats["buys"] += 1''', '''        "max_hold_min": CFG["LP_SOFT_MAX_HOLD_MIN"] if "lp_unlocked" in (c.get("soft") or []) else None,
        "links": m.get("links") or {},  # v8
    }
    st.stats["buys"] += 1
    deep_queue_add(mint, m["symbol"], m.get("links"), "opened", m.get("url", ""), c.get("score"))  # v8''')

# 11) Risk: trailing stretto, stop a tempo e tenuta piu lunghi
rep('''                        tdist_eff = min(tdist, CFG["TRAIL_TIGHT_PCT"]) if p["peak_pct"] >= CFG["TRAIL_TIGHT_AFTER_PCT"] \\
                            else tdist
                        # dopo il TP1''', '''                        tdist_eff = min(tdist, CFG["TRAIL_TIGHT_PCT"]) if p["peak_pct"] >= CFG["TRAIL_TIGHT_AFTER_PCT"] \\
                            else tdist
                        dx = deep_effect(mint)  # v8: verdetto di Mark (voto <= 30: trailing piu stretto)
                        if dx["trail_tighten"] > 0:
                            tdist_eff = max(3.0, tdist_eff - dx["trail_tighten"])
                        time_stop_eff = CFG["TIME_STOP_MIN"] + (dx["hold_extra"] if not p.get("max_hold_min") else 0)
                        deep_queue_peak(mint, p["symbol"], p["peak_pct"], p.get("links"), p.get("url", ""), "real")
                        # dopo il TP1''')
rep('''                                    "max_hold": pos_max_hold(p), "time_stop": CFG["TIME_STOP_MIN"]}''',
    '''                                    "max_hold": pos_max_hold(p, mint), "time_stop": time_stop_eff,
                                    "deep_score": dx["score"]}''')
rep('''                        elif stage == 0 and held > CFG["TIME_STOP_MIN"] and pnl < CFG["TIME_STOP_MIN_PCT"]:  # (v4 <=)
                            await sell_position(st, broker, mint, f"stop a tempo: {held:.0f} min e solo {pnl:+.1f}%")
                        elif held > pos_max_hold(p):
                            await sell_position(st, broker, mint, f"tempo massimo {pos_max_hold(p):g} min" +
                                                (" (LP sbloccata)" if pos_max_hold(p) < CFG["MAX_HOLD_MIN"] else ""))''',
    '''                        elif stage == 0 and held > time_stop_eff and pnl < CFG["TIME_STOP_MIN_PCT"]:  # (v4 <=)
                            await sell_position(st, broker, mint, f"stop a tempo: {held:.0f} min e solo {pnl:+.1f}%")
                        elif held > pos_max_hold(p, mint):
                            mh = pos_max_hold(p, mint)
                            await sell_position(st, broker, mint, f"tempo massimo {mh:g} min" +
                                                (" (LP sbloccata)" if mh < CFG["MAX_HOLD_MIN"] else "") +
                                                (f" (deep review {dx['score']:.0f})" if mh > CFG["MAX_HOLD_MIN"] else ""))''')

# 12) shadow: link e coda a +50%
rep('''          "stop_hits": 0, "rt": round(rt, 2), "src": src, "dec": dec, "tag": tag, "signals": signals,
          "max_hold": max_hold}''', '''          "stop_hits": 0, "rt": round(rt, 2), "src": src, "dec": dec, "tag": tag, "signals": signals,
          "max_hold": max_hold, "links": m.get("links") or {}, "url": m.get("url", "")}''')
rep('''                await shadow_apply(st, http, mint, sh, shadow_decide(sh, now), now)''',
    '''                dec = shadow_decide(sh, now)
                deep_queue_peak(mint, sh["sym"], sh.get("peak_pct", 0.0), sh.get("links"), sh.get("url", ""), "shadow")
                await shadow_apply(st, http, mint, sh, dec, now)''')
open(p, "w").write(s)
print("ok", n0, len(s))
