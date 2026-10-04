"""Views retain backing row identities, native unknowns and immutable full scope."""
from copy import deepcopy

import pytest
from r29_view import parse_native, interval_view, reconcile_views, reconcile_native_availability


def body(rows, series=('SOFR','EFFR')):
    return ('observation_date,'+','.join(s+'_20210130' for s in series)+'\n'+
            ''.join(','.join(row)+'\n' for row in rows)).encode()


def test_explicit_view_preserves_original_body_and_original_row_references():
    raw=body([('2000-07-03','','7.03'),('2021-01-28','0.05','0.08'),('2021-01-29','','.')])
    parsed=parse_native(raw,'2021-01-30');before=deepcopy(parsed)
    view=interval_view(parsed)
    assert parsed==before and len(parsed['rows'])==3
    assert view['outside_original_rows']==1 and not view['http_requested_range_honored']
    assert [r['original_row_index'] for r in view['rows']]==[1,2]
    assert view['rows'][0] is parsed['rows'][1]
    assert view['rows'][1]['native_values']=={'SOFR':'','EFFR':'.'}


def test_known_zero_and_exact_native_counterpart_agreement():
    rows=[('2021-01-28','0.00','0.00')]
    dual=interval_view(parse_native(body(rows),'2021-01-30'))
    singles={s:interval_view(parse_native(body([('2021-01-28','0.00')],(s,)), '2021-01-30',(s,))) for s in ('SOFR','EFFR')}
    out=reconcile_views(dual,singles)
    assert out['native_cells_checked']==2 and out['exact_same_date_text_and_unknown_states']


def test_same_float_value_with_different_native_text_not_silently_rounded():
    dual=interval_view(parse_native(body([('2021-01-28','0.00','0.08')]),'2021-01-30'))
    singles={s:interval_view(parse_native(body([('2021-01-28','0.0' if s=='SOFR' else '0.08')],(s,)),'2021-01-30',(s,))) for s in ('SOFR','EFFR')}
    with pytest.raises(ValueError):reconcile_views(dual,singles)


def test_empty_row_omission_retains_original_failure_and_both_native_states():
    dual=interval_view(parse_native(body([('2021-01-18','',''),('2021-01-28','0.05','0.08')]),'2021-01-30'))
    singles={s:interval_view(parse_native(body([('2021-01-28','0.05' if s=='SOFR' else '0.08')],(s,)),'2021-01-30',(s,))) for s in ('SOFR','EFFR')}
    with pytest.raises(ValueError):reconcile_views(dual,singles)
    result=reconcile_native_availability(dual,singles)
    assert result['native_cells_checked']==4 and result['known_exact_text_cells']==2
    assert result['both_unavailable_cells']==2 and result['native_presence_difference_cells']==2
    for row in result['unavailable_native_states']:
        assert row['dual_row_present'] and not row['single_row_present']
        assert row['dual_native_text']=='' and row['single_native_text'] is None


def test_known_native_rate_missing_in_counterpart_fails():
    dual=interval_view(parse_native(body([('2021-01-28','0.05','0.08')]),'2021-01-30'))
    singles={s:interval_view(parse_native(body([('2021-01-28','' if s=='SOFR' else '0.08')],(s,)),'2021-01-30',(s,))) for s in ('SOFR','EFFR')}
    with pytest.raises(ValueError):reconcile_native_availability(dual,singles)


@pytest.mark.parametrize('raw',[
    b'observation_date,SOFR_20210206,EFFR_20210130\n2021-01-28,0.05,0.08\n',
    body([('2021-02-01','0.05','0.08')]),
    body([('2021-01-28','nan','0.08')]),
    body([('2021-01-28','0.05','0.08'),('2021-01-28','0.05','0.08')]),
    body([('2021-01-28','0.05','0.08'),('2021-01-27','0.05','0.08')]),
])
def test_foreign_vintage_future_unknown_text_or_order_rejected(raw):
    with pytest.raises(ValueError):parse_native(raw,'2021-01-30')
