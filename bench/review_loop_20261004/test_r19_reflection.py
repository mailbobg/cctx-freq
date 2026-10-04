import pandas as pd
import pytest

from r19_reflection import native_reconcile


def fixture():
    t=pd.Timestamp('2024-01-08',tz='UTC'); end=t+pd.Timedelta(hours=23,minutes=59)
    minutes={a:pd.DataFrame({'open':[100.,110.],'close':[100.,110.]},index=[t,end]) for a in ('BTC','ETH')}
    # Actual buy .004 BTC at100, fee .0004; close at110, fee .00044.
    orders=pd.DataFrame([dict(time=t,asset='BTC',reason='weekly',price=100.,native_quantity=.004,dollar_amount=.4,cost=.0004),
        dict(time=end,asset='BTC',reason='terminal_daily_close',price=110.,native_quantity=-.004,dollar_amount=-.44,cost=.00044)])
    curve=pd.DataFrame([dict(pre_trade_open_equity=1.,cash=1.03916,units_BTC=0.,units_ETH=0.,equity=1.03916,
        cost=.00084,turnover_BTC=.84,turnover_ETH=0.)],index=[t])
    return curve,orders,minutes


def test_independent_native_source_units_and_cash_not_stake_convention():
    c,o,m=fixture();r=native_reconcile(c,o,m,.001)
    assert r['source_matched_native_fills']==2
    assert r['max_abs_cash_units_equity_fees_turnover_gap']<1e-12


@pytest.mark.parametrize('column,value', [('price',101.),('native_quantity',.008),('cost',0.),('time',pd.Timestamp('2024-01-08T01:00:00Z'))])
def test_wrong_source_price_quantity_cost_or_fill_time_is_rejected(column,value):
    c,o,m=fixture();o.loc[0,column]=value
    with pytest.raises(ValueError):native_reconcile(c,o,m,.001)


def test_retained_equity_cannot_mask_wrong_native_cash():
    c,o,m=fixture();c.loc[c.index[0],'cash']+=.001
    with pytest.raises(ValueError,match='reconciliation'):native_reconcile(c,o,m,.001)


def test_retained_csv_repr_roundtrips_before_source_identity_check(tmp_path):
    from r19_reflection import read_frame
    expected=pd.DataFrame({'price':[.12345678901234567], 'native_quantity':[.004000000000000001], 'cost':[.00040000000000000013]})
    path=tmp_path/'fills.csv';expected.to_csv(path,index=False)
    # Default pandas parsing can move a saved source price by an ULP.
    assert pd.read_csv(path).price.iloc[0] != expected.price.iloc[0]
    pd.testing.assert_frame_equal(read_frame(path),expected)
