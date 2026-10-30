p = "/workspace/memebot/desk.py"
s = open(p).read()
def rep(a, b, n=1):
    global s
    assert s.count(a) == n, (a[:70], s.count(a))
    s = s.replace(a, b)

rep('''    SRC = {"fomo": _fomo, "kol": _kol, "theses": _theses, "mom": _mom}
except Exception as _e:  # noqa: BLE001
    SRC = {"error": f"{type(_e).__name__}: {_e}"[:160]}
''', '''    SRC = {"fomo": _fomo, "kol": _kol, "theses": _theses, "mom": _mom}
except Exception as _e:  # noqa: BLE001
    SRC = {"error": f"{type(_e).__name__}: {_e}"[:160]}
try:
    from sources import gmgn_trending as _gmgn  # noqa: E402
    SRC["gmgn"] = _gmgn
except Exception:  # noqa: BLE001
    pass


def gmgn_personal_key():
    """v7: chiave GMGN PERSONALE (env GMGN_API_KEY, o ~/.config/desk/gmgn_key / gmgn_api_key). La chiave pubblica
    demo condivisa NON va mai usata in produzione: se e quella (o manca) ritorna None. Mai scritta nei log."""
    k = (os.environ.get("GMGN_API_KEY") or "").strip()
    if not k:
        for name in ("gmgn_key", "gmgn_api_key"):
            try:
                k = open(os.path.join(os.path.expanduser("~"), ".config", "desk", name)).read().strip()
            except OSError:
                k = ""
            if k:
                break
    pub = getattr(SRC.get("gmgn"), "PUBLIC_KEY", "gmgn_solbscbaseethmonadtron")
    return k if k and k != pub else None
''')

rep('''    "FOMO_TREND_BONUS": 0.03,         # solo dalla vera board Fomo (serve FOMOAPI_KEY), non dal sostituto
''', '''    "GMGN_TREND_BONUS": 0.03,         # fonte principale: trending GMGN, SOLO con chiave personale (GMGN_API_KEY)
    "FOMO_TREND_BONUS": 0.03,         # solo dalla vera board Fomo (serve FOMOAPI_KEY), non dal sostituto
''')

# Scout: GMGN con chiave personale, altrimenti Fomo/sostituto DexScreener (bonus 0)
old_scout = s[s.index("async def scout_fomo(st):"):s.index("async def scout_pumpportal(st):")]
new_scout = '''async def scout_fomo(st):
    """v7: trending. Fonte principale GMGN (OpenAPI ufficiale) SOLO con chiave personale; senza chiave il sostituto
    gratuito (DexScreener boosts via fomo_trending, o la vera board Fomo se c'e FOMOAPI_KEY), bonus 0 al sostituto.
    La chiave pubblica demo di GMGN non viene mai usata. Nessun bypass dei filtri."""
    if "fomo" not in SRC and "gmgn" not in SRC:
        log(st, "Scout", "warn", f"fonte trending non disponibile: {SRC.get('error', 'modulo assente')}")
        while True:
            await asyncio.sleep(3600)
    last_src = None
    while True:
        try:
            res, key = None, gmgn_personal_key()
            if key and "gmgn" in SRC:
                SRC["gmgn"]._key = (lambda k=key: (k, "personal"))   # mai la chiave demo condivisa
                r = await SRC["gmgn"].aget_trending(25, allow_substitute=False)
                if r.get("source") == "gmgn_trending" and r.get("tokens"):
                    res = r
            if res is None and "fomo" in SRC:
                res = await SRC["fomo"].aget_trending(25)
            res = res or {"source": "none", "tokens": []}
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
            if src != last_src:
                note = {"gmgn_trending": " - GMGN con chiave personale, bonus GMGN_TREND_BONUS",
                        "fomo_trending": " - board Fomo (FOMOAPI_KEY), bonus FOMO_TREND_BONUS",
                        "dex_boosts_substitute": " - SOSTITUTO gratuito (nessuna chiave GMGN personale), nessun bonus"}
                log(st, "Scout", "info", f"fonte trending: {src} ({len(res.get('tokens') or [])} token)"
                    + note.get(src, " - nessun bonus"))
                last_src = src
        except Exception as e:  # noqa: BLE001
            log(st, "Scout", "warn", f"fonte trending: errore ({type(e).__name__})")
        await asyncio.sleep(max(120, CFG["FOMO_SCOUT_SEC"]))


'''
s = s.replace(old_scout, new_scout)

rep('''BONUS_KEYS = {"FOMO_TREND_BONUS": ("src:fomo_trending", 0.01),''',
    '''BONUS_KEYS = {"GMGN_TREND_BONUS": ("src:gmgn_trending", 0.01), "FOMO_TREND_BONUS": ("src:fomo_trending", 0.01),''')
rep('''BONUS_LIMITS = {"FOMO_TREND_BONUS": (0.0, 0.08),''',
    '''BONUS_LIMITS = {"GMGN_TREND_BONUS": (0.0, 0.08), "FOMO_TREND_BONUS": (0.0, 0.08),''')
rep('''        tags += [f"src:{fr['source']}", f"fomo_rank:{fr['rank']}"]
        if fr["source"] == "fomo_trending":''', '''        rk = "gmgn_rank" if fr["source"] == "gmgn_trending" else "fomo_rank"
        tags += [f"src:{fr['source']}", f"{rk}:{fr['rank']}"]
        if fr["source"] == "gmgn_trending":
            parts += cfgb["GMGN_TREND_BONUS"]
        elif fr["source"] == "fomo_trending":''')
rep('''        b, t = SRC["theses"].thesis_bonus(mint, SRC["kol"].tier_of, cfgb)''',
    '''        b, t = SRC["theses"].thesis_bonus(mint, SRC["kol"].tier_of, cfgb, getattr(SRC["kol"], "weight_of", None))''')
rep('''"LP_SOFT_MAX_HOLD_MIN", "LP_EXTREME_LOCKED_PCT", "FOMO_TREND_BONUS",''',
    '''"LP_SOFT_MAX_HOLD_MIN", "LP_EXTREME_LOCKED_PCT", "GMGN_TREND_BONUS", "FOMO_TREND_BONUS",''')
open(p, "w").write(s)
print("ok")
