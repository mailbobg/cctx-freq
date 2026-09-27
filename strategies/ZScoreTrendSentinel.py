"""Loop round 2: "Dual Signal Trend Sentinel" (r/algotrading, 2026-01-18, u/DurdenBTC).

Daily: z = (close − EMA65) / stdev65. z > bull → long; z < bear → flat; in between keep the current state.
The author did not publish the thresholds; ours are fixed before running (see loop_log.md round 2).
"""

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.strategy import IStrategy


class ZScoreTrendSentinel(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1d"
    can_short = False
    stoploss = -0.99
    minimal_roi = {"0": 100}
    use_exit_signal = True
    process_only_new_candles = True
    startup_candle_count = 70

    period = 65
    bull = 0.5
    bear = -0.5

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        for k, v in config.get("zscore", {}).items():
            setattr(self, k, v)

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        ema = ta.EMA(dataframe, timeperiod=self.period)
        std = dataframe["close"].rolling(self.period).std()
        dataframe["z"] = (dataframe["close"] - ema) / std
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[dataframe["z"] > self.bull, ["enter_long", "enter_tag"]] = (1, "bull")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[dataframe["z"] < self.bear, ["exit_long", "exit_tag"]] = (1, "bear")
        return dataframe
