"""Hook di apprendimento: log di ogni segnale (anche scartato) e trade ombra sui quasi-segnali.
I CSV sono pensati per l'agente di apprendimento del team (ticher)."""
import csv, os
from datetime import datetime
from zoneinfo import ZoneInfo
from . import strategy as S

ROME = ZoneInfo("Europe/Rome")
SIG_COLS = ["ts", "bar_t", "coin", "kind", "side", "status", "decision", "reasons", "score", "atr_pct",
            "dist_atr15", "close", "atr1h", "ema_f", "ema_s", "vol_ratio", "funding_1h"]
SH_COLS = ["opened", "closed", "coin", "kind", "side", "source", "why", "entry", "exit", "stop_init",
           "r_mult_net", "max_fav_r", "hold_h", "exit_reason"]


def _ts(ms=None):
    d = datetime.fromtimestamp(ms / 1000, ROME) if ms else datetime.now(ROME)
    return d.strftime("%Y-%m-%d %H:%M:%S")


def _append(path, cols, row):
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)


class Learner:
    def __init__(self, base_dir, state=None, fee=0.00045, slip=0.0005, max_open=150):
        self.sig_path = os.path.join(base_dir, "signals.csv")
        self.sh_path = os.path.join(base_dir, "shadow_trades.csv")
        self.open = (state or {}).get("shadows", [])
        self.stats = (state or {}).get("shadow_stats", {})
        self.cost = 2 * (fee + slip)
        self.max_open = max_open

    def to_state(self):
        return {"shadows": self.open, "shadow_stats": self.stats}

    def log_signal(self, coin, row, ev, decision):
        _append(self.sig_path, SIG_COLS, {
            "ts": _ts(), "bar_t": _ts(row["t"]) if row else "", "coin": coin, "kind": ev.get("kind", ""),
            "side": ev.get("side", ""), "status": ev["status"], "decision": decision,
            "reasons": "|".join(ev.get("reasons", [])), "score": ev.get("score", ""), "atr_pct": ev.get("atr_pct", ""),
            "dist_atr15": ev.get("dist_atr15", ""), "close": row["c"] if row else "",
            "atr1h": round(row["atr1h"], 6) if row else "", "ema_f": round(row["ema_f"], 6) if row else "",
            "ema_s": round(row["ema_s"], 6) if row else "",
            "vol_ratio": round(row["v"] / row["vavg"], 3) if row and row["vavg"] else "",
            "funding_1h": row.get("funding") if row else ""})

    def add_shadow(self, coin, row, ev, source, why, sp):
        if len(self.open) >= self.max_open:
            return
        if any(s["coin"] == coin and s["side"] == ev["side"] and s["kind"] == ev["kind"] for s in self.open):
            return
        plan = S.initial_exit_plan(row["c"], ev["side"], row, sp)
        self.open.append({"coin": coin, "side": ev["side"], "kind": ev["kind"], "source": source, "why": why,
                          "plan": plan, "stop_init": plan["stop"], "opened_t": row["close_t"], "last_t": row["t"],
                          "realized_r": 0.0, "left": 1.0})

    def update(self, coin, rows, sp):
        """Avanza le ombre di `coin` sulle nuove barre chiuse (ordine conservativo come nel backtest)."""
        for s in [x for x in self.open if x["coin"] == coin]:
            pl, side = s["plan"], s["side"]
            for r in rows:
                if r is None or r["t"] <= s["last_t"]:
                    continue
                s["last_t"] = r["t"]
                adv, fav = (r["l"], r["h"]) if side > 0 else (r["h"], r["l"])
                done = None
                if (adv - pl["stop"]) * side <= 0:
                    px = r["o"] if (r["o"] - pl["stop"]) * side <= 0 else pl["stop"]
                    done = (px, "stop/trailing")
                else:
                    if pl["tp1"] and not pl["tp1_done"] and (fav - pl["tp1"]) * side >= 0:
                        f = sp["tp1_frac"]
                        s["realized_r"] += f * (pl["tp1"] - pl["entry"]) * side / pl["R"]
                        s["left"] -= f
                        S.after_tp1(pl, sp)
                    S.trail_update(pl, r["h"], r["l"], r["atr1h"], sp)
                    if S.max_hold_hit(pl, r["close_t"], sp):
                        done = (r["c"], "max_hold")
                if done:
                    px, why = done
                    r_mult = s["realized_r"] + s["left"] * (px - pl["entry"]) * side / pl["R"]
                    r_net = r_mult - self.cost * pl["entry"] / pl["R"]
                    _append(self.sh_path, SH_COLS, {
                        "opened": _ts(s["opened_t"]), "closed": _ts(r["close_t"]), "coin": coin, "kind": s["kind"],
                        "side": side, "source": s["source"], "why": s["why"], "entry": pl["entry"], "exit": px,
                        "stop_init": s["stop_init"], "r_mult_net": round(r_net, 3),
                        "max_fav_r": round((pl["best"] - pl["entry"]) * side / pl["R"], 3),
                        "hold_h": round((r["close_t"] - s["opened_t"]) / 3_600_000, 2), "exit_reason": why})
                    st = self.stats.setdefault(s["why"], {"n": 0, "wins": 0, "sum_r": 0.0})
                    st["n"] += 1; st["wins"] += int(r_net > 0); st["sum_r"] = round(st["sum_r"] + r_net, 3)
                    self.open.remove(s)
                    break
