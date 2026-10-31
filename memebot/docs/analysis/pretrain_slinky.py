#!/usr/bin/env python3
"""v8.7 (F) - Pre-training OFFLINE del prior sulle 12 feature di build_features usando il corpus pubblico
Slinky21/Pumpfun_Memecoin_Corpus (HuggingFace, CC BY 4.0, 5 giu - 14 lug 2026). Gira sul box, MAI sul server.

Uso:  python pretrain_slinky.py <dir_con_tokens.parquet_e_snapshots.parquet> <out model_prior.json>
Requisiti: duckdb, pandas, numpy, scikit-learn, pytz.

Ricostruzione (approssimata, vedi LIMITS):
- punto di decisione T = candela 1m a eta ~20 min (fase 1 = bucket da 60 s per i primi 30 min), solo token con
  mcap >= 60 SOL a T,
  ancora sulla bonding curve (non graduati prima di T), >= 30 scambi nella fase 1, top10 non 'suspect';
- candele 1m = bucket non vuoti della fase 1 (come GeckoTerminal salta i minuti senza scambi);
  analyze_chart identica al bot; servono >= 10 candele (il bot ne vuole 30, qui ce ne sono al massimo 20),
  altrimenti segnali di grafico neutri 0.5 come NO_CHART_NEUTRAL;
- bsr = acquisti/vendite della fase 1 (proxy di buys_h1/sells_h1); liquidity = 2 x SOL reali nella curva x SOL_USD;
  holders ~ somma dei wallet unici per bucket (sovrastima); concentration = initial_top10_pct_corrected;
- social / trending / narrative NON presenti nel corpus -> 0 (il loro peso resta ~0);
- etichetta y come learn_from_trade (pnl>0): percorso dei bucket 1m dopo T per 10 min (la fase 2 da 5 min ha
  quasi zero scambi registrati dopo il 25 giu, inutilizzabile): low <= -10% prima di high >= +30% -> 0 (prudente),
  high >= +30% -> 1, graduazione entro la finestra -> 1, altrimenti ultimo close >= +2% (commissioni) -> 1.
Split temporale: train 20 giu <= T < 3 lug (prima del 20 giu etichette corrotte), test T >= 2026-07-04 (dopo la rottura di regime del 3-4 lug).
"""
import json
import math
import sys
import time

import duckdb
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

FEATURES = ["trend", "macd", "rsi", "bsr", "volsurge", "higher_lows", "social",
            "trending", "narrative", "holders", "concentration", "liquidity"]
RULE_W = {"trend": .15, "macd": .10, "rsi": .10, "bsr": .12, "volsurge": .08, "higher_lows": .07,
          "social": .08, "trending": .07, "narrative": .05, "holders": .06, "concentration": .07,
          "liquidity": .05}
RSI_MIN, RSI_MAX, MAX_TOP10 = 45.0, 80.0, 35.0
SOL_USD = 76.0          # mediana entry_price_usd*1e9/initial_market_cap_sol nel corpus
MIN_MCAP_SOL = 60.0
MIN_CANDLES = 10
T_MIN = 20
H_MIN = 10
TRAIN_START = pd.Timestamp("2026-06-20", tz="UTC")  # prima: graduated_at e scambi fase 1 quasi assenti
TRAIN_END = pd.Timestamp("2026-07-03", tz="UTC")
TEST_START = pd.Timestamp("2026-07-04", tz="UTC")
SYSTEM_WALLET = "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s"  # solo nei trades (qui non usati)


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def ema_series(vals, n):
    k = 2 / (n + 1)
    out, e = [], None
    for v in vals:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def rsi(vals, n=14):
    if len(vals) < n + 1:
        return None
    gains = sum(max(vals[i] - vals[i - 1], 0) for i in range(1, n + 1))
    losses = sum(max(vals[i - 1] - vals[i], 0) for i in range(1, n + 1))
    ag, al = gains / n, losses / n
    for i in range(n + 1, len(vals)):
        d = vals[i] - vals[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def analyze_chart(c):   # stessa logica di desk.analyze_chart
    closes, vols, lows = [x[4] for x in c], [x[5] for x in c], [x[3] for x in c]
    e9, e21 = ema_series(closes, 9), ema_series(closes, 21)
    macd = [a - b for a, b in zip(ema_series(closes, 12), ema_series(closes, 26))]
    hist = [m - s for m, s in zip(macd, ema_series(macd, 9))]
    v5 = sum(vols[-5:]) / 5
    v20 = sum(vols[-25:-5]) / 20
    return {"close": closes[-1], "ema9": e9[-1], "ema21": e21[-1], "rsi": rsi(closes, 14),
            "macd_hist": hist[-1], "macd_rising": hist[-1] > hist[-2],
            "vol_ratio": v5 / v20 if v20 > 0 else 0.0,
            "higher_lows": min(lows[-5:]) > min(lows[-10:-5])}


def features(ph1, top10):
    ne = ph1[ph1.trade_count > 0]
    candles = list(zip(ne.ts, ne.price_open, ne.price_high, ne.price_low, ne.price_close,
                       ne.buy_volume_sol + ne.sell_volume_sol))
    ch = analyze_chart(candles) if len(candles) >= MIN_CANDLES else None
    buys, sells = ph1.buy_count.sum(), ph1.sell_count.sum()
    last = ph1.iloc[-1]
    liq = 2 * max(last.v_sol_eob / 1e9 - 30.0, 0.0) * SOL_USD
    holders = max(int(ne.unique_wallets.sum()), 1)
    f = {
        "trend": 1.0 if ch and ch["ema9"] > ch["ema21"] and ch["close"] >= ch["ema9"] else 0.0,
        "macd": ((1.0 if ch["macd_rising"] else 0.5) if ch["macd_hist"] > 0 else 0.0) if ch else 0.0,
        "rsi": 1.0 if ch and ch["rsi"] is not None and RSI_MIN <= ch["rsi"] <= RSI_MAX else 0.0,
        "bsr": clamp(buys / max(1, sells) - 1.0),
        "volsurge": clamp((ch["vol_ratio"] - 1) / 2) if ch else 0.0,
        "higher_lows": 1.0 if ch and ch["higher_lows"] else 0.0,
        "social": 0.0, "trending": 0.0, "narrative": 0.0,
        "holders": clamp(math.log10(holders) / 4),
        "concentration": clamp(1 - (top10 if top10 == top10 else 25.0) / MAX_TOP10),
        "liquidity": clamp((math.log10(max(liq, 1)) - 4) / 1.5),
    }
    if ch is None:
        for k in ("trend", "macd", "rsi", "volsurge", "higher_lows"):
            f[k] = 0.5
    return f, len(candles)


def label(entry, fwd, grad_ts, t_dec):
    end = t_dec + H_MIN * 60
    last = entry
    for r in fwd.itertuples():
        if grad_ts is not None and r.ts >= grad_ts:
            return 1
        if r.ts > end:
            break
        if r.trade_count <= 0:
            continue
        if r.price_low <= entry * 0.90:
            return 0
        if r.price_high >= entry * 1.30:
            return 1
        last = r.price_close
    if grad_ts is not None and grad_ts <= end:
        return 1
    return 1 if last >= entry * 1.02 else 0


def main(d, out):
    t0 = time.time()
    con = duckdb.connect()
    con.execute("PRAGMA memory_limit='5GB'")
    con.execute("PRAGMA threads=6")
    con.execute(f"create view s as select *, epoch(bucket_start) ts from '{d}/snapshots.parquet'")
    con.execute(f"create view t as select * from '{d}/tokens.parquet'")
    con.execute("""create temp table dec as
        with f as (select mint, min(ts) t0 from s where phase=1 group by 1),
        p1 as (select s.mint, max(s.ts) tdec, sum(s.trade_count) n1 from s join f using(mint)
               where s.phase=1 and s.ts <= f.t0 + ? group by 1),
        lastrow as (select s.mint, s.market_cap_sol_eob mc, p1.tdec, p1.n1 from s join p1
                    on s.mint=p1.mint and s.ts=p1.tdec and s.phase=1)
        select l.mint, l.tdec, l.mc, t.initial_top10_pct_corrected top10,
               epoch(t.graduated_at) grad_ts
        from lastrow l join t on t.mint=l.mint
        where l.mc >= ? and l.n1 >= 15 and not coalesce(t.top10_pct_suspect, false)
          and (t.graduated_at is null or epoch(t.graduated_at) > l.tdec)""", [T_MIN * 60, MIN_MCAP_SOL])
    dec = con.execute("select * from dec").df()
    print(f"token al punto di decisione: {len(dec)} ({time.time()-t0:.0f}s)")
    rows = con.execute(f"""select s.mint, s.ts, s.phase, s.v_sol_eob, s.trade_count, s.buy_count, s.sell_count,
            s.buy_volume_sol, s.sell_volume_sol, s.price_open, s.price_high, s.price_low, s.price_close,
            s.unique_wallets from s join dec on s.mint=dec.mint
            where s.phase = 1 and s.ts <= dec.tdec + {H_MIN * 60 + 60} order by s.mint, s.ts""").df()
    print(f"righe snapshot: {len(rows)} ({time.time()-t0:.0f}s)")
    info = dec.set_index("mint")
    recs = []
    for mint, g in rows.groupby("mint", sort=False):
        r = info.loc[mint]
        ph1 = g[(g.phase == 1) & (g.ts <= r.tdec)]
        ne = ph1[ph1.trade_count > 0]
        if ne.empty:
            continue
        entry = float(ne.price_close.iloc[-1])
        if entry <= 0:
            continue
        f, nc = features(ph1, r.top10)
        grad = None if pd.isna(r.grad_ts) else float(r.grad_ts)
        y = label(entry, g[g.ts > r.tdec], grad, r.tdec)
        f.update(mint=mint, y=y, tdec=r.tdec, ncandles=nc)
        recs.append(f)
    df = pd.DataFrame(recs)
    df["when"] = pd.to_datetime(df.tdec, unit="s", utc=True)
    tr, te = df[(df.when >= TRAIN_START) & (df.when < TRAIN_END)], df[df.when >= TEST_START]
    print(f"campioni: {len(df)} train {len(tr)} test {len(te)} ({time.time()-t0:.0f}s)")
    X, Xt = tr[FEATURES].values, te[FEATURES].values
    lr = LogisticRegression(C=1.0, max_iter=2000).fit(X, tr.y.values)
    p = lr.predict_proba(Xt)[:, 1]
    rule = (te[FEATURES] * pd.Series(RULE_W)).sum(axis=1).values
    q90 = np.quantile(p, 0.9)
    top = te.y.values[p >= q90]
    res = {
        "w": {k: round(float(v), 5) for k, v in zip(FEATURES, lr.coef_[0])},
        "b": round(float(lr.intercept_[0]), 5),
        "n": int(len(tr)),
        "source": "Slinky21/Pumpfun_Memecoin_Corpus (CC BY 4.0), 2026-06-05..2026-07-14, bonding curve pump.fun",
        "split": {"train": "2026-06-20 <= T < 2026-07-03", "test": "T >= 2026-07-04"},
        "metrics": {
            "n_test": int(len(te)), "base_rate_train": round(float(tr.y.mean()), 4),
            "base_rate_test": round(float(te.y.mean()), 4),
            "auc_test": round(float(roc_auc_score(te.y, p)), 4),
            "auc_rule_score_test": round(float(roc_auc_score(te.y, rule)), 4),
            "precision_top10pct_test": round(float(top.mean()), 4),
            "precision_p_ge_0.5_test": (round(float(te.y.values[p >= 0.5].mean()), 4)
                                        if (p >= 0.5).any() else None),
            "share_with_chart": round(float((df.ncandles >= MIN_CANDLES).mean()), 4),
        },
        "limits": [
            "social/trending/narrative assenti nel corpus: forzati a 0, peso ~0",
            "holders = somma wallet unici per bucket (sovrastima), top10 alla nascita del token",
            "grafico da <=20 candele 1m (bot: 100, minimo 30); soglia qui 10",
            "popolazione: tutti i token pump.fun con mcap>=60 SOL a ~20 min, non i soli candidati del bot",
            "etichetta su 10 min di bucket 1m (ordine stop/TP dentro il minuto ignoto: prudente)",
            "graduated_at quasi assente prima del 19 giu: etichetta da graduazione affidabile solo dopo",
            "solo fase bonding curve; dati di giu-lug 2026 (regime diverso da oggi)",
        ],
        "version": 1,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(out, "w") as fh:
        json.dump(res, fh, indent=1)
    print(json.dumps(res["metrics"], indent=1))
    print(json.dumps(res["w"]), res["b"])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
