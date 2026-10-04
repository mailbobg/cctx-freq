"""Native quarterly/spot information only, on unchanged covariance spot weights."""
import csv
from decimal import Decimal
import io
import re

import pandas as pd

from r7_dated import LISTED
from r7_data import expiry

ASSETS=['BTC','ETH']


def number(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d+(?:\.\d+)?',value):raise ValueError('Unreviewed native decimal')
    result=Decimal(value)
    if not result.is_finite():raise ValueError('Nonfinite native value')
    return result


def parse_native(body):
    rows=list(csv.reader(io.StringIO(body.decode('utf-8'))))
    if rows and rows[0][0] in ('open_time','openTime'):
        if len(rows[0])!=12:raise ValueError('Native header shape differs')
        rows=rows[1:]
    if not rows:raise ValueError('Empty native archive')
    parsed={};previous=None;scale=None
    for row in rows:
        if len(row)!=12 or not row[0].isdigit() or not row[6].isdigit():raise ValueError('Native source schema/epoch differs')
        opened,closed=int(row[0]),int(row[6]);unit=1000000 if opened>=10**15 else 1000
        if scale is None:scale=unit
        if unit!=scale or opened%(86400*scale) or closed-opened!=86400*scale-1:raise ValueError('Native epoch unit/grid/interval differs')
        day=pd.Timestamp(opened,unit='us' if scale==1000000 else 'ms',tz='UTC')
        if previous is not None and day-previous!=pd.Timedelta(days=1):raise ValueError('Duplicate/unordered/interior native calendar gap')
        op,high,low,close=(number(v) for v in row[1:5])
        if min(op,high,low,close)<=0 or low>min(op,close) or high<max(op,close):raise ValueError('Native OHLC physically invalid')
        volume,quote=number(row[5]),number(row[7])
        if not row[8].isdigit():raise ValueError('Invalid native trade count')
        trades=int(row[8]);number(row[9]);number(row[10])
        if (volume==0 and (quote!=0 or trades!=0)) or (volume>0 and (quote<=0 or trades<=0)):raise ValueError('Native activity inconsistent')
        parsed[day]=dict(close=row[4],open=row[1],high=row[2],low=row[3],volume=row[5],quote_volume=row[7],trades=trades,
            native_open_epoch=row[0],native_close_epoch=row[6],native_epoch_scale=scale,interval_end=day+pd.Timedelta(days=1))
        previous=day
    return parsed


def monday_time(decision):
    if not isinstance(decision,pd.Timestamp) or str(decision.tz)!='UTC' or decision!=decision.normalize() or decision.weekday()!=0:
        raise ValueError('Exact Monday UTC00 decision required')


def front_symbol(asset,decision):
    monday_time(decision)
    if asset not in ASSETS:raise ValueError('Unregistered native asset')
    for year in (decision.year,decision.year+1):
        for month in (3,6,9,12):
            last=pd.Timestamp(year=year,month=month,day=1,tz='UTC')+pd.offsets.MonthEnd(0)
            friday=last-pd.Timedelta(days=(last.weekday()-4)%7)
            if friday+pd.Timedelta(hours=8)>decision:return asset+'USDT_'+friday.strftime('%y%m%d')
    raise AssertionError('Expected nearest quarter missing')


def feature(asset,decision,spot,dated,provenance):
    monday_time(decision);symbol=front_symbol(asset,decision)
    friday=decision-pd.Timedelta(days=3);available=friday+pd.Timedelta(days=2)
    base=dict(asset=asset,decision=decision,symbol=symbol,expiry=expiry(symbol),observation_day=friday,
              available_at_assumed=available,historical_publication_certified=False,
              availability_assumption='Friday completed trade interval + one full additional UTC day, not authenticated receipt')
    if symbol in LISTED and LISTED[symbol]>decision:
        if decision!=pd.Timestamp('2021-02-01',tz='UTC'):raise ValueError('Unreviewed unavailable contract decision')
        return dict(**base,known=False,allow=False,reason='confirmed_not_listed',spot_close_decimal=None,future_close_decimal=None,
                    listing_evidence_not_first_archive_day=LISTED[symbol])
    if spot is None or dated is None or friday not in spot or friday not in dated:raise ValueError('Missing expected native Friday row')
    s,q=spot[friday],dated[friday]
    for row in (s,q):
        if row['interval_end']!=friday+pd.Timedelta(days=1) or row['interval_end']>=base['expiry'] or row['trades']<=0 or number(row['volume'])<=0 or number(row['quote_volume'])<=0:
            raise ValueError('Uncompleted/expired/no-trade source feature')
    for name in ('spot_sha256','future_sha256'):
        if not re.fullmatch(r'[0-9a-f]{64}',provenance.get(name,'')):raise ValueError('Native raw identity missing')
    allow=number(q['close'])>=number(s['close'])
    return dict(**base,known=True,allow=bool(allow),reason='premium_or_equal' if allow else 'discounted',
                spot_close_decimal=s['close'],future_close_decimal=q['close'],spot_native_close_epoch=s['native_close_epoch'],
                future_native_close_epoch=q['native_close_epoch'],spot_native_epoch_scale=s['native_epoch_scale'],
                future_native_epoch_scale=q['native_epoch_scale'],**provenance)


def overlay(base,features):
    if list(base)!=ASSETS or str(base.index.tz)!='UTC' or not base.index.is_unique or not base.index.is_monotonic_increasing or not base.index.equals(base.index.normalize()):
        raise ValueError('Unreviewed original weights/calendar')
    out=base*0.;audit=[]
    for day in base.index[base.index.weekday==6]:
        monday=day+pd.Timedelta(days=1)
        if monday not in features:continue  # Explicitly zero outside registered source windows.
        pair=features[monday]
        if set(pair)!=set(ASSETS):raise ValueError('Missing per-asset feature identity')
        for asset in ASSETS:
            f=pair[asset]
            if f['decision']!=monday or f['asset']!=asset or f['available_at_assumed']>monday or type(f['known']) is not bool or type(f['allow']) is not bool or f['allow'] and not f['known']:
                raise ValueError('Unknown/future/native feature identity')
            symbol=front_symbol(asset,monday)
            if f['symbol']!=symbol or f['expiry']!=expiry(symbol) or f['observation_day']!=monday-pd.Timedelta(days=3):
                raise ValueError('Foreign contract/expiry/observation identity')
            if f['allow']:out.loc[day,asset]=base.loc[day,asset]
            audit.append(dict(**f,baseline_weight=float(base.loc[day,asset]),target_weight=float(out.loc[day,asset])))
    return out,pd.DataFrame(audit)
