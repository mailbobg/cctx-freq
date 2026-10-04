"""Natural majority/native completeness/calendar causality, without fitted outcomes."""
import pandas as pd
import pytest

from r22_participation import PANEL, feature, overlay
from r22_sources import full_targets,probe_targets
from r22_run import exact_month
from r10_gold import simulate_spot


DAY=pd.Timestamp('2024-01-08',tz='UTC')


def panel(up=4,equal=False):
    friday=DAY-pd.Timedelta(days=3);past=friday-pd.Timedelta(days=30);out={}
    for i,symbol in enumerate(PANEL):
        now='100.000000000000001' if i<up else '100' if equal else '99'
        def row(day,close):
            return dict(source_symbol=symbol,close=close,volume='1',quote_volume='100',trades=1,
                        interval_end=day+pd.Timedelta(days=1),source_sha256='a'*64)
        out[symbol]={friday:row(friday,now),past:row(past,'100')}
    return out


@pytest.mark.parametrize('up,allow',[(0,False),(3,False),(4,True),(6,True)])
def test_exact_natural_majority(up,allow):
    f=feature(DAY,panel(up))
    assert f['positive_count']==up and f['allow'] is allow
    assert f['known'] is True
    assert f['historical_publication_certified'] is False


def test_equality_and_float_rounding_do_not_create_an_up_vote():
    p=panel(3,equal=True);f=feature(DAY,p)
    assert f['positive_count']==3 and f['allow'] is False
    assert float(next(iter(p.values()))[DAY-pd.Timedelta(days=3)]['close'])==100


@pytest.mark.parametrize('kind',['missing_member','missing_past','foreign_symbol','zero_activity','incomplete_interval'])
def test_missing_invalid_member_is_not_a_negative_or_an_omitted_vote(kind):
    p=panel();symbol=PANEL[0];friday=DAY-pd.Timedelta(days=3)
    if kind=='missing_member':del p[symbol]
    elif kind=='missing_past':del p[symbol][friday-pd.Timedelta(days=30)]
    elif kind=='foreign_symbol':p[symbol][friday]['source_symbol']=PANEL[1]
    elif kind=='zero_activity':p[symbol][friday]['trades']=0
    else:p[symbol][friday]['interval_end']=DAY+pd.Timedelta(seconds=1)
    with pytest.raises(ValueError):feature(DAY,p)


def test_dates_and_source_clock_are_exact():
    f=feature(DAY,panel())
    assert f['observation_day']==pd.Timestamp('2024-01-05',tz='UTC')
    assert f['prior_observation_day']==pd.Timestamp('2023-12-06',tz='UTC')
    assert f['available_at_assumed']==pd.Timestamp('2024-01-07',tz='UTC')
    with pytest.raises(ValueError):feature(DAY+pd.Timedelta(days=1),panel())


def test_after_covariance_shared_veto_retains_exact_weights_without_rescaling():
    base=pd.DataFrame({'BTC':[.1,.15],'ETH':[.07,.08]},index=pd.date_range('2024-01-07',periods=2,tz='UTC'))
    weights,_=overlay(base,{DAY:feature(DAY,panel(4))})
    assert weights.iloc[0].to_list()==[.1,.07]
    weights,_=overlay(base,{DAY:feature(DAY,panel(3))})
    assert weights.to_numpy().sum()==0
    f=feature(DAY,panel());f['available_at_assumed']=DAY+pd.Timedelta(seconds=1)
    with pytest.raises(ValueError):overlay(base,{DAY:f})


def test_declared_full_source_calendar_has_every_pair_and_preserves_probe_identity():
    targets=full_targets();probes=probe_targets()
    assert all(targets[k]==v for k,v in probes.items())
    for symbol in PANEL:
        for month in pd.date_range('2020-12-01','2026-08-01',freq='MS').strftime('%Y-%m'):
            key='spot_'+symbol+'_'+month
            assert targets[key].endswith(f'{symbol}-1d-{month}.zip')
            assert targets[key+'_checksum']==targets[key]+'.CHECKSUM'
    assert len([k for k in targets if k.startswith('spot_')])==828


@pytest.mark.parametrize('kind',['foreign_vote','missing_vote','altered_count','altered_native_price'])
def test_overlay_rejects_native_vote_identity_or_majority_corruption(kind):
    f=feature(DAY,panel(4));base=pd.DataFrame({'BTC':[.1],'ETH':[.07]},index=pd.date_range('2024-01-07',periods=1,tz='UTC'))
    if kind=='foreign_vote':f['native_votes'][0]['symbol']='BTCUSDT'
    elif kind=='missing_vote':f['native_votes'].pop()
    elif kind=='altered_count':f['positive_count']=5
    else:f['native_votes'][0]['friday_close_decimal']='99'
    with pytest.raises(ValueError):overlay(base,{DAY:f})


@pytest.mark.parametrize('month',['2021-02','2024-02','2020-12'])
def test_month_boundary_completeness_including_leap_day(month):
    start=pd.Timestamp(month+'-01',tz='UTC');stop=start+pd.offsets.MonthBegin(1)
    rows={d:{} for d in pd.date_range(start,stop-pd.Timedelta(days=1))}
    exact_month(rows,month)
    for day in (start,stop-pd.Timedelta(days=1)):
        with pytest.raises(ValueError):exact_month({d:r for d,r in rows.items() if d!=day},month)
    with pytest.raises(ValueError):exact_month({**rows,stop:{}},month)


def test_shared_veto_fully_exits_small_holdings_below_active_adjustment_band():
    dates=pd.date_range('2023-06-01','2024-01-15',tz='UTC')
    base=pd.DataFrame({'BTC':.01,'ETH':.01},index=dates)
    f=feature(DAY,panel(4));later=DAY+pd.Timedelta(days=7)
    bad=feature(later,{s:{d+pd.Timedelta(days=7):{**r,'interval_end':r['interval_end']+pd.Timedelta(days=7)} for d,r in rows.items()} for s,rows in panel(3).items()})
    target,_=overlay(base,{DAY:f,later:bad})
    px=pd.DataFrame({a+'_'+field:100. for a in ('BTC','ETH') for field in ('open','close')},index=dates)
    curve=simulate_spot(px,target,'2024-01-08','2024-01-15',.0012,terminal=False)
    assert curve.loc['2024-01-14',['units_BTC','units_ETH']].gt(0).all()
    assert curve.loc['2024-01-15',['units_BTC','units_ETH']].eq(0).all()
    assert curve.loc['2024-01-15','cost']>0
