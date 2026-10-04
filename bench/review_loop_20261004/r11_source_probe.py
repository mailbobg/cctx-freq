"""Replay/repair fixed historical option identity probes, without a P&L model."""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import time

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
DATA = HERE/'r11_probe'
REPAIRS = [('BTC-26MAR21-28000-P','2021-03-26'),('ETH-26MAR21-960-P','2021-03-26')]


def replay_record(record):
    path = DATA/record['path']
    stored = path.read_bytes()
    if record.get('storage_encoding') == 'gzip':
        if hashlib.sha256(stored).hexdigest() != record['stored_sha256']:
            raise ValueError('Stored compressed option source changed')
        body = gzip.decompress(stored)
    else:
        body = stored
    if hashlib.sha256(body).hexdigest() != record['sha256']:
        raise ValueError('Original option source bytes changed')
    return json.loads(body)


def main(fetch_repairs=False):
    sources_path = DATA/'api_sources.json'
    records = json.loads(sources_path.read_text())
    if fetch_repairs:
        for name,date in REPAIRS:
            expiry = pd.Timestamp(date,tz='UTC')+pd.Timedelta(hours=8)
            for method,params in [('get_instrument',{'instrument_name':name,'include_old':'true'}),
                                  ('get_last_trades_by_instrument_and_time',dict(instrument_name=name,include_old='true',
                                   start_timestamp=int((expiry-pd.Timedelta(days=7)).timestamp()*1000),
                                   end_timestamp=int(expiry.timestamp()*1000),count=100,sorting='asc'))]:
                url = requests.Request('GET','https://history.deribit.com/api/v2/public/'+method,params=params).prepare().url
                if url not in records:
                    response = requests.get(url,timeout=(5,15))
                    body = response.content
                    path = DATA/'raw'/hashlib.sha256(url.encode()).hexdigest();path.write_bytes(body)
                    records[url] = dict(path=str(path.relative_to(DATA)),sha256=hashlib.sha256(body).hexdigest(),
                                        status=response.status_code,retrieved_utc=datetime.now(timezone.utc).isoformat())
                    sources_path.write_text(json.dumps(records,indent=2)+'\n')
                    time.sleep(1.05)
    summary = {'raw_records_replayed':len(records),'metadata':{},'tapes':{},'api_errors':{},'source_only':True}
    for url,record in records.items():
        body = replay_record(record)
        if 'error' in body:
            summary['api_errors'][url] = body['error'];continue
        result = body['result']
        if '/get_instruments?' in url:
            # Full catalog is identity evidence. Contract creation/expiry must
            # still be filtered at a later frozen decision timestamp.
            continue
        if '/get_instrument?' in url:
            name = result['instrument_name']
            summary['metadata'][name] = {key:result.get(key) for key in ['quote_currency','settlement_currency','contract_size',
                'min_trade_amount','creation_timestamp','expiration_timestamp','is_active']}
        else:
            rows = result['trades']
            if not rows:
                summary['tapes'][url] = {'count':0,'has_more':result.get('has_more')};continue
            timestamps = pd.Series([r['timestamp'] for r in rows])
            if not timestamps.is_monotonic_increasing or len({r['trade_id'] for r in rows}) != len(rows):
                raise ValueError('Trade tape timestamps/order/identity are invalid')
            name = rows[0]['instrument_name']
            if any(r['instrument_name'] != name for r in rows):
                raise ValueError('Trade tape mixes contract identities')
            summary['tapes'][name] = {'count':len(rows),'has_more':result.get('has_more'),
                'first_utc':pd.to_datetime(timestamps.iloc[0],unit='ms',utc=True).isoformat(),
                'last_utc':pd.to_datetime(timestamps.iloc[-1],unit='ms',utc=True).isoformat(),
                'mark_field_count':sum('mark_price' in r for r in rows),'index_field_count':sum('index_price' in r for r in rows),
                'historical_bid_ask_quote_fields':sorted(set().union(*(r.keys() for r in rows)) & {'bid_price','ask_price','best_bid_price','best_ask_price'})}
    catalog = pd.read_parquet(DATA/'option_catalog.parquet')
    catalog_manifest = {'rows':len(catalog),'sha256':hashlib.sha256((DATA/'option_catalog.parquet').read_bytes()).hexdigest(),
                        'columns':list(catalog.columns),'limitation':'Archived metadata identity, not bid/ask history or proof of current tradability. Must filter creation/expiry at every decision.'}
    (DATA/'catalog_manifest.json').write_text(json.dumps(catalog_manifest,indent=2)+'\n')
    summary['limitations'] = 'Fixed schema/identity availability only, no premium/P&L selection. Sparse trade-event marks cannot certify a continuous daily/intraday executable bid/ask or mark path. Capped pages with has_more are explicitly incomplete. Inverse premiums and payouts are native BTC/ETH, not USDT. Minimum amounts constrain small-account protection; no fractional units invented.'
    (DATA/'source_validation.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--fetch-repairs',action='store_true')
    main(parser.parse_args().fetch_repairs)
