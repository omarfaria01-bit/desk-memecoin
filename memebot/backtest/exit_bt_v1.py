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
OHLCV_DIR = os.path.join(DATA, "ohlcv")
POOLS_FILE = os.path.join(DATA, "pools.json")
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

def fetch_ohlcv(p):
    fn = os.path.join(OHLCV_DIR, p["address"] + ".json")
    if os.path.exists(fn):
        return json.load(open(fn))
    rows = {}; before = None
    for _ in range(4):  # max 4000 minuti (~66h)
        params = {"aggregate": 1, "limit": 1000, "currency": "usd", "token": "base"}
        if before: params["before_timestamp"] = before
        d = get(f"/networks/solana/pools/{p['address']}/ohlcv/minute", params)
        lst = (d or {}).get("data", {}).get("attributes", {}).get("ohlcv_list", []) if d else []
        if not lst: break
        for r in lst: rows[int(r[0])] = [float(v) for v in r[1:6]]
        oldest = min(int(r[0]) for r in lst)
        if oldest <= p["created"] + 60 or len(lst) < 1000: break
        before = oldest
    out = [[t] + rows[t] for t in sorted(rows)]
    json.dump(out, open(fn, "w"))
    return out

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
    if rem > 0: got += rem * bars[last][4] / e
    return got - 1 - COST_RT, last - i0 + 1, reason

RULES = {
    "A_vecchio": dict(sl=-0.08, tps=[(1.00, 0.5, None)], trail_act=0.15, trail_dist=0.10, max_hold=360),
    "B_nuovo": dict(sl=-0.10, tps=[(0.30, 0.3, 0.03), (0.80, 0.3, None)], trail_act=0.40, trail_dist=0.15,
                    trail_tight=[(1.50, 0.08)], time_stop=(30, 0.05), max_hold=180),
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
    return dict(n=n, win=sum(r > 0 for r in rets) / n, avg=sum(rets) / n, sum=sum(rets), mdd_sum=mdd,
                comp10=comp - 1, mdd_comp10=cmdd)

def run_set(trades, R):
    res = [(t["t_entry"], *simulate(t["bars"], t["i0"], R)) for t in trades]
    res.sort(key=lambda x: x[0])
    return [r[1] for r in res], res

def fmt(name, s):
    if not s.get("n"): return f"| {name} | 0 | - | - | - | - | - | - |"
    return (f"| {name} | {s['n']} | {s['win']*100:.0f}% | {s['avg']*100:+.1f}% | {s['sum']*100:+.0f}% | "
            f"{s['mdd_sum']*100:.0f}% | {s['comp10']*100:+.1f}% | {s['mdd_comp10']*100:.1f}% |")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true"); ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--min-pools", type=int, default=40); ap.add_argument("--max-pools", type=int, default=60)
    ap.add_argument("--min-liq", type=float, default=8000); ap.add_argument("--max-age-h", type=float, default=48)
    ap.add_argument("--min-age-h", type=float, default=4)
    a = ap.parse_args()
    os.makedirs(OHLCV_DIR, exist_ok=True)
    if os.path.exists(POOLS_FILE) and not a.refresh:
        pools = json.load(open(POOLS_FILE))
    else:
        if a.no_fetch: sys.exit("nessuna cache pool")
        print("Raccolta pool..."); pools = collect_pools(a); json.dump(pools, open(POOLS_FILE, "w"), indent=1)
    print(f"Pool: {len(pools)}")
    if len(pools) < a.min_pools: print(f"ATTENZIONE: meno di {a.min_pools} pool idonee")
    trades = []; no_sig = 0
    for n, p in enumerate(pools):
        fn = os.path.join(OHLCV_DIR, p["address"] + ".json")
        if a.no_fetch and not os.path.exists(fn): continue
        if not os.path.exists(fn): print(f"  OHLCV {n+1}/{len(pools)} {p['address'][:6]}..")
        bars = fill_minutes(fetch_ohlcv(p))
        if len(bars) < 30: no_sig += 1; continue
        i0 = find_entry(bars)
        if i0 is None: no_sig += 1; continue
        h = int(hashlib.md5(p["address"].encode()).hexdigest(), 16) % 2
        trades.append(dict(pool=p["address"][:6], t_entry=bars[i0][0], i0=i0, bars=bars, half=h,
                           min_after=len(bars) - i0))
    print(f"Trade: {len(trades)}  (pool senza segnale/dati: {no_sig})")
    train = [t for t in trades if t["half"] == 0]; test = [t for t in trades if t["half"] == 1]

    # scelta di C SOLO sulla meta' "train" (split per hash dell'indirizzo), valutata sulla meta' "test"
    best = None
    for combo in itertools.product(*C_GRID.values()):
        R = make_C(*combo); r, _ = run_set(train, R); s = stats(r)
        score = s["avg"] if s.get("n") else -9
        if best is None or score > best[0]: best = (score, combo)
    RULES["C_variante"] = make_C(*best[1])
    # robustezza: quante combo battono B su entrambe le meta'
    print("C scelto (train):", dict(zip(C_GRID.keys(), best[1])))

    lines = ["| Set | Trade | Win | Medio/trade | Totale (somma) | MaxDD (somma) | Compost. 10%/trade | MaxDD compost. |",
             "|---|---|---|---|---|---|---|---|"]
    out = {}
    for name, R in RULES.items():
        for lab, sub in (("tutti", trades), ("train", train), ("test", test)):
            r, res = run_set(sub, R); s = stats(r); out[f"{name}/{lab}"] = s
            lines.append(fmt(f"{name} [{lab}]", s))
            if lab == "tutti":
                reasons = {}
                for x in res: reasons[x[3]] = reasons.get(x[3], 0) + 1
                out[f"{name}/uscite"] = reasons
    print("\n".join(lines))
    for name in RULES: print(name, "motivi uscita:", out[f"{name}/uscite"])
    hold = sorted(t["min_after"] for t in trades)
    print("minuti di dati dopo ingresso (mediana):", hold[len(hold)//2] if hold else None)
    json.dump(dict(results=out, C=dict(zip(C_GRID.keys(), best[1])), n_pools=len(pools), n_trades=len(trades)),
              open(os.path.join(HERE, "results.json"), "w"), indent=1, default=str)
    open(os.path.join(HERE, "results.md"), "w").write("\n".join(lines) + "\n")

if __name__ == "__main__":
    main()
