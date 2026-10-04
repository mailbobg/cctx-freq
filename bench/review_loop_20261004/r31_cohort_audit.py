"""Native full/partial reserve history coverage; no allocations or returns."""
import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import hashlib
import json

from r20_audit import retained
from r31_cohort_sources import DATA, HERE, PROTOCOL_COMMIT, decision_pairs, targets
from r31_native import parse_archive
from r31_units import normalized, parse_versions
from r31_units_sources import DATA as UNIT_DATA


def required_pair(parsed,monday_text,versions):
    monday=date.fromisoformat(monday_text)
    if (monday.isoformat()!=monday_text or monday.weekday()!=0 or
        parsed['vintage']!=(monday-timedelta(days=2)).isoformat()):
        raise ValueError('Foreign native vintage or changed original Monday')
    current=monday-timedelta(days=5);prior=current-timedelta(days=28)
    values=[]
    for observation in (current,prior):
        value,units,text=normalized(parsed,observation.isoformat(),versions)
        values.append(dict(observation_date=observation.isoformat(),native_text=text,
            original_row_index=list(parsed['rows']).index(observation.isoformat()),
            normalized_million_usd=format(value,'f'),native_version_units=units))
    return values


def audit(partial=False,checkpoint_capture_utc=None):
    body=(DATA/'manifest.json').read_bytes();snapshot=json.loads(body)
    if snapshot['protocol_commit']!=PROTOCOL_COMMIT:raise ValueError('Registered economic protocol differs')
    records=snapshot['sources'];fixed=targets()
    if any(key.split('_retry_')[0] not in fixed for key in records):raise ValueError('Unregistered version/retry')
    unit_manifest=json.loads((UNIT_DATA/'manifest.json').read_text())
    unit_record=unit_manifest['sources']['unit_versions']
    unit_body=retained(UNIT_DATA,unit_record)
    if unit_record['status']!=200:raise ValueError('Native unit metadata unavailable')
    versions=parse_versions(unit_body)
    parents={}
    for folder in ('r31_sources','r31_units_sources'):
        p=HERE/folder/'manifest.json'
        if hashlib.sha256(p.read_bytes()).hexdigest()!=snapshot['original_parent_manifest_sha256'][folder]:
            raise ValueError('Inherited parent source manifest changed')
        parents[folder]=json.loads(p.read_text())['sources']
    hashes={};coverage=[];native_rows=0;unknown=0;missing=[]
    hashes['r31_units_sources/'+unit_record['path']]=unit_record['sha256']
    for folder in parents:
        hashes[folder+'/manifest.json']=snapshot['original_parent_manifest_sha256'][folder]
    for key,r in records.items():
        if r['url']!=fixed[key.split('_retry_')[0]]:raise ValueError('Native version URL changed')
        retained(DATA,r)
        if r.get('path'):hashes['r31_cohort_sources/'+r['path']]=r['sha256']
        if r.get('inherited'):
            folder=r['original_manifest'].split('/')[0];old=parents[folder][r['original_record']]
            if (r['original_manifest_sha256']!=snapshot['original_parent_manifest_sha256'][folder] or
                any(r[k]!=old[k] for k in ('url','requested_utc','retrieved_utc','sha256','bytes','status'))):
                raise ValueError('Inherited original source/receipt provenance changed')
    for pair in decision_pairs():
        role='archive_'+pair['vintage']
        attempts=sorted((key,r) for key,r in records.items() if key.split('_retry_')[0]==role and r['status']==200)
        if not attempts:
            if not partial:raise ValueError('Required full native reserve version unavailable '+role)
            continue
        key,r=attempts[0];parsed=parse_archive(retained(DATA,r),pair['vintage'])
        if parsed['outside_requested_interval_dates']:raise ValueError('Native interval scope differs')
        native_rows+=len(parsed['rows']);unknown+=sum(v in ('','.') for v in parsed['rows'].values())
        monday=date.fromisoformat(pair['monday']);current=monday-timedelta(days=5);prior=current-timedelta(days=28)
        values=[];unavailable=[]
        for observation in (current,prior):
            native=parsed['rows'].get(observation.isoformat())
            if native in (None,'','.'):
                unavailable.append(dict(observation_date=observation.isoformat(),
                    native_row_present=observation.isoformat() in parsed['rows'],native_text=native,
                    native_state='absent_row' if native is None else 'blank' if native=='' else 'dot'))
                missing.append(dict(**pair,source_record=key,source_sha256=r['sha256'],
                                    **unavailable[-1]))
                continue
            value,units,text=normalized(parsed,observation.isoformat(),versions)
            values.append(dict(observation_date=observation.isoformat(),native_text=text,
                original_row_index=list(parsed['rows']).index(observation.isoformat()),
                normalized_million_usd=format(value,'f'),native_version_units=units))
        if not unavailable and required_pair(parsed,pair['monday'],versions)!=values:
            raise ValueError('Known exact native counterpart guard differs')
        coverage.append(dict(**pair,selected_record=key,source_sha256=r['sha256'],
            current_capture_utc=r['retrieved_utc'],native_observations=values,
            native_unavailable_required_observations=unavailable,
            native_csv_rows=len(parsed['rows']),original_bodies_and_decimal_text_unchanged=True))
    if partial:
        snapshot_name='r31_collection_prefix_snapshot.json'
        output_name='r31_collection_prefix_validation.json'
        snapshot_path=HERE/snapshot_name
        if snapshot_path.exists():raise ValueError('Existing original partial checkpoint must not be overwritten')
        snapshot_path.write_bytes(body)
    else:
        snapshot_name='r31_cohort_sources/manifest.json';output_name='r31_full_source_validation.json'
        if len(coverage)!=292:raise ValueError('Incomplete fixed source cohort')
    hashes[snapshot_name]=hashlib.sha256(body).hexdigest()
    clock=checkpoint_capture_utc or datetime.now(timezone.utc).isoformat()
    clock_value=datetime.fromisoformat(clock)
    if clock_value.tzinfo is None or clock_value<max(datetime.fromisoformat(r['retrieved_utc']) for r in records.values() if r['status']==200):
        raise ValueError('Original audit checkpoint cannot precede native capture')
    report=dict(protocol_commit=PROTOCOL_COMMIT,checkpoint_capture_utc=clock,
        partial_snapshot=partial,full_collection_complete=len(coverage)==292,
        retained_attempts=len(records),http_status_counts=dict(Counter(str(r['status']) for r in records.values())),
        inherited_original_successes=sum(bool(r.get('inherited')) for r in records.values()),
        native_versions_inspected=len(coverage),native_versions_with_known_exact_pair=sum(not c['native_unavailable_required_observations'] for c in coverage),
        native_required_cells_checked=len(coverage)*2,native_required_known_cells=len(coverage)*2-len(missing),
        native_unavailable_required_cells=len(missing),unavailable_native_cells=missing,
        full_required_source_gate=len(coverage)==292 and not missing,
        original_source_failure_not_cash_or_alpha=True,
        native_rows_parsed=native_rows,original_unknown_cells_preserved=unknown,
        source_coverage=coverage,native_input_sha256=hashes,
        no_allocation_or_price_or_fee_or_performance_computed=True,
        original_first_public_receipt_not_certified=True)
    (HERE/output_name).write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('source_coverage','native_input_sha256')},indent=2))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--partial',action='store_true')
    args=parser.parse_args();audit(args.partial)
