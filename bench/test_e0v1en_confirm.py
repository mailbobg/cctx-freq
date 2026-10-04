"""Entry confirmation invariants, including no future-bar dependence."""

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "strategies"))
from E0V1ENConfirm import delay_entries, confirm_entries  # noqa: E402


class ConfirmationTest(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame(
            {
                "low": [99.0, 99.2, 99.4, 99.1, 97.0, 96.0, 95.0, 94.0],
                "close": [100.0, 99.5, 100.2, 101.0, 98.0, 97.0, 96.0, 95.0],
                "enter_long": [1, 0, 0, 0, 1, 0, 0, 0],
                "enter_tag": ["buy_new", "", "", "", "buy_1", "", "", ""],
            }
        )

    def test_confirmation_waits_for_three_completed_bars(self):
        delayed = delay_entries(self.frame.copy())
        confirmed = confirm_entries(self.frame.copy())
        self.assertEqual(list(delayed["enter_long"]), [0, 0, 0, 1, 0, 0, 0, 1])
        self.assertEqual(list(confirmed["enter_long"]), [0, 0, 0, 1, 0, 0, 0, 0])
        # E0V1EN.custom_exit branches on the exact "buy_new" tag.
        self.assertEqual(confirmed.loc[3, "enter_tag"], "buy_new")

    def test_future_rows_cannot_change_past_signals(self):
        full = confirm_entries(self.frame.copy())
        for length in range(1, len(self.frame) + 1):
            prefix = confirm_entries(self.frame.iloc[:length].copy())
            self.assertEqual(
                list(prefix["enter_long"]), list(full["enter_long"].iloc[:length])
            )

    def test_wait_only_preserves_original_tag_without_future_bars(self):
        full = delay_entries(self.frame.copy())
        self.assertEqual(full.loc[3, "enter_tag"], "buy_new")
        self.assertEqual(full.loc[7, "enter_tag"], "buy_1")
        for length in range(1, len(self.frame) + 1):
            prefix = delay_entries(self.frame.iloc[:length].copy())
            self.assertEqual(
                list(prefix["enter_long"]), list(full["enter_long"].iloc[:length])
            )


if __name__ == "__main__":
    unittest.main()
