"""Keep version identity and native signed decimals before same-week subtraction."""
import csv
from datetime import date, timedelta
from decimal import Decimal, localcontext
import io
import re

from r28_sources import iso_date


def parse_archive(body, vintage):
    cutoff = iso_date(vintage)
    if cutoff.weekday() != 5:
        raise ValueError('Unreviewed vintage weekday')
    earliest = cutoff-timedelta(days=28)
    reader = csv.reader(io.StringIO(body.decode('utf-8')))
    if next(reader, None) != ['observation_date', 'NFCI_'+vintage.replace('-', '')]:
        raise ValueError('Native series or historical version header differs')
    rows = {}
    previous = None
    for fields in reader:
        if len(fields) != 2:
            raise ValueError('Native column count differs')
        day = iso_date(fields[0])
        if day.weekday() != 4 or not earliest <= day <= cutoff:
            raise ValueError('Native observation outside the frozen Friday grid/range')
        if previous is not None and day <= previous:
            raise ValueError('Native observations duplicated or unordered')
        text = fields[1]
        if text in ('', '.'):
            number = None
        elif re.fullmatch(r'-?\d+(?:\.\d+)?', text):
            number = Decimal(text)
            if not number.is_finite():
                raise ValueError('Native value is not finite')
        else:
            raise ValueError('Unreviewed native signed decimal text')
        rows[day] = dict(native_value=text, decimal_value=number)
        previous = day
    return dict(series='NFCI', vintage=vintage, rows=rows,
                observation_bounds=(earliest.isoformat(), vintage))


def same_week_revision(pair, current, prior):
    monday = iso_date(pair['monday'])
    if monday.weekday() != 0 or pair != dict(
        monday=monday.isoformat(),
        current_vintage=(monday-timedelta(days=2)).isoformat(),
        prior_vintage=(monday-timedelta(days=9)).isoformat(),
        observation_week=(monday-timedelta(days=17)).isoformat()):
        raise ValueError('Frozen same-week identity or vintage dates differ')
    for parsed, key in ((current, 'current_vintage'), (prior, 'prior_vintage')):
        if parsed['series'] != 'NFCI' or parsed['vintage'] != pair[key]:
            raise ValueError('Historical version counterpart differs')
    week = iso_date(pair['observation_week'])
    old = prior['rows'].get(week)
    now = current['rows'].get(week)
    out = dict(**pair, prior_native=old['native_value'] if old else None,
               current_native=now['native_value'] if now else None,
               revision_decimal=None, historical_first_public_receipt_certified=False)
    if old is None or now is None:
        out['state'] = 'missing_required_observation'
    elif old['decimal_value'] is None or now['decimal_value'] is None:
        out['state'] = 'unknown_native_value'
    else:
        with localcontext() as ctx:
            ctx.prec = max(50, len(old['native_value'])+len(now['native_value'])+2)
            difference = now['decimal_value']-old['decimal_value']
        out.update(state='known_same_week_revision', revision_decimal=format(difference, 'f'))
    return out
