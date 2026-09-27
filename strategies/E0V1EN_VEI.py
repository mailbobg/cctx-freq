"""Loop round 12: E0V1EN with the Volatility Expansion Index filter (r/algotrading 2025-12-09).

VEI = ATR(14) / ATR(100) on the strategy timeframe; no new entries while VEI > threshold.
"""

import sys
from pathlib import Path

import talib.abstract as ta

sys.path.insert(0, str(Path(__file__).parent))
from E0V1EN import E0V1EN  # noqa: E402


class E0V1EN_VEI(E0V1EN):
    vei_max = 1.2
    startup_candle_count = 200

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.vei_max = config.get("vei", {}).get("vei_max", self.vei_max)

    def populate_indicators(self, dataframe, metadata):
        dataframe = super().populate_indicators(dataframe, metadata)
        dataframe["vei"] = ta.ATR(dataframe, timeperiod=14) / ta.ATR(dataframe, timeperiod=100)
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        dataframe = super().populate_entry_trend(dataframe, metadata)
        dataframe.loc[dataframe["vei"] > self.vei_max, "enter_long"] = 0
        return dataframe
