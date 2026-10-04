"""Exact calendar-planned original report cohort, without allocation/outcomes."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
from threading import Lock,local
import time
import requests
from r20_audit import retained
from r33_calendar import targets

HERE=Path(__file__).resolve().parent
DATA=HERE/'r33_sources'
PROTOCOL_COMMIT='ff39936'


def collect(fetch=False,retry_transport=False):
    if fetch and retry_transport:raise ValueError('Original requests and explicit retries are separate')
    plan=json.loads((HERE/'r33_planned_sources.json').read_text())
    fixed=targets()
    if plan['protocol_commit']!=PROTOCOL_COMMIT or plan['exact_original_urls']!=fixed:
        raise ValueError('Frozen actual report request plan differs')
    if hashlib.sha256((HERE/'r32_source_audit.json').read_bytes()).hexdigest()!=plan['calendar_source_sha256']:
        raise ValueError('Original native calendar observation changed')
    parents={}
    for folder in ('r32_sources','r32_delayed_source'):
        f=HERE/folder/'manifest.json';x=json.loads(f.read_text())
        records=x['sources'] if 'sources' in x else {'publisher_2025-12-29':x['source']}
        parents[folder]=dict(manifest_sha256=hashlib.sha256(f.read_bytes()).hexdigest(),records=records)
    DATA.mkdir(exist_ok=True);(DATA/'raw').mkdir(exist_ok=True)
    path=DATA/'manifest.json';records=json.loads(path.read_text())['sources'] if path.exists() else {}

    def save():
        temp=path.with_suffix('.json.tmp')
        temp.write_text(json.dumps(dict(protocol_commit=PROTOCOL_COMMIT,source_only=True,no_new_outcomes=True,
            frozen_request_plan_sha256=hashlib.sha256((HERE/'r33_planned_sources.json').read_bytes()).hexdigest(),
            original_parent_manifest_sha256={k:v['manifest_sha256'] for k,v in parents.items()},sources=records),indent=2,sort_keys=True)+'\n')
        temp.replace(path)

    for role,url in fixed.items():
        if role in records:
            if records[role]['url']!=url:raise ValueError('Original report URL differs')
            retained(DATA,records[role]);continue
        inherited=[(folder,x['records'][role]) for folder,x in parents.items()
                   if role in x['records'] and x['records'][role]['status']==200]
        if inherited:
            if len(inherited)!=1:raise ValueError('Ambiguous original report inheritance')
            folder,old=inherited[0]
            if old['url']!=url:raise ValueError('Inherited source identity differs')
            raw=DATA/'raw'/hashlib.sha256((role+' '+url).encode()).hexdigest()
            if raw.exists():raise ValueError('Unregistered original raw body exists')
            raw.write_bytes(retained(HERE/folder,old))
            records[role]=dict(old,path='raw/'+raw.name,inherited=True,original_manifest=folder+'/manifest.json',
                original_manifest_sha256=parents[folder]['manifest_sha256'],original_record=role)
            save()
        elif not fetch:raise ValueError('Explicit original full source fetch required '+role)
    pending=[(r,u) for r,u in fixed.items() if r not in records] if fetch else []
    if retry_transport:
        for role,url in fixed.items():
            attempts=[(k,r) for k,r in records.items() if k.split('_retry_')[0]==role]
            if records[role]['status'] is None and not any(r['status'] is not None for _,r in attempts):
                pending.append((role+'_retry_'+str(len(attempts)),url))
    clients=local();dispatch=Lock();next_request=0.0

    def get(item):
        nonlocal next_request
        role,url=item
        with dispatch:
            delay=max(0,next_request-time.monotonic())
            if delay:time.sleep(delay)
            next_request=time.monotonic()+0.6
        start=datetime.now(timezone.utc).isoformat()
        try:
            if not hasattr(clients,'http'):clients.http=requests.Session()
            response=clients.http.get(url,timeout=(25,25));body=response.content
            raw=DATA/'raw'/hashlib.sha256((role+' '+url).encode()).hexdigest()
            if raw.exists():raise ValueError('Unregistered new raw source body exists')
            raw.write_bytes(body)
            return role,dict(url=url,final_url=response.url,status=response.status_code,path='raw/'+raw.name,
                bytes=len(body),sha256=hashlib.sha256(body).hexdigest(),requested_utc=start,
                retrieved_utc=datetime.now(timezone.utc).isoformat(),inherited=False,
                headers={k:response.headers[k] for k in ('Date','Content-Type','Last-Modified','ETag') if k in response.headers})
        except requests.RequestException as exc:
            return role,dict(url=url,status=None,path=None,requested_utc=start,
                failed_utc=datetime.now(timezone.utc).isoformat(),source_error=type(exc).__name__+': '+str(exc),inherited=False)

    with ThreadPoolExecutor(max_workers=2) as pool:
        for f in as_completed([pool.submit(get,item) for item in pending]):
            role,record=f.result();records[role]=record;save()
            print(role,record['status'],record.get('bytes',0),flush=True)
    for role,r in records.items():
        if role.split('_retry_')[0] not in fixed or r['url']!=fixed[role.split('_retry_')[0]]:
            raise ValueError('Foreign original source/retry')
        retained(DATA,r)
        if r.get('inherited'):
            folder=r['original_manifest'].split('/')[0];parent=parents[folder];old=parent['records'][r['original_record']]
            if r['original_manifest_sha256']!=parent['manifest_sha256'] or any(r[k]!=old[k] for k in ('url','sha256','bytes','requested_utc','retrieved_utc','status')):
                raise ValueError('Original native report capture provenance differs')
    return records


def selected():
    records=collect();out={}
    for role in targets():
        known=sorted((k,r) for k,r in records.items() if k.split('_retry_')[0]==role and r['status']==200)
        if not known:raise ValueError('Required original publisher source unavailable '+role)
        name,r=known[0];out[role]=(retained(DATA,r),r,name)
    return out


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--fetch',action='store_true');p.add_argument('--retry-transport',action='store_true')
    a=p.parse_args();collect(a.fetch,a.retry_transport)
