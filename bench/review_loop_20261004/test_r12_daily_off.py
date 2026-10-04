import numpy as np
import pandas as pd
import pytest

from r10_gold import simulate_spot
from r12_daily_off import simulate


def inputs():
    index = pd.date_range('2023-01-01', periods=250, tz='UTC')
    px = pd.DataFrame(100., index=index, columns=['BTC_open', 'BTC_close', 'ETH_open', 'ETH_close'])
    weights = pd.DataFrame(.2, index=index, columns=['BTC', 'ETH'])
    momentum = pd.DataFrame(1., index=index, columns=['BTC', 'ETH'])
    return px, weights, momentum


def run(px, weights, momentum, end='2023-08-11', **kw):
    return simulate(px, weights, momentum, '2023-08-07', end, .0012, **kw)


def test_disabled_overlay_reproduces_common_cash_ledger():
    px, weights, momentum = inputs()
    expected = simulate_spot(px, weights, '2023-08-07', '2023-08-11', .0012)
    actual = run(px, weights, momentum, daily_exit=False)
    pd.testing.assert_frame_equal(actual[expected.columns], expected)


def test_previous_close_off_exits_next_open_with_gap_and_cost():
    px, weights, momentum = inputs()
    momentum.loc['2023-08-07', 'BTC'] = 0
    px.loc['2023-08-08', ['BTC_open', 'BTC_close']] = 80.
    curve = run(px, weights, momentum, terminal=False)
    held = curve.loc['2023-08-07', 'units_BTC']
    exit_day = curve.loc['2023-08-08']
    assert held > 0 and exit_day.units_BTC == 0
    assert exit_day.turnover_BTC == pytest.approx(held * 80)
    assert exit_day.cost == pytest.approx(held * 80 * .0012)
    assert exit_day.realized_price_pnl == pytest.approx(held * (80 - 100))
    assert exit_day.daily_off_exit_BTC


def test_today_close_cannot_trigger_today_open_sale():
    px, weights, momentum = inputs()
    px.loc['2023-08-08', 'BTC_close'] = 20
    momentum.loc['2023-08-08', 'BTC'] = -1
    curve = run(px, weights, momentum, terminal=False)
    assert curve.loc['2023-08-08', 'units_BTC'] > 0
    assert curve.loc['2023-08-08', 'turnover_BTC'] == 0
    assert curve.loc['2023-08-09', 'units_BTC'] == 0


def test_midweek_recovery_waits_until_monday_other_asset_unchanged():
    px, weights, momentum = inputs()
    momentum.loc['2023-08-07', 'BTC'] = -1
    curve = run(px, weights, momentum, end='2023-08-14', terminal=False)
    assert curve.loc['2023-08-08':'2023-08-13', 'units_BTC'].eq(0).all()
    assert curve.loc['2023-08-14', 'units_BTC'] > 0
    assert curve.loc['2023-08-07':'2023-08-13', 'units_ETH'].nunique() == 1
    assert curve.loc['2023-08-08':'2023-08-13', 'turnover_ETH'].eq(0).all()


def test_monday_off_is_charged_once():
    px, weights, momentum = inputs()
    momentum.loc['2023-08-13', 'BTC'] = 0
    weights.loc['2023-08-13', 'BTC'] = 0
    curve = run(px, weights, momentum, end='2023-08-14', terminal=False)
    held = curve.loc['2023-08-13', 'units_BTC']
    assert held > 0
    assert curve.loc['2023-08-14', 'turnover_BTC'] == pytest.approx(held * 100)
    assert curve.loc['2023-08-14', 'cost'] == pytest.approx(held * 100 * .0012)


def test_real_held_execution_prefix_and_terminal_cost_identity():
    px, weights, momentum = inputs()
    momentum.loc['2023-08-07', 'BTC'] = -1
    full = run(px, weights, momentum, end='2023-08-14', terminal=False)
    part = run(px.loc[:'2023-08-10'], weights.loc[:'2023-08-10'], momentum.loc[:'2023-08-10'], end='2023-08-10', terminal=False)
    pd.testing.assert_frame_equal(part, full.loc[part.index])
    assert part.units_ETH.gt(0).all() and part.units_BTC.gt(0).any()
    terminal = run(px, weights, momentum)
    assert terminal.iloc[-1].units_BTC == terminal.iloc[-1].units_ETH == 0
    assert terminal.iloc[-1].equity == pytest.approx(1 - terminal.iloc[-1].cumulative_cost)
    assert terminal.accounting_gap.abs().max() < 1e-12


def test_unknown_prior_momentum_is_not_a_tradable_signal():
    px, weights, momentum = inputs()
    momentum.loc['2023-08-07', 'BTC'] = np.nan
    with pytest.raises(ValueError, match='prior momentum'):
        run(px, weights, momentum)


def test_signal_calendar_cannot_be_shifted():
    px, weights, momentum = inputs()
    with pytest.raises(ValueError, match='calendar'):
        run(px, weights, momentum.iloc[1:])
