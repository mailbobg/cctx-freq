"""X9 variant 2: long-only funding reversion (after nateemma's FundingCarry).

Pre-registration: user_data/bench/specs/2026-09-25-x9-funding-crowding-design.md, variant 2.

When funding is extremely negative relative to the coin's own trailing 90 days
(z-score < −1.5) the shorts are crowded and tend to get squeezed over the next
8–24h. Enter long, hold 16h, exit by time. No stop, no ROI: a stop whipsaws this
slow reversion. Only trade while BTC is above its 500-hour SMA (down-market
circuit breaker). Binance USDT perps, 1x.
"""

from datetime import datetime, timedelta

import pandas as pd
from pandas import DataFrame

from freqtrade.enums import CandleType
from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy


FUNDING_TF = "1h"  # freqtrade's label for the funding-rate series (rows are 8h/4h apart)
BTC_PAIR = "BTC/USDT:USDT"


class FundingReversionX9b(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1h"
    can_short = False
    stoploss = -0.99
    minimal_roi = {"0": 100}
    use_exit_signal = True             # required for the custom 16h exit
    process_only_new_candles = True
    startup_candle_count = 550

    ENTRY_Z = -1.5
    MAX_HOLD_H = 16
    MARKET_SMA = 500
    Z_WINDOW = "90D"

    order_types = {"entry": "market", "exit": "market", "stoploss": "market", "stoploss_on_exchange": False}

    def informative_pairs(self):
        pairs = [(pair, FUNDING_TF, CandleType.FUNDING_RATE) for pair in self.dp.current_whitelist()]
        pairs.append((BTC_PAIR, self.timeframe, CandleType.FUTURES))
        return pairs

    def leverage(self, pair, current_time, current_rate, proposed_leverage, max_leverage, entry_tag, side, **kwargs) -> float:
        return 1.0

    def funding_z(self, pair: str) -> DataFrame:
        """Trailing z-score of the settled funding rate. A funding settled at time F is
        usable by the first 1h candle that closes after F (candle date > F − 1h)."""
        fr = self.dp.get_pair_dataframe(pair, FUNDING_TF, candle_type=CandleType.FUNDING_RATE)
        if fr is None or fr.empty:
            return DataFrame(columns=["date", "funding_z"])
        s = fr.set_index("date")["funding_rate"].astype(float).sort_index()
        mean = s.rolling(self.Z_WINDOW, min_periods=60).mean()
        std = s.rolling(self.Z_WINDOW, min_periods=60).std()
        z = (s - mean) / std.replace(0, float("nan"))
        feat = DataFrame({"funding_z": z}).reset_index()
        feat["date"] = feat["date"] - pd.Timedelta(minutes=59)
        return feat

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        feat = self.funding_z(metadata["pair"])
        feat["date"] = feat["date"].astype(dataframe["date"].dtype)
        dataframe = pd.merge_asof(
            dataframe.sort_values("date"), feat.sort_values("date"),
            on="date", direction="backward", tolerance=pd.Timedelta(hours=9),
        )
        btc = self.dp.get_pair_dataframe(BTC_PAIR, self.timeframe)
        btc = btc[["date", "close"]].rename(columns={"close": "btc_close"})
        btc["btc_sma"] = btc["btc_close"].rolling(self.MARKET_SMA, min_periods=24).mean()
        dataframe = dataframe.merge(btc, on="date", how="left")
        dataframe["market_ok"] = dataframe["btc_close"] > dataframe["btc_sma"]
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["funding_z"] < self.ENTRY_Z) & dataframe["market_ok"],
            ["enter_long", "enter_tag"],
        ] = (1, "crowded_short")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float, current_profit: float, **kwargs):
        if current_time - trade.open_date_utc >= timedelta(hours=self.MAX_HOLD_H):
            return "time_exit"
        return None
