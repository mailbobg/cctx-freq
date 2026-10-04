"""Checksummed recent-window minute spot prices; offline exact replay."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
from threading import Lock
import zipfile

import numpy as np
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DATA = HERE/'r13_data'
START, END = pd.Timestamp('2024-01-01',tz='UTC'), pd.Timestamp('2026-09-01',tz='UTC')
ASSETS = ('BTC','ETH')


def parse_minutes(body,start,end):
    frame = pd.read_csv(io.BytesIO(body),header=None)
    if frame.shape[1] != 12 or frame.empty:
        raise ValueError('Wrong/empty minute schema')
    numeric = frame.apply(pd.to_numeric,errors='raise')
    if not np.isfinite(numeric.to_numpy()).all():
        raise ValueError('Unknown minute fields')
    opened,closed = numeric[0],numeric[6]
    if not opened.mod(1).eq(0).all() or not closed.mod(1).eq(0).all():
        raise ValueError('Fractional source timestamp')
    micro = opened.ge(10**15)
    if micro.any() and not micro.all():
        raise ValueError('Mixed archive epoch units')
    unit = 'us' if micro.all() else 'ms'
    scale = 1000000 if unit=='us' else 1000
    if not closed.sub(opened).eq(60*scale-1).all():
        raise ValueError('Wrong minute close span')
    index = pd.DatetimeIndex(pd.to_datetime(opened.astype('int64'),unit=unit,utc=True)).as_unit('ns')
    expected = pd.date_range(start,end,freq='min',inclusive='left').as_unit('ns')
    if not index.equals(expected):
        raise ValueError('Missing, repeated, reordered or unexpected minute')
    frame = numeric[[1,2,3,4,5,7,8]].set_axis(['open','high','low','close','volume','quote_volume','trades'],axis=1)
    if not frame.gt(0).all().all() or not frame.trades.mod(1).eq(0).all():
        raise ValueError('Nonpositive price/activity or fractional trade count')
    if not frame.high.ge(frame[['open','close','low']].max(axis=1)).all() or not frame.low.le(frame[['open','close','high']].min(axis=1)).all():
        raise ValueError('Invalid minute OHLC')
    frame.index=index
    return frame.rename_axis('date')


def checked_zip(body,check,filename):
    fields=check.decode().split()
    if len(fields)!=2 or fields[1]!=filename or fields[0]!=hashlib.sha256(body).hexdigest():
        raise ValueError('Official minute checksum/filename mismatch')
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        member=filename.removesuffix('.zip')+'.csv'
        if z.namelist()!=[member]:
            raise ValueError('Wrong archive member identity')
        return z.read(member)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--verify-cache-only',action='store_true')
    args=parser.parse_args()
    DATA.mkdir(exist_ok=True);(DATA/'raw').mkdir(exist_ok=True)
    sources_path=DATA/'sources.json'
    records=json.loads(sources_path.read_text()) if sources_path.exists() else {}
    lock=Lock()
    def get(url):
        if url in records:
            rec=records[url];body=(DATA/rec['path']).read_bytes()
            if hashlib.sha256(body).hexdigest()!=rec['sha256'] or len(body)!=rec['bytes']:
                raise ValueError('Frozen minute source bytes changed')
            if rec['status']!=200:
                raise ValueError('Retained source HTTP'+str(rec['status']))
            return body
        if args.verify_cache_only:
            raise ValueError('Offline replay has no frozen source response')
        response=requests.get(url,timeout=(10,60))
        body=response.content
        path=DATA/'raw'/hashlib.sha256(url.encode()).hexdigest()
        path.write_bytes(body)
        with lock:
            records[url]=dict(url=url,path=str(path.relative_to(DATA)),status=response.status_code,
                              bytes=len(body),sha256=hashlib.sha256(body).hexdigest(),retrieved_utc=datetime.now(timezone.utc).isoformat())
            temp=sources_path.with_suffix('.tmp');temp.write_text(json.dumps(records,indent=2)+'\n');temp.replace(sources_path)
        response.raise_for_status()
        return body
    jobs=[(a,m) for a in ASSETS for m in pd.date_range(START,END,freq='MS',inclusive='left')]
    def fetch(job):
        a,m=job;filename=f'{a}USDT-1m-{str(m.date())[:7]}.zip'
        url=f'https://data.binance.vision/data/spot/monthly/klines/{a}USDT/1m/{filename}'
        zipped=get(url);check=get(url+'.CHECKSUM')
        frame=parse_minutes(checked_zip(zipped,check,filename),m,m+pd.offsets.MonthBegin(1))
        print(a,str(m.date())[:7],len(frame),flush=True)
        return a,frame
    with ThreadPoolExecutor(max_workers=6) as pool:
        parts=list(pool.map(fetch,jobs))
    manifest=dict(source_count=len(records),source_manifest_sha256=hashlib.sha256(sources_path.read_bytes()).hexdigest(),output_sha256={},validation={})
    ref_manifest=json.loads((HERE/'r7_data/manifest.json').read_text())['output_sha256']
    for a in ASSETS:
        joined=pd.concat([f for name,f in parts if name==a])
        expected=pd.date_range(START,END,freq='min',inclusive='left').as_unit('ns')
        if not joined.index.equals(expected): raise ValueError('Full minute calendar disagrees')
        ref_path=HERE/f'r7_data/{a}USDT_spot.parquet'
        ref_sha=hashlib.sha256(ref_path.read_bytes()).hexdigest()
        if ref_sha!=ref_manifest[ref_path.name]: raise ValueError('Frozen daily source changed')
        daily=joined.resample('D').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum','quote_volume':'sum','trades':'sum'})
        ref=pd.read_parquet(ref_path).reindex(daily.index)
        columns=['open','high','low','close']
        daily_gap=float(np.max(np.abs(daily[columns].to_numpy()-ref[columns].to_numpy())))
        if daily_gap>1e-8: raise ValueError('Minute aggregation disagrees with frozen daily OHLC')
        cache_path=ROOT/f'user_data/data/binance/{a}_USDT-1m.feather'
        cache_sha=hashlib.sha256(cache_path.read_bytes()).hexdigest()
        cache=pd.read_feather(cache_path).set_index('date').reindex(joined.index)
        cache_gap=float(np.max(np.abs(joined[columns].to_numpy()-cache[columns].to_numpy())))
        if not np.isfinite(cache_gap) or cache_gap>1e-8: raise ValueError('Official minute archive disagrees with cached prices')
        target=DATA/f'{a}USDT_1m.parquet'
        if args.verify_cache_only:
            pd.testing.assert_frame_equal(pd.read_parquet(target),joined)
        else: joined.to_parquet(target)
        manifest['output_sha256'][target.name]=hashlib.sha256(target.read_bytes()).hexdigest()
        manifest['validation'][a]=dict(rows=len(joined),first=str(joined.index[0]),last=str(joined.index[-1]),
            positive_activity_every_minute=True,daily_OHLC_max_gap=daily_gap,cache_OHLC_max_gap=cache_gap,
            frozen_daily_sha256=ref_sha,existing_feather_sha256=cache_sha,missing_minutes=0)
    path=DATA/'manifest.json'
    if args.verify_cache_only:
        if json.loads(path.read_text())!=manifest: raise ValueError('Minute manifest does not replay exactly')
        print('Offline checksums, timestamps, calendar, activity and frozen daily/cached OHLC replay PASS')
    else: path.write_text(json.dumps(manifest,indent=2)+'\n');print(json.dumps(manifest,indent=2))


if __name__=='__main__': main()
