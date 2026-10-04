"""Exact original-report four-week comparison, after unchanged joint sizing."""
from datetime import date,datetime,timedelta
import re
import pandas as pd
from r10_gold import WINDOWS
from r31_native import parse_publisher
from r32_publication import parse_delayed_report
from r33_calendar import eligibility

ASSETS=['BTC','ETH']


def parse_report(body,release):
    # Compose reviewed strict parsers; do not weaken old Thursday/Friday logic.
    if date.fromisoformat(release).weekday()==0:
        if release!='2025-12-29':raise ValueError('Unreviewed native Monday report exception')
        return parse_delayed_report(body)
    return parse_publisher(body,release)


def validate(value):
    decision=datetime.fromisoformat(value['monday']+'T00:00:00+00:00')
    if decision.weekday()!=0:raise ValueError('Foreign weekly decision identity')
    for kind in ('current','prior'):
        row=value[kind]
        if row['native_units']!='Millions of dollars':raise ValueError('Unverified native report unit')
        if type(row['week_average_million_usd']) is not int or row['week_average_million_usd']<0:
            raise ValueError('Required native quantity unknown/invalid, never zero')
        observation=date.fromisoformat(row['observation_date'])
        if row['observation_date']!=row['expected_observation_date'] or observation.weekday()!=2:
            raise ValueError('Actual native observation differs from exact selected source')
        if (row['modeled_eligible_utc']!=eligibility(row['release_date']).isoformat() or
            eligibility(row['release_date'])>decision):raise ValueError('Native report eligibility is after decision or altered')
        if not re.fullmatch('[0-9a-f]{64}',row.get('source_body_sha256','')):
            raise ValueError('Original source body identity absent')
    if date.fromisoformat(value['prior']['observation_date'])!=date.fromisoformat(value['current']['observation_date'])-timedelta(days=28):
        raise ValueError('Exact four-week observation comparator differs')
    contraction=value['current']['week_average_million_usd']<value['prior']['week_average_million_usd']
    if type(value['contraction']) is not bool or value['contraction']!=contraction or value['known'] is not True:
        raise ValueError('Changed native quantity feature sign/known state')


def feature(pair,reports):
    out=dict(monday=pair['monday'],window=pair['window'],known=True,
             first_public_receipt_or_unchanged_original_edition_certified=False)
    for kind in ('current','prior'):
        planned=pair[kind];report=reports[planned['release_date']]
        if report['release_date']!=planned['release_date']:raise ValueError('Original report edition identity differs')
        out[kind]=dict(**planned,observation_date=report['observation_date'],
            native_units=report['native_units'],week_average_million_usd=report['week_average_million_usd'],
            source_body_sha256=report['source_body_sha256'])
    a,b=(out[k]['week_average_million_usd'] for k in ('current','prior'))
    if type(a) is not int or type(b) is not int:raise ValueError('Required native quantity unknown, never zero')
    out['contraction']=a<b
    validate(out);return out


def overlay(base,features):
    if (list(base)!=ASSETS or str(base.index.tz)!='UTC' or not base.index.is_unique or
        not base.index.is_monotonic_increasing or not base.index.equals(base.index.normalize())):
        raise ValueError('Original joint target calendar/asset identity differs')
    required={d for _,start,end in WINDOWS for d in pd.date_range(start,end,freq='W-MON',tz='UTC')}
    out=base.copy();audit=[]
    for sunday in base.index[base.index.weekday==6]:
        monday=sunday+pd.Timedelta(days=1)
        if monday not in required:continue
        if monday not in features:raise ValueError('Missing required native quantity feature')
        f=features[monday];validate(f)
        if f['monday']!=str(monday.date()):raise ValueError('Foreign native feature decision')
        if f['contraction']:out.loc[sunday]=0.
        flat=dict(monday=f['monday'],window=f['window'],known=True,contraction=f['contraction'])
        for kind in ('current','prior'):
            flat.update({kind+'_'+k:v for k,v in f[kind].items()})
        flat.update({f'{kind}_{a}':float(w.loc[sunday,a]) for kind,w in [('baseline',base),('target',out)] for a in ASSETS})
        audit.append(flat)
    return out,pd.DataFrame(audit)
