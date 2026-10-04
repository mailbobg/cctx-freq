"""Funding-source validation for fixed R17; no price/P&L outcomes or token access."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime,timezone
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
from threading import Lock

import numpy as np
import pandas as pd
import requests

from r16_knowledge import utc
from r17_carry import ASSETS,FundingSeries,rate_decimal


HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
DATA=HERE/'r17_data'
START=pd.Timestamp('2023-12-01',tz='UTC')
END=pd.Timestamp('2026-09-01',tz='UTC')


def parse_funding_csv(body,start,end):
    reader=csv.DictReader(io.StringIO(body.decode('utf-8')))
    if reader.fieldnames!=['calc_time','funding_interval_hours','last_funding_rate']:
        raise ValueError('Unexpected native funding archive schema')
    rows=list(reader)
    if not rows:raise ValueError('Empty monthly funding source')
    stamps=[int(r['calc_time']) for r in rows]
    if all(t>=10**14 for t in stamps):unit='us'
    elif all(10**11<=t<10**14 for t in stamps):unit='ms'
    else:raise ValueError('Unexpected or mixed funding epoch units')
    times=pd.DatetimeIndex(pd.to_datetime(stamps,unit=unit,utc=True)).as_unit('ns')
    if not ((times>=utc(start))&(times<utc(end))).all():raise ValueError('Funding source timestamps outside requested range')
    for row in rows:rate_decimal(row['last_funding_rate'])
    return pd.DataFrame({'slot':times.floor('h'),
                         'rate_decimal':[r['last_funding_rate'] for r in rows],
                         'interval_hours':[int(r['funding_interval_hours']) for r in rows]},index=times).rename_axis('time')


def compare_api(rows,symbol,legacy):
    if not rows or any(r.get('symbol')!=symbol for r in rows):raise ValueError('Funding API source has wrong instrument identity')
    times=pd.DatetimeIndex(pd.to_datetime([int(r['fundingTime']) for r in rows],unit='ms',utc=True)).as_unit('ns')
    slots=times.floor('h')
    if not slots.equals(pd.DatetimeIndex(legacy.events.slot)):
        raise ValueError('API funding calendar differs from independently declared archive slots')
    values=[rate_decimal(r['fundingRate']) for r in rows]
    if values!=list(legacy.events.rate_decimal):raise ValueError('API native decimal funding rates differ from original archive')
    marks=[]
    for row in rows:
        mark=row.get('markPrice')
        if mark in (None,''):marks.append(None);continue
        amount=rate_decimal(mark)
        if amount<=0:raise ValueError('Nonpositive API associated mark')
        marks.append(str(mark))
    frame=pd.DataFrame({'slot':slots,'rate_decimal':[str(r['fundingRate']) for r in rows],
                        'interval_hours':legacy.events.interval_hours.to_numpy(),
                        'mark_decimal':marks,'mark':[float(m) if m is not None else np.nan for m in marks]},index=times).rename_axis('time')
    validated=FundingSeries(frame,legacy.coverage_start,legacy.coverage_end)
    output=validated.events.copy();output['rate_decimal']=frame.rate_decimal
    return output


def freeze_local():
    DATA.mkdir(exist_ok=True)
    path=DATA/'legacy_sources.json'
    if path.exists():
        record=json.loads(path.read_text())
        for rel,rec in record['files'].items():
            if hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()!=rec['sha256']:
                raise ValueError('Frozen original funding CSV changed')
        return record
    files={}
    for symbol in ASSETS:
        for month in pd.date_range(START,END,freq='MS',inclusive='left'):
            tag=month.strftime('%Y-%m')
            p=ROOT/f'data_f1/raw/funding/{symbol}/{symbol}-fundingRate-{tag}.csv'
            files[str(p.relative_to(ROOT))]=dict(sha256=hashlib.sha256(p.read_bytes()).hexdigest(),bytes=p.stat().st_size,
                origin='Previously extracted CSV; ZIP URL inferred, not newly fetched. Whole native rate/calendar independently compared with retained public API response.',
                inferred_url=f'https://data.binance.vision/data/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{tag}.zip')
    record=dict(frozen_at_utc=datetime.now(timezone.utc).isoformat(),files=files)
    path.write_text(json.dumps(record,indent=2)+'\n');return record


def main(fetch=False):
    legacy_manifest=freeze_local()
    (DATA/'raw').mkdir(exist_ok=True)
    manifest_path=DATA/'api_sources.json'
    records=json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    lock=Lock()

    def download(url,session):
        if url in records:
            rec=records[url];body=(DATA/rec['path']).read_bytes()
            if hashlib.sha256(body).hexdigest()!=rec['sha256']:raise ValueError('Retained funding API bytes changed')
        elif fetch:
            response=session.get(url,timeout=(5,20))
            body=response.content;path=DATA/'raw'/hashlib.sha256(url.encode()).hexdigest();path.write_bytes(body)
            rec=dict(path=str(path.relative_to(DATA)),sha256=hashlib.sha256(body).hexdigest(),bytes=len(body),
                     status=response.status_code,retrieved_utc=datetime.now(timezone.utc).isoformat(),url=url)
            with lock:
                records[url]=rec
                temporary=manifest_path.with_suffix('.tmp');temporary.write_text(json.dumps(records,indent=2)+'\n');temporary.replace(manifest_path)
        else:raise ValueError('Funding source missing; explicitly fetch public record first')
        if rec['status']!=200:raise ValueError(f"Retained funding HTTP{rec['status']} is unavailable, not a zero rate")
        return json.loads(body,parse_float=Decimal)

    def asset_data(symbol):
        frames=[]
        for month in pd.date_range(START,END,freq='MS',inclusive='left'):
            p=ROOT/f'data_f1/raw/funding/{symbol}/{symbol}-fundingRate-{month:%Y-%m}.csv'
            frames.append(parse_funding_csv(p.read_bytes(),month,month+pd.offsets.MonthBegin(1)))
        legacy=FundingSeries(pd.concat(frames),START,END)
        cursor=int(START.timestamp()*1000);last=int(END.timestamp()*1000)-1;rows=[]
        with requests.Session() as session:
            while cursor<=last:
                url=requests.Request('GET','https://fapi.binance.com/fapi/v1/fundingRate',
                                     params=dict(symbol=symbol,startTime=cursor,endTime=last,limit=1000)).prepare().url
                page=download(url,session)
                if not isinstance(page,list):raise ValueError('Funding API did not return actual events')
                if not page:break
                final=int(page[-1]['fundingTime'])
                if final<cursor:raise ValueError('API pagination failed to advance')
                rows.extend(page);cursor=final+1
                if len(page)<1000:break
        frame=compare_api(rows,symbol,legacy)
        frame.to_parquet(DATA/f'{symbol}_funding.parquet')
        coverage=dict(rows=len(frame),first_slot=frame.slot.iloc[0].isoformat(),last_slot=frame.slot.iloc[-1].isoformat(),
                      declared_interval_counts={str(k):int(v) for k,v in frame.interval_hours.value_counts().items()},
                      missing_associated_marks=int(frame.mark.isna().sum()),max_exact_event_offset_ms=float((frame.index-pd.DatetimeIndex(frame.slot)).total_seconds().max()*1000),
                      native_decimal_rate_and_scheduled_calendar_match=True)
        print(symbol,'funding source coverage',json.dumps(coverage),flush=True)
        return coverage

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures={symbol:pool.submit(asset_data,symbol) for symbol in ASSETS}
        coverage={symbol:future.result() for symbol,future in futures.items()}
    outputs={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in DATA.glob('*_funding.parquet')}
    record=dict(source_only=True,legacy_csv_files=len(legacy_manifest['files']),retained_api_pages=len(records),
                coverage=coverage,output_sha256=outputs,
                limitations='Legacy CSV origin is inferred; actual native rates/hour slots independently match current historical API exactly. Original calc/event offsets are preserved. Missing associated marks remain unknown. One-hour signal publication lag is an explicit execution assumption, not raw publication evidence. No price, funding-profit or return screen here.')
    (DATA/'funding_manifest.json').write_text(json.dumps(record,indent=2)+'\n')
    print('Funding source validation complete; no P&L or performance aggregation.',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--fetch',action='store_true')
    main(parser.parse_args().fetch)
