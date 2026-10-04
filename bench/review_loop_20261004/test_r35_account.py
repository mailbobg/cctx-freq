"""Risk evidence must include execution fees, chronological peaks and gaps."""
import numpy as np
import pandas as pd
import pytest
from r35_account import marked_account,exported_orders,risk_pair


def prices(index,values):
    return {a:pd.DataFrame(dict(open=v,close=v,high=v,low=v,trades=1),index=index)
            for a,v in values.items()}


def orders(index,items):
    rows=[]
    for pos,a,side,q,price,fee in items:
        notional=q*price
        rows.append(dict(timestamp=index[pos],asset=a,side=side,quantity=q,price=price,
                         fee_rate=fee,fee_USDT=notional*fee,
                         cash_delta=-notional*(1+fee) if side=='buy' else notional*(1-fee),
                         quantity_delta=q if side=='buy' else -q))
    return pd.DataFrame(rows)


def test_entry_and_terminal_fees_and_initial_peak_participate():
    idx=pd.date_range('2024-01-01',periods=3,freq='min',tz='UTC')
    px=prices(idx,dict(BTC=[100]*3,ETH=[10]*3))
    oo=orders(idx,[(0,'BTC','buy',90,100,.01),(2,'BTC','sell',90,100,.02)])
    risk,_=marked_account(idx,px,oo)
    assert risk['terminal_cash_USDT']==9730
    assert risk['total_explicit_fees_USDT']==270
    assert risk['sampled_OPEN_CLOSE_and_fill_DD_pct']==pytest.approx(-2.7)
    assert risk['sampled_peak']['equity_USDT']==10000
    assert risk['sampled_trough']['phase']=='after_individual_fill'


def test_pre_sell_peak_and_intermediate_fee_event_preserved():
    idx=pd.date_range('2024-01-01',periods=3,freq='min',tz='UTC')
    px=prices(idx,dict(BTC=[100,120,120],ETH=[10]*3))
    oo=orders(idx,[(0,'BTC','buy',90,100,0),(1,'BTC','sell',90,120,.1)])
    risk,_=marked_account(idx,px,oo)
    assert risk['sampled_peak']['phase']=='pre_fill_OPEN'
    assert risk['sampled_peak']['equity_USDT']==11800
    assert risk['sampled_OPEN_CLOSE_and_fill_DD_pct']==pytest.approx(-1080/11800*100)


def test_ohlc_envelope_not_synchronous_measured_peak():
    idx=pd.date_range('2024-01-01',periods=2,freq='min',tz='UTC')
    px=prices(idx,dict(BTC=[100]*2,ETH=[100]*2))
    for a in px:px[a]['high']=[100,110];px[a]['low']=[100,90]
    oo=orders(idx,[(0,'BTC','buy',20,100,0),(0,'ETH','buy',20,100,0)])
    risk,_=marked_account(idx,px,oo)
    assert risk['sampled_OPEN_CLOSE_and_fill_DD_pct']==0
    assert risk['conservative_OHLC_envelope_DD_pct']==pytest.approx((9600/10400-1)*100)
    assert risk['envelope_is_bound_not_synchronous_price_or_high_before_low_observation'] is True


def test_missing_held_minutes_not_filled_or_certified():
    idx=pd.date_range('2024-01-01',periods=4,freq='min',tz='UTC')
    px=prices(idx,dict(BTC=[100,90,80,100],ETH=[10]*4));px['BTC']=px['BTC'].drop(idx[2])
    oo=orders(idx,[(0,'BTC','buy',90,100,0),(3,'BTC','sell',90,100,0)])
    risk,_=marked_account(idx,px,oo)
    assert risk['held_missing_asset_minutes']['BTC']==1
    assert risk['missing_sampled_marks']==2
    assert risk['sampled_OPEN_CLOSE_and_fill_DD_pct']==pytest.approx(-9)
    assert not risk['complete_held_intraday_price_and_fill_coverage']


def test_missing_flat_companion_prices_do_not_invent_account_loss():
    idx=pd.date_range('2024-01-01',periods=3,freq='min',tz='UTC')
    px=prices(idx,dict(BTC=[100]*3,ETH=[10]*3));px['ETH']=px['ETH'].drop(idx[1])
    oo=orders(idx,[(0,'BTC','buy',90,100,0),(2,'BTC','sell',90,100,0)])
    risk,_=marked_account(idx,px,oo)
    assert risk['held_missing_asset_minutes']=={'BTC':0,'ETH':0}
    assert risk['complete_held_intraday_price_and_fill_coverage']


def test_order_price_mismatch_does_not_certify_fidelity():
    idx=pd.date_range('2024-01-01',periods=2,freq='min',tz='UTC')
    px=prices(idx,dict(BTC=[100]*2,ETH=[10]*2))
    oo=orders(idx,[(0,'BTC','buy',90,101,0),(1,'BTC','sell',90,100,0)])
    risk,_=marked_account(idx,px,oo)
    assert len(risk['original_fill_OPEN_parity_failures'])==1
    assert not risk['complete_held_intraday_price_and_fill_coverage']


def test_ignored_export_cost_does_not_double_charge_sell_fee():
    at=int(pd.Timestamp('2024-01-01',tz='UTC').timestamp()*1000)
    t=dict(is_short=False,leverage=1,funding_fees=0,is_open=False,pair='BTC/USDT',fee_open=.01,fee_close=.01,
           exit_reason='weekly_off',orders=[dict(order_filled_timestamp=at,ft_order_side='buy',amount=1,
           safe_price=100,ft_is_entry=True,cost=101),dict(order_filled_timestamp=at+86400000,
           ft_order_side='sell',amount=1,safe_price=110,ft_is_entry=False,cost=111.1)])
    f=exported_orders(dict(starting_balance=10000,trades=[t]),.01)
    assert f.cash_delta.tolist()==[-101,108.9]
    assert f.fee_USDT.sum()==pytest.approx(2.1)


def test_negative_inventory_is_not_a_hedge_or_infinite_cash():
    idx=pd.date_range('2024-01-01',periods=2,freq='min',tz='UTC')
    px=prices(idx,dict(BTC=[100]*2,ETH=[10]*2))
    with pytest.raises(ValueError,match='negative spot'):
        marked_account(idx,px,orders(idx,[(0,'BTC','sell',1,100,0)]))
