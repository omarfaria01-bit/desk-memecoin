P = "/workspace/memebot/desk.py"
s = open(P).read()
def rep(old, new, cnt=1):
    global s
    n = s.count(old)
    assert n == cnt, (n, old[:90])
    s = s.replace(old, new)

rep('''def shadow_stats(hist):''', '''def pos_max_hold(p):
    """v7: tenuta massima della posizione (piu corta con LP sbloccata), mai oltre MAX_HOLD_MIN."""
    return min(CFG["MAX_HOLD_MIN"], fnum(p.get("max_hold_min")) or CFG["MAX_HOLD_MIN"])


def is_rug_event(reason, minutes):
    """v7: rug = liquidita -50% (LIQ_DROP_EXIT_PCT) entro RUG_WINDOW_MIN dall'ingresso, o -80% in qualsiasi momento."""
    mt = re.search(r"liquidita crollata del (\\d+)%", str(reason))
    if not mt:
        return False
    drop = float(mt.group(1))
    return drop >= 80 or (drop >= CFG["LIQ_DROP_EXIT_PCT"] and fnum(minutes) <= CFG["RUG_WINDOW_MIN"])


# ----------------------------------------------------------------------- v7: bonus dei segnali extra (sources/)
# chiave CFG -> (prefisso del tag in 'signals', passo del Learner). Limiti = OVR_LIMITS (anche per il Learner).
BONUS_KEYS = {"FOMO_TREND_BONUS": ("src:fomo_trending", 0.01), "FOMO_SUB_BONUS": ("src:dex_boosts_substitute", 0.01),
              "KOL_BONUS": ("kol:", 0.01), "KOL_THESIS_BONUS": ("kol_thesis:", 0.005),
              "MOMENTUM_YOUNG_BONUS": ("momentum_young:", 0.01)}
BONUS_LIMITS = {"FOMO_TREND_BONUS": (0.0, 0.08), "FOMO_SUB_BONUS": (0.0, 0.03), "KOL_BONUS": (0.0, 0.08),
                "KOL_THESIS_BONUS": (0.0, 0.06), "MOMENTUM_YOUNG_BONUS": (0.0, 0.08), "TOTAL_BONUS_CAP": (0.0, 0.10),
                "KOL_SOLD_PENALTY": (0.0, 0.05), "KOL_MIN_SOL": (0.05, 1.0), "MOMENTUM_MIN_SCORE": (0.4, 0.8)}


def bonus_eff(st, key):
    lim = BONUS_LIMITS.get(key, (-1e9, 1e9))
    d = fnum(((st.learner_bonus or {}).get("d") or {}).get(key)) if key in BONUS_KEYS else 0.0
    return round(clamp(fnum(CFG.get(key)) + d, lim[0], lim[1]), 4)


async def compute_bonus(st, mint, m):
    """Ritorna (bonus, tag). Mai negativo oltre -KOL_SOLD_PENALTY, mai oltre TOTAL_BONUS_CAP."""
    tags, parts = [], 0.0
    cfgb = {k: bonus_eff(st, k) for k in BONUS_LIMITS}
    fr = st.fomo_rank.get(mint)
    if fr:
        tags += [f"src:{fr['source']}", f"fomo_rank:{fr['rank']}"]
        if fr["source"] == "fomo_trending":
            parts += cfgb["FOMO_TREND_BONUS"]
        elif fr["source"] == "dex_boosts_substitute":
            parts += cfgb["FOMO_SUB_BONUS"]
    if "mom" in SRC:
        mr = await SRC["mom"].amomentum(mint)
        b, t = SRC["mom"].momentum_bonus(mr or {}, cfgb)
        parts, tags = parts + b, tags + t
    if "theses" in SRC:
        b, t = SRC["theses"].thesis_bonus(mint, SRC["kol"].tier_of, cfgb)
        parts, tags = parts + b, tags + t
    if "kol" in SRC:
        async with RPC_HEAVY_LOCK:  # condivide la RPC pubblica con il controllo cluster: uno alla volta
            kr = await SRC["kol"].akol_check(mint)
        b, t = SRC["kol"].kol_bonus(kr or {}, cfgb)
        parts, tags = parts + b, tags + t
        if (kr or {}).get("error"):
            tags.append("kol_err")
    total = clamp(parts, -cfgb["KOL_SOLD_PENALTY"], cfgb["TOTAL_BONUS_CAP"])
    return round(total, 4), tags


def record_signals(st, mint, signals, pnl_pct, rug, shadow):
    """v7: esito per i bonus (trade veri + shadow) e per il registro dei KOL (kol_ledger.json)."""
    tags = [t for t in str(signals or "").split("|") if t]
    if not tags:
        return
    lb = st.learner_bonus
    lb.setdefault("hist", []).append([round(float(pnl_pct), 2), 1 if rug else 0, tags, int(time.time()), int(shadow)])
    del lb["hist"][:-int(CFG["LEARN_BONUS_WINDOW"])]
    if "kol" in SRC:
        led = None
        for t in tags:
            if t.startswith("kol:"):
                try:
                    led = led or SRC["kol"].KolLedger()
                    led.record(t.split(":")[1], -100.0 if rug else pnl_pct, mint, shadow)
                except Exception:  # noqa: BLE001
                    pass
    learn_bonus_step(st)


def learn_bonus_step(st, now=None):
    """Per ogni bonus: media netta degli esiti CON il segnale contro quelli SENZA (>= LEARN_BONUS_MIN_N ciascuno).
    Meglio di LEARN_BONUS_EDGE punti -> +passo; peggio o neutro -> -passo fino a 0. Un passo all'ora, nei limiti."""
    now = now or time.time()
    lb = st.learner_bonus
    hist, d, last = lb.get("hist", []), lb.setdefault("d", {}), lb.setdefault("last", {})
    acts = []
    for key, (pref, step) in BONUS_KEYS.items():
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
        new = clamp(old + (step if aw >= awo + CFG["LEARN_BONUS_EDGE"] else -step), lim[0], lim[1])
        if abs(new - old) < 1e-9:
            continue
        d[key], last[key] = round(new - fnum(CFG.get(key)), 4), now
        msg = (f"bonus {key} {old:.3f} -> {new:.3f} (con il segnale {aw:+.1f}% su {len(w)}, senza {awo:+.1f}% "
               f"su {len(wo)})")
        st.lessons.append(f"{datetime.now(TZ):%d/%m %H:%M} {msg}")
        log(st, "Learner", "learn_bonus", msg, key=key, old=old, new=new, n_with=len(w), n_without=len(wo))
        acts.append(key)
    return acts


def analyst_shadow(st, http, mint, m, c, why):
    """v7: shadow sugli scarti soft dell'Analyst (dopo i controlli di sicurezza), tag analyst_momentum."""
    if not (CFG["SHADOW_TRADES"] and CFG["SHADOW_ANALYST_REJECTS"]):
        return
    flags = list((c.get("sec") or {}).get("soft") or [])
    spawn_shadow(st, http, mint, m, ["analyst_momentum"], f"Analyst: {why}", 0.0, flags, tag="analyst_momentum",
                 signals=[f"src:{m.get('src') or '?'}", "reason:analyst_momentum"])


def shadow_stats(hist):''')

# shadow csv header at start
rep('''SHADOW_CSV = os.path.join(DATA_DIR, "shadow_trades.csv")
''', '''SHADOW_CSV = os.path.join(DATA_DIR, "shadow_trades.csv")
SHADOW_FIELDS = ["aperto", "chiuso", "symbol", "mint", "motivo_scarto", "chiavi", "segnali_soft", "score", "soglia",
                 "prezzo_entrata", "liq_entrata", "rt_cost_pct", "quote_src", "pnl_pct", "peak_pct", "minuti", "uscita",
                 "rug", "tag", "signals"]


def ensure_shadow_csv():
    """v7: shadow_trades.csv con l'intestazione gia all'avvio (anche prima della prima chiusura)."""
    if os.path.exists(SHADOW_CSV):
        with open(SHADOW_CSV, newline="") as f:
            head = next(csv.reader(f), [])
        if head == SHADOW_FIELDS:
            return
        os.replace(SHADOW_CSV, SHADOW_CSV.replace(".csv", f"_old_{int(time.time())}.csv"))
    with open(SHADOW_CSV, "w", newline="") as f:
        csv.writer(f).writerow(SHADOW_FIELDS)
''')

# Fomo scout
rep('''async def scout_pumpportal(st):''', '''async def scout_fomo(st):
    """v7: board trending di Fomo (con FOMOAPI_KEY) o il sostituto DexScreener boosts. Nessun bypass dei filtri."""
    if "fomo" not in SRC:
        log(st, "Scout", "warn", f"fonte Fomo non disponibile: {SRC.get('error', 'modulo assente')}")
        while True:
            await asyncio.sleep(3600)
    first = True
    while True:
        try:
            res = await SRC["fomo"].aget_trending(25)
            src = res.get("source", "none")
            now = time.time()
            for t in res.get("tokens") or []:
                mint = t.get("mint")
                if not mint:
                    continue
                st.fomo_rank[mint] = {"rank": t.get("rank"), "source": src, "t": now}
                add_watch(st, mint, t.get("symbol") or "?", src)
            for k in [k for k, v in st.fomo_rank.items() if now - v["t"] > 6 * 3600]:
                st.fomo_rank.pop(k, None)
            if first:
                log(st, "Scout", "info", f"fonte trending: {src} ({len(res.get('tokens') or [])} token)"
                    + (" - SOSTITUTO, non Fomo, nessun bonus" if src == "dex_boosts_substitute" else ""))
                first = False
        except Exception as e:  # noqa: BLE001
            log(st, "Scout", "warn", f"fonte trending: errore ({type(e).__name__})")
        await asyncio.sleep(max(120, CFG["FOMO_SCOUT_SEC"]))


async def scout_pumpportal(st):''')

rep('''            "Scout pool": lambda: scout_gecko(st, http),''', '''            "Scout pool": lambda: scout_gecko(st, http),
            "Scout trending": lambda: scout_fomo(st),''')
rep('''        q_an, q_ex = asyncio.Queue(), asyncio.Queue()
        jobs = {''', '''        q_an, q_ex = asyncio.Queue(), asyncio.Queue()
        ensure_shadow_csv()
        jobs = {''')

# OVR
rep('''                  "SOFT_MAX_FLAGS": "SOFT_MAX_FLAGS", "SHADOW_NEAR_SCORE": "SHADOW_NEAR_SCORE"}''',
    '''                  "SOFT_MAX_FLAGS": "SOFT_MAX_FLAGS", "SHADOW_NEAR_SCORE": "SHADOW_NEAR_SCORE",
                  # v7: cluster, LP soft, bonus dei segnali (tutti con limiti fissi)
                  **{k: k for k in ("BUNDLE_MAX_NETWORKS", "BUNDLE_NET_MIN_WALLETS", "BUNDLE_TOTAL_HARD_PCT",
                                    "BUNDLE_MAX_INSIDER_WALLETS", "CLUSTER_FRESH_HARD", "CLUSTER_SOFT_NETWORKS",
                                    "CLUSTER_SOFT_PCT", "CLUSTER_FRESH_SOFT", "CLUSTER_PENALTY", "CLUSTER_SIZE_MULT",
                                    "LP_SOFT_MAX_HOLD_MIN", "LP_EXTREME_LOCKED_PCT", "FOMO_TREND_BONUS",
                                    "FOMO_SUB_BONUS", "KOL_BONUS", "KOL_THESIS_BONUS", "MOMENTUM_YOUNG_BONUS",
                                    "TOTAL_BONUS_CAP", "KOL_SOLD_PENALTY", "KOL_MIN_SOL", "MOMENTUM_MIN_SCORE")}}''')
rep('''              "SOFT_SIZE_MULT": (0.2, 1.0), "SOFT_MAX_FLAGS": (2.0, 4.0), "SHADOW_NEAR_SCORE": (0.0, 0.10)}
OVR_INT_KEYS = {"MAX_HOLD_MIN", "TIME_STOP_MIN", "SOFT_MAX_FLAGS"}''',
    '''              "SOFT_SIZE_MULT": (0.2, 1.0), "SOFT_MAX_FLAGS": (2.0, 4.0), "SHADOW_NEAR_SCORE": (0.0, 0.10),
              # v7
              "BUNDLE_MAX_NETWORKS": (3.0, 8.0), "BUNDLE_NET_MIN_WALLETS": (2.0, 6.0),
              "BUNDLE_TOTAL_HARD_PCT": (10.0, 40.0), "BUNDLE_MAX_INSIDER_WALLETS": (20.0, 60.0),
              "CLUSTER_FRESH_HARD": (0.7, 1.0), "CLUSTER_SOFT_NETWORKS": (1.0, 4.0), "CLUSTER_SOFT_PCT": (2.0, 15.0),
              "CLUSTER_FRESH_SOFT": (0.3, 0.8), "CLUSTER_PENALTY": (0.02, 0.15), "CLUSTER_SIZE_MULT": (0.2, 1.0),
              "LP_SOFT_MAX_HOLD_MIN": (15.0, 180.0), "LP_EXTREME_LOCKED_PCT": (0.0, 10.0),
              **BONUS_LIMITS}
OVR_INT_KEYS = {"MAX_HOLD_MIN", "TIME_STOP_MIN", "SOFT_MAX_FLAGS", "BUNDLE_MAX_NETWORKS", "BUNDLE_NET_MIN_WALLETS",
                "BUNDLE_MAX_INSIDER_WALLETS", "CLUSTER_SOFT_NETWORKS", "LP_SOFT_MAX_HOLD_MIN"}''')
open(P, "w").write(s)
print("ok part3")
