"""Backtest della strategia perp su candele reali Hyperliquid (1h + 15m), con fee, funding storico,
slippage stimato dal book L2 reale, margine isolato/liquidazione e limiti di portafoglio.

Uso:  python3 backtest/perp_bt.py            (scarica i dati mancanti in backtest/data, poi testa)
      python3 backtest/perp_bt.py --no-fetch (solo cache)   --refresh (riscarica tutto)
Split: primo 60% del periodo = TRAIN (scelta parametri su griglia piccola), ultimo 40% = TEST (fuori campione).
Ordine conservativo dentro la candela: se nella stessa barra si toccano stop e TP conta prima lo stop;
con gap l'uscita e all'apertura.
"""
import argparse, json, os, sys, time, itertools
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from perp.hl_data import HLInfo, margin_info, maint_rate
from perp import strategy as S
from perp import risk as RK
from perp.paper_broker import walk_book, liq_price

DATA = os.path.join(HERE, "data")
ROME = ZoneInfo("Europe/Rome")
H1, M15 = S.H1, S.M15
TAKER = 0.00045


def load_or_fetch(api, args):
    os.makedirs(DATA, exist_ok=True)
    mp = os.path.join(DATA, "meta.json")
    if args.refresh or not os.path.exists(mp):
        if args.no_fetch:
            sys.exit("cache vuota: rilancia senza --no-fetch")
        meta, ctxs = api.meta_and_ctxs()
        uni = []
        for a, c in zip(meta["universe"], ctxs):
            if a.get("isDelisted"):
                continue
            uni.append((float(c.get("dayNtlVlm") or 0), a["name"]))
        uni = [n for _, n in sorted(uni, reverse=True)[:args.n_coins]]
        slip = {}
        for c in uni:
            b = api.l2_book(c)
            mid = (b["bids"][0][0] + b["asks"][0][0]) / 2
            s = []
            for lv, buy in ((b["asks"], True), (b["bids"], False)):
                f, px = walk_book(lv, args.slip_notional / mid, None, buy)
                s.append(abs(px - mid) / mid if f > 0 else 0.002)
            slip[c] = max(sum(s) / 2, 0.0001)
        json.dump({"meta": meta, "universe": uni, "slip": slip, "fetched_ms": int(time.time() * 1000)}, open(mp, "w"))
    M = json.load(open(mp))
    now = M["fetched_ms"]
    data = {}
    for c in M["universe"]:
        fp = os.path.join(DATA, f"{c}.json")
        if os.path.exists(fp) and not args.refresh:
            data[c] = json.load(open(fp)); continue
        if args.no_fetch:
            continue
        c15 = api.candles(c, "15m", now - 5000 * M15, now)
        start = (c15[0]["t"] if c15 else now - 52 * 86400_000) - 260 * H1
        c1h = api.candles(c, "1h", start, now)
        fh = api.funding_history(c, c15[0]["t"] if c15 else start, now)
        data[c] = {"c15": c15, "c1h": c1h, "funding": fh}
        json.dump(data[c], open(fp, "w"))
        print(f"  dati {c}: 15m={len(c15)} 1h={len(c1h)} funding={len(fh)}", flush=True)
    return M, data


def prepare(M, data, sp):
    feats, fund, c1 = {}, {}, {}
    for c, d in data.items():
        fr = {(x["t"] // H1) * H1: x["rate"] for x in d["funding"]}
        fund[c] = fr
        # funding visibile alla barra = tasso dell'ora precedente gia pubblicato (niente look-ahead)
        rows = S.compute_features(d["c1h"], d["c15"], sp, funding=fr)
        feats[c] = {r["t"]: r for r in rows if r}
        c1[c] = ([b["t"] for b in d["c1h"]], [b["c"] for b in d["c1h"]])
    return feats, fund, c1


def simulate(M, data, feats, fund, c1, sp, rp, t0, t1, start_cash=1000.0):
    info = margin_info(M["meta"])
    slip = M["slip"]
    times = sorted({t for f in feats.values() for t in f if t0 <= t < t1})
    cash, pos, trades = start_cash, {}, []
    eq_curve, day, day_eq = [], None, start_cash
    n_sig = {"signal": 0, "near_miss": 0, "blocked": 0}

    def equity(t):
        e = cash
        for c, p in pos.items():
            r = feats[c].get(t)
            px = r["c"] if r else p["entry"]
            e += p["margin"] + p["qty"] * (px - p["entry"]) * p["side"]
        return e

    def close(c, qty, px, reason, t, slip_mult=1.0):
        nonlocal cash
        p = pos[c]
        s = p["side"]
        px_eff = px * (1 - s * slip[c] * slip_mult)
        q = min(qty, p["qty"])
        pnl = q * (px_eff - p["entry"]) * s
        fee = q * px_eff * TAKER
        rel = p["margin"] * q / p["qty"]
        cash += rel + pnl - fee
        p["margin"] -= rel; p["qty"] -= q
        p["pnl"] += pnl - fee
        p["fees"] += fee
        if p["qty"] <= 1e-12 or reason != "tp1":
            if p["qty"] > 1e-12:
                return close(c, p["qty"], px, reason, t, slip_mult)
            trades.append({"coin": c, "side": s, "kind": p["kind"], "entry_t": p["t"], "exit_t": t,
                           "entry": p["entry"], "exit": px_eff, "pnl": p["pnl"], "fees": p["fees"],
                           "funding": p["funding"], "R_usd": p["risk_usd"], "r_mult": p["pnl"] / p["risk_usd"],
                           "reason": reason, "lev": p["lev"], "hold_h": (t - p["t"]) / H1})
            del pos[c]

    for t in times:
        d = datetime.fromtimestamp(t / 1000, ROME).date()
        if d != day:
            day, day_eq = d, equity(t)
        # funding orario
        if t % H1 == 0:
            for c, p in pos.items():
                r = fund[c].get(t)
                if r is not None:
                    pay = p["side"] * p["qty"] * feats[c][t]["o"] * r if t in feats[c] else 0
                    p["margin"] -= pay; p["funding"] += pay; p["pnl"] -= pay
        # uscite
        for c in list(pos):
            r = feats[c].get(t)
            if not r:
                continue
            p = pos[c]; s = p["side"]; pl = p["plan"]
            adverse = r["l"] if s > 0 else r["h"]
            fav = r["h"] if s > 0 else r["l"]
            lq = liq_price(s, p["entry"], p["qty"], p["margin"], p["mmr"])
            if (adverse - lq) * s <= 0 and (r["o"] - lq) * s <= 0:
                cash += 0; p["pnl"] -= p["margin"]
                trades.append({"coin": c, "side": s, "kind": p["kind"], "entry_t": p["t"], "exit_t": t, "entry": p["entry"],
                               "exit": lq, "pnl": p["pnl"], "fees": p["fees"], "funding": p["funding"], "R_usd": p["risk_usd"],
                               "r_mult": p["pnl"] / p["risk_usd"], "reason": "LIQUIDAZIONE", "lev": p["lev"], "hold_h": 0})
                del pos[c]; continue
            if (adverse - pl["stop"]) * s <= 0:
                px = r["o"] if (r["o"] - pl["stop"]) * s <= 0 else pl["stop"]
                close(c, p["qty"], px, "trailing" if pl["tp1_done"] or (pl["stop"] - p["entry"]) * s > 0 else "stop", t, 2.0)
                continue
            if pl["tp1"] and not pl["tp1_done"] and (fav - pl["tp1"]) * s >= 0:
                px = r["o"] if (r["o"] - pl["tp1"]) * s >= 0 else pl["tp1"]
                close(c, p["qty0"] * sp["tp1_frac"], px, "tp1", t)
                S.after_tp1(pl, sp)
            if c in pos and pl["tp2"] and (fav - pl["tp2"]) * s >= 0:
                close(c, p["qty"], pl["tp2"], "tp2", t); continue
            if c in pos:
                S.trail_update(pl, r["h"], r["l"], r["atr1h"], sp)
                if S.max_hold_hit(pl, r["close_t"], sp):
                    close(c, p["qty"], r["c"], "max_hold", t)
        # ingressi (sulla barra chiusa t, esecuzione all'apertura della barra successiva)
        eq = equity(t)
        eq_curve.append((t, eq))
        cands = []
        for c, f in feats.items():
            r = f.get(t)
            if not r or c in pos:
                continue
            ev = S.evaluate(r, sp)
            if ev["status"] == "near_miss":
                n_sig["near_miss"] += 1
            if ev["status"] == "signal":
                cands.append((ev["score"], c, ev, r))
        for _, c, ev, r in sorted(cands, key=lambda x: x[0], reverse=True):
            n_sig["signal"] += 1
            nxt = feats[c].get(t + M15)
            if not nxt:
                continue
            s = ev["side"]
            entry = nxt["o"] * (1 + s * slip[c])
            plan = S.initial_exit_plan(entry, s, r, sp)
            plan["opened_ms"] = t + M15
            mmr = maint_rate(info[c], 1000)
            sz = RK.size_position(eq, entry, plan["stop"], mmr, info[c]["maxLev"], rp, TAKER)
            if not sz:
                continue
            ps = {k: {"side": v["side"], "notional": v["qty"] * v["entry"]} for k, v in pos.items()}
            ts1, cl1 = c1[c]
            closes = {}
            for k in list(ps) + [c]:
                tt, cc = c1[k]
                closes[k] = [x for x, y in zip(cc, tt) if y + H1 <= t + M15][-int(rp.get("corr_lookback_h", 168)) - 1:]
            ok, why, notional = RK.can_open(c, s, sz["notional"], ps, eq, day_eq, rp, closes)
            if not ok:
                n_sig["blocked"] += 1
                continue
            qty = notional / entry
            margin = notional / sz["leverage"]
            fee = notional * TAKER
            if margin + fee > cash:
                n_sig["blocked"] += 1
                continue
            cash -= margin + fee
            lq = liq_price(s, entry, qty, margin, mmr)
            assert RK.liq_safe(s, plan["stop"], lq, entry, 1.5), (c, entry, plan["stop"], lq)
            pos[c] = {"side": s, "qty": qty, "qty0": qty, "entry": entry, "margin": margin, "lev": sz["leverage"],
                      "mmr": mmr, "plan": plan, "t": t + M15, "kind": ev["kind"], "pnl": -fee, "fees": fee,
                      "funding": 0.0, "risk_usd": sz["risk_usd"] * notional / sz["notional"]}
    if times:
        tl = times[-1]
        for c in list(pos):
            r = feats[c].get(tl)
            close(c, pos[c]["qty"], r["c"] if r else pos[c]["entry"], "fine_periodo", tl)
        eq_curve.append((tl, equity(tl)))
    return trades, eq_curve, n_sig


def metrics(trades, curve, start=1000.0):
    if not curve:
        return {}
    eqs = [e for _, e in curve]
    peak, mdd = eqs[0], 0
    for e in eqs:
        peak = max(peak, e); mdd = min(mdd, e / peak - 1)
    wins = [t for t in trades if t["pnl"] > 0]
    gp = sum(t["pnl"] for t in wins); gl = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)
    return {"trades": len(trades), "win_rate": round(100 * len(wins) / len(trades), 1) if trades else 0,
            "return_pct": round(100 * (eqs[-1] / start - 1), 2), "max_dd_pct": round(100 * mdd, 2),
            "profit_factor": round(gp / gl, 2) if gl > 0 else None,
            "avg_r": round(sum(t["r_mult"] for t in trades) / len(trades), 3) if trades else 0,
            "median_r": round(sorted(t["r_mult"] for t in trades)[len(trades) // 2], 3) if trades else 0,
            "fees_usd": round(sum(t["fees"] for t in trades), 2), "funding_usd": round(sum(t["funding"] for t in trades), 2),
            "liquidations": sum(1 for t in trades if t["reason"] == "LIQUIDAZIONE"),
            "avg_lev": round(sum(t["lev"] for t in trades) / len(trades), 2) if trades else 0,
            "long": sum(1 for t in trades if t["side"] > 0), "short": sum(1 for t in trades if t["side"] < 0)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true"); ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--n-coins", type=int, default=18); ap.add_argument("--slip-notional", type=float, default=3000)
    ap.add_argument("--train-frac", type=float, default=0.6)
    args = ap.parse_args()
    api = HLInfo(budget_per_min=500)
    M, data = load_or_fetch(api, args)
    allt = sorted({b["t"] for d in data.values() for b in d["c15"]})
    t_start = allt[0] + 260 * M15  # warm-up indicatori
    t_end = allt[-1]
    t_split = t_start + int((t_end - t_start) * args.train_frac)
    fmt = lambda t: datetime.fromtimestamp(t / 1000, ROME).strftime("%Y-%m-%d %H:%M")
    print(f"Universo ({len(data)}): {', '.join(data)}")
    print(f"TRAIN {fmt(t_start)} -> {fmt(t_split)} | TEST {fmt(t_split)} -> {fmt(t_end)} (ora di Roma)")
    base_s, base_r = dict(S.DEFAULT_STRATEGY), dict(RK.DEFAULT_RISK)
    grid = [
        {"breakout_tf": "15m", "donchian_n": 20, "sl_atr": 1.5, "trail_atr": 2.5},
        {"breakout_tf": "15m", "donchian_n": 40, "sl_atr": 1.5, "trail_atr": 2.5},
        {"breakout_tf": "15m", "donchian_n": 40, "sl_atr": 2.0, "trail_atr": 3.0},
        {"breakout_tf": "15m", "donchian_n": 40, "sl_atr": 1.5, "trail_atr": 2.5, "trend_min_atr": 1.0},
        {"breakout_tf": "1h", "donchian_n_1h": 24, "sl_atr": 1.5, "trail_atr": 2.5},
        {"breakout_tf": "1h", "donchian_n_1h": 24, "sl_atr": 2.0, "trail_atr": 3.0},
        {"breakout_tf": "1h", "donchian_n_1h": 48, "sl_atr": 2.0, "trail_atr": 3.0},
        {"breakout_tf": "1h", "donchian_n_1h": 24, "sl_atr": 2.0, "trail_atr": 3.0, "trend_min_atr": 1.0},
    ]
    res = []
    for g in grid:
        sp = {**base_s, **g}
        feats, fund, c1 = prepare(M, data, sp)
        tr_tr, cu_tr, _ = simulate(M, data, feats, fund, c1, sp, base_r, t_start, t_split)
        te_tr, cu_te, _ = simulate(M, data, feats, fund, c1, sp, base_r, t_split, t_end + 1)
        mtr, mte = metrics(tr_tr, cu_tr), metrics(te_tr, cu_te)
        res.append({"params": g, "train": mtr, "test": mte})
        print(f"{g} | TRAIN {mtr} | TEST {mte}", flush=True)
    # scelta SOLO sul train: rendimento corretto per drawdown, minimo 15 trade
    ok = [r for r in res if r["train"].get("trades", 0) >= 15] or res
    best = max(ok, key=lambda r: r["train"]["return_pct"] / max(abs(r["train"]["max_dd_pct"]), 2.0))
    print("SCELTO (solo su train):", best["params"])
    sp = {**base_s, **best["params"]}
    out = {"universe": list(data), "train_period": [fmt(t_start), fmt(t_split)], "test_period": [fmt(t_split), fmt(t_end)],
           "slippage_est_per_side": M["slip"], "grid": res, "chosen": best}
    feats, fund, c1 = prepare(M, data, sp)
    for name, a, b in (("full", t_start, t_end + 1),):
        trs, cu, ns = simulate(M, data, feats, fund, c1, sp, base_r, a, b)
        out["full"] = {"metrics": metrics(trs, cu), "signals": ns,
                       "by_kind": {k: metrics([t for t in trs if t["kind"] == k], cu) for k in {t["kind"] for t in trs}},
                       "exit_reasons": {k: sum(1 for t in trs if t["reason"] == k) for k in {t["reason"] for t in trs}}}
        json.dump(trs, open(os.path.join(HERE, "trades_bt.json"), "w"))
    # variante con funding mean reversion attivo (stessi parametri)
    spf = {**sp, "funding_mr_enabled": True}
    feats, fund, c1 = prepare(M, data, spf)
    out["funding_mr_variant"] = {
        "train": metrics(*simulate(M, data, feats, fund, c1, spf, base_r, t_start, t_split)[:2]),
        "test": metrics(*simulate(M, data, feats, fund, c1, spf, base_r, t_split, t_end + 1)[:2])}
    print("FULL:", out["full"]); print("FUNDING MR:", out["funding_mr_variant"])
    # riferimento: BTC buy&hold nel test
    b = data.get("BTC")
    if b:
        px = {x["t"]: x["c"] for x in b["c15"]}
        ks = sorted(k for k in px if k >= t_split)
        out["btc_test_change_pct"] = round(100 * (px[ks[-1]] / px[ks[0]] - 1), 2)
        ks = sorted(k for k in px if t_start <= k < t_split)
        out["btc_train_change_pct"] = round(100 * (px[ks[-1]] / px[ks[0]] - 1), 2)
    json.dump(out, open(os.path.join(HERE, "results.json"), "w"), indent=1)
    print("BTC train/test:", out.get("btc_train_change_pct"), out.get("btc_test_change_pct"))


if __name__ == "__main__":
    main()
