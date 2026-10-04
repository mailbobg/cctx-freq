"""One native quantity confirmation via explicit validated label-key lookup."""
from collections import Counter
from datetime import datetime, timedelta
import hashlib
import re

import pandas as pd

from r21_basis import number, monday_time
from r23_inventory import label_groups
from r25_rest import parse_archive_observations
from r26_sources import monday_dates

ASSETS=['BTC','ETH']


def parse_day(body, symbol, file_day, source_sha256):
    if symbol!='BTCUSDT' or not re.fullmatch('[0-9a-f]{64}',source_sha256):
        raise ValueError('Unregistered native stock source identity')
    rows,order=parse_archive_observations(body,symbol,file_day)
    groups=label_groups(rows);index={g['label']:g for g in groups}
    start=datetime.strptime(file_day,'%Y-%m-%d')
    expected={start+timedelta(minutes=5*i) for i in range(288)}
    if set(index)!=expected: raise ValueError('Missing/extra required native daily label')
    if any(not g['identical_native_fields'] for g in groups):
        raise ValueError('Conflicting duplicate native stock label')
    last=index[start+timedelta(minutes=1435)]
    native=last['native_values'][0]
    # This is a declared-label index, not a sorted or corrected publisher file.
    day=pd.Timestamp(file_day,tz='UTC')
    return dict(source_symbol=symbol,file_day=file_day,source_sha256=source_sha256,
        csv_original_sha256=hashlib.sha256(body).hexdigest(),native_label=native['create_time'],
        quantity_decimal=native['sum_open_interest'],original_rows=len(rows),unique_labels=len(index),
        duplicate_multiplicity_histogram=dict(Counter(g['original_rows'] for g in groups)),
        selected_label_original_row_count=last['original_rows'],
        original_source_order_chronological=order['chronological_source_order_valid'],
        original_ordering_decreases=order['ordering_decreases'],
        assumed_interval_end=day+pd.Timedelta(days=1),
        native_label_as_utc_and_completed_day_is_assumption=True,
        actual_measurement_unit_and_publication_certified=False)


def feature(decision, series):
    monday_time(decision)
    friday=decision-pd.Timedelta(days=3);prior=friday-pd.Timedelta(days=30)
    if friday not in series or prior not in series: raise ValueError('Missing exact native inventory observation')
    now,old=series[friday],series[prior]
    for day,row in ((friday,now),(prior,old)):
        expected=day.strftime('%Y-%m-%d')+' 23:55:00'
        if (row.get('source_symbol')!='BTCUSDT' or row.get('file_day')!=day.strftime('%Y-%m-%d') or
            row.get('native_label')!=expected or row.get('assumed_interval_end')!=day+pd.Timedelta(days=1) or
            not re.fullmatch('[0-9a-f]{64}',row.get('source_sha256','')) or
            number(row['quantity_decimal'])<=0 or row.get('unique_labels')!=288):
            raise ValueError('Foreign/invalid/future native stock observation')
    allow=number(now['quantity_decimal'])>number(old['quantity_decimal'])
    return dict(decision=decision,observation_day=friday,prior_observation_day=prior,
        native_symbol='BTCUSDT',current_native_label=now['native_label'],prior_native_label=old['native_label'],
        current_quantity_decimal=now['quantity_decimal'],prior_quantity_decimal=old['quantity_decimal'],
        current_source_sha256=now['source_sha256'],prior_source_sha256=old['source_sha256'],
        available_at_assumed=friday+pd.Timedelta(days=2),historical_publication_certified=False,
        availability_assumption='Complete native Friday declared-label day + one full extra UTC day; not authenticated receipt',
        known=True,allow=bool(allow),reason='inventory_expanding' if allow else 'unchanged_or_contracting')


def overlay(base, features):
    if (list(base)!=ASSETS or str(base.index.tz)!='UTC' or not base.index.is_unique or
        not base.index.is_monotonic_increasing or not base.index.equals(base.index.normalize())):
        raise ValueError('Unreviewed original spot weight identities/calendar')
    required={pd.Timestamp(d) for d in monday_dates()};out=base*0.;audit=[]
    for sunday in base.index[base.index.weekday==6]:
        monday=sunday+pd.Timedelta(days=1)
        if monday not in required: continue
        if monday not in features: raise ValueError('Missing required native inventory decision')
        f=features[monday]
        if (f['decision']!=monday or f['observation_day']!=monday-pd.Timedelta(days=3) or
            f['prior_observation_day']!=f['observation_day']-pd.Timedelta(days=30) or
            f['native_symbol']!='BTCUSDT' or f['known'] is not True or type(f['allow']) is not bool or
            f['available_at_assumed']!=f['observation_day']+pd.Timedelta(days=2) or f['available_at_assumed']>monday or
            f['current_native_label']!=f['observation_day'].strftime('%Y-%m-%d')+' 23:55:00' or
            f['prior_native_label']!=f['prior_observation_day'].strftime('%Y-%m-%d')+' 23:55:00' or
            any(not re.fullmatch('[0-9a-f]{64}',f.get(field,'')) for field in ('current_source_sha256','prior_source_sha256')) or
            number(f['current_quantity_decimal'])<=0 or number(f['prior_quantity_decimal'])<=0 or
            f['allow']!=(number(f['current_quantity_decimal'])>number(f['prior_quantity_decimal']))):
            raise ValueError('Unknown/future/altered inventory confirmation')
        if f['allow']: out.loc[sunday]=base.loc[sunday]
        audit.append(dict(**f,**{f'{kind}_{asset}':float(weights.loc[sunday,asset])
            for kind,weights in [('baseline',base),('target',out)] for asset in ASSETS}))
    return out,pd.DataFrame(audit)
