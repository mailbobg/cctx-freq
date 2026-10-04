"""Verify research execution against the bot's entry/off/adjustment distinction."""
import numpy as np
import pandas as pd

from r1_r2b_risk import ASSETS, RiskHoldR2b, simulate


def flat_fixture():
    dates = pd.date_range("2023-01-01", periods=230, tz="UTC")
    px = pd.DataFrame(100., index=dates, columns=[f"{a}_{kind}" for a in ASSETS for kind in ("open", "close")])
    first = next(i for i in range(200, len(dates)) if dates[i].weekday() == 0)
    return px, first


def test_initial_entry_is_not_suppressed_by_adjustment_band():
    px, first = flat_fixture()
    weights = pd.DataFrame(0., index=px.index, columns=ASSETS)
    weights.iloc[:, 0] = .03
    signal = pd.DataFrame({"date": [px.index[first - 1]], "decide": [True], "weight": [.03]})
    assert RiskHoldR2b({}).populate_entry_trend(signal, {})["enter_long"].iloc[-1] == 1
    curve = simulate(px, weights, str(px.index[first].date()), str(px.index[first].date()), .0012)
    assert curve.exposure.iloc[-1] > .029
    assert curve.cost.iloc[-1] > 0


def test_zero_weight_exits_even_when_position_is_below_adjustment_band():
    px, first = flat_fixture()
    weights = pd.DataFrame(0., index=px.index, columns=ASSETS)
    # Enter above the band, then drift-free reduce to just under5% on the
    # following Monday; a separate zero-weight signal must fully exit it.
    weights.iloc[:first, 0] = .10
    weights.iloc[first:first + 7, 0] = .045
    signal = pd.DataFrame({"date": [px.index[first + 13]], "decide": [True], "weight": [0.]})
    assert RiskHoldR2b({}).populate_exit_trend(signal, {})["exit_long"].iloc[-1] == 1
    curve = simulate(px, weights, str(px.index[first].date()), str(px.index[first + 14].date()), .0012)
    assert 0 < curve.exposure.iloc[7] < .05
    assert curve.exposure.iloc[-1] == 0
    assert curve.cost.iloc[-1] > 0


def test_small_active_adjustment_still_uses_the_band():
    px, first = flat_fixture()
    weights = pd.DataFrame(0., index=px.index, columns=ASSETS)
    weights.iloc[:, 0] = .20
    weights.iloc[first:, 0] = .21
    curve = simulate(px, weights, str(px.index[first].date()), str(px.index[first + 7].date()), .0012)
    assert curve.cost.iloc[0] > 0
    assert curve.cost.iloc[-1] == 0
    assert np.isclose(curve.exposure.iloc[0], curve.exposure.iloc[-1])


def test_terminal_liquidation_uses_open_price_and_pays_the_exit_cost():
    px, first = flat_fixture()
    weights = pd.DataFrame(0., index=px.index, columns=ASSETS)
    weights.iloc[:, 0] = .5
    px.loc[px.index[first], "BTC_close"] = 200.
    day = str(px.index[first].date())
    curve = simulate(px, weights, day, day, .0012, terminal_liquidation=True)
    assert curve.exposure.iloc[-1] == 0
    assert np.isclose(curve.equity.iloc[-1], 1 - .495 * .0012 * 2)
