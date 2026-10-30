#!/usr/bin/env python3
"""Desk Memecoin - dashboard live in sola lettura.

Legge i file del bot (state.json, events.jsonl, equity.csv, trades.csv, run.log,
narratives.json, overrides.json) e li spinge al browser via Server-Sent Events.
NON scrive nessun file, NON controlla il bot, NON tocca STOP. Serve solo:
  GET /              -> index.html
  GET /api/snapshot  -> JSON
  GET /api/stream    -> SSE (evento "snap" quando i dati cambiano, "ping" ogni ~5 s)
"""
import ast, asyncio, csv, hashlib, io, json, os, re, time
from datetime import datetime
from zoneinfo import ZoneInfo
from aiohttp import web

HERE = os.path.dirname(os.path.abspath(__file__))
BOT = os.path.dirname(HERE)
TZ = ZoneInfo("Europe/Rome")
PORT = int(os.environ.get("DESK_WEB_PORT", "8077"))
HOST = os.environ.get("DESK_WEB_HOST", "127.0.0.1")
TICK = 1.5
P = lambda n: os.path.join(BOT, n)

# --------------------------------------------------------------- filtro nomi offensivi
REMOVED = "[nome rimosso]"
_LEET = str.maketrans({"0": "o", "1": "i", "!": "i", "3": "e", "4": "a", "@": "a", "5": "s", "$": "s", "7": "t", "8": "b"})
# sottostringhe (dopo normalizzazione e rimozione di tutto cio che non e lettera)
_SUBSTR = ["nigg", "nigga", "negro", "faggot", "fagot", "kike", "chink", "tranny", "trannie", "retard",
           "wetback", "beaner", "raghead", "towelhead", "sandnigg", "coonass", "spearchucker", "porchmonkey",
           "frocio", "ricchion", "culattone", "finocchio", "negr", "zingar", "terrone", "mongoloid", "spastic",
           "kaffir", "kafir", "paki", "golliwog", "jigaboo", "pickaninny"]
_SUB_RE = re.compile("|".join(sorted(map(re.escape, _SUBSTR), key=len, reverse=True)))
# parole intere (troppo corte per la sottostringa)
_WORD_RE = re.compile(r"(?<![a-z])(fag|fags|spic|spics|coon|coons|gook|gooks|dyke|dykes|nig|nigs|jap|japs|"
                      r"chinc|kyke|wop|wops|tard|tards|negri|negre)(?![a-z])")


def offensive(s):
    if not s:
        return False
    low = str(s).lower().translate(_LEET)
    letters = re.sub(r"[^a-z]", "", low)
    if _SUB_RE.search(letters):
        return True
    return bool(_WORD_RE.search(re.sub(r"[^a-z]+", " ", low)))


def clean_sym(s):
    s = "" if s is None else str(s)[:40]
    return REMOVED if offensive(s) else s


def clean_text(s, syms=()):
    """Toglie dal testo i simboli offensivi noti e ogni parola offensiva."""
    if s is None:
        return ""
    s = str(s)
    for sym in syms:
        if sym and offensive(sym):
            s = s.replace(sym, REMOVED)
    return re.sub(r"\S+", lambda m: REMOVED if offensive(m.group(0)) else m.group(0), s)


def clean_obj(o):
    if isinstance(o, str):
        return clean_text(o)
    if isinstance(o, list):
        return [clean_obj(x) for x in o]
    if isinstance(o, dict):
        return {clean_text(str(k)): clean_obj(v) for k, v in o.items()}
    return o


# --------------------------------------------------------------- lettura difensiva
def fnum(x, d=0.0):
    try:
        v = float(x)
        return v if v == v and abs(v) != float("inf") else d
    except Exception:
        return d


_cache = {}


def cached(path, parser):
    """Rilegge il file solo se mtime/size cambiano."""
    try:
        stt = os.stat(path)
    except OSError:
        return None
    key = (stt.st_mtime_ns, stt.st_size)
    c = _cache.get(path)
    if c and c[0] == key:
        return c[1]
    try:
        val = parser(path)
    except Exception:
        val = c[1] if c else None  # file a meta scrittura: tengo l'ultimo valore buono
    _cache[path] = (key, val)
    return val


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def tail_lines(path, max_bytes=600_000):
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        data = f.read().decode("utf-8", "replace")
    lines = data.splitlines()
    if size > max_bytes and lines:
        lines = lines[1:]
    return lines


def parse_events(path):
    out = []
    for line in tail_lines(path):
        try:
            e = json.loads(line)
            if isinstance(e, dict):
                out.append(e)
        except Exception:
            pass
    return out[-4000:]


def parse_equity(path):
    pts = []
    for line in tail_lines(path, 400_000):
        try:
            ts, v = line.strip().rsplit(",", 1)
            pts.append((ts, float(v)))
        except Exception:
            pass
    return pts


def parse_trades(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))[-300:]


# parametri del bot: letti dal sorgente di desk.py (solo lettura, solo valori numerici noti)
CFG_KEYS = ["START_BALANCE", "MAX_POSITIONS", "MAX_EXPOSURE_PCT", "STOP_LOSS_PCT", "CRASH_EXIT_PCT", "TP1_PCT",
            "TP1_FRACTION", "BREAKEVEN_PCT", "TAKE_PROFIT_PCT", "TAKE_PROFIT_FRACTION", "TRAIL_ACTIVATE_PCT",
            "TRAIL_DISTANCE_PCT", "TRAIL_TIGHT_AFTER_PCT", "TRAIL_TIGHT_PCT", "TIME_STOP_MIN", "TIME_STOP_MIN_PCT",
            "MAX_HOLD_MIN", "FEE_PCT", "SLIPPAGE_MULT", "PRIORITY_FEE_SOL", "SOL_USD_FALLBACK", "SCORE_THRESHOLD",
            "DAILY_LOSS_LIMIT_PCT", "LIQ_DROP_EXIT_PCT", "MIN_LIQUIDITY_USD", "MIN_VOLUME_H1", "MAX_TOP10_PCT",
            "MIN_BUYSELL_M5", "MODE"]
CFG_DEFAULT = {"START_BALANCE": 1000.0, "MAX_POSITIONS": 8, "STOP_LOSS_PCT": -10.0, "TP1_PCT": 30.0,
               "TP1_FRACTION": 0.3, "BREAKEVEN_PCT": 3.0, "TAKE_PROFIT_PCT": 80.0, "TAKE_PROFIT_FRACTION": 0.3,
               "TRAIL_ACTIVATE_PCT": 40.0, "TRAIL_DISTANCE_PCT": 15.0, "MAX_HOLD_MIN": 180, "FEE_PCT": 1.0,
               "SLIPPAGE_MULT": 2.0, "PRIORITY_FEE_SOL": 0.001, "SOL_USD_FALLBACK": 150.0, "MODE": "paper"}


def parse_cfg(path):
    cfg = dict(CFG_DEFAULT)
    tree = ast.parse(open(path, encoding="utf-8").read())
    for n in tree.body:
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", None) == "CFG" and isinstance(n.value, ast.Dict):
            for k, v in zip(n.value.keys, n.value.values):
                try:
                    kk, vv = ast.literal_eval(k), ast.literal_eval(v)
                    if kk in CFG_KEYS:
                        cfg[kk] = vv
                except Exception:
                    pass
    return cfg


def get_cfg():
    cfg = dict(cached(P("desk.py"), parse_cfg) or CFG_DEFAULT)
    over = cached(P("config.json"), read_json)  # letto solo internamente, mai servito
    if isinstance(over, dict):
        for k in CFG_KEYS:
            if k in over:
                cfg[k] = over[k]
    return cfg


# --------------------------------------------------------------- processo del bot
def bot_process():
    best = None
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
            if not any(c.endswith(b"desk.py") for c in cmd):
                continue
            if os.path.realpath(f"/proc/{pid}/cwd") != os.path.realpath(BOT):
                continue
            stat = open(f"/proc/{pid}/stat").read()
            start_ticks = int(stat.rsplit(")", 1)[1].split()[19])
            btime = next(int(l.split()[1]) for l in open("/proc/stat") if l.startswith("btime"))
            started = btime + start_ticks / os.sysconf("SC_CLK_TCK")
            if best is None or started < best[1]:
                best = (int(pid), started)
        except Exception:
            continue
    return best


# --------------------------------------------------------------- snapshot
SYM_RE = re.compile(r"^(?:COMPRA |VENDE \d+% di |CHIUDE )?(.+?) (scartato|superato|approvato|narrativa|bloccato)\b")
SCORE_RE = re.compile(r"punteggio (-?[\d.]+) \(soglia ([\d.]+)\)")


def sym_of(e):
    m = SYM_RE.match(str(e.get("msg", "")))
    if m:
        return m.group(1)
    m = re.match(r"^(?:COMPRA|CHIUDE) (\S+)", str(e.get("msg", "")))
    if m:
        return m.group(1)
    m = re.match(r"^VENDE \d+% di (\S+)", str(e.get("msg", "")))
    return m.group(1) if m else None


def hhmmss(ts):
    s = str(ts or "")
    return s[11:19] if len(s) >= 19 else s


def score_parts(msg):
    out = {}
    m = SCORE_RE.search(msg)
    if m:
        out["score"], out["threshold"] = fnum(m.group(1)), fnum(m.group(2))
    for key, rx in (("narr", r"narrativa ([+-][\d.]+)"), ("no_chart", r"grafico assente -([\d.]+)"),
                    ("bs5", r"acq/vend 5m ([\d.]+)"), ("vol5", r"volume 5m ([\d.]+)x"), ("rsi", r"RSI (\d+)"),
                    ("rt", r"costo a/r stimato ([\d.]+)%")):
        mm = re.search(rx, msg)
        if mm:
            out[key] = fnum(mm.group(1))
    return out


def exit_value(tokens, price, liq, cfg):
    gross = fnum(tokens) * fnum(price)
    if gross <= 0:
        return 0.0
    liq = max(fnum(liq, 1.0), 1.0)
    impact = gross / (liq / 2 + gross)
    slip = min(max(fnum(cfg.get("SLIPPAGE_MULT"), 2.0) * gross / liq, 0.0), 0.5)
    v = gross * (1 - impact) * (1 - slip) * (1 - fnum(cfg.get("FEE_PCT"), 1.0) / 100) \
        - fnum(cfg.get("PRIORITY_FEE_SOL"), 0.001) * fnum(cfg.get("SOL_USD_FALLBACK"), 150.0)
    return max(0.0, v)


def build():
    now = time.time()
    cfg = get_cfg()
    st = cached(P("state.json"), read_json)
    st = st if isinstance(st, dict) else {}
    events = cached(P("events.jsonl"), parse_events) or []
    eqs = cached(P("equity.csv"), parse_equity) or []
    trades_raw = cached(P("trades.csv"), parse_trades) or []
    narr = cached(P("narratives.json"), read_json)
    ovr = cached(P("overrides.json"), read_json)
    runlog = cached(P("run.log"), lambda p: tail_lines(p, 20000)[-40:]) or []
    proc = bot_process()
    start_bal = fnum(cfg.get("START_BALANCE"), 1000.0)

    # posizioni
    positions, invested_cost, pos_value = [], 0.0, 0.0
    raw_pos = st.get("positions") if isinstance(st.get("positions"), dict) else {}
    for mint, p in raw_pos.items():
        if not isinstance(p, dict):
            continue
        sym = clean_sym(p.get("symbol", "?"))
        tokens, entry, last = fnum(p.get("tokens")), fnum(p.get("entry_mark")), fnum(p.get("last_price"))
        value = exit_value(tokens, last, p.get("liq"), cfg)
        basis = tokens * entry
        pnl_pct = (value / basis - 1) * 100 if basis > 0 else 0.0
        cost, realized = fnum(p.get("cost")), fnum(p.get("realized"))
        ef = p.get("eff") if isinstance(p.get("eff"), dict) else {}
        stage = int(fnum(ef.get("tp_stage", p.get("tp_stage", 0))))
        stop = fnum(ef.get("stop"), fnum(cfg.get("STOP_LOSS_PCT"), -10))
        if stage >= 1 and cfg.get("BREAKEVEN_PCT") is not None:
            stop = max(stop, fnum(cfg.get("BREAKEVEN_PCT")))
        tp1 = fnum(ef.get("tp1"), fnum(cfg.get("TP1_PCT"), 30))
        tp2 = fnum(ef.get("tp"), fnum(cfg.get("TAKE_PROFIT_PCT"), 80))
        tstart = fnum(ef.get("trail_start"), fnum(cfg.get("TRAIL_ACTIVATE_PCT"), 40))
        tdist = fnum(ef.get("trail_dist"), fnum(cfg.get("TRAIL_DISTANCE_PCT"), 15))
        peak = fnum(p.get("peak_pct"))
        pos_value += value
        invested_cost += cost
        positions.append({
            "sym": sym, "mint": str(mint)[:60], "cost": round(cost, 2), "value": round(value, 2),
            "realized": round(realized, 2), "pnl_pct": round(pnl_pct, 2),
            "pnl_usd": round(value + realized - cost, 2), "peak": round(peak, 2),
            "min": int((now - fnum(p.get("opened"), now)) / 60), "score": round(fnum(p.get("score")), 3),
            "stop": round(stop, 1), "tp1": round(tp1, 1), "tp2": round(tp2, 1), "tp_stage": stage,
            "trail_start": round(tstart, 1), "trail_dist": round(tdist, 1), "trail_on": peak >= tstart,
            "trail_exit": round(peak - tdist, 1), "max_hold": int(fnum(ef.get("max_hold"), fnum(cfg.get("MAX_HOLD_MIN"), 180))),
            "narr_bonus": round(fnum(p.get("narr_bonus")), 3), "rt": fnum(p.get("rt_cost_pct"), None),
            "thesis": clean_text(str(p.get("thesis", ""))[:300], [p.get("symbol")]),
            "features": {str(k): round(fnum(v), 3) for k, v in (p.get("features") or {}).items()} if isinstance(p.get("features"), dict) else {},
        })
    positions.sort(key=lambda x: -x["pnl_pct"])

    cash = fnum(st.get("cash"), start_bal)
    eq_now = cash + pos_value if (st or not eqs) else eqs[-1][1]
    start = fnum(st.get("period_start_equity"), start_bal) or start_bal

    # equity: max ~400 punti
    eq = eqs
    if len(eq) > 400:
        step = len(eq) / 400
        eq = [eq[int(i * step)] for i in range(400)] + [eq[-1]]
    eq_out = [[str(t)[5:16].replace("T", " "), round(v, 2)] for t, v in eq]

    # eventi + candidati
    syms_seen = set()
    feed = []
    for e in events[-400:]:
        s = sym_of(e)
        if s:
            syms_seen.add(s)
        feed.append({"t": hhmmss(e.get("ts")), "a": str(e.get("agent", ""))[:20], "k": str(e.get("kind", ""))[:20],
                     "m": clean_text(str(e.get("msg", ""))[:260], [s])})
    feed.reverse()

    cands = {}
    for e in events:
        mint = e.get("mint")
        ag = e.get("agent")
        if not mint or ag not in ("Filter", "Analyst", "Executor", "Risk"):
            continue
        s = sym_of(e) or "?"
        c = cands.setdefault(mint, {"mint": str(mint)[:60], "sym": clean_sym(s), "first": e.get("ts"), "steps": []})
        c["last"] = e.get("ts")
        c["steps"].append({"t": hhmmss(e.get("ts")), "a": ag, "k": e.get("kind"),
                           "m": clean_text(str(e.get("msg", ""))[:260], [s])})
    cand_list = []
    for mint, c in cands.items():
        agents = {x["a"] for x in c["steps"]}
        if agents == {"Filter"} and not any(x["k"] == "pass" for x in c["steps"]):
            continue  # scartato subito al Filter: resta nel feed
        lastst = c["steps"][-1]
        stage = {"Filter": "Filter", "Analyst": "Analyst", "Executor": "Executor", "Risk": "Risk"}[lastst["a"]]
        if lastst["a"] == "Filter" and lastst["k"] == "pass":
            status = "in analisi"
        elif lastst["k"] in ("reject", "block"):
            status = "scartato"
        elif lastst["k"] == "approve":
            status = "approvato"
        elif lastst["k"] == "buy" or mint in raw_pos:
            status = "comprato"
        elif lastst["k"] == "sell":
            status = "venduto"
        else:
            status = lastst["k"]
        if mint in raw_pos:
            status = "in posizione"
        parts = {}
        for x in c["steps"]:
            if x["a"] in ("Analyst", "Executor"):
                parts.update(score_parts(x["m"]))
        reason = ""
        for x in reversed(c["steps"]):
            if x["k"] in ("reject", "block"):
                reason = x["m"].split(":", 1)[1].strip() if ":" in x["m"] else x["m"]
                break
        cand_list.append({"mint": c["mint"], "sym": c["sym"], "t": hhmmss(c["last"]), "ts": c["last"],
                          "status": status, "stage": stage, "reason": reason, "parts": parts,
                          "steps": c["steps"][-6:], "n": len(c["steps"])})
    cand_list.sort(key=lambda x: str(x["ts"]), reverse=True)
    cand_list = cand_list[:30]

    # ultimo messaggio per agente
    agents = {}
    for e in reversed(events):
        a = str(e.get("agent", ""))
        if a and a not in agents:
            agents[a] = {"t": hhmmss(e.get("ts")), "k": e.get("kind"), "m": clean_text(str(e.get("msg", ""))[:200], [sym_of(e)])}

    # trade chiusi
    trades = []
    for r in reversed(trades_raw):
        trades.append({"t": str(r.get("chiuso", ""))[5:], "sym": clean_sym(r.get("symbol")), "mint": str(r.get("mint", ""))[:60],
                       "cost": fnum(r.get("cost")), "pnl": fnum(r.get("pnl")), "pct": fnum(r.get("pnl_pct")),
                       "min": int(fnum(r.get("minutes"))), "peak": fnum(r.get("peak_pct")),
                       "score": fnum(r.get("score")), "tp_stage": int(fnum(r.get("tp_stage"))),
                       "reason": clean_text(str(r.get("reason", ""))[:120], [r.get("symbol")])})
    wins = sum(1 for t in trades if t["pnl"] > 0)

    # narrative di Radar
    narr_out, blocklist_n, narr_upd = [], 0, None
    if isinstance(narr, dict):
        narr_upd = narr.get("updated_at")
        blocklist_n = len(narr.get("blocklist_mints") or [])
        for n in (narr.get("narratives") or [])[:40]:
            if not isinstance(n, dict):
                continue
            mc = n.get("mint_confidence") if isinstance(n.get("mint_confidence"), dict) else {}
            narr_out.append({
                "theme": clean_text(str(n.get("theme", ""))[:120]),
                "tickers": [clean_sym(t) for t in (n.get("tickers") or [])[:8]],
                "keywords": [clean_sym(t) for t in (n.get("keywords") or [])[:8]],
                "strength": fnum(n.get("strength")), "trend": str(n.get("trend", ""))[:20],
                "velocity": n.get("velocity") if isinstance(n.get("velocity"), (int, float)) else None,
                "trusted": n.get("trusted_mentions") if isinstance(n.get("trusted_mentions"), (int, float)) else None,
                "mints": len(n.get("mints") or []),
                "conf_max": max([fnum(v) for v in mc.values()] or [None]) if mc else None,
                "note": clean_text(str(n.get("note", ""))[:240]),
            })
        narr_out.sort(key=lambda x: -x["strength"])

    # override di Stewy
    ovr_out = None
    if isinstance(ovr, dict):
        g = ovr.get("global") if isinstance(ovr.get("global"), dict) else {}
        pp = ovr.get("positions") if isinstance(ovr.get("positions"), dict) else {}
        sym_by_mint = {m: clean_sym(p.get("symbol")) for m, p in raw_pos.items() if isinstance(p, dict)}
        ovr_out = {"updated_at": str(ovr.get("updated_at", ""))[:40],
                   "reason": clean_text(str(ovr.get("reason", ""))[:300]),
                   "global": {str(k)[:40]: v for k, v in g.items() if isinstance(v, (int, float, str, bool)) or v is None},
                   "positions": [{"mint": str(m)[:60], "sym": sym_by_mint.get(m, ""),
                                  "vals": clean_obj({k: v for k, v in (d or {}).items() if isinstance(v, (int, float, str, bool))})}
                                 for m, d in list(pp.items())[:20] if isinstance(d, dict)]}
    ovr_events = [f for f in feed if f["a"] == "Risk" and f["k"] in ("override",)][:10]

    stats = st.get("stats") if isinstance(st.get("stats"), dict) else {}
    reasons = st.get("reject_reasons") if isinstance(st.get("reject_reasons"), dict) else {}
    reasons = sorted(((clean_text(str(k)), int(fnum(v))) for k, v in reasons.items()), key=lambda x: -x[1])[:15]

    data = {
        "mode": str(st.get("mode") or cfg.get("MODE") or "paper").upper(),
        "equity": round(eq_now, 2), "start": round(start, 2), "cash": round(cash, 2),
        "pnl": round(eq_now - start, 2), "pnl_pct": round((eq_now / start - 1) * 100, 2) if start else 0.0,
        "invested_pct": round((eq_now - cash) / eq_now * 100, 1) if eq_now > 0 else 0.0,
        "invested_cost": round(invested_cost, 2),
        "n_pos": len(positions), "max_pos": int(fnum(cfg.get("MAX_POSITIONS"), 8)),
        "alive": proc is not None, "pid": proc[0] if proc else None, "bot_started": proc[1] if proc else None,
        "period_start": fnum(st.get("period_start_ts"), None),
        "threshold": round(fnum(st.get("threshold"), fnum(cfg.get("SCORE_THRESHOLD"), 0.5)), 3),
        "learned": int(fnum((st.get("model") or {}).get("n") if isinstance(st.get("model"), dict) else 0)),
        "paused": bool(st.get("paused")), "stop_file": os.path.exists(P("STOP")),
        "blacklist": len(st.get("blacklist") or []),
        "stats": {str(k): int(fnum(v)) for k, v in stats.items()},
        "reasons": reasons, "eq": eq_out, "positions": positions, "trades": trades[:60],
        "closed_n": len(trades_raw), "wins": wins,
        "realized_total": round(sum(t["pnl"] for t in trades), 2),
        "feed": feed[:300], "agents": agents, "candidates": cand_list,
        "narratives": narr_out, "narr_updated": narr_upd, "narr_blocklist": blocklist_n,
        "overrides": ovr_out, "override_events": ovr_events,
        "lessons": [clean_text(str(x)[:200]) for x in (st.get("lessons") or [])[-10:]][::-1],
        "runlog": [clean_text(x[:220]) for x in runlog[-25:]],
        "params": {k: cfg.get(k) for k in CFG_KEYS if k in cfg and k != "MODE"},
        "files_age": {n: (round(now - os.path.getmtime(P(n))) if os.path.exists(P(n)) else None)
                      for n in ("state.json", "events.jsonl", "equity.csv", "narratives.json", "overrides.json")},
    }
    return data


# --------------------------------------------------------------- server
SNAP = {"data": None, "json": "{}", "hash": "", "ts": 0}
CLIENTS = set()


async def updater():
    while True:
        try:
            d = build()
            body = json.dumps(d, ensure_ascii=False, separators=(",", ":"), default=str)
            h = hashlib.sha1(body.encode()).hexdigest()
            if h != SNAP["hash"]:
                SNAP.update(json=body, hash=h)
                for q in list(CLIENTS):
                    if q.qsize() < 3:
                        q.put_nowait(("snap", body))
            SNAP["ts"] = time.time()
        except Exception as e:  # noqa: BLE001
            print(f"{datetime.now(TZ):%H:%M:%S} errore snapshot: {type(e).__name__}: {e}", flush=True)
        await asyncio.sleep(TICK)


SEC_HEADERS = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"}


async def index(request):
    with open(os.path.join(HERE, "index.html"), "rb") as f:
        body = f.read()
    return web.Response(body=body, content_type="text/html", charset="utf-8",
                        headers={**SEC_HEADERS, "Cache-Control": "no-cache"})


async def snapshot(request):
    return web.Response(text=SNAP["json"], content_type="application/json",
                        headers={**SEC_HEADERS, "Cache-Control": "no-store"})


async def stream(request):
    resp = web.StreamResponse(headers={**SEC_HEADERS, "Content-Type": "text/event-stream", "Cache-Control": "no-cache, no-transform",
                                       "X-Accel-Buffering": "no", "Connection": "keep-alive"})
    await resp.prepare(request)
    q = asyncio.Queue()
    CLIENTS.add(q)
    try:
        await resp.write(b"retry: 3000\n\n")
        await resp.write(f"event: snap\ndata: {SNAP['json']}\n\n".encode())
        while True:
            try:
                ev, body = await asyncio.wait_for(q.get(), timeout=5)
                await resp.write(f"event: {ev}\ndata: {body}\n\n".encode())
            except asyncio.TimeoutError:
                await resp.write(f"event: ping\ndata: {{\"t\":{time.time():.0f}}}\n\n".encode())
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        CLIENTS.discard(q)
    return resp


async def on_start(app):
    app["upd"] = asyncio.create_task(updater())


def main():
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/api/snapshot", snapshot)
    app.router.add_get("/api/stream", stream)
    app.on_startup.append(on_start)
    print(f"Desk web su http://{HOST}:{PORT} (sola lettura)", flush=True)
    web.run_app(app, host=HOST, port=PORT, print=None, access_log=None)


if __name__ == "__main__":
    main()
