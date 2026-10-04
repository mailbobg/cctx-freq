"""Original report/calendar source probe with exact inherited native captures."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import requests
from r20_audit import retained
from r31_sources import PROBES

HERE=Path(__file__).resolve().parent
DATA=HERE/'r32_sources'


def targets():
    result={'calendar_json':'https://www.federalreserve.gov/releases/h41/releaseDates.json'}
    for release in [d for _,current,prior in PROBES for d in (current,prior)]+['2025-11-06','2025-11-13','2025-04-03','2025-03-06']:
        result['publisher_'+release]='https://www.federalreserve.gov/releases/h41/'+release.replace('-','')+'/'
    result.update(publisher_calendar='https://www.federalreserve.gov/releases/h41/default.htm',
                  publisher_explanation='https://www.federalreserve.gov/RELEASES/h41/about.htm',
                  publisher_processing='https://www.federalreserve.gov/releases/h41/perfeval2012.htm')
    if len(result)!=18:raise ValueError('Original report source-plan scope differs')
    return result


def collect(fetch=False):
    DATA.mkdir(exist_ok=True);(DATA/'raw').mkdir(exist_ok=True)
    path=DATA/'manifest.json';records=json.loads(path.read_text())['sources'] if path.exists() else {}
    parents={}
    for folder in ('r31_sources','r31_units_sources'):
        p=HERE/folder/'manifest.json'
        parents[folder]=dict(hash=hashlib.sha256(p.read_bytes()).hexdigest(),records=json.loads(p.read_text())['sources'])
    scope=targets()
    if set(records)-set(scope):raise ValueError('Unregistered source role')

    def save():
        tmp=path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(dict(source_plan_commit='89bba77',source_stage_only=True,
            no_full_cohort_or_policy_or_performance=True,sources=records),indent=2,sort_keys=True)+'\n');tmp.replace(path)

    session=requests.Session()
    for role,url in scope.items():
        if role in records:
            if records[role]['url']!=url:raise ValueError('Original source request identity differs')
            retained(DATA,records[role]);continue
        matches=[(folder,x['records'][role]) for folder,x in parents.items() if role in x['records'] and x['records'][role]['status']==200]
        filename=hashlib.sha256((role+' '+url).encode()).hexdigest();raw=DATA/'raw'/filename
        if raw.exists():raise ValueError('Unregistered native source body exists')
        if matches:
            if len(matches)!=1:raise ValueError('Ambiguous old native capture')
            folder,old=matches[0]
            if old['url']!=url:raise ValueError('Inherited original URL differs')
            body=retained(HERE/folder,old);raw.write_bytes(body)
            records[role]=dict(old,path='raw/'+filename,inherited=True,original_manifest=folder+'/manifest.json',
                original_manifest_sha256=parents[folder]['hash'],original_record=role)
        elif not fetch:raise ValueError('Explicit original source fetch required '+role)
        else:
            start=datetime.now(timezone.utc).isoformat()
            try:
                response=session.get(url,timeout=(25,25));body=response.content;raw.write_bytes(body)
                records[role]=dict(url=url,final_url=response.url,status=response.status_code,
                    path='raw/'+filename,bytes=len(body),sha256=hashlib.sha256(body).hexdigest(),
                    requested_utc=start,retrieved_utc=datetime.now(timezone.utc).isoformat(),inherited=False,
                    headers={k:response.headers[k] for k in ('Date','Content-Type','Last-Modified','ETag') if k in response.headers})
            except requests.RequestException as exc:
                records[role]=dict(url=url,status=None,path=None,requested_utc=start,
                    failed_utc=datetime.now(timezone.utc).isoformat(),source_error=type(exc).__name__+': '+str(exc),inherited=False)
        save();print(role,records[role]['status'],'inherited',records[role]['inherited'],flush=True)
    for role,r in records.items():
        retained(DATA,r)
        if r.get('inherited'):
            folder=r['original_manifest'].split('/')[0];old=parents[folder]['records'][r['original_record']]
            if r['original_manifest_sha256']!=parents[folder]['hash'] or any(r[k]!=old[k] for k in ('url','sha256','bytes','requested_utc','retrieved_utc','status')):
                raise ValueError('Inherited original report provenance differs')
    return records


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--fetch',action='store_true')
    args=parser.parse_args();collect(args.fetch)
