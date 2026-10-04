"""The new cap must preserve loss recovery and reductions, unlike the old bundle."""

from datetime import datetime, timezone
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

STRATEGIES = Path(__file__).resolve().parents[1] / "strategies"
sys.path.insert(0, str(STRATEGIES))
from NostalgiaForInfinityX8PF import NostalgiaForInfinityX8PF


class X8CostCapTest(unittest.TestCase):
    def setUp(self):
        path = STRATEGIES / "NostalgiaForInfinityX8CostCap.py"
        self.assertTrue(path.exists(), "research cost-cap variant missing")
        spec = spec_from_file_location("research_cost_cap", path)
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        # Sizing callbacks do not need NFI's persistent profit cache or exchange setup.
        self.strategy = module.NostalgiaForInfinityX8CostCap.__new__(module.NostalgiaForInfinityX8CostCap)
        self.strategy.wallets = SimpleNamespace(get_total_stake_amount=lambda: 10000.)

    def adjust(self, result, invested=300., min_stake=10.):
        trade = SimpleNamespace(pair="ETH/USDT", stake_amount=invested)
        with patch.object(NostalgiaForInfinityX8PF, "adjust_trade_position", return_value=result):
            return self.strategy.adjust_trade_position(
                trade, datetime(2026, 10, 4, tzinfo=timezone.utc), 50., -.50,
                min_stake, 1000., 50., 50., -.50, -.50)

    def test_loss_addition_still_runs_below_old_loss_floor(self):
        self.assertEqual(self.adjust((100., "recovery")), (100., "recovery"))

    def test_caps_only_positive_adds_at_remaining_cost_budget(self):
        self.assertEqual(self.adjust((1000., "recovery")), (200., "recovery"))
        self.assertIsNone(self.adjust(20., invested=495.))
        self.assertIsNone(self.adjust(20., invested=600.))

    def test_original_reduction_and_no_action_are_preserved(self):
        self.assertEqual(self.adjust((-100., "trim")), (-100., "trim"))
        self.assertIsNone(self.adjust(None))


if __name__ == "__main__":
    unittest.main()
