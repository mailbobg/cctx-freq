"""Audit the frozen source gate; unavailable data is not a losing alpha result."""
from collections import Counter
import csv
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import re

from r17_prices import verified_zip
from r20_audit import retained
from r23_inventory import FIELDS
from r26_inventory import parse_day
import r26_sources as source

HERE=Path(__file__).resolve().parent


def describe(body, day):
    reader=csv.reader(io.StringIO(body.decode('utf-8')))
    if tuple(next(reader))!=FIELDS:raise ValueError('Native inventory source header differs')
    rows=list(reader);labels=[];groups={};field_errors=[]
    for i,fields in enumerate(rows):
        if len(fields)!=len(FIELDS) or fields[1]!='BTCUSDT':raise ValueError('Native row shape/symbol differs')
        label=datetime.strptime(fields[0],'%Y-%m-%d %H:%M:%S')
        if label.strftime('%Y-%m-%d %H:%M:%S')!=fields[0] or label.second or label.minute%5:
            raise ValueError('Native label format/grid differs')
        labels.append(label);groups.setdefault(label,[]).append(fields)
        for field,value in zip(FIELDS[2:],fields[2:]):
            if not re.fullmatch(r'\d+(?:\.\d+)?',value):
                field_errors.append(dict(row_index=i,native_label=fields[0],field=field,native_text=value,
                    state='blank_unknown' if value=='' else 'unreviewed_native_text'))
    start=datetime.strptime(day,'%Y-%m-%d')
    expected={start+timedelta(minutes=5*i) for i in range(288)}
    observed=set(groups);last_label=start+timedelta(minutes=1435);last=groups.get(last_label,[])
    qty=[fields[2] for fields in last]
    quantity_known=bool(qty) and len(set(qty))==1 and re.fullmatch(r'\d+(?:\.\d+)?',qty[0]) is not None and Decimal(qty[0])>0
    return dict(original_rows=len(rows),unique_native_labels=len(groups),
        minimum_declared_label=min(labels),maximum_declared_label=max(labels),
        missing_required_labels=sorted(expected-observed),extra_declared_labels=sorted(observed-expected),
        duplicate_multiplicity_histogram=dict(Counter(len(v) for v in groups.values())),
        conflicting_duplicate_labels=[label for label,values in groups.items() if any(v!=values[0] for v in values)],
        original_order_decreases=sum(b<a for a,b in zip(labels,labels[1:])),
        native_field_unknowns_or_unreviewed_text=field_errors,
        native_2355_label_present=bool(last),native_2355_original_quantity_texts=qty,
        native_2355_quantity_known_positive=quantity_known,
        source_unknown_fields_are_not_a_claim_of_corrupt_oi=True,
        no_source_sort_fill_deletion_clock_or_value_change=True)


def main():
    records=source.collect(source.targets());originals=source.targets();selected={};hashes={}
    for key,r in records.items():
        root=key.split('_retry_')[0]
        if root not in originals or r['url']!=originals[root]:raise ValueError('Foreign inventory source request')
        body=retained(source.DATA,r)
        if body is not None:hashes['r26_sources/'+r['path']]=r['sha256']
        if r['status']==200 and root not in selected:selected[root]=(body,r,key)
        if r.get('inherited'):
            oldpath=HERE/r['original_manifest'];old=json.loads(oldpath.read_text())['sources'][r['original_record']]
            if hashlib.sha256(oldpath.read_bytes()).hexdigest()!=r['original_manifest_sha256'] or old['sha256']!=r['sha256']:
                raise ValueError('Inherited original provenance changed')
    if set(selected)!=set(originals):raise ValueError('Unavailable required original counterpart; no feature/P&L')
    coverage=[];failures=[]
    for day in source.required_days():
        role='BTCUSDT_'+day;body,r,key=selected[role];check,c,check_key=selected[role+'_checksum']
        native=verified_zip(body,check,f'BTCUSDT-metrics-{day}.zip')
        facts=describe(native,day)
        try:
            parse_day(native,'BTCUSDT',day,r['sha256']);gate_pass=True;error=None
        except ValueError as exc:
            gate_pass=False;error=str(exc);failures.append(dict(day=day,strict_source_gate_error=error))
        coverage.append(dict(file_day=day,original_zip_record=key,checksum_record=check_key,
            zip_sha256=r['sha256'],checksum_sha256=c['sha256'],strict_preregistered_source_gate_pass=gate_pass,
            strict_source_gate_error=error,**facts))
    hashes['r26_sources/manifest.json']=hashlib.sha256((source.DATA/'manifest.json').read_bytes()).hexdigest()
    result=dict(source_stage_only=True,new_performance=False,registered_alpha_hypothesis=True,
        alpha_cohort_or_outcomes_evaluated=False,adopted=False,protocol_commit='2131eaf',
        original_request_roles=len(originals),retained_attempts=len(records),
        inherited_original_records=sum(bool(r.get('inherited')) for r in records.values()),
        new_http_attempts=sum(not r.get('inherited',False) for r in records.values()),
        status_counts=dict(Counter(str(r['status']) for r in records.values())),
        source_days=len(coverage),strict_source_gate_pass_days=sum(v['strict_preregistered_source_gate_pass'] for v in coverage),
        strict_source_gate_failure_days=len(failures),strict_source_gate_errors=dict(Counter(v['strict_source_gate_error'] for v in failures)),
        required_full_daily_calendar_failure_days=sum(bool(v['missing_required_labels'] or v['extra_declared_labels']) for v in coverage),
        days_with_unknown_or_unreviewed_original_fields=sum(bool(v['native_field_unknowns_or_unreviewed_text']) for v in coverage),
        days_missing_exact_required_2355_label=sum(not v['native_2355_label_present'] for v in coverage),
        days_exact_2355_quantity_not_known_positive=sum(not v['native_2355_quantity_known_positive'] for v in coverage),
        days_with_conflicting_original_duplicates=sum(bool(v['conflicting_duplicate_labels']) for v in coverage),
        source_gate_permits_full_alpha_screen=not failures,source_failures=failures,full_original_source_coverage=coverage,
        retained_inputs_sha256=hashes,code_sha256={name:hashlib.sha256((HERE/name).read_bytes()).hexdigest() for name in
            ('R26_SOURCE_PLAN.md','R26_PROTOCOL.md','r26_sources.py','r26_inventory.py','r26_audit.py','test_r26_sources.py','test_r26_inventory.py')},
        original_source_values_labels_order_duplicates_unknowns_and_capture_clocks_retained=True,
        no_source_failure_converted_to_zero_cash_or_fitted_repair=True,
        no_sign_horizon_label_day_window_asset_fee_risk_or_missingness_rescue=True,
        no_strategy_bot_image_or_independent_forward_change=True,
        historical_actual_measurement_units_first_public_receipts_and_original_versions_certified=False,
        interpretation='This audits the exact preregistered source requirements. Blank unrelated ratios do not prove OI corrupt; known exact stock endpoints do not establish a complete declared day or public vintage. With any gate failure R26 has no valid cohort/P&L, rather than a zero-return or losing-alpha result. Preserve the unavailable fixed hypothesis and use a distinct feasible information mechanism.')
    text=json.dumps(result,indent=2,sort_keys=True,default=lambda v:v.isoformat())+'\n';path=HERE/'r26_source_audit.json'
    if path.exists() and path.read_text()!=text:raise ValueError('Frozen native inventory coverage audit changed')
    path.write_text(text)
    print(json.dumps({k:result[k] for k in ('source_days','strict_source_gate_pass_days','strict_source_gate_failure_days',
        'strict_source_gate_errors','days_missing_exact_required_2355_label','source_gate_permits_full_alpha_screen')}))


if __name__=='__main__':main()
