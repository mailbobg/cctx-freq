"""Forensic native scope/clock inspection only; no alpha or eligible feature view."""
from collections import Counter
import csv
from datetime import date, timedelta
from decimal import Decimal, localcontext
import hashlib
import io
import json
import re

from r20_audit import retained
from r28_probe_audit import html_text
import r29_sources as source


def describe_native(body, vintage):
    cutoff = date.fromisoformat(vintage)
    if cutoff.isoformat() != vintage or cutoff.weekday() != 5:
        raise ValueError('Unreviewed native as-of identity')
    reader = csv.reader(io.StringIO(body.decode('utf-8')))
    columns = ['observation_date', 'SOFR_'+vintage.replace('-', ''), 'EFFR_'+vintage.replace('-', '')]
    if next(reader, None) != columns:
        raise ValueError('Native rate series/version/order differs')
    earliest = cutoff-timedelta(days=13)
    rows, last, unknown = {}, None, Counter(dict(SOFR=0, EFFR=0))
    for fields in reader:
        if len(fields) != 3:
            raise ValueError('Native original column count differs')
        day = date.fromisoformat(fields[0])
        if day.isoformat() != fields[0] or day.weekday() > 4 or day > cutoff or last is not None and day <= last:
            raise ValueError('Foreign/future/duplicated/unordered native observation')
        values = {}
        for key, text in zip(('SOFR', 'EFFR'), fields[1:]):
            if text in ('', '.'):
                unknown[key] += 1
            elif not re.fullmatch(r'-?\d+(?:\.\d+)?', text) or not Decimal(text).is_finite():
                raise ValueError('Invalid native signed rate value')
            values[key] = text
        rows[day] = values
        last = day
    if not rows:
        raise ValueError('Empty original rate source')
    known = [day for day, row in rows.items() if all(row[key] not in ('', '.') for key in ('SOFR', 'EFFR'))]
    common = max(known) if known else None
    row = rows[common] if common is not None else None
    pp, bp = None, None
    if row:
        with localcontext() as ctx:
            ctx.prec = max(50, sum(len(s) for s in row.values())+2)
            pp = Decimal(row['SOFR'])-Decimal(row['EFFR'])
            bp = pp*100
    outside = [day for day in rows if not earliest <= day <= cutoff]
    return dict(vintage=vintage, requested_observation_start=earliest.isoformat(), requested_observation_end=vintage,
        original_header=columns, original_rows=len(rows), first_original_observation=min(rows).isoformat(),
        last_original_observation=max(rows).isoformat(), outside_requested_range_rows=len(outside),
        original_requested_range_contract_pass=not outside, native_unknown_cells=dict(unknown),
        latest_known_date_by_series={key: max((day for day, row in rows.items() if row[key] not in ('', '.')),
            default=None).isoformat() if any(row[key] not in ('', '.') for row in rows.values()) else None for key in ('SOFR', 'EFFR')},
        latest_common_known_observation=common.isoformat() if common else None,
        latest_common_sofr_native=row['SOFR'] if row else None, latest_common_effr_native=row['EFFR'] if row else None,
        latest_common_difference_percentage_points=format(pp, 'f') if pp is not None else None,
        latest_common_difference_basis_points=format(bp, 'f') if bp is not None else None,
        descriptive_difference_is_not_a_return_or_crypto_funding_payout=True,
        native_observation_label_is_not_a_publication_date=True,
        registered_alpha_or_performance=False,
        no_native_row_deletion_sort_rounding_zero_fill_or_eligible_feature_view=True)


def main():
    originals = source.probe_targets()
    records = json.loads((source.DATA/'manifest.json').read_text())['sources']
    selected, hashes = {}, {}
    for key, record in sorted(records.items()):
        root = key.split('_retry_')[0]
        if root not in originals or record['url'] != originals[root]:
            raise ValueError('Foreign original/transport-retry source role')
        body = retained(source.DATA, record)
        if body is not None:
            hashes['r29_sources/'+record['path']] = record['sha256']
        if record['status'] == 200 and root not in selected:
            selected[root] = (body, record, key)
    if set(selected) != set(originals):
        raise ValueError('Unavailable primary source remains a source failure')
    markers = {
        'alfred_help': ('Observations by Vintage Date',),
        'alfred_sofr_identity': ('(SOFR)', 'Percent', 'Daily'),
        'alfred_effr_identity': ('(EFFR)', 'Percent', 'Daily'),
        'publisher_sofr': ('Secured Overnight Financing Rate',),
        'publisher_effr': ('Effective Federal Funds Rate',),
        'publisher_methodology': ('9:00 a.m. ET', '8:00 a.m. ET', '2:30 p.m. ET', 'one business day following its value date'),
        'publisher_indicative_warning': ('November 25, 2024', 'should not be considered official values'),
        'publisher_repo_study': ('quarter-end seasonality', 'the next day', 'cash'),
    }
    for key, phrases in markers.items():
        text = html_text(selected[key][0])
        if any(phrase not in text for phrase in phrases):
            raise ValueError('Retained primary definition/clock context differs '+key)
    samples = []
    for pair in source.probe_pairs():
        body, record, key = selected['archive_'+pair['vintage']]
        fact = describe_native(body, pair['vintage'])
        fact.update(monday=pair['monday'], source_record=key, original_url=record['url'],
            body_sha256=record['sha256'], capture_requested_utc=record['requested_utc'],
            capture_received_utc=record['retrieved_utc'], http_headers=record['headers'],
            capture_or_last_modified_is_not_first_public_receipt=True)
        samples.append(fact)
    hashes['r29_sources/manifest.json'] = hashlib.sha256((source.DATA/'manifest.json').read_bytes()).hexdigest()
    result = dict(source_stage_only=True, new_alpha_trial=False, new_performance=False, adopted=False,
        source_plan_commit='591d66a', original_request_roles=len(originals), retained_attempts=len(records),
        status_counts=dict(Counter(str(r['status']) for r in records.values())),
        native_forensic_samples=samples, requested_range_contract_pass_samples=sum(v['original_requested_range_contract_pass'] for v in samples),
        original_request_range_mismatch_resolved=False, eligible_native_feature_view_defined=False,
        publication_effective_dates_methodology_and_as_of_versions_are_distinct=True,
        historical_first_public_receipts_certified=False,
        primary_definition_publication_and_indicative_history_warning_bodies_retained=True,
        no_friday_backdating_blank_zero_cash_current_history_or_indicative_series_substitution=True,
        alpha_rule_threshold_staleness_feature_cohort_weights_or_outcomes_defined=False,
        no_strategy_bot_image_or_independent_forward_change=True, retained_inputs_sha256=hashes,
        code_sha256={name: hashlib.sha256((source.HERE/name).read_bytes()).hexdigest() for name in
            ('R29_SOURCE_PLAN.md', 'r29_sources.py', 'r29_observation.py', 'test_r29_observation.py')},
        interpretation='Original two-series responses ignore the bounded start query and include older rows. Preserve every row and this failed scope assumption. Latest same-value-date rate comparisons are descriptive source/clock facts only, not an eligible full feature cohort or alpha. Assess a separately documented bounded native view/source-contract correction before any economic protocol; do not silently drop rows, replace versions, backdate Friday publication or infer profitability.')
    text = json.dumps(result, indent=2, sort_keys=True)+'\n'
    path = source.HERE/'r29_source_observation.json'
    if path.exists() and path.read_text() != text:
        raise ValueError('Frozen original source observation changed')
    path.write_text(text)
    print(json.dumps({k: result[k] for k in ('original_request_roles', 'retained_attempts', 'status_counts',
        'requested_range_contract_pass_samples', 'eligible_native_feature_view_defined')}))
    for fact in samples:
        print(fact['monday'], fact['original_rows'], fact['outside_requested_range_rows'],
              fact['latest_common_known_observation'], fact['latest_common_difference_basis_points'])


if __name__ == '__main__':
    main()
