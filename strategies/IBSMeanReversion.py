"""Loop round 3: IBS mean reversion (r/algotrading 2026-03-03, "simple mean reversion setup, 70% win rate").

Daily. Entry: close < highest_high(10) − 2.5 × (mean_high(25) − mean_low(25)) and IBS < 0.3,
IBS = (close − low) / (high − low). Exit: close > yesterday's high. Long only, parameters as published.
"""

from pandas import DataFrame

from freqtrade.strategy import IStrategy


class IBSMeanReversion(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1d"
    can_short = False
    stoploss = -0.99
    minimal_roi = {"0": 100}
    use_exit_signal = True
    process_only_new_candles = True
    startup_candle_count = 30

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        rng = dataframe["high"].rolling(25).mean() - dataframe["low"].rolling(25).mean()
        dataframe["band"] = dataframe["high"].rolling(10).max() - 2.5 * rng
        span = (dataframe["high"] - dataframe["low"]).replace(0, float("nan"))
        dataframe["ibs"] = (dataframe["close"] - dataframe["low"]) / span
        dataframe["prev_high"] = dataframe["high"].shift(1)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[(dataframe["close"] < dataframe["band"]) & (dataframe["ibs"] < 0.3), ["enter_long", "enter_tag"]] = (1, "ibs")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[dataframe["close"] > dataframe["prev_high"], ["exit_long", "exit_tag"]] = (1, "above_prev_high")
        return dataframe
