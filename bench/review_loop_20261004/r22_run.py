"""Fixed external-panel source/cohort controls before one outcome screen."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from r21_basis import parse_native
from r22_participation import ASSETS,feature,overlay
from r22_sources import DATA,PANEL,full_targets,month_targets,selected
from r20_audit import retained
from r17_prices import verified_zip
from r1_r2b_risk import signals,risk_weights,RiskHoldR2b
from r9_selection import load_prices
from r10_gold import simulate_spot,WINDOWS
from r13_atr import simulate as minute_simulate
from r8_crowding import summarize
from r19_run import frozen_json,max_gap

HERE=Path(__file__).resolve().parent


def exact_month(rows,month):
    start=pd.Timestamp(month+'-01',tz='UTC');stop=start+pd.offsets.MonthBegin(1)
    expected=pd.date_range(start,stop-pd.Timedelta(days=1),freq='D')
    if list(rows)!=list(expected):raise ValueError('Native monthly source calendar differs')


def feature_inputs():
    months=pd.date_range('2020-12-01','2026-08-01',freq='MS').strftime('%Y-%m').tolist()
    targets=month_targets(months);bodies=selected(targets)
    records=json.loads((DATA/'manifest.json').read_text())['sources'];originals=full_targets();hashes={}
    for key,record in records.items():
        root=key.split('_retry_',1)[0]
        if root not in originals or record['url']!=originals[root]:raise ValueError('Unregistered original/retry source identity')
        retained(DATA,record)
        if record.get('path'):hashes['r22_sources/'+record['path']]=record['sha256']
    hashes['r22_sources/manifest.json']=hashlib.sha256((DATA/'manifest.json').read_bytes()).hexdigest()
    series={symbol:{} for symbol in PANEL};coverage=[]
    for symbol in PANEL:
        for month in months:
            key='spot_'+symbol+'_'+month;body,record,name=bodies[key]
            check,check_record,check_name=bodies[key+'_checksum'];filename=f'{symbol}-1d-{month}.zip'
            rows=parse_native(verified_zip(body,check,filename));exact_month(rows,month)
            for day,row in rows.items():
                if day in series[symbol]:raise ValueError('Duplicate across native source months')
                row.update(source_symbol=symbol,source_sha256=record['sha256'],source_url=record['url'],
                    source_record=name,current_capture_utc=record['retrieved_utc'])
                series[symbol][day]=row
            coverage.append(dict(symbol=symbol,month=month,native_days=len(rows),selected_zip_record=name,
                selected_checksum_record=check_name,zip_sha256=record['sha256'],checksum_sha256=check_record['sha256'],
                native_epoch_scale=next(iter(rows.values()))['native_epoch_scale']))
        expected=pd.date_range('2020-12-01','2026-08-31',tz='UTC')
        if list(series[symbol])!=list(expected):raise ValueError('Declared full native panel calendar differs')
    features={}
    for label,start,end in WINDOWS:
        for monday in pd.date_range(start,end,freq='W-MON',tz='UTC'):
            f=feature(monday,series);f['window']=label;features[monday]=f
    # Actually cut every source at two historical dates and recompute decisions.
    # Filtering a full feature dictionary alone would not test native causality.
    for cutoff in ('2021-12-31','2024-12-31'):
        end=pd.Timestamp(cutoff,tz='UTC')
        short={s:{d:r for d,r in rows.items() if d<=end} for s,rows in series.items()}
        for day,f in features.items():
            if day<=end+pd.Timedelta(days=1):
                pf=feature(day,short);pf['window']=f['window']
                if pf!=f:raise ValueError('Native feature prefix changed')
    audit=dict(source_stage_only=True,new_performance=False,protocol_commit='67fd65a',panel=list(PANEL),
        original_request_roles=len(originals),retained_attempts=len(records),
        failed_transport_attempts=sum(r['status'] is None for r in records.values()),
        selected_zip_checksum_pairs=len(coverage),native_rows_by_symbol={s:len(rows) for s,rows in series.items()},
        decision_mondays=len(features),native_feature_votes=sum(len(f['native_votes']) for f in features.values()),
        all_members_known=all(f['known'] for f in features.values()),
        source_feature_cohort={str(d):f for d,f in features.items()},native_archive_coverage=coverage,
        source_feature_prefix_equal=True,originals_never_overwritten=True,
        historical_publication_version_certified=False,current_capture_never_backdated=True,
        availability_assumption='Friday interval ends Saturday UTC00 plus one full additional day: assumed Sunday UTC00',
        limitations='Fixed post-history panel, not complete historical market/top-six. Current archive values/corrections and last trade closes do not authenticate first publication, bid/ask or actual fills. OI probe remains a separate untested source lead.')
    frozen_json(HERE/'r22_source_audit.json',audit)
    return features,hashes


def prepare():
    features,hashes=feature_inputs();px,spot,prior_hashes=load_prices();hashes.update(prior_hashes)
    for a in ASSETS:px[a+'_high'],px[a+'_low']=spot[a].high,spot[a].low
    base=risk_weights(px,signals(px,RiskHoldR2b));target,audit=overlay(base,features)
    disabled=pd.DataFrame(np.nan,index=px.index,columns=ASSETS)
    mm=json.loads((HERE/'r13_data/manifest.json').read_text());minutes={}
    for a in ASSETS:
        path=HERE/f'r13_data/{a}USDT_1m.parquet';sha=hashlib.sha256(path.read_bytes()).hexdigest()
        if sha!=mm['output_sha256'][path.name]:raise ValueError('Original native minute source changed')
        hashes[str(path.relative_to(HERE))]=sha;minutes[a]=pd.read_parquet(path)
    gaps={}
    for label,start,end in WINDOWS:
        for cost in (.0012,.0024):
            control=simulate_spot(px,base,start,end,cost)
            prior=pd.read_csv(HERE/f'r10_{label}_Cov10_baseline_cost{cost}_daily.csv',index_col='date',parse_dates=True)
            gaps[f'{label}_daily_cost{cost}']=max_gap(control,prior,list(control))
            if label=='recent_reused':
                m,_=minute_simulate(px,base,disabled,minutes,start,end,cost,active=False)
                prior_m=pd.read_csv(HERE/f'r13_{label}_Cov10_baseline_cost{cost}_daily.csv',index_col='date',parse_dates=True)
                gaps[f'{label}_minute_cost{cost}']=max_gap(m,prior_m,[c for c in m if not c.startswith('pending_')])
    for cutoff in ('2021-12-31','2024-12-31'):
        short=px.loc[:cutoff];prefix_base=risk_weights(short,signals(short,RiskHoldR2b))
        subset={d:f for d,f in features.items() if d<=short.index[-1]+pd.Timedelta(days=1)}
        prefix,_=overlay(prefix_base,subset);pd.testing.assert_frame_equal(prefix,target.loc[:cutoff])
    full,orders=minute_simulate(px,target,disabled,minutes,'2024-01-01','2024-07-05',.0012,active=False,terminal=False)
    cut=px.loc[:'2024-06-01'];subset={d:f for d,f in features.items() if d<=cut.index[-1]+pd.Timedelta(days=1)}
    cut_weights,_=overlay(risk_weights(cut,signals(cut,RiskHoldR2b)),subset)
    part,events=minute_simulate(cut,cut_weights,disabled.loc[cut.index],{a:f.loc[:'2024-06-01'] for a,f in minutes.items()},
        '2024-01-01','2024-06-01',.0012,active=False,terminal=False)
    pd.testing.assert_frame_equal(part,full.loc[:part.index[-1]])
    pd.testing.assert_frame_equal(events,orders.loc[orders.time<pd.Timestamp('2024-06-02',tz='UTC')].reset_index(drop=True))
    if not part[[f'pre_close_units_{a}' for a in ASSETS]].gt(0).any().any():raise AssertionError('Prefix must exercise actual held units')
    daily=simulate_spot(px,target,'2024-01-01','2024-07-05',.0012,terminal=False)
    gap=max_gap(daily,full,list(daily))
    names=['R22_SOURCE_PLAN.md','R22_PROTOCOL.md','r22_sources.py','r22_participation.py','test_r22_participation.py','test_r22_sources.py',
        'r22_run.py','r22_reflection.py','r1_r2b_risk.py','r9_selection.py','r10_gold.py','r13_atr.py',
        'r8_crowding.py','r17_prices.py','r20_audit.py','r21_basis.py','r19_run.py','r19_reflection.py','r22_source_audit.json',
        '../../docs/plans/2026-10-04-external-panel-participation-design.md']
    verification=dict(source_inputs_sha256=hashes,source_code_sha256={n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in names},
        baseline_max_abs_gap=gaps,candidate_daily_minute_prefix_gap=gap,exact_native_feature_and_weight_prefix_equal=True,
        real_held_execution_and_native_order_prefix_equal=True,ATR_disabled=True,
        sole_change='Shared strict majority of six fixed external native Friday 30-day returns, after original joint covariance weights',
        historical_publication_certified=False,no_companion_rescale=True,no_rule_tuning=True)
    audit.to_csv(HERE/'r22_decision_audit.csv',index=False);target.to_csv(HERE/'r22_weight_audit.csv')
    frozen_json(HERE/'r22_input_manifest.json',verification)
    return px,base,target,disabled,minutes,verification


def main():
    p=argparse.ArgumentParser();p.add_argument('--verify-only',action='store_true');args=p.parse_args()
    px,base,target,disabled,minutes,verification=prepare()
    if args.verify_only:
        print('Native panel sources/clocks, six reference controls and real-held/order prefixes PASS');return
    results={}
    for label,start,end in WINDOWS:
        for cost in (.0012,.0024):
            for name,weights in [('Cov10_baseline',base),('participation_confirmation',target)]:
                curve=simulate_spot(px,weights,start,end,cost);r=summarize(curve)
                r['max_accounting_gap']=float(curve.accounting_gap.abs().max());key=f'{label}_{name}_cost{cost}'
                if label=='recent_reused':
                    m,orders=minute_simulate(px,weights,disabled,minutes,start,end,cost,active=False)
                    r['daily_minute_core_max_gap']=max_gap(curve,m,list(curve))
                    r['sampled_minute_account_DD_pct']=float(m.sampled_minute_running_dd_pct.iloc[-1])
                    r['necessary_screen_gate'] &= r['sampled_minute_account_DD_pct']>=-5
                    curve=m;orders.to_csv(HERE/f'r22_{key}_orders.csv',index=False)
                r['risk_limitation']='Daily account CLOSE; recent synchronized minute OPEN/CLOSE. No earlier intraday/tick quote/depth/lot, historical publication version, Freqtrade or independent forward certification.'
                results[key]=r;curve.to_csv(HERE/f'r22_{key}_daily.csv')
                print(key,{k:round(r[k],4) for k in ('CAGR_pct','daily_MTM_DD_pct','fees_USDT_10000_start')},
                    'minute_DD',r.get('sampled_minute_account_DD_pct'),'gate',r['necessary_screen_gate'],flush=True)
    candidate={k:r for k,r in results.items() if '_participation_confirmation_' in k}
    verdict=dict(all_base_windows_pass=all(r['necessary_screen_gate'] for k,r in candidate.items() if k.endswith('0.0012')),
        aggregate_doubled_cost_profit_USDT_10000_per_window=sum(r['return_pct']*100 for k,r in candidate.items() if k.endswith('0.0024')),
        adopted=False,no_sign_horizon_panel_majority_day_latency_sizing_or_fee_rescue=True)
    verdict['aggregate_doubled_cost_net_profit_positive']=verdict['aggregate_doubled_cost_profit_USDT_10000_per_window']>0
    frozen_json(HERE/'r22_results.json',dict(input_manifest=verification,results=results,verdict=verdict,
        scope='One preregistered fixed-panel native participation gate; spot only, two reused windows, fixed costs. Historical publication is an explicit assumption; no class/bot/activation.'))


if __name__=='__main__':main()
