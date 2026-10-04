"""One exact majority on a fixed external panel; no new sizing or time search."""
import re

import pandas as pd

from r21_basis import number,monday_time
from r22_sources import PANEL

ASSETS=['BTC','ETH']


def feature(decision,series):
    monday_time(decision)
    if set(series)!=set(PANEL):raise ValueError('Incomplete/foreign panel membership')
    friday=decision-pd.Timedelta(days=3);prior=friday-pd.Timedelta(days=30);votes=[]
    for symbol in PANEL:
        frame=series[symbol]
        if friday not in frame or prior not in frame:raise ValueError('Missing exact native panel observation')
        now,old=frame[friday],frame[prior]
        for day,row in ((friday,now),(prior,old)):
            if row.get('source_symbol')!=symbol or row.get('interval_end')!=day+pd.Timedelta(days=1):raise ValueError('Native symbol/interval identity differs')
            if row.get('trades',0)<=0 or number(row['volume'])<=0 or number(row['quote_volume'])<=0 or number(row['close'])<=0:raise ValueError('Invalid/inactive native panel observation')
            if not re.fullmatch(r'[0-9a-f]{64}',row.get('source_sha256','')):raise ValueError('Missing native source identity')
        up=number(now['close'])>number(old['close'])
        votes.append(dict(symbol=symbol,friday_close_decimal=now['close'],prior_close_decimal=old['close'],positive=bool(up),
            friday_source_sha256=now['source_sha256'],prior_source_sha256=old['source_sha256']))
    positives=sum(v['positive'] for v in votes);allow=2*positives>len(PANEL)
    return dict(decision=decision,observation_day=friday,prior_observation_day=prior,
        available_at_assumed=friday+pd.Timedelta(days=2),historical_publication_certified=False,
        availability_assumption='Completed Friday trade interval + one full extra UTC day, not authenticated receipt',
        panel=list(PANEL),positive_count=positives,known=True,allow=bool(allow),
        reason='majority_positive' if allow else 'no_majority',native_votes=votes)


def overlay(base,features):
    if list(base)!=ASSETS or str(base.index.tz)!='UTC' or not base.index.is_unique or not base.index.is_monotonic_increasing or not base.index.equals(base.index.normalize()):
        raise ValueError('Unreviewed original weight calendar/identities')
    out=base*0.;audit=[]
    for day in base.index[base.index.weekday==6]:
        monday=day+pd.Timedelta(days=1)
        if monday not in features:continue  # Explicitly outside the registered source windows.
        f=features[monday]
        if f['decision']!=monday or f['observation_day']!=monday-pd.Timedelta(days=3) or f['prior_observation_day']!=f['observation_day']-pd.Timedelta(days=30):raise ValueError('Observation/decision identity differs')
        if f['available_at_assumed']>monday or f['panel']!=list(PANEL) or f['known'] is not True or type(f['allow']) is not bool or type(f['positive_count']) is not int or not 0<=f['positive_count']<=len(PANEL) or f['allow']!=(2*f['positive_count']>len(PANEL)):
            raise ValueError('Unknown/future/altered majority feature')
        votes=f['native_votes']
        if [v['symbol'] for v in votes]!=list(PANEL) or sum(v['positive'] is True for v in votes)!=f['positive_count']:
            raise ValueError('Native panel vote membership/count differs')
        for vote in votes:
            if type(vote['positive']) is not bool or vote['positive']!=(number(vote['friday_close_decimal'])>number(vote['prior_close_decimal'])):
                raise ValueError('Native vote direction differs from original decimal closes')
        if f['allow']:out.loc[day]=base.loc[day]
        metadata={k:v for k,v in f.items() if k!='native_votes'}
        for vote in f['native_votes']:
            metadata[vote['symbol']+'_positive']=vote['positive']
        audit.append(dict(**metadata,**{f'{kind}_{a}':float(weights.loc[day,a]) for kind,weights in [('baseline',base),('target',out)] for a in ASSETS}))
    return out,pd.DataFrame(audit)
