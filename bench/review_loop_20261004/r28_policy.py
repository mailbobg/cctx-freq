"""One sign of a native same-week historical NFCI revision after original sizing."""
from decimal import Decimal, localcontext
import re

import pandas as pd
from r10_gold import WINDOWS
from r21_basis import monday_time
from r28_revision import same_week_revision

ASSETS = ['BTC', 'ETH']


def pair_for(monday):
    monday_time(monday)
    return dict(monday=str(monday.date()),
        current_vintage=str((monday-pd.Timedelta(days=2)).date()),
        prior_vintage=str((monday-pd.Timedelta(days=9)).date()),
        observation_week=str((monday-pd.Timedelta(days=17)).date()))


def difference(current, prior):
    if any(not isinstance(s, str) or not re.fullmatch(r'-?\d+(?:\.\d+)?', s)
           for s in (current, prior)):
        raise ValueError('Unknown or invalid native signed value')
    with localcontext() as ctx:
        ctx.prec = max(50, len(current)+len(prior)+2)
        return Decimal(current)-Decimal(prior)


def validate_feature(monday, value):
    pair = pair_for(monday)
    if (value['decision'] != monday or value['observation_day'] != pd.Timestamp(pair['observation_week'], tz='UTC') or
        value['current_vintage'] != pair['current_vintage'] or value['prior_vintage'] != pair['prior_vintage'] or
        value['available_at_assumed'] != monday or value['known'] is not True or
        value['state'] != 'known_same_week_revision' or type(value['allow']) is not bool):
        raise ValueError('Unknown/future/altered revision identity or availability')
    for key in ('source_sha256', 'prior_source_sha256'):
        if not isinstance(value.get(key), str) or not re.fullmatch('[0-9a-f]{64}', value[key]):
            raise ValueError('Missing or foreign native source identity')
    delta = difference(value['current_native'], value['prior_native'])
    if (value['revision_decimal'] != format(delta, 'f') or value['allow'] != (delta <= 0) or
        value['historical_first_public_receipt_certified'] is not False):
        raise ValueError('Native revision sign/value or public-clock certificate differs')


def feature(monday, current, prior):
    pair = pair_for(monday)
    out = same_week_revision(pair, current, prior)
    if out['state'] != 'known_same_week_revision':
        raise ValueError('Unavailable required historical revision is not a cash feature')
    delta = difference(out['current_native'], out['prior_native'])
    value = dict(decision=monday, observation_day=pd.Timestamp(pair['observation_week'], tz='UTC'),
        current_vintage=pair['current_vintage'], prior_vintage=pair['prior_vintage'],
        current_native=out['current_native'], prior_native=out['prior_native'],
        revision_decimal=out['revision_decimal'], known=True, state=out['state'], allow=bool(delta <= 0),
        reason='nonpositive_same_week_revision' if delta <= 0 else 'upward_same_week_revision',
        available_at_assumed=monday, historical_first_public_receipt_certified=False,
        source_sha256=current['source_sha256'], prior_source_sha256=prior['source_sha256'],
        source_record=current['source_record'], prior_source_record=prior['source_record'],
        current_capture_utc=current['current_capture_utc'], prior_capture_utc=prior['current_capture_utc'],
        availability_assumption='Historical Saturday as-of date plus intervening Sunday before MondayUTC00; actual receipt/timezone boundary unverified')
    validate_feature(monday, value)
    return value


def overlay(base, features):
    if (list(base) != ASSETS or str(base.index.tz) != 'UTC' or not base.index.is_unique or
        not base.index.is_monotonic_increasing or not base.index.equals(base.index.normalize())):
        raise ValueError('Unreviewed original spot identities/calendar')
    required = {d for _, start, end in WINDOWS for d in pd.date_range(start, end, freq='W-MON', tz='UTC')}
    out = base*0.
    audit = []
    for sunday in base.index[base.index.weekday == 6]:
        monday = sunday+pd.Timedelta(days=1)
        if monday not in required:
            continue
        if monday not in features:
            raise ValueError('Missing required economic revision decision')
        value = features[monday]
        validate_feature(monday, value)
        if value['allow']:
            out.loc[sunday] = base.loc[sunday]
        audit.append(dict(**value, **{f'{kind}_{a}': float(weights.loc[sunday, a])
            for kind, weights in [('baseline', base), ('target', out)] for a in ASSETS}))
    return out, pd.DataFrame(audit)
