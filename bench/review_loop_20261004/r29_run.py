"""Freeze native rate/version joins and unchanged accounting before outcomes."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from r29_cohort_sources import DATA, decision_pairs, selected, targets
from r29_view import parse_native, interval_view
from r29_funding import ASSETS, feature, overlay
from r29_freeze import assert_frozen_inputs
from r20_audit import retained
from r1_r2b_risk import signals, risk_weights, RiskHoldR2b
from r9_selection import load_prices
from r10_gold import simulate_spot, WINDOWS
from r13_atr import simulate as minute_simulate
from r8_crowding import summarize
from r19_run import frozen_json, max_gap

HERE = Path(__file__).resolve().parent


def feature_inputs():
    bodies = selected()
    records = json.loads((DATA/'manifest.json').read_text())['sources']
    hashes, versions, coverage, features = {}, {}, [], {}
    for key, record in records.items():
        if key.split('_retry_')[0] not in targets():
            raise ValueError('Foreign original or retry source')
        retained(DATA, record)
        if record.get('path'):
            hashes['r29_cohort_sources/'+record['path']] = record['sha256']
    hashes['r29_cohort_sources/manifest.json'] = hashlib.sha256((DATA/'manifest.json').read_bytes()).hexdigest()
    for folder in ('r29_sources', 'r29_range_sources'):
        manifest = HERE/folder/'manifest.json'
        for record in json.loads(manifest.read_text())['sources'].values():
            retained(HERE/folder, record)
            if record.get('path'):
                hashes[folder+'/'+record['path']] = record['sha256']
        hashes[folder+'/manifest.json'] = hashlib.sha256(manifest.read_bytes()).hexdigest()
    for pair in decision_pairs():
        body, record, name = bodies['archive_'+pair['vintage']]
        parsed = parse_native(body, pair['vintage'])
        parsed.update(source_sha256=record['sha256'], source_record=name,
                      current_capture_utc=record['retrieved_utc'])
        if parsed['original_body_sha256'] != record['sha256']:
            raise ValueError('Native original body identity differs')
        versions[pair['vintage']] = parsed
        view = interval_view(parsed)
        coverage.append(dict(vintage=pair['vintage'], selected_record=name,
            source_sha256=record['sha256'], original_native_rows=len(parsed['rows']),
            analytical_interval_rows=len(view['rows']), outside_original_rows=view['outside_original_rows'],
            http_requested_range_honored=view['http_requested_range_honored'],
            explicitly_defined_analytical_interval_view=True))
        monday = pd.Timestamp(pair['monday'], tz='UTC')
        value = feature(monday, parsed)
        value['window'] = pair['window']
        features[monday] = value
    # Cut actual native versions and observations, then reconstruct the same features.
    for cutoff in ('2021-12-31', '2024-12-31'):
        end = pd.Timestamp(cutoff, tz='UTC')
        past = {v:dict(p, rows=[r for r in p['rows']
                               if pd.Timestamp(r['observation_date'], tz='UTC') <= end])
                for v,p in versions.items() if pd.Timestamp(v, tz='UTC') <= end}
        for monday, value in features.items():
            if monday <= end:
                rebuilt = feature(monday, past[value['vintage']])
                rebuilt['window'] = value['window']
                if rebuilt != value:
                    raise ValueError('Native observation/version prefix changed')
    audit = dict(source_stage_only=True, new_performance=False, protocol_commit='58d4344',
        retained_attempts=len(records), historical_versions=len(coverage), decision_mondays=len(features),
        failed_transport_attempts=sum(r['status'] is None for r in records.values()),
        inherited_original_captures=sum(bool(r.get('inherited')) for r in records.values()),
        all_required_same_date_rates_known=all(f['known'] for f in features.values()),
        native_eligible_rows=sum(len(f['native_eligible_observations']) for f in features.values()),
        source_feature_cohort={str(d):f for d,f in features.items()}, native_archive_coverage=coverage,
        actual_native_version_and_observation_prefix_equal=True,
        original_bodies_never_rewritten=True, historical_first_public_receipt_certified=False,
        limitations='Original dual CSV ignores requested date bounds: explicitly labeled native analytical view. Exact Saturday versions and prior Monday-Thursday common-known observations. Current capture is not original public receipt. Official rates are not retrospective indicative SOFR, investable funding payouts, authenticated crypto fills or a forward sample.')
    frozen_json(HERE/'r29_source_audit.json', audit)
    return features, hashes


def prepare():
    features, hashes = feature_inputs()
    from r29_reflection import validate_source_cohort
    validate_source_cohort(save=True)
    px, spot, prior_hashes = load_prices()
    hashes.update(prior_hashes)
    for asset in ASSETS:
        px[asset+'_high'], px[asset+'_low'] = spot[asset].high, spot[asset].low
    base = risk_weights(px, signals(px, RiskHoldR2b))
    target, audit = overlay(base, features)
    disabled = pd.DataFrame(np.nan, index=px.index, columns=ASSETS)
    manifest = json.loads((HERE/'r13_data/manifest.json').read_text())
    minutes = {}
    for asset in ASSETS:
        path = HERE/f'r13_data/{asset}USDT_1m.parquet'
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if sha != manifest['output_sha256'][path.name]:
            raise ValueError('Original native minute source changed')
        hashes[str(path.relative_to(HERE))] = sha
        minutes[asset] = pd.read_parquet(path)
    gaps = {}
    for label, start, end in WINDOWS:
        for cost in (.0012, .0024):
            control = simulate_spot(px, base, start, end, cost)
            prior = pd.read_csv(HERE/f'r10_{label}_Cov10_baseline_cost{cost}_daily.csv', index_col='date', parse_dates=True)
            gaps[f'{label}_daily_cost{cost}'] = max_gap(control, prior, list(control))
            if label == 'recent_reused':
                curve, _ = minute_simulate(px, base, disabled, minutes, start, end, cost, active=False)
                old = pd.read_csv(HERE/f'r13_{label}_Cov10_baseline_cost{cost}_daily.csv', index_col='date', parse_dates=True)
                gaps[f'{label}_minute_cost{cost}'] = max_gap(curve, old, [c for c in curve if not c.startswith('pending_')])
    for cutoff in ('2021-12-31', '2024-12-31'):
        short = px.loc[:cutoff]
        prefix_base = risk_weights(short, signals(short, RiskHoldR2b))
        subset = {d:f for d,f in features.items() if d <= short.index[-1]+pd.Timedelta(days=1)}
        prefix, _ = overlay(prefix_base, subset)
        pd.testing.assert_frame_equal(prefix, target.loc[:cutoff])
    full, orders = minute_simulate(px, target, disabled, minutes, '2024-01-01', '2024-07-05', .0012,
                                  active=False, terminal=False)
    cut = px.loc[:'2024-06-01']
    subset = {d:f for d,f in features.items() if d <= cut.index[-1]+pd.Timedelta(days=1)}
    cut_weights, _ = overlay(risk_weights(cut, signals(cut, RiskHoldR2b)), subset)
    part, events = minute_simulate(cut, cut_weights, disabled.loc[cut.index],
        {a:f.loc[:'2024-06-01'] for a,f in minutes.items()}, '2024-01-01', '2024-06-01', .0012,
        active=False, terminal=False)
    pd.testing.assert_frame_equal(part, full.loc[:part.index[-1]])
    pd.testing.assert_frame_equal(events, orders.loc[orders.time < pd.Timestamp('2024-06-02', tz='UTC')].reset_index(drop=True))
    if not part[[f'pre_close_units_{a}' for a in ASSETS]].gt(0).any().any():
        raise AssertionError('Prefix must exercise actual held units')
    daily = simulate_spot(px, target, '2024-01-01', '2024-07-05', .0012, terminal=False)
    gap = max_gap(daily, full, list(daily))
    names = ['R29_PROTOCOL.md', 'R29_NATIVE_AVAILABILITY_CLARIFICATION.md', 'R29_FREEZE_GUARD_CLARIFICATION.md', 'r29_cohort_sources.py',
        'r29_sources.py', 'r29_view.py', 'r29_funding.py', 'test_r29_funding.py', 'test_r29_native.py', 'test_r29_view.py',
        'r29_range_sources.py', 'r29_range_audit.py', 'r29_run.py', 'r29_reflection.py', 'r29_plot.py',
        'r29_freeze.py', 'test_r29_freeze.py',
        'r1_r2b_risk.py', 'r9_selection.py', 'r10_gold.py', 'r13_atr.py', 'r8_crowding.py',
        'r20_audit.py', 'r21_basis.py', 'r19_run.py', 'r19_reflection.py',
        'r29_range_audit.json', 'r29_strict_view_contract_failure.json',
        'r29_source_audit.json', 'r29_native_feature_validation.json']
    verification = dict(source_inputs_sha256=hashes,
        source_code_sha256={n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in names},
        baseline_max_abs_gap=gaps, candidate_daily_minute_prefix_gap=gap,
        actual_native_version_and_observation_prefix_equal=True,
        exact_weight_prefix_equal=True, real_held_execution_and_native_order_prefix_equal=True,
        ATR_disabled=True, sole_change='Strict positive same-date official SOFR minus EFFR vetoes original jointly sized BTC/ETH targets',
        historical_first_public_receipt_certified=False, no_companion_rescale=True, no_rule_tuning=True)
    audit.to_csv(HERE/'r29_decision_audit.csv', index=False)
    target.to_csv(HERE/'r29_weight_audit.csv')
    frozen_json(HERE/'r29_input_manifest.json', verification)
    return px, base, target, disabled, minutes, verification


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    fixed = None
    if not args.verify_only:
        fixed = assert_frozen_inputs(HERE)
    px, base, target, disabled, minutes, verification = prepare()
    if args.verify_only:
        print('Native same-date official rates, six frozen controls and actual held/order prefixes PASS')
        return
    # Check again after regeneration, before any full alpha outcome is produced.
    assert_frozen_inputs(HERE, fixed)
    results = {}
    for label, start, end in WINDOWS:
        for cost in (.0012, .0024):
            for name, weights in [('Cov10_baseline', base), ('official_funding_spread', target)]:
                curve = simulate_spot(px, weights, start, end, cost)
                result = summarize(curve)
                result['max_accounting_gap'] = float(curve.accounting_gap.abs().max())
                key = f'{label}_{name}_cost{cost}'
                if label == 'recent_reused':
                    minute, orders = minute_simulate(px, weights, disabled, minutes, start, end, cost, active=False)
                    result['daily_minute_core_max_gap'] = max_gap(curve, minute, list(curve))
                    result['sampled_minute_account_DD_pct'] = float(minute.sampled_minute_running_dd_pct.iloc[-1])
                    result['necessary_screen_gate'] &= result['sampled_minute_account_DD_pct'] >= -5
                    curve = minute
                    orders.to_csv(HERE/f'r29_{key}_orders.csv', index=False)
                result['risk_limitation'] = 'Daily account CLOSE; recent synchronized minute OPEN/CLOSE. No earlier intraday, quote/depth/lot, original publication receipt, actual bot or independent forward certification.'
                results[key] = result
                curve.to_csv(HERE/f'r29_{key}_daily.csv')
                print(key, {k:round(result[k], 4) for k in ('CAGR_pct', 'daily_MTM_DD_pct', 'fees_USDT_10000_start')},
                      'minute_DD', result.get('sampled_minute_account_DD_pct'), 'gate', result['necessary_screen_gate'], flush=True)
    candidate = {k:r for k,r in results.items() if '_official_funding_spread_' in k}
    verdict = dict(all_base_windows_pass=all(r['necessary_screen_gate'] for k,r in candidate.items() if k.endswith('0.0012')),
        aggregate_doubled_cost_profit_USDT_10000_per_window=sum(r['return_pct']*100 for k,r in candidate.items() if k.endswith('0.0024')),
        adopted=False, no_sign_threshold_date_window_latency_risk_fee_or_missingness_rescue=True)
    verdict['aggregate_doubled_cost_net_profit_positive'] = verdict['aggregate_doubled_cost_profit_USDT_10000_per_window'] > 0
    frozen_json(HERE/'r29_results.json', dict(input_manifest=verification, results=results, verdict=verdict,
        scope='One preregistered official overnight-rate sign gate; spot only, two reused windows, fixed costs. Original publication receipt remains an assumption; no adoption or bot activation.'))


if __name__ == '__main__':
    main()
