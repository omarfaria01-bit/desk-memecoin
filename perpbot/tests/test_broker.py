"""Test offline (API finta) della matematica del PaperBroker e del rischio.
Esegui:  ./venv/bin/python -m unittest discover -s tests -v"""
import os, sys, random, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from perp.paper_broker import PaperBroker, walk_book, liq_price
from perp.exchange import OrderRequest, make_broker, HARD_MAX_LEVERAGE
from perp import risk as RK, strategy as S
from perp.config import clamp


class FakeMarket:
    def __init__(self, mid=100.0, max_lev=20, tiers=None):
        self.m = mid
        self.info = {"maxLev": max_lev, "szDecimals": 3, "tiers": tiers or [(0.0, max_lev)], "delisted": False}
        self.t = 1_700_000_000_000

    def book(self):
        m = self.m
        return {"bids": [(m - 0.05, 2.0), (m - 0.10, 3.0), (m - 0.50, 100.0)],
                "asks": [(m + 0.05, 2.0), (m + 0.10, 3.0), (m + 0.50, 100.0)]}

    def l2_book(self, coin): return self.book()
    def mark(self, coin): return self.m
    def asset_info(self, coin): return self.info
    def now_ms(self): return self.t


CFG = {"start_cash": 1000.0, "fees": {"taker": 0.00045, "maker": 0.00015},
       "paper": {"latency_ms": [0, 0], "reject_prob": 0.0}, "risk": {"max_leverage": 10}}


def mk(mkt=None, **kw):
    cfg = {**CFG, "paper": {**CFG["paper"], **kw}}
    return PaperBroker(cfg, mkt or FakeMarket(), rng=random.Random(1), sleep=lambda s: None)


class TestBroker(unittest.TestCase):
    def test_walk_book_slippage(self):
        f, px = walk_book([(100.05, 2.0), (100.10, 3.0), (100.5, 100)], 4.0)
        self.assertAlmostEqual(f, 4.0)
        self.assertAlmostEqual(px, (2 * 100.05 + 2 * 100.10) / 4)
        f, px = walk_book([(100.05, 2.0), (100.10, 3.0)], 10.0, limit_px=100.07)
        self.assertAlmostEqual(f, 2.0)  # fill parziale entro il limite

    def test_market_fill_and_taker_fee(self):
        b = mk()
        r = b.place_order(OrderRequest("X", True, 4.0, "market", leverage=5))
        self.assertTrue(r.ok)
        avg = (2 * 100.05 + 2 * 100.10) / 4
        self.assertAlmostEqual(r.avg_px, avg)
        self.assertAlmostEqual(r.fee, 4 * avg * 0.00045)
        self.assertAlmostEqual(b.cash, 1000 - 4 * avg / 5 - r.fee)
        self.assertAlmostEqual(b.balance()["account_value"], 1000 - r.fee + 4 * (100 - avg))

    def test_close_pnl(self):
        m = FakeMarket(); b = mk(m)
        r1 = b.place_order(OrderRequest("X", True, 1.0, "market", leverage=5))
        m.m = 110.0
        r2 = b.place_order(OrderRequest("X", False, 1.0, "market", reduce_only=True))
        self.assertEqual(b.pos, {})
        self.assertAlmostEqual(b.cash, 1000 + (109.95 - 100.05) - r1.fee - r2.fee)

    def test_maker_fee_on_resting_limit(self):
        m = FakeMarket(); b = mk(m)
        r = b.place_order(OrderRequest("X", True, 1.0, "limit", limit_px=99.0, leverage=3))
        self.assertEqual(r.status, "resting")
        m.m = 98.9
        ev = b.on_tick()
        self.assertEqual(ev[0]["type"], "limit_fill")
        self.assertAlmostEqual(ev[0]["result"]["fee"], 99.0 * 0.00015)

    def test_funding_long_pays_short_receives(self):
        b = mk()
        b.place_order(OrderRequest("X", True, 2.0, "market", leverage=4))
        m0 = b.pos["X"]["margin"]
        ev = b.apply_funding({"X": 0.0001}, {"X": 100.0})
        self.assertAlmostEqual(ev[0]["payment"], 2 * 100 * 0.0001)
        self.assertAlmostEqual(b.pos["X"]["margin"], m0 - 0.02)
        b2 = mk()
        b2.place_order(OrderRequest("X", False, 2.0, "market", leverage=4))
        ev = b2.apply_funding({"X": 0.0001}, {"X": 100.0})
        self.assertAlmostEqual(ev[0]["payment"], -0.02)

    def test_liquidation_price_formula(self):
        # long 10x, mmr = 1/(2*20) = 2.5%: liq = (E - M/q)/(1-mmr)
        E, q, lev, mmr = 100.0, 1.0, 10, 0.025
        M = E * q / lev
        lp = liq_price(1, E, q, M, mmr)
        self.assertAlmostEqual(lp, (100 - 10) / 0.975)
        # al prezzo di liquidazione: margine + pnl == mmr * nozionale
        self.assertAlmostEqual(M + q * (lp - E), mmr * q * lp)
        ls = liq_price(-1, E, q, M, mmr)
        self.assertAlmostEqual(M + q * (E - ls), mmr * q * ls)

    def test_liquidation_event_loses_margin(self):
        m = FakeMarket(); b = mk(m)
        b.place_order(OrderRequest("X", True, 1.0, "market", leverage=10))
        lp = b.pos["X"]["liq_px"]; margin = b.pos["X"]["margin"]
        cash0 = b.cash
        m.m = lp - 0.01
        ev = b.on_tick()
        self.assertEqual(ev[0]["type"], "liquidation")
        self.assertAlmostEqual(ev[0]["loss"], margin)
        self.assertAlmostEqual(b.cash, cash0)
        self.assertEqual(b.pos, {})

    def test_leverage_hard_cap(self):
        b = mk(FakeMarket(max_lev=50))
        r = b.place_order(OrderRequest("X", True, 1.0, "market", leverage=11))
        self.assertFalse(r.ok)
        self.assertIn("leva", r.reason)
        b2 = mk(FakeMarket(max_lev=5))
        self.assertFalse(b2.place_order(OrderRequest("X", True, 1.0, "market", leverage=6)).ok)
        self.assertTrue(b.place_order(OrderRequest("X", True, 1.0, "market", leverage=10)).ok)
        self.assertEqual(HARD_MAX_LEVERAGE, 10)
        self.assertEqual(clamp("risk", "max_leverage", 50)[0], 10)

    def test_insufficient_margin(self):
        b = mk()
        r = b.place_order(OrderRequest("X", True, 200.0, "market", leverage=2))  # 20k nozionale, 10k margine
        self.assertFalse(r.ok)

    def test_trigger_stop(self):
        m = FakeMarket(); b = mk(m)
        b.place_order(OrderRequest("X", True, 1.0, "market", leverage=5))
        r = b.place_order(OrderRequest("X", False, 1.0, "trigger", reduce_only=True, trigger_px=97.0, tpsl="sl", tag="sl"))
        self.assertEqual(r.status, "resting")
        m.m = 98.0; self.assertEqual(b.on_tick(), [])
        m.m = 96.9
        ev = b.on_tick()
        self.assertEqual(ev[0]["type"], "trigger")
        self.assertEqual(b.pos, {})
        self.assertEqual(b.orders, {})

    def test_simulated_rejects(self):
        b = mk(reject_prob=1.0)
        r = b.place_order(OrderRequest("X", True, 1.0, "market", leverage=5))
        self.assertFalse(r.ok); self.assertIn("simulato", r.reason)

    def test_live_broker_blocked(self):
        os.environ["PERP_PAPER_ONLY"] = "1"
        cfg = {**CFG, "MODE": "live", "LIVE_ENABLED": True}
        self.assertEqual(make_broker(cfg, FakeMarket()).name, "paper")
        from perp.live_broker import HyperliquidLiveBroker
        with self.assertRaises(RuntimeError):
            HyperliquidLiveBroker(cfg)


class TestRisk(unittest.TestCase):
    def test_sizing_risk_and_stop_before_liq(self):
        for stop_pct, mmr, amax in ((0.5, 0.0125, 40), (1.0, 0.025, 20), (3.0, 0.05, 10), (8.0, 0.1, 5), (0.2, 0.0125, 40)):
            E = 100.0
            stop = E * (1 - stop_pct / 100)
            sz = RK.size_position(1000, E, stop, mmr, amax, {})
            self.assertLessEqual(sz["leverage"], 10)
            self.assertLessEqual(sz["leverage"], amax)
            lp = liq_price(1, E, sz["notional"] / E, sz["notional"] / sz["leverage"], mmr)
            self.assertLess(lp, stop, f"liq {lp} non oltre lo stop {stop}")
            self.assertTrue(RK.liq_safe(1, stop, lp, E, 1.5))
            # rischio allo stop ~1% equity (meno se limitato dal margine)
            self.assertLessEqual(sz["risk_usd"], 10.0 + 1e-6)
            lps = liq_price(-1, E, sz["notional"] / E, sz["notional"] / sz["leverage"], mmr)
            self.assertGreater(lps, E * (1 + stop_pct / 100))

    def test_portfolio_limits(self):
        pos = {"A": {"side": 1, "notional": 1000}, "B": {"side": 1, "notional": 1000}, "C": {"side": -1, "notional": 500}}
        ok, why, n = RK.can_open("D", 1, 1000, pos, 1000, 1000, {})
        self.assertTrue(ok); self.assertAlmostEqual(n, 500)  # 3x - 2500 = 500
        pos["E"] = {"side": 1, "notional": 100}
        self.assertFalse(RK.can_open("D", 1, 100, pos, 1000, 1000, {})[0])  # max 4
        self.assertFalse(RK.can_open("D", 1, 100, {}, 949, 1000, {})[0])  # -5% giornaliero
        self.assertFalse(RK.can_open("D", 1, 100, {}, 1000, 1000, {}, flags={"stop": True})[0])

    def test_correlation_guard(self):
        import math
        base = [100 + 10 * math.sin(i / 5) + i * 0.1 for i in range(200)]
        closes = {"A": base, "B": [x * 2 for x in base], "C": [x * 3 + 1 for x in base]}
        pos = {"A": {"side": 1, "notional": 100}, "B": {"side": 1, "notional": 100}}
        ok, why, _ = RK.can_open("C", 1, 100, pos, 1000, 1000, {}, closes)
        self.assertFalse(ok); self.assertIn("correlazione", why)
        self.assertTrue(RK.can_open("C", -1, 100, pos, 1000, 1000, {}, closes)[0])


class TestStrategy(unittest.TestCase):
    def test_trailing_only_moves_favourably(self):
        row = {"atr1h": 1.0, "close_t": 0}
        pl = S.initial_exit_plan(100, 1, row, {})
        self.assertAlmostEqual(pl["stop"], 98.5)
        S.trail_update(pl, 103, 101, 1.0, {})
        self.assertAlmostEqual(pl["stop"], 100.0)  # 103 - 3*1
        S.trail_update(pl, 101, 99, 1.0, {})
        self.assertAlmostEqual(pl["stop"], 100.0)


if __name__ == "__main__":
    unittest.main()
