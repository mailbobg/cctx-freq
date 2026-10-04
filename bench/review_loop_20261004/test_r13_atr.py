import numpy as np
import pandas as pd
import pytest
import talib

from r10_gold import simulate_spot
from r13_atr import atr_values, next_exit, simulate


@pytest.mark.parametrize('opened,low,stop,expected', [
    ([100,100,90],[99,93,89],94,2),
    ([100,100,102],[99,93,101],94,2),
    ([100,90,95],[99,89,94],94,1),
    ([90,100,100],[89,99,99],94,0),
    ([100,100,100],[99,99,93],94,3),
    ([100,100,100],[99,99,99],94,None),
])
def test_completed_minute_touch_uses_next_open_not_stop(opened,low,stop,expected):
    assert next_exit(np.array(opened),np.array(low),stop)==expected


def fixture(end='2023-08-21'):
    days=pd.date_range('2023-01-01',end,tz='UTC')
    px=pd.DataFrame(100.,index=days,columns=[a+'_'+c for a in ['BTC','ETH'] for c in ['open','high','low','close']])
    weights=pd.DataFrame(.2,index=days,columns=['BTC','ETH'])
    atr=pd.DataFrame(2.,index=days,columns=['BTC','ETH'])
    idx=pd.date_range(pd.Timestamp('2023-08-07',tz='UTC'),pd.Timestamp(end,tz='UTC')+pd.Timedelta(days=1),freq='min',inclusive='left')
    minutes={a:pd.DataFrame(100.,index=idx,columns=['open','high','low','close']) for a in ['BTC','ETH']}
    return px,weights,atr,minutes


def run(px,w,atr,minutes,end='2023-08-11',**kw):
    return simulate(px,w,atr,minutes,'2023-08-07',end,.0012,**kw)


def test_disabled_daily_curve_matches_original_and_cost_identity():
    px,w,atr,minutes=fixture()
    got,events=run(px,w,atr,minutes,active=False)
    ref=simulate_spot(px,w,'2023-08-07','2023-08-11',.0012)
    pd.testing.assert_frame_equal(got[ref.columns],ref)
    assert got.iloc[-1].equity==pytest.approx(1-got.iloc[-1].cumulative_cost)
    assert got.sampled_minute_running_dd_pct.iloc[-1]<0
    assert len(events)==4


def test_low_touch_recovery_fills_following_minute_and_companion_unchanged():
    px,w,atr,minutes=fixture()
    minutes['BTC'].loc['2023-08-08 01:00','low']=93
    minutes['BTC'].loc['2023-08-08 01:01',['open','high','low','close']]=102
    curve,events=run(px,w,atr,minutes,terminal=False)
    stopped=events.loc[events.reason.eq('atr_minute')]
    assert len(stopped)==1
    assert stopped.iloc[0].time==pd.Timestamp('2023-08-08 01:01',tz='UTC')
    assert stopped.iloc[0].price==102
    assert curve.loc['2023-08-08':'2023-08-11','units_BTC'].eq(0).all()
    assert curve.units_ETH.nunique()==1
    assert curve.accounting_gap.abs().max()<1e-12


def test_final_minute_pending_uses_next_day_gap_and_not_old_stop_price():
    px,w,atr,minutes=fixture()
    minutes['BTC'].loc['2023-08-07 23:59','low']=93
    minutes['BTC'].loc['2023-08-08 00:00',['open','high','low','close']]=80
    px.loc['2023-08-08','BTC_open']=80
    curve,events=run(px,w,atr,minutes,terminal=False)
    stopped=events.loc[events.reason.eq('atr_pending')]
    assert len(stopped)==1 and stopped.iloc[0].price==80
    assert curve.loc['2023-08-07','units_BTC']>0 and curve.loc['2023-08-08','units_BTC']==0


def test_current_day_future_high_cannot_ratchet_current_stop():
    px,w,atr,minutes=fixture()
    px.loc['2023-08-08','BTC_high']=140
    minutes['BTC'].loc['2023-08-08 18:00','high']=140
    curve,events=run(px,w,atr,minutes,terminal=False)
    assert curve.loc['2023-08-08','units_BTC']>0
    assert events.loc[events.reason.str.startswith('atr')].iloc[0].time==pd.Timestamp('2023-08-09',tz='UTC')


def test_new_level_is_not_applied_retroactively_to_previous_last_minute():
    px,w,atr,minutes=fixture()
    px.loc['2023-08-07','BTC_high']=110
    minutes['BTC'].loc['2023-08-07 14:00','high']=110
    minutes['BTC'].loc['2023-08-07 23:59',['open','high','low','close']]=105
    minutes['BTC'].loc['2023-08-07 23:59','low']=100
    minutes['BTC'].loc['2023-08-08',:]=105
    px.loc['2023-08-08',['BTC_open','BTC_high','BTC_low','BTC_close']]=105
    curve,events=run(px,w,atr,minutes,end='2023-08-08',terminal=False)
    assert curve.iloc[-1].units_BTC>0
    assert not events.reason.str.startswith('atr').any()


def test_monday_gap_protection_blocks_same_monday_restart():
    px,w,atr,minutes=fixture()
    px.loc['2023-08-13','BTC_high']=110
    minutes['BTC'].loc['2023-08-13 14:00','high']=110
    curve,events=run(px,w,atr,minutes,end='2023-08-21',terminal=False)
    assert curve.loc['2023-08-14':'2023-08-20','units_BTC'].eq(0).all()
    assert curve.loc['2023-08-21','units_BTC']>0
    assert len(events.loc[(events.time==pd.Timestamp('2023-08-14',tz='UTC')) & events.asset.eq('BTC')])==1


def test_partial_trim_add_does_not_reset_or_lower_the_armed_stop():
    px,w,atr,minutes=fixture()
    w.loc['2023-08-13','BTC']=.1
    atr.loc['2023-08-13':,'BTC']=10
    minutes['BTC'].loc['2023-08-15 01:00','low']=93
    curve,events=run(px,w,atr,minutes,end='2023-08-16',terminal=False)
    assert curve.loc['2023-08-14','units_BTC']<curve.loc['2023-08-13','units_BTC']
    assert curve.loc['2023-08-15','units_BTC']==0
    assert events.loc[events.reason.eq('atr_minute')].iloc[0].time==pd.Timestamp('2023-08-15 01:01',tz='UTC')


def test_held_execution_prefix_is_unchanged_by_future_prices():
    px,w,atr,minutes=fixture()
    minutes['BTC'].loc['2023-08-08 01:00','low']=93
    full,events=run(px,w,atr,minutes,end='2023-08-21',terminal=False)
    cutoff='2023-08-11'
    part,part_events=run(px.loc[:cutoff],w.loc[:cutoff],atr.loc[:cutoff],{a:f.loc[:cutoff] for a,f in minutes.items()},terminal=False)
    pd.testing.assert_frame_equal(part,full.loc[part.index])
    pd.testing.assert_frame_equal(part_events,events.loc[events.time<pd.Timestamp('2023-08-12',tz='UTC')].reset_index(drop=True))
    assert part.units_ETH.gt(0).all() and part.units_BTC.gt(0).any()


def test_wilder_atr_full_prefix_and_independent_seed_recursion():
    index=pd.date_range('2023-01-01',periods=70,tz='UTC')
    close=np.linspace(100,150,70)+np.sin(np.arange(70))*3
    high,low=close+2,close-1
    px=pd.DataFrame({a+'_'+k:v for a in ['BTC','ETH'] for k,v in [('high',high),('low',low),('close',close)]},index=index)
    full=atr_values(px)
    pd.testing.assert_frame_equal(atr_values(px.iloc[:50]),full.iloc[:50])
    tr=np.maximum(high[1:]-low[1:],np.maximum(abs(high[1:]-close[:-1]),abs(low[1:]-close[:-1])))
    manual=np.full(70,np.nan);manual[22]=tr[:22].mean()
    for i in range(23,70):manual[i]=(manual[i-1]*21+tr[i-1])/22
    np.testing.assert_allclose(full.BTC,manual,equal_nan=True,rtol=1e-14)
    assert talib.get_unstable_period('ATR')==0


def test_no_invented_atr_or_minute_calendar():
    px,w,atr,minutes=fixture()
    atr.loc['2023-08-06','BTC']=np.nan
    with pytest.raises(ValueError,match='ATR'):run(px,w,atr,minutes)
    px,w,atr,minutes=fixture();minutes['ETH']=minutes['ETH'].drop(index=minutes['ETH'].index[5])
    with pytest.raises(ValueError,match='minute calendar'):run(px,w,atr,minutes)


def test_array_trigger_matches_independent_minute_arrival_oracle():
    rng=np.random.default_rng(130004)
    for n in [1,2,7,1440]:
        for _ in range(40):
            opened=100+rng.normal(0,3,n);low=opened-abs(rng.normal(0,2,n))
            stop=float(rng.uniform(88,100));pending=False;expected=None
            for i in range(n):
                if pending or opened[i]<=stop:
                    expected=i;break
                pending=low[i]<=stop
            else:
                if pending:expected=n
            assert next_exit(opened,low,stop)==expected


def test_two_asset_intraday_cash_units_fills_match_streaming_oracle():
    px,w,atr,minutes=fixture(end='2023-08-08')
    rng=np.random.default_rng(130104)
    prices={}
    for a in ['BTC','ETH']:
        opened=100+rng.normal(0,3,1440);low=opened-abs(rng.normal(0,2,1440))
        opened[0]=100;low[0]=99
        prices[a]=np.column_stack([opened,opened+2,low,opened])
        minutes[a].loc['2023-08-08',:]=prices[a]
    curve,events=run(px,w,atr,minutes,end='2023-08-08',terminal=False)
    units=np.array([.2*.99/100]*2)
    cash=1-(units*100*(1+.0012)).sum();pending=[False,False];expected=[]
    for m in range(1440):
        for k,a in enumerate(['BTC','ETH']):
            if units[k]>0 and (pending[k] or prices[a][m,0]<=94):
                expected.append((pd.Timestamp('2023-08-08',tz='UTC')+pd.Timedelta(minutes=m),a,prices[a][m,0]))
                cash+=units[k]*prices[a][m,0]*(1-.0012);units[k]=0
            pending[k]=bool(units[k]>0 and prices[a][m,2]<=94)
    actual=events.loc[events.reason.eq('atr_minute')]
    assert list(actual[['time','asset','price']].itertuples(index=False,name=None))==expected
    np.testing.assert_allclose(curve.iloc[-1][['units_BTC','units_ETH']].to_numpy(float),units,atol=1e-15)
    assert curve.iloc[-1].cash==pytest.approx(cash,abs=1e-15)
