"""Delayed-entry research variants for E0V1EN.

See docs/plans/2026-10-04-e0v1en-confirmation-research-design.md.
The original E0V1EN source and e0v1en-live configuration remain unchanged.
E0V1ENWait3 is used only by the e0v1en spot dry-run for forward observation.
"""

import sys
from pathlib import Path

from pandas import DataFrame

sys.path.insert(0, str(Path(__file__).parent))
from E0V1ENPF import E0V1ENPF  # noqa: E402


def delay_entries(dataframe: DataFrame) -> DataFrame:
    """Move each original signal to the third completed bar after it."""
    delayed = dataframe["enter_long"].fillna(0).eq(1).shift(3, fill_value=False)
    tags = dataframe["enter_tag"].shift(3).fillna("")
    dataframe["enter_long"] = delayed.astype(int)
    dataframe["enter_tag"] = tags.where(delayed, "")
    return dataframe


def confirm_entries(dataframe: DataFrame) -> DataFrame:
    """Require stabilization after a signal, using only completed bars."""
    delayed = delay_entries(dataframe)
    signal_low = delayed["low"].shift(3)
    signal_close = delayed["close"].shift(3)
    intervening_low = delayed["low"].rolling(3, min_periods=3).min()
    confirmed = (
        delayed["enter_long"].eq(1)
        & intervening_low.gt(signal_low)
        & delayed["close"].gt(signal_close)
    )
    delayed["enter_long"] = confirmed.astype(int)
    delayed["enter_tag"] = delayed["enter_tag"].where(confirmed, "")
    return delayed


class E0V1ENWait3(E0V1ENPF):
    """Delay-only control: no stabilization condition."""

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return delay_entries(super().populate_entry_trend(dataframe, metadata))


class E0V1ENConfirm3(E0V1ENPF):
    """Candidate: delay three bars and require a higher low and close."""

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return confirm_entries(super().populate_entry_trend(dataframe, metadata))
