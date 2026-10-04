import pandas as pd
import pytest

from r13_data import parse_minutes, checked_zip


def source(unit=1000):
    begin = int(pd.Timestamp('2024-02-01', tz='UTC').timestamp())*unit
    rows = [[begin+i*60*unit,100,101,99,100.5,3,begin+(i+1)*60*unit-1,300,9,1,100,0] for i in range(3)]
    return pd.DataFrame(rows)


def parse(frame):
    return parse_minutes(frame.to_csv(index=False, header=False).encode(), pd.Timestamp('2024-02-01', tz='UTC'), pd.Timestamp('2024-02-01 00:03', tz='UTC'))


def test_both_timestamp_units_normalize_exactly():
    pd.testing.assert_frame_equal(parse(source(1000)), parse(source(1000000)))


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'reorder', 'span', 'mixed'])
def test_no_missing_duplicated_reordered_wrong_span_or_mixed_units(change):
    f = source()
    if change == 'missing': f = f.drop(index=1)
    if change == 'duplicate': f = pd.concat([f,f.iloc[:1]])
    if change == 'reorder': f = f.iloc[::-1]
    if change == 'span': f.loc[1,6] += 1
    if change == 'mixed': f.loc[1,[0,6]] *= 1000
    with pytest.raises(ValueError): parse(f)


@pytest.mark.parametrize('column,value', [(1,0),(2,98),(3,102),(4,float('nan')),(5,0),(7,-1),(8,0)])
def test_unknown_ohlc_and_nonpositive_actual_activity_are_rejected(column,value):
    f=source();f.loc[1,column]=value
    with pytest.raises(ValueError): parse(f)


def test_checksum_and_archive_identity_cannot_be_skipped():
    import hashlib,io,zipfile
    body=source().to_csv(index=False,header=False).encode()
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as z: z.writestr('BTCUSDT-1m-2024-02.csv',body)
    raw=stream.getvalue();sha=hashlib.sha256(raw).hexdigest()
    assert checked_zip(raw,f'{sha} BTCUSDT-1m-2024-02.zip'.encode(),'BTCUSDT-1m-2024-02.zip')==body
    with pytest.raises(ValueError,match='checksum'): checked_zip(raw,b'bad BTCUSDT-1m-2024-02.zip','BTCUSDT-1m-2024-02.zip')
    with pytest.raises(ValueError): checked_zip(raw,f'{sha} ETHUSDT-1m-2024-02.zip'.encode(),'BTCUSDT-1m-2024-02.zip')
