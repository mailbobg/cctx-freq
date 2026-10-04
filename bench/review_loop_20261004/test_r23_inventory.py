"""Native labels are retained observations, not invented execution clocks."""
from datetime import datetime

import pytest

from r23_inventory import FIELDS,parse_metrics,label_groups


def body(lines):
    return (','.join(FIELDS)+'\n'+'\n'.join(','.join(r) for r in lines)+'\n').encode()


def row(label='2026-06-25 00:00:00',symbol='BTCUSDT',oi='100.000000000000001'):
    return [label,symbol,oi,'1000000.00','1.1','1.2','1.3','1.4']


def test_native_units_precision_and_naive_label_do_not_invent_utc_receipt():
    rows=parse_metrics(body([row()]),'BTCUSDT','2026-06-25')
    assert rows[0]['sum_open_interest']=='100.000000000000001'
    assert rows[0]['label']==datetime(2026,6,25)
    assert rows[0]['label'].tzinfo is None


def test_identical_duplicates_are_reported_without_deleting_original_rows():
    rows=parse_metrics(body([row(),row()]),'BTCUSDT','2026-06-25')
    g=label_groups(rows)
    assert len(rows)==2 and len(g)==1
    assert g[0]['original_rows']==2 and g[0]['identical_native_fields'] is True


def test_conflicting_same_labels_remain_distinct_unresolved_values():
    rows=parse_metrics(body([row(),row(oi='101')]),'BTCUSDT','2026-06-25')
    g=label_groups(rows)
    assert len(rows)==2 and g[0]['original_rows']==2 and g[0]['identical_native_fields'] is False
    assert g[0]['native_values'][0]['sum_open_interest']!=g[0]['native_values'][1]['sum_open_interest']


@pytest.mark.parametrize('kind',['symbol','grid','negative','outside_file','schema','unordered'])
def test_source_identity_or_physical_label_errors_halt(kind):
    lines=[row()]
    if kind=='symbol':lines[0][1]='ETHUSDT'
    elif kind=='grid':lines[0][0]='2026-06-25 00:01:00'
    elif kind=='negative':lines[0][2]='-1'
    elif kind=='outside_file':lines[0][0]='2026-06-26 00:05:00'
    elif kind=='schema':lines[0].pop()
    else:lines=[row('2026-06-25 00:05:00'),row()]
    with pytest.raises(ValueError):parse_metrics(body(lines),'BTCUSDT','2026-06-25')
