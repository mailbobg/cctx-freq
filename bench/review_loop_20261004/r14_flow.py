"""One frozen positive aggressive-buy confirmation, after unchanged joint risk sizing."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from r14_data import decimal_value, exact_precision
from r1_r2b_risk import ASSETS, RiskHoldR2b, risk_weights, signals
from r9_selection import load_prices
from r10_gold import simulate_spot, WINDOWS
from r13_atr import simulate as simulate_minutes
from r8_crowding import summarize

HERE = Path(__file__).resolve().parent


def flow_features(frame):
    if not frame.index.is_unique or not frame.index.is_monotonic_increasing or str(frame.index.tz) != 'UTC':
        raise ValueError('Unreviewed UTC daily feature calendar')
    if not frame.index.equals(frame.index.normalize()):
        raise ValueError('Feature timestamps must be UTC daily OPEN labels')
    frame = frame.asfreq('D')
    known = frame[['quote_volume','buy_quote_volume','available_at']].notna().all(axis=1)
    total, buy = [], []
    for day, row in frame.iterrows():
        if not known.loc[day]:
            total.append(None); buy.append(None); continue
        if row.available_at != day+pd.Timedelta(days=1):
            raise ValueError('Unknown candle completion time')
        t, b = decimal_value(row.quote_volume), decimal_value(row.buy_quote_volume)
        if t <= 0 or not 0 <= b <= t: raise ValueError('Invalid decimal trade-side volume')
        total.append(t); buy.append(b)
    rows = []
    for i, day in enumerate(frame.index):
        count = int(known.iloc[max(0, i-29):i+1].sum())
        complete = i >= 29 and count == 30
        numerator, denominator, imbalance, positive = None, None, np.nan, False
        if complete:
            t, b = total[i-29:i+1], buy[i-29:i+1]
            with localcontext() as ctx:
                ctx.prec = exact_precision(t+b)
                denominator = sum(t, Decimal(0)); numerator = 2*sum(b, Decimal(0))-denominator
                imbalance = float(numerator/denominator); positive = numerator > 0
                numerator, denominator = str(numerator), str(denominator)
        rows.append((day, day+pd.Timedelta(days=1), count, bool(complete), numerator, denominator, imbalance, bool(positive)))
    return pd.DataFrame(rows, columns=['date','available_at','count','known','numerator_decimal',
        'denominator_decimal','imbalance','positive']).set_index('date')


def overlay(base, flows):
    if list(base) != ASSETS or set(flows) != set(ASSETS): raise ValueError('Unexpected asset identities')
    if not base.index.is_unique or not base.index.is_monotonic_increasing: raise ValueError('Unreviewed base calendar')
    out, rows = base.copy()*0., []
    for asset in ASSETS:
        features = flow_features(flows[asset]).reindex(base.index)
        allow = features.known.eq(True) & features.positive.eq(True)
        out.loc[allow, asset] = base.loc[allow, asset]
        for day in base.index[base.index.weekday == 6]:
            row = features.loc[day]
            rows.append(dict(decision=day+pd.Timedelta(days=1), feature_day=day, asset=asset,
                available_at=row.available_at, count=0 if pd.isna(row['count']) else int(row['count']),
                known=bool(row.known) if pd.notna(row.known) else False,
                positive=bool(row.positive == True) if pd.notna(row.positive) else False,
                imbalance=row.imbalance, numerator_decimal=row.numerator_decimal,
                denominator_decimal=row.denominator_decimal,
                baseline_weight=float(base.loc[day,asset]), target_weight=float(out.loc[day,asset])))
    audit = pd.DataFrame(rows).sort_values(['feature_day','asset']).reset_index(drop=True)
    return out, audit


def max_gap(curve, prior, columns):
    if not curve.index.as_unit('ns').equals(prior.index.as_unit('ns')): raise ValueError('Control execution calendar changed')
    gap = float(np.abs(curve[columns].to_numpy()-prior[columns].to_numpy()).max())
    if gap > 1e-12: raise ValueError('Frozen control/ledger parity failed')
    return gap


def prepare():
    px, spot, hashes = load_prices()
    flow_manifest_path, minute_manifest_path = HERE/'r14_data/manifest.json', HERE/'r13_data/manifest.json'
    fm, mm = json.loads(flow_manifest_path.read_text()), json.loads(minute_manifest_path.read_text())
    flows, minutes = {}, {}
    for asset in ASSETS:
        px[asset+'_high'], px[asset+'_low'] = spot[asset].high, spot[asset].low
        for folder, suffix, manifest, container in [('r14_data','flow',fm,flows), ('r13_data','1m',mm,minutes)]:
            path = HERE/f'{folder}/{asset}USDT_{suffix}.parquet'; sha = hashlib.sha256(path.read_bytes()).hexdigest()
            if sha != manifest['output_sha256'][path.name]: raise ValueError('Frozen flow/minute source changed')
            hashes[str(path.relative_to(HERE))] = sha; container[asset] = pd.read_parquet(path)
    base = risk_weights(px, signals(px, RiskHoldR2b)); target, audit = overlay(base, flows)
    # ATR is disabled. No ATR values or stop rule are used by this trial.
    disabled_atr = pd.DataFrame(np.nan, index=px.index, columns=ASSETS)
    gaps = {}
    for label, start, end in WINDOWS:
        for cost in (.0012,.0024):
            reference = pd.read_csv(HERE/f'r10_{label}_Cov10_baseline_cost{cost}_daily.csv',index_col='date',parse_dates=True)
            control = simulate_spot(px, base, start, end, cost)
            columns = list(control.columns)
            gaps[f'{label}_daily_cost{cost}'] = max_gap(control, reference, columns)
            if label == 'recent_reused':
                mcontrol, _ = simulate_minutes(px, base, disabled_atr, minutes, start, end, cost, active=False)
                oldminute = pd.read_csv(HERE/f'r13_{label}_Cov10_baseline_cost{cost}_daily.csv',index_col='date',parse_dates=True)
                numeric = [c for c in mcontrol if not c.startswith('pending_')]
                gaps[f'{label}_minute_cost{cost}'] = max_gap(mcontrol, oldminute, numeric)
    for cutoff in ('2021-12-31','2024-12-31'):
        prefix_px = px.loc[:cutoff]; prefix_flows = {a:f.loc[:cutoff] for a,f in flows.items()}
        shorter, _ = overlay(risk_weights(prefix_px, signals(prefix_px,RiskHoldR2b)), prefix_flows)
        pd.testing.assert_frame_equal(shorter, target.loc[:cutoff])
        for a in ASSETS:
            pd.testing.assert_frame_equal(flow_features(prefix_flows[a]), flow_features(flows[a]).loc[:cutoff])
    # Real positions, weekly full exits, and original trade events must all be causal.
    full, full_orders = simulate_minutes(px,target,disabled_atr,minutes,'2024-01-01','2024-07-05',.0012,active=False,terminal=False)
    shortpx = px.loc[:'2024-06-01']
    short, orders = simulate_minutes(shortpx,target.loc[shortpx.index],disabled_atr.loc[shortpx.index],
        {a:f.loc[:'2024-06-01'] for a,f in minutes.items()},'2024-01-01','2024-06-01',.0012,active=False,terminal=False)
    pd.testing.assert_frame_equal(short, full.loc[short.index])
    pd.testing.assert_frame_equal(orders,full_orders.loc[full_orders.time<pd.Timestamp('2024-06-02',tz='UTC')].reset_index(drop=True))
    if not short[[f'pre_close_units_{a}' for a in ASSETS]].gt(0).any().any():
        raise AssertionError('Prefix verification must exercise actual held positions')
    daily = simulate_spot(px,target,'2024-01-01','2024-07-05',.0012,terminal=False)
    candidate_gap = max_gap(daily, full, list(daily.columns))
    names = ['R14_PROTOCOL.md','r14_data.py','test_r14_data.py','r14_flow.py','test_r14_flow.py',
             'r1_r2b_risk.py','r9_selection.py','r10_gold.py','r13_atr.py','r8_crowding.py']
    verification = dict(source_inputs_sha256=hashes, source_code_sha256={n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in names},
        flow_manifest_sha256=hashlib.sha256(flow_manifest_path.read_bytes()).hexdigest(),
        minute_manifest_sha256=hashlib.sha256(minute_manifest_path.read_bytes()).hexdigest(),
        baseline_max_abs_gap=gaps, candidate_daily_minute_prefix_gap=candidate_gap,
        exact_feature_weight_prefix_equal=True, real_held_execution_order_prefix_equal=True,
        current_bar_excluded=True, companion_not_rescaled=True, ATR_disabled=True)
    path = HERE/'r14_input_manifest.json'
    if path.exists() and json.loads(path.read_text()) != verification: raise ValueError('Frozen R14 verification changed')
    path.write_text(json.dumps(verification,indent=2)+'\n')
    return px,spot,base,target,audit,disabled_atr,minutes,verification


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--verify-only',action='store_true'); args=parser.parse_args()
    px,spot,base,target,audit,disabled_atr,minutes,verification = prepare()
    if args.verify_only:
        print('Exact flow/weight prefixes, actual held execution/orders, six frozen control curves and daily/minute parity PASS'); return
    results = {}
    audit.to_csv(HERE/'r14_decision_audit.csv',index=False); target.to_csv(HERE/'r14_weight_audit.csv')
    for label,start,end in WINDOWS:
        for cost in (.0012,.0024):
            for name,weights in [('Cov10_baseline',base),('buy_confirmation',target)]:
                curve = simulate_spot(px,weights,start,end,cost)
                result = summarize(curve)
                result['max_accounting_gap'] = float(curve.accounting_gap.abs().max())
                key = f'{label}_{name}_cost{cost}'
                if label == 'recent_reused':
                    mcurve,orders = simulate_minutes(px,weights,disabled_atr,minutes,start,end,cost,active=False)
                    result['daily_minute_core_max_gap'] = max_gap(curve,mcurve,list(curve.columns))
                    result['sampled_minute_account_DD_pct'] = float(mcurve.sampled_minute_running_dd_pct.iloc[-1])
                    result['necessary_screen_gate'] &= result['sampled_minute_account_DD_pct'] >= -5
                    curve = mcurve; orders.to_csv(HERE/f'r14_{key}_orders.csv',index=False)
                result['risk_limitation'] = 'Daily account CLOSE risk; recent synchronized minute OPEN before/after orders and CLOSE are observed. No early intraday risk, tick quote/depth, actual Freqtrade or independent forward certification.'
                results[key] = result; curve.to_csv(HERE/f'r14_{key}_daily.csv')
                print(key,{k:round(result[k],4) for k in ('CAGR_pct','daily_MTM_DD_pct','fees_USDT_10000_start')},
                    'minute_DD',result.get('sampled_minute_account_DD_pct'),'gate',result['necessary_screen_gate'],flush=True)
    candidate = {k:v for k,v in results.items() if '_buy_confirmation_' in k}
    verdict = dict(all_base_windows_pass=all(v['necessary_screen_gate'] for k,v in candidate.items() if k.endswith('0.0012')),
        all_doubled_cost_terminal_profits_positive=all(v['return_pct']>0 for k,v in candidate.items() if k.endswith('0.0024')),
        adopted=False, no_parameter_sign_or_execution_rescue=True)
    (HERE/'r14_results.json').write_text(json.dumps(dict(captured_utc=datetime.now(timezone.utc).isoformat(),
        input_manifest=verification, results=results, verdict=verdict,
        scope='Single preregistered positive30-day aggressive-buy veto applied after Cov10. Two reused windows, fixed costs, no rule tuning, class, bot or activation.'),indent=2)+'\n')


if __name__ == '__main__': main()
