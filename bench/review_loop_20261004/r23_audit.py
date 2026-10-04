"""Bounded source audit; observed labels cannot certify past measurement time."""
from collections import Counter
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from r20_audit import retained
from r17_prices import verified_zip
from r23_inventory import FIELDS,parse_metrics,label_groups
import r23_sources as source

HERE=Path(__file__).resolve().parent


def describe(rows):
    groups=label_groups(rows);labels=[g['label'] for g in groups]
    gaps=[dict(prior=a.isoformat(),following=b.isoformat(),minutes=(b-a).total_seconds()/60)
        for a,b in zip(labels,labels[1:]) if b-a!=timedelta(minutes=5)]
    return dict(original_rows=len(rows),unique_declared_labels=len(groups),declared_first_label=labels[0].isoformat(),
        declared_last_label=labels[-1].isoformat(),duplicate_multiplicity_histogram=dict(Counter(g['original_rows'] for g in groups)),
        conflicting_within_file_labels=[g for g in groups if not g['identical_native_fields']],naive_label_grid_gaps=gaps,
        first_native_row={k:rows[0][k] for k in FIELDS},last_native_row={k:rows[-1][k] for k in FIELDS},
        raw_duplicates_never_deleted=True,measurement_time_zone_certified=False,
        label_start_or_end_semantics_certified=False,authenticated_first_public_receipt=None)


def main():
    data=source.DATA;original=source.collect(source.targets());parsed={};archives=[];failed=[]
    records=json.loads((data/'manifest.json').read_text())['sources']
    if set(records)!=set(source.targets()):raise ValueError('Unreviewed/missing bounded request cohort')
    for key,(body,r) in original.items():
        retained(data,r)
        expected=404 if key.startswith('ETHUSDT_2021-01-01') else 200
        if r['status']!=expected:raise ValueError('Observed source HTTP status changed '+key)
        if expected==404:
            tree=ET.fromstring(body)
            failed.append(dict(request=key,status=404,error_code=tree.findtext('Code'),
                body_sha256=r['sha256'],unknown_inventory_not_zero=True,no_retry_or_date_substitution=True))
    for symbol in source.SYMBOLS:
        for day in source.DAYS:
            key=symbol+'_'+day
            body,r=original[key];checksum,c=original[key+'_checksum']
            rows=parse_metrics(verified_zip(body,checksum,f'{symbol}-metrics-{day}.zip'),symbol,day);parsed[(symbol,day)]=rows
            archives.append(dict(symbol=symbol,file_day=day,zip_sha256=r['sha256'],checksum_sha256=c['sha256'],
                current_capture=r['retrieved_utc'],current_http_last_modified_label=r['headers'].get('Last-Modified'),**describe(rows)))
    olddata=HERE/'r22_sources';old=json.loads((olddata/'manifest.json').read_text())['sources']
    btc,bc=old['oi_early_day'],old['oi_early_day_checksum']
    if btc['url']!='https://data.binance.vision/data/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-2021-01-01.zip':raise ValueError('Inherited original early source identity changed')
    early=parse_metrics(verified_zip(retained(olddata,btc),retained(olddata,bc),'BTCUSDT-metrics-2021-01-01.zip'),'BTCUSDT','2021-01-01')
    inherited=dict(source_manifest='r22_sources/manifest.json',source_record='oi_early_day',checksum_record='oi_early_day_checksum',
        zip_sha256=btc['sha256'],checksum_sha256=bc['sha256'],current_capture=btc['retrieved_utc'],**describe(early))
    comparisons=[]
    for symbol in source.SYMBOLS:
        for earlier,later in zip(source.DAYS,source.DAYS[1:]):
            left,right=parsed[(symbol,earlier)],parsed[(symbol,later)]
            overlaps=sorted(set(r['label'] for r in left)&set(r['label'] for r in right))
            conflict=[]
            for label in overlaps:
                values=[{k:r[k] for k in FIELDS} for r in [*left,*right] if r['label']==label]
                conflict.append(dict(label=label,all_original_native_rows=values,identical_native_fields=all(v==values[0] for v in values)))
            comparisons.append(dict(symbol=symbol,earlier_file_day=earlier,later_file_day=later,
                duplicate_declared_labels=conflict,naive_boundary_gap_minutes=(right[0]['label']-left[-1]['label']).total_seconds()/60))
    docs=original['official_rest'][0].decode();issue=json.loads(original['source_clock_issue'][0])
    if issue['number']!=491 or issue['html_url']!='https://github.com/binance/binance-public-data/issues/491':raise ValueError('Clock report identity differs')
    if 'End time of the period' not in docs:raise ValueError('Official REST time-description content differs')
    report_claim='00:05:00' in issue['body'] and '2026-06-24' in issue['body']
    if not report_claim:raise ValueError('Retained bounded report content changed')
    result=dict(source_only=True,new_alpha_trial=False,new_performance=False,adopted=False,plan_commit='9163efa',
        original_request_roles=len(records),status_counts=dict(Counter(str(r['status']) for r in records.values())),
        native_schema=list(FIELDS),original_archives=archives,inherited_early_btc=inherited,source_failures=failed,
        cross_file_label_comparisons=comparisons,
        official_rest=dict(period_end_description_present=True,api_description_does_not_certify_csv_labels=True,
            native_asset_unit_and_historical_contract_scope_certified=False,latest_month_rest_limit_not_archive_history_limit=True),
        third_party_clock_report=dict(url=issue['html_url'],created_at_label=issue['created_at'],updated_at_label=issue['updated_at'],
            issue_state_at_capture=issue['state'],comments_at_capture=issue['comments'],publisher_confirmation_found=False,
            claim_earlier_day_starts_0005=True,current_six_archives_all_start_0000=all(a['declared_first_label'].endswith('T00:00:00') for a in archives),
            current_boundary_duplicate_found=any(c['duplicate_declared_labels'] for c in comparisons),
            claimed_change_not_reproduced_in_current_originals=True,
            no_authenticated_older_raw_version_or_cause=True,http_last_modified_not_first_public_version_proof=True,
            no_timestamp_shift_or_first_last_deduplication_applied=True),
        code_sha256={n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in ('R23_SOURCE_PLAN.md','r23_sources.py','r23_inventory.py','test_r23_inventory.py','r23_audit.py')},
        retained_body_sha256={key:r['sha256'] for key,r in records.items()},
        verdict='Bounded source observations cannot certify full-history inventory/clock/publication fidelity. Early ETH original and checksum are genuine404, not zero. Reported label boundary is not reproduced by current bodies; no authenticated revision explanation. No OI alpha rule/cohort/backtest or runtime change.',
        next_action='Preserve actual failure and discrepant source lead. Verify feasible inventory scope, physical units, measurement/availability intervals and authentic historical versions before a distinct critic/rule; do not shift/de-duplicate current data to fit a report or market outcome.')
    path=HERE/'r23_source_audit.json';text=json.dumps(result,indent=2,sort_keys=True,default=lambda x:x.isoformat())+'\n'
    if path.exists() and path.read_text()!=text:raise ValueError('Frozen bounded inventory source audit changed')
    path.write_text(text)
    print('Six native source label calendars and inherited early BTC audited; two early ETH404bodies retained. No OI rule, cohort or performance.')


if __name__=='__main__':main()
