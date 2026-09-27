"""Loop round 5: Turtle System 2 on crypto perps (r/algotradingcrypto 2026-07-05, live on KuCoin futures).

Daily. Long on a 55-day high breakout, short on a 55-day low breakout. Exit on the opposite 20-day
breakout. Stop 2N, N = ATR(20). Risk 1% of equity per N (so 2% at the stop). Max 12 positions, no
pyramiding. 1x.
"""

from datetime import datetime

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy, stoploss_from_absolute


class TurtleCryptoS2(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1d"
    can_short = True
    stoploss = -0.99            # the 2N stop is applied in custom_stoploss
    use_custom_stoploss = True
    minimal_roi = {"0": 100}
    use_exit_signal = True
    process_only_new_candles = True
    startup_candle_count = 60

    long_only = False
    risk_per_n = 0.01

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        for k, v in config.get("turtle", {}).items():
            setattr(self, k, v)

    def leverage(self, pair, current_time, current_rate, proposed_leverage, max_leverage, entry_tag, side, **kwargs) -> float:
        return 1.0

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["hh55"] = dataframe["high"].rolling(55).max().shift(1)
        dataframe["ll55"] = dataframe["low"].rolling(55).min().shift(1)
        dataframe["hh20"] = dataframe["high"].rolling(20).max().shift(1)
        dataframe["ll20"] = dataframe["low"].rolling(20).min().shift(1)
        dataframe["n"] = ta.ATR(dataframe, timeperiod=20)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[dataframe["close"] > dataframe["hh55"], ["enter_long", "enter_tag"]] = (1, "b55")
        if not self.long_only:
            dataframe.loc[dataframe["close"] < dataframe["ll55"], ["enter_short", "enter_tag"]] = (1, "s55")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[dataframe["close"] < dataframe["ll20"], ["exit_long", "exit_tag"]] = (1, "x20")
        dataframe.loc[dataframe["close"] > dataframe["hh20"], ["exit_short", "exit_tag"]] = (1, "x20")
        return dataframe

    def _n(self, pair: str) -> float | None:
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        return None if df is None or df.empty else float(df["n"].iloc[-1])

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float, proposed_stake: float,
                            min_stake: float | None, max_stake: float, leverage: float, entry_tag: str | None,
                            side: str, **kwargs) -> float:
        n = self._n(pair)
        if not n or n <= 0:
            return 0.0
        equity = self.wallets.get_total_stake_amount()
        units = equity * self.risk_per_n / n          # coins such that a 1N move = 1% of equity
        return min(units * current_rate, max_stake, equity * 0.25)

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                        current_profit: float, after_fill: bool, **kwargs) -> float | None:
        """Fixed stop at entry ± 2N, N taken from the last closed candle before the entry (not trailing)."""
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if df is None or df.empty:
            return None
        before = df[df["date"] < trade.open_date_utc]
        if before.empty:
            return None
        n = float(before["n"].iloc[-1])
        stop_price = trade.open_rate + (2 * n if trade.is_short else -2 * n)
        return stoploss_from_absolute(stop_price, current_rate, is_short=trade.is_short, leverage=trade.leverage)
