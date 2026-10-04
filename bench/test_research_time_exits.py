"""Unconfigured research classes must execute their documented holding deadlines."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

from freqtrade.persistence import Trade

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "strategies"))
from BBBreakoutBTC import BBBreakoutBTC
from FlushReboundX11 import FlushReboundX11
from FundingReversionX9b import FundingReversionX9b
from XSMomentumX10 import XSMomentumX10


class DeadlineTest(unittest.TestCase):
    def test_all_documented_deadlines_execute_without_config_override(self):
        cases = [(BBBreakoutBTC, timedelta(minutes=1075), "time_1075m"),
                 (FlushReboundX11, timedelta(hours=24), "time_exit"),
                 (FundingReversionX9b, timedelta(hours=16), "time_exit"),
                 (XSMomentumX10, timedelta(days=7), "rebalance")]
        now = datetime(2026, 10, 4, tzinfo=timezone.utc)
        for cls, age, reason in cases:
            with self.subTest(strategy=cls.__name__):
                strategy = cls({"stake_currency": "USDT"})
                strategy.minimal_roi = {int(k): v for k, v in strategy.minimal_roi.items()}
                trade = Trade(pair="BTC/USDT", amount=1, stake_amount=100, open_rate=100,
                              open_date=now-age, fee_open=0, fee_close=0, is_short=False, leverage=1)
                before = strategy.should_exit(trade, 100, now-timedelta(minutes=1), enter=False, exit_=False)
                self.assertEqual(before, [])
                at = strategy.should_exit(trade, 100, now, enter=False, exit_=False)
                self.assertIn(reason, [r.exit_reason for r in at])


if __name__ == "__main__":
    unittest.main()
