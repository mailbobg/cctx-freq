"""Loop rounds 8–11: classic published daily rules, at their published parameters.

ConnorsRSI2   : close > SMA200 and RSI(2) < 10 → buy; exit close > SMA5.
TurnOfMonth   : buy at the close of the month's last day, sell at the close of the 3rd day of the new month.
NDownDays     : 3 consecutive down closes → buy; exit at the first up close.
GoldenCross   : SMA50 crosses above SMA200 → buy; crosses below → sell.
"""

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.strategy import IStrategy


class _Daily(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1d"
    can_short = False
    stoploss = -0.99
    minimal_roi = {"0": 100}
    use_exit_signal = True
    process_only_new_candles = True
    startup_candle_count = 210


class ConnorsRSI2(_Daily):
    def populate_indicators(self, df: DataFrame, metadata: dict) -> DataFrame:
        df["sma200"] = ta.SMA(df, timeperiod=200)
        df["sma5"] = ta.SMA(df, timeperiod=5)
        df["rsi2"] = ta.RSI(df, timeperiod=2)
        return df

    def populate_entry_trend(self, df, metadata):
        df.loc[(df["close"] > df["sma200"]) & (df["rsi2"] < 10), ["enter_long", "enter_tag"]] = (1, "rsi2")
        return df

    def populate_exit_trend(self, df, metadata):
        df.loc[df["close"] > df["sma5"], ["exit_long", "exit_tag"]] = (1, "above_sma5")
        return df


class TurnOfMonth(_Daily):
    startup_candle_count = 5

    def populate_indicators(self, df, metadata):
        d = df["date"]
        df["last_day"] = (d + __import__("pandas").Timedelta(days=1)).dt.day == 1
        df["day3"] = d.dt.day == 3
        return df

    def populate_entry_trend(self, df, metadata):
        df.loc[df["last_day"], ["enter_long", "enter_tag"]] = (1, "tom")
        return df

    def populate_exit_trend(self, df, metadata):
        df.loc[df["day3"], ["exit_long", "exit_tag"]] = (1, "day3")
        return df


class NDownDays(_Daily):
    startup_candle_count = 5

    def populate_indicators(self, df, metadata):
        down = df["close"] < df["close"].shift(1)
        df["down3"] = down & down.shift(1) & down.shift(2)
        df["up"] = df["close"] > df["close"].shift(1)
        return df

    def populate_entry_trend(self, df, metadata):
        df.loc[df["down3"], ["enter_long", "enter_tag"]] = (1, "down3")
        return df

    def populate_exit_trend(self, df, metadata):
        df.loc[df["up"], ["exit_long", "exit_tag"]] = (1, "up")
        return df


class GoldenCross(_Daily):
    def populate_indicators(self, df, metadata):
        df["sma50"] = ta.SMA(df, timeperiod=50)
        df["sma200"] = ta.SMA(df, timeperiod=200)
        return df

    def populate_entry_trend(self, df, metadata):
        df.loc[(df["sma50"] > df["sma200"]) & (df["sma50"].shift(1) <= df["sma200"].shift(1)), ["enter_long", "enter_tag"]] = (1, "gc")
        return df

    def populate_exit_trend(self, df, metadata):
        df.loc[(df["sma50"] < df["sma200"]) & (df["sma50"].shift(1) >= df["sma200"].shift(1)), ["exit_long", "exit_tag"]] = (1, "dc")
        return df


class EMACross5_40(_Daily):
    """Loop round 13 (r/algotrading 2020-09-29): EMA5 crosses above EMA40 → long; crosses below → flat."""
    startup_candle_count = 60

    def populate_indicators(self, df, metadata):
        df["f"] = ta.EMA(df, timeperiod=5)
        df["s"] = ta.EMA(df, timeperiod=40)
        return df

    def populate_entry_trend(self, df, metadata):
        df.loc[(df["f"] > df["s"]) & (df["f"].shift(1) <= df["s"].shift(1)), ["enter_long", "enter_tag"]] = (1, "x_up")
        return df

    def populate_exit_trend(self, df, metadata):
        df.loc[(df["f"] < df["s"]) & (df["f"].shift(1) >= df["s"].shift(1)), ["exit_long", "exit_tag"]] = (1, "x_dn")
        return df


class HiLo10(_Daily):
    """Round 13 variant: close > previous 10-day high → long; close < previous 10-day low → flat."""
    startup_candle_count = 15

    def populate_indicators(self, df, metadata):
        df["hh"] = df["high"].rolling(10).max().shift(1)
        df["ll"] = df["low"].rolling(10).min().shift(1)
        return df

    def populate_entry_trend(self, df, metadata):
        df.loc[df["close"] > df["hh"], ["enter_long", "enter_tag"]] = (1, "hh10")
        return df

    def populate_exit_trend(self, df, metadata):
        df.loc[df["close"] < df["ll"], ["exit_long", "exit_tag"]] = (1, "ll10")
        return df


class PriceVsSMA200(_Daily):
    """Loop round 15 (falsify list): hold while close > SMA200."""
    def populate_indicators(self, df, metadata):
        df["sma200"] = ta.SMA(df, timeperiod=200)
        return df

    def populate_entry_trend(self, df, metadata):
        df.loc[df["close"] > df["sma200"], ["enter_long", "enter_tag"]] = (1, "above200")
        return df

    def populate_exit_trend(self, df, metadata):
        df.loc[df["close"] < df["sma200"], ["exit_long", "exit_tag"]] = (1, "below200")
        return df


class TSMOM30(_Daily):
    """Loop round 16 (falsify list): each Monday, hold if the 30-day return > 0, else flat."""
    startup_candle_count = 40

    def populate_indicators(self, df, metadata):
        df["mom30"] = df["close"] / df["close"].shift(30) - 1
        df["monday_next"] = (df["date"] + __import__("pandas").Timedelta(days=1)).dt.weekday == 0
        return df

    def populate_entry_trend(self, df, metadata):
        df.loc[df["monday_next"] & (df["mom30"] > 0), ["enter_long", "enter_tag"]] = (1, "mom+")
        return df

    def populate_exit_trend(self, df, metadata):
        df.loc[df["monday_next"] & (df["mom30"] <= 0), ["exit_long", "exit_tag"]] = (1, "mom-")
        return df


class VolTargetSMA200(PriceVsSMA200):
    """Loop round 17 (falsify list): SMA200 trend, sized to a 20% annualised vol target (cap 100% of a slot)."""
    def populate_indicators(self, df, metadata):
        df = super().populate_indicators(df, metadata)
        import numpy as np
        df["vol30"] = np.log(df["close"]).diff().rolling(30).std() * np.sqrt(365)
        return df

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake, min_stake, max_stake, leverage, entry_tag, side, **kwargs):
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        v = float(df["vol30"].iloc[-1]) if df is not None and len(df) else 0.0
        scale = min(1.0, 0.20 / v) if v > 0 else 0.0
        return proposed_stake * scale
