"""Execution oracles fixed before historical R17 performance."""
import numpy as np
import pandas as pd
import pytest

from r17_carry import ASSETS,FundingSeries
from r17_engine import simulate


def fixture(end='2024-01-16',selected=None,first_rate='0'):
    start=pd.Timestamp('2024-01-01',tz='UTC');finish=pd.Timestamp(end,tz='UTC')
    hours=pd.date_range(start,finish,freq='h',inclusive='left')
    frames={s:pd.DataFrame(100.,index=hours,columns=['open','high','low','close']) for s in ASSETS}
    spot={s:f.copy() for s,f in frames.items()};trade={s:f.copy() for s,f in frames.items()};mark={s:f.copy() for s,f in frames.items()}
    # A one-hour scheduled offset makes the weekly old-book payment observable.
    slots=pd.date_range(start-pd.Timedelta(hours=7),finish+pd.Timedelta(hours=1),freq='8h',inclusive='left')
    events=pd.DataFrame({'slot':slots,'rate_decimal':first_rate,'interval_hours':8,'mark':100.},index=slots)
    fund={s:FundingSeries(events,start-pd.Timedelta(hours=7),finish+pd.Timedelta(hours=1)) for s in ASSETS}
    decisions=[]
    for day in pd.date_range(start,finish,freq='W-MON',inclusive='left'):
        hour=day+pd.Timedelta(hours=1)
        decisions.append({'decision':hour.isoformat(),'selected':list((selected or {}).get(day.strftime('%Y-%m-%d'),()))})
    return spot,trade,mark,fund,decisions,start,finish


def run(data,cost=.0012,policy='ranked',terminal=True):
    return simulate(*data,cost=cost,policy=policy,terminal=terminal)


def test_flat_cash_unknown_prices_independent_constant():
    data=fixture()
    for kind in data[:3]:
        for frame in kind.values():frame[:]=np.nan
    daily,hours,orders=run(data)
    assert np.all(hours.equity.to_numpy()==10000.)
    assert daily.equity.eq(10000.).all() and daily.cost.eq(0).all() and daily.funding.eq(0).all()
    assert orders.empty and not hours.marked_margin_concern.any()


def test_one_slot_cash_and_equal_control_same_occupied_gross():
    data=fixture(selected={'2024-01-01':[ASSETS[0]]})
    _,ranked,ro=run(data);_,equal,eo=run(data,policy='equal_pairs')
    t=pd.Timestamp('2024-01-01 01:00',tz='UTC')
    q=.99*2500/(100*(1+.0012))
    assert ranked.loc[t,ASSETS[0]+'_spot_quantity']==pytest.approx(q)
    assert ranked.loc[t,ASSETS[1]+'_spot_quantity']==0
    assert ranked.loc[t,'gross_notional']==pytest.approx(equal.loc[t,'gross_notional'])
    assert ranked.loc[t,'spot_cash']==pytest.approx(equal.loc[t,'spot_cash'])
    assert len(ro)==4 and len(eo)==32


def test_old_quantity_funding_before_weekly_close_and_new_entry():
    data=fixture(selected={'2024-01-01':[ASSETS[0]],'2024-01-08':[ASSETS[1]]},first_rate='.001')
    _,hour,orders=run(data,cost=0)
    first=pd.Timestamp('2024-01-01 01:00',tz='UTC');change=first+pd.Timedelta(days=7)
    assert hour.loc[first,'funding']==0
    old_q=.99*2500/100
    assert hour.loc[change,'funding']==pytest.approx(old_q*100*.001)
    assert hour.loc[change,ASSETS[0]+'_spot_quantity']==0
    assert hour.loc[change,ASSETS[1]+'_spot_quantity']>0
    assert orders.loc[orders.date.eq(change),'action'].tolist()==['close','close','open','open']
    assert hour.loc[change,'after_funding_equity']-hour.loc[change,'before_funding_equity']==pytest.approx(hour.loc[change,'funding'])


def test_identical_price_move_cancels_and_four_actual_costs():
    data=fixture(selected={'2024-01-01':[ASSETS[0]]})
    boundary=pd.Timestamp('2024-01-08 01:00',tz='UTC')
    for kind in data[:3]:
        for frame in kind.values():frame.loc[boundary:]=120.
    daily,hour,orders=run(data)
    q=.99*2500/(100*1.0012)
    expected_cost=q*(2*100+2*120)*.0012
    assert daily.realized_price_pnl.sum()==pytest.approx(0)
    assert daily.cost.sum()==pytest.approx(expected_cost)
    assert hour.equity.iloc[-1]==pytest.approx(10000-expected_cost)
    assert orders.cost.sum()==pytest.approx(expected_cost)
    assert len(orders)==4


def test_basis_and_associated_mark_funding_hand_oracle_terminal_costs():
    data=fixture(end='2024-01-02',selected={'2024-01-01':[ASSETS[0]]},first_rate='.001')
    for kind in data[1:3]:
        for frame in kind.values():frame.loc[pd.Timestamp('2024-01-01 23:00',tz='UTC'),['close','high']]=102.
    # Associated payment mark is original event data, not that hour's OPEN.
    for series in data[3].values():series.events.loc[series.events.slot.dt.hour.eq(9),'mark']=110.
    daily,hour,orders=run(data)
    q=.99*2500/(100*1.0012)
    funding=q*(110+100)*.001
    costs=q*(100+100+100+102)*.0012
    assert daily.funding.sum()==pytest.approx(funding)
    assert daily.realized_price_pnl.sum()==pytest.approx(-2*q)
    assert hour.equity.iloc[-1]==pytest.approx(10000-2*q+funding-costs)
    assert len(orders)==4 and orders.action.tolist()==['open','open','terminal','terminal']
    assert hour.iloc[-1].filter(like='_quantity').eq(0).all()


def test_margin_failure_visible_despite_positive_combined_account():
    data=fixture(end='2024-01-02',selected={'2024-01-01':ASSETS[:2]})
    at=pd.Timestamp('2024-01-01 10:00',tz='UTC')
    for frame in data[2].values():frame.loc[at,'high']=220.
    daily,hour,_=run(data)
    assert hour.loc[at,'equity']>0 and hour.loc[at,'adverse_futures_equity']<0
    assert hour.loc[at,'envelope_margin_concern']
    assert not hour.marked_margin_concern.any()


def test_invested_full_prefix_same_curve_quantities_orders():
    data=fixture(selected={'2024-01-01':ASSETS[:2],'2024-01-08':ASSETS[2:4]},first_rate='.001')
    for i,kind in enumerate(data[:3]):
        for j,frame in enumerate(kind.values()):
            path=100+np.arange(len(frame))*.003+j*.1
            for col in frame.columns:frame[col]=path+i*.01
    _,full,orders=run(data,terminal=False)
    finish=pd.Timestamp('2024-01-10',tz='UTC')
    prefix=list(data);prefix[6]=finish
    prefix[4]=[r for r in data[4] if pd.Timestamp(r['decision'])<finish]
    for k in range(3):prefix[k]={s:f.loc[f.index<finish].copy() for s,f in data[k].items()}
    prefix[3]={s:FundingSeries(f.events.loc[f.events.slot<finish],f.coverage_start,finish) for s,f in data[3].items()}
    _,partial,po=run(prefix,terminal=False)
    pd.testing.assert_frame_equal(full.loc[full.index<finish],partial)
    pd.testing.assert_frame_equal(orders.loc[orders.date<finish].reset_index(drop=True),po)


def test_unknown_held_price_missing_week_or_unknown_policy_fail_closed():
    data=fixture(selected={'2024-01-01':[ASSETS[0]]})
    data[0][ASSETS[1]].iloc[2,0]=np.nan
    with pytest.raises(ValueError,match='price'):run(data)
    data=fixture(selected={'2024-01-01':[ASSETS[0]]})
    data[4].pop(1)
    with pytest.raises(ValueError,match='decision'):run(data)
    with pytest.raises(ValueError,match='policy'):run(fixture(),policy='future_best')


def test_post_fill_mark_cap_joint_scaling_and_exact_native_matching():
    data=fixture(end='2024-01-02',selected={'2024-01-01':ASSETS[:2]})
    for frame in data[2].values():frame[:]=102.
    _,hours,orders=run(data)
    t=pd.Timestamp('2024-01-01 01:00',tz='UTC')
    assert hours.loc[t,'futures_gross']<=.99*hours.loc[t,'after_orders_futures_equity']+1e-7
    assert hours.loc[t,'gross_notional']<=.99*hours.loc[t,'after_orders_equity']+1e-7
    for s in ASSETS:assert np.all(hours[s+'_spot_quantity'].to_numpy()==-hours[s+'_future_quantity'].to_numpy())
    assert orders.quantity.min()>0
