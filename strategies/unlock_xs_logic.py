"""Weekly selection of U2 (forward test of U1's rule; spec: bench/specs/2026-10-01-u2-unlock-forward.md).

Pure function, no freqtrade: given daily candles strictly before a Monday and the stored unlock schedule it returns the
coins to short that week. The backtest engine (bench/u1_unlock.py) and this function must agree week by week
(bench/test_u2_parity.py).
"""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

HORIZON_DAYS = 14
VOL_DAYS = 30
MIN_VALID_DAYS = 20


def load_schedule(path) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    df = pd.read_csv(path).sort_values(["pair", "ts"])
    return {pair: (g["ts"].to_numpy("int64"), g["tokens"].to_numpy(float)) for pair, g in df.groupby("pair")}


def unlock_tokens(schedule, pair: str, start_ts: int, end_ts: int) -> float:
    """Tokens unlocking in (start_ts, end_ts]."""
    ev = schedule.get(pair)
    if ev is None:
        return 0.0
    a, b = np.searchsorted(ev[0], start_ts, "right"), np.searchsorted(ev[0], end_ts, "right")
    return float(ev[1][a:b].sum()) if b > a else 0.0


def select_week(monday: datetime, frames: dict[str, pd.DataFrame], schedule, thr: float = 0.02, k: int = 6,
                top_n: int = 100, btc: str = "BTC/USDT:USDT", first_dates: dict | None = None,
                min_hist_days: int = 60) -> list[tuple[str, float]]:
    """[(pair, score)] best first. `monday` is a tz-aware UTC midnight; frames carry date, close, quote_volume.

    Score S = unlock value in (Monday, Monday + 14 d] / (14 × mean daily quote volume of the previous 30 days), with the
    value taken at the Sunday close. Universe: the `top_n` pairs by 30-day quote volume (BTC excluded).
    `first_dates` (pair → first candle date) enforces the 60-day history rule; live, the pair list's AgeFilter does it.
    """
    yesterday = monday - timedelta(days=1)
    win_start = monday - timedelta(days=VOL_DAYS)
    rows = []
    for pair, f in frames.items():
        if pair == btc:
            continue
        f = f[(f["date"] < monday) & (f["date"] >= win_start)]
        if f.empty or f["date"].iloc[-1] != yesterday or not np.isfinite(f["close"].iloc[-1]):
            continue
        q = f["quote_volume"].to_numpy(float)
        q = q[np.isfinite(q)]
        if len(q) < MIN_VALID_DAYS:
            continue
        if first_dates is not None and first_dates.get(pair, monday) > monday - timedelta(days=min_hist_days):
            continue
        rows.append((pair, float(q.sum()), float(q.mean()), float(f["close"].iloc[-1])))
    rows.sort(key=lambda r: (-r[1], r[0]))
    start = int(monday.timestamp())
    scored = []
    for pair, _, mean_q, last in rows[:top_n]:
        tokens = unlock_tokens(schedule, pair, start, start + HORIZON_DAYS * 86400)
        if tokens > 0 and mean_q > 0:
            s = tokens * last / (HORIZON_DAYS * mean_q)
            if s >= thr:
                scored.append((pair, s))
    scored.sort(key=lambda r: (-r[1], r[0]))
    return scored[:k]
