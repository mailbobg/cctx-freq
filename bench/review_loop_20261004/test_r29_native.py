"""Exact independent source joins reject synthetic feature substitutions."""
from copy import deepcopy
import hashlib

import pandas as pd
import pytest

from r29_funding import feature
from r29_view import parse_native
from r29_reflection import check_native_feature


def fixture():
    body = b'observation_date,SOFR_20210130,EFFR_20210130\n2021-01-25,0.08,0.05\n2021-01-27,0.05,0.08\n2021-01-28,,0.08\n2021-01-29,0.10,0.08\n'
    record = dict(sha256=hashlib.sha256(body).hexdigest(), retrieved_utc='2026-10-05T00:00:00+00:00')
    parsed = parse_native(body, '2021-01-30')
    parsed.update(source_sha256=record['sha256'], source_record='archive_2021-01-30', current_capture_utc=record['retrieved_utc'])
    monday = pd.Timestamp('2021-02-01', tz='UTC')
    return monday, feature(monday, parsed), body, record


def test_all_eligible_native_cells_and_blank_state_checked_before_selection():
    result = check_native_feature(*fixture())
    assert result == dict(original_rows=4, eligible_rows=3, selected_native_rate_cells=2,
                         eligible_native_rate_cells=6, eligible_unknown_cells=1)


@pytest.mark.parametrize('key,value',[
    ('sofr_native', '0.050'), ('current_capture_utc', '2021-01-30T00:00:00+00:00'),
    ('observation_day', '2021-01-25T00:00:00+00:00'), ('selected_original_row_index', 0),
    ('allow', False), ('historical_first_public_receipt_certified', True),
])
def test_independent_native_text_capture_date_index_sign_and_receipt_not_substituted(key, value):
    monday, f, body, record = fixture()
    f[key] = value
    with pytest.raises(ValueError):
        check_native_feature(monday, f, body, record)


def test_older_or_unknown_eligible_native_row_cannot_be_deleted():
    monday, f, body, record = fixture()
    for index in (0, 2):
        forged = deepcopy(f)
        forged['native_eligible_observations'].pop(index)
        with pytest.raises(ValueError):
            check_native_feature(monday, forged, body, record)


def test_selected_native_value_and_self_consistent_sign_cannot_replace_raw():
    monday, f, body, record = fixture()
    f['native_eligible_observations'][1]['native_values']['SOFR'] = '0.09'
    f.update(sofr_native='0.09', allow=False, reason='positive_official_funding_spread')
    with pytest.raises(ValueError):
        check_native_feature(monday, f, body, record)
