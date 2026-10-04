"""A missing exact source observation halts, never becomes stale data or cash."""
import copy
import json

import pytest

from r31_cohort_audit import required_pair
from r31_cohort_sources import DATA
from r31_native import parse_archive
from r31_units import parse_versions
from r31_units_sources import DATA as UNITS


def source(vintage):
    m=json.loads((DATA/'manifest.json').read_text());r=m['sources']['archive_'+vintage]
    return parse_archive((DATA/r['path']).read_bytes(),vintage)


def units():
    m=json.loads((UNITS/'manifest.json').read_text());r=m['sources']['unit_versions']
    return parse_versions((UNITS/r['path']).read_bytes())


def test_actual_missing_current_week_does_not_use_known_previous_week():
    parsed=source('2025-04-05')
    assert parsed['rows']['2025-03-26']=='3450.933' and '2025-04-02' not in parsed['rows']
    with pytest.raises(ValueError):required_pair(parsed,'2025-04-07',units())


def test_actual_complete_same_version_pair_uses_exact_dates_and_native_units():
    selected=required_pair(source('2025-04-12'),'2025-04-14',units())
    assert [r['observation_date'] for r in selected]==['2025-04-09','2025-03-12']
    assert all(r['native_version_units']['units']=='Billions of U.S. Dollars' for r in selected)


@pytest.mark.parametrize('value',['','.'])
def test_blank_or_dot_is_not_a_real_zero_or_favorable_cash(value):
    parsed=source('2025-04-12');parsed['rows']['2025-04-09']=value
    with pytest.raises(ValueError):required_pair(parsed,'2025-04-14',units())


def test_real_native_zero_is_distinct_from_missing():
    parsed=source('2025-04-12');parsed['rows']['2025-04-09']='0'
    selected=required_pair(parsed,'2025-04-14',units())
    assert selected[0]['native_text']=='0' and selected[0]['normalized_million_usd']=='0'


@pytest.mark.parametrize('change',['foreign_vintage','wrong_weekday'])
def test_foreign_version_or_changed_decision_calendar_cannot_rescue(change):
    parsed=copy.deepcopy(source('2025-04-12'));monday='2025-04-14'
    if change=='foreign_vintage':parsed['vintage']='2025-04-19'
    else:monday='2025-04-15'
    with pytest.raises(ValueError):required_pair(parsed,monday,units())
