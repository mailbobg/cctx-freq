"""Checksummed required-hour price sources for R17, without a profit screen."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime,timezone
from decimal import Decimal,localcontext
import hashlib
import io
import json
from pathlib import Path
from threading import Lock
import zipfile

import numpy as np
import pandas as pd
import requests

from r16_knowledge import utc
from r17_carry import ASSETS,rate_decimal


HERE=Path(__file__).resolve().parent
DATA=HERE/'r17_prices'
BEGIN=pd.Timestamp('2024-01-01',tz='UTC')
END=pd.Timestamp('2026-09-01',tz='UTC')
XRP_VOLUME_URL='https://fapi.binance.com/fapi/v1/klines?symbol=XRPUSDT&interval=1h&startTime=1699959600000&endTime=1699963199999&limit=1'


def verified_zip(body,checksum,name):
    words=checksum.decode().split()
    if len(words)!=2 or words[1]!=name or words[0]!=hashlib.sha256(body).hexdigest():
        raise ValueError('Official archive checksum/name differs')
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        member=name.replace('.zip','.csv')
        if archive.namelist()!=[member]:raise ValueError('Unexpected ZIP member identity')
        return archive.read(member)


def verified_volume_counterpart(body,api,timestamp):
    """Explicit retained counterpart; original raw archive is never modified."""
    rows=list(csv.reader(io.StringIO(body.decode())))
    actual=json.loads(api,parse_float=str)
    if len(actual)!=1 or len(actual[0])!=12 or int(actual[0][0])!=timestamp:
        raise ValueError('Counterpart candle identity differs')
    matches=[i for i,r in enumerate(rows) if r[0] not in ('open_time','openTime') and int(r[0])==timestamp]
    if len(matches)!=1:raise ValueError('Counterpart archive candle identity differs')
    i=matches[0];replacement=[str(v) for v in actual[0]]
    if any(rate_decimal(rows[i][j])!=rate_decimal(replacement[j]) for j in range(12) if j!=5):
        raise ValueError('Counterpart changes a non-volume candle field')
    rows[i][5]=replacement[5]
    return ('\n'.join(','.join(r) for r in rows)+'\n').encode()


def parse_hourly(body,start,end,trade,allow_gaps=False):
    rows=list(csv.reader(io.StringIO(body.decode('utf-8'))))
    if rows and rows[0][0] in ('open_time','openTime'):rows=rows[1:]
    if not rows or any(len(r)!=12 for r in rows):raise ValueError('Unexpected hourly candle schema')
    stamps=np.array([int(r[0]) for r in rows],dtype=np.int64)
    finishes=np.array([int(r[6]) for r in rows],dtype=np.int64)
    if (stamps>=10**15).all():unit='us';scale=1000000
    elif ((stamps>=10**11)&(stamps<10**14)).all():unit='ms';scale=1000
    else:raise ValueError('Unexpected/mixed candle epoch units')
    if not np.equal(finishes-stamps,3600*scale-1).all():raise ValueError('Incomplete or wrong hourly candle span')
    index=pd.DatetimeIndex(pd.to_datetime(stamps,unit=unit,utc=True)).as_unit('ns')
    expected=pd.date_range(utc(start),utc(end),freq='h',inclusive='left').as_unit('ns')
    if not index.is_unique or not index.is_monotonic_increasing or len(index.difference(expected)) or (not allow_gaps and not index.equals(expected)):
        raise ValueError('Missing/repeated/reordered hourly calendar')
    values=[]
    for row in rows:
        o,h,l,c=[rate_decimal(r) for r in row[1:5]]
        if min(o,h,l,c)<=0 or h<max(o,l,c) or l>min(o,h,c):raise ValueError('Invalid physical hourly OHLC')
        out=[float(o),float(h),float(l),float(c)]
        if trade:
            v,q,n,b,bq=[rate_decimal(row[j]) for j in (5,7,8,9,10)]
            if min(v,q,n)<=0 or n!=n.to_integral_value() or b<0 or b>v or bq<0 or bq>q:
                raise ValueError('Nonpositive/invalid actual hourly trade activity')
            # Fixed source-digit quantization bound, not a fitted float tolerance.
            with localcontext() as ctx:
                ctx.prec=60
                tolerance=Decimal(10)**q.as_tuple().exponent+h*Decimal(10)**v.as_tuple().exponent
                if q<l*v-tolerance or q>h*v+tolerance:raise ValueError('Native/quote amount exceeds physical hourly price bound')
            out.extend([float(v),float(q),int(n)])
        values.append(out)
    columns=['open','high','low','close']+(['volume','quote_volume','trades'] if trade else [])
    return pd.DataFrame(values,index=index,columns=columns).rename_axis('date')


def required_hours(decisions):
    index=pd.DatetimeIndex([],tz='UTC')
    for record in decisions:
        if not record['selected']:continue
        opened=utc(record['decision']);closed=min(opened+pd.Timedelta(days=7),END-pd.Timedelta(hours=1))
        index=index.union(pd.date_range(opened,closed,freq='h'))
    return index.as_unit('ns')


def main(fetch=False):
    forecast=json.loads((HERE/'r17_forecast_validation.json').read_text())
    for name,sha in forecast['decision_outputs_sha256'].items():
        if hashlib.sha256((HERE/name).read_bytes()).hexdigest()!=sha:raise ValueError('Frozen rate-only decisions changed')
    decisions=json.loads((HERE/'r17_decisions.json').read_text())
    needed=required_hours(decisions)
    if needed.empty:raise ValueError('No active holding interval requires a price-dependent screen')
    months=sorted({t.strftime('%Y-%m') for t in needed})
    DATA.mkdir(exist_ok=True);(DATA/'raw').mkdir(exist_ok=True)
    path=DATA/'sources.json';records=json.loads(path.read_text()) if path.exists() else {};lock=Lock()
    dependencies={}

    def dependency(relative):
        p=HERE/relative;sha=hashlib.sha256(p.read_bytes()).hexdigest()
        with lock:dependencies[relative]=sha
        return p

    # Replayed provenance is part of reuse; a derived Parquet hash alone does
    # not confirm its retained original archive bodies are still intact.
    reused_raw_counts={}
    reused_manifests={}
    for folder in ('r6_data','r7_data','r13_data'):
        manifest=json.loads(dependency(folder+'/manifest.json').read_text())
        source_path=dependency(folder+'/sources.json')
        if 'source_manifest_sha256' in manifest and hashlib.sha256(source_path.read_bytes()).hexdigest()!=manifest['source_manifest_sha256']:
            raise ValueError('Reused original source manifest changed')
        sources=json.loads(source_path.read_text())
        for record in sources.values():
            raw=(HERE/folder/record['path']).read_bytes()
            if len(raw)!=record['bytes'] or hashlib.sha256(raw).hexdigest()!=record['sha256']:
                raise ValueError('Reused original source body changed')
        reused_raw_counts[folder]=len(sources);reused_manifests[folder]=manifest

    def downloaded(url,session):
        if url in records:
            record=records[url];body=(DATA/record['path']).read_bytes()
            if hashlib.sha256(body).hexdigest()!=record['sha256']:raise ValueError('Retained price source changed')
        elif fetch:
            response=session.get(url,timeout=(5,20));body=response.content
            p=DATA/'raw'/hashlib.sha256(url.encode()).hexdigest();p.write_bytes(body)
            record=dict(path=str(p.relative_to(DATA)),sha256=hashlib.sha256(body).hexdigest(),bytes=len(body),
                        status=response.status_code,retrieved_utc=datetime.now(timezone.utc).isoformat(),url=url)
            with lock:
                records[url]=record
                temp=path.with_suffix('.tmp');temp.write_text(json.dumps(records,indent=2)+'\n');temp.replace(path)
        else:raise ValueError('Price source unavailable; explicitly fetch public archive')
        if record['status']!=200:raise ValueError(f"Retained price HTTP{record['status']} is not a candle")
        return body

    def archived(kind,symbol,tag,session,daily=False):
        if kind=='spot':base='spot';endpoint='klines'
        else:base='futures/um';endpoint='markPriceKlines' if kind=='mark' else 'klines'
        name=f'{symbol}-1h-{tag}.zip';frequency='daily' if daily else 'monthly'
        url=f'https://data.binance.vision/data/{base}/{frequency}/{endpoint}/{symbol}/1h/{name}'
        body=downloaded(url,session);check=downloaded(url+'.CHECKSUM',session)
        native=verified_zip(body,check,name)
        # One exact, separately documented availability-only source exception.
        # Daily and monthly archives agree on the erroneous native volume;
        # the retained exchange counterpart differs in that field alone.
        if kind=='trade' and symbol=='XRPUSDT' and tag=='2023-11' and not daily:
            native=verified_volume_counterpart(native,downloaded(XRP_VOLUME_URL,session),1699959600000)
        return native

    def reuse(symbol,kind):
        if kind=='mark':
            manifest=json.loads(dependency('r6_data/manifest.json').read_text());name=f'{symbol}_mark.parquet'
            p=dependency('r6_data/'+name)
            if hashlib.sha256(p.read_bytes()).hexdigest()!=manifest['output_sha256'][name]:raise ValueError('Frozen reused R6 mark changed')
            return pd.read_parquet(p)
        manifest=json.loads(dependency('r13_data/manifest.json').read_text());name=f'{symbol}_1m.parquet'
        p=dependency('r13_data/'+name)
        if hashlib.sha256(p.read_bytes()).hexdigest()!=manifest['output_sha256'][name]:raise ValueError('Frozen reused R13 spot changed')
        minute=pd.read_parquet(p)
        hourly=minute.resample('h').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum','quote_volume':'sum','trades':'sum'})
        if not minute.resample('h').size().eq(60).all():raise ValueError('Reused minute-derived hour incomplete')
        return hourly

    def asset_prices(symbol):
        frames={};availability={}
        with requests.Session() as session:
            for kind in ('spot','trade','mark'):
                if symbol in ('BTCUSDT','ETHUSDT') and kind in ('spot','mark'):
                    combined=reuse(symbol,kind)
                else:
                    parts=[];tags=months if kind=='mark' or symbol in ('BTCUSDT','ETHUSDT') else ['2023-11',*months]
                    for tag in tags:
                        first=pd.Timestamp(tag+'-01',tz='UTC');after=first+pd.offsets.MonthBegin(1)
                        raw=archived(kind,symbol,tag,session)
                        parsed=parse_hourly(raw,first,after,trade=kind!='mark',allow_gaps=kind=='mark')
                        expected=pd.date_range(first,after,freq='h',inclusive='left').as_unit('ns')
                        missing=expected.difference(parsed.index)
                        if len(missing):
                            if kind!='mark':raise ValueError('Required traded-hour gap')
                            for day in missing.floor('D').unique():
                                supplement=archived(kind,symbol,str(day.date()),session,daily=True)
                                daily=parse_hourly(supplement,day,day+pd.Timedelta(days=1),trade=False)
                                overlap=parsed.index.intersection(daily.index)
                                if not np.array_equal(parsed.loc[overlap].to_numpy(),daily.loc[overlap].to_numpy()):raise ValueError('Conflicting official mark supplement')
                                parsed=pd.concat([parsed,daily.loc[daily.index.difference(parsed.index)]]).sort_index()
                            if not parsed.index.equals(expected):raise ValueError('Mark calendar remains incomplete after official supplement')
                        parts.append(parsed)
                    combined=pd.concat(parts).sort_index()
                if not combined.index.is_unique:raise ValueError('Repeated retrieved candle')
                if not needed.isin(combined.index).all():raise ValueError('Required holding/closing-hour source unavailable')
                actual=combined.loc[needed]
                if not np.isfinite(actual[['open','high','low','close']].to_numpy()).all():raise ValueError('Unknown required prices')
                if kind!='mark' and not actual[['volume','quote_volume','trades']].gt(0).all().all():raise ValueError('Unreviewed hourly trade activity')
                actual.to_parquet(DATA/f'{symbol}_{kind}.parquet');frames[kind]=len(actual)
                if kind in ('spot','trade'):
                    if symbol in ('BTCUSDT','ETHUSDT'):
                        oldkind='spot' if kind=='spot' else 'trade';folder='r7_data' if kind=='spot' else 'r6_data'
                        name=f'{symbol}_{oldkind}.parquet';p=dependency(f'{folder}/{name}')
                        if hashlib.sha256(p.read_bytes()).hexdigest()!=reused_manifests[folder]['output_sha256'][name]:
                            raise ValueError('Reused original availability price output changed')
                        old=pd.read_parquet(p)
                        availability[kind]=old.index.min().isoformat()
                    else:availability[kind]=combined.index.min().isoformat()
                    if utc(availability[kind])>BEGIN-pd.Timedelta(days=60):raise ValueError('Native market not proven at least60 days old before first decision')
        record=dict(required_hours=len(needed),rows=frames,proven_trade_before=availability)
        print(symbol,'price sources validated',json.dumps(record),flush=True)
        return record

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures={s:pool.submit(asset_prices,s) for s in ASSETS};coverage={s:f.result() for s,f in futures.items()}
    outputs={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in DATA.glob('*.parquet')}
    manifest=dict(source_only=True,new_performance=False,decision_hashes=forecast['decision_outputs_sha256'],
                  required_hour_count=len(needed),months_retrieved=months,new_http_records=len(records),coverage=coverage,
                  reused_raw_bodies_verified=reused_raw_counts,
                  source_reconciliation='r17_price_reconciliation.json',
                  reused_dependency_sha256=dependencies,output_sha256=outputs,
                  limitations='All eight pairs cover every actually held/order hour and control. Other full-window hours are flat cash without invented prices. Reused inputs keep original provenance. Hourly candle prices are not executable quotes or intrahour ticks; no profitability screen here.')
    (DATA/'manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
    print('Price source collection complete; no P&L.',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--fetch',action='store_true')
    main(parser.parse_args().fetch)
