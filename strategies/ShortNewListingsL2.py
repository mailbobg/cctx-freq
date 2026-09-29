"""L2: short newly listed Binance USDT-M perpetuals, BTC-hedged, exit at 3x (spec: bench/specs/2026-09-29-l2-listings-forward.md).

Forward paper test only. Cross margin, 1x. For each crypto perpetual whose onboardDate is recent and that is not a
re-listing: short 2.5 % of equity at the first hourly open at or after D0 + 3 days 00:00 UTC (24 h entry window), hold
21 days or until the price reaches 3x entry (stoploss −2.0 at 1x). One BTC long hedges the sum of the open shorts.
"""

from datetime import datetime, timedelta, timezone

from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy

BTC = "BTC/USDT:USDT"
STABLE = {"USDC", "BUSD", "TUSD", "PAX", "USDP", "DAI", "FDUSD", "USD1", "UST", "USTC", "SUSD", "EUR", "GBP", "AUD",
          "USDE", "USDT"}
SIZE = 0.025          # of total equity per event
MAX_EVENTS = 10
HOLD = timedelta(days=21)
DELAY = timedelta(days=3)          # D0 00:00 + 3 days = after the close of D0 + 2
WINDOW = timedelta(days=1)


class ShortNewListingsL2(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = True
    startup_candle_count = 120
    process_only_new_candles = True
    minimal_roi = {"0": 100}
    stoploss = -2.0                     # short at 1x: stop price = 3x entry
    trailing_stop = False
    use_exit_signal = True             # freqtrade only calls custom_exit when exit signals are enabled
    position_adjustment_enable = True
    max_entry_position_adjustment = -1
    order_types = {"entry": "market", "exit": "market", "emergency_exit": "market", "force_entry": "market",
                   "force_exit": "market", "stoploss": "market", "stoploss_on_exchange": False}

    # ------------------------------------------------------------ helpers
    def _onboard(self, pair: str) -> datetime | None:
        m = self.dp.market(pair) if self.dp else None
        info = (m or {}).get("info", {}) or {}
        ts = info.get("onboardDate")
        if not ts:
            return None
        return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)

    def _is_candidate(self, pair: str) -> bool:
        if pair == BTC or not pair.endswith("/USDT:USDT"):
            return False
        m = self.dp.market(pair) if self.dp else None
        if not m:
            return False
        info = m.get("info", {}) or {}
        if info.get("underlyingType", "COIN") != "COIN" or info.get("contractType", "PERPETUAL") != "PERPETUAL":
            return False
        return m.get("base", "").upper() not in STABLE

    def _open_shorts(self) -> list:
        return [t for t in Trade.get_trades_proxy(is_open=True) if t.pair != BTC and t.is_short]

    def _equity(self) -> float:
        return self.wallets.get_total_stake_amount() if self.wallets else 0.0

    # ------------------------------------------------------------ signals
    def leverage(self, pair: str, current_time: datetime, current_rate: float, proposed_leverage: float,
                 max_leverage: float, entry_tag: str | None, side: str, **kwargs) -> float:
        return 1.0

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pair = metadata["pair"]
        dataframe["enter_long"] = 0
        dataframe["enter_short"] = 0
        if pair == BTC:
            dataframe.loc[:, ["enter_long", "enter_tag"]] = (1, "hedge")   # confirm_trade_entry decides
            return dataframe
        if not self._is_candidate(pair) or dataframe.empty:
            return dataframe
        onboard = self._onboard(pair)
        if onboard is None:
            return dataframe
        d0 = onboard.replace(hour=0, minute=0, second=0, microsecond=0)
        start = d0 + DELAY
        if dataframe["date"].iloc[0] < onboard - timedelta(days=1):
            return dataframe                     # history predates onboardDate: re-listing / migrated contract
        close_time = dataframe["date"] + timedelta(hours=1)
        sig = (close_time >= start) & (close_time < start + WINDOW)
        dataframe.loc[sig, ["enter_short", "enter_tag"]] = (1, "new_listing")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        return dataframe

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float, time_in_force: str,
                            current_time: datetime, entry_tag: str | None, side: str, **kwargs) -> bool:
        if pair == BTC:
            return side == "long" and len(self._open_shorts()) > 0
        if side != "short" or not self._is_candidate(pair):
            return False
        if Trade.get_trades_proxy(pair=pair):
            return False                          # one trade per listing, ever
        return len(self._open_shorts()) < MAX_EVENTS

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float, proposed_stake: float,
                            min_stake: float | None, max_stake: float, leverage: float, entry_tag: str | None,
                            side: str, **kwargs) -> float:
        if pair == BTC:
            target = sum(t.stake_amount for t in self._open_shorts())
        else:
            target = SIZE * self._equity()
        return min(max(target, min_stake or 0.0), max_stake)

    def adjust_trade_position(self, trade: Trade, current_time: datetime, current_rate: float,
                              current_profit: float, min_stake: float | None, max_stake: float,
                              current_entry_rate: float, current_exit_rate: float, current_entry_profit: float,
                              current_exit_profit: float, **kwargs):
        if trade.pair != BTC or trade.has_open_orders:
            return None
        target = sum(t.stake_amount for t in self._open_shorts())
        if target <= 0:
            return None                           # custom_exit closes the hedge
        diff = target - trade.stake_amount
        band = 0.5 * SIZE * self._equity()
        if diff > band:
            return min(diff, max_stake), "hedge_up"
        if diff < -band and -diff < trade.stake_amount:
            return diff, "hedge_down"
        return None

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        if pair == BTC:
            return "hedge_flat" if not self._open_shorts() else None
        if current_time - trade.open_date_utc >= HOLD:
            return "time_21d"
        return None
