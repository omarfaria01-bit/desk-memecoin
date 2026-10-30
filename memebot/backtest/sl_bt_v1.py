#!/usr/bin/env python3
"""
Backtest dello STOP LOSS di Desk Memecoin (PAPER, solo dati pubblici). Non tocca desk.py ne' i file del bot.

Rispecchia il Risk agent di desk.py (v8, letto in sola lettura):
  - controllo ogni RISK_CHECK_SEC = 15 s -> 4 controlli per candela da 1 min, percorso intrabar
    O,L,H,C (candela verde) oppure O,H,L,C (rossa);
  - ordine delle uscite: liquidita crollata -> crollo (CRASH_EXIT_PCT, immediato) -> stop confermato su N
    controlli di fila -> TP1 30% a +30% (poi stop a pareggio +3%, confermato anche lui) -> TP2 30% a +80%
    -> trailing (start +30%, distanza 10%, 8% dopo +150%) -> time stop (240 min, di fatto spento) -> max hold 180;
  - pnl "mark" netto come mark_pnl_pct: valore di uscita (impatto pool + slippage 2*size/liq + fee 1% + prio fee)
    contro il costo; costo andata+ritorno minimo 3%.
Liquidita nel tempo: NON presente nei dati OHLCV -> proxy pool a prodotto costante: liq_t = liq_entry*sqrt(p_t/p_e)
(un calo di liquidita del 30% equivale a prezzo -51%; i ritiri di LP non sono visibili).

Uso:
  python3 sl_bt.py                      # campioni survivor + trade/shadow del bot (fetch con cache)
  python3 sl_bt.py --sample newpools    # campione newpools (solo cache scritta dal job exit_bt; nessun fetch)
  python3 sl_bt.py --no-fetch           # solo cache
"""
import argparse, csv, hashlib, json, math, os, statistics, sys, time, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__)); BOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import exit_bt as E  # get() con limite 3.2 s e backoff 429, fill_minutes, find_entry

CSV_DIR = os.path.join(E.DATA, "csv")
TZ = ZoneInfo("Europe/Rome")
# --- parametri del bot (desk.py v8 + overrides.json attuali) ---
FEE = 0.01; SLIP_MULT = 2.0; PRIO_USD = 0.001 * 190; MIN_RT = 0.03
TP1, TP1_F, BE, TP2, TP2_F = 30.0, 0.30, 3.0, 80.0, 0.30
TRAIL_START, TRAIL_DIST, TRAIL_TIGHT_AFTER, TRAIL_TIGHT = 30.0, 10.0, 150.0, 8.0
TIME_STOP, TIME_STOP_PCT, MAX_HOLD = 240, 5.0, 180
LIQ_DROP_BOT = 50.0; CRASH_BOT = -15.0
BASE_SIZE = 30.0          # $ per trade (= SHADOW_SIZE_USD; ~3% di 1000$)
REF_SL = 12.0             # perdita in $ allo stop costante = BASE_SIZE * 12%

# ---------------- modello costi (stesse formule di desk.py) ----------------
def slip(usd, liq): return min(0.5, max(0.0, SLIP_MULT * usd / max(liq, 1.0)))
def buy(size, price, liq):
    net = size * (1 - FEE) - PRIO_USD
    imp = net / (liq / 2 + net)
    fill = price * (1 + imp) / (1 - slip(net, liq))
    return net / fill
def exit_val(tokens, price, liq, extra):
    g = tokens * price
    if g <= 0: return 0.0
    imp = g / (max(liq, 1.0) / 2 + g)
    return max(0.0, g * (1 - imp) * (1 - slip(g, liq)) * (1 - FEE) * (1 - extra) - PRIO_USD)

def path(b):
    o, h, l, c = b[1:5]
    return [o, l, h, c] if c >= o else [o, h, l, c]

def simulate(bars, i0, e, liq_e, size, sl, confirm, crash, liq_drop):
    """sl: stop % negativo o None (nessuno stop); ritorna dict con pnl in $ e motivo."""
    tok0 = buy(size, e, liq_e)
    rt = 1 - exit_val(tok0, e, liq_e, 0) / size
    extra = max(0.0, (MIN_RT - rt) / (1 - rt)) if rt < MIN_RT else 0.0   # porta il round trip ad almeno 3%
    entry_mark = size / tok0
    tok = tok0; realized = 0.0; stage = 0; peak = -1e9; hits = 0; reason = "fine_dati"; t_exit = None
    for k, i in enumerate(range(i0, min(len(bars), i0 + MAX_HOLD + 2))):
        pts = path(bars[i]) if k > 0 else [e] + path(bars[i])[1:]
        for j, p in enumerate(pts):
            held = k + j * 0.25
            L = liq_e * math.sqrt(max(p, 1e-18) / e)
            pnl = (exit_val(tok, p, L, extra) / (tok * entry_mark) - 1) * 100
            peak = max(peak, pnl)
            stop_eff = (max(sl if sl is not None else -1e9, BE) if stage >= 1 else sl)
            hits = hits + 1 if (stop_eff is not None and pnl <= stop_eff) else 0
            tdist = min(TRAIL_DIST, TRAIL_TIGHT) if peak >= TRAIL_TIGHT_AFTER else TRAIL_DIST
            sell = None
            if L < liq_e * (1 - liq_drop / 100): sell, reason = 1.0, "liquidita"
            elif crash is not None and pnl <= crash: sell, reason = 1.0, "crollo"
            elif hits >= confirm: sell, reason = 1.0, ("stop" if stage == 0 else "pareggio")
            elif stage < 2 and pnl >= (TP1 if stage == 0 else TP2):
                frac = min(1.0, (TP1_F if stage == 0 else TP2_F) * tok0 / tok); stage += 1
                realized += exit_val(tok * frac, p, L, extra); tok -= tok * frac
                continue
            elif peak >= TRAIL_START and pnl <= peak - tdist: sell, reason = 1.0, "trailing"
            elif stage == 0 and held > TIME_STOP and pnl < TIME_STOP_PCT: sell, reason = 1.0, "tempo"
            elif held > MAX_HOLD: sell, reason = 1.0, "max_hold"
            if sell:
                realized += exit_val(tok, p, L, extra); tok = 0; t_exit = i; break
        if tok <= 0: break
    if tok > 0:
        last = bars[min(len(bars), i0 + MAX_HOLD + 2) - 1]
        p = last[4]; realized += exit_val(tok, p, liq_e * math.sqrt(p / e), extra); t_exit = len(bars) - 1
    # rimbalzo: dopo uno stop in perdita, il prezzo torna sopra l'ingresso entro la finestra di hold?
    rebound = False
    if reason in ("stop", "crollo", "liquidita") and t_exit is not None:
        rebound = any(b[2] > e for b in bars[t_exit + 1: i0 + MAX_HOLD + 1])
    return dict(pnl=realized - size, reason=reason, rebound=rebound, rt=max(rt, MIN_RT))

# ---------------- dati ----------------
def load_ohlcv_sample(kind):
    out = []
    if kind == "survivor":
        pools = json.load(open(E.POOLS_FILE)); odir = E.OHLCV_DIR; liqk = "liq_now"
    else:
        pf = os.path.join(E.NP_DIR, "pools.json"); odir = os.path.join(E.NP_DIR, "ohlcv"); liqk = "reserve_at_snap"
        if not os.path.exists(pf): sys.exit("nessun campione newpools")
        pools = json.load(open(pf))
    for p in pools:
        fn = os.path.join(odir, p["address"] + ".json")
        if not os.path.exists(fn): continue  # mai fetch qui (newpools: lo scarica il job di exit_bt)
        raw = json.load(open(fn))
        sf = os.path.join(odir, "succ_" + p["address"] + ".json")
        if os.path.exists(sf):
            nxt = json.load(open(sf))
            if raw and nxt: raw = [b for b in raw] + [b for b in nxt if b[0] > raw[-1][0]]
        bars = E.fill_minutes(raw)
        if len(bars) < 30: continue
        i0 = E.find_entry(bars)
        if i0 is None: continue
        liq = float(p.get(liqk) or 0)
        if liq <= 0: continue
        out.append(dict(id=p["address"], bars=bars, i0=i0, e=bars[i0][1], liq=liq))
    return out

def fetch_csv_trades(no_fetch):
    """Trade reali (trades.csv) e shadow (shadow_trades.csv) del bot: ingresso e liquidita registrati dal bot."""
    rows = []
    try:
        for r in csv.DictReader(open(os.path.join(BOT, "trades.csv"))):
            t = datetime.strptime(r["chiuso"], "%Y-%m-%d %H:%M").replace(tzinfo=TZ).timestamp() - float(r["minutes"] or 0) * 60
            rows.append(dict(mint=r["mint"], t=t, price=None, liq=float(r["liq_entry"] or 0), src="reale",
                             sig="", soft=r.get("soft_flags", "")))
    except FileNotFoundError: pass
    try:
        for r in csv.DictReader(open(os.path.join(BOT, "shadow_trades.csv"))):
            try:
                t = datetime.strptime(r["aperto"], "%Y-%m-%d %H:%M").replace(tzinfo=TZ).timestamp()
                price = float(r["prezzo_entrata"]); liq = float(r["liq_entrata"])
            except Exception: continue  # riga malformata
            if price <= 0 or liq < 100: continue
            rows.append(dict(mint=r["mint"], t=t, price=price, liq=liq, src="shadow", sig=r.get("signals", ""),
                             soft=r.get("segnali_soft", "")))
    except FileNotFoundError: pass
    out = []
    for r in rows:
        fn = os.path.join(CSV_DIR, r["mint"] + ".json")
        if os.path.exists(fn): raw = json.load(open(fn))
        elif no_fetch: continue
        else:
            raw = []
            try:  # pool principale via DexScreener (gratuito), OHLCV via GeckoTerminal (limite 3.2 s)
                req = urllib.request.Request(f"https://api.dexscreener.com/token-pairs/v1/solana/{r['mint']}",
                                             headers={"User-Agent": "paper-sl-backtest/1.0"})
                pairs = json.load(urllib.request.urlopen(req, timeout=20))
                pairs = sorted(pairs, key=lambda x: -float((x.get("liquidity") or {}).get("usd") or 0))
                if pairs:
                    d = E.get(f"/networks/solana/pools/{pairs[0]['pairAddress']}/ohlcv/minute",
                              {"aggregate": 1, "limit": 1000, "currency": "usd", "token": "base"})
                    raw = [[int(x[0])] + [float(v) for v in x[1:6]] for x in
                           ((d or {}).get("data", {}).get("attributes", {}).get("ohlcv_list", []))]
                    raw.sort()
            except Exception as ex:
                print("  fetch fallito:", type(ex).__name__)
            if raw: json.dump(raw, open(fn, "w"))  # niente cache se il fetch fallisce (429): si riprova al prossimo giro
        bars = E.fill_minutes(raw)
        idx = [k for k, b in enumerate(bars) if b[0] >= r["t"] - 59]
        if not bars or not idx or idx[0] < 10: continue
        i0 = idx[0]; e = r["price"] or bars[i0][1]
        out.append(dict(id=r["mint"], bars=bars, i0=i0, e=e, liq=r["liq"] or 10000, src=r["src"], sig=r["sig"]))
    return out

# ---------------- configurazioni ----------------
SLS = [-8, -12, -15, -20, -25, -30, None]
def crash_for(sl):  # per stop oltre -15 il CRASH_EXIT_PCT del codice va spostato, altrimenti scatta lui a -15
    return CRASH_BOT if sl is None or sl > CRASH_BOT else round(1.5 * sl, 1)

def run_cfg(trades, sl_of, confirm, scaled, noSL=False):
    res = []
    for t in sorted(trades, key=lambda x: x["bars"][x["i0"]][0]):
        sl = sl_of(t)
        size = BASE_SIZE * (REF_SL / abs(sl if sl is not None else -51.0)) if scaled else BASE_SIZE
        if noSL:
            r = simulate(t["bars"], t["i0"], t["e"], t["liq"], size, None, 99, None, 30.0)
        else:
            r = simulate(t["bars"], t["i0"], t["e"], t["liq"], size, sl, confirm, crash_for(sl), LIQ_DROP_BOT)
        r["ret"] = r["pnl"] / BASE_SIZE  # in unita' della size base (R comparabile tra size diverse)
        res.append(r)
    return res

def stats(res):
    n = len(res)
    if not n: return dict(n=0)
    r = [x["ret"] for x in res]; eq = pk = mdd = 0.0
    for v in r: eq += v; pk = max(pk, eq); mdd = min(mdd, eq - pk)
    c = cp = 1.0; cdd = 0.0
    for v in r: c *= 1 + 0.10 * v; cp = max(cp, c); cdd = min(cdd, c / cp - 1)
    stops = [x for x in res if x["reason"] in ("stop", "crollo", "liquidita")]
    return dict(n=n, win=sum(v > 0 for v in r) / n, exp=sum(r) / n, med=statistics.median(r), sum=sum(r),
                mdd=mdd, comp=c - 1, cdd=cdd, stops=len(stops), rebound=sum(x["rebound"] for x in stops))

def half(t): return int(hashlib.md5(t["id"].encode()).hexdigest(), 16) % 2

# ---------------- stop dinamico ----------------
def features(t):
    b = t["bars"]; i = t["i0"]; pre = b[max(0, i - 10):i]
    atr = statistics.mean((x[2] - x[3]) / x[4] for x in pre if x[4] > 0) * 100 if pre else 5.0
    ret5 = (b[i - 1][4] / b[i - 6][4] - 1) * 100 if i >= 6 and b[i - 6][4] > 0 else 0.0
    return atr, ret5

def dyn_stop(k, w, med_ret5):
    def f(t):
        atr, r5 = features(t)
        s = k * atr + (w if r5 > med_ret5 else -w)   # momentum forte: un po' piu' largo, debole: piu' stretto
        return -min(30.0, max(8.0, s))
    return f

def fmt(name, s):
    if not s.get("n"): return f"| {name} | 0 | | | | | | | | |"
    return (f"| {name} | {s['n']} | {s['win']*100:.0f}% | {s['exp']*100:+.2f}% | {s['med']*100:+.2f}% | {s['sum']*100:+.0f}% | "
            f"{s['mdd']*100:.0f}% | {s['comp']*100:+.1f}% | {s['cdd']*100:.1f}% | {s['stops']} / {s['rebound']} |")

HDR = ["| Config | Trade | Win | Expectancy/trade | Mediana | Somma | MaxDD (somma) | Compost. 10% | DD compost. | Stop scattati / poi rimbalzati sopra ingresso |",
       "|---|---|---|---|---|---|---|---|---|---|"]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", choices=["survivor", "newpools"], default="survivor")
    ap.add_argument("--no-fetch", action="store_true")
    a = ap.parse_args()
    os.makedirs(CSV_DIR, exist_ok=True)
    trades = load_ohlcv_sample(a.sample)
    bot = fetch_csv_trades(a.no_fetch)
    print(f"campione {a.sample}: {len(trades)} ingressi; trade del bot con OHLCV: {len(bot)} "
          f"(con 'signals': {sum(1 for t in bot if t.get('sig'))})")
    for t in trades: t["half"] = half(t)
    ins = [t for t in trades if t["half"] == 0]; oos = [t for t in trades if t["half"] == 1]
    md = [f"# Backtest stop loss ({a.sample}) - {datetime.now(TZ).strftime('%Y-%m-%d %H:%M')} Europe/Rome",
          f"ingressi: {len(trades)} (in-sample {len(ins)}, out-of-sample {len(oos)}); size base ${BASE_SIZE:.0f}; "
          "rendimenti in % della size base", ""] + HDR
    out = {}
    cfgs = []
    for sl in SLS:
        for scaled in (False, True):
            if sl is None:
                cfgs.append((f"noSL (liq-30% proxy) {'size scalata' if scaled else 'size fissa'}", (lambda t: None), 1, scaled, True))
                continue
            for conf in (1, 2, 3):
                cfgs.append((f"SL {sl} conf{conf} {'size scalata' if scaled else 'size fissa'}", (lambda t, s=sl: s), conf, scaled, False))
    # stop dinamico: parametri scelti SOLO sulla meta' in-sample (expectancy), size scalata, conferma 2
    med_r5 = statistics.median([features(t)[1] for t in ins]) if ins else 0.0
    best = None
    for k in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0):
        for w in (0.0, 3.0):
            s = stats(run_cfg(ins, dyn_stop(k, w, med_r5), 2, True))
            if s.get("n") and (best is None or s["exp"] > best[0]): best = (s["exp"], k, w)
    if best:
        cfgs.append((f"DINAMICO k={best[1]}xATR10 {'+-'+str(best[2]) if best[2] else ''} [8..30] conf2 size scalata",
                     dyn_stop(best[1], best[2], med_r5), 2, True, False))
        sls = [dyn_stop(best[1], best[2], med_r5)(t) for t in trades]
        out["dynamic_params"] = dict(k=best[1], w=best[2], med_ret5=med_r5,
                                     stop_median=statistics.median(sls), stop_min=min(sls), stop_max=max(sls))
    rows_all, rows_split = [], ["", "## Split per hash della pool: in-sample | out-of-sample", "",
                                "| Config | IS n | IS exp | IS win | OOS n | OOS exp | OOS win | OOS MaxDD | OOS stop/rimbalzi |",
                                "|---|---|---|---|---|---|---|---|---|"]
    for name, f, conf, scaled, nosl in cfgs:
        s = stats(run_cfg(trades, f, conf, scaled, nosl)); si = stats(run_cfg(ins, f, conf, scaled, nosl))
        so = stats(run_cfg(oos, f, conf, scaled, nosl))
        out[name] = dict(all=s, ins=si, oos=so)
        rows_all.append(fmt(name, s))
        if so.get("n") and si.get("n"):
            rows_split.append(f"| {name} | {si['n']} | {si['exp']*100:+.2f}% | {si['win']*100:.0f}% | {so['n']} | "
                              f"{so['exp']*100:+.2f}% | {so['win']*100:.0f}% | {so['mdd']*100:.0f}% | {so['stops']}/{so['rebound']} |")
    md += rows_all + rows_split
    if bot:
        md += ["", f"## Trade reali/shadow del bot ({len(bot)}, solo controllo di coerenza: troppo pochi)", ""] + HDR
        for name, f, conf, scaled, nosl in cfgs:
            if "size fissa" in name and ("conf2" in name or "noSL" in name):
                s = stats(run_cfg(bot, f, conf, scaled, nosl)); out["bot/" + name] = s; md.append(fmt(name, s))
    md += ["", "Note: liquidita' nel tempo = proxy a prodotto costante (liq*sqrt(p/p0)); per SL oltre -15 il crollo immediato "
           "e' spostato a 1.5xSL (nel codice CRASH_EXIT_PCT=-15 scatterebbe prima); survivor: liquidita' d'ingresso = "
           "liquidita' attuale (look-ahead)."]
    txt = "\n".join(md); print(txt)
    suf = "" if a.sample == "survivor" else "_newpools"
    open(os.path.join(HERE, f"results_sl{suf}.md"), "w").write(txt + "\n")
    json.dump(out, open(os.path.join(HERE, f"results_sl{suf}.json"), "w"), indent=1, default=str)

if __name__ == "__main__":
    main()
