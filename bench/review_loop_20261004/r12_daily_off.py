"""Frozen scheduling trial: original off exits daily, additions stay weekly."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from r1_r2b_risk import ASSETS, RiskHoldR2b, risk_weights, signals, weekly_difference
from r8_crowding import summarize
from r9_selection import load_prices

HERE = Path(__file__).resolve().parent
WINDOWS = [('early_reused', '2021-02-01', '2023-12-31'), ('recent_reused', '2024-01-01', '2026-08-31')]


def simulate(px, weights, momentum, start, end, cost, daily_exit=True, terminal=True):
    if not px.index.equals(weights.index) or not px.index.equals(momentum.index):
        raise ValueError('Price, target and signal calendar disagree')
    if list(weights.columns) != ASSETS or list(momentum.columns) != ASSETS:
        raise ValueError('Unexpected signal asset order')
    if not np.isfinite(cost) or cost < 0:
        raise ValueError('Invalid cost allowance')
    cash, units, average = 1., np.zeros(2), np.zeros(2)
    realized, total_cost, rows = 0., 0., []
    for i, day in enumerate(px.index):
        if not (start <= str(day.date()) <= end) or i < 200:
            continue
        opened = px.loc[day, [a+'_open' for a in ASSETS]].to_numpy(float)
        closed = px.loc[day, [a+'_close' for a in ASSETS]].to_numpy(float)
        if not np.isfinite(np.r_[opened, closed]).all() or np.min(np.r_[opened, closed]) <= 0:
            raise ValueError('Unknown or nonpositive execution price')
        fees, turnover = 0., 0.
        asset_turnover = np.zeros(2)
        before_open = cash+units@opened
        current = units*opened
        diff = np.zeros(2)
        if day.weekday() == 0:
            target = weights.iloc[i-1].to_numpy(float)
            if not np.isfinite(target).all() or target.min() < 0 or target.sum() > 1+1e-12:
                raise ValueError('Invalid long-only target')
            diff = weekly_difference(current, target, before_open)
        exited = np.zeros(2, dtype=bool)
        if daily_exit:
            prior = momentum.iloc[i-1].to_numpy(float)
            if not np.isfinite(prior).all():
                raise ValueError('Unknown prior momentum')
            off = prior <= 0
            exited = off & (units > 1e-14)
            diff[off] = -current[off]
        for k in np.argsort(diff):
            amount = max(diff[k], -current[k])
            if amount > 0:
                amount = min(amount, max(0., cash)/(1+cost))
            quantity = amount/opened[k]
            if quantity > 0:
                average[k] = (average[k]*units[k]+amount)/(units[k]+quantity)
            elif quantity < 0:
                realized += -quantity*(opened[k]-average[k])
            units[k] += quantity
            if abs(units[k]) < 1e-14:
                units[k], average[k] = 0., 0.
            fee = abs(amount)*cost
            cash -= amount+fee
            fees += fee
            turnover += abs(amount)
            asset_turnover[k] += abs(amount)
        if cash < -1e-10 or units.min() < -1e-10:
            raise AssertionError('Spot borrowing or negative units')
        pre_units, pre_cash = units.copy(), cash
        pre_equity = cash+units@closed
        pre_exposure = (units@closed)/pre_equity
        if terminal and str(day.date()) == end:
            sold = units*closed
            closing_fee = sold.sum()*cost
            realized += units@(closed-average)
            cash += sold.sum()-closing_fee
            units[:], average[:] = 0., 0.
            fees += closing_fee
            turnover += sold.sum()
            asset_turnover += sold
        total_cost += fees
        equity = cash+units@closed
        unrealized = units@(closed-average)
        gap = equity-(1+realized+unrealized-total_cost)
        if abs(gap) > 1e-10:
            raise AssertionError('Realized/unrealized price profit and cost ledger disagree')
        row = dict(date=day, equity=equity, cash=cash, exposure=(units@closed)/equity, cost=fees, turnover=turnover,
                   pre_close_equity=pre_equity, pre_close_cash=pre_cash, pre_close_exposure=pre_exposure,
                   pre_trade_open_equity=before_open, realized_price_pnl=realized, unrealized_price_pnl=unrealized,
                   cumulative_cost=total_cost, accounting_gap=gap)
        row.update({'units_'+a:units[k] for k,a in enumerate(ASSETS)})
        row.update({'pre_close_units_'+a:pre_units[k] for k,a in enumerate(ASSETS)})
        row.update({'turnover_'+a:asset_turnover[k] for k,a in enumerate(ASSETS)})
        row.update({'daily_off_exit_'+a:bool(exited[k]) for k,a in enumerate(ASSETS)})
        rows.append(row)
    result = pd.DataFrame(rows).set_index('date')
    if result.empty or str(result.index[-1].date()) != end:
        raise ValueError('Incomplete requested execution calendar')
    return result


def prepare():
    px, spot, hashes = load_prices()
    weights = risk_weights(px, signals(px, RiskHoldR2b))
    closes = px[[a+'_close' for a in ASSETS]].set_axis(ASSETS, axis=1)
    momentum = closes/closes.shift(30)-1
    gaps = {}
    for label, start, end in WINDOWS:
        for cost in (.0012, .0024):
            baseline = simulate(px, weights, momentum, start, end, cost, daily_exit=False)
            old = pd.read_csv(HERE/f'r10_{label}_Cov10_baseline_cost{cost}_daily.csv', index_col='date', parse_dates=True)
            if not baseline.index.as_unit('ns').equals(old.index.as_unit('ns')):
                raise ValueError('Control calendar changed')
            columns = [x for x in baseline if not x.startswith('daily_off_exit_')]
            gap = float(np.max(np.abs(baseline[columns].to_numpy()-old[columns].to_numpy())))
            if gap > 1e-12:
                raise ValueError('Daily-exit ledger changed the disabled weekly control')
            gaps[f'{label}_cost{cost}'] = gap
    for cutoff in ('2021-12-31', '2024-12-31'):
        part = px.loc[:cutoff]
        pd.testing.assert_frame_equal(risk_weights(part, signals(part, RiskHoldR2b)), weights.loc[:cutoff])
        part_closes = part[[a+'_close' for a in ASSETS]].set_axis(ASSETS, axis=1)
        pd.testing.assert_frame_equal(part_closes/part_closes.shift(30)-1, momentum.loc[:cutoff])
    full = simulate(px, weights, momentum, '2024-01-01', '2024-07-05', .0012, terminal=False)
    subset = px.loc[:'2024-06-01']
    part = simulate(subset, weights.loc[subset.index], momentum.loc[subset.index], '2024-01-01', '2024-06-01', .0012, terminal=False)
    pd.testing.assert_frame_equal(part, full.loc[part.index])
    assert part.units_BTC.gt(0).any() and part.units_ETH.gt(0).any()
    names = ['R12_PROTOCOL.md', 'r12_daily_off.py', 'test_r12_daily_off.py', 'r1_r2b_risk.py', 'r9_selection.py']
    version = dict(source_inputs_sha256=hashes,
                   source_code_sha256={x:hashlib.sha256((HERE/x).read_bytes()).hexdigest() for x in names},
                   baseline_max_abs_csv_gap=gaps, full_prefix_signals_equal=True, real_held_execution_prefix_equal=True)
    path = HERE/'r12_input_manifest.json'
    if path.exists() and json.loads(path.read_text()) != version:
        raise ValueError('Frozen daily-exit inputs/engine verification changed')
    path.write_text(json.dumps(version, indent=2)+'\n')
    return px, spot, weights, momentum, version


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    px, spot, weights, momentum, version = prepare()
    if args.verify_only:
        print('Four matched controls, causal signals and real held execution prefixes PASS')
        return
    results = {}
    for label, start, end in WINDOWS:
        for cost in (.0012, .0024):
            for name, active in [('Cov10_baseline', False), ('daily_off', True)]:
                curve = simulate(px, weights, momentum, start, end, cost, daily_exit=active)
                low = sum(curve['pre_close_units_'+a]*spot[a].low.reindex(curve.index) for a in ASSETS)
                curve['held_low_equity_envelope'] = np.minimum(curve.pre_close_cash+low, curve.equity)
                off_monday = curve.index.weekday != 0
                for a in ASSETS:
                    if curve['units_'+a].diff().loc[off_monday].dropna().gt(1e-12).any():
                        raise AssertionError('Daily exits created an off-Monday addition')
                prior_peak = curve.pre_close_equity.cummax().shift(1).fillna(1.).clip(lower=1.)
                summary = summarize(curve)
                summary.update(
                    max_accounting_gap=float(curve.accounting_gap.abs().max()),
                    daily_multiasset_low_envelope_vs_prior_close_peak_pct=float((curve.held_low_equity_envelope/prior_peak-1).min()*100),
                    daily_off_exit_counts={a:int(curve['daily_off_exit_'+a].sum()) for a in ASSETS},
                    non_monday_off_exit_counts={a:int(curve.loc[off_monday, 'daily_off_exit_'+a].sum()) for a in ASSETS},
                    risk_limitation='Actual held-asset daily low sum need not be simultaneous. Modeled daily OPEN fills, no exact quotes or intraday execution proof.')
                key = f'{label}_{name}_cost{cost}'
                results[key] = summary
                curve.to_csv(HERE/f'r12_{key}_daily.csv')
                print(key, {k:round(summary[k], 4) for k in ('CAGR_pct', 'daily_MTM_DD_pct', 'fees_USDT_10000_start')}, 'gate', summary['necessary_screen_gate'], flush=True)
    (HERE/'r12_results.json').write_text(json.dumps(dict(captured_utc=datetime.now(timezone.utc).isoformat(),
        input_manifest=version, results=results,
        scope='Preregistered daily original-off exits only; unchanged weekly entries/sizing and reused windows. No parameter, schedule, insurance or filter sweep. No actual Freqtrade/forward certification or bot activation.'), indent=2)+'\n')


if __name__ == '__main__':
    main()
