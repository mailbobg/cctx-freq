"""Hand-checkable tests for the U1 engine on synthetic data (no files needed).

Run: .venv/bin/python -m pytest -c /dev/null --rootdir user_data/bench user_data/bench/test_u1_unlock.py -q
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import u1_unlock as U  # noqa: E402

IDX = pd.date_range("2021-10-01", "2026-09-05", freq="D", tz="UTC")
COLS = ["AAAUSDT", "BBBUSDT", "BTCUSDT", "CCCUSDT"]


def day(s):
    return int(IDX.get_loc(pd.Timestamp(s, tz="UTC")))


def make(drop_day="2023-03-15", drop_to=9.0, spike=None):
    n = len(IDX)
    C = np.full((n, 4), 10.0)
    C[:, 2] = 100.0
    C[day(drop_day):, 0] = drop_to
    H = C.copy()
    if spike:
        d, hi, cl = spike
        H[day(d), 0] = hi
        C[day(d):, 0] = cl
        H[day(d) + 1:, 0] = cl
    D = U.Data.__new__(U.Data)
    D.cols, D.idx = COLS, IDX
    D.C, D.H = C, H
    D.QV = np.full((n, 4), 1000.0)
    D.F = np.zeros((n, 4))
    D.btc = 2
    D.sec = IDX.as_unit("s").asi8
    D.first = np.zeros(4, dtype=int)
    ts = int(pd.Timestamp("2023-03-14 12:00", tz="UTC").timestamp())
    D.ev = {(0, "insiders"): (np.array([ts]), np.array([100_000.0]))}       # 100k tokens × 10 = 1e6 USD ≫ 14 × 1000
    D.ev_syms = [0]
    return D


def test_short_week_pnl_matches_hand_calculation():
    D = make()
    res = U.run(D, U.Params())
    assert res["sels"][day("2023-03-06")] == (0,)                           # unlock 03-14 is inside both Mondays' 14-day windows
    assert res["sels"][day("2023-03-13")] == (0,)
    assert res["sels"][day("2023-03-20")] == ()                             # unlock now in the past
    eq0 = 10_000.0
    units = 0.05 * eq0 / 10.0                                                # 50 units short at 10
    gain = units * (10.0 - 9.0)                                              # falls on Wed 03-15
    costs = (units * 10 * 0.001 + (units * 10 / 100) * 100 * 0.0007) \
        + (units * 9 * 0.001 + (units * 10 / 100) * 100 * 0.0007)            # enter + cover (covered at 9 after the drop)
    end = res["equity"].loc[pd.Timestamp("2023-03-20", tz="UTC")]
    assert abs(end - (eq0 + gain - costs)) < 0.5
    assert res["liqs"] == 0 and res["coin_weeks"] == 2


def test_liquidation_loses_the_whole_notional():
    D = make(drop_day="2026-06-01", spike=("2023-03-15", 20.0, 15.0))      # high 2x entry, close 1.5x
    res = U.run(D, U.Params())
    assert res["liqs"] == 1
    loss = 50 * (20.0 - 10.0)                                                # settled at 2x entry
    end = res["equity"].loc[pd.Timestamp("2023-03-16", tz="UTC")]
    assert abs(end - (10_000 - loss)) < 20                                   # minus small fees, BTC leg flat


def test_control_never_picks_the_unlocking_coin_and_matches_count():
    D = make()
    for seed in range(5):
        res = U.run(D, U.Params(), control_seed=seed)
        assert res["coin_weeks"] == 2                                        # same count as the real arm: 1 coin in 2 weeks
    from u1_unlock import control_pool, prep, select
    QV30, MV = prep(D.C, D.QV)
    t = day("2023-03-13")
    sel, cand = select(D, t, U.Params(), QV30, MV)
    pool = control_pool(D, t, U.Params(), cand)
    assert 0 not in pool and set(pool) == {1, 3}


def test_selection_ignores_future_prices():
    D = make()
    t = day("2023-03-13")
    QV30, MV = U.prep(D.C, D.QV)
    full, _ = U.select(D, t, U.Params(), QV30, MV)
    C2 = D.C.copy()
    C2[t:] = np.nan
    cut, _ = U.select(D, t, U.Params(), QV30, MV, C=C2)
    assert full == cut


def test_pnl_scales_with_position_weight():
    D = make()
    small = U.run(D, U.Params(weight=0.05))["equity"].iloc[-1] - 10_000
    big = U.run(D, U.Params(weight=0.10))["equity"].iloc[-1] - 10_000
    assert small > 0 and abs(big / small - 2.0) < 0.1                       # the 10 % fall pays; double size ≈ double P&L
