"""Retain bounded native counterparts; no inventory rule or outcome."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
from threading import local
from urllib.parse import urlencode

import requests

from r20_audit import retained

HERE=Path(__file__).resolve().parent
DATA=HERE/'r24_sources'
SYMBOLS=('BTCUSDT','ETHUSDT')
DAYS=('2026-06-23','2026-06-24','2026-06-25','2026-06-26')


def targets():
    out={}
    for symbol in SYMBOLS:
        for day in DAYS+(('2021-01-01',) if symbol=='BTCUSDT' else ()):
            key='klines_'+symbol+'_'+day;name=f'{symbol}-5m-{day}.zip'
            url=f'https://data.binance.vision/data/futures/um/daily/klines/{symbol}/5m/{name}'
            out[key]=url;out[key+'_checksum']=url+'.CHECKSUM'
    for symbol,day in (('BTCUSDT','2020-12-30'),('BTCUSDT','2021-01-29'),('ETHUSDT','2021-01-29')):
        key='metrics_'+symbol+'_'+day
        url=f'https://data.binance.vision/data/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{day}.zip'
        out[key]=url;out[key+'_checksum']=url+'.CHECKSUM'
    out.update(eth_metrics_listing='https://s3-ap-northeast-1.amazonaws.com/data.binance.vision/?'+urlencode(dict(prefix='data/futures/um/daily/metrics/ETHUSDT/',**{'max-keys':3})),
        native_inventory_blog='https://www.binance.com/en-IN/blog/futures/421499824684900398',
        native_inventory_paper='https://arxiv.org/html/2310.14973v2')
    return out


def collect(requests_by_name,fetch=False):
    DATA.mkdir(exist_ok=True);(DATA/'raw').mkdir(exist_ok=True);path=DATA/'manifest.json'
    records=json.loads(path.read_text())['sources'] if path.exists() else {};missing=[]
    for name,url in requests_by_name.items():
        if name in records:
            if records[name]['url']!=url:raise ValueError('Original counterpart request changed')
            retained(DATA,records[name])
        elif fetch:missing.append((name,url))
        else:raise ValueError('Explicit counterpart fetch required '+name)
    clients=local()
    def get(item):
        name,url=item;start=datetime.now(timezone.utc).isoformat()
        try:
            if not hasattr(clients,'http'):clients.http=requests.Session()
            r=clients.http.get(url,timeout=(25,25));body=r.content;end=datetime.now(timezone.utc).isoformat()
            file=hashlib.sha256((name+' '+url).encode()).hexdigest();(DATA/'raw'/file).write_bytes(body)
            return name,dict(url=url,final_url=r.url,status=r.status_code,path='raw/'+file,requested_utc=start,
                retrieved_utc=end,bytes=len(body),sha256=hashlib.sha256(body).hexdigest(),
                headers={k:r.headers[k] for k in ('Date','Content-Type','Last-Modified','ETag') if k in r.headers})
        except requests.RequestException as e:
            return name,dict(url=url,status=None,path=None,requested_utc=start,failed_utc=datetime.now(timezone.utc).isoformat(),source_error=type(e).__name__+': '+str(e))
    with ThreadPoolExecutor(max_workers=2) as pool:
        for f in as_completed([pool.submit(get,item) for item in missing]):
            name,r=f.result();records[name]=r;tmp=path.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(dict(source_only=True,new_alpha_trial=False,new_performance=False,sources=records),indent=2,sort_keys=True)+'\n');tmp.replace(path)
            print(name,r['status'],r.get('bytes',0),flush=True)
    return {k:(retained(DATA,records[k]),records[k]) for k in requests_by_name}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--fetch',action='store_true');a=p.parse_args()
    collect(targets(),a.fetch)
