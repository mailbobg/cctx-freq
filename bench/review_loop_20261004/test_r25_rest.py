"""Validate source identities/unknowns, not any trading rule."""
import json
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import pytest

from r25_sources import DAY, SYMBOLS, bounds, targets
from r25_rest import parse_api, compare_fields


def oi(ts, qty='12.34567890'):
    return dict(symbol='BTCUSDT', sumOpenInterest=qty,
                sumOpenInterestValue='1234.56789000', timestamp=ts)


def parse(rows, kind='oi', symbol='BTCUSDT'):
    start, end = bounds()
    return parse_api(json.dumps(rows).encode(), symbol, kind, start, end,
                     targets()[f'{kind}_{symbol}'])


def metric():
    return dict(create_time=DAY+' 00:00:00', symbol='BTCUSDT',
                sum_open_interest='12.34567890', sum_open_interest_value='1234.56789000',
                sum_taker_long_short_vol_ratio='1.00000000')


def test_frozen_roles_and_request_window():
    start, end = bounds()
    assert DAY == '2026-09-05' and SYMBOLS == ('BTCUSDT','ETHUSDT')
    assert start == int(datetime(2026,9,5,tzinfo=timezone.utc).timestamp()*1000)
    assert end-start == 86400000 and len(targets()) == 9
    for symbol in SYMBOLS:
        for kind, path in [('oi','/futures/data/openInterestHist'),('taker','/futures/data/takerlongshortRatio')]:
            u=urlparse(targets()[f'{kind}_{symbol}'])
            assert u.netloc=='fapi.binance.com' and u.path==path
            assert parse_qs(u.query)==dict(symbol=[symbol], period=['5m'],startTime=[str(start)],endTime=[str(end)],limit=['500'])


def test_exact_decimal_and_both_fixed_offsets_without_source_shift():
    start, _ = bounds(); original=metric()
    rows=parse([oi(start),oi(start+300000,'12.34567891')])
    out=compare_fields(original, rows, 'oi')
    assert original==metric() and out['source_label']==DAY+' 00:00:00'
    assert out['label_time']['fields']['quantity']['exact_decimal_equal'] is True
    q=out['label_plus_5m']['fields']['quantity']
    assert q['absolute_error_decimal']=='0.00000001' and not q['exact_decimal_equal']
    assert q['archive_native']=='12.34567890' and q['api_native']=='12.34567891'
    assert not out['measurement_unit_or_publication_certified']


@pytest.mark.parametrize('change',[
    lambda r:r.update(symbol='ETHUSDT'),
    lambda r:r.update(timestamp=r['timestamp']+1),
    lambda r:r.update(timestamp=str(r['timestamp'])),
    lambda r:r.update(timestamp=True),
    lambda r:r.update(sumOpenInterest='-1'),
    lambda r:r.update(sumOpenInterest='NaN'),
    lambda r:r.update(sumOpenInterest=12.3),
])
def test_identity_grid_and_native_numeric_errors(change):
    start,_=bounds();r=oi(start);change(r)
    with pytest.raises(ValueError):parse([r])


def test_duplicate_unordered_outside_and_foreign_request_rejected():
    start,end=bounds()
    for rows in [[oi(start),oi(start)], [oi(start+300000),oi(start)], [oi(end+300000)]]:
        with pytest.raises(ValueError):parse(rows)
    with pytest.raises(ValueError):
        parse_api(json.dumps([oi(start)]).encode(),'BTCUSDT','oi',start,end,targets()['oi_ETHUSDT'])
    with pytest.raises(ValueError):
        parse_api(b'[{"timestamp":1,"timestamp":2}]','BTCUSDT','oi',start,end,targets()['oi_BTCUSDT'])


def test_empty_missing_null_and_true_zero_remain_distinct():
    start,_=bounds();empty=parse([])
    assert empty=={}
    missing=compare_fields(metric(),empty,'oi')
    assert missing['label_time']['state']=='missing_counterpart'
    rows=parse([dict(buySellRatio=None,buyVol='0.0000',sellVol='0.0000',timestamp=start)],'taker')
    out=compare_fields(metric(),rows,'taker')['label_time']
    assert out['fields']['ratio']['state']=='api_null_unknown' and out['native_buy_sell']['state']=='zero_sell_undefined_ratio'
    assert Decimal(out['native_buy_sell']['buy_native'])==0
    assert 'symbol' not in rows[start]
    with pytest.raises(ValueError):parse([dict(timestamp=start,buyVol='1',sellVol='1')],'taker')


def test_api_reported_ratio_and_derived_buy_sell_are_not_overwritten():
    start,_=bounds()
    rows=parse([dict(buySellRatio='1.0000',buyVol='3.0000',sellVol='2.0000',timestamp=start)],'taker')
    out=compare_fields(metric(),rows,'taker')['label_time']
    assert out['fields']['ratio']['exact_decimal_equal']
    assert out['native_buy_sell']['derived_ratio_decimal']=='1.5'
    assert rows[start]['buySellRatio']=='1.0000'


def test_forensic_archive_comparison_preserves_unordered_source_rows():
    from r23_inventory import FIELDS, parse_metrics
    from r25_rest import parse_archive_observations
    import csv,io
    buffer=io.StringIO();writer=csv.writer(buffer);writer.writerow(FIELDS)
    for minute in (5,0):
        writer.writerow([DAY+f' 00:{minute:02}:00','BTCUSDT','12.34567890','1234.56789000','1','1','1','1.00000000'])
    body=buffer.getvalue().encode()
    with pytest.raises(ValueError,match='unordered'):parse_metrics(body,'BTCUSDT',DAY)
    rows,diagnostic=parse_archive_observations(body,'BTCUSDT',DAY)
    assert [row['create_time'] for row in rows]==[DAY+' 00:05:00',DAY+' 00:00:00']
    assert diagnostic['strict_parser_failure']=='Native labels unordered'
    assert diagnostic['ordering_decreases']==[dict(index=1,prior=DAY+' 00:05:00',following=DAY+' 00:00:00')]
    assert not diagnostic['chronological_source_order_valid']
    assert diagnostic['future_feature_cohort_eligibility_not_granted']


def test_forensic_parser_keeps_foreign_or_invalid_rows_rejected():
    from r23_inventory import FIELDS
    from r25_rest import parse_archive_observations
    body=(','.join(FIELDS)+'\n'+DAY+' 00:00:00,ETHUSDT,12,100,1,1,1,1\n').encode()
    with pytest.raises(ValueError):parse_archive_observations(body,'BTCUSDT',DAY)


def test_forensic_api_retains_outside_boundary_without_relabeling():
    from r25_rest import parse_api_observations
    start,end=bounds();row=dict(buySellRatio='1.0000',buyVol='1.0000',sellVol='1.0000',timestamp=start-300000)
    with pytest.raises(ValueError):parse([row],'taker')
    body=json.dumps([row]).encode()
    rows,diagnostic=parse_api_observations(body,'BTCUSDT','taker',start,end,targets()['taker_BTCUSDT'])
    assert list(rows)==[start-300000] and rows[start-300000]['timestamp']==start-300000
    assert diagnostic['outside_requested_epochs']==[start-300000]
    assert diagnostic['strict_requested_window_failure']=='Original API epoch/grid/window differs'
    assert diagnostic['future_feature_cohort_eligibility_not_granted']
    row['timestamp']=start-1
    with pytest.raises(ValueError):parse_api_observations(json.dumps([row]).encode(),'BTCUSDT','taker',start,end,targets()['taker_BTCUSDT'])
