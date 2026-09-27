"""X11: buy the altcoins hit hardest in a broad market flush, hold 24h.

Pre-registration and acceptance criteria:
user_data/bench/specs/2026-09-26-x11-flush-rebound-design.md

On every closed 5m candle: if BTC fell ≥ 0.5% over the last hour and at least 10 altcoins fell ≥ 4% more
than BTC over that hour, a flush fires (then 4h cooldown). Buy the flushed coins, most-dropped first, at
most 10, 9% of equity each, at the next candle's open; exit purely by time after 24h.
"""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy


BTC_PAIR = "BTC/USDT"


def flush_panel(close: DataFrame, btc: pd.Series, *, lookback: int = 12, btc_drop: float = -0.005,
                alt_drop: float = -0.04, breadth_min: int = 10, cooldown: int = 48, max_coins: int = 10) -> DataFrame:
    """Boolean date × pair frame: True where the pair is bought at the next candle's open.

    Uses only candles up to and including each row (backward shifts), so it can be re-run on truncated
    data for the look-ahead check.
    """
    lc = np.log(close)
    lb = np.log(btc.reindex(close.index))
    btc_1h = lb - lb.shift(lookback)
    x_1h = (lc - lc.shift(lookback)).sub(btc_1h, axis=0)
    dropped = x_1h <= alt_drop
    breadth = dropped.sum(axis=1)
    cand = np.flatnonzero(((btc_1h <= btc_drop) & (breadth >= breadth_min)).to_numpy())
    selected = pd.DataFrame(False, index=close.index, columns=close.columns)
    last = -10**9
    for i in cand:
        if i - last < cooldown:
            continue
        last = i
        row = x_1h.iloc[i]
        picks = row[dropped.iloc[i]].sort_values().index[:max_coins]
        selected.iloc[i, selected.columns.get_indexer(picks)] = True
    return selected


class FlushReboundX11(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "5m"
    can_short = False
    stoploss = -0.25
    minimal_roi = {"0": 100}
    use_exit_signal = False
    process_only_new_candles = True
    startup_candle_count = 20

    # pre-registered parameters (the neighbourhood check overrides them through an "x11" config block)
    lookback = 12
    btc_drop = -0.005
    alt_drop = -0.04
    breadth_min = 10
    cooldown = 48
    max_coins = 10
    stake_fraction = 0.09
    hold_hours = 24

    order_types = {"entry": "market", "exit": "market", "stoploss": "market", "stoploss_on_exchange": False}

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        for key, value in config.get("x11", {}).items():
            if not hasattr(self, key):
                raise ValueError(f"unknown x11 parameter {key}")
            setattr(self, key, value)
        self._key = None
        self._selected: DataFrame | None = None

    def _build(self) -> None:
        closes = {}
        for pair in self.dp.current_whitelist():
            df = self.dp.get_pair_dataframe(pair, self.timeframe)
            if df is not None and not df.empty:
                closes[pair] = df.set_index("date")["close"]
        if BTC_PAIR not in closes:
            btc = self.dp.get_pair_dataframe(BTC_PAIR, self.timeframe)
            closes[BTC_PAIR] = btc.set_index("date")["close"]
        close = DataFrame(closes).sort_index()
        key = (tuple(sorted(closes)), close.index[-1], len(close))
        if key == self._key:
            return
        btc = close.pop(BTC_PAIR)
        self._selected = flush_panel(close, btc, lookback=self.lookback, btc_drop=self.btc_drop,
                                     alt_drop=self.alt_drop, breadth_min=self.breadth_min,
                                     cooldown=self.cooldown, max_coins=self.max_coins)
        self._key = key

    def informative_pairs(self):
        return [(BTC_PAIR, self.timeframe)]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        self._build()
        pair = metadata["pair"]
        sel = self._selected[pair] if pair in self._selected else pd.Series(False, index=self._selected.index)
        dataframe = dataframe.merge(sel.rename("flush_buy"), left_on="date", right_index=True, how="left")
        dataframe["flush_buy"] = dataframe["flush_buy"].fillna(False).astype(bool)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        if metadata["pair"] != BTC_PAIR:
            dataframe.loc[dataframe["flush_buy"], ["enter_long", "enter_tag"]] = (1, "flush")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float, current_profit: float, **kwargs):
        if current_time - trade.open_date_utc >= timedelta(hours=self.hold_hours):
            return "time_exit"
        return None

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float, proposed_stake: float,
                            min_stake: float | None, max_stake: float, leverage: float, entry_tag: str | None,
                            side: str, **kwargs) -> float:
        return min(self.wallets.get_total_stake_amount() * self.stake_fraction, max_stake)
