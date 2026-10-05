"""R2b: C plus a 200-day SMA soft filter, BTC + ETH weekly (forward dry-run record).

Research: CCXT-Fr trend-ensemble tree, node "R2b: C + SMA200 soft filter (half size below)", passed all six
pre-registered criteria on 2018-03..2020-12 and 2021-01..2026-09 backtests (engine research/trend_ensemble/engine.py
on the orx/r2b-c-sma200-soft-filter-half-size-below branch). Backtest windows were already seen by earlier work,
so this dry-run is the clean test.
Rule, each Monday from the Sunday daily close, per coin:
  on    = 30-day return > 0
  scale = 1 if close > SMA200(close) else 0.5
  target weight = 0.5 * min(1, 0.60 / sigma30) * on * scale
rebalance a coin only when target and current weight differ by more than 5 percentage points.
Timeframe 1d: the Sunday candle closes at Monday 00:00 UTC and orders go out at that moment.
Standalone copy of RiskHoldC (not a subclass) so later edits to C cannot change this record.
"""

from datetime import datetime
import math

import numpy as np
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy


class RiskHoldR2b(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1d"
    can_short = False
    stoploss = -0.99                 # no stop: exits are the weekly rule
    minimal_roi = {"0": 100}
    trailing_stop = False
    use_exit_signal = True
    process_only_new_candles = True
    startup_candle_count = 230          # SMA200 + margin
    position_adjustment_enable = True
    max_entry_position_adjustment = -1

    lookback, vol_target, cap, band = 30, 0.60, 0.5, 0.05
    sma_len, below_scale = 200, 0.5

    def populate_indicators(self, df: DataFrame, metadata: dict) -> DataFrame:
        df["mom"] = df["close"] / df["close"].shift(self.lookback) - 1
        df["vol"] = np.log(df["close"]).diff().rolling(30).std() * np.sqrt(365)
        df["sma"] = df["close"].rolling(self.sma_len).mean()
        scale = np.where(df["close"] > df["sma"], 1.0, self.below_scale)
        df["weight"] = np.where(df["mom"] > 0, self.cap * np.minimum(1, self.vol_target / df["vol"]) * scale, 0.0)
        df.loc[df["sma"].isna(), "weight"] = 0.0          # not enough history: stay out
        df["decide"] = df["date"].dt.weekday == 6          # Sunday candle: decision at its close (Monday 00:00 UTC)
        return df

    def populate_entry_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        df.loc[df["decide"] & (df["weight"] > 0), ["enter_long", "enter_tag"]] = (1, "weekly_on")
        return df

    def populate_exit_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        df.loc[df["decide"] & (df["weight"] <= 0), ["exit_long", "exit_tag"]] = (1, "weekly_off")
        return df

    def _target(self, pair: str) -> float | None:
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if df is None or df.empty or not bool(df["decide"].iloc[-1]):
            return None                                     # only act on the Sunday close
        return float(df["weight"].iloc[-1])

    def _equity(self, pair: str | None = None, current_rate: float | None = None) -> float | None:
        """Spot equity, including unrealized P&L; unavailable quotes block sizing.

        Use callback price for the current pair, live bids for other positions,
        and only analyzed (closed) candles in backtests. Never fetch live quotes
        from a historical simulation.
        """
        value = self.wallets.get_total(self.config.get("stake_currency", "USDT"))
        for trade in Trade.get_trades_proxy(is_open=True):
            if not trade.amount:
                continue
            if trade.pair == pair:
                price = current_rate
            elif self.dp.runmode.value in ("live", "dry_run"):
                price = self.dp.ticker(trade.pair).get("bid")
            else:
                df, _ = self.dp.get_analyzed_dataframe(trade.pair, self.timeframe)
                price = None if df.empty else df["close"].iloc[-1]
            if price is None or not math.isfinite(price) or price <= 0:
                return None
            value += trade.amount * price
        value *= self.config.get("tradable_balance_ratio", 1.0)
        return value if math.isfinite(value) and value > 0 else None

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float, proposed_stake: float,
                            min_stake: float | None, max_stake: float, leverage: float, entry_tag: str | None,
                            side: str, **kwargs) -> float:
        w = self._target(pair)
        equity = self._equity(pair, current_rate) if w else None
        stake = min(max_stake, equity * w) if equity else 0.0
        return stake if stake >= (min_stake or 0.0) else 0.0

    def adjust_trade_position(self, trade: Trade, current_time: datetime, current_rate: float, current_profit: float,
                              min_stake: float | None, max_stake: float, current_entry_rate: float,
                              current_exit_rate: float, current_entry_profit: float, current_exit_profit: float,
                              **kwargs):
        w = self._target(trade.pair)
        if w is None or w <= 0:
            return None
        # once per week: skip if an order was already filled since this Monday 00:00 UTC
        monday = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
        if trade.date_last_filled_utc >= monday:
            return None
        equity = self._equity(trade.pair, current_rate)
        if equity is None:
            return None
        current_value = trade.amount * current_rate
        diff = equity * w - current_value
        if abs(diff) <= self.band * equity:
            return None
        if diff < 0:
            # Negative stake is a cost-basis amount, not sale proceeds.
            return max(diff / current_value, -0.999) * trade.stake_amount, "weekly_trim"
        stake = min(diff, max_stake)
        return (stake, "weekly_add") if stake >= (min_stake or 0.0) and stake > 0 else None
