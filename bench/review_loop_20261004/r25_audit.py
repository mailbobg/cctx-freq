"""Replay bounded original REST/archive counterparts; no trading cohort."""
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from r17_prices import verified_zip
from r20_audit import retained
from r23_inventory import label_groups
from r23_audit import describe
from r25_rest import parse_api_observations, compare_fields, parse_archive_observations, PAIR_FIELDS
import r25_sources as source

HERE=Path(__file__).resolve().parent


def main():
    source.collect(source.targets())
    records=json.loads((source.DATA/'manifest.json').read_text())['sources']
    bodies={}; selected={}; failures=[]
    for key,r in records.items():
        original=key.split('_retry_')[0]
        if original not in source.targets() or r['url']!=source.targets()[original]:
            raise ValueError('Foreign original/retry request identity')
        if key!=original and records[original]['status'] is not None:
            raise ValueError('Only transport-failure same-URL retries preregistered')
        bodies[key]=retained(source.DATA,r)
        if r['status']==200 and original not in selected: selected[original]=key
        if r['status']!=200:
            failures.append(dict(record=key,original_role=original,status=r['status'],
                source_error=r.get('source_error'),body_sha256=r.get('sha256'),
                unknown_not_zero=True))
    for role,url in source.targets().items():
        if role not in records or records[role]['url']!=url: raise ValueError('Original request missing')
    archives=[]; api_samples=[]; comparisons=[]; summaries=[]; start,end=source.bounds()
    for symbol in source.SYMBOLS:
        role='metrics_'+symbol; check=role+'_checksum'
        if role not in selected or check not in selected: continue
        zip_record,check_record=records[selected[role]],records[selected[check]]
        rows,ordering=parse_archive_observations(verified_zip(bodies[selected[role]],bodies[selected[check]],
            f'{symbol}-metrics-{source.DAY}.zip'),symbol,source.DAY)
        groups=label_groups(rows)
        if any(not g['identical_native_fields'] for g in groups):
            raise ValueError('Conflicting original archive labels')
        expected_labels={datetime.strptime(source.DAY,'%Y-%m-%d')+timedelta(minutes=5*i) for i in range(288)}
        observed={g['label'] for g in groups}
        archives.append(dict(symbol=symbol,zip_record=selected[role],checksum_record=selected[check],
            zip_sha256=zip_record['sha256'],checksum_sha256=check_record['sha256'],
            original_row_order_observation=ordering,
            minimum_declared_label=min(observed),maximum_declared_label=max(observed),
            missing_expected_labels=sorted(expected_labels-observed),extra_declared_labels=sorted(observed-expected_labels),
            exact_complete_declared_calendar=observed==expected_labels,**describe(rows)))
        native=[g['native_values'][0] for g in groups]
        for kind in ('oi','taker'):
            role=f'{kind}_{symbol}'
            if role not in selected:
                api_samples.append(dict(symbol=symbol,kind=kind,state='unavailable_counterpart',
                    actual_rows=None,unknown_not_zero=True)); continue
            key=selected[role];r=records[key]
            api,window_observation=parse_api_observations(bodies[key],symbol,kind,start,end,r['url'])
            expected_epochs=set(range(start,end+1,300000)); observed_epochs=set(api)
            api_samples.append(dict(symbol=symbol,kind=kind,record=key,body_sha256=r['sha256'],
                state='known_rows' if api else 'empty_source',actual_rows=len(api),
                first_native_epoch=min(api) if api else None,last_native_epoch=max(api) if api else None,
                exact_requested_grid_complete=observed_epochs==expected_epochs,
                missing_expected_epochs=sorted(expected_epochs-observed_epochs),
                extra_returned_epochs=sorted(observed_epochs-expected_epochs),
                original_returned_window_observation=window_observation,
                api_symbol_payload_present=all('symbol' in row for row in api.values()) if api else None,
                taker_symbol_evidence_is_request_not_invented_payload=kind=='taker',
                first_public_receipt_or_previous_version_certified=False))
            group_comparisons=[compare_fields(row,api,kind) for row in native]
            comparisons.extend(group_comparisons)
            for convention in ('label_time','label_plus_5m'):
                for alias,_,_ in PAIR_FIELDS[kind]:
                    known=[v[convention]['fields'][alias] for v in group_comparisons
                        if v[convention]['state']=='known_counterpart' and v[convention]['fields'][alias]['state']=='known']
                    differences=[Decimal(v['absolute_error_decimal']) for v in known]
                    relative=[Decimal(v['relative_error_decimal']) for v in known if v['relative_error_decimal'] is not None]
                    summaries.append(dict(symbol=symbol,kind=kind,convention=convention,field=alias,
                        archive_labels=len(native),known_comparisons=len(known),
                        missing_counterparts=sum(v[convention]['state']=='missing_counterpart' for v in group_comparisons),
                        null_field_unknown=sum(v[convention]['state']=='known_counterpart' and
                            v[convention]['fields'][alias]['state']=='api_null_unknown' for v in group_comparisons),
                        exact_decimal_equal=sum(v['exact_decimal_equal'] for v in known),
                        max_absolute_error_decimal=format(max(differences),'f') if differences else None,
                        max_relative_error_decimal=str(max(relative)) if relative else None,
                        no_fitted_tolerance_or_clock_selection=True))
    doc={}
    if 'official_rest' in selected:
        key=selected['official_rest'];text=bodies[key].decode('utf-8');endpoint_facts={}
        for endpoint,description in (('/futures/data/openInterestHist','End time of the period'),
                                      ('/futures/data/takerlongshortRatio','Start time of the period')):
            position=text.find(endpoint)
            description_offset=text[position:position+42000].find(description)
            if position<0 or description_offset<0: raise ValueError('Current endpoint/time description not found')
            endpoint_facts[endpoint]=dict(endpoint_position=position,description_offset=description_offset,
                current_native_timestamp_description=description)
        doc=dict(record=key,body_sha256=records[key]['sha256'],endpoint_facts=endpoint_facts,
            current_description_not_historical_csv_measurement_or_publication_certification=True)
    result=dict(source_only=True,new_alpha_trial=False,new_performance=False,adopted=False,
        plan_commit='9bcfd84',fixed_day=source.DAY,original_roles=len(source.targets()),retained_attempts=len(records),
        status_counts=dict(Counter(str(r['status']) for r in records.values())),source_failures=failures,
        selected_original_or_explicit_retry_records=selected,original_archives=archives,api_samples=api_samples,
        native_comparison_summaries=summaries,all_native_comparisons=comparisons,current_documentation=doc,
        original_labels_decimal_values_duplicates_errors_and_publication_unknowns_preserved=True,
        actual_historical_measurement_units_and_first_public_versions_certified=False,
        no_signal_sign_horizon_threshold_weighting_or_performance=True,
        no_strategy_bot_image_or_forward_cohort_change=True,
        raw_body_sha256={k:r['sha256'] for k,r in records.items() if r['path'] is not None},
        code_sha256={n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in
            ('R25_SOURCE_PLAN.md','r25_sources.py','r25_rest.py','test_r25_rest.py','r25_audit.py')},
        interpretation='Exact agreement can corroborate these current native field/label counterparts under the explicit naive-label-as-UTC assumption. It is not an actual measurement-instant/unit/scope/first-public-version certification and does not establish alpha; all mismatches remain unchanged.')
    output=json.dumps(result,indent=2,sort_keys=True,default=lambda v:v.isoformat())+'\n'
    path=HERE/'r25_source_audit.json'
    if path.exists() and path.read_text()!=output: raise ValueError('Frozen REST/archive source audit changed')
    path.write_text(output)
    print(json.dumps(dict(attempts=len(records),status_counts=result['status_counts'],
        comparisons=len(comparisons),source_only=True,new_alpha_trial=False,new_performance=False)))


if __name__=='__main__':main()
