"""X12: E0V1ENPF plus a crash guard.

Pre-registration and acceptance criteria:
user_data/bench/specs/2026-09-27-x12-dipbuy-crash-guard-design.md

A. at most `max_entries` new entries in any rolling `window_minutes` across all pairs;
B. a pair that exits on its stop-loss is locked for 24h (StoplossGuard, per pair),
   on top of E0V1EN's own 8h CooldownPeriod. Run backtests with --enable-protections.
Signals, exits, stop and sizing are E0V1ENPF's, unchanged.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from E0V1ENPF import E0V1ENPF  # noqa: E402

from freqtrade.persistence import Trade  # noqa: E402


class E0V1ENX12(E0V1ENPF):
    max_entries = 2
    window_minutes = 60

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        # the pre-registered variant (a) overrides these through an "x12" config block
        for key, value in config.get("x12", {}).items():
            if key not in ("max_entries", "window_minutes"):
                raise ValueError(f"unknown x12 parameter {key}")
            setattr(self, key, value)

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

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float, time_in_force: str,
                            current_time: datetime, entry_tag: str | None, side: str, **kwargs) -> bool:
        since = current_time - timedelta(minutes=self.window_minutes)
        recent = sum(1 for t in Trade.get_trades_proxy() if t.open_date_utc > since)
        if recent >= self.max_entries:
            return False
        return super().confirm_trade_entry(pair, order_type, amount, rate, time_in_force, current_time,
                                           entry_tag, side, **kwargs)
