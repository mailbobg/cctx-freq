"""X13: E0V1ENPF with a resting take-profit instead of the built-in trailing stop.

Pre-registration and acceptance criteria:
user_data/bench/specs/2026-09-27-x13-resting-tp-design.md

Backtest model of the resting limit sell at +3%: minimal_roi {"0": 0.03}, trailing stop off, run with
--timeframe-detail 1m and ignore_roi_if_entry_signal = false (config-x13.json). All other exits are E0V1EN's.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from E0V1ENPF import E0V1ENPF  # noqa: E402


class E0V1ENX13(E0V1ENPF):
    trailing_stop = False
    minimal_roi = {"0": 0.03}


class E0V1ENX13R(E0V1ENX13):
    """Pre-registered variant (a): no new entries while BTC/USDT's last closed daily close is below its 200-day SMA."""

    startup_candle_count = 400   # the 1d informative needs 200+ daily candles of history

    def informative_pairs(self):
        return super().informative_pairs() + [("BTC/USDT", "1d")]

    def populate_indicators(self, dataframe, metadata):
        from freqtrade.strategy import merge_informative_pair
        dataframe = super().populate_indicators(dataframe, metadata)
        btc = self.dp.get_pair_dataframe("BTC/USDT", "1d")[["date", "close"]].copy()
        btc["sma200"] = btc["close"].rolling(200).mean()
        btc["bear"] = (btc["close"] < btc["sma200"]).astype(int)
        # merge_informative_pair shifts so only the last *closed* daily candle is visible
        return merge_informative_pair(dataframe, btc[["date", "bear", "sma200"]], self.timeframe, "1d", ffill=True)

    def populate_entry_trend(self, dataframe, metadata):
        dataframe = super().populate_entry_trend(dataframe, metadata)
        blocked = (dataframe["bear_1d"] == 1) | dataframe["sma200_1d"].isna()
        dataframe.loc[blocked, "enter_long"] = 0
        return dataframe
