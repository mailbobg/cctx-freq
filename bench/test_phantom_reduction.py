"""Liquidity limits must apply to sale notional, not negative cost-basis stake."""

from datetime import datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "strategies"))
from phantom_fill import PhantomFillMixin


class OriginalAdjustment:
    def adjust_trade_position(self, *args, **kwargs):
        return self.original


class SizedAdjustment(PhantomFillMixin, OriginalAdjustment):
    pass


class ReductionTest(unittest.TestCase):
    def reduced(self, amount, stake, rate, cap, request):
        strategy = SizedAdjustment()
        strategy.original = (request, "trim")
        strategy._fillable = lambda *args: cap
        trade = SimpleNamespace(pair="ETH/USDT", amount=amount, stake_amount=stake)
        result = strategy.adjust_trade_position(
            trade, datetime(2026, 10, 4, tzinfo=timezone.utc), rate, 0., 10., 1000.)
        return result, abs(result[0]) * amount / stake * rate

    def test_appreciated_sale_cannot_exceed_candle_volume_share(self):
        result, sold = self.reduced(1., 100., 300., 100., -100.)
        self.assertAlmostEqual(sold, 100.)
        self.assertEqual(result[1], "trim")

    def test_cost_conversion_below_entry_uses_full_liquidity_limit(self):
        result, sold = self.reduced(4., 100., 25., 20., -50.)
        self.assertAlmostEqual(sold, 20.)
        self.assertAlmostEqual(result[0], -20.)

    def test_notional_limit_also_applies_to_leveraged_quantity(self):
        result, sold = self.reduced(10., 100., 20., 100., -100.)
        self.assertAlmostEqual(sold, 100.)
        self.assertAlmostEqual(result[0], -50.)


if __name__ == "__main__":
    unittest.main()
