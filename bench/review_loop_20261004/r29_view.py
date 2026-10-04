"""Explicit analytical native interval views of unchanged historical raw bodies."""
import csv
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import io
import re


def parse_native(body, vintage, series=('SOFR', 'EFFR')):
    if series not in (('SOFR', 'EFFR'), ('SOFR',), ('EFFR',)):
        raise ValueError('Unreviewed native source identities/order')
    cutoff = date.fromisoformat(vintage)
    if cutoff.isoformat() != vintage or cutoff.weekday() != 5:
        raise ValueError('Unreviewed canonical Saturday as-of date')
    reader = csv.reader(io.StringIO(body.decode('utf-8')))
    header = ['observation_date']+[s+'_'+vintage.replace('-', '') for s in series]
    if next(reader, None) != header:
        raise ValueError('Native column series/order/vintage differs')
    rows, previous = [], None
    for index, fields in enumerate(reader):
        if len(fields) != len(header):
            raise ValueError('Original native row width differs')
        day = date.fromisoformat(fields[0])
        if (day.isoformat() != fields[0] or day.weekday() > 4 or day > cutoff or
            previous is not None and day <= previous):
            raise ValueError('Future/foreign/duplicate/unordered native observation')
        for text in fields[1:]:
            if text in ('', '.'):
                continue
            if not re.fullmatch(r'-?\d+(?:\.\d+)?', text) or not Decimal(text).is_finite():
                raise ValueError('Unreviewed original signed decimal')
        rows.append(dict(original_row_index=index, observation_date=fields[0],
                         native_values=dict(zip(series, fields[1:]))))
        previous = day
    if not rows:
        raise ValueError('Empty original archive is unavailable')
    return dict(vintage=vintage, series=series, original_header=header, rows=rows,
                original_body_sha256=hashlib.sha256(body).hexdigest(), original_body_bytes=len(body))


def interval_view(parsed):
    cutoff = date.fromisoformat(parsed['vintage'])
    start = cutoff-timedelta(days=13)
    selected = [row for row in parsed['rows'] if start <= date.fromisoformat(row['observation_date']) <= cutoff]
    outside = len(parsed['rows'])-len(selected)
    return dict(vintage=parsed['vintage'], series=parsed['series'],
        original_body_sha256=parsed['original_body_sha256'], original_body_bytes=parsed['original_body_bytes'],
        original_native_rows=len(parsed['rows']), outside_original_rows=outside,
        http_requested_range_honored=outside==0,
        explicitly_defined_analytical_interval_view=True,
        interval_start=start.isoformat(), interval_end=cutoff.isoformat(), rows=selected,
        original_body_and_full_parsed_rows_unchanged=True)


def reconcile_views(dual, singles):
    if dual['series'] != ('SOFR', 'EFFR') or set(singles) != {'SOFR', 'EFFR'}:
        raise ValueError('Incomplete/foreign native counterpart scope')
    dates = [r['observation_date'] for r in dual['rows']]
    checked = 0
    for series in ('SOFR', 'EFFR'):
        counterpart = singles[series]
        if (counterpart['series'] != (series,) or counterpart['vintage'] != dual['vintage'] or
            counterpart['interval_start'] != dual['interval_start'] or counterpart['interval_end'] != dual['interval_end'] or
            [r['observation_date'] for r in counterpart['rows']] != dates):
            raise ValueError('Exact date/series/version/range counterpart differs')
        for original, single in zip(dual['rows'], counterpart['rows']):
            if original['native_values'][series] != single['native_values'][series]:
                raise ValueError('Native counterpart text/unknown state differs')
            checked += 1
    return dict(native_observation_dates_checked=len(dates), native_cells_checked=checked,
                exact_same_date_text_and_unknown_states=True,
                historical_first_public_receipt_or_constant_methodology_certified=False)


def reconcile_native_availability(dual, singles):
    if dual['series'] != ('SOFR', 'EFFR') or set(singles) != {'SOFR', 'EFFR'}:
        raise ValueError('Incomplete/foreign native availability scope')
    original = {r['observation_date']: r for r in dual['rows']}
    checked, known, unavailable, presence_differences = 0, 0, [], 0
    dates_seen = set()
    for series in ('SOFR', 'EFFR'):
        view = singles[series]
        if (view['series'] != (series,) or view['vintage'] != dual['vintage'] or
            view['interval_start'] != dual['interval_start'] or view['interval_end'] != dual['interval_end']):
            raise ValueError('Native series/vintage/interval identity differs')
        other = {r['observation_date']: r for r in view['rows']}
        dates = original.keys() | other.keys()
        dates_seen.update(dates)
        for day in sorted(dates):
            a = original[day]['native_values'][series] if day in original else None
            b = other[day]['native_values'][series] if day in other else None
            a_known, b_known = a not in (None, '', '.'), b not in (None, '', '.')
            if a_known != b_known or a_known and a != b:
                raise ValueError('Known native value unavailable/altered in counterpart')
            checked += 1
            presence_differences += int((day in original) != (day in other))
            if a_known:
                known += 1
            else:
                unavailable.append(dict(series=series, observation_date=day,
                    dual_row_present=day in original, single_row_present=day in other,
                    dual_native_text=a, single_native_text=b, state='both_unavailable'))
    return dict(native_observation_dates_checked=len(dates_seen), native_cells_checked=checked,
        known_exact_text_cells=known, both_unavailable_cells=len(unavailable),
        native_presence_difference_cells=presence_differences,
        unavailable_native_states=unavailable, complete_native_union_availability_and_known_value_check=True,
        raw_serialization_identity_not_claimed=True, no_missing_zero_or_cash_substitution=True,
        historical_first_public_receipt_or_constant_methodology_certified=False)
