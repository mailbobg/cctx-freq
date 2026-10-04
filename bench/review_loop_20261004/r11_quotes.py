"""Preregistered quote feasibility sample; no option return/backtest model."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import gzip
import hashlib
import io
import json
from pathlib import Path
from threading import Lock
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
DATA = HERE/'r11_quotes_data'
DATES = ('2021-02-01','2024-01-01','2026-08-01')
ASSETS = ('BTC','ETH')


def select_identity(catalog, asset, day, prior_close):
    if not np.isfinite(prior_close) or prior_close <= 0:
        raise ValueError('Unknown/nonpositive prior completed close')
    timestamp = int(day.timestamp()*1000)
    eligible = catalog.loc[catalog.instrument_name.str.startswith(asset+'-') & catalog.option_type.eq('put') &
                           catalog.creation_timestamp.lt(timestamp) &
                           catalog.expiration_timestamp.ge(timestamp+28*86400000) &
                           catalog.expiration_timestamp.le(timestamp+90*86400000)].copy()
    threshold = Decimal(str(float(prior_close)))*Decimal('.9')
    eligible = eligible.loc[eligible.strike.map(lambda x: np.isfinite(x) and Decimal(str(float(x)))<=threshold)]
    if eligible.empty:
        return None
    if eligible.instrument_name.duplicated().any():
        raise ValueError('Conflicting repeated instrument identity')
    earliest = eligible.expiration_timestamp.min()
    return eligible.loc[eligible.expiration_timestamp.eq(earliest)].sort_values('strike',ascending=False).iloc[0].to_dict()


def first_quote(frame, day, minimum):
    begin = int(day.timestamp()*1000000)
    end = begin+60000000
    observed = frame.loc[frame.timestamp.ge(begin) & frame.timestamp.lt(end) &
                         frame.local_timestamp.ge(begin) & frame.local_timestamp.lt(end)].copy()
    fields = ['bid_price','ask_price','bid_amount','ask_amount']
    known = np.isfinite(observed[fields].to_numpy(float)).all(axis=1) & observed[fields].gt(0).all(axis=1)
    uncrossed = observed.bid_price.lt(observed.ask_price)
    enough = observed.ask_amount.ge(minimum)
    eligible = known & uncrossed & enough
    stats = dict(first_minute_rows=len(observed),missing_or_nonpositive_side_rows=int((~known).sum()),
                 crossed_rows=int((known & ~uncrossed).sum()),insufficient_ask_lot_rows=int((known & uncrossed & ~enough).sum()))
    return (observed.loc[eligible].iloc[0].to_dict() if eligible.any() else None),stats


def coverage_amount(units, minimum):
    if not np.isfinite([units,minimum]).all() or units < 0 or minimum <= 0:
        raise ValueError('Invalid native quantity/minimum lot')
    return float((Decimal(str(float(units)))/Decimal(str(float(minimum)))).to_integral_value(rounding=ROUND_CEILING)*Decimal(str(float(minimum))))


def prepare():
    catalog_path = HERE/'r11_probe/option_catalog.parquet'
    expected = json.loads((HERE/'r11_probe/catalog_manifest.json').read_text())['sha256']
    if hashlib.sha256(catalog_path.read_bytes()).hexdigest() != expected:
        raise ValueError('Frozen historical identities changed')
    catalog = pd.read_parquet(catalog_path)
    sources = {'r11_probe/option_catalog.parquet':expected}
    rows = []
    prices = {}
    source_manifest = json.loads((HERE/'r7_data/manifest.json').read_text())['output_sha256']
    for asset in ASSETS:
        path = HERE/f'r7_data/{asset}USDT_spot.parquet'
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if sha != source_manifest[path.name]:
            raise ValueError('Frozen spot decision price changed')
        sources[str(path.relative_to(HERE))] = sha
        prices[asset] = pd.read_parquet(path)
    for date in DATES:
        day = pd.Timestamp(date,tz='UTC')
        label = 'early_reused' if day.year<2024 else 'recent_reused'
        control_path = HERE/f'r10_{label}_Cov10_baseline_cost0.0012_daily.csv'
        sources[control_path.name] = hashlib.sha256(control_path.read_bytes()).hexdigest()
        control = pd.read_csv(control_path,index_col='date',parse_dates=True).loc[day]
        opened_equity = float(control.pre_close_cash)+sum(float(control['pre_close_units_'+a])*float(prices[a].loc[day,'open']) for a in ASSETS)
        for asset in ASSETS:
            prior = float(prices[asset].loc[day-pd.Timedelta(days=1),'close'])
            identity = select_identity(catalog,asset,day,prior)
            row = dict(date=date,asset=asset,prior_spot_close=prior,spot_open_conversion_proxy=float(prices[asset].loc[day,'open']),
                       control_held_native_units_at_10000_start=float(control['pre_close_units_'+asset]*10000),
                       control_cash_USDT_at_10000_start=float(control.pre_close_cash*10000),
                       control_open_marked_equity_USDT_at_10000_start=opened_equity*10000)
            if identity:
                for key in ['instrument_name','creation_timestamp','expiration_timestamp','strike','min_trade_amount','quote_currency','settlement_currency','contract_size']:
                    row[key] = identity[key]
                if row['quote_currency']!=asset or row['settlement_currency']!=asset or row['contract_size']!=1:
                    raise ValueError('Unreviewed inverse option quotation/contract units')
            else:
                row['instrument_name'] = None
            rows.append(row)
    manifest = dict(source_inputs_sha256=sources,selections=rows,scope='Identity selection and quantity benchmark only; quote values not used')
    path = HERE/'r11_quote_manifest.json'
    if path.exists() and json.loads(path.read_text()) != manifest:
        raise ValueError('Frozen insurance quote identity selection changed')
    path.write_text(json.dumps(manifest,indent=2)+'\n')
    return rows,manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--freeze-identities',action='store_true')
    parser.add_argument('--verify-cache-only',action='store_true')
    args = parser.parse_args()
    selections,manifest = prepare()
    if args.freeze_identities:
        print(json.dumps(selections,indent=2));return
    DATA.mkdir(exist_ok=True);(DATA/'raw').mkdir(exist_ok=True)
    sources_path = DATA/'sources.json'
    records = json.loads(sources_path.read_text()) if sources_path.exists() else {}
    lock = Lock()

    def inspect(selection):
        row = selection.copy()
        name = row['instrument_name']
        if not name:
            row['quote_unavailable_reason']='No eligible known identity';return row
        parts = row['date'].split('-')
        url = 'https://datasets.tardis.dev/v1/deribit/quotes/'+ '/'.join(parts)+'/'+quote(name,safe='')+'.csv.gz'
        if url in records:
            rec = records[url];body=(DATA/rec['path']).read_bytes()
            if hashlib.sha256(body).hexdigest()!=rec['sha256']:
                raise ValueError('Quote raw source changed')
        else:
            if args.verify_cache_only:
                raise ValueError('Offline source replay lacks a frozen quote response')
            response = requests.get(url,timeout=(5,30))
            body=response.content
            path=DATA/'raw'/hashlib.sha256(url.encode()).hexdigest();path.write_bytes(body)
            rec=dict(url=url,path=str(path.relative_to(DATA)),status=response.status_code,bytes=len(body),sha256=hashlib.sha256(body).hexdigest(),
                     retrieved_utc=datetime.now(timezone.utc).isoformat())
            with lock:
                records[url]=rec
                temp=sources_path.with_suffix('.tmp');temp.write_text(json.dumps(records,indent=2)+'\n');temp.replace(sources_path)
        row['quote_source_url']=url
        if rec['status']!=200:
            row['quote_unavailable_reason']='Source HTTP'+str(rec['status']);return row
        frame=pd.read_csv(io.BytesIO(gzip.decompress(body)))
        required=['exchange','symbol','timestamp','local_timestamp','ask_price','bid_price','ask_amount','bid_amount']
        if list(frame.columns)!=required:
            # The documented quote ordering places ask_amount before ask_price.
            if set(frame.columns)!=set(required):
                raise ValueError('Unexpected quote schema')
        if not frame.exchange.eq('deribit').all() or not frame.symbol.eq(name).all():
            raise ValueError('Wrong quote exchange/contract identity')
        if frame[['timestamp','local_timestamp']].isna().any().any():
            raise ValueError('Quote event/arrival timestamp missing')
        times=frame[['timestamp','local_timestamp']].to_numpy(float)
        if not np.isfinite(times).all() or ((times<1e14)|(times>=1e16)).any():
            raise ValueError('Quote timestamps are not the documented microsecond epoch schema')
        selected,stats=first_quote(frame,pd.Timestamp(row['date'],tz='UTC'),row['min_trade_amount'])
        row.update(stats)
        row['all_day_quote_rows']=len(frame)
        if selected is None:
            row['quote_unavailable_reason']='No first-minute finite two-sided quote/depth at one minimum lot';return row
        row['first_observed_quote']=selected
        minimum=row['min_trade_amount'];needed=coverage_amount(row['control_held_native_units_at_10000_start'],minimum)
        row.update(one_lot_native_premium=minimum*selected['ask_price'],
                   one_lot_USDT_premium_proxy=minimum*selected['ask_price']*row['spot_open_conversion_proxy'],
                   one_lot_USDT_immediate_spread_proxy=minimum*(selected['ask_price']-selected['bid_price'])*row['spot_open_conversion_proxy'],
                   ceil_coverage_native_amount=needed,displayed_top_ask_covers_ceiled_holdings=bool(selected['ask_amount']>=needed),
                   ceil_coverage_USDT_premium_at_top_quote_proxy=needed*selected['ask_price']*row['spot_open_conversion_proxy'])
        print(row['date'],name,'source_rows',len(frame),'quote_minute_rows',stats['first_minute_rows'],flush=True)
        return row

    with ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(inspect,selections))
    output=dict(input_manifest=manifest,results=results,
                limitations='Three fixed point snapshots, gross ask premiums only, no annualization or P&L trial. Native currency translation uses approximate separate Binance OPEN; actual executable conversion, fees, transfers, basis and atomic option fills unverified. Quote availability/depth at these dates is not complete historical mark/quote coverage or alpha evidence.')
    path=HERE/'r11_quote_results.json'
    if args.verify_cache_only and path.exists():
        if json.loads(path.read_text())!=output:
            raise ValueError('Frozen quote results do not reproduce from raw cached responses')
        print('Offline raw/decompression/identity/timestamp/native-quantity replay PASS');return
    path.write_text(json.dumps(output,indent=2)+'\n')


if __name__=='__main__':
    main()
