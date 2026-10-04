"""Separate seven-role native unit-boundary probe; original source bodies untouched."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from threading import local
from urllib.parse import urlencode

import requests

DATA = Path(__file__).resolve().parent/'r31_units_sources'


def targets():
    result = dict(unit_versions='https://alfred.stlouisfed.org/category?cid=123&ob=pv&od=desc&t=balance')
    for release,vintage in (('2025-11-06','2025-11-08'),('2025-11-13','2025-11-15')):
        result['release_metadata_'+release] = 'https://alfred.stlouisfed.org/release?'+urlencode(dict(rid=20,rd=release,ob='pv',od='desc',t='balance'))
        result['publisher_'+release] = 'https://www.federalreserve.gov/releases/h41/'+release.replace('-','')+'/'
        day = date.fromisoformat(vintage)
        result['archive_'+vintage] = 'https://alfred.stlouisfed.org/graph/alfredgraph.csv?'+urlencode(dict(
            id='WRESBAL',vintage_date=vintage,cosd=(day-timedelta(days=41)).isoformat(),coed=vintage))
    if len(result)!=7: raise ValueError('Native unit-plan role count differs')
    return result


def collect(fetch=False):
    DATA.mkdir(exist_ok=True); (DATA/'raw').mkdir(exist_ok=True)
    path=DATA/'manifest.json'
    records=json.loads(path.read_text())['sources'] if path.exists() else {}
    scope=targets();pending=[]
    if set(records)-set(scope):raise ValueError('Unregistered extra native unit role')
    for role,url in scope.items():
        if role in records:
            r=records[role]
            if r['url']!=url:raise ValueError('Original unit request identity differs')
            if r.get('path'):
                body=(DATA/r['path']).read_bytes()
                if hashlib.sha256(body).hexdigest()!=r['sha256']:raise ValueError('Original unit native body changed')
        elif fetch:pending.append((role,url))
        else:raise ValueError('Explicit original unit fetch required '+role)
    clients=local()

    def get(item):
        role,url=item;start=datetime.now(timezone.utc).isoformat()
        try:
            if not hasattr(clients,'http'):clients.http=requests.Session()
            response=clients.http.get(url,timeout=(25,25));body=response.content
            raw=DATA/'raw'/hashlib.sha256((role+' '+url).encode()).hexdigest()
            if raw.exists():raise ValueError('Unregistered native unit body exists')
            raw.write_bytes(body)
            return role,dict(url=url,final_url=response.url,status=response.status_code,
                requested_utc=start,retrieved_utc=datetime.now(timezone.utc).isoformat(),
                path='raw/'+raw.name,bytes=len(body),sha256=hashlib.sha256(body).hexdigest(),
                headers={k:response.headers[k] for k in ('Date','Content-Type','Last-Modified','ETag') if k in response.headers})
        except requests.RequestException as exc:
            return role,dict(url=url,status=None,path=None,requested_utc=start,
                failed_utc=datetime.now(timezone.utc).isoformat(),source_error=type(exc).__name__+': '+str(exc))

    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in as_completed([pool.submit(get,item) for item in pending]):
            role,record=future.result();records[role]=record
            tmp=path.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(dict(unit_plan_commit='cc1af5b',source_only=True,
                no_alpha_rule_cohort_weights_or_performance=True,sources=records),indent=2,sort_keys=True)+'\n')
            tmp.replace(path);print(role,record['status'],record.get('bytes',0),flush=True)
    return records


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--fetch',action='store_true')
    args=parser.parse_args();collect(args.fetch)
