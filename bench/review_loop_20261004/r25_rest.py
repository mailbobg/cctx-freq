"""Exact two-offset REST/archive comparisons without certifying native clocks."""
from datetime import datetime, timezone
from decimal import localcontext
import csv
import io
import json

from r21_basis import number
from r23_inventory import FIELDS, parse_metrics
from r25_sources import bounds, targets, SYMBOLS

DECIMAL_FIELDS={'oi':('sumOpenInterest','sumOpenInterestValue'),
                'taker':('buySellRatio','buyVol','sellVol')}
PAIR_FIELDS={'oi':(('quantity','sum_open_interest','sumOpenInterest'),
                  ('value','sum_open_interest_value','sumOpenInterestValue')),
             'taker':(('ratio','sum_taker_long_short_vol_ratio','buySellRatio'),)}


def parse_archive_observations(body, symbol, file_day):
    """Forensic key comparisons only; retain order and the strict parser failure."""
    failure=None
    try: parse_metrics(body,symbol,file_day)
    except ValueError as exc:
        if str(exc)!='Native labels unordered': raise
        failure=str(exc)
    reader=csv.reader(io.StringIO(body.decode('utf-8')))
    header=next(reader)
    if tuple(header)!=FIELDS: raise ValueError('Native inventory header differs')
    rows=[]
    for fields in reader:
        # Reuse all original field, symbol, calendar, grid and physical checks.
        # Only cross-row order is separately retained rather than corrected.
        buffer=io.StringIO();writer=csv.writer(buffer);writer.writerow(header);writer.writerow(fields)
        rows.extend(parse_metrics(buffer.getvalue().encode('utf-8'),symbol,file_day))
    if not rows: raise ValueError('Empty original inventory source')
    decreases=[dict(index=i,prior=a['create_time'],following=b['create_time'])
        for i,(a,b) in enumerate(zip(rows,rows[1:]),1) if b['label']<a['label']]
    return rows,dict(strict_parser_failure=failure,ordering_decreases=decreases,
        chronological_source_order_valid=failure is None,
        no_source_sort_deletion_or_value_change=True,future_feature_cohort_eligibility_not_granted=True)


def object_fields(pairs):
    result={}
    for key,value in pairs:
        if key in result: raise ValueError('Duplicate original JSON key')
        result[key]=value
    return result


def parse_api(body, symbol, kind, start_ms, end_ms, request_url):
    return _parse_api(body,symbol,kind,start_ms,end_ms,request_url,enforce_window=True)


def parse_api_observations(body, symbol, kind, start_ms, end_ms, request_url):
    """Forensic comparisons retain out-of-window native boundaries as failures."""
    failure=None
    try: parse_api(body,symbol,kind,start_ms,end_ms,request_url)
    except ValueError as exc:
        if str(exc)!='Original API epoch/grid/window differs': raise
        failure=str(exc)
    rows=_parse_api(body,symbol,kind,start_ms,end_ms,request_url,enforce_window=False)
    outside=[ts for ts in rows if not start_ms<=ts<=end_ms]
    return rows,dict(strict_requested_window_failure=failure,outside_requested_epochs=outside,
        original_requested_window_compliant=not outside,
        no_original_epoch_shift_deletion_or_value_change=True,
        future_feature_cohort_eligibility_not_granted=True)


def _parse_api(body, symbol, kind, start_ms, end_ms, request_url, enforce_window):
    if (symbol not in SYMBOLS or kind not in DECIMAL_FIELDS or
        (start_ms,end_ms)!=bounds() or request_url!=targets()[f'{kind}_{symbol}']):
        raise ValueError('Unregistered original API identity/window')
    payload=json.loads(body,object_pairs_hook=object_fields)
    if not isinstance(payload,list): raise ValueError('Original API rows are not a list')
    rows={}; last=None
    for row in payload:
        if not isinstance(row,dict): raise ValueError('Original API row shape differs')
        if (kind=='oi' and row.get('symbol')!=symbol) or ('symbol' in row and row['symbol']!=symbol):
            raise ValueError('Original API symbol differs')
        timestamp=row.get('timestamp')
        if (type(timestamp) is not int or timestamp%300000 or
            (enforce_window and not start_ms<=timestamp<=end_ms)):
            raise ValueError('Original API epoch/grid/window differs')
        if last is not None and timestamp<=last: raise ValueError('Duplicate/unordered API snapshot')
        for field in DECIMAL_FIELDS[kind]:
            if field not in row: raise ValueError('Original API required field absent')
            if row[field] is not None: number(row[field])
        # Nulls and actual decimal zeros are retained; no value is substituted.
        rows[timestamp]=dict(row); last=timestamp
    return rows


def pair(archive, api):
    source=number(archive)
    if api is None: return dict(state='api_null_unknown',archive_native=archive,api_native=None)
    counterpart=number(api)
    with localcontext() as ctx:
        ctx.prec=50; error=abs(source-counterpart)
        return dict(state='known',archive_native=archive,api_native=api,
            exact_decimal_equal=source==counterpart,absolute_error_decimal=format(error,'f'),
            relative_error_decimal=str(error/source) if source else None,
            archive_zero_relative_error_undefined=source==0)


def compare_fields(metric, api_rows, kind):
    if kind not in PAIR_FIELDS or metric['symbol'] not in SYMBOLS:
        raise ValueError('Unregistered archive field/symbol')
    label=datetime.strptime(metric['create_time'],'%Y-%m-%d %H:%M:%S')
    ts=int(label.replace(tzinfo=timezone.utc).timestamp()*1000)
    result=dict(source_label=metric['create_time'],symbol=metric['symbol'],kind=kind,
        naive_label_as_utc_is_comparison_assumption=True,
        measurement_unit_or_publication_certified=False)
    for name,offset in [('label_time',0),('label_plus_5m',300000)]:
        counterpart=api_rows.get(ts+offset)
        if counterpart is None:
            result[name]=dict(state='missing_counterpart',comparison_epoch=ts+offset,fields=None)
            continue
        if 'symbol' in counterpart and counterpart['symbol']!=metric['symbol']:
            raise ValueError('Foreign API counterpart')
        if counterpart['timestamp']!=ts+offset: raise ValueError('API counterpart index differs')
        out=dict(state='known_counterpart',comparison_epoch=ts+offset,
            fields={alias:pair(metric[csv_field],counterpart[api_field])
                    for alias,csv_field,api_field in PAIR_FIELDS[kind]})
        if kind=='taker':
            buy,sell=counterpart['buyVol'],counterpart['sellVol']
            qty=dict(buy_native=buy,sell_native=sell,reported_ratio_native=counterpart['buySellRatio'])
            if buy is None or sell is None: qty.update(state='api_null_unknown',derived_ratio_decimal=None)
            elif number(sell)==0: qty.update(state='zero_sell_undefined_ratio',derived_ratio_decimal=None)
            else:
                with localcontext() as ctx:
                    ctx.prec=50; qty.update(state='known',derived_ratio_decimal=str(number(buy)/number(sell)))
            out['native_buy_sell']=qty
        result[name]=out
    return result
