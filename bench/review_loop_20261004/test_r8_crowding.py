"""The spot veto cannot use the Monday funding event or redistribute exposure."""
import numpy as np
import pandas as pd
import pytest

from r8_crowding import overlay, close_at_final_close, summarize


def events(rate=.0003, delayed=True):
    slots = pd.date_range('2024-01-01','2024-01-08',freq='8h',inclusive='left',tz='UTC')
    times=slots+pd.Timedelta(milliseconds=1) if delayed else slots
    return pd.DataFrame({'slot':slots,'rate':rate},index=times)


def baseline():
    return pd.DataFrame([[.2,.3]],index=pd.DatetimeIndex(['2024-01-07'],tz='UTC'),columns=['BTC','ETH'])


def test_exact_extreme_boundary_is_not_a_veto_and_does_not_rescale_other_coin():
    base=baseline()
    funding={'BTC':events(.0003),'ETH':events(.00031)}
    out,audit=overlay(base,funding)
    assert out.iloc[0].tolist()==pytest.approx([.2,0.])
    assert audit.mean_rate.tolist()==pytest.approx([.0003,.00031])
    assert audit.complete.all()


@pytest.mark.parametrize('delay',[0,1])
def test_current_monday_funding_is_unavailable_and_cannot_change_the_decision(delay):
    base=baseline();f=events(.0001)
    monday=pd.Timestamp('2024-01-08',tz='UTC')
    future=pd.DataFrame({'slot':[monday],'rate':[1.]},index=[monday+pd.Timedelta(milliseconds=delay)])
    with_future=pd.concat([f,future])
    out,audit=overlay(base,{'BTC':with_future,'ETH':f})
    pd.testing.assert_frame_equal(out,base)
    assert audit.event_count.eq(21).all()


@pytest.mark.parametrize('problem',['missing','repeated','nonfinite'])
def test_unreviewed_week_blocks_only_its_asset(problem):
    f=events(.0001)
    if problem=='missing':f=f.iloc[:-1]
    if problem=='repeated':f=pd.concat([f,f.iloc[-1:]])
    if problem=='nonfinite':f.iloc[-1,f.columns.get_loc('rate')]=np.nan
    out,audit=overlay(baseline(),{'BTC':f,'ETH':events(.0001)})
    assert out.iloc[0].tolist()==pytest.approx([0.,.3])
    assert not audit.iloc[0].complete and audit.iloc[1].complete


def test_off_sunday_weights_remain_unchanged_and_future_events_cannot_change_prefix():
    dates=pd.date_range('2024-01-01','2024-01-14',tz='UTC')
    base=pd.DataFrame(np.tile([.2,.3],(len(dates),1)),index=dates,columns=['BTC','ETH'])
    f=pd.concat([events(.0001),events(.00031).set_axis(events(.00031).index+pd.Timedelta(days=7))])
    f.loc[f.index>pd.Timestamp('2024-01-08',tz='UTC'),'slot']=f.loc[f.index>pd.Timestamp('2024-01-08',tz='UTC'),'slot']+pd.Timedelta(days=7)
    full,_=overlay(base,{'BTC':f,'ETH':f})
    part,_=overlay(base.loc[:'2024-01-07'],{'BTC':f.loc[:'2024-01-07'],'ETH':f.loc[:'2024-01-07']})
    pd.testing.assert_frame_equal(part,full.loc[part.index])
    pd.testing.assert_frame_equal(full.loc[dates.weekday!=6],base.loc[dates.weekday!=6])


def flat_inputs():
    dates=pd.date_range('2023-01-01','2024-01-07',tz='UTC')
    px=pd.DataFrame(100.,index=dates,columns=['BTC_open','BTC_close','ETH_open','ETH_close'])
    w=pd.DataFrame(.5,index=dates,columns=['BTC','ETH'])
    return px,w


def test_final_close_charges_every_invested_dollar_and_reconciles_cash():
    px,w=flat_inputs()
    normal=close_at_final_close(px,w,'2024-01-01','2024-01-07',.0012,terminal=False)
    final=close_at_final_close(px,w,'2024-01-01','2024-01-07',.0012)
    invested=normal.equity.iloc[-1]*normal.exposure.iloc[-1]
    assert final.cost.sum()==pytest.approx(normal.cost.sum()+invested*.0012)
    assert final.cash.iloc[-1]==pytest.approx(final.equity.iloc[-1])
    assert final.equity.iloc[-1]==pytest.approx(normal.equity.iloc[-1]-invested*.0012)
    assert final.exposure.iloc[-1]==0
    assert summarize(final)['return_pct']<0
    assert summarize(final)['daily_MTM_DD_pct']==pytest.approx((final.equity.iloc[-1]-1)*100)
