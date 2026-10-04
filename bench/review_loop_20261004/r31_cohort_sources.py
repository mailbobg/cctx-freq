"""Frozen full reserve-quantity vintages with exact original capture inheritance."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from threading import Lock, local
import time
from urllib.parse import urlencode

import pandas as pd
import requests

from r10_gold import WINDOWS
from r20_audit import retained

HERE=Path(__file__).resolve().parent
DATA=HERE/'r31_cohort_sources'
PROTOCOL_COMMIT='c1ae33b'


def decision_pairs():
    out=[]
    for window,start,end in WINDOWS:
        for monday in pd.date_range(start,end,freq='W-MON',tz='UTC'):
            out.append(dict(window=window,monday=str(monday.date()),vintage=str((monday-pd.Timedelta(days=2)).date())))
    if len(out)!=292:raise ValueError('Original decision scope differs')
    return out


def targets():
    out={}
    for pair in decision_pairs():
        v=pd.Timestamp(pair['vintage'])
        out['archive_'+pair['vintage']]='https://alfred.stlouisfed.org/graph/alfredgraph.csv?'+urlencode(dict(
            id='WRESBAL',vintage_date=pair['vintage'],cosd=str((v-pd.Timedelta(days=41)).date()),coed=pair['vintage']))
    if len(out)!=292:raise ValueError('Duplicate original Saturday versions')
    return out


def collect(fetch=False,retry_transport=False):
    if fetch and retry_transport:raise ValueError('Original pass and explicit retry are separate')
    DATA.mkdir(exist_ok=True);(DATA/'raw').mkdir(exist_ok=True)
    path=DATA/'manifest.json';records=json.loads(path.read_text())['sources'] if path.exists() else {}
    fixed=targets();parents={}
    for folder in ('r31_sources','r31_units_sources'):
        p=HERE/folder/'manifest.json'
        parents[folder]=dict(manifest_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),records=json.loads(p.read_text())['sources'])

    def save():
        tmp=path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(dict(protocol_commit=PROTOCOL_COMMIT,
            original_parent_manifest_sha256={k:v['manifest_sha256'] for k,v in parents.items()},
            sources=records,no_new_outcomes=True),indent=2,sort_keys=True)+'\n');tmp.replace(path)

    for role,url in fixed.items():
        if role in records:
            if records[role]['url']!=url:raise ValueError('Original cohort URL differs')
            retained(DATA,records[role])
            continue
        matches=[(folder,info['records'][role]) for folder,info in parents.items()
                 if role in info['records'] and info['records'][role]['status']==200]
        if matches:
            if len(matches)!=1:raise ValueError('Ambiguous original capture inheritance')
            folder,original=matches[0]
            if original['url']!=url:raise ValueError('Inherited source request identity differs')
            body=retained(HERE/folder,original)
            raw=DATA/'raw'/hashlib.sha256((role+' '+url).encode()).hexdigest()
            if raw.exists():raise ValueError('Unregistered inherited raw body exists')
            raw.write_bytes(body)
            records[role]=dict(original,path='raw/'+raw.name,inherited=True,original_manifest=folder+'/manifest.json',
                original_manifest_sha256=parents[folder]['manifest_sha256'],original_record=role)
            save()
        elif not fetch:raise ValueError('Explicit original full fetch required '+role)
    pending=[(role,url) for role,url in fixed.items() if role not in records] if fetch else []
    if retry_transport:
        for role,url in fixed.items():
            attempts=[(key,r) for key,r in records.items() if key.split('_retry_')[0]==role]
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
            if raw.exists():raise ValueError('Unregistered original cohort native body exists')
            raw.write_bytes(body)
            return role,dict(url=url,final_url=response.url,status=response.status_code,
                path='raw/'+raw.name,bytes=len(body),sha256=hashlib.sha256(body).hexdigest(),
                requested_utc=start,retrieved_utc=datetime.now(timezone.utc).isoformat(),inherited=False,
                headers={k:response.headers[k] for k in ('Date','Content-Type','Last-Modified','ETag') if k in response.headers})
        except requests.RequestException as exc:
            return role,dict(url=url,status=None,path=None,requested_utc=start,
                failed_utc=datetime.now(timezone.utc).isoformat(),source_error=type(exc).__name__+': '+str(exc),inherited=False)

    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in as_completed([pool.submit(get,item) for item in pending]):
            role,record=future.result();records[role]=record;save()
            print(role,record['status'],record.get('bytes',0),flush=True)
    for role,r in records.items():
        root=role.split('_retry_')[0]
        if root not in fixed or r['url']!=fixed[root]:raise ValueError('Unregistered full version/retry request')
        retained(DATA,r)
        if r.get('inherited'):
            folder=r['original_manifest'].split('/')[0];info=parents[folder];old=info['records'][r['original_record']]
            if (r['original_manifest_sha256']!=info['manifest_sha256'] or r['sha256']!=old['sha256'] or
                r['requested_utc']!=old['requested_utc'] or r['retrieved_utc']!=old['retrieved_utc']):
                raise ValueError('Inherited original capture provenance differs')
    return records


def selected():
    records=collect();out={}
    for role in targets():
        good=sorted((k,r) for k,r in records.items() if k.split('_retry_')[0]==role and r['status']==200)
        if not good:raise ValueError('Required native reserve version unavailable '+role)
        name,record=good[0];out[role]=(retained(DATA,record),record,name)
    return out


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--fetch',action='store_true');parser.add_argument('--retry-transport',action='store_true')
    args=parser.parse_args();collect(args.fetch,args.retry_transport)
