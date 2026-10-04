"""Bounded native views versus independent single-series requests, before alpha."""
from collections import Counter
import hashlib
import json

from r20_audit import retained
from r29_view import parse_native, interval_view, reconcile_views, reconcile_native_availability
import r29_sources as original
import r29_range_sources as counterpart


def main():
    prior = json.loads((original.HERE/'r29_source_observation.json').read_text())
    for name, sha in prior['retained_inputs_sha256'].items():
        if hashlib.sha256((original.HERE/name).read_bytes()).hexdigest() != sha:
            raise ValueError('Original native probe input changed')
    records = json.loads((counterpart.DATA/'manifest.json').read_text())['sources']
    if set(records) != set(counterpart.probe_targets()):
        raise ValueError('Unreviewed independent counterpart request scope')
    originals = json.loads((original.DATA/'manifest.json').read_text())['sources']
    samples, hashes = [], dict(prior['retained_inputs_sha256'])
    for pair in original.probe_pairs():
        key = 'archive_'+pair['vintage']
        successes = sorted((name, r) for name, r in originals.items()
                           if name.split('_retry_')[0] == key and r['status'] == 200)
        if not successes:
            raise ValueError('Required unchanged original dual-series version unavailable')
        name, record = successes[0]
        body = retained(original.DATA, record)
        if record['url'] != original.probe_targets()[key]:
            raise ValueError('Original dual-series request altered')
        parsed = parse_native(body, pair['vintage'])
        dual = interval_view(parsed)
        single_views, identities = {}, {}
        for series in ('SOFR', 'EFFR'):
            selected = series+'_'+pair['vintage']
            r = records[selected]
            if r['url'] != counterpart.probe_targets()[selected] or r['status'] != 200:
                raise ValueError('Required native single-series counterpart unavailable')
            single_body = retained(counterpart.DATA, r)
            view = interval_view(parse_native(single_body, pair['vintage'], (series,)))
            single_views[series] = view
            hashes['r29_range_sources/'+r['path']] = r['sha256']
            identities[series] = dict(source_record=selected, source_url=r['url'],
                sha256=r['sha256'], requested_utc=r['requested_utc'], captured_utc=r['retrieved_utc'],
                original_native_rows=view['original_native_rows'],
                outside_original_rows=view['outside_original_rows'],
                actual_http_range_honored=view['http_requested_range_honored'],
                native_view=view['rows'])
        try:
            reconcile_views(dual, single_views)
            strict_error = None
        except ValueError as exc:
            strict_error = str(exc)
        check = reconcile_native_availability(dual, single_views)
        if hashlib.sha256(body).hexdigest() != record['sha256'] or len(parsed['rows']) != dual['original_native_rows']:
            raise ValueError('Native original body or full row scope changed during view')
        samples.append(dict(**pair, original_source_record=name, original_source_sha256=record['sha256'],
            original_full_rows=dual['original_native_rows'], original_extra_older_rows=dual['outside_original_rows'],
            original_http_range_contract_pass=dual['http_requested_range_honored'],
            explicit_native_interval_start=dual['interval_start'], explicit_native_interval_end=dual['interval_end'],
            original_backed_native_view=dual['rows'], single_series_counterparts=identities,
            direct_native_counterpart_check=check, original_exact_calendar_check_pass=strict_error is None,
            original_exact_calendar_check_error=strict_error, original_full_rows_and_bodies_retained=True))
    hashes['r29_range_sources/manifest.json'] = hashlib.sha256((counterpart.DATA/'manifest.json').read_bytes()).hexdigest()
    result = dict(source_stage_only=True, new_alpha_trial=False, new_performance=False, adopted=False,
        original_source_plan_commit='591d66a', range_check_plan_commit='ece778f',
        original_scope_failure_retained_in='r29_source_observation.json',
        independent_counterpart_roles=8, retained_new_attempts=len(records),
        new_status_counts=dict(Counter(str(r['status']) for r in records.values())),
        forensic_mondays=len(samples), native_in_range_dates=sum(v['direct_native_counterpart_check']['native_observation_dates_checked'] for v in samples),
        native_counterpart_cells_checked=sum(v['direct_native_counterpart_check']['native_cells_checked'] for v in samples),
        native_known_exact_text_cells=sum(v['direct_native_counterpart_check']['known_exact_text_cells'] for v in samples),
        native_both_unavailable_cells=sum(v['direct_native_counterpart_check']['both_unavailable_cells'] for v in samples),
        native_presence_difference_cells=sum(v['direct_native_counterpart_check']['native_presence_difference_cells'] for v in samples),
        original_exact_calendar_contract_pass_samples=sum(v['original_exact_calendar_check_pass'] for v in samples),
        all_single_series_actual_http_ranges_honored=all(c['actual_http_range_honored'] for v in samples for c in v['single_series_counterparts'].values()),
        original_dual_series_http_ranges_honored=False,
        explicit_native_view_source_contract_crosscheck_pass=True,
        no_original_raw_row_deletion_sort_fill_rounding_or_version_replacement=True,
        economic_rule_threshold_staleness_or_full_alpha_cohort_outcomes_defined=False,
        historical_first_public_receipts_constant_methodology_or_actual_execution_certified=False,
        no_strategy_bot_image_or_independent_forward_change=True, samples=samples,
        retained_inputs_sha256=hashes,
        code_sha256={n: hashlib.sha256((original.HERE/n).read_bytes()).hexdigest() for n in
            ('R29_RANGE_PLAN.md', 'R29_NATIVE_AVAILABILITY_CLARIFICATION.md', 'r29_strict_view_contract_failure.json',
             'r29_range_sources.py', 'r29_view.py', 'test_r29_view.py', 'r29_range_audit.py')},
        interpretation='Every known same-vintage/date native value matches an independently requested single-series original. All unavailable cells and presence/text encodings are separately retained; one original exact-calendar check still fails for an extra all-empty dual row. The unchanged dual-series response still violates the original HTTP range assumption. The separately documented analytical view and complete native availability contract do not rewrite raw data, waive missing required known values, or certify publication/execution/crypto predictability. Freeze a separate critic and one economic rule before full features/outcomes.')
    text = json.dumps(result, indent=2, sort_keys=True)+'\n'
    path = original.HERE/'r29_range_audit.json'
    if path.exists() and path.read_text() != text:
        raise ValueError('Frozen native source contract corroboration changed')
    path.write_text(text)
    print(json.dumps({k: result[k] for k in ('independent_counterpart_roles', 'new_status_counts',
        'native_in_range_dates', 'native_counterpart_cells_checked',
        'all_single_series_actual_http_ranges_honored', 'explicit_native_view_source_contract_crosscheck_pass')}))


if __name__ == '__main__':
    main()
