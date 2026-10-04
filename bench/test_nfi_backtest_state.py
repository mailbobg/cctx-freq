"""Historical simulations cannot inherit persisted future profit targets."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

from freqtrade.enums import RunMode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "strategies"))
from NostalgiaForInfinityX8PF import NostalgiaForInfinityX8PF
from NostalgiaForInfinityX7PF import NostalgiaForInfinityX7PF


class BacktestStateTest(unittest.TestCase):
    def construct(self, cls, mode, directory):
        return cls({"exchange": {"name": "binance"}, "stake_currency": "USDT",
                    "user_data_dir": directory, "runmode": mode, "bot_name": "state-test",
                    "dry_run": True, "max_open_trades": 6})

    def test_backtest_never_loads_or_overwrites_persisted_profit_targets(self):
        for cls, prefix in ((NostalgiaForInfinityX8PF, "nfix8"), (NostalgiaForInfinityX7PF, "nfix7")):
            with self.subTest(strategy=cls.__name__), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                file = directory / f"{prefix}-profit_max-state-test-binance-USDT-(backtest).json"
                sentinel = {"BTC/USDT": {"time_profit_reached": "2026-07-10T00:35:00+00:00", "profit": .20}}
                file.write_text(json.dumps(sentinel))
                first = self.construct(cls, RunMode.BACKTEST, directory)
                self.assertEqual(first.target_profit_cache.data, {})
                first.target_profit_cache.data["ETH/USDT"] = {"profit": .01}
                first.target_profit_cache.save()
                first.bot_start()
                self.assertEqual(first.target_profit_cache.data, {})
                second = self.construct(cls, RunMode.BACKTEST, directory)
                self.assertEqual(second.target_profit_cache.data, {})
                self.assertEqual(json.loads(file.read_text()), sentinel)

    def test_dry_run_still_loads_its_existing_persistent_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            file = directory / "nfix8-profit_max-state-test-binance-USDT.json"
            sentinel = {"BTC/USDT": {"profit": .20}}
            file.write_text(json.dumps(sentinel))
            strategy = self.construct(NostalgiaForInfinityX8PF, RunMode.DRY_RUN, directory)
            strategy.bot_start()
            self.assertEqual(strategy.target_profit_cache.data, sentinel)


if __name__ == "__main__":
    unittest.main()
