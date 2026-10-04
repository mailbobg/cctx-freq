"""Independent risk sampling oracle: daily flat close can hide order-phase loss."""
import numpy as np
import pandas as pd
import pytest

from r17_screen import metrics


def flat_frames():
    daily=pd.DataFrame({'equity':[10000.],'funding':[0.],'cost':[0.],'realized_price_pnl':[0.]},index=pd.date_range('2024-01-01',periods=1,tz='UTC'))
    hours=pd.date_range('2024-01-01',periods=24,freq='h',tz='UTC')
    frame=pd.DataFrame(index=hours)
    for c in ('before_funding_equity','after_funding_equity','after_orders_equity','pre_terminal_equity','equity'):frame[c]=10000.
    for c in ('before_funding_futures_equity','after_funding_futures_equity','after_orders_futures_equity','closing_futures_equity','adverse_futures_equity'):frame[c]=5000.
    frame['gross_notional']=0.;frame['active']=False;frame['marked_margin_concern']=False;frame['envelope_margin_concern']=False
    orders=pd.DataFrame(columns=['date','action','leg'])
    return daily,frame,orders


def test_open_order_phase_loss_not_hidden_by_daily_or_hour_close():
    daily,hour,orders=flat_frames()
    hour.loc[hour.index[5],'after_orders_equity']=9000.
    result=metrics(daily,hour,orders)
    assert result['daily_close_DD_pct']==0
    assert result['sampled_hour_phase_DD_pct']==pytest.approx(10.)
    assert not result['recent_necessary_screen_gate']


def test_initial_capital_in_peak_and_cash_oracle_no_income():
    result=metrics(*flat_frames())
    assert result['net_profit_USDT']==result['CAGR_pct']==result['fees_USDT']==result['funding_USDT']==0
    assert result['daily_close_DD_pct']==result['sampled_hour_phase_DD_pct']==0
    daily,hour,orders=flat_frames()
    for c in ('before_funding_equity','after_funding_equity','after_orders_equity','pre_terminal_equity'):hour[c]=9900.
    assert metrics(daily,hour,orders)['sampled_hour_phase_DD_pct']==pytest.approx(1.)
