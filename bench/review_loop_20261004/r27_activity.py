"""One exact native-quantity activity majority; no new exposure or price rule."""
from decimal import localcontext
import re

import pandas as pd

from r21_basis import number, monday_time
from r22_sources import PANEL
from r10_gold import WINDOWS

ASSETS=['BTC','ETH']


def aggregate(vote, friday):
    values=vote['native_daily_volumes']
    expected=pd.date_range(friday-pd.Timedelta(days=59),friday,freq='D').strftime('%Y-%m-%d').tolist()
    if [v['native_day'] for v in values]!=expected:raise ValueError('Missing/foreign native activity calendar')
    for v in values:
        if not re.fullmatch('[0-9a-f]{64}',v.get('source_sha256','')) or number(v['volume_decimal'])<=0:
            raise ValueError('Unknown/inactive native activity source')
    with localcontext() as ctx:
        ctx.prec=50
        prior=sum(number(v['volume_decimal']) for v in values[:30])
        current=sum(number(v['volume_decimal']) for v in values[30:])
    return current,prior


def feature(decision, series):
    monday_time(decision)
    if set(series)!=set(PANEL):raise ValueError('Incomplete/foreign activity panel')
    friday=decision-pd.Timedelta(days=3);votes=[]
    days=pd.date_range(friday-pd.Timedelta(days=59),friday,freq='D')
    for symbol in PANEL:
        frame=series[symbol];cells=[]
        for day in days:
            if day not in frame:raise ValueError('Missing exact native activity day')
            row=frame[day]
            if (row.get('source_symbol')!=symbol or row.get('interval_end')!=day+pd.Timedelta(days=1) or
                row.get('trades',0)<=0 or number(row.get('volume'))<=0 or number(row.get('quote_volume'))<=0 or
                not re.fullmatch('[0-9a-f]{64}',row.get('source_sha256',''))):
                raise ValueError('Foreign/invalid/future native traded-activity observation')
            cells.append(dict(native_day=day.strftime('%Y-%m-%d'),volume_decimal=row['volume'],source_sha256=row['source_sha256']))
        vote=dict(symbol=symbol,native_daily_volumes=cells)
        current,prior=aggregate(vote,friday)
        vote.update(current_volume_sum_decimal=format(current,'f'),prior_volume_sum_decimal=format(prior,'f'),positive=bool(current>prior))
        votes.append(vote)
    count=sum(v['positive'] for v in votes);allow=2*count>len(PANEL)
    return dict(decision=decision,observation_day=friday,prior_observation_day=friday-pd.Timedelta(days=59),
        current_start=friday-pd.Timedelta(days=29),prior_start=friday-pd.Timedelta(days=59),prior_end=friday-pd.Timedelta(days=30),
        available_at_assumed=friday+pd.Timedelta(days=2),historical_publication_certified=False,
        availability_assumption='Completed Friday trade interval + one full extra UTC day, not authenticated receipt',
        panel=list(PANEL),positive_count=count,known=True,allow=bool(allow),
        reason='activity_majority_expanding' if allow else 'no_expanding_activity_majority',native_votes=votes)


def overlay(base, features):
    if (list(base)!=ASSETS or str(base.index.tz)!='UTC' or not base.index.is_unique or
        not base.index.is_monotonic_increasing or not base.index.equals(base.index.normalize())):
        raise ValueError('Unreviewed original spot weight calendar/identities')
    required={d for _,start,end in WINDOWS for d in pd.date_range(start,end,freq='W-MON',tz='UTC')}
    out=base*0.;audit=[]
    for sunday in base.index[base.index.weekday==6]:
        monday=sunday+pd.Timedelta(days=1)
        if monday not in required:continue
        if monday not in features:raise ValueError('Missing required activity decision')
        f=features[monday];friday=monday-pd.Timedelta(days=3)
        if (f['decision']!=monday or f['observation_day']!=friday or
            f['prior_observation_day']!=friday-pd.Timedelta(days=59) or
            f['current_start']!=friday-pd.Timedelta(days=29) or f['prior_start']!=friday-pd.Timedelta(days=59) or
            f['prior_end']!=friday-pd.Timedelta(days=30) or f['available_at_assumed']!=friday+pd.Timedelta(days=2) or
            f['panel']!=list(PANEL) or f['known'] is not True or type(f['allow']) is not bool or
            type(f['positive_count']) is not int or not 0<=f['positive_count']<=len(PANEL)):
            raise ValueError('Unknown/future/altered activity feature')
        votes=f['native_votes']
        if [v['symbol'] for v in votes]!=list(PANEL):raise ValueError('Native activity member identity differs')
        for vote in votes:
            current,prior=aggregate(vote,friday)
            if (number(vote['current_volume_sum_decimal'])!=current or number(vote['prior_volume_sum_decimal'])!=prior or
                type(vote['positive']) is not bool or vote['positive']!=(current>prior)):
                raise ValueError('Native activity sum/direction differs')
        count=sum(v['positive'] for v in votes)
        if f['positive_count']!=count or f['allow']!=(2*count>len(PANEL)):
            raise ValueError('Native activity count/majority differs')
        if f['allow']:out.loc[sunday]=base.loc[sunday]
        metadata={k:v for k,v in f.items() if k!='native_votes'}
        for vote in votes:metadata[vote['symbol']+'_positive']=vote['positive']
        audit.append(dict(**metadata,**{f'{kind}_{a}':float(weights.loc[sunday,a])
            for kind,weights in [('baseline',base),('target',out)] for a in ASSETS}))
    return out,pd.DataFrame(audit)
