"""Replay actual public metric sources; do not infer historical publication or P&L."""
import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
from pathlib import Path
import re

import r20_sources as source

HERE=Path(__file__).resolve().parent
FIELDS=('CapMVRVCur','SplyCur','FeeTotNtv','AssetCompletionTime','AssetEODCompletionTime')


def utc(value):
    dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    if dt.tzinfo is None:raise ValueError('Timezone missing')
    return dt.astimezone(timezone.utc)


def retained(folder,record):
    start=utc(record['requested_utc'])
    end=utc(record['retrieved_utc'] if record['status'] is not None else record['failed_utc'])
    if start>end:raise ValueError('Response precedes request')
    if record['path'] is None:
        if record['status'] is not None:raise ValueError('HTTP response body absent')
        return None
    p=Path(record['path'])
    if p.is_absolute() or '..' in p.parts or len(p.parts)!=2 or p.parts[0]!='raw':raise ValueError('Unreviewed raw path')
    body=(folder/p).read_bytes()
    if len(body)!=record['bytes'] or hashlib.sha256(body).hexdigest()!=record['sha256']:raise ValueError('Retained original body changed')
    return body


def native(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d+(?:\.\d+)?',value):raise ValueError('Invalid native decimal text')
    try:number=Decimal(value)
    except InvalidOperation as exc:raise ValueError('Invalid metric') from exc
    if not number.is_finite() or number<0:raise ValueError('Invalid metric')
    return number


def parse_btc_csv(body):
    reader=csv.reader(io.StringIO(body.decode('utf-8')));header=next(reader)
    if len(header)!=len(set(header)) or header[0]!='time' or not set(FIELDS).issubset(header):raise ValueError('CSV schema differs')
    rows={};last=None
    for fields in reader:
        if len(fields)!=len(header):raise ValueError('CSV column count differs')
        row=dict(zip(header,fields));day=date.fromisoformat(row['time'])
        if day.isoformat()!=row['time'] or last is not None and day<=last:raise ValueError('CSV native dates duplicated or unordered')
        rows[row['time']]={key:native(row[key]) if row[key]!='' else None for key in FIELDS};last=day
    if not rows:raise ValueError('Empty BTC source')
    return rows


def api_sample(body,status,day):
    # Failure, unsupported key, null database value and actual decimal zero differ.
    j=json.loads(body)
    if status!=200:
        if not isinstance(j.get('error'),dict):raise ValueError('HTTP failure without structured source error')
        return dict(response_state=j['error']['type'],source_error=j['error'],values=None,
                    first_public_available_utc=None,historical_value_version_certified=False)
    data=j.get('data')
    if not isinstance(data,list) or len(data)!=1 or j.get('next_page_url'):raise ValueError('Incomplete/duplicate fixed sample')
    row=data[0]
    if row.get('asset')!='btc' or row.get('time') not in (day+'T00:00:00.000000000Z',day+'T00:00:00Z'):raise ValueError('Observation identity differs')
    states={};values={}
    for key in FIELDS:
        states[key]='unsupported_metric' if key not in row else 'no_database_value' if row[key] is None else 'value'
        values[key]=native(row[key]) if states[key]=='value' else None
    completion=None
    value=values['AssetEODCompletionTime']
    if value is not None:
        if value!=value.to_integral_value() or value<=0:raise ValueError('Invalid epoch seconds')
        completion=datetime.fromtimestamp(int(value),timezone.utc).isoformat()
    return dict(response_state='sample',values=values,states=states,observation_label=row['time'],
                completion_utc=completion,first_public_available_utc=None,historical_value_version_certified=False)


def main():
    data=source.DATA;manifest=json.loads((data/'manifest.json').read_text());records=manifest['sources']
    expected={**source.initial_targets(),**source.history_targets(),**source.context_targets(),
              'public_metric_catalog_all':'https://community-api.coinmetrics.io/v4/catalog/metrics'}
    if set(records)!=set(expected):raise ValueError('Unreviewed/missing source request')
    bodies={}
    for key,record in records.items():
        if record['url']!=expected[key]:raise ValueError('Source request identity changed')
        bodies[key]=retained(data,record)
    for key in expected:
        intended=403 if key.startswith('api_btc_') else 400 if key=='public_metric_catalog' else 200
        if records[key]['status']!=intended:raise ValueError('Observed source status changed '+key)
    repo=json.loads(bodies['repo_identity']);tree=json.loads(bodies['current_tree']);commits=json.loads(bodies['latest_commits'])
    if repo['id']!=137470543 or repo['full_name']!='coinmetrics/data' or repo['private'] or tree['truncated']:raise ValueError('Repository identity/visibility/tree differs')
    if len(commits)!=3 or commits[0]['sha']!=tree['sha']:raise ValueError('Current tree ref/commit cohort differs')
    paths={entry['path']:entry for entry in tree['tree']}
    for key,path in [('current_btc_csv','csv/btc.csv'),('current_readme','README.md'),('publisher_script_0','scripts/generate.js')]:
        body=bodies[key]
        if hashlib.sha1(b'blob '+str(len(body)).encode()+b'\0'+body).hexdigest()!=paths[path]['sha']:raise ValueError('Git content object identity differs')
    rows=parse_btc_csv(bodies['current_btc_csv']);days=[date.fromisoformat(day) for day in rows]
    gaps=[dict(previous=a.isoformat(),following=b.isoformat(),days=(b-a).days) for a,b in zip(days,days[1:]) if b-a!=timedelta(days=1)]
    probes=[]
    for day in source.DATES:
        denied=api_sample(bodies['api_btc_'+day],403,day)
        sample=api_sample(bodies['api_free_'+day],200,day)
        if denied['response_state']!='forbidden' or 'CapRealUSD' not in denied['source_error']['message']:raise ValueError('Permission error role differs')
        if day not in rows:raise ValueError('CSV probe label missing')
        counterpart=rows[day]
        differences={key:dict(csv=counterpart[key],api=sample['values'][key],api_minus_csv=sample['values'][key]-counterpart[key])
                     for key in FIELDS if sample['values'][key] is not None and counterpart[key] is not None and sample['values'][key]!=counterpart[key]}
        history={kind:json.loads(bodies[kind+day]) for kind in ('history_','history_repo_')}
        probes.append(dict(day=day,realized_cap_access=denied,sample=sample,csv_values=counterpart,differences=differences,
                           api_capture_utc=records['api_free_'+day]['retrieved_utc'],historical_commits_returned=history,
                           usable_at_past_decision_certified=False))
    catalog=json.loads(bodies['public_metric_catalog_all'])
    if not isinstance(catalog.get('data'),list) or catalog.get('next_page_url'):raise ValueError('Incomplete metric catalog')
    selected={item['metric']:item for item in catalog['data'] if item['metric'] in FIELDS}
    if set(selected)!=set(FIELDS):raise ValueError('Metric definition/support missing')
    metric_scope={key:dict(unit=item['unit'],btc_daily_supported=any(f['frequency']=='1d' and 'btc' in f['assets'] for f in item['frequencies']),
                           reviewable_field=item.get('reviewable'),reviewable_field_present='reviewable' in item)
                  for key,item in selected.items()}
    output=dict(source_only=True,new_alpha_trial=False,new_performance=False,adopted=False,
        plan_commit='d62991d',source_responses=len(records),status_counts={str(s):sum(r['status']==s for r in records.values()) for s in sorted(set(r['status'] for r in records.values()))},
        current_repository=dict(id=repo['id'],created_at_label=repo['created_at'],public_now=True,
            resolved_revision_sha=commits[0]['sha'],native_git_tree_sha=commits[0]['commit']['tree']['sha'],
            tree_api_returned_ref_sha=tree['sha'],latest_committer_date_label=commits[0]['commit']['committer']['date'],
            author_committer_labels_are_not_first_public_timestamps=True,
            blob_identities_verified=True,publisher_script_inspected_not_executed=True),
        csv=dict(rows=len(rows),first_day=days[0].isoformat(),last_day=days[-1].isoformat(),calendar_gaps=gaps,
            schema=list(next(csv.reader(io.StringIO(bodies['current_btc_csv'].decode())))),
            blank_counts={key:sum(row[key] is None for row in rows.values()) for key in FIELDS},
            covers_recent_full_window=days[-1]>=date(2026,8,31)),
        public_metric_scope=metric_scope,probes=probes,
        unsupported_catalog_request=dict(status=400,error=json.loads(bodies['public_metric_catalog'])['error'],
             correction='Retain invalid page_size request; ordinary documented unfiltered catalog requested separately, not a permission bypass.'),
        stablecoin_paths_only=[path for path in paths if re.fullmatch(r'csv/(?:usdt|usdc)(?:_[^/]+)?\.csv',path)],
        definitions_and_limits=['MVRV is the ratio of market cap to last-on-chain-movement realized capitalization, not an actual purchase-cost ledger.',
            'The native supply metric describes ledger-visible units; an exact100BTC discrepancy in three samples has no authenticated revision explanation here.',
            'Completion time indicates calculation completion, not authenticated first publication or a version identifier.',
            'Current values/compatible clocks do not authenticate historical knowledge; no clock is backdated from Git commit labels.',
            'Repository-wide/path history queries at all three fixed cutoffs return empty. This does not prove the project never published older data or that history was rewritten.',
            'CSV current-snapshot endMay2026 and missing historical content/publication versions prevent this full-window PIT screen.',
            'Matching MVRV/fee values at three dates do not prove all historical values were immutable.',
            'Native-chain stablecoin names are not an authenticated complete historical monetary aggregate; no series is summed.'],
        verdict='Source probe cannot support source-faithful full-window historical strategy selection. No alpha hypothesis, rule, backtest or runtime change.',
        next_action='Use a distinct source-feasible information mechanism; retain this failed source entrance and exact original responses.',
        code_sha256={n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in ('R20_SOURCE_PLAN.md','r20_sources.py','r20_audit.py','test_r20_audit.py')},
        retained_body_sha256={key:record['sha256'] for key,record in records.items() if record.get('path')})
    (HERE/'r20_source_audit.json').write_text(json.dumps(output,indent=2,default=str,sort_keys=True)+'\n')
    print('R20 retained original source/decimal/clock audit complete; no historical alpha or P&L trial.')


if __name__=='__main__':main()
