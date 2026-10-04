import numpy as np
import pandas as pd
import pytest

from r1_r2b_risk import RiskHoldR2b, signals
from r6_relative import ASSETS, simulate as old_simulate, weights as old_weights
from r15_trend import raw_signal, weights, banded_request, simulate
from test_r6_engine import inputs


def test_original_long_branch_and_disabled_weights_exact():
    close,_,_,_=inputs()
    px=close.rename(columns={'BTCUSDT':'BTC_close','ETHUSDT':'ETH_close'})
    old=signals(px,RiskHoldR2b).set_axis(ASSETS,axis=1)
    pd.testing.assert_frame_equal(raw_signal(close,shorts=False),old)
    pd.testing.assert_frame_equal(raw_signal(close),old)
    pd.testing.assert_frame_equal(weights(close,shorts=False),old_weights(close,'cov10_long'))


def test_symmetric_negative_amplitude_and_prefix_without_relative_ranking():
    close,_,_,_=inputs();close['ETHUSDT']=10000/close.BTCUSDT
    raw=raw_signal(close)
    assert raw.BTCUSDT.iloc[-1]>0 and raw.ETHUSDT.iloc[-1]<0
    assert raw.BTCUSDT.iloc[-1] == pytest.approx(-raw.ETHUSDT.iloc[-1])
    out=weights(close);cutoff=close.index[-8]
    pd.testing.assert_frame_equal(raw_signal(close.loc[:cutoff]),raw.loc[:cutoff])
    pd.testing.assert_frame_equal(weights(close.loc[:cutoff]),out.loc[:cutoff])
    assert out.abs().sum(axis=1).max()<=1+1e-12


def test_zero_momentum_is_flat_and_sma_equality_half_sizes_short():
    days=pd.date_range('2020-01-01',periods=260,tz='UTC')
    close=pd.DataFrame(100.,index=days,columns=ASSETS)
    assert raw_signal(close).eq(0).all().all()
    close.loc[days[-31],'ETHUSDT']=110
    close.loc[days[-41],'ETHUSDT']=90
    raw=raw_signal(close)
    sigma=np.log(close.ETHUSDT).diff().rolling(30).std().iloc[-1]*np.sqrt(365)
    assert raw.ETHUSDT.iloc[-1]==pytest.approx(-.25*min(1,.6/sigma))
    assert raw.ETHUSDT.iloc[:199].eq(0).all()


def test_missing_calendar_and_sma_cannot_create_short():
    close,_,_,_=inputs();close['ETHUSDT']=10000/close.BTCUSDT
    missing=close.index[-38];close=close.drop(index=missing)
    out=weights(close);raw=raw_signal(close)
    assert out.loc[missing:missing+pd.Timedelta(days=30)].eq(0).all().all()
    assert raw.loc[missing:,'ETHUSDT'].eq(0).all()


def test_same_sign_short_band_but_exit_and_tiny_flip_bypass():
    price=np.array([100.,100.]);equity=10000.
    request=banded_request([-10.,0.],[-12.,0.],[-.121212,0.],equity,price)
    assert request.tolist()==[-10.,0.]
    assert banded_request([-.1,0.],[.1,0.],[.001,0.],equity,price).tolist()==[.1,0.]
    assert banded_request([-.1,0.],[0.,0.],[0.,0.],equity,price).tolist()==[0.,0.]
    assert banded_request([0.,0.],[-.1,0.],[-.001,0.],equity,price).tolist()==[-.1,0.]


def flat_inputs():
    close,trade,mark,fund=inputs()
    for f in trade.values():f.loc[:,:]=100.
    for a in ASSETS:fund[a]['rate']=.001 if a=='BTCUSDT' else .002
    prescribed=pd.DataFrame(np.tile([.1,-.1],(len(close),1)),index=close.index,columns=ASSETS)
    return close,trade,mark,fund,prescribed


def test_disabled_long_execution_matches_old_ledger_and_costs():
    close,trade,mark,fund=inputs();w=old_weights(close,'cov10_long')
    expected=old_simulate(trade,mark,fund,w,'2021-02-01','2021-02-15',.0012,mechanism='cov10_long')
    daily,hours,orders=simulate(trade,mark,fund,w,'2021-02-01','2021-02-15',.0012,signed_band=False)
    pd.testing.assert_frame_equal(daily,expected[0])
    pd.testing.assert_frame_equal(hours[expected[1].columns],expected[1])
    assert orders.cost.sum()==pytest.approx(daily.cost.sum())


def test_funding_uses_old_monday_positions_before_flip_and_new_at_eight():
    close,trade,mark,fund,w=flat_inputs()
    w.loc['2021-02-07':]=[-.1,.1]
    daily,hours,orders=simulate(trade,mark,fund,w,'2021-02-01','2021-02-15',.0012)
    assert hours.loc['2021-02-01 00:00','funding']==0
    assert hours.loc['2021-02-08 00:00','funding']>0
    assert hours.loc['2021-02-08 08:00','funding']<0
    flips=orders.loc[orders.time.eq(pd.Timestamp('2021-02-08',tz='UTC'))]
    assert len(flips)==2 and (flips.old_quantity*flips.new_quantity<0).all()
    assert flips.cost.sum()==pytest.approx((flips.quantity_delta.abs()*flips.price).sum()*.0012)
    assert daily.balance.iloc[-1]==pytest.approx(10000+daily.realized_pnl.sum()+daily.funding.sum()-daily.cost.sum())


def test_flat_prices_zero_funding_lose_all_open_close_costs_not_short_proceeds():
    close,trade,mark,fund,w=flat_inputs()
    for f in fund.values():f['rate']=0.
    daily,hours,orders=simulate(trade,mark,fund,w,'2021-02-01','2021-02-07',.001)
    assert daily.balance.iloc[0]==pytest.approx(9998.02)
    assert daily.equity.iloc[-1]==pytest.approx(9996.04)
    assert daily.equity.iloc[-1]==pytest.approx(10000-orders.cost.sum())
    assert hours.gross.max()<.99
    assert hours.sampled_hour_running_dd_pct.iloc[-1]==pytest.approx(-.0396)


def test_execution_and_orders_prefix_with_held_shorts_and_flips():
    close,trade,mark,fund,w=flat_inputs();w.loc['2021-02-07':]=[-.1,.1]
    full=simulate(trade,mark,fund,w,'2021-02-01','2021-02-15',.0012,terminal=False)
    part=simulate(trade,mark,fund,w,'2021-02-01','2021-02-10',.0012,terminal=False)
    pd.testing.assert_frame_equal(part[0],full[0].loc[:'2021-02-10'])
    pd.testing.assert_frame_equal(part[1],full[1].loc[:'2021-02-10'])
    pd.testing.assert_frame_equal(part[2],full[2].loc[full[2].time<pd.Timestamp('2021-02-11',tz='UTC')].reset_index(drop=True))
    assert part[0].BTC_quantity.lt(0).any() and part[0].ETH_quantity.lt(0).any()


@pytest.mark.parametrize('bad', ['funding','mark','target','cost'])
def test_missing_inputs_and_invalid_cost_block_execution(bad):
    close,trade,mark,fund,w=flat_inputs();cost=.0012
    if bad=='funding':fund['BTCUSDT']=fund['BTCUSDT'].iloc[1:]
    if bad=='mark':mark['ETHUSDT']=mark['ETHUSDT'].iloc[1:]
    if bad=='target':w.loc['2021-01-31','ETHUSDT']=np.nan
    if bad=='cost':cost=-.001
    with pytest.raises(ValueError):simulate(trade,mark,fund,w,'2021-02-01','2021-02-07',cost)
