"""X14 as a freqtrade futures bot: long the flushed altcoins, short BTC, exit after 24 h.

Reference dry-run only — the pre-registered spec (bench/specs/2026-09-28-x14-hedged-flush-design.md) FAILED on
2025–26 and drawdown; this bot exists to build a forward record, exactly like c-hold.

Rules (same as the backtest engine bench/x14_flush.py, with two simplifications noted):
- detection on closed 5m candles over the whitelist (weekly top-60 perps by volume, BTC and non-crypto excluded via
  the pairlist config): BTC 1h ≤ −0.5 % and ≥ max(6, 10 %) of the coins 1h ≤ −4 % vs BTC; 4 h cooldown;
- entry at the next candle: long the 5 most-dropped coins, 4 % of equity each, short BTC for the same total notional;
- exit all legs 24 h after entry; a long leg is also closed at −25 %;
- at most 2 episodes open; an episode is identified by its enter_tag.
Simplifications vs the engine: the BTC hedge is not reduced when a long leg is stopped (it stays until the 24 h exit),
and legs of one episode are entered as independent freqtrade trades (a leg can be rejected by the exchange rules
without cancelling the others).
"""

import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy

sys.path.insert(0, str(Path(__file__).parent))
from FlushReboundX11 import flush_panel  # noqa: E402

BTC_PAIR = "BTC/USDT:USDT"


class HedgedFlushX14(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "5m"
    can_short = True
    stoploss = -0.99                      # exits are decided in custom_exit
    minimal_roi = {"0": 100}
    use_exit_signal = True               # Freqtrade also gates custom_exit with this flag
    process_only_new_candles = True
    startup_candle_count = 20
    order_types = {"entry": "market", "exit": "market", "stoploss": "market", "stoploss_on_exchange": False}

    # pre-registered parameters (spec)
    lookback = 12
    btc_drop = -0.005
    alt_drop = -0.04
    breadth_frac = 0.10
    breadth_floor = 6
    cooldown = 48
    n_coins = 5
    weight = 0.04
    hold_hours = 24
    leg_stop = -0.25
    max_episodes = 2

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._key = None
        self._panel: DataFrame | None = None
        self._fires: pd.Series | None = None

    def leverage(self, pair: str, current_time: datetime, current_rate: float, proposed_leverage: float,
                 max_leverage: float, entry_tag: str | None, side: str, **kwargs) -> float:
        return 1.0

    def informative_pairs(self):
        return [(BTC_PAIR, self.timeframe)]

    # ---------------------------------------------------------------- cross-sectional detection

    def _build(self) -> None:
        closes = {}
        for pair in self.dp.current_whitelist():
            if pair == BTC_PAIR:
                continue
            df = self.dp.get_pair_dataframe(pair, self.timeframe)
            if df is not None and not df.empty:
                closes[pair] = df.set_index("date")["close"]
        btc_df = self.dp.get_pair_dataframe(BTC_PAIR, self.timeframe)
        if not closes or btc_df is None or btc_df.empty:
            return
        close = DataFrame(closes).sort_index()
        btc = btc_df.set_index("date")["close"]
        key = (tuple(sorted(closes)), close.index[-1], len(close))
        if key == self._key:
            return
        need = max(self.breadth_floor, math.ceil(self.breadth_frac * close.shape[1]))
        self._panel = flush_panel(close, btc, lookback=self.lookback, btc_drop=self.btc_drop, alt_drop=self.alt_drop,
                                  breadth_min=need, cooldown=self.cooldown, max_coins=self.n_coins)
        self._fires = self._panel.sum(axis=1)        # number of coins picked at each fire (0 elsewhere)
        self._key = key

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        self._build()
        pair = metadata["pair"]
        if self._panel is None:
            dataframe["flush_buy"] = False
            dataframe["flush_n"] = 0
            return dataframe
        if pair == BTC_PAIR:
            n = self._fires.rename("flush_n")
            dataframe = dataframe.merge(n, left_on="date", right_index=True, how="left")
            dataframe["flush_n"] = dataframe["flush_n"].fillna(0).astype(int)
            dataframe["flush_buy"] = False
        else:
            sel = self._panel[pair] if pair in self._panel else pd.Series(False, index=self._panel.index)
            dataframe = dataframe.merge(sel.rename("flush_buy"), left_on="date", right_index=True, how="left")
            dataframe["flush_buy"] = dataframe["flush_buy"].fillna(False).astype(bool)
            dataframe["flush_n"] = 0
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        tag = "flush_" + dataframe["date"].dt.strftime("%Y%m%d%H%M")
        if metadata["pair"] == BTC_PAIR:
            mask = dataframe["flush_n"] > 0
            dataframe.loc[mask, "enter_short"] = 1
            dataframe.loc[mask, "enter_tag"] = tag[mask]
        else:
            mask = dataframe["flush_buy"]
            dataframe.loc[mask, "enter_long"] = 1
            dataframe.loc[mask, "enter_tag"] = tag[mask]
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    # ---------------------------------------------------------------- sizing, limits, exits

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float, proposed_stake: float,
                            min_stake: float | None, max_stake: float, leverage: float, entry_tag: str | None,
                            side: str, **kwargs) -> float:
        equity = self.wallets.get_total_stake_amount()
        if pair == BTC_PAIR:
            n = self.n_coins
            if self._fires is not None and entry_tag:
                t = pd.Timestamp(datetime.strptime(entry_tag[6:], "%Y%m%d%H%M"), tz="UTC")
                if t in self._fires.index and self._fires[t] > 0:
                    n = int(self._fires[t])
            return min(max_stake, equity * self.weight * n)
        return min(max_stake, equity * self.weight)

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float, time_in_force: str,
                            current_time: datetime, entry_tag: str | None, side: str, **kwargs) -> bool:
        episodes = {t.enter_tag for t in Trade.get_trades_proxy(is_open=True) if t.enter_tag}
        if entry_tag not in episodes and len(episodes) >= self.max_episodes:
            return False
        return True

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        if current_time - trade.open_date_utc >= timedelta(hours=self.hold_hours):
            return "time"
        if not trade.is_short and current_profit <= self.leg_stop:
            return "stop"
        return None
