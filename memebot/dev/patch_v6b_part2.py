P = "/workspace/memebot/desk.py"
s = open(P).read()

def rep(old, new, cnt=1):
    global s
    assert s.count(old) == cnt, (old[:90], s.count(old))
    s = s.replace(old, new)

# ---------------------------------------------------------------- stato
rep('''        self.hard_tripped = False
        # non salvati
''', '''        self.hard_tripped = False
        self.learner_shadow = {}   # v6: statistiche degli esiti per segnale soft + correzioni apprese
        self.shadow_trades = {}    # v6: operazioni ipotetiche aperte (nessun denaro)
        # non salvati
''')
rep('''            "total_start": self.total_start, "hard_tripped": self.hard_tripped,
        }''', '''            "total_start": self.total_start, "hard_tripped": self.hard_tripped,
            "learner_shadow": self.learner_shadow, "shadow_trades": self.shadow_trades,
        }''')
rep('''    st.blacklist = set(d.get("blacklist", []))
    if d.get("mode", "paper") != CFG["MODE"]:''', '''    st.blacklist = set(d.get("blacklist", []))
    ls = d.get("learner_shadow")
    st.learner_shadow = ls if isinstance(ls, dict) else {}
    if d.get("mode", "paper") != CFG["MODE"]:''')
rep('''    st.hard_tripped = bool(d.get("hard_tripped", False))
    return st''', '''    st.hard_tripped = bool(d.get("hard_tripped", False))
    sh = d.get("shadow_trades")
    st.shadow_trades = sh if isinstance(sh, dict) else {}
    for v in st.shadow_trades.values():  # dopo un riavvio il "nessun prezzo da 15 min" riparte da adesso
        v["last_ok"] = time.time()
    return st''')

# ---------------------------------------------------------------- helper segnali soft + learner
rep('''async def security_check(st, http, mint, m, cache):''', r'''# ======================================================================= v6: segnali SOFT graduati
# Per ogni segnale: chiave CFG della soglia grave, verso in cui la soglia "allenta" (+1 = sale), passo del
# Learner e limiti FISSI entro cui il Learner puo muoverla. Gli scarti HARD non sono mai qui.
SOFT_FLAGS = {
    "bundle": {"label": "reti collegate (bundle)", "hard": "BUNDLE_HARD_PCT", "dir": 1, "step": 2.0, "lim": (15.0, 35.0)},
    "equal": {"label": "quote identiche", "hard": "EQUAL_HARD_PCT", "dir": 1, "step": 1.0, "lim": (6.0, 14.0)},
    "top10": {"label": "top 10 concentrati", "hard": "TOP10_HARD_PCT", "dir": 1, "step": 1.0, "lim": (45.0, 55.0)},
    "lp": {"label": "LP bloccata in parte", "hard": "LP_HARD_PCT", "dir": -1, "step": 2.5, "lim": (15.0, 40.0)},
}
LEARN_PEN_LIM = (0.02, 0.10)    # penalita per segnale: il Learner resta tra 0.02 e 0.10
LEARN_SIZE_LIM = (0.3, 0.8)     # moltiplicatore size per segnale: tra 0.3x e 0.8x
SHADOW_TASKS = set()
BROKER_REF = [None]


def _ls(st, key):
    e = st.learner_shadow.setdefault(key, {})
    for k, v in (("hist", []), ("pen_d", 0.0), ("size_d", 0.0), ("hard_d", 0.0), ("last_adj", 0.0), ("adj", 0)):
        e.setdefault(k, v)
    return e


def _learned(base, delta, lim):
    if not delta:
        return base
    return clamp(base + delta, min(lim[0], base), max(lim[1], base))


def soft_pen(st, flag):
    return _learned(CFG["SOFT_PENALTY"], (st.learner_shadow.get(flag) or {}).get("pen_d", 0.0), LEARN_PEN_LIM)


def soft_size(st, flag):
    return _learned(CFG["SOFT_SIZE_MULT"], (st.learner_shadow.get(flag) or {}).get("size_d", 0.0), LEARN_SIZE_LIM)


def soft_hard(st, flag):
    spec = SOFT_FLAGS[flag]
    return _learned(CFG[spec["hard"]], (st.learner_shadow.get(flag + "_grave") or {}).get("hard_d", 0.0), spec["lim"])


def bundle_pct(r):
    """Quota dell'offerta tenuta dalle reti di wallet collegati (RugCheck insiderNetworks). None = non calcolabile."""
    tok = r.get("token") or {}
    supply = fnum(tok.get("supply"))
    nets = [n for n in (r.get("insiderNetworks") or []) if isinstance(n, dict)]
    if supply <= 0 or not nets:
        return None
    pct = sum(fnum(n.get("tokenAmount")) for n in nets) / supply * 100
    return round(pct, 2) if 0 <= pct <= 100 else None


def shadow_stats(hist):
    """n, win rate (rug contati 3 volte), media netta % (rug = -100%), quota rug/-50%."""
    n = len(hist)
    if not n:
        return {"n": 0, "win": 0.0, "avg": 0.0, "rug": 0.0}
    rugs = sum(1 for h in hist if h[1])
    wins = sum(1 for h in hist if h[0] > 0 and not h[1])
    pnls = [-100.0 if h[1] else h[0] for h in hist]
    return {"n": n, "win": round(wins / (n + 2 * rugs), 3), "avg": round(sum(pnls) / n, 2), "rug": round(rugs / n, 3)}


def record_outcome(st, keys, pnl_pct, rug, src, now=None):
    now = now or time.time()
    rug = bool(rug or pnl_pct <= -50)
    for key in keys:
        e = _ls(st, key)
        e["hist"].append([round(float(pnl_pct), 2), 1 if rug else 0, src, int(now)])
        del e["hist"][:-int(CFG["LEARN_SHADOW_WINDOW"])]
        learn_shadow_step(st, key, now)


def learn_shadow_step(st, key, now=None):
    """Al massimo un passo all'ora per segnale, solo con >= LEARN_SHADOW_MIN_N esiti. Ritorna l'azione o None."""
    now = now or time.time()
    e = _ls(st, key)
    sts = shadow_stats(e["hist"])
    if sts["n"] < CFG["LEARN_SHADOW_MIN_N"] or now - e["last_adj"] < CFG["LEARN_STEP_SEC"]:
        return None
    loosen = sts["avg"] >= CFG["LEARN_LOOSEN_AVG"] and sts["win"] >= CFG["LEARN_LOOSEN_WIN"] \
        and sts["rug"] <= CFG["LEARN_LOOSEN_RUG"]
    tighten = sts["avg"] < 0 or sts["rug"] > CFG["LEARN_TIGHTEN_RUG"]
    if not (loosen or tighten):
        return None
    d = 1 if loosen else -1
    if key == "score":  # i quasi-promossi alimentano la soglia: solo verso il basso e solo se chiaramente in utile
        if not loosen or sts["win"] < CFG["LEARN_THR_WIN"]:
            return None
        old = st.threshold
        st.threshold = max(CFG["THRESHOLD_MIN"], st.threshold - CFG["LEARN_THR_STEP"])
        if st.threshold >= old - 1e-12:
            return None
        what = f"soglia {old:.3f} -> {st.threshold:.3f}"
    elif key.endswith("_grave") and key[:-6] in SOFT_FLAGS:
        flag = key[:-6]
        spec = SOFT_FLAGS[flag]
        old = soft_hard(st, flag)
        base = CFG[spec["hard"]]
        new = clamp(old + d * spec["dir"] * spec["step"], min(spec["lim"][0], base), max(spec["lim"][1], base))
        if abs(new - old) < 1e-9:
            return None
        e["hard_d"] = round(new - base, 4)
        what = f"soglia grave {spec['hard']} {old:g} -> {new:g}"
    elif key in SOFT_FLAGS:
        op, osz = soft_pen(st, key), soft_size(st, key)
        bp, bs = CFG["SOFT_PENALTY"], CFG["SOFT_SIZE_MULT"]
        np_ = clamp(op - d * CFG["LEARN_PEN_STEP"], min(LEARN_PEN_LIM[0], bp), max(LEARN_PEN_LIM[1], bp))
        ns = clamp(osz + d * CFG["LEARN_SIZE_STEP"], min(LEARN_SIZE_LIM[0], bs), max(LEARN_SIZE_LIM[1], bs))
        if abs(np_ - op) < 1e-9 and abs(ns - osz) < 1e-9:
            return None
        e["pen_d"], e["size_d"] = round(np_ - bp, 4), round(ns - bs, 4)
        what = f"penalita {op:.3f} -> {np_:.3f}, size x{osz:.2f} -> x{ns:.2f}"
    else:
        return None
    e["last_adj"], e["adj"] = now, e["adj"] + 1
    act = "allenta" if loosen else "stringe"
    msg = (f"{act} '{key}' su {sts['n']} esiti (win {sts['win'] * 100:.0f}%, media {sts['avg']:+.1f}%, "
           f"rug {sts['rug'] * 100:.0f}%): {what}")
    st.lessons.append(f"{datetime.now(TZ):%d/%m %H:%M} {msg}")
    log(st, "Learner", "learn_adjust", msg, flag=key, action=act, n=sts["n"], win=sts["win"], avg=sts["avg"],
        rug=sts["rug"], change=what)
    return act


async def security_check(st, http, mint, m, cache):''')

# ---------------------------------------------------------------- security_check graduato
rep('''        if max(locked) <= 0 or max(locked) < CFG["MIN_LP_LOCKED_PCT"]:
            return False, f"LP bloccata solo al {max(locked):.0f}%", "LP non bloccata", None
''', '''        lpmax = max(locked)
        if lpmax <= 0:  # HARD: LP tutta in mano a chi l'ha creata
            return False, f"LP bloccata solo al {lpmax:.0f}%", "LP non bloccata", None
        if lpmax < CFG["MIN_LP_LOCKED_PCT"]:  # v6: LP in parte bloccata = soft; sotto LP_HARD_PCT = scarto
            lh = soft_hard(st, "lp")
            if lpmax < lh:
                severe.append(("lp", f"LP bloccata solo al {lpmax:.0f}% (< {lh:g}%)", "LP non bloccata"))
            else:
                soft.append(("lp", f"LP bloccata al {lpmax:.0f}%"))
''')
rep('''    markets = r.get("markets") or []
    # pump.fun: curva''', '''    markets = r.get("markets") or []
    soft, severe = [], []  # v6: (segnale, testo) e (segnale, testo, categoria)
    # pump.fun: curva''')
rep('''    pcts = [fnum(h.get("pct")) for h in holders]
    if max(pcts) <= 1.0 and sum(pcts) <= 1.0:
        pcts = [p * 100 for p in pcts]
    top10 = sum(sorted(pcts, reverse=True)[:10])
    if top10 > CFG["MAX_TOP10_PCT"]:
        return False, f"top 10 holder al {top10:.1f}%", "top 10 troppo alti", None

    # la lezione di Pangea: tanti wallet con la stessa identica quota
    same = Counter(round(p, 2) for p in pcts if p >= 0.1)
    if same:
        val, n = same.most_common(1)[0]
        if n >= CFG["MAX_IDENTICAL_WALLETS"]:
            return False, f"{n} wallet con la stessa quota ({val}%): distribuzione coordinata", \\
                "wallet identici", None
    insider_pct = sum(p for h, p in zip(holders, pcts) if h.get("insider"))
    if insider_pct > CFG["MAX_INSIDER_PCT"]:
        return False, f"insider al {insider_pct:.1f}%", "insider", None
    if CFG["REJECT_INSIDER_NETWORKS"] and fnum(r.get("graphInsidersDetected")) > 0:
        return False, "reti di wallet collegati (bundle)", "insider/bundle", None
''', '''    # (v5) pcts = [pct]; se tutte <= 1 venivano moltiplicate x100 -> falsi "top 10 al 99%" sui token molto
    # distribuiti. v6: quota = uiAmount / offerta (RugCheck "pct" e gia in percento, usato come riserva)
    supply_ui = fnum(tok.get("supply")) / 10 ** int(fnum(tok.get("decimals"))) if tok.get("supply") else 0.0
    pcts = [fnum(h.get("uiAmount")) / supply_ui * 100 if supply_ui > 0 and fnum(h.get("uiAmount")) > 0
            else fnum(h.get("pct")) for h in holders]
    top10 = sum(sorted(pcts, reverse=True)[:10])
    t10h = soft_hard(st, "top10")
    if top10 > t10h:
        severe.append(("top10", f"top 10 holder al {top10:.1f}% (> {t10h:g}%)", "top 10 troppo alti"))
    elif top10 > CFG["MAX_TOP10_PCT"]:
        soft.append(("top10", f"top 10 al {top10:.1f}%"))

    # la lezione di Pangea: tanti wallet con la stessa identica quota (v6: grave se insieme tengono molto)
    same = Counter(round(p, 2) for p in pcts if p >= CFG["EQUAL_MIN_PCT"])
    if same:
        val, n = same.most_common(1)[0]
        if n >= CFG["MAX_IDENTICAL_WALLETS"]:
            tot, eh = val * n, soft_hard(st, "equal")
            if tot >= eh:
                severe.append(("equal", f"{n} wallet con la stessa quota ({val}%, insieme {tot:.1f}%): "
                                        f"distribuzione coordinata", "wallet identici"))
            else:
                soft.append(("equal", f"{n} wallet con quota {val}% (insieme {tot:.1f}%)"))
    insider_pct = sum(p for h, p in zip(holders, pcts) if h.get("insider"))
    if insider_pct > CFG["MAX_INSIDER_PCT"]:
        return False, f"insider al {insider_pct:.1f}%", "insider", None
    if CFG["REJECT_INSIDER_NETWORKS"] and fnum(r.get("graphInsidersDetected")) > 0:
        # v6: conta la quota dell'offerta nelle reti collegate (prima bastava una rete qualsiasi)
        bp, bh = bundle_pct(r), soft_hard(st, "bundle")
        if bp is None:
            soft.append(("bundle", "reti di wallet collegati (quota non calcolabile)"))
        elif bp >= bh:
            severe.append(("bundle", f"reti di wallet collegati al {bp:.1f}% dell'offerta (bundle)", "insider/bundle"))
        elif bp >= CFG["BUNDLE_SOFT_PCT"]:
            soft.append(("bundle", f"reti collegate al {bp:.1f}%"))
''')
rep('''    dangers = [x.get("name", "?") for x in (r.get("risks") or []) if str(x.get("level", "")).lower() == "danger"]
    if dangers:
        return False, "rischi gravi: " + ", ".join(dangers[:3]), "rischi gravi RugCheck", None
    sec = {"top10": round(top10, 2), "holders": total_holders or len(holders), "creator": creator,
           "insider_pct": round(insider_pct, 2)}
    return True, f"top 10 al {top10:.1f}%, {sec['holders']} holder, LP ok", None, sec''', '''    # v6: i "danger" di RugCheck su concentrazione top 10 e LP sbloccata sono gia graduati sopra
    dangers = [x.get("name", "?") for x in (r.get("risks") or []) if str(x.get("level", "")).lower() == "danger"
               and not any(k in str(x.get("name", "")).lower() for k in ("top 10 holders", "lp unlocked"))]
    if dangers:
        return False, "rischi gravi: " + ", ".join(dangers[:3]), "rischi gravi RugCheck", None
    sec = {"top10": round(top10, 2), "holders": total_holders or len(holders), "creator": creator,
           "insider_pct": round(insider_pct, 2), "soft": [f for f, _ in soft], "soft_txt": [t for _, t in soft]}
    if severe:  # soglia grave di un segnale soft: scarto (con shadow trade per imparare)
        f0, why, cat = severe[0]
        sec.update(severe=f0, shadow_keys=[f + "_grave" for f, _, _ in severe],
                   soft=[f for f, _, _ in severe] + sec["soft"])
        return False, why, cat, sec
    if len(soft) >= CFG["SOFT_MAX_FLAGS"]:
        sec.update(severe="multi", shadow_keys=["multi"])
        return False, "troppi segnali soft insieme: " + "; ".join(sec["soft_txt"]), "troppi segnali soft", sec
    return True, f"top 10 al {top10:.1f}%, {sec['holders']} holder, LP ok" + \\
        (f", segnali soft: {'; '.join(sec['soft_txt'])}" if soft else ""), None, sec''')

# filter_agent: i soft gravi vanno all'Analyst solo per lo shadow trade
rep('''                ok, reason, cat, sec = await security_check(st, http, mint, m, cache)
                if not ok:
                    reject(st, mint, m["symbol"], reason, cat=cat)
                    continue''', '''                ok, reason, cat, sec = await security_check(st, http, mint, m, cache)
                if not ok:
                    reject(st, mint, m["symbol"], reason, cat=cat)
                    if CFG["SHADOW_TRADES"] and sec and sec.get("severe"):
                        # v6: nessun acquisto possibile; l'Analyst valuta solo se aprire uno shadow trade
                        await q_out.put({"mint": mint, "m": m, "sec": sec, "filter_ok": True,
                                         "shadow_only": reason, "shadow_keys": sec.get("shadow_keys")})
                    continue''')
open(P, "w").write(s)
print("ok")
