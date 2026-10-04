"""Retain one frozen native inventory scope, with original inheritance receipts."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from threading import local

import requests
from r20_audit import retained

HERE=Path(__file__).resolve().parent
DATA=HERE/'r26_sources'
WINDOWS=(('2021-02-01','2023-12-31'),('2024-01-01','2026-08-31'))
INHERITED={'2020-12-30':('r24_sources','metrics_BTCUSDT_2020-12-30'),
    '2021-01-29':('r24_sources','metrics_BTCUSDT_2021-01-29'),
    '2026-06-24':('r23_sources','BTCUSDT_2026-06-24'),
    '2026-06-26':('r23_sources','BTCUSDT_2026-06-26')}


def monday_dates():
    result=[]
    for start,end in WINDOWS:
        day=datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
        stop=datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
        if day.weekday()!=0: raise ValueError('Frozen first decision must be Monday')
        while day<=stop: result.append(day);day+=timedelta(days=7)
    return result


def required_days():
    return sorted({(monday-timedelta(days=offset)).strftime('%Y-%m-%d')
                   for monday in monday_dates() for offset in (3,33)})


def targets():
    out={}
    for day in required_days():
        key='BTCUSDT_'+day
        url=f'https://data.binance.vision/data/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-{day}.zip'
        out[key]=url;out[key+'_checksum']=url+'.CHECKSUM'
    return out


def persist(path, records):
    tmp=path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(dict(source_stage_only=True,new_performance=False,
        protocol_commit='2131eaf',sources=records),indent=2,sort_keys=True)+'\n');tmp.replace(path)


def inherit(records, path):
    for day,(directory,oldkey) in INHERITED.items():
        oldpath=HERE/directory/'manifest.json';old=json.loads(oldpath.read_text())['sources']
        for suffix in ('','_checksum'):
            key='BTCUSDT_'+day+suffix;origin=oldkey+suffix
            if key in records:
                retained(DATA,records[key]);continue
            r=old[origin];body=retained(HERE/directory,r)
            if r['url']!=targets()[key] or r['status']!=200: raise ValueError('Inherited identity/status differs')
            name=hashlib.sha256((key+' '+r['url']).encode()).hexdigest();file=DATA/'raw'/name
            if file.exists() and file.read_bytes()!=body: raise ValueError('Inherited byte copy changed')
            file.write_bytes(body)
            records[key]=dict(r,path='raw/'+name,inherited=True,original_manifest=directory+'/manifest.json',
                original_record=origin,original_manifest_sha256=hashlib.sha256(oldpath.read_bytes()).hexdigest(),
                copied_bytes_not_a_new_http_observation=True)
            persist(path,records)


def collect(requests_by_name, fetch=False):
    DATA.mkdir(exist_ok=True);(DATA/'raw').mkdir(exist_ok=True);path=DATA/'manifest.json'
    records=json.loads(path.read_text())['sources'] if path.exists() else {}
    inherit(records,path);missing=[];original_targets=targets()
    for name,url in requests_by_name.items():
        original=name.split('_retry_')[0]
        if original not in original_targets or url!=original_targets[original]:raise ValueError('Unregistered inventory request')
        if name!=original and (original not in records or records[original]['status'] is not None):
            raise ValueError('Only original transport-failure retries allowed')
        if name in records:
            if records[name]['url']!=url:raise ValueError('Original request changed')
            retained(DATA,records[name])
        elif fetch:missing.append((name,url))
        else:raise ValueError('Explicit inventory source fetch required '+name)
    clients=local()
    def get(item):
        name,url=item;start=datetime.now(timezone.utc).isoformat()
        try:
            if not hasattr(clients,'http'):clients.http=requests.Session()
            r=clients.http.get(url,timeout=(25,25));body=r.content;end=datetime.now(timezone.utc).isoformat()
            file=hashlib.sha256((name+' '+url).encode()).hexdigest();(DATA/'raw'/file).write_bytes(body)
            return name,dict(url=url,final_url=r.url,status=r.status_code,path='raw/'+file,
                requested_utc=start,retrieved_utc=end,bytes=len(body),sha256=hashlib.sha256(body).hexdigest(),
                headers={k:r.headers[k] for k in ('Date','Content-Type','Last-Modified','ETag') if k in r.headers})
        except requests.RequestException as e:
            return name,dict(url=url,status=None,path=None,requested_utc=start,
                failed_utc=datetime.now(timezone.utc).isoformat(),source_error=type(e).__name__+': '+str(e))
    with ThreadPoolExecutor(max_workers=2) as pool:
        for done,f in enumerate(as_completed([pool.submit(get,item) for item in missing]),1):
            name,r=f.result();records[name]=r;persist(path,records)
            if done%25==0 or r['status']!=200 or done==len(missing):
                print(dict(new_completed=done,new_total=len(missing),record=name,status=r['status'],
                    retained_records=len(records)),flush=True)
    return records


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--fetch',action='store_true');p.add_argument('--retry-failures',action='store_true');a=p.parse_args()
    if a.retry_failures:
        records=json.loads((DATA/'manifest.json').read_text())['sources'];stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        requests_by_name={key+'_retry_'+stamp:url for key,url in targets().items() if records[key]['status'] is None}
    else:requests_by_name=targets()
    collect(requests_by_name,fetch=a.fetch)
