"""U2's live selection function must pick exactly what the U1 backtest engine picks, Monday by Monday.

Run: .venv/bin/python -m pytest -c /dev/null --rootdir user_data/bench user_data/bench/test_u2_parity.py -q
Needs the real data files (data_p1, data_f1, bench/events); skipped when they are missing.
"""

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "strategies"))
import u1_unlock as U  # noqa: E402
from unlock_xs_logic import load_schedule, select_week  # noqa: E402

DATA_OK = Path("user_data/data_f1/daily_ohlc.parquet").exists() and Path("user_data/bench/events/unlocks.parquet").exists()
pytestmark = pytest.mark.skipif(not DATA_OK, reason="needs the local data files")


def frames_before(D, t):
    """daily candles of every perp strictly before row t, in the live format (date, close, quote_volume)."""
    out = {}
    lo = max(0, t - 40)
    for j, sym in enumerate(D.cols):
        c = D.C[lo:t, j]
        if np.isnan(c).all():
            continue
        out[sym[:-4] + "/USDT:USDT"] = pd.DataFrame({"date": D.idx[lo:t], "close": c, "quote_volume": D.QV[lo:t, j]})
    return out


def test_live_selection_equals_backtest_selection():
    D = U.Data()
    p = replace(U.Params(), thr=0.02, k=6)
    res = U.run(D, p, sels_only=True)
    schedule = load_schedule("user_data/strategies/unlock_schedule.csv")
    first = {s[:-4] + "/USDT:USDT": D.idx[D.first[j]] for j, s in enumerate(D.cols) if D.first[j] < 10**9}
    mondays = [t for t in res["sels"]]
    rng = np.random.default_rng(7)
    picked = sorted(rng.choice(mondays, 60, replace=False).tolist() + [m for m in mondays if res["sels"][m]][:25])
    nonempty = 0
    for t in picked:
        frames = frames_before(D, t)
        live = select_week(D.idx[t].to_pydatetime(), frames, schedule, thr=0.02, k=6, first_dates=first)
        want = [D.cols[j][:-4] + "/USDT:USDT" for j in res["sels"][t]]
        assert [p_ for p_, _ in live] == want, (D.idx[t], live, want)
        nonempty += bool(want)
    assert nonempty >= 15                         # the comparison is not vacuous
