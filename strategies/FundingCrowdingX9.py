"""X9: funding-rate crowding reversal on USDT perpetuals.

Design and pre-registered acceptance criteria:
user_data/bench/specs/2026-09-25-x9-funding-crowding-design.md

Once a day (1d candles) we look at each perp's own funding-rate history:
  * f_ann    : funding collected over the last 24h, annualised
  * crowding : percentile of the trailing 7-day cumulative funding within the
               trailing 90 days (crowding relative to the coin's own history)
Short crowded longs (high f_ann, crowding in the top decile) and long crowded
shorts (negative f_ann, crowding in the bottom decile), equal size, 1x, no DCA.
Exit when the crowding resolves (percentile back through 0.5), after 7 days,
or at −8%.

Funding settles every 8h on most Binance perps but every 4h (or 1h) on some,
so all windows are time based, not row based.
"""

from datetime import datetime, timedelta

import pandas as pd
from pandas import DataFrame

from freqtrade.enums import CandleType
from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy


# freqtrade stores funding-rate history under its `funding_fee_timeframe` label ("1h" on Binance);
# the rows themselves are spaced by the pair's real funding interval.
FUNDING_TF = "1h"


class FundingCrowdingX9(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1d"
    can_short = True
    stoploss = -0.08
    minimal_roi = {"0": 10}  # never exit on ROI; exits come from signals, the time stop or the stoploss
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = True
    process_only_new_candles = True
    startup_candle_count = 100  # 90 days of funding history + buffer

    # thresholds (see spec; the neighbourhood check varies these)
    short_funding_ann = 0.30
    long_funding_ann = -0.10
    short_crowding_pct = 0.90
    long_crowding_pct = 0.10
    exit_crowding_pct = 0.50
    max_hold_days = 7
    max_per_side = 5
    max_per_side_if_other_empty = 3

    order_types = {
        "entry": "market",
        "exit": "market",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        # the neighbourhood check overrides thresholds through an "x9" block in an extra config file
        for key, value in config.get("x9", {}).items():
            if not hasattr(self, key):
                raise ValueError(f"unknown x9 parameter {key}")
            setattr(self, key, value)

    def informative_pairs(self):
        return [(pair, FUNDING_TF, CandleType.FUNDING_RATE) for pair in self.dp.current_whitelist()]

    def leverage(self, pair, current_time, current_rate, proposed_leverage, max_leverage, entry_tag, side, **kwargs) -> float:
        return 1.0

    # ---------------------------------------------------------------- features
    def funding_features(self, pair: str) -> DataFrame:
        """Daily funding features. The row for day D uses only fundings settled up to
        and including D+1 00:00, which is the close of candle D."""
        fr = self.dp.get_pair_dataframe(pair, FUNDING_TF, candle_type=CandleType.FUNDING_RATE)
        if fr is None or fr.empty:
            return DataFrame(columns=["date", "f_ann", "crowding"])
        s = fr.set_index("date")["funding_rate"].astype(float).sort_index()
        f_ann = s.rolling("24h").sum() * 365
        cum7 = s.rolling("7D").sum()
        crowding = cum7.rolling("90D", min_periods=45 * 3).rank(pct=True)
        feat = DataFrame({"f_ann": f_ann, "crowding": crowding}).reset_index()
        # the candle dated D closes at D+1 00:00. Only fundings settled strictly before that close are
        # used (the one settled exactly at D+1 00:00 is treated as not yet available), so the latest
        # funding attached to row D is the D 16:00 one: shift by 23h and merge backwards.
        feat["date"] = feat["date"] - pd.Timedelta(hours=23)
        return feat

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        feat = self.funding_features(metadata["pair"])
        feat["date"] = feat["date"].astype(dataframe["date"].dtype)  # candle dates are [ms], funding dates [us]
        dataframe = pd.merge_asof(
            dataframe.sort_values("date"),
            feat.sort_values("date"),
            on="date",
            direction="backward",
            tolerance=pd.Timedelta(hours=8),
        )
        dataframe["prev_high"] = dataframe["high"].shift(1)
        dataframe["prev_low"] = dataframe["low"].shift(1)
        return dataframe

    # ---------------------------------------------------------------- signals
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["f_ann"] > self.short_funding_ann)
            & (dataframe["crowding"] > self.short_crowding_pct)
            & (dataframe["close"] < dataframe["prev_high"]),
            ["enter_short", "enter_tag"],
        ] = (1, "crowded_long")
        dataframe.loc[
            (dataframe["f_ann"] < self.long_funding_ann)
            & (dataframe["crowding"] < self.long_crowding_pct)
            & (dataframe["close"] > dataframe["prev_low"]),
            ["enter_long", "enter_tag"],
        ] = (1, "crowded_short")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[dataframe["crowding"] < self.exit_crowding_pct, "exit_short"] = 1
        dataframe.loc[dataframe["crowding"] > self.exit_crowding_pct, "exit_long"] = 1
        return dataframe

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float, current_profit: float, **kwargs):
        if current_time - trade.open_date_utc >= timedelta(days=self.max_hold_days):
            return "time_stop"
        return None

    # ---------------------------------------------------------------- side caps
    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float, time_in_force: str, current_time: datetime, entry_tag, side: str, **kwargs) -> bool:
        open_trades = Trade.get_open_trades()
        same = sum(1 for t in open_trades if t.is_short == (side == "short"))
        other = len(open_trades) - same
        cap = self.max_per_side if other > 0 else self.max_per_side_if_other_empty
        return same < cap
