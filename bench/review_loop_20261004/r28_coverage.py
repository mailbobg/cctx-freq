"""Full fixed native observation/version coverage; source failure has no P&L."""
from collections import Counter
from datetime import date
from decimal import Decimal
import hashlib
import json

from r20_audit import retained
from r28_revision import parse_archive, same_week_revision
import r28_cohort_sources as source


def places(text):
    if text is None or text in ('', '.'):
        return None
    return max(0, -Decimal(text).as_tuple().exponent)


def describe_pair(pair, current, prior):
    out = same_week_revision(pair, current, prior)
    week = date.fromisoformat(pair['observation_week'])
    current_places = places(out['current_native'])
    prior_places = places(out['prior_native'])
    out.update(required_pair_known=out['state']=='known_same_week_revision',
        missing_required_sides=[key for key, parsed in [('current', current), ('prior', prior)]
                                if week not in parsed['rows']],
        current_native_rows=len(current['rows']), prior_native_rows=len(prior['rows']),
        current_latest_observation=max(current['rows']).isoformat() if current['rows'] else None,
        prior_latest_observation=max(prior['rows']).isoformat() if prior['rows'] else None,
        current_reported_decimal_places=current_places, prior_reported_decimal_places=prior_places,
        reported_decimal_places_differ=(current_places is not None and prior_places is not None and
                                        current_places != prior_places),
        no_latest_week_substitution_or_missingness_cash=True)
    return out


def main():
    records = source.collect()
    selected = source.selected()
    series, hashes, versions = {}, {}, []
    for key, record in records.items():
        retained(source.DATA, record)
        if record.get('path'):
            hashes['r28_cohort_sources/'+record['path']] = record['sha256']
    for key, (body, record, used) in selected.items():
        vintage = key.removeprefix('archive_')
        parsed = parse_archive(body, vintage)
        series[vintage] = parsed
        versions.append(dict(vintage=vintage, source_record=used, source_sha256=record['sha256'],
            current_capture_utc=record['retrieved_utc'], native_rows=len(parsed['rows']),
            native_row_decimal_place_counts=dict(Counter(str(places(row['native_value'])) for row in parsed['rows'].values())),
            native_first_observation=min(parsed['rows']).isoformat() if parsed['rows'] else None,
            native_latest_observation=max(parsed['rows']).isoformat() if parsed['rows'] else None))
    coverage = []
    for pair in source.decision_pairs():
        native_pair = {k: v for k, v in pair.items() if k != 'window'}
        current_key = 'archive_'+pair['current_vintage']
        prior_key = 'archive_'+pair['prior_vintage']
        current_body, current_record, current_used = selected[current_key]
        prior_body, prior_record, prior_used = selected[prior_key]
        fact = describe_pair(native_pair, series[pair['current_vintage']], series[pair['prior_vintage']])
        fact.update(window=pair['window'], current_source_record=current_used, prior_source_record=prior_used,
            current_source_sha256=current_record['sha256'], prior_source_sha256=prior_record['sha256'])
        if not fact['required_pair_known']:
            fact.update(current_original_csv=current_body.decode('utf-8'),
                        prior_original_csv=prior_body.decode('utf-8'))
        coverage.append(fact)
    hashes['r28_cohort_sources/manifest.json'] = hashlib.sha256((source.DATA/'manifest.json').read_bytes()).hexdigest()
    probe = json.loads((source.HERE/'r28_probe_audit.json').read_text())
    for name, sha in probe['retained_inputs_sha256'].items():
        if hashlib.sha256((source.HERE/name).read_bytes()).hexdigest() != sha:
            raise ValueError('Original probe native provenance changed')
        hashes[name] = sha
    failed = [row for row in coverage if not row['required_pair_known']]
    result = dict(source_stage_only=True, registered_alpha_hypothesis=True,
        alpha_weight_cohort_or_outcomes_evaluated=False, new_performance=False, adopted=False,
        source_plan_commit='d1437ea', protocol_commit='24454f2', original_version_roles=len(source.targets()),
        retained_cohort_records=len(records), inherited_original_probe_records=sum(bool(r.get('inherited')) for r in records.values()),
        new_cohort_http_attempts=sum(not r.get('inherited', False) for r in records.values()),
        status_counts=dict(Counter(str(r['status']) for r in records.values())),
        distinct_native_versions=len(versions), native_observation_rows=sum(v['native_rows'] for v in versions),
        native_reported_decimal_place_counts=dict(Counter(str(places(row['native_value'])) for s in series.values() for row in s['rows'].values())),
        required_decision_pairs=len(coverage), known_required_pairs=len(coverage)-len(failed),
        unavailable_required_pairs=len(failed), missing_required_native_cells=sum(len(row['missing_required_sides']) for row in coverage),
        pairs_with_reported_precision_change=sum(row['reported_decimal_places_differ'] for row in coverage),
        unavailable_required_pair_states=dict(Counter(row['state'] for row in failed)),
        source_gate_permits_full_alpha_screen=not failed, native_version_coverage=versions,
        exact_same_week_coverage=coverage, unavailable_required_pair_examples=failed,
        prior_incorrect_native_header_contract_and_failures_retained=True,
        original_signed_precision_versions_values_dates_unknowns_and_captures_retained=True,
        no_old_week_current_history_rounding_or_missingness_substitution=True,
        no_sign_threshold_week_version_day_latency_window_asset_fee_or_risk_rescue=True,
        historical_version_semantics_supported=True, historical_first_public_receipts_certified=False,
        date_only_clock_limit='R28_CLOCK_CLARIFICATION.md',
        no_strategy_bot_image_or_independent_forward_change=True, retained_inputs_sha256=hashes,
        code_sha256={name: hashlib.sha256((source.HERE/name).read_bytes()).hexdigest() for name in
            ('R28_SOURCE_PLAN.md', 'R28_PROTOCOL.md', 'R28_CLOCK_CLARIFICATION.md', 'r28_sources.py',
             'r28_revision.py', 'r28_probe_audit.py', 'r28_cohort_sources.py', 'r28_policy.py',
             'r28_coverage.py', 'test_r28_revision.py', 'test_r28_policy.py', 'test_r28_coverage.py')},
        interpretation='Exact same-observation-week pairs differ from latest-week comparisons. Missing fixed weeks prevent the registered full policy screen: no P&L, zero-return result, stale/cash repair or losing-alpha claim. Native precision changes are source facts without an authenticated economic cause; no fitted noise threshold. Preserve this unavailable hypothesis and choose a genuinely different feasible mechanism.' if failed else
            'Full fixed source coverage passes; still no economic performance or authenticated first-public/actual execution evidence. Freeze source/cohort/controls before the sole outcome screen.')
    text = json.dumps(result, indent=2, sort_keys=True)+'\n'
    path = source.HERE/'r28_source_coverage.json'
    if path.exists() and path.read_text() != text:
        raise ValueError('Frozen source coverage changed')
    path.write_text(text)
    print(json.dumps({k: result[k] for k in ('distinct_native_versions', 'required_decision_pairs',
        'known_required_pairs', 'unavailable_required_pairs', 'missing_required_native_cells',
        'pairs_with_reported_precision_change', 'source_gate_permits_full_alpha_screen')}))


if __name__ == '__main__':
    main()
