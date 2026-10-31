# Review relazione Claude: test (b) chiusura vs ombra, (c) posizione nel range / lama che cade,
# (d) regime SOL/BTC, (h) piani d'uscita. Solo dati locali + 2 serie gratuite (SOL 15m GeckoTerminal, BTC 1h CoinGecko).
import json, os, sys, glob, csv, statistics as S, bisect
sys.path.insert(0, 'backtest'); sys.argv = ['x']
import exit_bt as E
A = 'docs/analysis'
pools = json.load(open(E.POOLS_FILE))
surv, _ = E.load_trades(pools, E.OHLCV_DIR, True, 0)
npl = json.load(open(os.path.join(E.NP_DIR, 'pools.json')))
newp, _ = E.load_trades(npl, os.path.join(E.NP_DIR, 'ohlcv'), True, 0)
COST = 0.03
# regole del bot oggi (desk.py CFG): SL -12 (stop_applied), crollo -15, TP1 +30 30% -> BE +3, TP2 +80 30%,
# trailing da +30 al 15% (8% dopo +150) [overrides.json], time stop 240 min (> hold, quindi spento), hold 180
BOT = dict(sl=-.12, crash=-.15, tps=[(.30, .30), (.80, .30)], be=.03, act=.30, dist=.15, tight=(1.5, .08),
           ts=None, hold=180, moon=0.0, moon_trail=None, liq_exit=.5)
def V(**k): d = dict(BOT); d.update(k); return d
PLANS = {
  'attuale': BOT,
  'capitale a 3x (TP3 +200 vende 33%)': V(tps=[(.30, .30), (.80, .30), (2.0, .33)]),
  'capitale a 5x (TP3 +400 vende 33%)': V(tps=[(.30, .30), (.80, .30), (4.0, .33)]),
  'moonbag 15% senza trailing': V(moon=.15),
  'moonbag 15% trailing 40%': V(moon=.15, moon_trail=.40),
  'solo capitale a 3x + moonbag (TP1 +200 vende 50%, trailing 25%)': V(tps=[(2.0, .5)], act=.40, dist=.25),
}
def sim(bars, i0, R, liq=None, cost=None):
    """bars [t,o,h,l,c,(vol)]; ordine conservativo: stop prima dei TP. Ritorna rendimento netto."""
    tick = cost is not None
    e = bars[i0][1] * (1 + cost) if tick else bars[i0][1]; rem = 1.0; got = 0.0; peak = e; stop = e * (1 + R['sl']); tps = [list(x) for x in R['tps']]
    trail = False; tp_done = 0; moon = R['moon']; mpeak = e; mrem = 0.0; l0 = liq[i0] if liq else None
    for k in range(i0, len(bars)):
        o, h, l, c = bars[k][1:5]; m = k - i0
        if liq and l0 and liq[k] is not None and liq[k] < l0 * (1 - R['liq_exit']):
            hc = min(1.0, liq[k] / l0) if tick else 1.0   # pool svuotata: si vende solo cio che resta
            got += (rem + mrem) * c * hc / e; rem = mrem = 0; break
        if mrem > 0:   # moonbag: solo trailing proprio o fine hold
            mpeak = max(mpeak, h)
            if R['moon_trail'] and l <= mpeak * (1 - R['moon_trail']):
                got += mrem * min(o, mpeak * (1 - R['moon_trail'])) / e; mrem = 0
        if rem > 0:
            if trail:
                dist = R['tight'][1] if peak >= e * (1 + R['tight'][0]) else R['dist']
                stop = max(stop, peak * (1 - dist))
            crash = e * (1 + R['crash']) if tp_done == 0 else 0
            if l <= max(stop, crash if R['sl'] > R['crash'] else 0):
                px = min(o, max(stop, crash)) if m > 0 else max(stop, crash)
                px = min(px, o)
                if tick: px = l
                sell = rem - (moon if (trail and moon) else 0)
                got += sell * px / e; mrem += rem - sell; rem = 0
            else:
                for tp in tps:
                    if tp[1] > 0 and h >= e * (1 + tp[0]):
                        s = min(rem, tp[1]); got += s * (h if tick else max(o, e * (1 + tp[0]))) / e; rem -= s; tp[1] = 0; tp_done += 1
                        if tp_done == 1: stop = max(stop, e * (1 + R['be']))
                peak = max(peak, h)
                if not trail and peak >= e * (1 + R['act']): trail = True; mpeak = peak
                if trail and rem > 0:
                    dist = R['tight'][1] if peak >= e * (1 + R['tight'][0]) else R['dist']
                    if c < peak * (1 - dist):
                        sell = rem - min(rem, moon); got += sell * c / e; mrem += rem - sell; rem = 0
                if rem > 0 and tp_done == 0 and R['ts'] and m + 1 == R['ts'][0] and c < e * (1 + R['ts'][1]):
                    got += rem * c / e; rem = 0
        if rem <= 1e-9 and mrem <= 1e-9: break
        if m + 1 >= R['hold']:
            got += (rem + mrem) * c / e; rem = mrem = 0; break
    if rem + mrem > 0: got += (rem + mrem) * bars[-1][4] / e
    return got - 1 - (0 if tick else COST)
def st(r):
    if not r: return 'n=0'
    eq = pk = mdd = 0
    for x in r: eq += x; pk = max(pk, eq); mdd = min(mdd, eq - pk)
    return (f"n={len(r)} win {sum(x>0 for x in r)/len(r)*100:.0f}% medio {S.mean(r)*100:+.1f}% mediana {S.median(r)*100:+.1f}% "
            f"somma {sum(r)*100:+.0f}% maxDD {mdd*100:.0f}%")
out = []
P = lambda *a: (print(*a), out.append(' '.join(str(x) for x in a)))
# ---------- tick path dei trade shadow/reali del bot ----------
sh = [r for r in csv.DictReader(open('shadow_trades.csv')) if r['valido'] == '1']
paths = []
for r in sh:
    fn = f"price_paths/{r['mint']}.jsonl"
    if not os.path.exists(fn): continue
    L = sorted({x['t']: x for x in map(json.loads, open(fn)) if x.get('p')}.values(), key=lambda x: x['t'])
    ents = [x for x in L if x.get('ev') == 'entry' and abs(x['p'] / float(r['prezzo_entrata']) - 1) < 1e-6]
    if not ents: continue
    t0 = ents[0]['t']; seg = [x for x in L if t0 <= x['t'] <= t0 + 200 * 60]
    # tick -> barre da 1 minuto
    bars, liq = [], []
    for x in seg:
        mi = int((x['t'] - t0) // 60)
        if bars and bars[-1][0] == mi:
            b = bars[-1]; b[2] = max(b[2], x['p']); b[3] = min(b[3], x['p']); b[4] = x['p']
            if not x.get('ev'): liq[-1] = x.get('l')
        else:
            o = bars[-1][4] if bars else x['p']
            bars.append([mi, o, max(o, x['p']), min(o, x['p']), x['p']]); liq.append(None if x.get('ev') else x.get('l'))
    bars[0][1] = bars[0][2] = bars[0][3] = float(r['prezzo_entrata'])
    paths.append(dict(sym=r['symbol'], bars=bars, liq=liq, mins=bars[-1][0], rug=r['rug'] == '1', bot_pnl=float(r['pnl_pct']), cost=float(r['rt_cost_pct'] or 3) / 100))
P(f"\n## Piani d'uscita sui percorsi reali registrati dal bot ({len(paths)} trade shadow validi con price_path)")
for n, R in PLANS.items():
    P(f"- {n}: {st([sim(p['bars'], 0, R, p['liq'], p['cost']) for p in paths])}")
P(f"\n## Piani d'uscita sul backtest survivor GeckoTerminal ({len(surv)} trade) e newpools ({len(newp)})")
for n, R in PLANS.items():
    P(f"- {n}: survivor {st([sim(t['bars'], t['i0'], R) for t in surv])} | newpools {st([sim(t['bars'], t['i0'], R) for t in newp])}")
mx = []
for t in surv:
    e = t['bars'][t['i0']][1]; mx.append(max(b[2] for b in t['bars'][t['i0']:t['i0'] + 180]) / e - 1)
for p in paths: mx.append(max(b[2] for b in p['bars'][:180]) / p['bars'][0][1] - 1)
P(f"- picco entro 180 min (survivor+bot, n={len(mx)}): >= +100% {sum(x>=1 for x in mx)}, >= +200% (3x) {sum(x>=2 for x in mx)}, >= +400% (5x) {sum(x>=4 for x in mx)}")
# ---------- (b) chiusura vs ombra ----------
def entries(bars, mode):
    for t in range(9, len(bars) - 1):
        v5 = sum(b[5] for b in bars[t-4:t+1]); vp = sum(b[5] for b in bars[t-9:t-4])
        if v5 < E.MIN_VOL5 or v5 < E.ACC_FACTOR * max(vp, 1e-9): continue
        vwap = sum((b[2] + b[3] + b[4]) / 3 * b[5] for b in bars[t-4:t+1]) / v5
        o, h, l, c = bars[t][1:5]; rng = h - l
        if mode == 'ombra' and h > vwap: return t + 1                       # basta toccare (tick/ombra)
        if mode == 'chiusura' and c > vwap: return t + 1                    # regola attuale del backtest
        if mode == 'chiusura_forte' and c > vwap and (rng <= 0 or (h - c) / rng <= 0.5) and c >= o: return t + 1
        if mode == 'chiusura+conferma' and c > vwap and t + 1 < len(bars) - 1 and bars[t+1][4] > c: return t + 2
    return None
P("\n## (b) Conferma su candela chiusa vs ombra (survivor+newpools, regole d'uscita attuali)")
allb = [(t['bars']) for t in surv + newp]
for mode in ('ombra', 'chiusura', 'chiusura_forte', 'chiusura+conferma'):
    r = []
    for b in allb:
        i = entries(b, mode)
        if i is not None and len(b) - i >= 30: r.append(sim(b, i, BOT))
    P(f"- {mode}: {st(r)}")
# ---------- (c) posizione nel range / lama che cade ----------
P("\n## (c) Posizione del prezzo nel range delle ultime 30 barre all'ingresso (survivor+newpools, uscite attuali)")
rows = []
for t in surv + newp:
    b, i = t['bars'], t['i0']; w = b[max(0, i-30):i]
    hi = max(x[2] for x in w); lo = min(x[3] for x in w); e = b[i][1]
    pos = (e - lo) / (hi - lo) if hi > lo else .5
    r15 = e / b[max(0, i-15)][4] - 1
    rows.append((pos, r15, sim(b, i, BOT)))
rows5 = [(t['bars'][t['i0']][1] / t['bars'][max(0, t['i0'] - 5)][4] - 1, 0, sim(t['bars'], t['i0'], BOT)) for t in surv + newp]
for lab, f in (('basso (<0.33)', lambda p: p < .33), ('meta (0.33-0.66)', lambda p: .33 <= p < .66), ('alto (>=0.66)', lambda p: p >= .66)):
    P(f"- {lab}: {st([x[2] for x in rows if f(x[0])])}")
P(f"- ritorno 5 min all'ingresso < 0: {st([x[2] for x in rows5 if x[0] < 0])} | >= 0: {st([x[2] for x in rows5 if x[0] >= 0])}")
P(f"- ultimi 15 min < -10% (lama): {st([x[2] for x in rows if x[1] < -.10])} | altri {st([x[2] for x in rows if x[1] >= -.10])}")
# ---------- (d) regime SOL / BTC ----------
sol = json.load(open(f'{A}/sol_15m.json'))['data']['attributes']['ohlcv_list']; sol.sort()
st_ = [x[0] for x in sol]
btc = json.load(open(f'{A}/btc.json'))['prices']; bt_ = [x[0] / 1000 for x in btc]
def solret(ts, h):
    i = bisect.bisect_right(st_, ts) - 1; j = bisect.bisect_right(st_, ts - h * 3600) - 1
    return sol[i][4] / sol[j][4] - 1 if i >= 0 and j >= 0 else None
def btcret(ts, h):
    i = bisect.bisect_right(bt_, ts) - 1; j = bisect.bisect_right(bt_, ts - h * 3600) - 1
    return btc[i][1] / btc[j][1] - 1 if i >= 0 and j >= 0 else None
P("\n## (d) Regime di mercato all'ingresso (survivor+newpools; uscite attuali)")
reg = []
for t in surv + newp:
    ts = t['t_entry']; r = sim(t['bars'], t['i0'], BOT)
    reg.append((solret(ts, 4), btcret(ts, 4), solret(ts, 1), r, ts))
for lab, f in (('SOL 4h <= -2%', lambda x: x[0] is not None and x[0] <= -.02), ('SOL 4h -2..+2%', lambda x: x[0] is not None and -.02 < x[0] < .02),
               ('SOL 4h >= +2%', lambda x: x[0] is not None and x[0] >= .02), ('BTC 4h <= -1.5%', lambda x: x[1] is not None and x[1] <= -.015),
               ('BTC 4h > -1.5%', lambda x: x[1] is not None and x[1] > -.015)):
    P(f"- {lab}: {st([x[3] for x in reg if f(x)])}")
import datetime as D
hack0 = D.datetime(2026, 10, 29, 19, 0).timestamp(); hack1 = D.datetime(2026, 10, 30, 13, 0).timestamp()
P(f"- ingressi nella finestra del panico (29/10 19:00 - 30/10 13:00 Roma, BTC da ~80.8k a ~77.3k): {st([x[3] for x in reg if hack0 <= x[4] < hack1])}")
P(f"- fuori finestra: {st([x[3] for x in reg if not (hack0 <= x[4] < hack1)])}")
# trade del bot: token in calo mentre SOL sale?
P("\n## (d) Trade shadow del bot: token in calo a 5 min mentre SOL sale")
shr = json.load(open(f'{A}/ck_shadow_rows.json'))
import time
def tsr(s): return D.datetime.strptime(s, '%Y-%m-%d %H:%M').timestamp()
g1 = [r['pnl'] for r in shr if r['ret5'] is not None and r['ret5'] < 0 and (solret(tsr(r['t']), 1) or 0) > 0]
g2 = [r['pnl'] for r in shr if r['ret5'] is not None and r['ret5'] < 0 and (solret(tsr(r['t']), 1) or 0) <= 0]
P(f"- token giu e SOL 1h su: n={len(g1)} medio {S.mean(g1):+.1f}% | token giu e SOL 1h giu/piatto: n={len(g2)} medio {S.mean(g2) if g2 else 0:+.1f}%")
sr = [solret(tsr(r['t']), 4) for r in shr]; sr = [x for x in sr if x is not None]
P(f"- SOL 4h durante i trade del bot (30-31/10): min {min(sr)*100:+.1f}%, max {max(sr)*100:+.1f}%")
open(f'{A}/ck_backtest_out.md', 'w').write('\n'.join(out) + '\n')
# validazione simulatore: confronto con il pnl registrato dal bot
d = [(p['sym'], round(p['bot_pnl'], 1), round(sim(p['bars'], 0, BOT, p['liq'], p['cost']) * 100, 1)) for p in paths]
print('VALID', S.mean([x[1] for x in d]), S.mean([x[2] for x in d]))
print([x for x in d if abs(x[1] - x[2]) > 8])
for n, R in PLANS.items():
    print(n, [(p['sym'], round(sim(p['bars'], 0, R, p['liq'], p['cost'])*100)) for p in paths if max(b[2] for b in p['bars']) / p['bars'][0][1] > 1.8])
