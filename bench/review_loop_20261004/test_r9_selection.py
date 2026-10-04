"""Selection is causal and is applied before the unchanged risk budget."""
import numpy as np
import pandas as pd
import pytest

from r9_selection import select_raw, selected_weights, single_asset_envelopes, envelope_metrics
from r1_r2b_risk import signals, RiskHoldR2b


def prices(periods=400, tied=False):
    dates=pd.date_range('2020-01-01',periods=periods,tz='UTC')
    t=np.arange(periods)
    btc=100*np.exp(.001*t+.006*np.sin(t/5))
    eth=btc.copy() if tied else 100*np.exp(.002*t+.008*np.sin(t/7))
    return pd.DataFrame({'BTC_close':btc,'ETH_close':eth,'BTC_open':btc,'ETH_open':eth},index=dates)


def test_exact_positive_momentum_ties_select_btc_consistently():
    px=prices(tied=True);raw=signals(px,RiskHoldR2b)
    chosen,audit=select_raw(px,raw)
    active=raw.BTC.gt(0)
    pd.testing.assert_series_equal(chosen.loc[active,'BTC'],raw.loc[active,'BTC'])
    assert chosen.loc[active,'ETH'].eq(0).all()
    assert audit.loc[active,'winner'].eq('BTC').all()


def test_stronger_asset_keeps_its_original_raw_signal_and_loser_is_zero():
    px=prices();raw=signals(px,RiskHoldR2b)
    chosen,audit=select_raw(px,raw)
    valid=audit.winner.eq('ETH') & raw.ETH.gt(0)
    assert valid.any()
    pd.testing.assert_series_equal(chosen.loc[valid,'ETH'],raw.loc[valid,'ETH'])
    assert chosen.loc[valid,'BTC'].eq(0).all()


def test_all_negative_momentum_and_unknown_comparison_stay_cash():
    px=prices()
    px.loc[:,:]=100*np.exp(-.002*np.arange(len(px)))[:,None]
    chosen,_=select_raw(px,signals(px,RiskHoldR2b))
    assert chosen.eq(0).all().all()
    px=prices();gap=px.index[-20]
    px.loc[gap,'ETH_close']=np.nan
    w=selected_weights(px)[0]
    assert w.loc[gap:].eq(0).all().all()


def test_full_future_history_cannot_change_a_selected_weight_prefix():
    px=prices(500)
    full,_=selected_weights(px)
    for n in (240,350):
        shorter,_=selected_weights(px.iloc[:n])
        pd.testing.assert_frame_equal(shorter,full.iloc[:n])
    assert full.gt(0).any().any()
    assert (full.gt(0).sum(axis=1)<=1).all()
    assert full.sum(axis=1).le(.5+1e-12).all()


def test_actual_held_asset_lows_detect_loss_hidden_by_daily_closes():
    dates=pd.date_range('2024-01-01','2024-01-02',tz='UTC')
    curve=pd.DataFrame({'equity':1.,'pre_close_equity':1.,'pre_close_cash':.8,'pre_close_exposure':.2},index=dates)
    target=pd.DataFrame({'BTC':[.2],'ETH':[0.]},index=pd.DatetimeIndex(['2023-12-31'],tz='UTC'))
    spot={'BTC':pd.DataFrame({'low':[100.,90.],'high':[120.,110.],'close':[100.,100.]},index=dates),
          'ETH':pd.DataFrame({'low':[1.,1.],'high':[200.,200.],'close':[100.,100.]},index=dates)}
    out=single_asset_envelopes(curve,target,spot)
    assert out.held_low_equity.tolist()==pytest.approx([1.,.98])
    # Monday's observed held high precedes Tuesday's observed held low.
    assert envelope_metrics(out)['observed_held_low_vs_prior_high_peak_DD_pct']==pytest.approx((.98/1.04-1)*100)


def test_single_asset_risk_audit_rejects_a_two_asset_target():
    day=pd.Timestamp('2024-01-01',tz='UTC')
    curve=pd.DataFrame({'equity':[1.],'pre_close_equity':[1.],'pre_close_cash':[.8],'pre_close_exposure':[.2]},index=[day])
    target=pd.DataFrame({'BTC':[.1],'ETH':[.1]},index=[day-pd.Timedelta(days=1)])
    with pytest.raises(ValueError,match='two assets'):
        single_asset_envelopes(curve,target,{})
