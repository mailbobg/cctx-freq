"""Quote feasibility is causal and never selects a premium or invented fraction."""
import numpy as np
import pandas as pd
import pytest

from r11_quotes import select_identity, first_quote, coverage_amount


DAY = pd.Timestamp('2024-01-01',tz='UTC')


def catalog():
    def row(name,strike,days,created_days=-10):
        return dict(instrument_name=name,option_type='put',strike=strike,
                    creation_timestamp=int((DAY+pd.Timedelta(days=created_days)).timestamp()*1000),
                    expiration_timestamp=int((DAY+pd.Timedelta(days=days)).timestamp()*1000))
    return pd.DataFrame([row('BTC-first-90-P',90,28),row('BTC-first-85-P',85,28),
                         row('BTC-first-95-P',95,28),row('BTC-later-90-P',90,60),
                         row('BTC-too-early-90-P',90,27),row('BTC-created-today-89-P',89,28,0)])


def quote_frame():
    start = int(DAY.timestamp()*1000000)
    return pd.DataFrame(dict(timestamp=[start+1000000,start+500000,start+61000000],
                             local_timestamp=[start+1000001,start+1000002,start+61000001],
                             ask_price=[.02,.01,.005],bid_price=[.019,.009,.004],
                             ask_amount=[.1,.1,.1],bid_amount=[.1,.1,.1]))


def test_identity_uses_earliest_eligible_expiry_and_exact_lower_strike():
    assert select_identity(catalog(),'BTC',DAY,100)['instrument_name']=='BTC-first-90-P'


def test_unknown_prior_price_or_absent_identity_is_unavailable():
    with pytest.raises(ValueError,match='prior'):
        select_identity(catalog(),'BTC',DAY,np.nan)
    assert select_identity(catalog(),'ETH',DAY,100) is None


def test_first_capture_order_is_preserved_instead_of_cheapest_or_exchange_sorted():
    row,stats = first_quote(quote_frame(),DAY,.1)
    assert row['ask_price']==.02
    assert stats['first_minute_rows']==2


def test_future_arrival_or_future_exchange_time_cannot_be_moved_into_minute():
    frame = quote_frame().iloc[:2].copy()
    start = int(DAY.timestamp()*1000000)
    frame.loc[0,'local_timestamp']=start+60000000
    frame.loc[1,'timestamp']=start+60000000
    row,_=first_quote(frame,DAY,.1)
    assert row is None


def test_missing_side_crossed_quotes_and_insufficient_lot_size_are_not_fills():
    frame=quote_frame()
    frame.loc[0,'bid_price']=np.nan
    frame.loc[1,'bid_price']=.02
    row,_=first_quote(frame,DAY,.1)
    assert row is None
    frame=quote_frame();frame.loc[:,'ask_amount']=.099
    row,stats=first_quote(frame,DAY,.1)
    assert row is None and stats['insufficient_ask_lot_rows']==2


def test_native_option_minimum_is_rounded_up_without_extra_lot_at_equality():
    assert coverage_amount(.024,.1)==.1
    assert coverage_amount(.1,.1)==.1
    assert coverage_amount(.3,.1)==.3
    assert coverage_amount(0.,.1)==0.
    with pytest.raises(ValueError):
        coverage_amount(-1.,.1)
