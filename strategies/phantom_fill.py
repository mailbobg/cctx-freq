"""Liquidity-aware ("phantom fill") sizing mixin for backtests.

A standard backtest fills any order at the candle price regardless of volume.
This mixin caps every entry and every position adjustment at a share of the
last closed candle's quote volume and drops orders that would fall below a
minimum notional. Mix it in front of the real strategy:

    class NostalgiaForInfinityX7PF(PhantomFillMixin, NostalgiaForInfinityX7):
        pass

Idea and the 10% figure: nateemma/strategies, us_spot_market_study.md.
"""

from datetime import datetime

from freqtrade.persistence import Trade


class PhantomFillMixin:
    max_volume_share = 0.10  # of the last closed candle's quote volume
    min_notional = 10.0      # USDT; smaller orders are dropped

    def _fillable(self, pair: str, current_time: datetime) -> float:
        """Quote volume of the last candle closed before current_time, times the share."""
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if df is None or df.empty:
            return 0.0
        closed = df[df["date"] < current_time]
        if closed.empty:
            return 0.0
        row = closed.iloc[-1]
        return float(row["volume"] * row["close"]) * self.max_volume_share

    def _cap(self, stake: float, pair: str, current_time: datetime, min_stake: float | None) -> float:
        """Cap a positive stake; return 0 when the fillable size is below the minimums."""
        cap = self._fillable(pair, current_time)
        capped = min(stake, cap)
        floor = max(self.min_notional, min_stake or 0.0)
        return capped if capped >= floor else 0.0

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float, proposed_stake: float,
                            min_stake: float | None, max_stake: float, leverage: float, entry_tag: str | None,
                            side: str, **kwargs) -> float:
        stake = super().custom_stake_amount(pair, current_time, current_rate, proposed_stake, min_stake, max_stake,
                                            leverage, entry_tag, side, **kwargs)
        return self._cap(stake, pair, current_time, min_stake)

    def adjust_trade_position(self, trade: Trade, current_time: datetime, *args, **kwargs):
        result = super().adjust_trade_position(trade, current_time, *args, **kwargs)
        if result is None:
            return None
        stake, tag = result if isinstance(result, tuple) else (result, None)
        if stake is None:
            return result
        min_stake = kwargs.get("min_stake") if "min_stake" in kwargs else (args[2] if len(args) > 2 else None)
        if stake > 0:
            stake = self._cap(stake, trade.pair, current_time, min_stake)
            if stake == 0:
                return None
        else:
            cap = self._fillable(trade.pair, current_time)
            if cap < self.min_notional:
                return None
            stake = -min(-stake, cap)
        return (stake, tag) if isinstance(result, tuple) else stake
