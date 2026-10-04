"""Units follow native vintage metadata, never observation-date or value guesses."""
import copy
import json

import pytest

from r31_native import parse_archive, parse_publisher
from r31_sources import DATA as ORIGINAL
from r31_units_sources import DATA
from r31_units import parse_versions, parse_release_meta, reconcile_native, version_units


def body(folder,role):
    m=json.loads((folder/'manifest.json').read_text())
    return (folder/m['sources'][role]['path']).read_bytes()


def versions():return parse_versions(body(DATA,'unit_versions'))


def test_publisher_unit_versions_are_native_release_intervals():
    rows=versions()
    assert len(rows)==4
    assert version_units(rows,'2025-11-08')['units']=='Billions of U.S. Dollars'
    assert version_units(rows,'2025-11-15')['units']=='Millions of U.S. Dollars'


@pytest.mark.parametrize('release,units,last', [('2025-11-06','Billions of U.S. Dollars','2862.569'),
                                             ('2025-11-13','Millions of U.S. Dollars','2855030.0')])
def test_dated_metadata_independently_confirms_units_and_last_native_value(release,units,last):
    parsed=parse_release_meta(body(DATA,'release_metadata_'+release),release)
    assert parsed['units']==units and parsed['last_native_value']==last


def test_same_old_observation_in_two_vintages_does_not_create_thousandfold_gain():
    publisher=parse_publisher(body(DATA,'publisher_2025-11-06'),'2025-11-06')
    values=[]
    for vintage in ('2025-11-08','2025-11-15'):
        checked=reconcile_native(publisher,parse_archive(body(DATA,'archive_'+vintage),vintage),versions())
        values.append(checked['archive_normalized_million_usd'])
    assert values==['2862569.000','2862569']


@pytest.mark.parametrize('monday,current,prior', [
    ('2019-09-23','2019-09-19','2019-08-22'),('2021-02-01','2021-01-28','2020-12-31'),
    ('2024-01-01','2023-12-28','2023-11-30'),('2026-08-31','2026-08-27','2026-07-30'),
    ('2024-12-02','2024-11-29','2024-10-31')])
def test_all_original_forensic_counterparts_match_only_with_native_vintage_units(monday,current,prior):
    from datetime import date,timedelta
    vintage=(date.fromisoformat(monday)-timedelta(days=2)).isoformat()
    archive=parse_archive(body(ORIGINAL,'archive_'+vintage),vintage)
    for release in (current,prior):
        result=reconcile_native(parse_publisher(body(ORIGINAL,'publisher_'+release),release),archive,versions())
        assert result['normalized_native_value_exact']


@pytest.mark.parametrize('change',['overlap','unknown_unit','gap','outside_current_metadata'])
def test_unverified_or_ambiguous_unit_scope_cannot_be_guessed(change):
    rows=copy.deepcopy(versions()); vintage='2025-11-08'
    if change=='overlap':rows.append(copy.deepcopy(rows[2]))
    elif change=='unknown_unit':rows[2]['units']='Trillions of Dollars'
    elif change=='gap':rows=rows[:2]+rows[3:]
    else:vintage='2026-10-02'
    with pytest.raises(ValueError):version_units(rows,vintage)


def test_neighbor_series_and_wrong_release_are_not_native_unit_proof():
    source=body(DATA,'unit_versions')
    with pytest.raises(ValueError):parse_versions(source.replace(b'href="/series?seid=WRESBAL"',b'href="/series?seid=WTREGEN"'))
    with pytest.raises(ValueError):parse_release_meta(body(DATA,'release_metadata_2025-11-06'),'2025-11-13')


def test_unit_evidence_does_not_hide_actual_value_difference_or_missing():
    publisher=parse_publisher(body(DATA,'publisher_2025-11-06'),'2025-11-06')
    archive=parse_archive(body(DATA,'archive_2025-11-08'),'2025-11-08')
    for value in ('2862.568','.'):
        archive['rows']['2025-11-05']=value
        with pytest.raises(ValueError):reconcile_native(publisher,archive,versions())
