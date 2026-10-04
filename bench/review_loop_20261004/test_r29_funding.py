"""Known shared dates, native rate signs and public lag before target decisions."""
from copy import deepcopy

import pandas as pd
import pytest

from r29_view import parse_native
from r29_funding import feature, overlay


def parsed(rows):
    raw=('observation_date,SOFR_20210130,EFFR_20210130\n'+
         ''.join(','.join(r)+'\n' for r in rows)).encode()
    out=parse_native(raw,'2021-01-30')
    out.update(source_sha256='a'*64,source_record='archive_2021-01-30',current_capture_utc='2026-10-05T00:00:00+00:00')
    return out


def f(sofr,effr):
    return feature(pd.Timestamp('2021-02-01',tz='UTC'),parsed([('2021-01-28',sofr,effr)]))


def base():
    return pd.DataFrame({'BTC':[.2],'ETH':[.1]},index=pd.DatetimeIndex(['2021-01-31'],tz='UTC'))


@pytest.mark.parametrize('sofr,effr,allow',[
    ('0.05','0.08',True),('0.08','0.05',False),('0.00','-0.00',True),
    ('0.0500000000000001','0.0500000000000000',False),
])
def test_exact_native_rate_comparison_and_original_joint_targets(sofr,effr,allow):
    value=f(sofr,effr);assert value['allow']==allow
    out,audit=overlay(base(),{value['decision']:value})
    pd.testing.assert_frame_equal(out,base() if allow else base()*0.)
    assert len(audit)==1


def test_known_friday_cannot_override_fixed_following_business_day_cutoff():
    item=parsed([('2021-01-28','0.05','0.08'),('2021-01-29','0.10','0.08')])
    value=feature(pd.Timestamp('2021-02-01',tz='UTC'),item)
    assert value['observation_day']==pd.Timestamp('2021-01-28',tz='UTC') and value['allow']


def test_distinct_calendar_unknowns_require_most_recent_same_known_date():
    item=parsed([('2021-01-25','0.05','0.08'),('2021-01-27','0.05','0.08'),('2021-01-28','','0.08')])
    value=feature(pd.Timestamp('2021-02-01',tz='UTC'),item)
    assert value['observation_day']==pd.Timestamp('2021-01-27',tz='UTC')
    assert value['native_eligible_observations'][-1]['native_values']['SOFR']==''


@pytest.mark.parametrize('rows',[
    [('2021-01-25','','0.08'),('2021-01-28','0.05','.')],
    [('2021-01-15','0.05','0.08')],
    [('2021-01-29','0.05','0.08')],
])
def test_no_common_known_previous_mon_thu_halts_not_cash(rows):
    with pytest.raises(ValueError):feature(pd.Timestamp('2021-02-01',tz='UTC'),parsed(rows))


def test_absent_required_decision_halts_not_zero_target():
    with pytest.raises(ValueError):overlay(base(),{})


@pytest.mark.parametrize('key,value',[
    ('allow',True),('source_sha256',''),('sofr_native','0.00'),
    ('observation_day',pd.Timestamp('2021-01-29',tz='UTC')),
    ('vintage','2021-02-06'),('available_at_assumed',pd.Timestamp('2021-02-02',tz='UTC')),
])
def test_altered_native_direction_date_version_hash_or_clock_rejected(key,value):
    item=deepcopy(f('0.08','0.05'));item[key]=value
    with pytest.raises(ValueError):overlay(base(),{item['decision']:item})
