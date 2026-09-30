"""Live dry-run test of ShortNewListingsL2 (not a strategy to trade): PONS is treated as listed at D0 = today - 3 days,
so its entry window is open now; the hold is 90 minutes so the whole lifecycle (entry, BTC hedge, time exit, hedge flat)
can be watched in a few hours. Everything else is the production code path."""
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/freqtrade/user_data/strategies")
import ShortNewListingsL2 as mod
from ShortNewListingsL2 import ShortNewListingsL2

mod.HOLD = timedelta(minutes=90)
TEST_PAIR = "PONS/USDT:USDT"


class L2LiveTest(ShortNewListingsL2):
    def _onboard(self, pair):
        if pair == TEST_PAIR:
            d = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=3)
            return d + timedelta(hours=11, minutes=45)
        return super()._onboard(pair)

    @staticmethod
    def _history_ok(dataframe, onboard):
        return True                 # PONS has 24 days of real history; the check itself is exercised by the backtest
