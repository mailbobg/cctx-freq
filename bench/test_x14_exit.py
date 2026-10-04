"""Exercise the real Freqtrade exit dispatcher, including bot overrides."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import unittest

from freqtrade.persistence import Trade

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "strategies"))
from HedgedFlushX14 import HedgedFlushX14


class X14ExitTest(unittest.TestCase):
    def dispatch(self, hours, rate, short=False, override=None):
        strategy = HedgedFlushX14({"stake_currency": "USDT"})
        # StrategyResolver normalizes JSON ROI keys before runtime dispatch.
        strategy.minimal_roi = {int(k): v for k, v in strategy.minimal_roi.items()}
        if override is not None:
            strategy.use_exit_signal = override
        now = datetime(2026, 10, 4, tzinfo=timezone.utc)
        trade = Trade(pair="BTC/USDT:USDT", amount=1, stake_amount=100,
                      open_rate=100, open_date=now - timedelta(hours=hours),
                      fee_open=0, fee_close=0, is_short=short, leverage=1)
        return [r.exit_reason for r in strategy.should_exit(
            trade, rate, now, enter=False, exit_=False)]

    def test_both_legs_close_at_24_hours_through_real_dispatcher(self):
        for short in (False, True):
            with self.subTest(short=short):
                self.assertIn("time", self.dispatch(24, 100, short))

    def test_long_loss_rule_runs_before_timeout(self):
        self.assertIn("stop", self.dispatch(1, 74))
        self.assertEqual(self.dispatch(1, 80), [])
        self.assertEqual(self.dispatch(1, 126, short=True), [])

    def test_all_venue_configurations_allow_custom_exits(self):
        for name in ("x14", "x14-bybit", "x14-bitget"):
            cfg = json.loads((ROOT / "bots" / f"{name}.json").read_text())
            with self.subTest(bot=name):
                self.assertIn("time", self.dispatch(24, 100, override=cfg["use_exit_signal"]))


if __name__ == "__main__":
    unittest.main()
