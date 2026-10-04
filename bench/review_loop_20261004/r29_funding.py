"""Fixed same-date official overnight-rate sign, after original joint sizing."""
from decimal import Decimal
import re

import pandas as pd

from r21_basis import monday_time
from r10_gold import WINDOWS

ASSETS = ['BTC', 'ETH']


def selection(decision, rows):
    monday_time(decision)
    lower = decision-pd.Timedelta(days=7)
    upper = decision-pd.Timedelta(days=4)
    previous = None
    selected = None
    for row in rows:
        day = pd.Timestamp(row['observation_date'], tz='UTC')
        if (not lower <= day <= upper or day.weekday() > 3 or
                previous is not None and day <= previous or
                set(row['native_values']) != {'SOFR', 'EFFR'} or
                type(row['original_row_index']) is not int or row['original_row_index'] < 0):
            raise ValueError('Altered eligible native date, row or series identity')
        values = row['native_values']
        for value in values.values():
            if value not in ('', '.') and not re.fullmatch(r'-?\d+(?:\.\d+)?', value):
                raise ValueError('Invalid native signed rate text')
        if all(v not in ('', '.') for v in values.values()):
            selected = row
        previous = day
    if selected is None:
        raise ValueError('No common known same-date rates in fixed previous Monday-Thursday')
    return selected


def feature(decision, parsed):
    monday_time(decision)
    vintage = str((decision-pd.Timedelta(days=2)).date())
    if (parsed['vintage'] != vintage or tuple(parsed['series']) != ('SOFR', 'EFFR') or
            not re.fullmatch('[0-9a-f]{64}', parsed.get('source_sha256', '')) or
            not parsed.get('source_record') or not parsed.get('current_capture_utc')):
        raise ValueError('Unknown or foreign historical version/source identity')
    lower, upper = (decision-pd.Timedelta(days=n) for n in (7, 4))
    eligible = [r for r in parsed['rows']
                if lower <= pd.Timestamp(r['observation_date'], tz='UTC') <= upper]
    chosen = selection(decision, eligible)
    sofr, effr = (chosen['native_values'][s] for s in ('SOFR', 'EFFR'))
    allow = Decimal(sofr) <= Decimal(effr)
    return dict(decision=decision, vintage=vintage,
        observation_day=pd.Timestamp(chosen['observation_date'], tz='UTC'),
        selected_original_row_index=chosen['original_row_index'],
        eligible_start=lower, eligible_end=upper, native_eligible_observations=eligible,
        sofr_native=sofr, effr_native=effr, known=True, allow=bool(allow),
        reason='nonpositive_official_funding_spread' if allow else 'positive_official_funding_spread',
        available_at_assumed=decision, historical_first_public_receipt_certified=False,
        source_sha256=parsed['source_sha256'], source_record=parsed['source_record'],
        current_capture_utc=parsed['current_capture_utc'],
        availability_assumption='Same past Saturday archive; previous Monday-Thursday rates assumed usable Monday UTC00. No authenticated original public receipt.')


def overlay(base, features):
    if (list(base) != ASSETS or str(base.index.tz) != 'UTC' or not base.index.is_unique or
            not base.index.is_monotonic_increasing or not base.index.equals(base.index.normalize())):
        raise ValueError('Unreviewed original spot weight calendar/identities')
    required = {d for _, start, end in WINDOWS
                for d in pd.date_range(start, end, freq='W-MON', tz='UTC')}
    out = base*0.
    audit = []
    for sunday in base.index[base.index.weekday == 6]:
        monday = sunday+pd.Timedelta(days=1)
        if monday not in required:
            continue
        if monday not in features:
            raise ValueError('Missing required same-date funding decision')
        f = features[monday]
        chosen = selection(monday, f['native_eligible_observations'])
        values = chosen['native_values']
        allow = Decimal(values['SOFR']) <= Decimal(values['EFFR'])
        if (f['decision'] != monday or f['vintage'] != str((monday-pd.Timedelta(days=2)).date()) or
                f['eligible_start'] != monday-pd.Timedelta(days=7) or
                f['eligible_end'] != monday-pd.Timedelta(days=4) or
                f['available_at_assumed'] != monday or f['known'] is not True or
                f['historical_first_public_receipt_certified'] is not False or
                type(f['allow']) is not bool or f['allow'] != allow or
                f['sofr_native'] != values['SOFR'] or f['effr_native'] != values['EFFR'] or
                f['selected_original_row_index'] != chosen['original_row_index'] or
                f['observation_day'] != pd.Timestamp(chosen['observation_date'], tz='UTC') or
                f['reason'] != ('nonpositive_official_funding_spread' if allow else 'positive_official_funding_spread') or
                not re.fullmatch('[0-9a-f]{64}', f.get('source_sha256', '')) or
                not f.get('source_record') or not f.get('current_capture_utc')):
            raise ValueError('Unknown, future or altered native funding feature')
        if allow:
            out.loc[sunday] = base.loc[sunday]
        metadata = {k:v for k,v in f.items() if k != 'native_eligible_observations'}
        audit.append(dict(**metadata, **{f'{kind}_{a}':float(weights.loc[sunday, a])
            for kind, weights in [('baseline', base), ('target', out)] for a in ASSETS}))
    return out, pd.DataFrame(audit)
