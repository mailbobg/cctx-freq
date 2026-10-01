"""U2: forward paper test of U1's token-unlock pressure short (spec: bench/specs/2026-10-01-u2-unlock-forward.md).

Every Monday 00:00 UTC, on the daily candles up to Sunday's close and the stored unlock schedule
(strategies/unlock_schedule.csv): short up to 6 coins whose unlocks in the next 14 days are worth at least 0.02 × 14 days of
their normal volume, 5 % of equity each, against one BTC long of equal total notional. A coin leaves when it drops out
of the weekly selection. Futures, isolated, 1x. Selection logic lives in unlock_xs_logic.py (tested against the backtest).
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from unlock_xs_logic import load_schedule, select_week  # noqa: E402

BTC = "BTC/USDT:USDT"
SIZE = 0.05              # of equity per short
THRESHOLD = 0.02
TOP_K = 6
ENTRY_WINDOW_HOURS = 6   # entries only in the first hours of Monday's candle (a late restart must not open a week late)
SCHEDULE = Path(__file__).resolve().parent / "unlock_schedule.csv"
AUDIT = Path(__file__).resolve().parents[1] / "unlock_xs_decisions.jsonl"


def monday_of(now: datetime) -> datetime:
    d = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return d - timedelta(days=d.weekday())


class UnlockXS(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1d"
    can_short = True
    startup_candle_count = 45
    process_only_new_candles = True
    minimal_roi = {"0": 100}
    stoploss = -0.99
    trailing_stop = False
    use_exit_signal = True
    position_adjustment_enable = True
    max_entry_position_adjustment = -1
    ignore_buying_expired_candle_after = ENTRY_WINDOW_HOURS * 3600
    order_types = {"entry": "market", "exit": "market", "emergency_exit": "market", "force_entry": "market",
                   "force_exit": "market", "stoploss": "market", "stoploss_on_exchange": False}

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._schedule = load_schedule(SCHEDULE)
        self._week = None
        self._selected: dict[str, float] = {}

    # ------------------------------------------------------------ weekly selection
    def _frames(self) -> dict:
        out = {}
        for pair in self.dp.current_whitelist():
            df = self.dp.get_pair_dataframe(pair, self.timeframe)
            if df is None or df.empty:
                continue
            f = df[["date", "close", "volume"]].copy()
            f["quote_volume"] = f["volume"] * f["close"]       # live candles carry base volume; U1 used quote volume
            out[pair] = f[["date", "close", "quote_volume"]]
        return out

    def _decision_day(self, now: datetime) -> datetime:
        return monday_of(now)

    def bot_loop_start(self, current_time: datetime, **kwargs) -> None:
        monday = self._decision_day(current_time)
        if self._week == monday:
            return
        frames = self._frames()
        if not frames:
            return
        yesterday = monday - timedelta(days=1)
        fresh = [f for f in frames.values() if not f.empty and f["date"].iloc[-1] >= yesterday]
        if len(fresh) < 0.8 * len(frames) or BTC not in frames:     # candles for the new day not loaded yet: retry next loop
            return
        picked = select_week(monday, frames, self._schedule, thr=THRESHOLD, k=TOP_K, btc=BTC)
        self._selected = dict(picked)
        self._week = monday
        try:
            with AUDIT.open("a") as fh:
                fh.write(json.dumps({"monday": monday.isoformat(), "selected": picked, "universe": len(frames)}) + "\n")
        except OSError:
            pass

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
        if dataframe.empty:
            return dataframe
        last = dataframe.index[-1:]
        if pair == BTC:
            if self._selected:
                dataframe.loc[last, ["enter_long", "enter_tag"]] = (1, "hedge")
        elif pair in self._selected:
            dataframe.loc[last, ["enter_short", "enter_tag"]] = (1, "unlock_pressure")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        return dataframe

    def _open_shorts(self) -> list:
        return [t for t in Trade.get_trades_proxy(is_open=True) if t.pair != BTC and t.is_short]

    def _equity(self) -> float:
        return self.wallets.get_total_stake_amount() if self.wallets else 0.0

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float, time_in_force: str,
                            current_time: datetime, entry_tag: str | None, side: str, **kwargs) -> bool:
        if pair == BTC:
            return side == "long" and bool(self._selected or self._open_shorts())
        return side == "short" and pair in self._selected

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float, proposed_stake: float,
                            min_stake: float | None, max_stake: float, leverage: float, entry_tag: str | None,
                            side: str, **kwargs) -> float:
        if pair == BTC:
            n = max(len(self._open_shorts()), len(self._selected))
            target = n * SIZE * self._equity()
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
            return None
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
        if self._week != self._decision_day(current_time):
            return None                      # this week's selection is not computed yet: never exit on a stale one
        if pair not in self._selected and trade.open_date_utc < self._week:
            return "dropped_from_selection"
        return None
