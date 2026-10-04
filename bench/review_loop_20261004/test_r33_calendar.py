import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from r33_calendar import cohort,targets,eligibility,exact_prior,latest


def test_delayed_report_never_used_before_source_arrival():
    row=next(x for x in cohort() if x['monday']=='2025-12-29')
    assert row['current']['release_date']=='2025-12-18'
    assert row['current']['expected_observation_date']=='2025-12-17'
    assert row['prior']['expected_observation_date']=='2025-11-19'


def test_later_decision_can_use_exact_published_holiday_comparator():
    row=next(x for x in cohort() if x['monday']=='2026-01-26')
    assert row['current']['expected_observation_date']=='2026-01-21'
    assert row['prior']['expected_observation_date']=='2025-12-24'
    assert row['prior']['release_date']=='2025-12-29'


def test_full_native_calendar_targets_and_uniform_dst_buffer():
    assert len(cohort())==292 and len(targets())==296
    assert eligibility('2025-12-29').isoformat()=='2025-12-30T06:00:00+00:00'
    assert eligibility('2025-04-03').isoformat()=='2025-04-04T05:00:00+00:00'


def test_missing_actual_report_cannot_fall_back_to_nearest_comparator():
    with pytest.raises(ValueError,match='no actual'):
        exact_prior({'releases':{}},'2025-12-24','2026-01-26')


def test_fake_future_report_is_excluded():
    c={'releases':{'2025-12-18':{'release_date':'2025-12-18'},'2025-12-29':{'release_date':'2025-12-29'}}}
    assert latest(c,'2025-12-29')['release_date']=='2025-12-18'


def test_unreviewed_report_weekday_halts_before_quantity():
    with pytest.raises(ValueError,match='weekday'):
        latest({'releases':{'2025-12-23':{'release_date':'2025-12-23'}}},'2025-12-29')
