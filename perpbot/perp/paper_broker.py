"""PaperBroker: simulazione realistica di Hyperliquid con soldi finti.

- fill contro il book L2 reale (si "cammina" il book: slippage vero, fill parziali se manca profondita)
- fee taker/maker Hyperliquid (tier base), funding orario dai tassi reali
- margine isolato con prezzo di liquidazione realistico (mantenimento dai marginTables della meta)
- latenza simulata e rifiuti casuali degli ordini
- ordini trigger (SL/TP) e limit restano "sull'exchange" e vengono valutati a ogni tick, come su HL
"""
import random, time
from .exchange import Broker, OrderRequest, OrderResult, HARD_MAX_LEVERAGE
from .hl_data import maint_rate


def walk_book(levels, size, limit_px=None, is_buy=True):
    """Riempie `size` camminando i livelli [(px, sz)] fino a limit_px. Ritorna (filled, avg_px)."""
    filled, cost = 0.0, 0.0
    for px, sz in levels:
        if limit_px is not None and ((is_buy and px > limit_px) or (not is_buy and px < limit_px)):
            break
        take = min(sz, size - filled)
        if take <= 0:
            break
        filled += take
        cost += take * px
        if filled >= size - 1e-12:
            break
    return filled, (cost / filled if filled > 0 else 0.0)


def liq_price(side, entry, size_abs, margin, mmr):
    """Prezzo di liquidazione isolata (formula Hyperliquid). side=+1 long, -1 short.
    Liquidazione quando margine + PnL = mmr * nozionale al prezzo corrente."""
    if size_abs <= 0:
        return None
    if side > 0:
        p = (entry - margin / size_abs) / (1 - mmr)
        return max(p, 0.0)
    return (entry + margin / size_abs) / (1 + mmr)


class PaperBroker(Broker):
    name = "paper"
    is_live = False

    def __init__(self, cfg, market, state=None, rng=None, sleep=time.sleep):
        self.cfg = cfg
        self.market = market
        fees = cfg.get("fees", {})
        self.taker = float(fees.get("taker", 0.00045))
        self.maker = float(fees.get("maker", 0.00015))
        p = cfg.get("paper", {})
        self.lat_ms = p.get("latency_ms", [150, 600])
        self.reject_prob = float(p.get("reject_prob", 0.02))
        self.max_slip = float(p.get("market_slippage_cap_pct", 5.0)) / 100.0
        self.trigger_slip = float(p.get("trigger_slippage_cap_pct", 10.0)) / 100.0
        self.max_lev_cfg = min(float(cfg.get("risk", {}).get("max_leverage", HARD_MAX_LEVERAGE)), HARD_MAX_LEVERAGE)
        self.rng = rng or random.Random()
        self.sleep = sleep
        st = state or {}
        self.cash = float(st.get("cash", cfg.get("start_cash", 1000.0)))
        self.pos = st.get("positions", {})
        self.orders = {int(k): v for k, v in st.get("orders", {}).items()}
        self.next_id = int(st.get("next_id", 1))
        self.funding_total = st.get("funding_total", {})
        self.fees_total = float(st.get("fees_total", 0.0))
        self.realized_total = float(st.get("realized_total", 0.0))
        self.fills = []  # fill di questa sessione (l'engine li scarica)
        self.closed = []  # posizioni chiuse (l'engine le scarica)

    # ---------- persistenza ----------
    def to_state(self):
        return {"cash": self.cash, "positions": self.pos, "orders": self.orders, "next_id": self.next_id,
                "funding_total": self.funding_total, "fees_total": self.fees_total,
                "realized_total": self.realized_total}

    # ---------- helper ----------
    def _lev_cap(self, coin):
        info = self.market.asset_info(coin)
        return min(self.max_lev_cfg, HARD_MAX_LEVERAGE, float(info["maxLev"]))

    def _round_sz(self, coin, sz):
        d = self.market.asset_info(coin)["szDecimals"]
        f = 10 ** d
        return int(sz * f + 1e-9) / f

    def _mmr(self, coin, notional):
        return maint_rate(self.market.asset_info(coin), notional)

    def _recalc_liq(self, coin):
        p = self.pos[coin]
        q = abs(p["size"])
        p["liq_px"] = liq_price(1 if p["size"] > 0 else -1, p["entry_px"], q, p["margin"],
                                self._mmr(coin, q * p["entry_px"]))

    def _reject(self, reason):
        return OrderResult(False, "rejected", reason=reason)

    # ---------- Broker API ----------
    def set_leverage(self, coin, leverage, isolated=True):
        if leverage > self._lev_cap(coin) + 1e-9 or leverage < 1:
            return False
        if coin in self.pos:  # come HL: non si cambia leva con posizione aperta in isolata (semplificazione)
            return abs(self.pos[coin]["leverage"] - leverage) < 1e-9
        return True

    def place_order(self, req: OrderRequest) -> OrderResult:
        coin = req.coin
        info = self.market.asset_info(coin)
        if info is None or info.get("delisted"):
            return self._reject("coin sconosciuta o delistata")
        size = self._round_sz(coin, req.size)
        if size <= 0:
            return self._reject("size zero dopo arrotondamento szDecimals")
        cur = self.pos.get(coin)
        if req.reduce_only:
            if not cur or (cur["size"] > 0) == req.is_buy:
                return self._reject("reduce-only senza posizione da ridurre")
            size = min(size, abs(cur["size"]))
        else:
            lev = float(req.leverage or (cur["leverage"] if cur else 1.0))
            if lev > self._lev_cap(coin) + 1e-9:
                return self._reject(f"leva {lev:.2f}x oltre il limite {self._lev_cap(coin):.0f}x")
            if lev < 1:
                return self._reject("leva < 1")
            if cur and abs(cur["leverage"] - lev) > 1e-9 and (cur["size"] > 0) == req.is_buy:
                return self._reject("leva diversa da quella della posizione aperta")
            req.leverage = lev
        req.size = size
        if req.order_type == "trigger":
            if req.trigger_px is None or not req.reduce_only:
                return self._reject("trigger richiede trigger_px e reduce_only")
            return self._rest(req)
        # latenza: il book viene letto DOPO il ritardo, come nella realta
        if self.lat_ms and max(self.lat_ms) > 0:
            self.sleep(self.rng.uniform(*self.lat_ms) / 1000.0)
        if self.rng.random() < self.reject_prob:
            return self._reject("rifiuto simulato (rete/exchange)")
        if req.order_type == "limit":
            book = self.market.l2_book(coin)
            best = book["asks"][0][0] if req.is_buy else book["bids"][0][0]
            crosses = (req.is_buy and req.limit_px >= best) or ((not req.is_buy) and req.limit_px <= best)
            if not crosses:
                if req.tif == "Ioc":
                    return OrderResult(False, "cancelled", reason="IOC non eseguito")
                return self._rest(req)
            if req.tif == "Alo":
                return self._reject("post-only avrebbe incrociato")
            return self._take(req, book, req.limit_px)
        if req.order_type == "market":
            book = self.market.l2_book(coin)
            mid = (book["asks"][0][0] + book["bids"][0][0]) / 2
            cap = mid * (1 + self.max_slip) if req.is_buy else mid * (1 - self.max_slip)
            return self._take(req, book, cap)
        return self._reject("tipo ordine sconosciuto")

    def _rest(self, req):
        oid = self.next_id
        self.next_id += 1
        self.orders[oid] = {"oid": oid, **req.__dict__}
        return OrderResult(True, "resting", order_id=oid)

    def _take(self, req, book, limit_px, fee_rate=None):
        levels = book["asks"] if req.is_buy else book["bids"]
        filled, avg = walk_book(levels, req.size, limit_px, req.is_buy)
        filled = self._round_sz(req.coin, filled)
        if filled <= 0:
            return OrderResult(False, "cancelled", reason="nessuna liquidita entro il limite di prezzo")
        return self._apply_fill(req, filled, avg, self.taker if fee_rate is None else fee_rate)

    def _apply_fill(self, req, filled, px, fee_rate):
        coin = req.coin
        notional = filled * px
        fee = notional * fee_rate
        cur = self.pos.get(coin)
        sign = 1 if req.is_buy else -1
        realized = 0.0
        if cur and (cur["size"] > 0) != req.is_buy:  # riduzione / chiusura
            q = abs(cur["size"])
            close = min(filled, q)
            side = 1 if cur["size"] > 0 else -1
            realized = close * (px - cur["entry_px"]) * side
            released = cur["margin"] * close / q
            cur["margin"] -= released
            cur["size"] += sign * close
            cur["realized"] = cur.get("realized", 0.0) + realized
            cur["fees"] = cur.get("fees", 0.0) + fee
            self.cash += released + realized - fee
            if abs(cur["size"]) < 1e-12:
                self._closed(coin, cur)
            else:
                self._recalc_liq(coin)
            filled = close
        else:  # apertura / aumento
            lev = float(req.leverage)
            margin = notional / lev
            if margin + fee > self.cash + 1e-9:
                return self._reject(f"margine insufficiente: servono {margin + fee:.2f}, liberi {self.cash:.2f}")
            self.cash -= margin + fee
            if cur:
                q = abs(cur["size"])
                cur["entry_px"] = (cur["entry_px"] * q + px * filled) / (q + filled)
                cur["size"] += sign * filled
                cur["margin"] += margin
                cur["fees"] = cur.get("fees", 0.0) + fee
            else:
                self.pos[coin] = {"coin": coin, "size": sign * filled, "entry_px": px, "margin": margin,
                                  "leverage": lev, "liq_px": None, "funding_paid": 0.0, "fees": fee,
                                  "realized": 0.0, "opened_ms": self.market.now_ms()}
            self._recalc_liq(coin)
        self.fees_total += fee
        self.realized_total += realized
        fill = {"ts_ms": self.market.now_ms(), "coin": coin, "is_buy": req.is_buy, "size": filled, "px": px,
                "fee": fee, "realized": realized, "tag": req.tag, "reduce_only": req.reduce_only}
        self.fills.append(fill)
        status = "filled" if filled >= req.size - 1e-12 else "partial"
        return OrderResult(True, status, filled_sz=filled, avg_px=px, fee=fee, extra={"realized": realized})

    def _closed(self, coin, p):
        self.closed.append(dict(p))
        self.pos.pop(coin, None)
        for oid in [o for o, v in self.orders.items() if v["coin"] == coin and v.get("reduce_only")]:
            self.orders.pop(oid, None)

    def cancel(self, coin, order_id):
        o = self.orders.get(int(order_id))
        if o and o["coin"] == coin:
            self.orders.pop(int(order_id))
            return True
        return False

    def open_orders(self, coin=None):
        return [dict(o) for o in self.orders.values() if coin is None or o["coin"] == coin]

    def positions(self):
        out = {}
        for c, p in self.pos.items():
            m = self.market.mark(c) or p["entry_px"]
            d = dict(p)
            d["mark_px"] = m
            d["upnl"] = p["size"] * (m - p["entry_px"])
            d["notional"] = abs(p["size"]) * m
            out[c] = d
        return out

    def balance(self):
        ps = self.positions()
        upnl = sum(p["upnl"] for p in ps.values())
        mu = sum(p["margin"] for p in ps.values())
        return {"account_value": self.cash + mu + upnl, "free": self.cash, "margin_used": mu, "upnl": upnl,
                "notional": sum(p["notional"] for p in ps.values())}

    def funding(self, coin=None):
        if coin:
            return float(self.funding_total.get(coin, 0.0))
        return float(sum(self.funding_total.values()))

    # ---------- eventi di mercato (solo paper) ----------
    def apply_funding(self, rates, oracle_px):
        """Funding orario Hyperliquid: pagamento = size * prezzo oracolo * tasso (long paga se tasso > 0).
        In isolata il funding si scala dal margine della posizione."""
        events = []
        for c, p in list(self.pos.items()):
            r = rates.get(c)
            px = oracle_px.get(c) or self.market.mark(c)
            if r is None or not px:
                continue
            pay = p["size"] * px * r
            p["margin"] -= pay
            p["funding_paid"] = p.get("funding_paid", 0.0) + pay
            self.funding_total[c] = self.funding_total.get(c, 0.0) + pay
            self._recalc_liq(c)
            events.append({"coin": c, "rate": r, "payment": pay})
        return events

    def on_tick(self, now_ms=None):
        """Valuta liquidazioni, trigger e limit in attesa usando il mark price. Ritorna gli eventi."""
        events = []
        for c, p in list(self.pos.items()):
            m = self.market.mark(c)
            if not m or p.get("liq_px") is None:
                continue
            if (p["size"] > 0 and m <= p["liq_px"]) or (p["size"] < 0 and m >= p["liq_px"]):
                events.append(self._liquidate(c, p, m))
        for oid, o in sorted(list(self.orders.items())):
            if oid not in self.orders:
                continue
            c = o["coin"]
            m = self.market.mark(c)
            if not m:
                continue
            if o["order_type"] == "trigger":
                # SL: long -> vende se mark <= trigger; TP: long -> vende se mark >= trigger
                is_sell = not o["is_buy"]
                if o["tpsl"] == "sl":
                    hit = (m <= o["trigger_px"]) if is_sell else (m >= o["trigger_px"])
                else:
                    hit = (m >= o["trigger_px"]) if is_sell else (m <= o["trigger_px"])
                if hit:
                    req = OrderRequest(c, o["is_buy"], o["size"], "market", reduce_only=True, tag=o.get("tag", ""))
                    book = self.market.l2_book(c)
                    mid = (book["asks"][0][0] + book["bids"][0][0]) / 2
                    cap = mid * (1 + self.trigger_slip) if o["is_buy"] else mid * (1 - self.trigger_slip)
                    if c not in self.pos:
                        self.orders.pop(oid, None); continue
                    req.size = min(req.size, abs(self.pos[c]["size"]))
                    res = self._take(req, book, cap)
                    # come su HL lo stop resta attivo se il book non basta: si rimuove solo se la posizione e chiusa
                    if c not in self.pos or o["tpsl"] == "tp" and res.ok:
                        self.orders.pop(oid, None)
                    events.append({"type": "trigger", "oid": oid, "tag": o.get("tag"), "coin": c,
                                   "trigger_px": o["trigger_px"], "result": res.to_dict()})
            elif o["order_type"] == "limit":
                cross = (o["is_buy"] and m <= o["limit_px"]) or ((not o["is_buy"]) and m >= o["limit_px"])
                if cross:
                    req = OrderRequest(c, o["is_buy"], o["size"], "limit", o["limit_px"], o["reduce_only"],
                                       leverage=o.get("leverage"), tag=o.get("tag", ""))
                    self.orders.pop(oid, None)
                    res = self._apply_fill(req, o["size"], o["limit_px"], self.maker)
                    events.append({"type": "limit_fill", "oid": oid, "coin": c, "result": res.to_dict()})
        return events

    def _liquidate(self, coin, p, mark):
        """Liquidazione isolata: si perde tutto il margine della posizione (il resto va al liquidatore)."""
        q = abs(p["size"])
        loss = p["margin"]
        self.realized_total -= loss
        fill = {"ts_ms": self.market.now_ms(), "coin": coin, "is_buy": p["size"] < 0, "size": q,
                "px": p["liq_px"], "fee": 0.0, "realized": -loss, "tag": "LIQUIDATION", "reduce_only": True}
        self.fills.append(fill)
        self._closed(coin, p)
        return {"type": "liquidation", "coin": coin, "mark": mark, "liq_px": p["liq_px"], "loss": loss}
