"""Frozen archived-term experiment, reference parity first and outcomes second."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from r19_sources import collect, full_targets, DATA
from r19_term import parse_archive, parse_counterpart, decision_feature, overlay, ASSETS
from r1_r2b_risk import signals, risk_weights, RiskHoldR2b
from r9_selection import load_prices
from r10_gold import simulate_spot, WINDOWS
from r13_atr import simulate as minute_simulate
from r8_crowding import summarize

HERE = Path(__file__).resolve().parent


def json_text(value):
    return json.dumps(value, indent=2, sort_keys=True, default=lambda x:x.isoformat()) + '\n'


def frozen_json(path, value):
    text = json_text(value)
    if path.exists() and path.read_text() != text: raise ValueError('Frozen artifact changed ' + path.name)
    path.write_text(text)


def max_gap(curve, prior, columns):
    if not curve.index.as_unit('ns').equals(prior.index.as_unit('ns')): raise ValueError('Reference execution calendar changed')
    gap = float(np.abs(curve[columns].to_numpy()-prior[columns].to_numpy()).max())
    if gap > 1e-12: raise ValueError('Reference/ledger parity failed')
    return gap


def feature_inputs():
    original = collect(full_targets())
    manifest = json.loads((DATA/'manifest.json').read_text())['sources']
    features, hashes, counters, rows, selected = {}, {}, {}, [], []
    for key,(body,r) in original.items():
        if key.startswith('cboe_current'):
            if r['status'] != 200: raise ValueError('Unavailable current counterpart')
            counters['vix3m' if key.endswith('vix3m_lead') else 'vix'] = parse_counterpart(body, required_start='2021-01-01')
        elif key.startswith('alfred_'):
            if r['status'] != 200: raise ValueError('Unavailable primary archive documentation')
            if key.endswith('identity') and (b'Chicago Board Options Exchange' not in body or b'Daily, Close' not in body):
                raise ValueError('Source identity/frequency changed')
    for label,start,end in WINDOWS:
        for monday in pd.date_range(start,end,freq='W-MON',tz='UTC'):
            vintage = str((monday-pd.Timedelta(days=2)).date()); key = 'archive_'+vintage
            body,r = original[key]; used=key
            if r['status'] != 200:
                retries = [(k,q) for k,q in sorted(manifest.items()) if k.startswith(key+'_retry_') and q['url']==r['url'] and q['status']==200]
                if not retries: raise ValueError('Archive HTTP/transport error is not a cash feature '+key)
                used,r = retries[0]; body=(DATA/r['path']).read_bytes()
            if not body or len(body) != r['bytes'] or hashlib.sha256(body).hexdigest() != r['sha256']:
                raise ValueError('Archive source identity/body changed')
            f=parse_archive(body,vintage)
            feature=decision_feature(monday,f,vintage,r['sha256'],pd.Timestamp(r['retrieved_utc']))
            feature['source_record']=used; feature['window']=label; features[monday]=feature
            selected.append(dict(source_record=used, vintage=vintage, full_body_observation_rows=len(f),
                first_observation_label=str(f.index.min().date()), last_observation_label=str(f.index.max().date())))
            for name,column in [('vix','vix_decimal'),('vix3m','vix3m_decimal')]:
                archived=feature[column]; friday=feature['observation_day']; counterpart=counters[name]
                current=counterpart.loc[friday,'close_decimal'] if friday in counterpart.index else None
                equal=archived is not None and current is not None and Decimal(archived)==Decimal(current)
                rows.append(dict(decision=str(monday), series=name, friday=str(friday.date()),
                    archived_decimal=archived, current_decimal=current, both_present=archived is not None and current is not None,
                    native_equal=bool(equal), current_never_overwrites_archive=True))
    for key,r in manifest.items():
        if r.get('path'):
            body=(DATA/r['path']).read_bytes()
            if len(body)!=r['bytes'] or hashlib.sha256(body).hexdigest()!=r['sha256']:
                raise ValueError('Retained raw response changed')
            hashes[str((DATA/r['path']).relative_to(HERE))]=r['sha256']
    audit=dict(source_stage_only=True,new_performance=False,total_http_attempts=len(manifest),
        successful_http_200=sum(r['status']==200 for r in manifest.values()),
        requested_mondays=len(features), known_friday_pairs=sum(f['known'] for f in features.values()),
        unknown_friday_pairs=sum(not f['known'] for f in features.values()),
        source_records=selected, counterpart_comparison=rows,
        ancient_counterpart_ohlc_errors=counters['vix'].attrs['outside_required_ohlc_errors'],
        current_counterpart_errors_in_required_window=0,
        archive_version_used_not_current_history=True, range_parameters_not_assumed_respected=True,
        exact_publication_utc_unavailable=True, date_only_conservative_availability='Saturday UTC midnight + two days',
        missing_friday_never_carried_from_thursday=True, no_bot_or_cohort_change=True)
    frozen_json(HERE/'r19_source_audit.json',audit)
    return features,hashes


def prepare():
    features,hashes=feature_inputs()
    px,spot,old_hashes=load_prices(); hashes.update(old_hashes)
    for a in ASSETS: px[a+'_high'],px[a+'_low']=spot[a].high,spot[a].low
    base=risk_weights(px,signals(px,RiskHoldR2b)); target,audit=overlay(base,features)
    disabled=pd.DataFrame(np.nan,index=px.index,columns=ASSETS)
    mm=json.loads((HERE/'r13_data/manifest.json').read_text()); minutes={}
    for a in ASSETS:
        path=HERE/f'r13_data/{a}USDT_1m.parquet'; sha=hashlib.sha256(path.read_bytes()).hexdigest()
        if sha!=mm['output_sha256'][path.name]: raise ValueError('Frozen minute source changed')
        hashes[str(path.relative_to(HERE))]=sha; minutes[a]=pd.read_parquet(path)
    gaps={}
    for label,start,end in WINDOWS:
        for cost in (.0012,.0024):
            control=simulate_spot(px,base,start,end,cost)
            prior=pd.read_csv(HERE/f'r10_{label}_Cov10_baseline_cost{cost}_daily.csv',index_col='date',parse_dates=True)
            gaps[f'{label}_daily_cost{cost}']=max_gap(control,prior,list(control))
            if label=='recent_reused':
                m,_=minute_simulate(px,base,disabled,minutes,start,end,cost,active=False)
                prior_m=pd.read_csv(HERE/f'r13_{label}_Cov10_baseline_cost{cost}_daily.csv',index_col='date',parse_dates=True)
                columns=[c for c in m if not c.startswith('pending_')]
                gaps[f'{label}_minute_cost{cost}']=max_gap(m,prior_m,columns)
    for cutoff in ('2021-12-31','2024-12-31'):
        short=px.loc[:cutoff]; prefix_base=risk_weights(short,signals(short,RiskHoldR2b))
        prefix_features={d:f for d,f in features.items() if d<=short.index[-1]+pd.Timedelta(days=1)}
        prefix_target,_=overlay(prefix_base,prefix_features)
        pd.testing.assert_frame_equal(prefix_target,target.loc[:cutoff])
    # The source/weight prefix is independent of subsequent archive and bars.
    full,orders=minute_simulate(px,target,disabled,minutes,'2024-01-01','2024-07-05',.0012,active=False,terminal=False)
    cut=px.loc[:'2024-06-01']; subset={d:f for d,f in features.items() if d<=cut.index[-1]+pd.Timedelta(days=1)}
    short_target,_=overlay(risk_weights(cut,signals(cut,RiskHoldR2b)),subset)
    part,events=minute_simulate(cut,short_target,disabled.loc[cut.index],{a:f.loc[:'2024-06-01'] for a,f in minutes.items()},
        '2024-01-01','2024-06-01',.0012,active=False,terminal=False)
    pd.testing.assert_frame_equal(part,full.loc[part.index])
    pd.testing.assert_frame_equal(events,orders.loc[orders.time<pd.Timestamp('2024-06-02',tz='UTC')].reset_index(drop=True))
    if not part[[f'pre_close_units_{a}' for a in ASSETS]].gt(0).any().any(): raise AssertionError('Prefix must exercise real holdings')
    daily=simulate_spot(px,target,'2024-01-01','2024-07-05',.0012,terminal=False)
    candidate_gap=max_gap(daily,full,list(daily))
    names=['R19_SOURCE_PLAN.md','R19_PROTOCOL.md','r19_sources.py','r19_term.py','test_r19_term.py','r19_run.py',
        'r1_r2b_risk.py','r9_selection.py','r10_gold.py','r13_atr.py','r8_crowding.py','r19_sources/manifest.json']
    verification=dict(source_inputs_sha256=hashes,source_code_sha256={n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in names},
        baseline_max_abs_gap=gaps,candidate_daily_minute_prefix_gap=candidate_gap,exact_source_weight_prefix_equal=True,
        real_held_execution_and_native_order_prefix_equal=True,ATR_disabled=True,
        sole_change='Archived previous-Friday shared volatility-term veto; exact Saturday archive, two-day date-only latency',
        no_current_history_backdating=True,no_companion_rescale=True,no_rule_tuning=True)
    audit.to_csv(HERE/'r19_decision_audit.csv',index=False);target.to_csv(HERE/'r19_weight_audit.csv')
    frozen_json(HERE/'r19_input_manifest.json',verification)
    return px,base,target,disabled,minutes,verification


def main():
    p=argparse.ArgumentParser();p.add_argument('--verify-only',action='store_true');args=p.parse_args()
    px,base,target,disabled,minutes,verification=prepare()
    if args.verify_only:
        print('Archive identities/clocks/native comparison, six references, exact prefixes and held native-order prefix PASS');return
    results={}
    for label,start,end in WINDOWS:
        for cost in (.0012,.0024):
            for name,weights in [('Cov10_baseline',base),('term_confirmation',target)]:
                curve=simulate_spot(px,weights,start,end,cost);r=summarize(curve)
                r['max_accounting_gap']=float(curve.accounting_gap.abs().max())
                key=f'{label}_{name}_cost{cost}'
                if label=='recent_reused':
                    m,orders=minute_simulate(px,weights,disabled,minutes,start,end,cost,active=False)
                    r['daily_minute_core_max_gap']=max_gap(curve,m,list(curve))
                    r['sampled_minute_account_DD_pct']=float(m.sampled_minute_running_dd_pct.iloc[-1])
                    r['necessary_screen_gate'] &= r['sampled_minute_account_DD_pct']>=-5
                    curve=m;orders.to_csv(HERE/f'r19_{key}_orders.csv',index=False)
                r['risk_limitation']='Daily account CLOSE; recent synchronized minute OPEN before/after orders and CLOSE only. No earlier intraday/tick executable quote/depth/lot or actual Freqtrade/independent forward certification.'
                results[key]=r;curve.to_csv(HERE/f'r19_{key}_daily.csv')
                print(key,{k:round(r[k],4) for k in ('CAGR_pct','daily_MTM_DD_pct','fees_USDT_10000_start')},
                    'minute_DD',r.get('sampled_minute_account_DD_pct'),'gate',r['necessary_screen_gate'],flush=True)
    candidates={k:r for k,r in results.items() if '_term_confirmation_' in k}
    verdict=dict(all_base_windows_pass=all(r['necessary_screen_gate'] for k,r in candidates.items() if k.endswith('0.0012')),
        aggregate_doubled_cost_profit_USDT_10000_per_window=sum(r['return_pct']*100 for k,r in candidates.items() if k.endswith('0.0024')),
        adopted=False,no_sign_holiday_latency_sizing_or_universe_rescue=True)
    verdict['aggregate_doubled_cost_net_profit_positive']=verdict['aggregate_doubled_cost_profit_USDT_10000_per_window']>0
    frozen_json(HERE/'r19_results.json',dict(input_manifest=verification,results=results,verdict=verdict,
        scope='Single preregistered archived-term confirmation. Two reused windows, fixed costs and no class/bot/activation. Source vintage has date-only conservative latency; weekly Friday knowledge only.'))


if __name__=='__main__': main()
