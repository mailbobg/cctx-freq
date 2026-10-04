"""Freeze native rate/version joins and unchanged accounting before outcomes."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from r33_audit import source_inputs
from r33_quantity import ASSETS, overlay
from r33_freeze import assert_frozen_inputs
from r20_audit import retained
from r1_r2b_risk import signals, risk_weights, RiskHoldR2b
from r9_selection import load_prices
from r10_gold import simulate_spot, WINDOWS
from r13_atr import simulate as minute_simulate
from r8_crowding import summarize
from r19_run import frozen_json, max_gap

HERE = Path(__file__).resolve().parent


def feature_inputs():
    return source_inputs()


def prepare():
    features, hashes = feature_inputs()
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
    names = ['R33_PROTOCOL.md','r33_calendar.py','r33_sources.py','r33_quantity.py',
        'r33_audit.py','r33_run.py','r33_reflection.py','r33_freeze.py',
        'test_r33_freeze.py','test_r33_calendar.py','test_r33_quantity.py']
    code = {n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in names}
    root = HERE.parents[2]
    for module in list(sys.modules.values()):
        file = getattr(module, '__file__', None)
        if not file: continue
        path = Path(file).resolve()
        if path.suffix == '.py' and path.is_file() and path.is_relative_to(root) and '.venv' not in path.parts:
            code[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    for label, _, _ in WINDOWS:
        for cost in (.0012, .0024):
            for prefix in (['r10','r13'] if label=='recent_reused' else ['r10']):
                name=f'{prefix}_{label}_Cov10_baseline_cost{cost}_daily.csv'
                hashes[name]=hashlib.sha256((HERE/name).read_bytes()).hexdigest()
    verification = dict(source_inputs_sha256=hashes,
        source_code_sha256=code,
        baseline_max_abs_gap=gaps, candidate_daily_minute_prefix_gap=gap,
        actual_native_version_and_observation_prefix_equal=True,
        exact_weight_prefix_equal=True, real_held_execution_and_native_order_prefix_equal=True,
        ATR_disabled=True, sole_change='Strict contraction of current original-report weekly average versus exact four-week original-report average vetoes joint BTC/ETH targets',
        historical_first_public_receipt_certified=False, no_companion_rescale=True, no_rule_tuning=True)
    audit.to_csv(HERE/'r33_decision_audit.csv', index=False)
    target.to_csv(HERE/'r33_weight_audit.csv')
    frozen_json(HERE/'r33_input_manifest.json', verification)
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
        print('Native original-report quantities, six frozen controls and actual held/order prefixes PASS')
        return
    # Check again after regeneration, before any full alpha outcome is produced.
    assert_frozen_inputs(HERE, fixed)
    results = {}
    for label, start, end in WINDOWS:
        for cost in (.0012, .0024):
            for name, weights in [('Cov10_baseline', base), ('reserve_quantity', target)]:
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
                    orders.to_csv(HERE/f'r33_{key}_orders.csv', index=False)
                result['risk_limitation'] = 'Daily account CLOSE; recent synchronized minute OPEN/CLOSE. No earlier intraday, quote/depth/lot, original publication receipt, actual bot or independent forward certification.'
                results[key] = result
                curve.to_csv(HERE/f'r33_{key}_daily.csv')
                print(key, {k:round(result[k], 4) for k in ('CAGR_pct', 'daily_MTM_DD_pct', 'fees_USDT_10000_start')},
                      'minute_DD', result.get('sampled_minute_account_DD_pct'), 'gate', result['necessary_screen_gate'], flush=True)
    candidate = {k:r for k,r in results.items() if '_reserve_quantity_' in k}
    verdict = dict(all_base_windows_pass=all(r['necessary_screen_gate'] for k,r in candidate.items() if k.endswith('0.0012')),
        aggregate_doubled_cost_profit_USDT_10000_per_window=sum(r['return_pct']*100 for k,r in candidate.items() if k.endswith('0.0024')),
        adopted=False, no_sign_threshold_date_window_latency_risk_fee_or_missingness_rescue=True)
    verdict['aggregate_doubled_cost_net_profit_positive'] = verdict['aggregate_doubled_cost_profit_USDT_10000_per_window'] > 0
    frozen_json(HERE/'r33_results.json', dict(input_manifest=verification, results=results, verdict=verdict,
        scope='One preregistered asynchronous original-report reserve quantity contraction gate; spot only, two reused windows, fixed costs. Original publication receipt remains an assumption; no adoption or bot activation.'))


if __name__ == '__main__':
    main()
