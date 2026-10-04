"""Original H.4.1 averages, dated columns and archive units stay distinct."""
from decimal import Decimal
import json

import pytest

from r31_native import parse_archive, parse_publisher, reconcile_same_units
from r31_sources import DATA


def body(role):
    manifest = json.loads((DATA/'manifest.json').read_text())
    return (DATA/manifest['sources'][role]['path']).read_bytes()


@pytest.mark.parametrize('release,average,wednesday', [
    ('2019-09-19',1394122,1385426),('2021-01-28',3163008,3228751),
    ('2023-12-28',3446459,3434213),('2024-11-29',3227674,3234035),
    ('2026-08-27',2924936,2916824)])
def test_original_ascii_and_modern_table_do_not_confuse_average_with_stock(release,average,wednesday):
    parsed = parse_publisher(body('publisher_'+release),release)
    assert parsed['week_average_million_usd'] == average
    assert parsed['wednesday_level_million_usd'] == wednesday
    assert parsed['observation_date'] < parsed['release_date']


def test_friday_holiday_publication_is_not_backdated_to_thursday_or_wednesday():
    parsed = parse_publisher(body('publisher_2024-11-29'),'2024-11-29')
    assert parsed['observation_date'] == '2024-11-27'
    with pytest.raises(ValueError):
        parse_publisher(body('publisher_2024-11-29'),'2024-11-28')


@pytest.mark.parametrize('change', ['wrong_units','wrong_average_header','wrong_date','swapped_column_semantics'])
def test_tampered_table_units_header_dates_or_column_identity_fail(change):
    source = body('publisher_2026-08-27')
    original = source
    if change == 'wrong_units': source = source.replace(b'Millions',b'Billions')
    elif change == 'wrong_average_header': source = source.replace(b'Averages of daily figures',b'Wednesday end-of-day stocks')
    elif change == 'wrong_date': source = source.replace(b'Aug 26, 2026',b'Aug 25, 2026')
    else: source = source.replace(b't2r1c1 t2h1c2 t2h2c1 ',b't2r1c1 t2h1c3 ')
    assert source != original
    with pytest.raises(ValueError): parse_publisher(source,'2026-08-27')


def test_same_unit_assumption_fails_in_old_native_archive_without_guessed_scaling():
    publisher = parse_publisher(body('publisher_2021-01-28'),'2021-01-28')
    archive = parse_archive(body('archive_2021-01-30'),'2021-01-30')
    assert archive['rows']['2021-01-27'] == '3163.008'
    with pytest.raises(ValueError): reconcile_same_units(publisher,archive)


def test_actual_new_native_archive_same_unit_counterpart_matches():
    publisher = parse_publisher(body('publisher_2026-08-27'),'2026-08-27')
    archive = parse_archive(body('archive_2026-08-29'),'2026-08-29')
    assert reconcile_same_units(publisher,archive)['same_unit_value_exact']


def test_native_csv_missing_is_distinct_from_real_zero():
    archive = parse_archive(b'observation_date,WRESBAL_20210130\n2021-01-20,.\n2021-01-27,0\n','2021-01-30')
    assert archive['rows']['2021-01-20'] == '.'
    assert Decimal(archive['rows']['2021-01-27']) == 0


@pytest.mark.parametrize('text', [
    'observation_date,WRESBAL_20210129\n2021-01-27,1\n',
    'observation_date,WRESBAL_20210130\n2021-01-27,1\n2021-01-27,2\n',
    'observation_date,WRESBAL_20210130\n2021-02-03,1\n',
    'observation_date,WRESBAL_20210130\n2021-01-26,1\n',
    'observation_date,WRESBAL_20210130\n2021-01-27,NaN\n'])
def test_foreign_version_duplicate_future_wrong_weekday_or_invalid_value_fails(text):
    with pytest.raises(ValueError): parse_archive(text.encode(),'2021-01-30')
