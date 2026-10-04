from decimal import Decimal

import pandas as pd
import pytest

from r17_data import parse_funding_csv, compare_api
from r17_carry import FundingSeries


def body(unit=1):
    times=pd.date_range('2024-01-01',periods=3,freq='8h',tz='UTC')
    lines=['calc_time,funding_interval_hours,last_funding_rate']
    lines += [f'{int(t.timestamp()*1000)*unit},8,0.00010000' for t in times]
    return ('\n'.join(lines)+'\n').encode()


def api_rows():
    times=pd.date_range('2024-01-01',periods=3,freq='8h',tz='UTC')
    return [dict(symbol='BTCUSDT',fundingTime=int(t.timestamp()*1000)+1,
                 fundingRate='0.00010000',markPrice='42000.12340000') for t in times]


def test_funding_source_millisecond_and_microsecond_units_preserve_literal_decimal():
    ms=parse_funding_csv(body(),'2024-01-01T00:00:00Z','2024-01-02T00:00:00Z')
    us=parse_funding_csv(body(1000),'2024-01-01T00:00:00Z','2024-01-02T00:00:00Z')
    pd.testing.assert_frame_equal(ms,us)
    assert ms.rate_decimal.eq('0.00010000').all()
    series=FundingSeries(ms,ms.slot.iloc[0],'2024-01-02T00:00:00Z')
    captured=compare_api(api_rows(),'BTCUSDT',series)
    assert captured.index[0].microsecond==1000
    assert captured.rate_decimal.iloc[0]=='0.00010000'
    assert captured.mark_decimal.iloc[0]=='42000.12340000'


def test_independent_counterpart_requires_exact_native_rate_slot_and_identity():
    f=parse_funding_csv(body(),'2024-01-01T00:00:00Z','2024-01-02T00:00:00Z')
    series=FundingSeries(f,f.slot.iloc[0],'2024-01-02T00:00:00Z')
    for change in ['symbol','rate','missing']:
        rows=api_rows()
        if change=='symbol':rows[0]['symbol']='ETHUSDT'
        if change=='rate':rows[0]['fundingRate']='0.000100000000000001'
        if change=='missing':rows=rows[1:]
        with pytest.raises(ValueError):compare_api(rows,'BTCUSDT',series)


def test_missing_associated_mark_stays_unknown_and_nonpositive_mark_rejected():
    f=parse_funding_csv(body(),'2024-01-01T00:00:00Z','2024-01-02T00:00:00Z')
    series=FundingSeries(f,f.slot.iloc[0],'2024-01-02T00:00:00Z')
    rows=api_rows();rows[0]['markPrice']=''
    captured=compare_api(rows,'BTCUSDT',series)
    assert pd.isna(captured.mark_decimal.iloc[0]) and pd.isna(captured.mark.iloc[0])
    rows[0]['markPrice']='-1'
    with pytest.raises(ValueError,match='mark'):compare_api(rows,'BTCUSDT',series)


def test_wrong_schema_month_bounds_and_epoch_units_fail():
    with pytest.raises(ValueError,match='schema'):
        parse_funding_csv(body().replace(b'last_funding_rate',b'predicted_rate'),'2024-01-01T00:00:00Z','2024-01-02T00:00:00Z')
    with pytest.raises(ValueError,match='range'):
        parse_funding_csv(body(),'2024-01-02T00:00:00Z','2024-01-03T00:00:00Z')
    with pytest.raises(ValueError,match='epoch'):
        parse_funding_csv(body().replace(b'1704067200000',b'1704067200'),'2024-01-01T00:00:00Z','2024-01-02T00:00:00Z')
