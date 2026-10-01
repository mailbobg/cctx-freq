"""Tests for the "what is this bot waiting for" notes.

Run: .venv/bin/python -m pytest user_data/panel/test_waiting.py -q
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from panel.waiting import listings_note, weekly_note  # noqa: E402


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def ts(*a):
    return utc(*a).timestamp()


def test_weekly_next_monday():
    n = weekly_note(utc(2026, 10, 1, 3, 12), 0, 1)           # Thursday 03:12
    assert "10-05 01:00 UTC（周一）" in n and "3 天 21 小时" in n


def test_weekly_same_day_before_and_after_the_hour():
    assert "10-05 01:00" in weekly_note(utc(2026, 10, 5, 0, 30), 0, 1)
    assert "10-12 01:00" in weekly_note(utc(2026, 10, 5, 1, 0), 0, 1)       # exactly at the hour: next week


def test_weekly_text_appended():
    assert weekly_note(utc(2026, 10, 1), 0, 1, "做空").endswith("。做空")


ROWS = [(ts(2026, 9, 6, 8), "PONS", "COIN"), (ts(2026, 9, 22, 9), "MOONSHOT", "PREMARKET"),
        (ts(2026, 9, 23, 9), "OURA", "PREMARKET")]


def test_listings_waiting_shows_latest_of_the_bots_types():
    now = utc(2026, 10, 1, 3, 0)
    coin = listings_note(now, ROWS, {"COIN"})
    both = listings_note(now, ROWS, {"COIN", "PREMARKET"})
    assert "PONS" in coin and "24 天前" in coin and "OURA" not in coin
    assert "OURA" in both and "7 天前" in both


def test_listings_entry_window_and_upcoming():
    rows = ROWS + [(ts(2026, 9, 28, 11, 45), "NEWA", "COIN"), (ts(2026, 9, 30, 6), "NEWB", "COIN")]
    n = listings_note(utc(2026, 10, 1, 3, 0), rows, {"COIN"})
    assert "入场窗口进行中：NEWA（窗口至 10-02 00:00 UTC）" in n       # D0 09-28 -> window 10-01
    assert "即将入场：NEWB（10-03 00:00 UTC 入场）" in n


def test_listings_window_closed_after_24h():
    n = listings_note(utc(2026, 10, 2, 0, 0), [(ts(2026, 9, 28, 11, 45), "NEWA", "COIN")], {"COIN"})
    assert n.startswith("等待新币上线") and "NEWA" in n


def test_listings_no_rows():
    assert listings_note(utc(2026, 10, 1), [], {"COIN"}) == "暂时读不到新合约列表"
