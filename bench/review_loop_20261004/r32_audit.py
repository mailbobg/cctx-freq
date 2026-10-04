"""Publisher-edition/calendar source feasibility, without allocation or P&L."""
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import re

from r20_audit import retained
from r31_cohort_sources import decision_pairs
from r31_native import parse_publisher
from r32_calendar import parse_calendar, report_for_observation
from r32_sources import DATA, collect

HERE=Path(__file__).resolve().parent


def audit():
    records=collect()
    if len(records)!=18 or any(r['status']!=200 for r in records.values()):
        raise ValueError('Every frozen forensic source role is required')
    native=retained(DATA,records['calendar_json'])
    captured=datetime.fromisoformat(records['calendar_json']['retrieved_utc']).date().isoformat()
    calendar=parse_calendar(native,captured)
    reports={role[10:]:parse_publisher(retained(DATA,r),role[10:]) for role,r in records.items()
             if re.fullmatch(r'publisher_\d{4}-\d{2}-\d{2}',role)}
    if len(reports)!=14:raise ValueError('Original report probe scope differs')
    for release,report in reports.items():
        selected=report_for_observation(calendar,report['observation_date'])
        if selected['release_date']!=release or records['publisher_'+release]['url']!=selected['report_url']:
            raise ValueError('Dated report is not the first actual listed Thursday/Friday edition')
    previous=json.loads((HERE/'r31_version_aware_source_audit.json').read_text())
    comparisons=[]
    for old in previous['native_version_aware_counterparts']:
        report=reports[old['release']]
        if (report['observation_date']!=old['observation_date'] or
            report['week_average_million_usd']!=old['publisher_native_million_usd']):
            raise ValueError('Inherited publisher source value/date changed')
        comparisons.append(dict(**old,edition_comparison_not_general_revision_equivalence=True))
    pairs=[];required={};unavailable=[]
    for item in decision_pairs():
        monday=date.fromisoformat(item['monday'])
        current=monday-timedelta(days=5);prior=current-timedelta(days=28)
        row=dict(window=item['window'],monday=item['monday'])
        for kind,observation in (('current',current),('prior',prior)):
            try:chosen=report_for_observation(calendar,observation.isoformat())
            except ValueError as exc:
                nearby=[c for d,c in calendar['releases'].items()
                        if abs((date.fromisoformat(d)-observation).days)<8]
                row[kind]=dict(observation_date=observation.isoformat(),source_known=False,
                    source_error=str(exc),actual_nearby_calendar_editions=nearby)
                unavailable.append(dict(window=item['window'],monday=item['monday'],kind=kind,**row[kind]))
            else:
                row[kind]=dict(observation_date=observation.isoformat(),source_known=True,**chosen)
                required[chosen['release_date']]=chosen
        pairs.append(row)
    initial=json.loads((HERE/'r31_initial_cohort_contract_failure.json').read_text())
    failed_manifest=HERE/'r31_failed_cohort_snapshot.json'
    # Source evidence is new; the original failed cohort and its missing cell are immutable inputs.
    old_records=json.loads(failed_manifest.read_text())['sources']
    missing=old_records['archive_2025-04-05']
    old_body=retained(HERE/'r31_cohort_sources',missing)
    from r31_native import parse_archive
    old_rows=parse_archive(old_body,'2025-04-05')['rows']
    if '2025-04-02' in old_rows:raise ValueError('Original missing R31 cell was silently filled')
    hashes={'r32_sources/manifest.json':hashlib.sha256((DATA/'manifest.json').read_bytes()).hexdigest(),
            'r31_version_aware_source_audit.json':hashlib.sha256((HERE/'r31_version_aware_source_audit.json').read_bytes()).hexdigest(),
            'r31_failed_cohort_snapshot.json':hashlib.sha256(failed_manifest.read_bytes()).hexdigest(),
            'r31_initial_cohort_contract_failure.json':hashlib.sha256((HERE/'r31_initial_cohort_contract_failure.json').read_bytes()).hexdigest(),
            'r31_cohort_sources/'+missing['path']:missing['sha256']}
    for role,r in records.items():
        hashes['r32_sources/'+r['path']]=r['sha256']
        if r.get('inherited'):hashes[r['original_manifest']]=r['original_manifest_sha256']
    return dict(source_stage_only=True,new_economic_outcomes=False,source_plan_commit='89bba77',
        original_http200_roles=18,inherited_roles=sum(bool(r.get('inherited')) for r in records.values()),
        native_calendar=calendar,original_reports=reports,native_version_counterparts=comparisons,
        required_decision_pairs=pairs,required_report_editions=required,
        required_cells=584,calendar_selected_cells=584-len(unavailable),
        calendar_unavailable_cells=unavailable,
        calendar_only_required_editions=len(required),full_report_cohort_not_yet_collected=True,
        forensic_dated_report_calendar_gate=True,calendar_only_required_publication_gate=not unavailable,
        missing_r31_cell_still_absent=True,original_r31_failed_source_sha256=missing['sha256'],
        r31_original_failure_unchanged=initial,first_public_receipt_or_unchanged_original_edition_certified=False,
        provisional_dated_publisher_research_only=True,adopted=False,native_input_sha256=hashes)


if __name__=='__main__':
    result=audit()
    (HERE/'r32_source_audit.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print(json.dumps({k:result[k] for k in ('original_http200_roles','inherited_roles',
        'calendar_only_required_editions','forensic_dated_report_calendar_gate',
        'calendar_only_required_publication_gate','missing_r31_cell_still_absent','adopted')},indent=2))
