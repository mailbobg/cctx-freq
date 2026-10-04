"""Retain the frozen current REST/archive source scope; no alpha/P&L."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from threading import local
from urllib.parse import urlencode

import requests
from r20_audit import retained

HERE = Path(__file__).resolve().parent
DATA = HERE/'r25_sources'
DAY = '2026-09-05'
SYMBOLS = ('BTCUSDT','ETHUSDT')


def bounds():
    day = datetime.strptime(DAY,'%Y-%m-%d').replace(tzinfo=timezone.utc)
    return int(day.timestamp()*1000), int((day+timedelta(days=1)).timestamp()*1000)


def targets():
    start,end = bounds(); out = {}
    for symbol in SYMBOLS:
        url=f'https://data.binance.vision/data/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{DAY}.zip'
        out['metrics_'+symbol]=url; out['metrics_'+symbol+'_checksum']=url+'.CHECKSUM'
        query=urlencode(dict(symbol=symbol,period='5m',startTime=start,endTime=end,limit=500))
        out['oi_'+symbol]='https://fapi.binance.com/futures/data/openInterestHist?'+query
        out['taker_'+symbol]='https://fapi.binance.com/futures/data/takerlongshortRatio?'+query
    out['official_rest']='https://developers.binance.info/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data'
    return out


def collect(requests_by_name, fetch=False):
    DATA.mkdir(exist_ok=True); (DATA/'raw').mkdir(exist_ok=True); path=DATA/'manifest.json'
    records=json.loads(path.read_text())['sources'] if path.exists() else {}; missing=[]
    for name,url in requests_by_name.items():
        if name in records:
            if records[name]['url']!=url: raise ValueError('Original counterpart request changed')
            retained(DATA,records[name])
        elif fetch: missing.append((name,url))
        else: raise ValueError('Explicit counterpart fetch required '+name)
    clients=local()
    def get(item):
        name,url=item; start=datetime.now(timezone.utc).isoformat()
        try:
            if not hasattr(clients,'http'): clients.http=requests.Session()
            r=clients.http.get(url,timeout=(25,25)); body=r.content; end=datetime.now(timezone.utc).isoformat()
            file=hashlib.sha256((name+' '+url).encode()).hexdigest(); (DATA/'raw'/file).write_bytes(body)
            return name,dict(url=url,final_url=r.url,status=r.status_code,path='raw/'+file,requested_utc=start,
                retrieved_utc=end,bytes=len(body),sha256=hashlib.sha256(body).hexdigest(),
                headers={k:r.headers[k] for k in ('Date','Content-Type','Last-Modified','ETag') if k in r.headers})
        except requests.RequestException as e:
            return name,dict(url=url,status=None,path=None,requested_utc=start,
                failed_utc=datetime.now(timezone.utc).isoformat(),source_error=type(e).__name__+': '+str(e))
    with ThreadPoolExecutor(max_workers=2) as pool:
        for f in as_completed([pool.submit(get,item) for item in missing]):
            name,r=f.result(); records[name]=r; tmp=path.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(dict(source_only=True,new_alpha_trial=False,new_performance=False,sources=records),indent=2,sort_keys=True)+'\n'); tmp.replace(path)
            print(name,r['status'],r.get('bytes',0),flush=True)
    return {k:(retained(DATA,records[k]),records[k]) for k in requests_by_name}


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--fetch',action='store_true'); a=p.parse_args()
    collect(targets(),a.fetch)
