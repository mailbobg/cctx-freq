"""Retain original fixed-panel archives and failed attempts, without source defaults."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
from threading import local
from urllib.parse import urlencode

import pandas as pd
import requests

from r20_audit import retained

HERE=Path(__file__).resolve().parent
DATA=HERE/'r22_sources'
PANEL=('SOLUSDT','XRPUSDT','BNBUSDT','ADAUSDT','DOGEUSDT','AVAXUSDT')
BASE='https://data.binance.vision/data/spot/monthly/klines/'


def month_targets(months):
    targets={}
    for symbol in PANEL:
        for month in months:
            key='spot_'+symbol+'_'+month;url=f'{BASE}{symbol}/1d/{symbol}-1d-{month}.zip'
            targets[key]=url;targets[key+'_checksum']=url+'.CHECKSUM'
    return targets


def probe_targets():
    return dict(**month_targets(['2021-01']),
        publisher_readme='https://raw.githubusercontent.com/binance/binance-public-data/master/README.md',
        oi_listing='https://s3-ap-northeast-1.amazonaws.com/data.binance.vision/?'+urlencode(dict(prefix='data/futures/um/daily/metrics/BTCUSDT/',**{'max-keys':3})),
        oi_early_day='https://data.binance.vision/data/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-2021-01-01.zip',
        oi_early_day_checksum='https://data.binance.vision/data/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-2021-01-01.zip.CHECKSUM')


def full_targets():
    months=pd.date_range('2020-12-01','2026-08-01',freq='MS').strftime('%Y-%m').tolist()
    return {**probe_targets(),**month_targets(months)}


def collect(targets,fetch=False,workers=2,timeouts=(25,25)):
    DATA.mkdir(exist_ok=True);(DATA/'raw').mkdir(exist_ok=True);path=DATA/'manifest.json'
    records=json.loads(path.read_text())['sources'] if path.exists() else {};missing=[]
    for key,url in targets.items():
        if key in records:
            if records[key]['url']!=url:raise ValueError('Frozen request identity changed')
            retained(DATA,records[key])
        else:
            if not fetch:raise ValueError('Explicit public source fetch required '+key)
            missing.append((key,url))
    clients=local()
    def get(item):
        key,url=item;began=datetime.now(timezone.utc).isoformat()
        try:
            if not hasattr(clients,'http'):clients.http=requests.Session()
            response=clients.http.get(url,timeout=timeouts);body=response.content;received=datetime.now(timezone.utc).isoformat()
            name=hashlib.sha256((key+' '+url).encode()).hexdigest();(DATA/'raw'/name).write_bytes(body)
            return key,dict(url=url,final_url=response.url,status=response.status_code,path='raw/'+name,
                requested_utc=began,retrieved_utc=received,bytes=len(body),sha256=hashlib.sha256(body).hexdigest(),
                request_timeout_seconds=list(timeouts),collector_workers=workers,
                headers={k:response.headers[k] for k in ('Date','Content-Type','Last-Modified','ETag') if k in response.headers})
        except requests.RequestException as e:
            return key,dict(url=url,status=None,path=None,requested_utc=began,failed_utc=datetime.now(timezone.utc).isoformat(),
                request_timeout_seconds=list(timeouts),collector_workers=workers,source_error=type(e).__name__+': '+str(e))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(get,item) for item in missing]):
            key,record=future.result();records[key]=record
            temporary=path.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(dict(source_only=True,new_performance=False,sources=records),indent=2,sort_keys=True)+'\n')
            temporary.replace(path)
            print(key,record['status'],record.get('bytes',0),flush=True)
    return {key:((DATA/records[key]['path']).read_bytes() if records[key].get('path') else None,records[key]) for key in targets}


def selected(targets):
    collect(targets);records=json.loads((DATA/'manifest.json').read_text())['sources'];output={}
    for key,url in targets.items():
        candidates=[(name,r) for name,r in records.items() if (name==key or name.startswith(key+'_retry_')) and r['url']==url and r['status']==200]
        if not candidates:raise ValueError('Source failure is not a cash/zero feature '+key)
        name,record=sorted(candidates,key=lambda n:(n[1]['retrieved_utc'],n[0]))[0]
        output[key]=(retained(DATA,record),record,name)
    return output


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--fetch',action='store_true');p.add_argument('--full',action='store_true');args=p.parse_args()
    records=collect(full_targets() if args.full else probe_targets(),args.fetch)
    print('Stored',len(records),'original request roles. No signal/outcome computation.')
