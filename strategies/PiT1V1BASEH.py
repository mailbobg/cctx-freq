"""PiT1V1BASEH - 4h Donchian breakout seed for the trend family (t_loop genesis).

Paradigm: trend-following (complements the dip-buy E0/E1 family).
Hypothesis: on 4h crypto majors, 20-bar breakouts with an ATR trailing stop
capture multi-day legs while sitting out chop; no counters, no mean-reversion.
Parent: none (genesis seed, hand-written minimal).
Created: 2026-10-06
Status: candidate
Uses MTF: no
"""

import numpy as np
import talib.abstract as ta
from pandas import DataFrame

from freqtrade.strategy import IStrategy


class PiT1V1BASEH(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "4h"
    startup_candle_count = 200
    process_only_new_candles = True

    minimal_roi = {"0": 1}
    stoploss = -0.25
    trailing_stop = True
    trailing_stop_positive = 0.02
    trailing_stop_positive_offset = 0.05
    trailing_only_offset_is_reached = True

    order_types = {
        "entry": "market", "exit": "market",
        "emergency_exit": "market", "force_entry": "market",
        "force_exit": "market", "stoploss": "market",
        "stoploss_on_exchange": False,
    }

    pair_basket = ["BTC/USDT", "SOL/USDT", "AVAX/USDT"]
    test_timeranges = [
        ("bull_2021", "20210101-20211231"),
        ("winter_2022", "20220101-20221231"),
        ("recovery_23_25", "20230101-20251231"),
        ("full_5y", "20210101-20251231"),
    ]

    donchian_len = 20
    atr_len = 14

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["dc_high"] = dataframe["high"].rolling(self.donchian_len).max()
        dataframe["dc_low"] = dataframe["low"].rolling(self.donchian_len).min()
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=self.atr_len)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "enter_tag"] = ""
        broke = (dataframe["close"] > dataframe["dc_high"].shift(1)) & dataframe["atr"].notna()
        dataframe.loc[broke, "enter_long"] = 1
        dataframe.loc[broke, "enter_tag"] = "donch_break"
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, ["exit_long", "exit_tag"]] = (0, "long_out")
        broke = (dataframe["close"] < dataframe["dc_low"].shift(1)) & dataframe["atr"].notna()
        dataframe.loc[broke, "exit_long"] = 1
        dataframe.loc[broke, "exit_tag"] = "donch_break_dn"
        return dataframe
