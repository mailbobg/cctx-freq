import hashlib
import io
import zipfile

import pandas as pd
import pytest

from r17_prices import parse_hourly, verified_zip


def csv_body(micro=False, activity=True):
    rows=[]
    unit=1000 if micro else 1
    for t in pd.date_range('2025-01-01',periods=2,freq='h',tz='UTC'):
        opened=int(t.timestamp()*1000)*unit
        closed=opened+3600000*unit-1
        rows.append([opened,'100','110','90','105','10' if activity else '0',closed,
                     '1000' if activity else '0','2' if activity else '0','5' if activity else '0','500' if activity else '0','0'])
    return ('\n'.join(','.join(map(str,r)) for r in rows)+'\n').encode()


def test_physical_hourly_epoch_switch_and_exact_calendar():
    a=parse_hourly(csv_body(),'2025-01-01T00:00:00Z','2025-01-01T02:00:00Z',trade=True)
    b=parse_hourly(csv_body(True),'2025-01-01T00:00:00Z','2025-01-01T02:00:00Z',trade=True)
    pd.testing.assert_frame_equal(a,b)
    assert a.index.equals(pd.date_range('2025-01-01',periods=2,freq='h',tz='UTC'))
    assert a.quote_volume.iloc[0]==1000 and a.trades.iloc[0]==2


def test_trade_activity_and_native_quote_bound_not_needed_for_nontraded_marks():
    with pytest.raises(ValueError,match='activity'):
        parse_hourly(csv_body(activity=False),'2025-01-01T00:00:00Z','2025-01-01T02:00:00Z',trade=True)
    assert len(parse_hourly(csv_body(activity=False),'2025-01-01T00:00:00Z','2025-01-01T02:00:00Z',trade=False))==2
    with pytest.raises(ValueError,match='quote'):
        parse_hourly(csv_body().replace(b',1000,',b',1000000,'),'2025-01-01T00:00:00Z','2025-01-01T02:00:00Z',trade=True)


def test_missing_repeated_calendar_and_invalid_hour_span_fail():
    raw=csv_body()
    with pytest.raises(ValueError,match='calendar'):
        parse_hourly(raw.splitlines()[0]+b'\n','2025-01-01T00:00:00Z','2025-01-01T02:00:00Z',trade=True)
    with pytest.raises(ValueError,match='calendar'):
        parse_hourly(raw+raw,'2025-01-01T00:00:00Z','2025-01-01T02:00:00Z',trade=True)
    with pytest.raises(ValueError,match='span'):
        parse_hourly(raw.replace(b'1735693199999',b'1735693200000'),'2025-01-01T00:00:00Z','2025-01-01T02:00:00Z',trade=True)


def test_checksum_and_exact_zip_member_identity_required():
    name='BTCUSDT-1h-2025-01.zip';stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:archive.writestr(name.replace('.zip','.csv'),csv_body())
    data=stream.getvalue();checksum=f'{hashlib.sha256(data).hexdigest()}  {name}\n'.encode()
    assert verified_zip(data,checksum,name)==csv_body()
    with pytest.raises(ValueError,match='checksum'):verified_zip(data,checksum.replace(b'BTC',b'ETH'),name)
    with pytest.raises(ValueError,match='checksum'):verified_zip(data+b'corrupt',checksum,name)
