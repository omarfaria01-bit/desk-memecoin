"""Motore del Perp Desk: dati -> strategia -> rischio -> esecuzione (tramite Broker) -> log/snapshot.

Agenti logici (come nel bot memecoin): Data, Strategy, Risk, Executor, Broker, Learner, Logger.
"""
import csv, json, os, time, traceback
from datetime import datetime
from zoneinfo import ZoneInfo

from .hl_data import HLInfo, margin_info, maint_rate
from .exchange import OrderRequest, make_broker, paper_only_enforced, HARD_MAX_LEVERAGE
from . import strategy as S
from . import risk as RK
from .config import load_config, apply_overrides
from .learning import Learner

ROME = ZoneInfo("Europe/Rome")
H1, M15 = S.H1, S.M15
TRADE_COLS = ["closed", "coin", "side", "kind", "leverage", "entry_px", "exit_px", "size", "notional_entry",
              "pnl_usd", "pnl_pct_equity", "r_mult", "fees", "funding", "hold_h", "reason", "score",
              "atr_pct", "stop_init", "liq_px", "opened"]


def now_ms():
    return int(time.time() * 1000)


def rome(ms=None):
    d = datetime.fromtimestamp(ms / 1000, ROME) if ms else datetime.now(ROME)
    return d.isoformat(timespec="seconds")


class Market:
    """Cache dati di mercato condivisa da engine e PaperBroker."""

    def __init__(self, api):
        self.api = api
        self.info, self.ctx, self.mids = {}, {}, {}
        self.ctx_ms = 0

    def refresh_ctx(self):
        meta, ctxs = self.api.meta_and_ctxs()
        self.info = margin_info(meta)
        self.ctx = {}
        for a, c in zip(meta["universe"], ctxs):
            f = lambda k: float(c[k]) if c.get(k) not in (None, "") else None
            self.ctx[a["name"]] = {"funding": f("funding"), "oracle": f("oraclePx"), "mark": f("markPx"),
                                   "vol24": f("dayNtlVlm"), "oi": f("openInterest"), "prev_day": f("prevDayPx")}
        self.ctx_ms = now_ms()

    def refresh_mids(self):
        self.mids = self.api.all_mids()

    def asset_info(self, coin):
        return self.info.get(coin)

    def mark(self, coin):
        return self.mids.get(coin) or (self.ctx.get(coin) or {}).get("mark")

    def l2_book(self, coin):
        return self.api.l2_book(coin)

    def now_ms(self):
        return now_ms()


class Engine:
    def __init__(self, base_dir):
        self.dir = base_dir
        self.cfg = load_config(base_dir)
        self.p = lambda name: os.path.join(base_dir, name)
        self.api = HLInfo(budget_per_min=int(self.cfg["data"].get("rate_budget_per_min", 600)))
        self.market = Market(self.api)
        st = {}
        if os.path.exists(self.p("state.json")):
            try:
                st = json.load(open(self.p("state.json")))
            except Exception:
                st = {}
        self.st = st
        self.plans = st.get("plans", {})
        self.day = st.get("day"); self.day_start_eq = st.get("day_start_eq")
        self.last_bar = st.get("last_bar", 0)
        self.last_funding_hour = st.get("last_funding_hour", now_ms() // H1)
        self.universe = st.get("universe", [])
        self.universe_ms = 0
        self.c15, self.c1h = {}, {}
        self.c1h_ms = {}
        self.last_signals = []
        self.recent_events = []
        self.equity_hist = st.get("equity_hist", [])
        self.ov_mtime = None
        self.sp, self.rp, _ = apply_overrides(self.cfg, base_dir)
        self.learner = Learner(base_dir, st.get("learner"), fee=self.cfg["fees"]["taker"])
        self.broker = None
        self._broker_state = st.get("broker")
        self.started = rome()
        self._last_tick_ms = 0
        self.stats = st.get("stats", {"trades": 0, "wins": 0, "exec_rejects": 0, "liquidations": 0})

    # ---------------- log ----------------
    def log(self, msg, agent="Engine", kind="info", **kw):
        line = f"{rome()} [{agent}] {msg}"
        with open(self.p("run.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
        ev = {"ts": rome(), "agent": agent, "kind": kind, "msg": msg, **kw}
        with open(self.p("events.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        self.recent_events = (self.recent_events + [ev])[-60:]

    # ---------------- persistenza ----------------
    def save(self):
        st = {"saved_at": rome(), "mode": "paper", "broker": self.broker.to_state(), "plans": self.plans,
              "day": self.day, "day_start_eq": self.day_start_eq, "last_bar": self.last_bar,
              "last_funding_hour": self.last_funding_hour, "universe": self.universe,
              "learner": self.learner.to_state(), "stats": self.stats, "equity_hist": self.equity_hist[-3000:]}
        tmp = self.p("state.json.tmp")
        json.dump(st, open(tmp, "w"), indent=1)
        os.replace(tmp, self.p("state.json"))

    # ---------------- dati ----------------
    def refresh_universe(self):
        u = self.cfg["universe"]
        cands = []
        for c, x in self.market.ctx.items():
            inf = self.market.info.get(c)
            if not inf or inf["delisted"] or c in u.get("exclude", []):
                continue
            vol = x.get("vol24") or 0
            if vol < u.get("min_day_volume_usd", 2e7):
                continue
            cands.append((vol, c))
        top = [c for _, c in sorted(cands, reverse=True)[:int(u.get("size", 18))]]
        for c in self.plans:
            if c not in top:
                top.append(c)
        if top != self.universe:
            self.log(f"universo aggiornato ({len(top)}): {', '.join(top)}", "Data")
        self.universe = top
        self.universe_ms = now_ms()

    def _merge(self, old, new, keep):
        d = {b["t"]: b for b in old}
        for b in new:
            d[b["t"]] = b
        return [d[k] for k in sorted(d)][-keep:]

    def refresh_candles(self, coin):
        t = now_ms()
        old15 = self.c15.get(coin, [])
        start = old15[-2]["t"] if len(old15) > 2 else t - 320 * M15
        self.c15[coin] = self._merge(old15, self.api.candles(coin, "15m", start, t), 400)
        if t - self.c1h_ms.get(coin, 0) > 50 * 60_000 or not self.c1h.get(coin):
            old1 = self.c1h.get(coin, [])
            st1 = old1[-2]["t"] if len(old1) > 2 else t - 280 * H1
            self.c1h[coin] = self._merge(old1, self.api.candles(coin, "1h", st1, t), 320)
            self.c1h_ms[coin] = t
        if len(self.c1h[coin]) and self.c1h[coin][-1]["t"] + H1 <= t - 60_000:
            # una nuova barra 1h e chiusa: aggiorna subito
            self.c1h[coin] = self._merge(self.c1h[coin], self.api.candles(coin, "1h", self.c1h[coin][-1]["t"], t), 320)
            self.c1h_ms[coin] = t

    def features(self, coin):
        t = now_ms()
        c15 = [b for b in self.c15.get(coin, []) if b["t"] + M15 <= t]
        c1h = [b for b in self.c1h.get(coin, []) if b["t"] + H1 <= t]
        rows = S.compute_features(c1h, c15, self.sp)
        fr = (self.market.ctx.get(coin) or {}).get("funding")
        for r in rows[-8:]:
            if r:
                r["funding"] = fr
        return rows

    # ---------------- conto ----------------
    def equity(self):
        return self.broker.balance()["account_value"]

    def flags(self):
        return {"stop": os.path.exists(self.p("STOP")), "pause": os.path.exists(self.p("PAUSE"))}

    def risk_positions(self):
        return {c: {"side": 1 if p["size"] > 0 else -1, "notional": p["notional"]}
                for c, p in self.broker.positions().items()}

    # ---------------- esecuzione ----------------
    def place(self, req, retries=1):
        for i in range(retries + 1):
            res = self.broker.place_order(req)
            if res.ok or res.status != "rejected" or "simulato" not in res.reason:
                return res
            self.stats["exec_rejects"] += 1
            self.log(f"{req.coin} ordine {req.tag} rifiutato ({res.reason}), riprovo", "Executor", "reject")
        return res

    def open_position(self, coin, row, ev):
        sp, rp = self.sp, self.rp
        side = ev["side"]
        mid = self.market.mark(coin) or row["c"]
        plan0 = S.initial_exit_plan(mid, side, row, sp)
        eq = self.equity()
        info = self.market.asset_info(coin)
        mmr = maint_rate(info, 1000)
        sz = RK.size_position(eq, mid, plan0["stop"], mmr, info["maxLev"], rp, self.cfg["fees"]["taker"])
        closes = {c: [b["c"] for b in self.c1h.get(c, [])] for c in list(self.plans) + [coin]}
        ok, why, notional = RK.can_open(coin, side, sz["notional"] if sz else 0, self.risk_positions(), eq,
                                        self.day_start_eq, rp, closes, self.flags())
        if not ok or not sz:
            self.log(f"{coin} {'LONG' if side > 0 else 'SHORT'} bloccato dal rischio: {why}", "Risk", "block", coin=coin)
            return "blocked:" + why
        lev = min(sz["leverage"], HARD_MAX_LEVERAGE)
        if not self.broker.set_leverage(coin, lev, isolated=True):
            return "blocked:leva non accettata"
        res = self.place(OrderRequest(coin, side > 0, notional / mid, "market", leverage=lev, tag="entry"))
        if not res.ok or res.filled_sz <= 0:
            self.log(f"{coin} ingresso fallito: {res.reason or res.status}", "Executor", "exec_fail", coin=coin)
            return "exec_reject:" + (res.reason or res.status)
        plan = S.initial_exit_plan(res.avg_px, side, row, sp)
        plan["opened_ms"] = now_ms()
        pos = self.broker.positions()[coin]
        if not RK.liq_safe(side, plan["stop"], pos["liq_px"], res.avg_px, 1.5):
            self.log(f"{coin} liquidazione {pos['liq_px']:.6g} troppo vicina allo stop {plan['stop']:.6g}: chiudo subito",
                     "Risk", "critical", coin=coin)
            self.place(OrderRequest(coin, side < 0, abs(pos["size"]), "market", reduce_only=True, tag="liq_guard"), 3)
            return "closed:liq_guard"
        plan.update({"coin": coin, "kind": ev["kind"], "score": ev.get("score"), "atr_pct": ev.get("atr_pct"),
                     "leverage": lev, "stop_init": plan["stop"], "liq_px": pos["liq_px"], "size0": res.filled_sz,
                     "notional0": res.filled_sz * res.avg_px, "risk_usd": sz["risk_usd"] * notional / sz["notional"],
                     "fees": res.fee, "realized": 0.0, "funding": 0.0, "sl_oid": None, "tp1_oid": None,
                     "opened": rome(), "exit_px": None, "exit_tag": None, "eq_at_open": eq})
        self.plans[coin] = plan
        self.place_protection(coin)
        self.log(f"APERTO {coin} {'LONG' if side > 0 else 'SHORT'} {ev['kind']} {res.filled_sz:g} @ {res.avg_px:.6g} "
                 f"leva {lev}x nozionale ${res.filled_sz * res.avg_px:.0f}, stop {plan['stop']:.6g} "
                 f"(liq {pos['liq_px']:.6g})" + (f", TP1 {plan['tp1']:.6g}" if plan["tp1"] else ""), "Executor", "open", coin=coin)
        return "opened"

    def place_protection(self, coin):
        """Mette (o rimette) lo stop e il TP1 come ordini trigger reduce-only, come si farebbe su HL."""
        pl = self.plans[coin]
        pos = self.broker.positions().get(coin)
        if not pos:
            return
        side = pl["side"]
        if pl.get("sl_oid"):
            self.broker.cancel(coin, pl["sl_oid"])
        r = self.place(OrderRequest(coin, side < 0, abs(pos["size"]), "trigger", reduce_only=True,
                                    trigger_px=pl["stop"], tpsl="sl", tag="sl"), 3)
        pl["sl_oid"] = r.order_id if r.ok else None
        if not r.ok:
            self.log(f"{coin} impossibile piazzare lo stop: {r.reason}", "Executor", "critical", coin=coin)
        if pl.get("tp1") and not pl["tp1_done"] and not pl.get("tp1_oid"):
            r = self.place(OrderRequest(coin, side < 0, pl["size0"] * self.sp["tp1_frac"], "trigger", reduce_only=True,
                                        trigger_px=pl["tp1"], tpsl="tp", tag="tp1"), 3)
            pl["tp1_oid"] = r.order_id if r.ok else None

    def close_position(self, coin, reason):
        pos = self.broker.positions().get(coin)
        if not pos:
            return
        pl = self.plans.get(coin)
        if pl:
            pl["exit_tag"] = reason
        res = self.place(OrderRequest(coin, pos["size"] < 0, abs(pos["size"]), "market", reduce_only=True, tag=reason), 3)
        self.log(f"{coin} chiusura ({reason}): {res.status} {res.reason}", "Executor", "close", coin=coin)

    def process_fills(self):
        for f in self.broker.fills:
            pl = self.plans.get(f["coin"])
            if not pl or f["tag"] == "entry":
                continue
            pl["fees"] += f["fee"]; pl["realized"] += f["realized"]
            pl["exit_px"] = f["px"]
            pl["exit_tag"] = pl.get("exit_tag") or f["tag"]
            if f["tag"] in ("sl", "tp1", "LIQUIDATION"):
                pl["exit_tag"] = f["tag"]
            if f["tag"] == "tp1" and not pl["tp1_done"]:
                S.after_tp1(pl, self.sp)
                pl["tp1_oid"] = None
                self.log(f"{f['coin']} TP1 preso {f['size']:g} @ {f['px']:.6g}, stop a pareggio {pl['stop']:.6g}",
                         "Executor", "tp1", coin=f["coin"])
                if f["coin"] in self.broker.pos:
                    self.place_protection(f["coin"])
        self.broker.fills = []
        for p in self.broker.closed:
            c = p["coin"]
            pl = self.plans.pop(c, None)
            if not pl:
                continue
            pl["funding"] = p.get("funding_paid", pl["funding"])
            pnl = pl["realized"] - pl["fees"] - pl["funding"]
            if pl["exit_tag"] == "LIQUIDATION":
                self.stats["liquidations"] += 1
            reason = {"sl": "trailing" if (pl["stop"] - pl["entry"]) * pl["side"] > 0 else "stop"}.get(pl["exit_tag"], pl["exit_tag"])
            self.stats["trades"] += 1; self.stats["wins"] += int(pnl > 0)
            row = {"closed": rome(), "coin": c, "side": "LONG" if pl["side"] > 0 else "SHORT", "kind": pl["kind"],
                   "leverage": pl["leverage"], "entry_px": pl["entry"], "exit_px": pl["exit_px"], "size": pl["size0"],
                   "notional_entry": round(pl["notional0"], 2), "pnl_usd": round(pnl, 4),
                   "pnl_pct_equity": round(100 * pnl / pl["eq_at_open"], 3), "r_mult": round(pnl / pl["risk_usd"], 3),
                   "fees": round(pl["fees"], 4), "funding": round(pl["funding"], 4),
                   "hold_h": round((now_ms() - pl["opened_ms"]) / H1, 2), "reason": reason, "score": pl["score"],
                   "atr_pct": pl["atr_pct"], "stop_init": pl["stop_init"], "liq_px": pl["liq_px"], "opened": pl["opened"]}
            new = not os.path.exists(self.p("trades.csv"))
            with open(self.p("trades.csv"), "a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=TRADE_COLS)
                if new:
                    w.writeheader()
                w.writerow(row)
            self.log(f"CHIUSO {c} {row['side']} {reason}: PnL ${pnl:+.2f} ({row['r_mult']:+.2f}R, fee ${pl['fees']:.2f}, "
                     f"funding ${pl['funding']:+.2f})", "Executor", "close", coin=c, pnl=pnl)
        self.broker.closed = []

    # ---------------- cicli ----------------
    def on_bar(self, bar_t):
        sp, rp = self.sp, self.rp
        flags = self.flags()
        coins = list(dict.fromkeys(self.universe + list(self.plans)))
        rows_by = {}
        for c in coins:
            if now_ms() - self._last_tick_ms > 5000:  # durante il download candele continua a sorvegliare stop/liquidazioni
                self.tick(); self.snapshot()
            try:
                self.refresh_candles(c)
                rows_by[c] = self.features(c)
            except Exception as e:
                self.log(f"{c} candele non disponibili: {e}", "Data", "warn")
        # 1) gestione posizioni: trailing e max hold sulla barra chiusa
        for c, pl in list(self.plans.items()):
            rows = [r for r in rows_by.get(c, []) if r]
            if not rows or c not in self.broker.pos:
                continue
            r = rows[-1]
            old = pl["stop"]
            S.trail_update(pl, r["h"], r["l"], r["atr1h"], sp)
            if S.max_hold_hit(pl, now_ms(), sp):
                self.close_position(c, "max_hold"); continue
            if pl["stop"] != old or not pl.get("sl_oid") or int(pl["sl_oid"]) not in self.broker.orders:
                pl["sl_oid"] = pl.get("sl_oid") if pl.get("sl_oid") and int(pl["sl_oid"]) in self.broker.orders else None
                self.place_protection(c)
                if pl["stop"] != old:
                    self.log(f"{c} stop spostato {old:.6g} -> {pl['stop']:.6g}", "Executor", "trail", coin=c)
        self.process_fills()
        # 2) ombre
        for c, rows in rows_by.items():
            self.learner.update(c, rows[-6:], sp)
        # 3) nuovi segnali
        cands, sigs = [], []
        for c in self.universe:
            rows = rows_by.get(c) or []
            if not rows or rows[-1] is None:
                continue
            r = rows[-1]
            ev = S.evaluate(r, sp)
            if ev["status"] in ("signal", "near_miss"):
                sigs.append({"coin": c, **{k: ev.get(k) for k in ("status", "side", "kind", "reasons", "score", "atr_pct")}})
            if ev["status"] == "near_miss":
                self.learner.log_signal(c, r, ev, "shadow")
                self.learner.add_shadow(c, r, ev, "near_miss", ev["reasons"][0], sp)
            elif ev["status"] == "signal":
                cands.append((ev["score"], c, r, ev))
        for _, c, r, ev in sorted(cands, key=lambda x: x[0], reverse=True):
            if c in self.plans:
                self.learner.log_signal(c, r, ev, "blocked:posizione gia aperta"); continue
            if flags["stop"] or flags["pause"]:
                dec = "blocked:" + ("STOP" if flags["stop"] else "PAUSE")
            else:
                dec = self.open_position(c, r, ev)
            self.learner.log_signal(c, r, ev, dec)
            if dec.startswith("blocked") or dec.startswith("exec_reject"):
                self.learner.add_shadow(c, r, ev, "risk_block" if dec.startswith("blocked") else "exec_reject",
                                        dec.split(":", 1)[1][:60], sp)
        self.last_signals = (sigs + self.last_signals)[:40] if sigs else self.last_signals
        n = len(cands)
        self.log(f"barra 15m chiusa alle {datetime.fromtimestamp((bar_t + M15) / 1000, ROME):%H:%M} analizzata: {len(rows_by)} coin, "
                 f"{n} segnali, {sum(1 for s in sigs if s['status'] == 'near_miss')} quasi-segnali, "
                 f"{len(self.plans)} posizioni, ombre aperte {len(self.learner.open)}", "Strategy")

    def tick(self):
        t = now_ms()
        self._last_tick_ms = t
        self.market.refresh_mids()
        if t - self.market.ctx_ms > int(self.cfg["data"].get("ctx_refresh_s", 60)) * 1000:
            self.market.refresh_ctx()
        # funding orario (HL lo regola ogni ora)
        h = t // H1
        if h > self.last_funding_hour:
            rates = {c: (self.market.ctx.get(c) or {}).get("funding") for c in self.broker.pos}
            orc = {c: (self.market.ctx.get(c) or {}).get("oracle") for c in self.broker.pos}
            evs = self.broker.apply_funding(rates, orc)
            for e in evs:
                if e["coin"] in self.plans:
                    self.plans[e["coin"]]["funding"] += e["payment"]
                self.log(f"funding {e['coin']}: tasso {e['rate'] * 100:.4f}%/h, pagato ${e['payment']:+.4f}", "Broker", "funding")
            self.last_funding_hour = h
        for e in self.broker.on_tick(t):
            if e["type"] == "liquidation":
                self.log(f"LIQUIDAZIONE {e['coin']} @ {e['liq_px']:.6g}: perso margine ${e['loss']:.2f}", "Broker", "critical")
            elif e["type"] == "trigger":
                self.log(f"{e['coin']} trigger {e['tag']} @ {e['trigger_px']:.6g}: {e['result']['status']} "
                         f"{e['result']['filled_sz']:g} @ {e['result']['avg_px']:.6g}", "Broker", "trigger", coin=e["coin"])
        self.process_fills()
        # kill switch
        if os.path.exists(self.p("STOP")) and self.broker.pos:
            for c in list(self.broker.pos):
                self.close_position(c, "STOP")
            for o in self.broker.open_orders():
                self.broker.cancel(o["coin"], o["oid"])
            self.process_fills()
        # giorno (Rome) e stop giornaliero
        eq = self.equity()
        d = datetime.now(ROME).date().isoformat()
        if d != self.day:
            self.day, self.day_start_eq = d, eq
            self.log(f"nuovo giorno {d}: equity di partenza ${eq:.2f}", "Risk")
        if t - (self.equity_hist[-1][0] if self.equity_hist else 0) >= 60_000:
            b = self.broker.balance()
            self.equity_hist.append([t, round(eq, 4)])
            new = not os.path.exists(self.p("equity.csv"))
            with open(self.p("equity.csv"), "a", encoding="utf-8") as f:
                if new:
                    f.write("ts,equity,cash,margin_used,upnl,positions,exposure_x\n")
                f.write(f"{rome()},{eq:.4f},{b['free']:.4f},{b['margin_used']:.4f},{b['upnl']:.4f},"
                        f"{len(self.broker.pos)},{b['notional'] / eq if eq else 0:.3f}\n")

    def snapshot(self):
        b = self.broker.balance()
        eq = b["account_value"]
        start = float(self.cfg["start_cash"])
        pos = []
        for c, p in self.broker.positions().items():
            pl = self.plans.get(c, {})
            side = 1 if p["size"] > 0 else -1
            pos.append({"coin": c, "side": "LONG" if side > 0 else "SHORT", "size": p["size"], "entry_px": p["entry_px"],
                        "mark_px": p["mark_px"], "leverage": p["leverage"], "margin": round(p["margin"], 4),
                        "notional": round(p["notional"], 2), "upnl": round(p["upnl"], 4),
                        "upnl_pct_margin": round(100 * p["upnl"] / p["margin"], 2) if p["margin"] else None,
                        "liq_px": p["liq_px"], "stop": pl.get("stop"), "tp1": pl.get("tp1"), "tp1_done": pl.get("tp1_done"),
                        "kind": pl.get("kind"), "opened": pl.get("opened"), "funding_paid": round(p.get("funding_paid", 0), 4),
                        "chart": f"https://app.hyperliquid.xyz/trade/{c}"})
        trades = []
        if os.path.exists(self.p("trades.csv")):
            with open(self.p("trades.csv"), encoding="utf-8") as f:
                trades = list(csv.DictReader(f))[-30:]
        snap = {"updated_at": rome(), "updated_ms": now_ms(), "mode": "PAPER", "paper_only": paper_only_enforced(),
                "broker": self.broker.name, "started": self.started, "start_cash": start, "equity": round(eq, 4),
                "pnl_usd": round(eq - start, 4), "pnl_pct": round(100 * (eq / start - 1), 3), "cash_free": round(b["free"], 4),
                "margin_used": round(b["margin_used"], 4), "upnl": round(b["upnl"], 4),
                "exposure_x": round(b["notional"] / eq, 3) if eq else 0, "day_start_equity": self.day_start_eq,
                "day_pnl_pct": round(100 * (eq / self.day_start_eq - 1), 3) if self.day_start_eq else 0,
                "flags": self.flags(), "positions": pos, "open_orders": self.broker.open_orders(),
                "universe": [{"coin": c, "mid": self.market.mids.get(c),
                              "funding_1h": (self.market.ctx.get(c) or {}).get("funding"),
                              "vol24": (self.market.ctx.get(c) or {}).get("vol24")} for c in self.universe],
                "signals": self.last_signals[:20], "recent_trades": trades, "events": self.recent_events[-30:],
                "equity_curve": self.equity_hist[-720:], "params": {"strategy": self.sp, "risk": self.rp},
                "stats": self.stats, "shadow_open": len(self.learner.open), "shadow_stats": self.learner.stats,
                "fees_total": round(self.broker.fees_total, 4), "funding_total": round(self.broker.funding(), 4)}
        tmp = self.p("perp_snapshot.json.tmp")
        json.dump(snap, open(tmp, "w"), default=str)
        os.replace(tmp, self.p("perp_snapshot.json"))
        return snap

    def check_overrides(self):
        path = self.p("overrides.json")
        m = os.path.getmtime(path) if os.path.exists(path) else None
        if m != self.ov_mtime:
            self.ov_mtime = m
            self.sp, self.rp, notes = apply_overrides(self.cfg, self.dir)
            self.log("overrides ricaricati" + (": " + "; ".join(notes) if notes else ""), "Engine")

    def run(self, once=False):
        if self.cfg.get("MODE") != "paper" or not paper_only_enforced():
            self.log("ATTENZIONE: config non in paper o PERP_PAPER_ONLY disattivato: il broker live e uno stub", "Engine", "warn")
        self.market.refresh_ctx(); self.market.refresh_mids()
        self.broker = make_broker(self.cfg, self.market, lambda m: self.log(m, "Engine", "warn"), self._broker_state)
        self.log(f"avvio Perp Desk: broker={self.broker.name} paper_only={paper_only_enforced()} "
                 f"equity ${self.equity():.2f} leva max {min(self.rp['max_leverage'], HARD_MAX_LEVERAGE)}x", "Engine")
        tick_s = float(self.cfg["data"].get("tick_s", 5))
        errors = 0
        while True:
            try:
                self.check_overrides()
                if now_ms() - self.universe_ms > int(self.cfg["universe"].get("refresh_min", 60)) * 60_000:
                    self.refresh_universe()
                self.tick()
                bar_t = (now_ms() - int(self.cfg["data"].get("bar_delay_s", 20)) * 1000) // M15 * M15 - M15
                if bar_t > self.last_bar:
                    self.on_bar(bar_t)
                    self.last_bar = bar_t
                self.snapshot()
                self.save()
                errors = 0
            except Exception as e:
                errors += 1
                self.log(f"errore nel ciclo: {e!r}", "Engine", "error", tb=traceback.format_exc()[-800:])
                time.sleep(min(60, 5 * errors))
            if once:
                return
            time.sleep(tick_s)
