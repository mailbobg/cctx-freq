"""Generalized spot accounting and gold sizing before any gold performance screen."""
import numpy as np
import pandas as pd
import pytest

from r10_gold import gold_weights, simulate_spot
from r1_r2b_risk import risk_weights, signals, RiskHoldR2b
from r8_crowding import close_at_final_close


def prices(periods=500):
    dates = pd.date_range('2020-01-01', periods=periods, tz='UTC', name='date')
    t = np.arange(periods)
    result = {}
    for asset, growth, amplitude in [('BTC', .001, .01), ('ETH', .002, .012), ('PAXG', .0002, .003)]:
        close = 100*np.exp(growth*t+amplitude*np.sin(t/5))
        result[asset+'_close'] = close
        result[asset+'_open'] = close*.999
    return pd.DataFrame(result, index=dates)


def test_disabled_gold_exactly_preserves_two_asset_book():
    px = prices()
    target = risk_weights(px, signals(px, RiskHoldR2b))
    begin, end = str(px.index[210].date()), str(px.index[-1].date())
    old = close_at_final_close(px, target, begin, end, .0012)
    target['PAXG'] = 0.
    actual = simulate_spot(px, target, begin, end, .0012)
    for column in ['equity', 'cash', 'exposure', 'cost', 'turnover']:
        np.testing.assert_allclose(actual[column], old[column], atol=1e-12, rtol=0)


def test_flat_gold_round_trip_loses_both_fees_with_reconciled_units():
    px = prices(220); px.loc[:, :] = 100.
    target = pd.DataFrame({'BTC':0., 'ETH':0., 'PAXG':.5}, index=px.index)
    curve = simulate_spot(px, target, '2020-07-20', '2020-08-07', .0012)
    assert curve.equity.iloc[-1] == pytest.approx(1-.495*.0012*2)
    assert curve.cost.sum() == pytest.approx(.495*.0012*2)
    assert curve.units_PAXG.iloc[-1] == 0
    assert curve.realized_price_pnl.iloc[-1] == pytest.approx(0., abs=1e-12)
    assert curve.equity.iloc[-1] == curve.cash.iloc[-1]
    assert curve.accounting_gap.abs().max() < 1e-12


def test_asset_specific_costs_cannot_credit_cash_or_hide_terminal_fee():
    px = prices(220); px.loc[:, :] = 100.
    target = pd.DataFrame({'BTC':0., 'ETH':0., 'PAXG':1.}, index=px.index)
    curve = simulate_spot(px, target, '2020-07-20', '2020-08-07', {'BTC':.0012, 'ETH':.0012, 'PAXG':.01})
    assert curve.cash.min() >= -1e-12
    assert curve.equity.iloc[-1] < .99
    assert curve.pre_close_units_PAXG.iloc[-1] > 0
    assert curve.cost.iloc[-1] > 0
    assert curve.accounting_gap.abs().max() < 1e-12


def test_partial_exit_price_profit_reconciles_after_final_sale():
    px = prices(260)
    target = pd.DataFrame({'BTC':.5, 'ETH':.3, 'PAXG':.2}, index=px.index)
    target.loc[px.index[235]:, 'BTC'] = .1
    target.loc[px.index[245]:, 'ETH'] = 0.
    curve = simulate_spot(px, target, '2020-07-20', str(px.index[-1].date()), .0012)
    assert curve.realized_price_pnl.iloc[-1] != 0
    assert curve.accounting_gap.abs().max() < 1e-12
    assert curve.filter(regex='^units_').min().min() >= -1e-12
    assert curve.exposure.iloc[-1] == 0
    assert curve.realized_price_pnl.iloc[-1]-curve.cost.sum() == pytest.approx(curve.equity.iloc[-1]-1)
    np.testing.assert_allclose(curve.filter(regex='^turnover_').sum(axis=1),curve.turnover,atol=1e-12,rtol=0)


def test_constant_gold_is_independent_of_its_momentum_and_three_asset_risk_is_causal():
    px = prices()
    px['PAXG_close'] = 100*np.exp(-.0002*np.arange(len(px)))
    px['PAXG_open'] = px.PAXG_close
    full = gold_weights(px)
    assert full.PAXG.iloc[210:].gt(0).all()
    assert full.sum(axis=1).le(1+1e-12).all()
    cov = np.log(px[['BTC_close','ETH_close','PAXG_close']]).diff().rolling(30).cov()*365
    for day in px.index[210:]:
        vector = full.loc[day].to_numpy()
        assert vector@cov.loc[day].to_numpy()@vector <= .1**2+1e-12
    pd.testing.assert_frame_equal(gold_weights(px.iloc[:350]), full.iloc[:350])


def test_missing_gold_calendar_blocks_all_covariance_until_full_consecutive_window():
    px = prices(); missing = px.index[300]
    px = px.drop(missing)
    out = gold_weights(px)
    assert missing in out.index
    assert out.loc[missing:missing+pd.Timedelta(days=30)].eq(0).all().all()
    assert out.loc[missing+pd.Timedelta(days=31):].PAXG.gt(0).all()


def test_held_execution_prefix_and_invalid_execution_prices():
    px = prices(); target = gold_weights(px)
    full = simulate_spot(px, target, '2020-07-20', str(px.index[-1].date()), .0012, terminal=False)
    part = simulate_spot(px.iloc[:350], target.iloc[:350], '2020-07-20', str(px.index[349].date()), .0012, terminal=False)
    pd.testing.assert_frame_equal(part, full.loc[part.index])
    assert part.pre_close_units_PAXG.gt(0).any()
    px.loc[px.index[220], 'PAXG_open'] = 0.
    with pytest.raises(ValueError, match='execution price'):
        simulate_spot(px, target, '2020-07-20', str(px.index[-1].date()), .0012)


def test_shifted_target_dates_cannot_trade_using_positional_lookahead():
    px = prices(); target = gold_weights(px)
    target.index = target.index+pd.Timedelta(days=1)
    with pytest.raises(ValueError,match='calendars'):
        simulate_spot(px,target,'2020-07-20',str(px.index[-1].date()),.0012)
