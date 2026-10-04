"""Guard native stock lookup, source completeness and exact causal decisions."""
import csv
import io

import pandas as pd
import pytest

from r23_inventory import FIELDS
from r26_inventory import parse_day, feature, overlay


def original(day, quantity='100.00000000', unordered=False, duplicate=None):
    rows=[[day+f' {i//12:02}:{(i%12)*5:02}:00','BTCUSDT',quantity,'1000000','1','1','1','1'] for i in range(288)]
    if duplicate is not None:
        extra=list(rows[-1]);extra[2]=duplicate;rows.append(extra)
    if unordered: rows=[*rows[1:],rows[0]]
    buf=io.StringIO();w=csv.writer(buf);w.writerow(FIELDS);w.writerows(rows)
    return buf.getvalue().encode()


def native(day, quantity='100.00000000', **kwargs):
    return parse_day(original(day,quantity,**kwargs),'BTCUSDT',day,'a'*64)


def sources(now='100.00000001', old='100.00000000'):
    return {pd.Timestamp(day,tz='UTC'):native(day,q) for day,q in [('2021-01-29',now),('2020-12-30',old)]}


def test_complete_native_label_lookup_does_not_use_unordered_last_row():
    day='2021-01-29';body=original(day,unordered=True)
    data=parse_day(body,'BTCUSDT',day,'a'*64)
    assert data['native_label']==day+' 23:55:00'
    assert data['original_rows']==288 and data['unique_labels']==288
    assert not data['original_source_order_chronological']
    assert data['quantity_decimal']=='100.00000000'
    assert body==original(day,unordered=True)


def test_conflicting_duplicate_and_missing_labels_halt_instead_of_cash():
    with pytest.raises(ValueError):native('2021-01-29',duplicate='101.00000000')
    text=original('2021-01-29').decode().splitlines()
    with pytest.raises(ValueError):parse_day(('\n'.join(text[:-1])+'\n').encode(),'BTCUSDT','2021-01-29','a'*64)
    data=native('2021-01-29',duplicate='100.00000000')
    assert data['original_rows']==289 and data['unique_labels']==288 and data['quantity_decimal']=='100.00000000'
    with pytest.raises(ValueError):feature(pd.Timestamp('2021-02-01',tz='UTC'),{})


def test_decimal_direction_does_not_collapse_to_float_equality():
    f=feature(pd.Timestamp('2021-02-01',tz='UTC'),sources('100.0000000000000001','100.0000000000000000'))
    assert f['allow'] and f['known']
    assert not f['historical_publication_certified']
    assert f['available_at_assumed']==pd.Timestamp('2021-01-31',tz='UTC')
    equal=feature(pd.Timestamp('2021-02-01',tz='UTC'),sources('100.00000000','100.00000000'))
    assert not equal['allow'] and equal['known']


@pytest.mark.parametrize('damage',[
    lambda r:r.update(source_symbol='ETHUSDT'),
    lambda r:r.update(native_label='2021-01-29 23:50:00'),
    lambda r:r.update(source_sha256=''),
    lambda r:r.update(assumed_interval_end=pd.Timestamp('2021-02-02',tz='UTC')),
    lambda r:r.update(quantity_decimal='0'),
])
def test_foreign_future_or_invalid_native_observation_rejected(damage):
    rows=sources();damage(rows[pd.Timestamp('2021-01-29',tz='UTC')])
    with pytest.raises(ValueError):feature(pd.Timestamp('2021-02-01',tz='UTC'),rows)


def test_post_covariance_gate_and_incoherent_or_missing_feature():
    monday=pd.Timestamp('2021-02-01',tz='UTC');sunday=monday-pd.Timedelta(days=1)
    base=pd.DataFrame({'BTC':[.08],'ETH':[.03]},index=pd.DatetimeIndex([sunday]))
    allow=feature(monday,sources());held,_=overlay(base,{monday:allow})
    pd.testing.assert_frame_equal(held,base)
    veto=feature(monday,sources('99.00000000','100.00000000'));flat,_=overlay(base,{monday:veto})
    assert (flat==0).all().all() and base.loc[sunday,'BTC']==.08
    corrupt=dict(veto,allow=True)
    with pytest.raises(ValueError):overlay(base,{monday:corrupt})
    with pytest.raises(ValueError):overlay(base,{})
    with pytest.raises(ValueError):overlay(base,{monday:dict(allow,current_source_sha256='')})
