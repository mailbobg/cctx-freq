"""Source/controls/prefixes first, fixed quarterly-discount outcomes second."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from r21_basis import ASSETS, parse_native, front_symbol, feature, overlay
from r7_dated import LISTED
from r17_prices import verified_zip
from r1_r2b_risk import signals, risk_weights, RiskHoldR2b
from r9_selection import load_prices
from r10_gold import simulate_spot, WINDOWS
from r13_atr import simulate as minute_simulate
from r8_crowding import summarize
from r19_run import frozen_json, max_gap

HERE=Path(__file__).resolve().parent
DATA=HERE/'r7_data'


def feature_inputs():
    manifest=json.loads((DATA/'manifest.json').read_text());records=json.loads((DATA/'sources.json').read_text())
    hashes={};bodies={}
    for url,r in records.items():
        if r['url']!=url or Path(r['path']).is_absolute() or '..' in Path(r['path']).parts:raise ValueError('Unreviewed inherited raw identity')
        path=DATA/r['path'];body=path.read_bytes()
        if len(body)!=r['bytes'] or hashlib.sha256(body).hexdigest()!=r['sha256']:raise ValueError('Original R7 raw source changed')
        bodies[url]=body;hashes[str(path.relative_to(HERE))]=r['sha256']
    for name,sha in manifest['output_sha256'].items():
        p=DATA/name
        if hashlib.sha256(p.read_bytes()).hexdigest()!=sha:raise ValueError('Original R7 parsed price changed')
        hashes[str(p.relative_to(HERE))]=sha
    for name in ('sources.json','manifest.json'):
        hashes['r7_data/'+name]=hashlib.sha256((DATA/name).read_bytes()).hexdigest()
    cache={};selected_sources=set();features={};counterpart=[]
    parquet={name:pd.read_parquet(DATA/name) for name in manifest['output_sha256']}
    def native(asset,symbol,friday):
        filename=f'{symbol}-1d-{friday:%Y-%m}.zip'
        prefix='spot' if symbol==asset+'USDT' else 'futures/um'
        url=f'https://data.binance.vision/data/{prefix}/monthly/klines/{symbol}/1d/{filename}'
        if url not in records or url+'.CHECKSUM' not in records:raise ValueError('Expected individually identified native source absent '+url)
        if url not in cache:
            csv=verified_zip(bodies[url],bodies[url+'.CHECKSUM'],filename);cache[url]=parse_native(csv)
        selected_sources.update((url,url+'.CHECKSUM'))
        return cache[url],url
    for label,start,end in WINDOWS:
        for monday in pd.date_range(start,end,freq='W-MON',tz='UTC'):
            pair={};friday=monday-pd.Timedelta(days=3)
            for asset in ASSETS:
                symbol=front_symbol(asset,monday)
                if symbol in LISTED and LISTED[symbol]>monday:
                    f=feature(asset,monday,None,None,{})
                else:
                    spot,surl=native(asset,asset+'USDT',friday);dated,furl=native(asset,symbol,friday)
                    provenance=dict(spot_url=surl,future_url=furl,spot_sha256=records[surl]['sha256'],future_sha256=records[furl]['sha256'],
                        spot_current_capture=records[surl]['retrieved_utc'],future_current_capture=records[furl]['retrieved_utc'])
                    f=feature(asset,monday,spot,dated,provenance)
                    for name,rows,file in [('spot',spot,asset+'USDT_spot.parquet'),('future',dated,symbol+'.parquet')]:
                        frame=parquet[file]
                        if friday not in frame.index or frame.loc[friday,'trades']<=0:raise ValueError('Native feature/parsed calendar differs')
                        native_close=float(rows[friday]['close']);prior_close=float(frame.loc[friday,'close']);gap=abs(native_close-prior_close)
                        if gap>1e-8:raise ValueError('Native/parsed counterpart price differs')
                        counterpart.append(dict(decision=monday,asset=asset,kind=name,symbol=asset+'USDT' if name=='spot' else symbol,
                            native_close_decimal=rows[friday]['close'],existing_parquet_close=prior_close,absolute_gap=gap))
                f['window']=label;pair[asset]=f
            features[monday]=pair
    audit=dict(source_stage_only=True,new_performance=False,protocol_commit='bd9e42a',original_responses_verified=len(records),
        original_parquets_verified=len(manifest['output_sha256']),required_native_csvs_parsed=len(cache),
        required_original_zip_checksum_responses=len(selected_sources),decision_mondays=len(features),
        asset_decisions=sum(len(f) for f in features.values()),known_asset_decisions=sum(f['known'] for pair in features.values() for f in pair.values()),
        source_feature_cohort={str(d):pair for d,pair in features.items()},selected_source_urls=sorted(selected_sources),native_parquet_counterparts=counterpart,
        max_native_parquet_counterpart_gap=max(r['absolute_gap'] for r in counterpart),
        originals_never_overwritten=True,trade_close_decimal_comparison_not_float=True,
        historical_publication_version_certified=False,
        availability_assumption='Friday native interval endsSaturday UTC00 + one full additional day:Sunday UTC00',
        current_capture_never_claimed_historical_receipt=True,
        limitations='Current original exchange archive versions, not historical publication vintages. Last trade closes need not be synchronous/executable bid/ask. Historical contract specification/quote/lot fidelity and first-public receipts are not certified. Necessary screens only; no actual futures position.')
    frozen_json(HERE/'r21_source_audit.json',audit)
    return features,hashes


def prepare():
    features,hashes=feature_inputs();px,spot,old_hashes=load_prices();hashes.update(old_hashes)
    for a in ASSETS:px[a+'_high'],px[a+'_low']=spot[a].high,spot[a].low
    base=risk_weights(px,signals(px,RiskHoldR2b));target,audit=overlay(base,features)
    disabled=pd.DataFrame(np.nan,index=px.index,columns=ASSETS)
    mm=json.loads((HERE/'r13_data/manifest.json').read_text());minutes={}
    for a in ASSETS:
        path=HERE/f'r13_data/{a}USDT_1m.parquet';sha=hashlib.sha256(path.read_bytes()).hexdigest()
        if sha!=mm['output_sha256'][path.name]:raise ValueError('Frozen native minute source changed')
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
                columns=[c for c in m if not c.startswith('pending_')]
                gaps[f'{label}_minute_cost{cost}']=max_gap(m,prior_m,columns)
    for cutoff in ('2021-12-31','2024-12-31'):
        short=px.loc[:cutoff];prefix_base=risk_weights(short,signals(short,RiskHoldR2b))
        prefix_features={d:f for d,f in features.items() if d<=short.index[-1]+pd.Timedelta(days=1)}
        for d,pair in prefix_features.items():
            for a,f in pair.items():
                if f['known']:
                    if f['observation_day']>short.index[-1] or f['available_at_assumed']>d:raise ValueError('Future native observation')
        prefix_target,_=overlay(prefix_base,prefix_features)
        pd.testing.assert_frame_equal(prefix_target,target.loc[:cutoff])
    full,orders=minute_simulate(px,target,disabled,minutes,'2024-01-01','2024-07-05',.0012,active=False,terminal=False)
    cut=px.loc[:'2024-06-01'];subset={d:f for d,f in features.items() if d<=cut.index[-1]+pd.Timedelta(days=1)}
    short_target,_=overlay(risk_weights(cut,signals(cut,RiskHoldR2b)),subset)
    part,events=minute_simulate(cut,short_target,disabled.loc[cut.index],{a:f.loc[:'2024-06-01'] for a,f in minutes.items()},
        '2024-01-01','2024-06-01',.0012,active=False,terminal=False)
    pd.testing.assert_frame_equal(part,full.loc[:part.index[-1]])
    pd.testing.assert_frame_equal(events,orders.loc[orders.time<pd.Timestamp('2024-06-02',tz='UTC')].reset_index(drop=True))
    if not part[[f'pre_close_units_{a}' for a in ASSETS]].gt(0).any().any():raise AssertionError('Prefix must exercise real holdings')
    daily=simulate_spot(px,target,'2024-01-01','2024-07-05',.0012,terminal=False)
    candidate_gap=max_gap(daily,full,list(daily))
    names=['R21_PROTOCOL.md','r21_basis.py','test_r21_basis.py','r21_run.py','r21_reflection.py',
        'r1_r2b_risk.py','r9_selection.py','r10_gold.py','r13_atr.py','r8_crowding.py',
        'r7_dated.py','r7_data.py','r17_prices.py','r19_run.py','r19_reflection.py','r21_source_audit.json']
    verification=dict(source_inputs_sha256=hashes,source_code_sha256={n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in names},
        baseline_max_abs_gap=gaps,candidate_daily_minute_prefix_gap=candidate_gap,exact_source_weight_prefix_equal=True,
        real_held_execution_and_native_order_prefix_equal=True,ATR_disabled=True,
        sole_change='Per-asset exact Friday native quarterly discount veto after original joint covariance weights',
        historical_publication_certified=False,no_companion_rescale=True,no_rule_tuning=True)
    audit.to_csv(HERE/'r21_decision_audit.csv',index=False);target.to_csv(HERE/'r21_weight_audit.csv')
    frozen_json(HERE/'r21_input_manifest.json',verification)
    return px,base,target,disabled,minutes,verification


def main():
    p=argparse.ArgumentParser();p.add_argument('--verify-only',action='store_true');args=p.parse_args()
    px,base,target,disabled,minutes,verification=prepare()
    if args.verify_only:
        print('Native sources/clocks/identities, six references and exact real-held/order prefixes PASS');return
    results={}
    for label,start,end in WINDOWS:
        for cost in (.0012,.0024):
            for name,weights in [('Cov10_baseline',base),('basis_confirmation',target)]:
                curve=simulate_spot(px,weights,start,end,cost);r=summarize(curve)
                r['max_accounting_gap']=float(curve.accounting_gap.abs().max());key=f'{label}_{name}_cost{cost}'
                if label=='recent_reused':
                    m,orders=minute_simulate(px,weights,disabled,minutes,start,end,cost,active=False)
                    r['daily_minute_core_max_gap']=max_gap(curve,m,list(curve))
                    r['sampled_minute_account_DD_pct']=float(m.sampled_minute_running_dd_pct.iloc[-1])
                    r['necessary_screen_gate'] &= r['sampled_minute_account_DD_pct']>=-5
                    curve=m;orders.to_csv(HERE/f'r21_{key}_orders.csv',index=False)
                r['risk_limitation']='Daily account CLOSE; recent synchronized minute OPEN/CLOSE. No earlier intraday/tick quote/depth/lot, past exact publication version, Freqtrade/independent forward certification.'
                results[key]=r;curve.to_csv(HERE/f'r21_{key}_daily.csv')
                print(key,{k:round(r[k],4) for k in ('CAGR_pct','daily_MTM_DD_pct','fees_USDT_10000_start')},
                    'minute_DD',r.get('sampled_minute_account_DD_pct'),'gate',r['necessary_screen_gate'],flush=True)
    candidate={k:r for k,r in results.items() if '_basis_confirmation_' in k}
    verdict=dict(all_base_windows_pass=all(r['necessary_screen_gate'] for k,r in candidate.items() if k.endswith('0.0012')),
        aggregate_doubled_cost_profit_USDT_10000_per_window=sum(r['return_pct']*100 for k,r in candidate.items() if k.endswith('0.0024')),
        adopted=False,no_sign_tenor_day_latency_sizing_or_universe_rescue=True)
    verdict['aggregate_doubled_cost_net_profit_positive']=verdict['aggregate_doubled_cost_profit_USDT_10000_per_window']>0
    frozen_json(HERE/'r21_results.json',dict(input_manifest=verification,results=results,verdict=verdict,
        scope='One preregistered native quarterly-discount information gate; spot-only. Two reused windows, fixed costs. Historical availability is an explicit conservative market-record assumption, not a certified publication version. No class/bot/activation.'))


if __name__=='__main__':main()
