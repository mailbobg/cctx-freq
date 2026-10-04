"""Real publisher dates, rather than observation labels, choose report editions."""
import copy
import json

import pytest

from r32_calendar import parse_calendar, report_for_observation
from r32_sources import DATA


def original():
    m=json.loads((DATA/'manifest.json').read_text())
    return (DATA/m['sources']['calendar_json']['path']).read_bytes()


def parsed():return parse_calendar(original(),'2026-10-04')


def test_actual_calendar_contains_original_gap_report_and_friday_holiday():
    calendar=parsed()
    assert report_for_observation(calendar,'2025-04-02')['release_date']=='2025-04-03'
    assert report_for_observation(calendar,'2024-11-27')['release_date']=='2024-11-29'
    assert calendar['first_release_date']=='1996-06-27'
    assert calendar['last_release_date']=='2026-10-01'


@pytest.mark.parametrize('change',['duplicate_date','foreign_month','foreign_year','future_date'])
def test_native_calendar_identity_conflicts_cannot_generate_a_report(change):
    body=json.loads(original());slot=body[0]['Months'][0]
    if change=='duplicate_date':slot['Dates'].append(slot['Dates'][0])
    elif change=='foreign_month':slot['MonthValue']='202609'
    elif change=='foreign_year':body[0]['yearValue']='2025'
    else:slot['Dates']=['20261008']
    with pytest.raises(ValueError):parse_calendar(json.dumps(body).encode(),'2026-10-04')


def test_absent_release_cannot_use_a_different_week_or_default_thursday():
    calendar=parsed();calendar['releases']={k:v for k,v in calendar['releases'].items() if k!='2024-11-29'}
    with pytest.raises(ValueError):report_for_observation(calendar,'2024-11-27')


def test_first_listed_publication_is_fixed_even_when_later_revision_is_present():
    calendar=copy.deepcopy(parsed())
    calendar['releases']['2025-04-04']=dict(release_date='2025-04-04',native_date_text='20250404',
        native_year_index=0,native_month_index=0,native_day_index=0,
        report_url='https://www.federalreserve.gov/releases/h41/20250404/')
    assert report_for_observation(calendar,'2025-04-02')['release_date']=='2025-04-03'


def test_observation_date_is_not_release_date_or_an_arbitrary_weekday():
    with pytest.raises(ValueError):report_for_observation(parsed(),'2025-04-03')
