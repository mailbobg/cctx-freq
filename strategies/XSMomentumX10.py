"""X10: weekly cross-sectional momentum on USDT perps, inverse-vol sized, market neutral.

Pre-registration and acceptance criteria:
user_data/bench/specs/2026-09-25-x10-xs-momentum-design.md

Every Monday 00:00 UTC: rank the universe by the 21-day return ending yesterday
(skipping the most recent day), long the top quintile, short the bottom
quintile, each side 45% of equity split by inverse volatility, hold 7 days.
No ROI, no trailing stop, no DCA; a −25% disaster stop only.

Daily candles: the row dated D closes at D+1 00:00 and its signal is executed
at that open. The rebalance rows are therefore the Sunday rows, and the
momentum on row D uses closes up to D−1 (yesterday relative to the execution
day, minus the skipped day).
"""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy


class XSMomentumX10(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1d"
    can_short = True
    stoploss = -0.25
    minimal_roi = {"0": 100}
    use_exit_signal = False
    process_only_new_candles = True
    startup_candle_count = 60

    # pre-registered parameters (the neighbourhood check overrides them through an "x10" config block)
    lookback_days = 21
    skip_days = 1
    quantile = 0.20
    vol_days = 30
    side_exposure = 0.45
    max_position = 0.08
    hold_days = 7
    rebalance_weekday = 0  # Monday (execution day)

    order_types = {"entry": "market", "exit": "market", "stoploss": "market", "stoploss_on_exchange": False}

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        for key, value in config.get("x10", {}).items():
            if not hasattr(self, key):
                raise ValueError(f"unknown x10 parameter {key}")
            setattr(self, key, value)
        self._panel_key = None
        self._rank: DataFrame | None = None      # date × pair → percentile rank of momentum
        self._weight: DataFrame | None = None    # date × pair → stake weight (fraction of equity), signed

    def leverage(self, pair, current_time, current_rate, proposed_leverage, max_leverage, entry_tag, side, **kwargs) -> float:
        return 1.0

    # ------------------------------------------------------------------ cross-sectional panel
    def _build_panel(self) -> None:
        """Momentum rank and inverse-vol weights for every pair and date, computed once per data set."""
        pairs = self.dp.current_whitelist()
        closes = {}
        for pair in pairs:
            df = self.dp.get_pair_dataframe(pair, self.timeframe)
            if df is not None and not df.empty:
                closes[pair] = df.set_index("date")["close"]
        close = DataFrame(closes).sort_index()
        key = (tuple(sorted(closes)), close.index[-1] if len(close) else None, len(close))
        if key == self._panel_key:
            return
        mom = close.shift(self.skip_days) / close.shift(self.skip_days + self.lookback_days) - 1
        vol = np.log(close).diff().rolling(self.vol_days).std() * np.sqrt(365)
        rank = mom.rank(axis=1, pct=True)
        inv_vol = 1.0 / vol.replace(0, np.nan)
        long_mask = rank >= 1 - self.quantile
        short_mask = rank <= self.quantile
        long_w = inv_vol.where(long_mask)
        short_w = inv_vol.where(short_mask)
        long_w = long_w.div(long_w.sum(axis=1), axis=0) * self.side_exposure
        short_w = short_w.div(short_w.sum(axis=1), axis=0) * self.side_exposure
        weight = long_w.fillna(0) - short_w.fillna(0)
        weight = weight.clip(-self.max_position, self.max_position)
        self._rank, self._weight, self._panel_key = rank, weight, key

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        self._build_panel()
        pair = metadata["pair"]
        rank = self._rank[pair] if pair in self._rank else pd.Series(dtype=float)
        weight = self._weight[pair] if pair in self._weight else pd.Series(dtype=float)
        dataframe = dataframe.merge(rank.rename("mom_rank"), left_on="date", right_index=True, how="left")
        dataframe = dataframe.merge(weight.rename("weight"), left_on="date", right_index=True, how="left")
        # the row dated D is executed at D+1 00:00; rebalance when D+1 is the rebalance weekday
        dataframe["rebalance"] = (dataframe["date"] + pd.Timedelta(days=1)).dt.weekday == self.rebalance_weekday
        return dataframe

    # ------------------------------------------------------------------ signals
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[dataframe["rebalance"] & (dataframe["weight"] > 0), ["enter_long", "enter_tag"]] = (1, "xs_top")
        dataframe.loc[dataframe["rebalance"] & (dataframe["weight"] < 0), ["enter_short", "enter_tag"]] = (1, "xs_bottom")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float, current_profit: float, **kwargs):
        if current_time - trade.open_date_utc >= timedelta(days=self.hold_days):
            return "rebalance"
        return None

    # ------------------------------------------------------------------ sizing
    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float, proposed_stake: float,
                            min_stake: float | None, max_stake: float, leverage: float, entry_tag: str | None,
                            side: str, **kwargs) -> float:
        signal_date = pd.Timestamp(current_time).floor("D") - pd.Timedelta(days=1)
        try:
            w = float(self._weight.at[signal_date, pair])
        except (KeyError, TypeError, ValueError):
            return 0.0
        if (side == "long" and w <= 0) or (side == "short" and w >= 0):
            return 0.0
        equity = self.wallets.get_total_stake_amount()
        stake = abs(w) * equity
        return min(stake, max_stake)
