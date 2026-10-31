#!/usr/bin/env python3
"""
Backtest rapido delle regole di uscita (PAPER, solo dati pubblici GeckoTerminal).
Non tocca desk.py ne' lo stato del bot. Nessun wallet.

Uso:
  python3 exit_bt.py              # fetch (con cache) + backtest
  python3 exit_bt.py --no-fetch   # solo backtest sui dati in cache
  python3 exit_bt.py --refresh    # ignora la lista pool in cache e ne raccoglie una nuova
Opzioni: --min-pools 40 --max-pools 60 --min-liq 8000 --max-age-h 48 --min-age-h 4
"""
import argparse, json, os, sys, time, hashlib, itertools
from datetime import datetime, timezone
import urllib.request, urllib.error

BASE = "https://api.geckoterminal.com/api/v2"
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OHLCV_DIR = os.path.join(DATA, "ohlcv")          # campione "survivor" (run 1)
POOLS_FILE = os.path.join(DATA, "pools.json")
NP_DIR = os.path.join(DATA, "newpools")           # campione "newpools" (senza bias di sopravvivenza)
DEAD_LOSS = -0.90   # sensitivita': quota residua di un trade la cui pool smette di scambiare durante l'hold
MIN_INTERVAL = 3.2          # >= 3s tra richieste (il bot usa la stessa API)
COST_RT = 0.03              # costo round trip ~3%
NON_MEME = {  # mint "quote"/blue-chip da escludere come base token
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}
_last = [0.0]

def get(path, params=None):
    url = BASE + path
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    for attempt in range(6):
        wait = MIN_INTERVAL - (time.time() - _last[0])
        if wait > 0: time.sleep(wait)
        _last[0] = time.time()
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "paper-exit-backtest/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                back = min(60, 10 * 2 ** attempt)
                print(f"  HTTP {e.code}, backoff {back}s", file=sys.stderr); time.sleep(back); continue
            if e.code == 404: return None
            raise
        except Exception as e:
            back = 5 * (attempt + 1); print(f"  err {e}, retry {back}s", file=sys.stderr); time.sleep(back)
    return None

def iso(s): return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()

def collect_pools(a):
    now = time.time(); seen = {}; pools = []
    sources = [("/networks/solana/new_pools", {"page": p}) for p in range(1, 4)]
    sources += [("/networks/solana/trending_pools", {"duration": d, "page": p}) for d in ("24h", "6h") for p in range(1, 6)]
    sources += [("/networks/solana/pools", {"sort": "h24_volume_usd_desc", "page": p}) for p in range(1, 11)]
    sources += [("/networks/solana/pools", {"sort": "h24_tx_count_desc", "page": p}) for p in range(1, 11)]
    sources += [(f"/networks/solana/dexes/{dx}/pools", {"sort": "h24_volume_usd_desc", "page": p})
                for dx in ("pumpswap", "raydium", "meteora-damm-v2", "raydium-launchlab") for p in range(1, 6)]
    for path, params in sources:
        if len(pools) >= a.max_pools: break
        d = get(path, params)
        if not d or not d.get("data"): continue
        for x in d["data"]:
            at = x["attributes"]; addr = at["address"]
            if addr in seen: continue
            seen[addr] = 1
            try:
                age_h = (now - iso(at["pool_created_at"])) / 3600
                liq = float(at.get("reserve_in_usd") or 0)
            except Exception: continue
            base = x["relationships"]["base_token"]["data"]["id"].split("_", 1)[-1]
            if base in NON_MEME: continue
            if not (a.min_age_h <= age_h <= a.max_age_h) or liq < a.min_liq: continue
            pools.append({"address": addr, "created": iso(at["pool_created_at"]), "liq_now": liq,
                          "dex": x["relationships"]["dex"]["data"]["id"], "src": path})
            if len(pools) >= a.max_pools: break
        print(f"  {path} {params}: {len(pools)} pool idonee")
    return pools

def fetch_ohlcv(p, odir=None):
    fn = os.path.join(odir or OHLCV_DIR, p["address"] + ".json")
    if os.path.exists(fn):
        return json.load(open(fn))
    rows = {}; before = None
    for _ in range(4):  # max 4000 minuti (~66h)
        params = {"aggregate": 1, "limit": 1000, "currency": "usd", "token": "base"}
        if before: params["before_timestamp"] = before
        d = get(f"/networks/solana/pools/{p['address']}/ohlcv/minute", params)
        if d is None:  # 429/errore persistente (o 404): NON salvare un file vuoto, si riprova al prossimo giro
            if rows: break
            return []
        lst = d.get("data", {}).get("attributes", {}).get("ohlcv_list", [])
        if not lst: break
        for r in lst: rows[int(r[0])] = [float(v) for v in r[1:6]]
        oldest = min(int(r[0]) for r in lst)
        if oldest <= p["created"] + 60 or len(lst) < 1000: break
        before = oldest
    out = [[t] + rows[t] for t in sorted(rows)]
    json.dump(out, open(fn, "w"))
    return out

def snapshot_newpools(n_sample):
    """Legge TUTTE le pagine di new_pools disponibili (free tier: 10 pagine = ~200 pool, ordinate per creazione).
    Nessun filtro di liquidita' attuale. Salva tutto lo snapshot e un campione uniforme lungo la sequenza."""
    os.makedirs(os.path.join(NP_DIR, "ohlcv"), exist_ok=True)
    snap_ts = time.time(); allp = []; seen = set()
    for page in range(1, 11):
        d = get("/networks/solana/new_pools", {"page": page})
        if not d or not d.get("data"): break
        for x in d["data"]:
            at = x["attributes"]; addr = at["address"]
            if addr in seen: continue
            seen.add(addr)
            base = x["relationships"]["base_token"]["data"]["id"].split("_", 1)[-1]
            allp.append({"address": addr, "created": iso(at["pool_created_at"]), "snap_ts": snap_ts,
                         "reserve_at_snap": float(at.get("reserve_in_usd") or 0),
                         "dex": x["relationships"]["dex"]["data"]["id"], "base_is_meme": base not in NON_MEME})
    allp.sort(key=lambda p: p["created"])
    json.dump(allp, open(os.path.join(NP_DIR, f"snapshot_{int(snap_ts)}.json"), "w"))
    cand = [p for p in allp if p["base_is_meme"]]
    step = max(1, len(cand) / n_sample) if cand else 1
    sample = [cand[int(i * step)] for i in range(min(n_sample, len(cand)))]
    pf = os.path.join(NP_DIR, "pools.json")
    old = json.load(open(pf)) if os.path.exists(pf) else []
    have = {p["address"] for p in old}
    old += [p for p in sample if p["address"] not in have]
    json.dump(old, open(pf, "w"), indent=1)
    span = (allp[-1]["created"] - allp[0]["created"]) / 60 if allp else 0
    print(f"snapshot: {len(allp)} pool, finestra creazione {span:.0f} min; campione +{len(sample)} (totale {len(old)})")
    return old

# ---------------- segnali ----------------
def fill_minutes(raw):
    """Riempie i minuti senza trade (vol 0, prezzo = close precedente)."""
    if not raw: return []
    out = []; t0 = raw[0][0]; idx = {r[0]: r for r in raw}; prev = None
    for t in range(t0, raw[-1][0] + 60, 60):
        r = idx.get(t)
        if r: out.append(r); prev = r[4]
        else: out.append([t, prev, prev, prev, prev, 0.0])
    return out

ACC_FACTOR = 2.0     # vol ultimi 5m >= 2x vol 5m precedenti
MIN_VOL5 = 2000.0    # e almeno $2k di volume negli ultimi 5m (filtra rumore)

def find_entry(bars):
    """Primo minuto t (>=10 barre di storia) con accelerazione volume e close > VWAP5.
    Ingresso all'OPEN della barra successiva (niente look-ahead)."""
    for t in range(9, len(bars) - 1):
        v5 = sum(b[5] for b in bars[t-4:t+1]); vp = sum(b[5] for b in bars[t-9:t-4])
        if v5 < MIN_VOL5 or v5 < ACC_FACTOR * max(vp, 1e-9): continue
        vwap = sum((b[2] + b[3] + b[4]) / 3 * b[5] for b in bars[t-4:t+1]) / v5
        if bars[t][4] > vwap:
            return t + 1
    return None

# ---------------- simulatore uscite ----------------
def simulate(bars, i0, R):
    """R: dict regole. Ritorna (rendimento netto, minuti, motivo). Ordine intrabar conservativo:
    stop (SL/BE/trailing calcolato sul picco delle barre precedenti) prima dei TP."""
    e = bars[i0][1]
    rem = 1.0; got = 0.0; peak = e; stop = e * (1 + R["sl"]); tps = [list(x) for x in R["tps"]]
    trail_on = False; reason = "end_data"; last = i0
    for k, i in enumerate(range(i0, len(bars))):
        o, h, l, c = bars[i][1:5]; last = i
        # trailing dal picco precedente
        if trail_on:
            dist = R["trail_dist"]
            for thr, d in R.get("trail_tight", []):
                if peak >= e * (1 + thr): dist = d
            stop = max(stop, peak * (1 - dist))
        if l <= stop:
            px = min(o, stop) if k > 0 else min(o, stop) if o < stop else stop
            got += rem * px / e; rem = 0; reason = "stop"; break
        for tp in tps:
            lvl, frac, be = tp
            if frac > 0 and h >= e * (1 + lvl):
                sell = min(rem, frac); got += sell * (max(o, e * (1 + lvl))) / e; rem -= sell; tp[1] = 0
                if be is not None: stop = max(stop, e * (1 + be))
        if rem <= 1e-9: reason = "tp"; break
        peak = max(peak, h)
        if not trail_on and peak >= e * (1 + R["trail_act"]): trail_on = True
        if trail_on:  # nuovo picco su questa barra: esci a close solo se la close e' sotto il trailing
            dist = R["trail_dist"]
            for thr, d in R.get("trail_tight", []):
                if peak >= e * (1 + thr): dist = d
            if c < peak * (1 - dist):
                got += rem * c / e; rem = 0; reason = "trail"; break
        if R.get("time_stop") and k + 1 == R["time_stop"][0] and c < e * (1 + R["time_stop"][1]):
            got += rem * c / e; rem = 0; reason = "time"; break
        if k + 1 >= R["max_hold"]:
            got += rem * c / e; rem = 0; reason = "max_hold"; break
    dead_rem = 0.0
    if rem > 0:
        got += rem * bars[last][4] / e
        if reason == "end_data": dead_rem = rem
    simulate.dead_rem = dead_rem
    return got - 1 - COST_RT, last - i0 + 1, reason

RULES = {
    "A_rif": dict(sl=-0.08, tps=[(1.00, 0.5, None)], trail_act=0.15, trail_dist=0.10, max_hold=360),
    "B": dict(sl=-0.10, tps=[(0.30, 0.3, 0.03), (0.80, 0.3, None)], trail_act=0.40, trail_dist=0.15,
              trail_tight=[(1.50, 0.08)], time_stop=(30, 0.05), max_hold=180),
    "B_live": dict(sl=-0.12, tps=[(0.30, 0.3, 0.03), (0.80, 0.3, None)], trail_act=0.40, trail_dist=0.15,
                   trail_tight=[(1.50, 0.08)], time_stop=(240, 0.05), max_hold=180),
    "C15": dict(sl=-0.15, tps=[(0.30, 0.3, 0.03), (0.80, 0.3, None)], trail_act=0.40, trail_dist=0.15,
                trail_tight=[(1.50, 0.08)], time_stop=None, max_hold=180),
}

def make_C(sl, tp1, ts_min, act, dist):
    return dict(sl=sl, tps=[(tp1, 0.3, 0.03), (0.80, 0.3, None)], trail_act=act, trail_dist=dist,
                trail_tight=[(1.50, 0.08)], time_stop=(ts_min, 0.05) if ts_min else None, max_hold=180)

C_GRID = dict(sl=[-0.10, -0.15, -0.20], tp1=[0.20, 0.30], ts_min=[15, 30, None], act=[0.25, 0.40], dist=[0.15, 0.20])

def stats(rets):
    n = len(rets)
    if not n: return dict(n=0)
    eq = 0; pk = 0; mdd = 0
    for r in rets: eq += r; pk = max(pk, eq); mdd = min(mdd, eq - pk)
    comp = 1.0; cpk = 1.0; cmdd = 0
    for r in rets: comp *= 1 + 0.10 * r; cpk = max(cpk, comp); cmdd = min(cmdd, comp / cpk - 1)
    srt = sorted(rets); med = srt[n // 2] if n % 2 else (srt[n // 2 - 1] + srt[n // 2]) / 2
    return dict(n=n, win=sum(r > 0 for r in rets) / n, avg=sum(rets) / n, med=med, sum=sum(rets), mdd_sum=mdd,
                comp10=comp - 1, mdd_comp10=cmdd)

def run_set(trades, R, dead_penalty=False):
    res = []
    for t in trades:
        r, m, why = simulate(t["bars"], t["i0"], R)
        if dead_penalty and simulate.dead_rem > 0 and t.get("stale"):
            # la pool ha smesso di scambiare prima della fine dell'hold: quota residua = perdita grande
            r = r - simulate.dead_rem * (t["bars"][-1][4] / t["bars"][t["i0"]][1]) + simulate.dead_rem * (1 + DEAD_LOSS)
            why = "dead"
        res.append((t["t_entry"], r, m, why))
    res.sort(key=lambda x: x[0])
    return [r[1] for r in res], res

def fmt(name, s):
    if not s.get("n"): return f"| {name} | 0 | - | - | - | - | - | - | - |"
    return (f"| {name} | {s['n']} | {s['win']*100:.0f}% | {s['avg']*100:+.1f}% | {s['med']*100:+.1f}% | {s['sum']*100:+.0f}% | "
            f"{s['mdd_sum']*100:.0f}% | {s['comp10']*100:+.1f}% | {s['mdd_comp10']*100:.1f}% |")

def successor_bars(p, odir, after_ts, no_fetch=False):
    """Se la pool ha smesso di scambiare, cerca un'altra pool dello stesso token creata dopo (migrazione).
    Cache: succ_<pool>.json (lista OHLCV, [] se nessuna)."""
    fn = os.path.join(odir, "succ_" + p["address"] + ".json")
    if os.path.exists(fn): return json.load(open(fn))
    if no_fetch: return []
    out = []
    d = get(f"/networks/solana/pools/{p['address']}")
    try: mint = d["data"]["relationships"]["base_token"]["data"]["id"].split("_", 1)[-1]
    except Exception: mint = None
    if mint:
        tp = get(f"/networks/solana/tokens/{mint}/pools")
        cands = []
        for x in (tp or {}).get("data", []):
            at = x["attributes"]
            if at["address"] == p["address"]: continue
            if x["relationships"]["base_token"]["data"]["id"].split("_", 1)[-1] != mint: continue
            c = iso(at["pool_created_at"])
            if c >= p["created"] and c <= after_ts + 3600:
                cands.append((float(at.get("reserve_in_usd") or 0), c, at["address"]))
        if cands:
            _, c, addr = max(cands)  # la pool piu' liquida tra quelle nate dopo (tipicamente la migrazione)
            out = fetch_ohlcv({"address": addr, "created": c}, odir)
    json.dump(out, open(fn, "w"))
    return out

def load_trades(pools, odir, no_fetch, min_age_h=0, liq_min=0, stitch=False):
    trades = []; cnt = dict(pools=len(pools), not_ready=0, no_ohlcv=0, short=0, no_signal=0, liq_skip=0, trades=0)
    now = time.time()
    for n, p in enumerate(pools):
        if (now - p["created"]) / 3600 < min_age_h: cnt["not_ready"] += 1; continue
        if liq_min and p.get("reserve_at_snap", 1e18) < liq_min: cnt["liq_skip"] += 1; continue
        fn = os.path.join(odir, p["address"] + ".json")
        if no_fetch and not os.path.exists(fn): continue
        if not os.path.exists(fn): print(f"  OHLCV {n+1}/{len(pools)} {p['address'][:6]}..", flush=True)
        raw = fetch_ohlcv(p, odir)
        if not raw: cnt["no_ohlcv"] += 1; continue
        bars = fill_minutes(raw)
        if len(bars) < 30: cnt["short"] += 1; continue
        i0 = find_entry(bars)
        if i0 is None: cnt["no_signal"] += 1; continue
        stale0 = (os.path.getmtime(fn) - bars[-1][0]) > 1800
        if stitch and stale0 and bars[-1][0] < bars[i0][0] + 360 * 60:
            nxt = successor_bars(p, odir, bars[-1][0], no_fetch)
            if nxt:  # migrazione (es. bonding curve pump.fun -> pumpswap): continua sulla nuova pool
                bars = fill_minutes([b for b in raw if b[0] <= bars[-1][0]] + [b for b in nxt if b[0] > bars[-1][0]])
                cnt["stitched"] = cnt.get("stitched", 0) + 1
                fn = os.path.join(odir, "succ_" + p["address"] + ".json")
        h = int(hashlib.md5(p["address"].encode()).hexdigest(), 16) % 2
        # "stale": l'ultimo scambio e' >30 min prima del fetch -> pool morta/abbandonata
        stale = (os.path.getmtime(fn) - bars[-1][0]) > 1800
        trades.append(dict(pool=p["address"][:6], t_entry=bars[i0][0], i0=i0, bars=bars, half=h,
                           min_after=len(bars) - i0, stale=stale))
    cnt["trades"] = len(trades)
    return trades, cnt

HDR = ["| Set | Trade | Win | Medio/trade | Mediana | Totale (somma) | MaxDD (somma) | Compost. 10%/trade | MaxDD compost. |",
       "|---|---|---|---|---|---|---|---|---|"]

def report(trades, label, dead_penalty=False):
    lines = [f"### {label}"] + HDR; out = {}
    train = [t for t in trades if t["half"] == 0]; test = [t for t in trades if t["half"] == 1]
    for name, R in RULES.items():
        for lab, sub in (("tutti", trades), ("meta0", train), ("meta1", test)):
            r, res = run_set(sub, R, dead_penalty); s = stats(r); out[f"{name}/{lab}"] = s
            lines.append(fmt(f"{name} [{lab}]", s))
            if lab == "tutti":
                reasons = {}
                for x in res: reasons[x[3]] = reasons.get(x[3], 0) + 1
                out[f"{name}/uscite"] = reasons
    txt = "\n".join(lines); print(txt)
    for name in RULES: print("  ", name, "uscite:", out[f"{name}/uscite"])
    return txt, out


# ---------------- test extra (richiesti da Kiwi) ----------------
def pool_meta(addrs, cache_fn, no_fetch=False):
    """GeckoTerminal pools/multi (30 per richiesta): locked_liquidity_percentage, mint, reserve attuale. Cache su file."""
    meta = json.load(open(cache_fn)) if os.path.exists(cache_fn) else {}
    todo = [a for a in addrs if a not in meta]
    if not no_fetch:
        for i in range(0, len(todo), 30):
            d = get("/networks/solana/pools/multi/" + ",".join(todo[i:i+30]))
            if d is None: print("  meta: fetch fallito, uso parziale"); break
            for x in d.get("data", []):
                at = x["attributes"]; ll = at.get("locked_liquidity_percentage")
                meta[at["address"]] = dict(locked=float(ll) if ll not in (None, "") else None,
                                           mint=x["relationships"]["base_token"]["data"]["id"].split("_", 1)[-1],
                                           reserve_now=float(at.get("reserve_in_usd") or 0))
            json.dump(meta, open(cache_fn, "w"), indent=1)
    return meta

def with_(R, **kw):
    R = dict(R); R.update(kw); return R

def extra_tests(trades, pools, meta, label):
    full = {p["address"][:6]: p for p in pools}
    lines = [f"## Test extra - {label} ({len(trades)} trade)"]; out = {}
    BL = RULES["B_live"]
    def addr(t): return full[t["pool"]]["address"]
    def unlocked(t):
        m = meta.get(addr(t)); 
        if not m or m["locked"] is None: return None
        return m["locked"] < 50
    known = [t for t in trades if unlocked(t) is not None]
    n_unl = sum(1 for t in known if unlocked(t))
    lines.append(f"LP lock (GeckoTerminal locked_liquidity_percentage, valore ATTUALE non all'ingresso; <50% = non bloccata): "
                 f"noto per {len(known)}/{len(trades)} trade, non bloccata {n_unl}, bloccata {len(known)-n_unl}")
    def run_mixed(R_unl, R_lock):
        res = []
        for t in trades:
            R = R_unl if unlocked(t) else R_lock
            r, m, why = simulate(t["bars"], t["i0"], R); res.append((t["t_entry"], r))
        res.sort(); return [x[1] for x in res]
    cfgs = [("B_live (trail 15%, hold 180)", lambda: run_set(trades, BL)[0]),
            ("B_live trail 10%", lambda: run_set(trades, with_(BL, trail_dist=0.10))[0]),
            ("B_live trail 10% attivo da TP1 (+30%)", lambda: run_set(trades, with_(BL, trail_dist=0.10, trail_act=0.30))[0]),
            ("B_live hold 60 per LP non bloccata, 180 altrimenti", lambda: run_mixed(with_(BL, max_hold=60), BL)),
            ("B_live hold 60 per TUTTI", lambda: run_set(trades, with_(BL, max_hold=60))[0]),
            ("B_live trail 10% + hold 60 LP non bloccata", lambda: run_mixed(with_(BL, trail_dist=0.10, max_hold=60), with_(BL, trail_dist=0.10)))]
    lines += HDR
    for name, f in cfgs:
        s = stats(f()); out[name] = s; lines.append(fmt(name, s))
    # young momentum: eta' del token (pool) all'ingresso < 6h; tutti i trade hanno gia' il segnale di volume in accelerazione
    young = [t for t in trades if (t["t_entry"] - full[t["pool"]]["created"]) / 3600 < 6]
    old = [t for t in trades if t not in young]
    lines += ["", f"Young momentum (eta' < 6h all'ingresso + volume in accelerazione) vs resto, regole B_live: {len(young)} vs {len(old)} trade"] + HDR
    for name, sub in (("young <6h", young), ("resto >=6h", old)):
        s = stats(run_set(sub, BL)[0]); out["young/" + name] = s; lines.append(fmt(name, s))
    txt = "\n".join(lines); print(txt)
    return txt, out

def migration_check(pools, meta, no_fetch=False):
    """Per i token del campione: la pool piu' liquida oggi e' un'altra (migrazione)? tokens/multi con top_pools."""
    mints = sorted({m["mint"] for a, m in meta.items() if a in {p["address"] for p in pools}})
    res = {}
    if no_fetch: return res
    for i in range(0, len(mints), 30):
        d = get("/networks/solana/tokens/multi/" + ",".join(mints[i:i+30]), {"include": "top_pools"})
        if d is None: break
        inc = {x["id"]: x["attributes"] for x in d.get("included", []) if x.get("type") == "pool"}
        for x in d.get("data", []):
            mint = x["attributes"]["address"]
            tops = [inc.get(y["id"], {}) for y in x.get("relationships", {}).get("top_pools", {}).get("data", [])]
            res[mint] = [(t.get("address"), float(t.get("reserve_in_usd") or 0)) for t in tops if t]
    return res

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", choices=["survivor", "newpools"], default="survivor")
    ap.add_argument("--snapshot", action="store_true", help="newpools: legge ora tutte le pagine new_pools e campiona")
    ap.add_argument("--n-sample", type=int, default=100)
    ap.add_argument("--no-fetch", action="store_true"); ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--min-pools", type=int, default=40); ap.add_argument("--max-pools", type=int, default=60)
    ap.add_argument("--min-liq", type=float, default=8000); ap.add_argument("--max-age-h", type=float, default=48)
    ap.add_argument("--min-age-h", type=float, default=4)
    ap.add_argument("--extra", action="store_true", help="test extra (trail 10/15, hold 60/180 per LP, young momentum)")
    ap.add_argument("--np-dir", default=None, help="newpools: cartella dati alternativa (es. data/newpools_1031_s1); output con suffisso")
    ap.add_argument("--snapshot-only", action="store_true", help="newpools: fa solo lo snapshot ed esce (nessun OHLCV)")
    a = ap.parse_args()
    global NP_DIR
    np_suffix = ""
    if a.np_dir:
        NP_DIR = a.np_dir if os.path.isabs(a.np_dir) else os.path.join(HERE, a.np_dir)
        np_suffix = "_" + os.path.basename(os.path.normpath(NP_DIR))
    md = []; allout = {}
    if a.sample == "survivor":
        os.makedirs(OHLCV_DIR, exist_ok=True)
        if os.path.exists(POOLS_FILE) and not a.refresh: pools = json.load(open(POOLS_FILE))
        else:
            if a.no_fetch: sys.exit("nessuna cache pool")
            pools = collect_pools(a); json.dump(pools, open(POOLS_FILE, "w"), indent=1)
        trades, cnt = load_trades(pools, OHLCV_DIR, a.no_fetch)
        print("conteggi:", cnt)
        t, o = report(trades, f"Campione SURVIVOR (liquidita' attuale >= ${a.min_liq:.0f})"); md.append(t); allout["survivor"] = o
        allout["survivor_counts"] = cnt
        outname = "results_survivor"
        if a.extra:
            meta = pool_meta([p["address"] for p in pools], os.path.join(DATA, "pool_meta.json"), a.no_fetch)
            t, o = extra_tests(trades, pools, meta, "SURVIVOR"); md.append(t); allout["extra"] = o
    else:
        pf = os.path.join(NP_DIR, "pools.json")
        if a.snapshot or not os.path.exists(pf):
            if a.no_fetch: sys.exit("nessuno snapshot newpools")
            snapshot_newpools(a.n_sample)
            if a.snapshot_only: print("solo snapshot:", NP_DIR); return
        pools = json.load(open(pf))
        ready_at = max(p["created"] for p in pools) + a.min_age_h * 3600
        print(f"pool nel campione: {len(pools)}; tutte pronte (eta' >= {a.min_age_h}h) alle "
              f"{datetime.fromtimestamp(ready_at).strftime('%Y-%m-%d %H:%M')} (ora del box)")
        trades, cnt = load_trades(pools, os.path.join(NP_DIR, "ohlcv"), a.no_fetch, a.min_age_h, stitch=True)
        cnt["stale_trades"] = sum(t["stale"] for t in trades)
        print("conteggi:", cnt); allout["counts"] = cnt
        t, o = report(trades, "NEWPOOLS, nessun filtro di liquidita'"); md.append(t); allout["all"] = o
        t, o = report(trades, f"NEWPOOLS, sensitivita': pool morte durante l'hold = {DEAD_LOSS*100:.0f}% sul residuo", True)
        md.append(t); allout["dead_penalty"] = o
        liq_tr = [x for x, p in zip(trades, [None]*len(trades))]
        addr_liq = {p["address"][:6]: p.get("reserve_at_snap", 0) for p in pools}
        lt = [x for x in trades if addr_liq.get(x["pool"], 0) >= a.min_liq]
        t, o = report(lt, f"NEWPOOLS, solo pool con riserva >= ${a.min_liq:.0f} allo snapshot (eta' pochi minuti, prima dell'ingresso)")
        md.append(t); allout["liq_at_snap"] = o
        if a.extra:
            meta = pool_meta([p["address"] for p in pools], os.path.join(NP_DIR, "pool_meta.json"), a.no_fetch)
            t, o = extra_tests(trades, pools, meta, "NEWPOOLS"); md.append(t); allout["extra"] = o
            mig = migration_check(pools, meta, a.no_fetch)
            moved = {k: v for k, v in mig.items() if v and v[0][0] not in {p["address"] for p in pools} and v[0][1] >= 8000}
            alive = sum(1 for p in pools if meta.get(p["address"], {}).get("reserve_now", 0) >= 8000)
            mt = (f"Migrazioni/sopravvivenza: token controllati {len(mig)}; con pool principale diversa e riserva >= $8k oggi: {len(moved)}; "
                  f"pool del campione con riserva >= $8k oggi: {alive}")
            print(mt); md.append(mt); allout["migration"] = dict(checked=len(mig), moved_8k=len(moved), alive_8k=alive)
        md.append("conteggi: " + json.dumps(cnt))
        outname = "results_newpools" + np_suffix
    json.dump(allout, open(os.path.join(HERE, outname + ".json"), "w"), indent=1, default=str)
    open(os.path.join(HERE, outname + ".md"), "w").write("\n\n".join(md) + "\n")
    print("scritto", outname + ".md/.json")

if __name__ == "__main__":
    main()
