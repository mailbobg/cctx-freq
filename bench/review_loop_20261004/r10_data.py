"""Checksummed Binance PAXG spot archive, separate from reused crypto inputs."""
from concurrent.futures import ThreadPoolExecutor
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
from threading import Lock
import zipfile

import pandas as pd
import requests

from r7_data import parse_archive

HERE = Path(__file__).resolve().parent
DATA = HERE/'r10_data'
START, END = pd.Timestamp('2020-09-01',tz='UTC'),pd.Timestamp('2026-09-01',tz='UTC')


def main(verify_cache=False):
    DATA.mkdir(exist_ok=True)
    (DATA/'raw').mkdir(exist_ok=True)
    record_path = DATA/'sources.json'
    records = json.loads(record_path.read_text()) if record_path.exists() else {}
    lock = Lock()

    def get(url, session):
        if url in records:
            rec = records[url]
            body = (DATA/rec['path']).read_bytes()
            if hashlib.sha256(body).hexdigest() != rec['sha256']:
                raise ValueError('Archived raw source changed')
            return body
        if verify_cache:
            raise ValueError('Offline replay lacks a frozen source')
        response = session.get(url,timeout=(5,15))
        response.raise_for_status()
        body = response.content
        path = DATA/'raw'/hashlib.sha256(url.encode()).hexdigest()
        path.write_bytes(body)
        with lock:
            records[url] = dict(url=url,path=str(path.relative_to(DATA)),sha256=hashlib.sha256(body).hexdigest(),
                                bytes=len(body),retrieved_utc=datetime.now(timezone.utc).isoformat())
            temp = record_path.with_suffix('.tmp')
            temp.write_text(json.dumps(records,indent=2)+'\n');temp.replace(record_path)
        return body

    def month_archive(month):
        tag = str(month.date())[:7]
        name = f'PAXGUSDT-1d-{tag}.zip'
        url = f'https://data.binance.vision/data/spot/monthly/klines/PAXGUSDT/1d/{name}'
        with requests.Session() as session:
            body = get(url,session)
            check = get(url+'.CHECKSUM',session).decode().split()
            if len(check) != 2 or check[1] != name or check[0] != hashlib.sha256(body).hexdigest():
                raise ValueError('Official gold archive checksum mismatch')
        with zipfile.ZipFile(io.BytesIO(body)) as zipped:
            csv = name.replace('.zip','.csv')
            if zipped.namelist() != [csv]:
                raise ValueError('Unexpected gold archive contents')
            frame = parse_archive(zipped.read(csv),month,month+pd.offsets.MonthBegin(1),include_activity=True)
        print(tag,'rows',len(frame),flush=True)
        return frame

    months = pd.date_range(START,END,freq='MS',inclusive='left')
    with ThreadPoolExecutor(max_workers=4) as pool:
        frames = list(pool.map(month_archive,months))
    px = pd.concat(frames).sort_index()
    expected = pd.date_range(START,END,freq='D',inclusive='left').as_unit('ns')
    if not px.index.equals(expected):
        raise ValueError('Gold active calendar has missing or repeated actual days')
    path = DATA/'PAXGUSDT_spot.parquet'
    if verify_cache:
        pd.testing.assert_frame_equal(px,pd.read_parquet(path))
        manifest = json.loads((DATA/'manifest.json').read_text())
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest['output_sha256'][path.name]:
            raise ValueError('Frozen parsed gold archive changed')
        print('Offline raw/checksum/calendar/activity/parsed replay PASS',flush=True)
        return
    px.to_parquet(path)
    (DATA/'manifest.json').write_text(json.dumps({'captured_utc':datetime.now(timezone.utc).isoformat(),
        'rows':len(px),'start':str(px.index[0]),'last':str(px.index[-1]),'zero_trade_days':int(px.trades.eq(0).sum()),
        'zero_quote_volume_days':int(px.quote_volume.eq(0).sum()),'raw_sources':len(records),
        'output_sha256':{path.name:hashlib.sha256(path.read_bytes()).hexdigest()},
        'listing_utc':'2020-08-28T12:00:00Z',
        'listing_source':'https://www.binance.com/en/support/announcement/detail/a8971e79c00f4350b1ab27c449e1f049',
        'limitations':'Official traded candles and activity, not atomic quotes/fills. Microsecond timestamps normalized by tested R7 parser. Actual complete post-listing calendar only; no interpolation.'},indent=2)+'\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify-cache-only',action='store_true')
    main(parser.parse_args().verify_cache_only)
