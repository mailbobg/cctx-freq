"""Live dry-run harness for UnlockXS (not a strategy to trade). Today (UTC) is treated as the Monday; SUI and ADA get a huge
fake unlock tomorrow so they are selected whatever the real schedule says; 40 minutes after the first decision the weekly
selection is switched to empty so the drop-exit and the BTC hedge close can be watched. The decision, entry, sizing, hedge
and exit code is the production code."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, "/freqtrade/user_data/strategies")
import UnlockXS as mod
from UnlockXS import UnlockXS

mod.AUDIT = Path("/freqtrade/user_data/unlock_xs_livetest.jsonl")


class U2LiveTest(UnlockXS):
    SWITCH_AFTER = timedelta(minutes=40)

    def __init__(self, config):
        super().__init__(config)
        tomorrow = int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp())
        for pair in ("SUI/USDT:USDT", "ADA/USDT:USDT"):
            self._schedule[pair] = (np.array([tomorrow]), np.array([1e13]))
        self._t0 = None
        self._switched_at = None

    def _decision_day(self, now):
        return self._switched_at or now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    def bot_loop_start(self, current_time, **kwargs):
        if self._switched_at is None and self._week is not None:
            self._t0 = self._t0 or current_time
            if current_time - self._t0 >= self.SWITCH_AFTER:
                self._switched_at = current_time
                self._selected, self._week = {}, current_time
                return
        super().bot_loop_start(current_time, **kwargs)
