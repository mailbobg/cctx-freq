"""Reconcile fixed native fields/windows, without choosing a strategy latency."""
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pandas as pd

from r20_audit import retained
from r17_prices import verified_zip
from r23_inventory import parse_metrics,label_groups
from r23_audit import describe
from r24_reconcile import parse_5m,ratio_comparison,oi_volume_comparison
import r24_sources as source

HERE=Path(__file__).resolve().parent


def summarize_ratios(comparisons):
    out={}
    for convention in ('preceding','following'):
        for unit in ('base','quote'):
            values=[c[convention][unit] for c in comparisons if c[convention]['state']=='known_native_interval' and c[convention][unit]['state']=='value']
            errors=pd.Series([float(v['relative_error_decimal']) for v in values if v['relative_error_decimal'] is not None])
            out[convention+'_'+unit]=dict(compared_values=len(values),within_reported_half_ulp=sum(v['within_reported_half_ulp'] for v in values),
                median_relative_error=float(errors.median()) if len(errors) else None,max_relative_error=float(errors.max()) if len(errors) else None,
                missing_intervals=sum(c[convention]['state']=='missing_native_interval' for c in comparisons),
                zero_sell_states=sum(c[convention]['state']=='known_native_interval' and c[convention][unit]['state']=='zero_sell_volume' for c in comparisons))
    return out


def summarize_bounds(comparisons):
    out={}
    for convention in ('label_time_window','label_plus_5m_window'):
        values=[c[convention] for c in comparisons if c[convention]['state']=='known_native_interval']
        out[convention]=dict(compared_values=len(values),strict_exceeds_volume=sum(v['exceeds_native_volume'] for v in values),
            summed_absolute_unit_change_decimal=str(sum((Decimal(c['absolute_native_oi_change']) for c in comparisons),Decimal(0))),
            summed_compared_native_volume_decimal=str(sum((Decimal(v['volume_decimal']) for v in values),Decimal(0))),
            summed_interval_excess_decimal=str(sum((Decimal(v['excess_decimal']) for v in values),Decimal(0))),
            max_interval_excess_decimal=str(max((Decimal(v['excess_decimal']) for v in values),default=Decimal(0))),
            missing_intervals=len(comparisons)-len(values),not_actual_inventory_or_clock_certification=True)
    return out


def main():
    data=source.DATA;source.collect(source.targets());records=json.loads((data/'manifest.json').read_text())['sources'];originals=source.targets()
    for name,r in records.items():
        root=name.split('_retry_',1)[0]
        if root not in originals or r['url']!=originals[root]:raise ValueError('Unregistered counterpart request identity')
        retained(data,r)
    chosen={}
    def get(key):
        candidates=[(n,r) for n,r in records.items() if (n==key or n.startswith(key+'_retry_')) and r['url']==originals[key] and r['status']==200]
        if not candidates:raise ValueError('Native counterpart failure, no zero/cash fallback '+key)
        name,r=sorted(candidates,key=lambda p:(p[1]['retrieved_utc'],p[0]))[0];chosen[key]=name
        return retained(data,r),r
    prices={s:{} for s in source.SYMBOLS};price_coverage=[]
    for symbol in source.SYMBOLS:
        for day in source.DAYS+(('2021-01-01',) if symbol=='BTCUSDT' else ()):
            key='klines_'+symbol+'_'+day;body,r=get(key);checksum,c=get(key+'_checksum')
            rows=parse_5m(verified_zip(body,checksum,f'{symbol}-5m-{day}.zip'),day)
            expected=list(pd.date_range(day,periods=288,freq='5min',tz='UTC'))
            if list(rows)!=expected:raise ValueError('Full declared native counterpart calendar differs')
            if set(prices[symbol])&set(rows):raise ValueError('Native counterpart duplicates across files')
            prices[symbol].update(rows);price_coverage.append(dict(symbol=symbol,day=day,native_5m_intervals=len(rows),zip_sha256=r['sha256'],checksum_sha256=c['sha256']))
    prior_records={};raw_inputs={};metrics=[]
    def inherited(folder,keys):
        root=HERE/folder;all_records=json.loads((root/'manifest.json').read_text())['sources'];used={}
        for key in keys:
            r=all_records[key];used[key]=(retained(root,r),r);raw_inputs[folder+'/'+r['path']]=r['sha256']
        prior_records[folder]=dict(manifest_sha256=hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest(),records={k:r['sha256'] for k,(_,r) in used.items()})
        return used
    recent_keys=[s+'_'+d+ending for s in source.SYMBOLS for d in source.DAYS[1:] for ending in ('','_checksum')]
    recent=inherited('r23_sources',recent_keys)
    for symbol in source.SYMBOLS:
        for day in source.DAYS[1:]:
            key=symbol+'_'+day;body,r=recent[key];checksum,c=recent[key+'_checksum']
            rows=parse_metrics(verified_zip(body,checksum,f'{symbol}-metrics-{day}.zip'),symbol,day)
            metrics.append((symbol,day,rows,r['sha256']))
    early=inherited('r22_sources',['oi_early_day','oi_early_day_checksum'])
    body,r=early['oi_early_day'];checksum,c=early['oi_early_day_checksum']
    metrics.append(('BTCUSDT','2021-01-01',parse_metrics(verified_zip(body,checksum,'BTCUSDT-metrics-2021-01-01.zip'),'BTCUSDT','2021-01-01'),r['sha256']))
    first_decision_sources=[]
    for day in ('2020-12-30','2021-01-29'):
        key='metrics_BTCUSDT_'+day;body,r=get(key);checksum,c=get(key+'_checksum')
        rows=parse_metrics(verified_zip(body,checksum,f'BTCUSDT-metrics-{day}.zip'),'BTCUSDT',day)
        first_decision_sources.append(dict(symbol='BTCUSDT',file_day=day,zip_sha256=r['sha256'],checksum_sha256=c['sha256'],**describe(rows)))
    ratio_details=[];bound_details=[];summary=[]
    for symbol,day,rows,sha in metrics:
        groups=label_groups(rows)
        if any(not g['identical_native_fields'] for g in groups):raise ValueError('Conflicting source labels cannot select first/last values')
        # Grouping identical observations for comparison retains counts and all
        # original raw records; it is not a reconstructed canonical time series.
        observations=[g['native_values'][0] for g in groups]
        ratios=[dict(symbol=symbol,file_day=day,source_sha256=sha,original_identical_records=g['original_rows'],
            **ratio_comparison(o,prices[symbol])) for g,o in zip(groups,observations)]
        bounds=[dict(symbol=symbol,file_day=day,source_sha256=sha,**oi_volume_comparison(old,new,prices[symbol])) for old,new in zip(observations,observations[1:])]
        ratio_details.extend(ratios);bound_details.extend(bounds)
        summary.append(dict(symbol=symbol,file_day=day,original_rows=len(rows),comparison_labels=len(observations),
            duplicate_multiplicity_histogram=dict(Counter(g['original_rows'] for g in groups)),
            ratio_correspondence=summarize_ratios(ratios),inventory_volume_bounds=summarize_bounds(bounds),
            grouped_only_fully_identical_fields_with_original_counts=True,no_source_clock_certification=True))
    original_failures=[]
    for key in ('metrics_ETHUSDT_2021-01-29','metrics_ETHUSDT_2021-01-29_checksum'):
        r=records[key]
        if r['status']!=404:raise ValueError('Observed missing early ETH counterpart changed')
        tree=ET.fromstring(retained(data,r));original_failures.append(dict(key=key,status=404,source_error_code=tree.findtext('Code'),unknown_not_zero=True))
    listing_body,listing_r=get('eth_metrics_listing');tree=ET.fromstring(listing_body);ns={'s':'http://s3.amazonaws.com/doc/2006-03-01/'}
    listing=dict(original_keys=[n.text for n in tree.findall('s:Contents/s:Key',ns)],truncated=tree.findtext('s:IsTruncated',namespaces=ns),
        first_current_listed_key_not_authenticated_first_public_day=True,not_a_complete_inventory=True)
    paper,paper_r=get('native_inventory_paper')
    if b'Reconciling Open Interest' not in paper:raise ValueError('Primary context paper identity differs')
    context_blog=[dict(record=n,status=r['status'],bytes=r.get('bytes'),source_error=r.get('source_error'),
        context_not_used_for_native_numeric_check=True) for n,r in records.items() if n.startswith('native_inventory_blog')]
    result=dict(source_only=True,new_alpha_trial=False,new_performance=False,adopted=False,plan_commit='6f597ce',
        original_request_roles=len(originals),retained_attempts=len(records),status_counts=dict(Counter(str(r['status']) for r in records.values())),
        selected_original_or_retry_records=chosen,native_price_counterparts=price_coverage,inherited_original_sources=prior_records,
        native_ratio_and_quantity_checks=summary,all_ratio_comparisons=ratio_details,all_inventory_volume_comparisons=bound_details,
        first_potential_decision_btc_source_feasibility=first_decision_sources,early_eth_source_failures=original_failures,eth_bounded_listing=listing,
        blog_context_responses=context_blog,primary_paper_sha256=paper_r['sha256'],
        metric_labels_never_shifted=True,raw_duplicates_never_deleted=True,no_fitted_error_tolerance=True,
        correspondence_precision_rule='Half one unit in the last digit of the original published Decimal text, fixed before comparisons',
        two_fixed_clock_conventions_not_trading_parameter_search=True,
        actual_measurement_timezone_unit_first_public_version_certified=False,
        no_signal_sign_threshold_horizon_cohort_or_performance=True,
        limitations='A source ratio correspondence can support one tested field/window under an explicit naive-label-as-UTC comparison assumption. It does not authenticate OI stock snapshot time/units, accurate or complete inventory, historical public receipts or archive versions. A volume inequality pass is necessary physical compatibility only; neither it nor a strict violation identifies which feed/clock/unit is right. No source errors create a zero/cash observation.',
        code_sha256={n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in ('R24_SOURCE_PLAN.md','r24_sources.py','r24_reconcile.py','test_r24_reconcile.py','r24_audit.py')},
        retained_body_sha256={n:r['sha256'] for n,r in records.items() if r.get('path')},inherited_raw_body_sha256=raw_inputs)
    path=HERE/'r24_source_audit.json';text=json.dumps(result,indent=2,sort_keys=True,default=lambda x:x.isoformat())+'\n'
    if path.exists() and path.read_text()!=text:raise ValueError('Frozen original counterpart audit changed')
    path.write_text(text)
    for v in summary:
        print(v['symbol'],v['file_day'],'ratio',v['ratio_correspondence'],'OI',v['inventory_volume_bounds'])
    print('Native source checks only; no inventory strategy or P&L.')


if __name__=='__main__':main()
