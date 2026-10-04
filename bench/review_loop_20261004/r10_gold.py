"""Frozen gold-sleeve test with explicit units and a matched spot cash ledger."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from r1_r2b_risk import signals, risk_weights, RiskHoldR2b, weekly_difference
from portfolio_risk import cap_weights
from r8_crowding import summarize
from r9_selection import load_prices

HERE = Path(__file__).resolve().parent
ASSETS = ['BTC', 'ETH', 'PAXG']
WINDOWS = [('early_reused', '2021-02-01', '2023-12-31'), ('recent_reused', '2024-01-01', '2026-08-31')]


def gold_weights(px):
    px = px.sort_index().asfreq('D')
    raw = signals(px, RiskHoldR2b)
    raw['PAXG'] = .5
    closes = px[[a+'_close' for a in ASSETS]].set_axis(ASSETS, axis=1)
    cov = np.log(closes).diff().rolling(30).cov()*365
    out = raw*0
    for day in px.index:
        out.loc[day] = cap_weights(raw.loc[day].to_numpy(), cov.loc[day].reindex(index=ASSETS, columns=ASSETS).to_numpy(), .10, 1.)
    return out


def simulate_spot(px, weights, start, end, cost, terminal=True):
    if not px.index.equals(weights.index):
        raise ValueError('Price and target execution calendars disagree')
    assets = list(weights.columns)
    costs = np.array([cost[a] for a in assets]) if isinstance(cost, dict) else np.full(len(assets), cost)
    if not np.isfinite(costs).all() or (costs < 0).any():
        raise ValueError('Invalid cost allowance')
    cash, units, average = 1., np.zeros(len(assets)), np.zeros(len(assets))
    realized, total_cost, rows = 0., 0., []
    for i, day in enumerate(px.index):
        if not (start <= str(day.date()) <= end) or i < 200:
            continue
        opened = px.loc[day, [a+'_open' for a in assets]].to_numpy(float)
        closed = px.loc[day, [a+'_close' for a in assets]].to_numpy(float)
        if not np.isfinite(np.r_[opened, closed]).all() or np.min(np.r_[opened, closed]) <= 0:
            raise ValueError('Unknown or nonpositive execution price')
        fees, turnover = 0., 0.
        asset_turnover = np.zeros(len(assets))
        before_open = cash+units@opened
        if day.weekday() == 0:
            target = weights.iloc[i-1].to_numpy(float)
            if not np.isfinite(target).all() or target.min() < 0 or target.sum() > 1+1e-12:
                raise ValueError('Invalid long-only target')
            diff = weekly_difference(units*opened, target, before_open)
            for k in np.argsort(diff):
                amount = max(diff[k], -units[k]*opened[k])
                if amount > 0:
                    amount = min(amount, max(0., cash)/(1+costs[k]))
                quantity = amount/opened[k]
                if quantity > 0:
                    average[k] = (average[k]*units[k]+amount)/(units[k]+quantity)
                elif quantity < 0:
                    realized += -quantity*(opened[k]-average[k])
                units[k] += quantity
                if abs(units[k]) < 1e-14:
                    units[k] = 0.
                    average[k] = 0.
                fee = abs(amount)*costs[k]
                cash -= amount+fee
                fees += fee
                turnover += abs(amount)
                asset_turnover[k] += abs(amount)
        if cash < -1e-10 or units.min() < -1e-10:
            raise AssertionError('Spot borrowing or negative units')
        pre_units = units.copy()
        pre_equity = cash+units@closed
        pre_cash = cash
        pre_exposure = (units@closed)/pre_equity
        if terminal and str(day.date()) == end:
            sold = units*closed
            closing_fee = sold@costs
            realized += units@(closed-average)
            cash += sold.sum()-closing_fee
            units[:] = 0.
            average[:] = 0.
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
        row.update({'units_'+a:units[k] for k,a in enumerate(assets)})
        row.update({'pre_close_units_'+a:pre_units[k] for k,a in enumerate(assets)})
        row.update({'turnover_'+a:asset_turnover[k] for k,a in enumerate(assets)})
        rows.append(row)
    result = pd.DataFrame(rows).set_index('date')
    if result.empty or str(result.index[-1].date()) != end:
        raise ValueError('Incomplete requested execution calendar')
    return result


def prepare():
    px, spot, hashes = load_prices()
    manifest = json.loads((HERE/'r10_data/manifest.json').read_text())
    gold_path = HERE/'r10_data/PAXGUSDT_spot.parquet'
    gold_sha = hashlib.sha256(gold_path.read_bytes()).hexdigest()
    if gold_sha != manifest['output_sha256']['PAXGUSDT_spot.parquet']:
        raise ValueError('Gold source hash changed')
    hashes[str(gold_path.relative_to(HERE.parents[2]))] = gold_sha
    gold = pd.read_parquet(gold_path)
    spot['PAXG'] = gold
    px['PAXG_open'], px['PAXG_close'] = gold.open, gold.close
    baseline = risk_weights(px, signals(px, RiskHoldR2b))
    baseline['PAXG'] = 0.
    candidate = gold_weights(px)
    for cutoff in ('2021-12-31','2024-12-31'):
        pd.testing.assert_frame_equal(gold_weights(px.loc[:cutoff]), candidate.loc[:cutoff])
    full = simulate_spot(px,candidate,'2024-01-01','2024-07-05',.0012,terminal=False)
    part = simulate_spot(px.loc[:'2024-06-01'],candidate.loc[:'2024-06-01'],'2024-01-01','2024-06-01',.0012,terminal=False)
    pd.testing.assert_frame_equal(part,full.loc[part.index])
    assert part.pre_close_units_PAXG.gt(0).any()
    gaps = {}
    for label,start,end in WINDOWS:
        for cost in (.0012,.0024):
            curve = simulate_spot(px,baseline,start,end,cost)
            old = pd.read_csv(HERE/f'r8_{label}_Cov10_baseline_cost{cost}_daily.csv',index_col='date',parse_dates=True)
            if not curve.index.as_unit('ns').equals(old.index.as_unit('ns')):
                raise ValueError('Control calendar changed')
            cols = ['equity','cash','exposure','cost','turnover']
            gap = float(np.max(np.abs(curve[cols].to_numpy()-old[cols].to_numpy())))
            if gap > 1e-12:
                raise ValueError('Generalized cash ledger changed two-asset control')
            gaps[f'{label}_cost{cost}'] = gap
    verification = {'source_inputs_sha256':hashes,'baseline_max_abs_csv_gap':gaps,
                    'full_prefix_weights_equal':True,'real_held_execution_prefix_equal':True}
    path = HERE/'r10_input_manifest.json'
    if path.exists() and json.loads(path.read_text()) != verification:
        raise ValueError('Frozen gold input/control checks changed')
    path.write_text(json.dumps(verification,indent=2)+'\n')
    return px,spot,baseline,candidate,verification


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify-only',action='store_true')
    args = parser.parse_args()
    px,spot,baseline,candidate,verification = prepare()
    if args.verify_only:
        print('All two-asset control curves, source hashes, causal weights and held execution prefixes PASS')
        return
    results = {}
    candidate.to_csv(HERE/'r10_weight_audit.csv')
    for label,start,end in WINDOWS:
        for cost_label,cost in [('cost0.0012',.0012),('cost0.0024',.0024),('PAXG_cost2',{'BTC':.0012,'ETH':.0012,'PAXG':.0024})]:
            for name,target in [('Cov10_baseline',baseline),('fixed_gold',candidate)]:
                curve = simulate_spot(px,target,start,end,cost)
                low = sum(curve['pre_close_units_'+a]*spot[a].low.reindex(curve.index) for a in ASSETS)
                curve['held_low_equity_envelope'] = np.minimum(curve.pre_close_cash+low,curve.equity)
                r = summarize(curve)
                activity = {}
                for asset in ASSETS:
                    days = curve.index[curve['turnover_'+asset].gt(1e-12)]
                    actual = spot[asset].reindex(days)
                    if len(actual) and (actual[['trades','quote_volume']].le(0).any().any() or actual[['trades','quote_volume']].isna().any().any()):
                        raise ValueError('Modeled trade without actual source trading activity')
                    participation = curve.loc[days,'turnover_'+asset]*10000/actual.quote_volume*100
                    activity[asset] = {'modeled_trade_days':len(days),
                                       'max_daily_quote_participation_pct_at_10000_start':float(participation.max()) if len(days) else 0.}
                peak = curve.pre_close_equity.cummax().shift(1).fillna(1.).clip(lower=1.)
                r.update(daily_multiasset_low_envelope_vs_prior_close_peak_pct=float((curve.held_low_equity_envelope/peak-1).min()*100),
                         max_accounting_gap=float(curve.accounting_gap.abs().max()),
                         realized_price_profit_USDT_10000_start=float(curve.realized_price_pnl.iloc[-1]*10000),
                         mean_gold_weight_pct=float((curve.pre_close_units_PAXG*px.PAXG_close.reindex(curve.index)/curve.pre_close_equity).mean()*100),
                         activity_diagnostics=activity,
                         risk_limitation='Weighted sum of actual held-asset daily lows; extremes need not coincide. Prior CLOSE peaks are observed. Monday before-order OPEN separately captured, actual quotes/fills/intraday path not certified.')
                key = f'{label}_{name}_{cost_label}'
                results[key] = r
                curve.to_csv(HERE/f'r10_{key}_daily.csv')
                print(key,{k:round(r[k],4) for k in ('CAGR_pct','daily_MTM_DD_pct','fees_USDT_10000_start')},'gate',r['necessary_screen_gate'],flush=True)
    source_names = ['R10_PROTOCOL.md','r10_gold.py','r10_data.py','test_r10_gold.py','r1_r2b_risk.py','r8_crowding.py','r9_selection.py']
    sources = {name:hashlib.sha256((HERE/name).read_bytes()).hexdigest() for name in source_names}
    (HERE/'r10_results.json').write_text(json.dumps({'captured_utc':datetime.now(timezone.utc).isoformat(),
        'source_sha256':sources,'input_manifest':verification,'results':results,
        'limitations':'Preregistered fixed gold sleeve, no weight/filter/universe/cost sweep. Reused history, modeled OPEN fills, no cash yield or redemption. No actual Freqtrade/intraday/forward certification or activation.'},indent=2)+'\n')


if __name__ == '__main__':
    main()
