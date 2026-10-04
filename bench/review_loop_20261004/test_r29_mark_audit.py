"""Recover intraminute capital loss even when daily closes fully recover."""
import pandas as pd
import pytest

from r29_mark_audit import mark_path


def fixture(cost=0., terminal=False):
    day=pd.Timestamp('2024-01-01',tz='UTC');index=pd.date_range(day,periods=1440,freq='min')
    minutes={a:pd.DataFrame({'open':10.,'close':10.},index=index) for a in ('BTC','ETH')}
    if not terminal:minutes['BTC'].iloc[1,0]=9.
    fee=.6*cost;cash=.4-fee;equity=1-fee
    fills=[dict(time=day,asset='BTC',reason='weekly',price=10.,native_quantity=.06,dollar_amount=.6,cost=fee)]
    if terminal:
        fills.append(dict(time=day+pd.Timedelta(minutes=1439),asset='BTC',reason='terminal_daily_close',price=10.,native_quantity=-.06,dollar_amount=-.6,cost=fee))
        cash=equity-fee;equity=cash
    low=equity if terminal else .94
    risk=(low-1)*100
    curve=pd.DataFrame([dict(date=day,equity=equity,cash=cash,units_BTC=0. if terminal else .06,units_ETH=0.,
        sampled_minute_peak_equity=1.,sampled_minute_running_dd_pct=risk,
        sampled_minute_day_min_equity=low,sampled_minute_day_max_equity=1.)]).set_index('date')
    return curve,pd.DataFrame(fills),minutes,cost


def test_daily_recovery_does_not_hide_native_minute_open_drawdown():
    result=mark_path(*fixture())
    assert result['native_account_max_DD_pct']==pytest.approx(-6.)
    assert result['first_5pct_breach']['time']=='2024-01-01T00:01:00+00:00'
    assert result['first_5pct_breach']['stage']=='OPEN'
    assert result['minute_open_close_marks_checked']==2880


def test_initial_prefill_cash_and_terminal_fee_both_enter_peak_drawdown():
    result=mark_path(*fixture(.01,terminal=True))
    assert result['native_account_max_DD_pct']==pytest.approx(-1.2)
    assert result['worst_trough']['stage']=='terminal_CASH'
    assert result['first_5pct_breach'] is None


@pytest.mark.parametrize('column',['sampled_minute_peak_equity','sampled_minute_running_dd_pct',
    'sampled_minute_day_min_equity','sampled_minute_day_max_equity'])
def test_altered_daily_reported_minute_risk_labels_rejected(column):
    curve,orders,minutes,cost=fixture()
    curve[column]+=0.1
    with pytest.raises(ValueError):mark_path(curve,orders,minutes,cost)


def test_non_native_fill_price_cannot_build_a_risk_path():
    curve,orders,minutes,cost=fixture()
    orders.loc[0,'price']=10.01
    with pytest.raises(ValueError):mark_path(curve,orders,minutes,cost)


def test_missing_native_minute_is_not_filled_to_recover_risk():
    curve,orders,minutes,cost=fixture()
    minutes['BTC']=minutes['BTC'].drop(minutes['BTC'].index[1])
    with pytest.raises(ValueError):mark_path(curve,orders,minutes,cost)
