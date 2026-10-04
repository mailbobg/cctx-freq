"""Loop round 4: Bollinger breakout on BTC H1 (r/algotrading 2025-06-25).

BB(42, 2.5). Long when close >= upper band, short when close <= lower band. Take profit 3%, stop loss
1.5%, time exit after 1075 minutes. One position at a time (the author allowed three; freqtrade allows
one per pair).
"""

from datetime import datetime, timedelta

from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy


class BBBreakoutBTC(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = True
    stoploss = -0.015
    minimal_roi = {"0": 0.03}
    use_exit_signal = True             # also enables the documented custom holding deadline
    process_only_new_candles = True
    startup_candle_count = 50

    def leverage(self, pair, current_time, current_rate, proposed_leverage, max_leverage, entry_tag, side, **kwargs) -> float:
        return 1.0

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        mid = dataframe["close"].rolling(42).mean()
        sd = dataframe["close"].rolling(42).std(ddof=0)
        dataframe["upper"] = mid + 2.5 * sd
        dataframe["lower"] = mid - 2.5 * sd
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[dataframe["close"] >= dataframe["upper"], ["enter_long", "enter_tag"]] = (1, "bb_up")
        dataframe.loc[dataframe["close"] <= dataframe["lower"], ["enter_short", "enter_tag"]] = (1, "bb_down")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float, current_profit: float, **kwargs):
        if current_time - trade.open_date_utc >= timedelta(minutes=1075):
            return "time_1075m"
        return None
