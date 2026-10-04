"""E0V1ENWTA2: WTA (tag-weighted sizing + relaxed buy_new exit) + X12 crash guard.

X12's guard, unchanged (see specs/2026-09-27-x12-dipbuy-crash-guard-design.md):
  A. at most 2 new entries in any rolling 60 minutes (confirm_trade_entry).
  B. StoplossGuard: pair locked 24h after a stop-loss, on top of the 8h CooldownPeriod.
Run ALL backtests with --enable-protections (the dry-run runs them; without
the flag every number is overstated).

Acceptance (mirrors X12): worst MTM drawdown <= 25% across windows, every
window > 0, beats X12 on chained return and beats the de-leveraged control.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from E0V1ENWTA import E0V1ENWTA  # noqa: E402
from freqtrade.persistence import Trade  # noqa: E402


class E0V1ENWTA2(E0V1ENWTA):
    # v3: 2.0x doubles concentrated too much into crash legs (2022 DD 24.97%,
    # at the 25% limit; doubles fire hardest exactly when both dip conditions
    # trigger simultaneously = crash legs). 1.5x keeps the tilt, halves the tail.
    stake_double = 1.5
    max_entries = 2
    window_minutes = 60

    @property
    def protections(self):
        return super().protections + [
            {
                "method": "StoplossGuard",
                "lookback_period_candles": 288,
                "trade_limit": 1,
                "stop_duration_candles": 288,
                "only_per_pair": True,
            }
        ]

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time: datetime, entry_tag: str | None,
                            side: str, **kwargs) -> bool:
        since = current_time - timedelta(minutes=self.window_minutes)
        recent = sum(1 for t in Trade.get_trades_proxy() if t.open_date_utc > since)
        if recent >= self.max_entries:
            return False
        return super().confirm_trade_entry(pair, order_type, amount, rate, time_in_force,
                                           current_time, entry_tag, side, **kwargs)
