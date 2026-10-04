"""Source data defects must not turn into apparently complete risk evidence."""
import hashlib
import io
import zipfile
import pandas as pd
import pytest
from r35_native import checked_csv, parse_minutes, targets


def native(rows=3, micro=False):
    ts=int(pd.Timestamp('2018-04-01',tz='UTC').timestamp())*(1000000 if micro else 1000)
    step=60000000 if micro else 60000
    return pd.DataFrame([[ts+i*step,10,11,9,10,2,ts+(i+1)*step-1,20,2,1,10,0]
                         for i in range(rows)])


def parse(x):
    return parse_minutes(x.to_csv(header=False,index=False).encode(),
                         pd.Timestamp('2018-04-01',tz='UTC'),pd.Timestamp('2018-04-01 00:03',tz='UTC'))


def test_fixed_calendar_no_unpublished_month_dependency():
    jobs=targets()
    assert len(jobs)==244
    assert len({u for j in jobs for u in (j['url'],j['checksum_url'])})==488
    assert sum(j['kind']=='daily' for j in jobs)==42
    assert all('2026-09.zip' not in j['url'] for j in jobs)


def test_native_ms_us_are_equal_and_missing_is_retained():
    a,info=parse(native());b,_=parse(native(micro=True))
    pd.testing.assert_frame_equal(a,b)
    assert info['missing_minutes']==0
    sparse,info=parse(native().drop(index=1))
    assert len(sparse)==2 and info['missing_minutes']==1
    assert info['missing_utc']==['2018-04-01 00:01:00+00:00']


def test_native_zero_activity_is_a_stale_price_mark_not_synthesized():
    x=native();x.loc[1,[5,7,8,9,10]]=0
    f,info=parse(x)
    assert info['zero_activity_minutes']==1 and f.loc[f.index[1],'open']==10
    x.loc[1,8]=1
    with pytest.raises(ValueError,match='zero-trade'):parse(x)


@pytest.mark.parametrize('defect',['duplicate','reordered','span','mixed','negative','ohlc','taker'])
def test_native_schema_defects_fail(defect):
    x=native()
    if defect=='duplicate':x.loc[1,0]=x.loc[0,0];x.loc[1,6]=x.loc[0,6]
    if defect=='reordered':x=x.iloc[::-1]
    if defect=='span':x.loc[1,6]+=1
    if defect=='mixed':x.loc[1,0]*=1000;x.loc[1,6]=x.loc[1,0]+59999999
    if defect=='negative':x.loc[1,5]=-1
    if defect=='ohlc':x.loc[1,2]=8
    if defect=='taker':x.loc[1,9]=3
    with pytest.raises(ValueError):parse(x)


def test_checksum_and_member_literal_identity():
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:z.writestr('BTCUSDT-1m-2018-04.csv','native')
    body=b.getvalue();sha=hashlib.sha256(body).hexdigest()
    assert checked_csv(body,(sha+' BTCUSDT-1m-2018-04.zip').encode(),'BTCUSDT-1m-2018-04.zip')==b'native'
    with pytest.raises(ValueError):checked_csv(body,('0'*64+' BTCUSDT-1m-2018-04.zip').encode(),'BTCUSDT-1m-2018-04.zip')
    with pytest.raises(ValueError):checked_csv(body,(sha+' ETHUSDT-1m-2018-04.zip').encode(),'BTCUSDT-1m-2018-04.zip')


def test_observed_diagnostic_keeps_source_failure_and_never_repairs_corrupt_bar():
    from r35_observed import observed_rows
    x=native();x.loc[1,6]=x.loc[1,0]-1239472
    body=x.to_csv(header=False,index=False).encode()
    frame,info=observed_rows(body,pd.Timestamp('2018-04-01',tz='UTC'),pd.Timestamp('2018-04-01 00:03',tz='UTC'))
    assert len(frame)==2 and info['missing_minutes']==1
    assert info['original_strict_contract_failed'] is True
    assert len(info['excluded_nonstandard_span_rows'])==1
    assert info['excluded_nonstandard_span_rows'][0]['original_csv_zero_based_row']==1
    with pytest.raises(ValueError,match='span'):parse(x)
    x.loc[0,2]=8
    with pytest.raises(ValueError,match='OHLC'):observed_rows(x.to_csv(header=False,index=False).encode(),
        pd.Timestamp('2018-04-01',tz='UTC'),pd.Timestamp('2018-04-01 00:03',tz='UTC'))
