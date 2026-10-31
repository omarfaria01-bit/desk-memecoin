# Analisi (solo lettura) dei trade shadow/reali per la review della relazione di Claude.
import csv, json, re, statistics as S, datetime as D
sh=[r for r in csv.DictReader(open('shadow_trades.csv')) if r['valido']=='1']
tr=list(csv.DictReader(open('trades.csv')))
passes={}
for l in open('events.jsonl'):
    e=json.loads(l)
    if e.get('kind')=='pass':
        m=re.search(r'top 10 al ([\d.]+)%',e['msg'])
        if m: passes.setdefault(e['mint'],[]).append((e['ts'],float(m.group(1))))
def top10(mint,ts):
    c=[v for t,v in passes.get(mint,[]) if t[:16].replace('T',' ')<=ts] or [v for t,v in passes.get(mint,[])]
    return c[-1] if c else None
def imp(f,s): return (1-(f/(f+s))**2)*100
rows=[]
for r in sh:
    p=float(r['prezzo_entrata'] or 0); liq=float(r['liq_entrata'] or 0)
    if p<=0 or liq<=0: continue
    supply=1e9  # pump.fun / launchpad standard (verificato sotto per i non-pump)
    f=(liq/2)/(p*supply)*100
    ef=json.loads(r['entry_features']) if r['entry_features'] else {}
    t10=top10(r['mint'],r['aperto'])
    rows.append(dict(kind='shadow',sym=r['symbol'],mint=r['mint'],t=r['aperto'],pnl=float(r['pnl_pct']),peak=float(r['peak_pct']),
        rug=r['rug']=='1',exit=r['uscita'][:30],tag=r['tag'],f=f,mcap=p*supply,liq=liq,imp1=imp(f,1),imp5=imp(f,5),
        t10=t10,imp_t10=imp(f,t10) if t10 else None,ret5=ef.get('ret_5m'),acc=ef.get('vol_accel'),atr=ef.get('atr_1m_10'),
        pump=r['mint'].endswith('pump')))
json.dump(rows,open('docs/analysis/ck_shadow_rows.json','w'),indent=0)
def st(x):
    x=[v['pnl'] for v in x]
    return f"n={len(x)} medio {S.mean(x):+.1f}% mediana {S.median(x):+.1f}% win {sum(v>0 for v in x)/len(x)*100:.0f}%" if x else "n=0"
print('valid shadow con prezzo/liq:',len(rows),' pump-mint:',sum(r['pump'] for r in rows))
print('TUTTI',st(rows), 'rug',sum(r['rug'] for r in rows))
for lo,hi in ((0,2),(2,4),(4,6),(6,10),(10,100)):
    g=[r for r in rows if lo<=r['f']<hi]
    print(f"pool {lo}-{hi}% supply:",st(g),'rug',sum(r['rug'] for r in g), 'imp1% medio',round(S.mean([r['imp1'] for r in g]),1) if g else '-')
print('--- ret_5m all ingresso (lama che cade)')
for lab,cond in (('ret5<0',lambda r:r['ret5'] is not None and r['ret5']<0),('0-5',lambda r:r['ret5'] is not None and 0<=r['ret5']<5),('5-15',lambda r:r['ret5'] is not None and 5<=r['ret5']<15),('>=15',lambda r:r['ret5'] is not None and r['ret5']>=15),('nd',lambda r:r['ret5'] is None)):
    print(lab,st([r for r in rows if cond(r)]))
print('--- top10 noto:',sum(r['t10'] is not None for r in rows))
for r in sorted(rows,key=lambda r:r['f']):
    print(f"{r['t'][5:]} {r['sym'][:10]:10} f={r['f']:5.2f}% imp1={r['imp1']:4.0f}% t10={r['t10']} impT10={r['imp_t10'] and round(r['imp_t10'])} ret5={r['ret5']} pnl={r['pnl']:+.1f} peak={r['peak']:.0f} rug={int(r['rug'])} {r['exit']}")
