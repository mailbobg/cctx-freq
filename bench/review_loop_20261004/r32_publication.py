"""Forensic actual Monday publication and independent original CSV cell join."""
import csv
from datetime import datetime
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from r20_audit import retained
from r31_native import NativeHTML,LABEL,compact,integer
from r31_units import parse_versions,version_units
from r32_delayed_source import DATA,collect

HERE=Path(__file__).resolve().parent


def parse_delayed_report(body):
    p=NativeHTML();p.feed(body.decode('utf-8'));whole=''.join(p.text)
    if compact('Release Date: December 29, 2025') not in compact(whole):
        raise ValueError('Native delayed release identity differs')
    c=p.cells
    matches=[k for k,v in c.items() if compact(v['text'])==compact(LABEL)]
    if len(matches)!=1:raise ValueError('Native quantity row identity differs')
    prefix=matches[0][:-2];context=p.tables[c[matches[0]]['table_index']]
    if compact('Millions of dollars') not in compact(context):raise ValueError('Native unit label differs')
    expected={'t2h1c2':'Averages of daily figures','t2h2c1':'Week endedDec 24, 2025',
              't2h1c3':'WednesdayDec 24, 2025','t2h3c1':'Dec 17, 2025','t2h3c2':'Dec 25, 2024'}
    if any(compact(c[k]['text'])!=compact(v) for k,v in expected.items()):
        raise ValueError('Native observation or column semantics differ')
    cells=[c[prefix+'c'+str(i)] for i in range(2,6)]
    refs=['t2r1c1 t2h1c2 t2h2c1','t2r1c1 t2h1c2 t2h2c2 t2h3c1',
          't2r1c1 t2h1c2 t2h2c2 t2h3c2','t2r1c1 t2h1c3']
    if any(' '.join(v['attributes'].get('header','').split())!=ref for v,ref in zip(cells,refs)):
        raise ValueError('Native column semantic references differ')
    values=list(map(lambda x:integer(x['text']),cells))
    return dict(release_date='2025-12-29',observation_date='2025-12-24',native_units='Millions of dollars',
        week_average_million_usd=values[0],signed_week_change_million_usd=values[1],
        signed_year_change_million_usd=values[2],wednesday_level_million_usd=values[3],
        native_header_cells={k:c[k] for k in expected},native_value_cells=cells,
        source_body_sha256=hashlib.sha256(body).hexdigest(),forensic_exception_only=True,
        no_generalized_or_relaxed_r31_parser=True)


def audit():
    record=collect()
    if record['status']!=200:raise ValueError('Actual delayed report unavailable')
    publisher=parse_delayed_report(retained(DATA,record))
    source=json.loads((HERE/'r32_source_audit.json').read_text())
    calendar=source['native_calendar']['releases']['2025-12-29']
    if calendar['report_url']!=record['url']:raise ValueError('Actual delayed report/calendar identity differs')
    old_manifest=HERE/'r31_cohort_sources/manifest.json'
    old=json.loads(old_manifest.read_text())['sources']['archive_2025-12-27']
    original=retained(HERE/'r31_cohort_sources',old)
    rows=list(csv.reader(io.StringIO(original.decode('utf-8'))))
    if rows[0]!=['observation_date','WRESBAL_20251227']:raise ValueError('Original CSV vintage identity differs')
    matches=[(i,r) for i,r in enumerate(rows) if r[0]=='2025-12-24']
    if len(matches)!=1 or len(matches[0][1])!=2:raise ValueError('Original exact required CSV cell missing/duplicated')
    index,fields=matches[0]
    unit_manifest=HERE/'r31_units_sources/manifest.json'
    unit_record=json.loads(unit_manifest.read_text())['sources']['unit_versions']
    unit_body=retained(HERE/'r31_units_sources',unit_record)
    units=version_units(parse_versions(unit_body),'2025-12-27')
    value=Decimal(fields[1])*units['exact_multiplier_to_million_usd']
    if value!=publisher['week_average_million_usd']:raise ValueError('Independent CSV/report value join differs')
    decision=datetime.fromisoformat('2025-12-29T00:00:00+00:00')
    eastern=decision.astimezone(ZoneInfo('America/New_York'))
    after=publisher['release_date']>eastern.date().isoformat()
    if not after:raise ValueError('Expected actual source-publication clock counterexample is absent')
    hashes={
        'r32_source_audit.json':hashlib.sha256((HERE/'r32_source_audit.json').read_bytes()).hexdigest(),
        'r32_delayed_source/manifest.json':hashlib.sha256((DATA/'manifest.json').read_bytes()).hexdigest(),
        'r32_delayed_source/'+record['path']:record['sha256'],
        'r31_cohort_sources/manifest.json':hashlib.sha256(old_manifest.read_bytes()).hexdigest(),
        'r31_cohort_sources/'+old['path']:old['sha256'],
        'r31_units_sources/manifest.json':hashlib.sha256(unit_manifest.read_bytes()).hexdigest(),
        'r31_units_sources/'+unit_record['path']:unit_record['sha256']}
    hashes.update(source['native_input_sha256'])
    return dict(anomaly_plan_commit='cbf2d76',source_only=True,publisher=publisher,calendar_entry=calendar,
        original_native_archive=dict(vintage='2025-12-27',observation_date=fields[0],native_value_text=fields[1],
            native_csv_row_index=index,native_units=units,normalized_million_usd=format(value,'f'),source_sha256=old['sha256']),
        archive_value_exactly_matches_publisher_average=True,
        modeled_decision_utc=decision.isoformat(),modeled_decision_eastern=eastern.isoformat(),
        source_publication_date_after_decision_eastern_date=after,
        current_http_last_modified_as_corrobation_only=record.get('headers',{}).get('Last-Modified'),
        current_headers_or_calendar_not_authenticated_first_receipt=True,
        vintage_label_is_not_sufficient_publication_clock_certificate=True,
        no_general_claim_all_archive_versions_wrong=True,
        full_required_source_gate=False,no_valid_full_report_policy_or_economic_outcomes=True,
        no_r31_rewrite_or_missing_cash_or_late_report_rescue=True,adopted=False,native_input_sha256=hashes)


if __name__=='__main__':
    x=audit();(HERE/'r32_publication_audit.json').write_text(json.dumps(x,indent=2,sort_keys=True)+'\n')
    print(json.dumps({k:x[k] for k in ('archive_value_exactly_matches_publisher_average',
        'modeled_decision_eastern','source_publication_date_after_decision_eastern_date',
        'vintage_label_is_not_sufficient_publication_clock_certificate','full_required_source_gate','adopted')},indent=2))
