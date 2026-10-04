"""Fixed native interval/quantity comparisons; no inferred trading clock."""
import csv
from decimal import Decimal,localcontext
import io

import pandas as pd

from r21_basis import number


def parse_5m(body,file_day):
    native=list(csv.reader(io.StringIO(body.decode('utf-8'))))
    if native and native[0][0]=='open_time':
        if len(native[0])!=12:raise ValueError('Native counterpart header differs')
        native=native[1:]
    if not native:raise ValueError('Empty native counterpart')
    start=pd.Timestamp(file_day,tz='UTC');end=start+pd.Timedelta(days=1);out={};previous=None
    for row in native:
        if len(row)!=12 or not row[0].isdigit() or not row[6].isdigit() or not row[8].isdigit():raise ValueError('Native counterpart schema differs')
        t=int(row[0]);finish=int(row[6])
        if not 10**11<=t<10**14 or t%300000 or finish-t!=299999:raise ValueError('Native futures millisecond5m interval differs')
        label=pd.Timestamp(t,unit='ms',tz='UTC')
        if not start<=label<end or previous is not None and label-previous!=pd.Timedelta(minutes=5):raise ValueError('Native counterpart file/grid/order differs')
        op,high,low,close=(number(v) for v in row[1:5]);v,q,b,bq=(number(row[k]) for k in (5,7,9,10));trades=int(row[8])
        if min(op,high,low,close)<=0 or low>min(op,close) or high<max(op,close):raise ValueError('Native counterpart OHLC invalid')
        if b>v or bq>q or (v==0 and (q!=0 or trades!=0)) or (v>0 and (q<=0 or trades<=0)):raise ValueError('Native counterpart activity inconsistent')
        out[label]=dict(open=op,high=high,low=low,close=close,volume=v,quote_volume=q,
            taker_buy_volume=b,taker_buy_quote_volume=bq,trade_count=trades,end=label+pd.Timedelta(minutes=5))
        previous=label
    return out


def assumed_label(row):
    # This is one explicitly hypothetical mapping used for counterpart testing,
    # not a change to the original naive metric label or a certified UTC clock.
    return pd.Timestamp(row['create_time'],tz='UTC')


def native_ratio(row,reported,base=True):
    volume=row['volume' if base else 'quote_volume'];buy=row['taker_buy_volume' if base else 'taker_buy_quote_volume'];sell=volume-buy
    if sell==0:return dict(state='zero_sell_volume',buy_decimal=str(buy),sell_decimal=str(sell),ratio_decimal=None,
        absolute_error_decimal=None,relative_error_decimal=None,within_reported_half_ulp=None)
    with localcontext() as context:
        context.prec=50;ratio=buy/sell;error=abs(ratio-reported)
        tolerance=Decimal(5).scaleb(reported.as_tuple().exponent-1)
        relative=error/abs(reported) if reported else None
        return dict(state='value',buy_decimal=str(buy),sell_decimal=str(sell),ratio_decimal=str(ratio),
            absolute_error_decimal=str(error),relative_error_decimal=str(relative) if relative is not None else None,
            reported_half_ulp_decimal=str(tolerance),within_reported_half_ulp=error<=tolerance)


def ratio_comparison(metric,prices):
    label=assumed_label(metric);reported=number(metric['sum_taker_long_short_vol_ratio']);out={}
    for convention,begin in [('preceding',label-pd.Timedelta(minutes=5)),('following',label)]:
        if begin not in prices:out[convention]=dict(state='missing_native_interval',assumed_start=begin.isoformat())
        else:out[convention]=dict(state='known_native_interval',assumed_start=begin.isoformat(),interval_end=prices[begin]['end'].isoformat(),
            base=native_ratio(prices[begin],reported),quote=native_ratio(prices[begin],reported,False))
    return dict(source_label_unchanged=metric['create_time'],reported_native_ratio_decimal=metric['sum_taker_long_short_vol_ratio'],
        comparison_label_timezone_assumption='Naive CSV label interpreted as UTC only for two fixed counterpart tests',
        measurement_clock_certified=False,**out)


def oi_volume_comparison(old,new,prices):
    t0,t1=assumed_label(old),assumed_label(new)
    if t1-t0!=pd.Timedelta(minutes=5):raise ValueError('Inventory comparison requires adjacent declared labels')
    change=abs(number(new['sum_open_interest'])-number(old['sum_open_interest']));out={}
    for convention,begin in [('label_time_window',t0),('label_plus_5m_window',t0+pd.Timedelta(minutes=5))]:
        if begin not in prices:out[convention]=dict(state='missing_native_interval',assumed_start=begin.isoformat())
        else:
            volume=prices[begin]['volume'];out[convention]=dict(state='known_native_interval',assumed_start=begin.isoformat(),
                volume_decimal=str(volume),exceeds_native_volume=change>volume,
                excess_decimal=str(max(change-volume,Decimal(0))))
    return dict(prior_source_label_unchanged=old['create_time'],source_label_unchanged=new['create_time'],absolute_native_oi_change=str(change),
        comparison_label_timezone_assumption='Naive CSV labels interpreted as UTC only for two fixed counterpart tests',
        actual_inventory_unit_and_clock_certified=False,**out)
