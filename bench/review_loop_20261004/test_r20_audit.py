"""Source integrity/semantics, never a synthetic historical profitability test."""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json

import pytest

from r20_audit import FIELDS, api_sample, parse_btc_csv, retained


DAY='2024-01-05'
VALUES=('1.983754393826','19590774.24589445','117.05381845','1704516041','1704516041')


def csv_body(rows=None):
    rows=rows or [[DAY,*VALUES]]
    return (','.join(['time',*FIELDS])+'\n'+'\n'.join(','.join(r) for r in rows)+'\n').encode()


def api_body(**changes):
    row=dict(asset='btc',time=DAY+'T00:00:00.000000000Z',**dict(zip(FIELDS,VALUES)))
    row.update(changes)
    return json.dumps(dict(data=[row])).encode()


def test_exact_native_decimals_and_completion_not_publication():
    sample=api_sample(api_body(),200,DAY)
    assert sample['values']['CapMVRVCur']==Decimal(VALUES[0])
    assert sample['completion_utc']=='2024-01-06T04:40:41+00:00'
    assert sample['first_public_available_utc'] is None
    assert sample['historical_value_version_certified'] is False


@pytest.mark.parametrize('changes',[dict(asset='eth'),dict(time='2024-01-06T00:00:00Z'),dict(time=DAY+'T01:00:00Z')])
def test_foreign_identity_or_observation_interval_rejected(changes):
    with pytest.raises(ValueError):api_sample(api_body(**changes),200,DAY)


def test_null_unsupported_and_known_zero_are_different():
    row=json.loads(api_body())['data'][0]
    row['CapMVRVCur']=None
    del row['SplyCur']
    row['FeeTotNtv']='0'
    sample=api_sample(json.dumps(dict(data=[row])).encode(),200,DAY)
    assert sample['states']['CapMVRVCur']=='no_database_value'
    assert sample['states']['SplyCur']=='unsupported_metric'
    assert sample['states']['FeeTotNtv']=='value'
    assert sample['values']['CapMVRVCur'] is None
    assert sample['values']['SplyCur'] is None
    assert sample['values']['FeeTotNtv']==Decimal(0)


def test_permission_failure_never_becomes_an_empty_or_zero_sample():
    body=b'{"error":{"type":"forbidden","message":"Unavailable with supplied credentials"}}'
    result=api_sample(body,403,DAY)
    assert result['response_state']=='forbidden'
    assert result['values'] is None
    with pytest.raises(ValueError):api_sample(body,200,DAY)


@pytest.mark.parametrize('value',['NaN','Infinity','-1',1.5])
def test_invalid_native_metric_never_admitted(value):
    with pytest.raises(ValueError):api_sample(api_body(CapMVRVCur=value),200,DAY)


def test_csv_native_blanks_and_duplicate_dates():
    row=[DAY,*VALUES];row[1]=''
    sample=parse_btc_csv(csv_body([row]))
    assert sample[DAY]['CapMVRVCur'] is None
    with pytest.raises(ValueError):parse_btc_csv(csv_body([row,row]))
    with pytest.raises(ValueError):parse_btc_csv(csv_body([['2024-01-06',*VALUES],row]))


def test_csv_duplicated_header_and_wrong_column_count_rejected():
    with pytest.raises(ValueError):parse_btc_csv(csv_body().replace(b'time,CapMVRVCur',b'time,time'))
    with pytest.raises(ValueError):parse_btc_csv(csv_body().replace(b'117.05381845,',b''))


def test_duplicate_or_unfinished_api_page_not_a_complete_sample():
    row=json.loads(api_body())['data'][0]
    with pytest.raises(ValueError):api_sample(json.dumps(dict(data=[row,row])).encode(),200,DAY)
    with pytest.raises(ValueError):api_sample(json.dumps(dict(data=[row],next_page_url='https://example.com')).encode(),200,DAY)


def test_retained_bytes_and_capture_clock_checked(tmp_path):
    body=api_body();(tmp_path/'raw').mkdir();(tmp_path/'raw'/'sample').write_bytes(body)
    record=dict(path='raw/sample',bytes=len(body),sha256=hashlib.sha256(body).hexdigest(),status=200,
                requested_utc='2026-10-04T13:00:00Z',retrieved_utc='2026-10-04T13:00:01Z')
    assert retained(tmp_path,record)==body
    with pytest.raises(ValueError):retained(tmp_path,dict(record,retrieved_utc='2026-10-04T12:00:00Z'))
    with pytest.raises(ValueError):retained(tmp_path,dict(record,path='../sample'))
    (tmp_path/'raw'/'sample').write_bytes(body+b' ')
    with pytest.raises(ValueError):retained(tmp_path,record)
