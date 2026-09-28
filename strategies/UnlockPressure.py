"""Experimental APT/TIA supply-pressure strategies for Freqtrade futures.

This is disclosed insider vesting divided by trailing futures volume, not
circulating-supply dilution. See the research README for schedule provenance,
small-universe limitations, and independent-leg execution risks.
"""
from datetime import datetime, timedelta
from math import isfinite

import numpy as np
import pandas as pd
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy

PAIRS = ("APT/USDT:USDT", "TIA/USDT:USDT")
AVAILABLE = {
    PAIRS[0]: pd.Timestamp("2022-10-18T00:00:00Z"),
    PAIRS[1]: pd.Timestamp("2023-11-01T18:30:10Z"),
}
APT_POOL = 190_000_000 + 134_782_640.233
# Use the actual rounded figures in the public 2023 git version, not later revisions.
TIA_BACKERS = 356_000_000.
TIA_CONTRIBUTORS = 176_000_000.
TIA_CLIFF = pd.Timestamp("2024-10-30T00:00:00Z")


def _utc(value):
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        raise ValueError("timestamps must include timezone")
    return stamp.tz_convert("UTC")


def scheduled_tokens(pair, asof, horizon_days=28, calendar_shift_days=0):
    """Known scheduled release in (asof, asof + horizon], unavailable -> NaN.

    APT dates use calendar months after 2022-10-12. TIA's vesting year is
    modeled as 365 days; date-only releases use 00:00 UTC. These are forecasts
    from disclosed schedules, not measurements of actual sold/unlocked tokens.
    """
    if pair not in AVAILABLE:
        raise ValueError(f"unsupported supply schedule: {pair}")
    start = _utc(asof)
    if start < AVAILABLE[pair]:
        return np.nan
    end = start + pd.Timedelta(days=horizon_days)
    shift = pd.Timedelta(days=calendar_shift_days)
    if pair == PAIRS[0]:
        total = 0.
        genesis = pd.Timestamp("2022-10-12T00:00:00Z")
        for month in range(13, 49):
            date = genesis + pd.DateOffset(months=month) + shift
            if start < date <= end:
                total += APT_POOL * (3 if month <= 18 else 1) / 48
        return total
    cliff = TIA_CLIFF + shift
    total = (TIA_BACKERS + TIA_CONTRIBUTORS) * .33 if start < cliff <= end else 0.
    for pool, duration in ((TIA_BACKERS, 365), (TIA_CONTRIBUTORS, 730)):
        finish = cliff + pd.Timedelta(days=duration)
        overlap = max(pd.Timedelta(0), min(end, finish) - max(start, cliff))
        total += pool * .67 * overlap.total_seconds() / (duration * 86400)
    return total


def build_weights(frames, mode="ls", calendar_shift_days=0, blocked_entry_weeks=()):
    """Causal hourly panel; Monday 00:00 candle -> Monday 01:00 execution.

    Explicit data exclusions are research exclusions, not predictive information.
    """
    if mode not in ("ls", "short", "control"):
        raise ValueError(f"unknown mode: {mode}")
    clean = {}
    for pair in PAIRS:
        df = frames.get(pair)
        if df is not None and not df.empty:
            df = df.set_index("date").sort_index()
            if df.index.has_duplicates:
                raise ValueError(f"duplicate candles for {pair}")
            clean[pair] = df
    if not clean:
        return DataFrame(columns=list(PAIRS), index=pd.DatetimeIndex([], tz="UTC"), dtype=float)
    first = min(d.index.min() for d in clean.values())
    last = max(d.index.max() for d in clean.values())
    index = pd.date_range(first, last, freq="h", tz="UTC", name="date")
    weights = DataFrame(0., index=index, columns=list(PAIRS))
    if len(clean) != len(PAIRS):
        return weights
    volume = DataFrame({p: d["volume"].reindex(index) for p, d in clean.items()})
    close = DataFrame({p: d["close"].reindex(index) for p, d in clean.items()})
    volume = volume.where((volume > 0) & np.isfinite(volume) & (close > 0) & np.isfinite(close))
    trailing = volume.rolling(28 * 24, min_periods=28 * 24).sum()
    execute_at = index + pd.Timedelta(hours=1)
    decision = (execute_at.weekday == 0) & (execute_at.hour == 1)
    for date in index[decision]:
        if date.date().isoformat() in blocked_entry_weeks:
            continue
        denominators = trailing.loc[date]
        if denominators.isna().any():
            continue
        asof = date + pd.Timedelta(hours=1)
        releases = np.array([scheduled_tokens(p, asof, calendar_shift_days=calendar_shift_days) for p in PAIRS])
        if not np.isfinite(releases).all():
            continue
        if mode == "control":
            weights.loc[date] = [.4, -.4]
            continue
        scores = releases / denominators.to_numpy()
        if np.isclose(scores[0], scores[1], rtol=1e-9, atol=1e-12):
            continue
        short = int(np.argmax(scores))
        weights.loc[date, PAIRS[short]] = -.4
        if mode == "ls":
            weights.loc[date, PAIRS[1 - short]] = .4
    return weights


def safe_stake(balance, weight, trades, marks, max_stake, min_stake):
    """Balance excludes open PnL; add it once and reserve closing costs.

    Cap *new* marked gross at 80% of marked equity. This is an entry check,
    not a promise to maintain a continuous gross or neutral-exposure cap.
    """
    equity, gross = float(balance), 0.
    if not isfinite(equity) or not isfinite(weight) or not 0 < abs(weight) <= .4:
        return 0.
    for trade in trades:
        mark = marks.get(trade.pair)
        if mark is None or not isfinite(mark) or mark <= 0:
            return 0.
        notional = trade.amount * mark
        direction = -1 if trade.is_short else 1
        equity += direction * trade.amount * (mark - trade.open_rate)
        equity -= trade.amount * trade.open_rate * (trade.fee_open or 0.)
        equity -= notional * (trade.fee_close or 0.)
        equity += trade.funding_fees or 0.
        gross += notional
    amount = min(abs(weight) * equity, .8 * equity - gross, max_stake)
    return float(amount) if isfinite(amount) and amount > 0 and amount >= (min_stake or 0.) else 0.


class UnlockPressureLSV1(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = True
    startup_candle_count = 672
    process_only_new_candles = True
    minimal_roi = {}
    stoploss = -.20
    use_exit_signal = True
    exit_profit_only = False
    position_adjustment_enable = False
    trailing_stop = False
    ignore_buying_expired_candle_after = 300
    mode = "ls"
    order_types = {"entry": "market", "exit": "market", "stoploss": "market", "stoploss_on_exchange": False}

    def __init__(self, config):
        super().__init__(config)
        self._weights = None
        self._key = None
        self._leg_snapshot = ()
        self.calendar_shift_days = int(config.get("unlock_pressure", {}).get("calendar_shift_days", 0))
        self.blocked_entry_weeks = config.get("unlock_pressure", {}).get("blocked_entry_weeks", [])

    def informative_pairs(self):
        return [(p, self.timeframe) for p in PAIRS]

    def _panel(self):
        frames = {p: self.dp.get_pair_dataframe(p, self.timeframe) for p in PAIRS}
        key = tuple((p, len(d), d["date"].iloc[-1] if not d.empty else None) for p, d in frames.items())
        if key != self._key:
            self._weights = build_weights(frames, self.mode, self.calendar_shift_days, self.blocked_entry_weeks)
            self._key = key

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        self._panel()
        pair = metadata["pair"]
        dataframe["supply_weight"] = dataframe["date"].map(self._weights[pair]) if pair in PAIRS else 0.
        dataframe["supply_weight"] = dataframe["supply_weight"].fillna(0.)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = (dataframe["supply_weight"] > 0).astype(int)
        dataframe["enter_short"] = (dataframe["supply_weight"] < 0).astype(int)
        dataframe["enter_tag"] = np.where(dataframe["supply_weight"] > 0, "lower_supply", "higher_supply")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        return dataframe

    def leverage(self, pair, current_time, current_rate, proposed_leverage, max_leverage, entry_tag, side, **kwargs):
        return 1.

    def bot_loop_start(self, current_time: datetime, **kwargs):
        # Freeze before any pair is processed. An intrabar stop in the first
        # pair must not trigger the other pair's exit at that same bar's open.
        self._leg_snapshot = tuple((t.pair, t.is_short) for t in Trade.get_trades_proxy(is_open=True))

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        age = current_time - trade.open_date_utc
        if age >= timedelta(hours=166):
            return "weekly_exit"
        if self.mode != "short" and age >= timedelta(minutes=5):
            hedge = any(p in PAIRS and p != pair and short != trade.is_short for p, short in self._leg_snapshot)
            if not hedge:
                return "missing_other_leg"
        return None

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake, min_stake,
                            max_stake, leverage, entry_tag, side, **kwargs):
        try:
            when = pd.Timestamp(current_time).floor("h") - pd.Timedelta(hours=1)
            weight = float(self._weights.at[when, pair])
            if (side == "long" and weight <= 0) or (side == "short" and weight >= 0):
                return 0.
            trades = Trade.get_trades_proxy(is_open=True)
            marks = {}
            for trade in trades:
                if trade.open_date_utc == current_time:
                    marks[trade.pair] = trade.open_rate
                    continue
                df, _ = self.dp.get_analyzed_dataframe(trade.pair, self.timeframe)
                known = df[df.date < current_time]
                if known.empty or known.date.iloc[-1] != when:
                    return 0.
                marks[trade.pair] = float(known.close.iloc[-1])
            balance = self.wallets.get_total(self.config["stake_currency"])
            return safe_stake(balance, weight, trades, marks, max_stake, min_stake)
        except (KeyError, ValueError, TypeError, AttributeError, IndexError):
            # Freqtrade otherwise falls back to the proposed stake after callback exceptions.
            return 0.


class UnlockPressureShortV1(UnlockPressureLSV1):
    mode = "short"


class FixedPairControlV1(UnlockPressureLSV1):
    mode = "control"
