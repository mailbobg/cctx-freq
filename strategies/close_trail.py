"""Trailing stop evaluated on candle closes instead of intra-candle highs.

freqtrade's built-in trailing stop reacts to every price the bot sees. In a backtest that is the candle's
high, which a live bot polling the bid every few seconds often never sees (dry-run reconciliation
2026-09-27, bench/dryrun_recon.md: RARE). This mixin replaces it with a rule that only uses prices both
backtest and live observe:

- activate once the profit at the current rate reaches `trail_offset`;
- afterwards exit as soon as the current rate is `trail_distance` below the highest rate seen since
  activation.

In a backtest the rate given to custom_exit is the candle open (= previous close): run with
`--timeframe-detail 1m` for 1m closes, without it for 5m closes. Offset and distance default to the
strategy's own trailing_stop_positive_offset / trailing_stop_positive.
"""

from datetime import datetime

from freqtrade.persistence import Trade


class CloseTrailMixin:
    trailing_stop = False

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        base = next(c for c in type(self).__mro__ if c is not CloseTrailMixin and "trailing_stop_positive" in vars(c))
        self.trail_offset = vars(base).get("trailing_stop_positive_offset", 0.03)
        self.trail_distance = vars(base)["trailing_stop_positive"]
        self._peaks: dict = {}

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        key = (pair, trade.open_date_utc)
        peak = self._peaks.get(key)
        if peak is not None or current_profit >= self.trail_offset:
            peak = max(peak or 0.0, current_rate)
            self._peaks[key] = peak
            if current_rate <= peak * (1 - self.trail_distance):
                del self._peaks[key]
                return "trail_close"
        return super().custom_exit(pair, trade, current_time, current_rate, current_profit, **kwargs)
