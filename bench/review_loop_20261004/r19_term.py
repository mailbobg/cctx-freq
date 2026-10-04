"""One preregistered weekly SPX volatility-term gate on unchanged crypto risk."""
import csv
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import re

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ASSETS = ['BTC', 'ETH']


def date_label(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('Unreviewed date-only label')
    try: return pd.Timestamp(value, tz='UTC')
    except ValueError as e: raise ValueError('Invalid date') from e


def decimal_index(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+(?:\.\d+)?', value) or Decimal(value) <= 0:
        raise ValueError('Invalid positive native decimal index')
    return value


def parse_archive(body, vintage):
    limit = date_label(vintage)
    rows = list(csv.reader(io.StringIO(body.decode('utf-8'))))
    expected = ['observation_date', *[f'{s}_{vintage.replace("-", "")}' for s in ('VIXCLS', 'VXVCLS')]]
    if not rows or rows[0] != expected: raise ValueError('Archive series/vintage header identity mismatch')
    parsed, previous = [], None
    for row in rows[1:]:
        if len(row) != 3: raise ValueError('Unreviewed archived observation schema')
        day = date_label(row[0])
        if day.weekday() > 4 or day > limit or (previous is not None and day <= previous):
            raise ValueError('Future, weekend, duplicate or unordered archive calendar')
        values = [None if value in ('', '.') else decimal_index(value) for value in row[1:]]
        parsed.append((day, *values)); previous = day
    if not parsed: raise ValueError('Empty archive observations')
    frame = pd.DataFrame(parsed, columns=['observation_date', 'vix_decimal', 'vix3m_decimal'], dtype=object).set_index('observation_date')
    frame.index = pd.DatetimeIndex(frame.index)
    frame.attrs['requested_vintage'] = vintage
    return frame


def parse_counterpart(body, required_start=None):
    begin = date_label(required_start) if required_start is not None else None
    rows = list(csv.reader(io.StringIO(body.decode('utf-8-sig'))))
    if not rows or rows[0] != ['DATE','OPEN','HIGH','LOW','CLOSE']:
        raise ValueError('Current Cboe counterpart schema changed')
    parsed, previous, outside_errors = [], None, []
    for row in rows[1:]:
        if len(row) != 5 or not re.fullmatch(r'\d{2}/\d{2}/\d{4}', row[0]): raise ValueError('Counterpart schema/date error')
        day = pd.Timestamp(datetime.strptime(row[0], '%m/%d/%Y'), tz='UTC')
        if day.weekday() > 4 or (previous is not None and day <= previous): raise ValueError('Counterpart calendar error')
        values = [Decimal(decimal_index(v)) for v in row[1:]]
        op, high, low, close = values
        if low > min(op, close) or high < max(op, close):
            if begin is None or day >= begin: raise ValueError('Physically inconsistent counterpart OHLC')
            outside_errors.append(row)
        parsed.append((day, row[4])); previous = day
    if not parsed: raise ValueError('Empty counterpart')
    frame = pd.DataFrame(parsed, columns=['observation_date','close_decimal']).set_index('observation_date')
    frame.attrs['required_start'] = required_start
    frame.attrs['outside_required_ohlc_errors'] = outside_errors
    return frame


def decision_feature(decision, frame, vintage, raw_sha, receipt):
    if not isinstance(decision, pd.Timestamp) or str(decision.tz) != 'UTC' or decision != decision.normalize() or decision.weekday() != 0:
        raise ValueError('Only exact Monday UTC OPEN decisions are permitted')
    saturday = decision - pd.Timedelta(days=2)
    if date_label(vintage) != saturday or frame.attrs.get('requested_vintage') != vintage:
        raise ValueError('Requested preceding Saturday archive identity changed')
    if not re.fullmatch(r'[0-9a-f]{64}', raw_sha): raise ValueError('Missing raw-body SHA')
    if not isinstance(receipt, pd.Timestamp) or str(receipt.tz) != 'UTC' or receipt < saturday:
        raise ValueError('Unknown current local source receipt')
    available = saturday + pd.Timedelta(days=2)
    friday = decision - pd.Timedelta(days=3)
    # This is a conservative regular-session completion bound, not a promise
    # of Friday opening, early-close hours, or the archive publication clock.
    completion = pd.Timestamp(str(friday.date()) + ' 16:15', tz='America/New_York').tz_convert('UTC')
    if available > decision or completion > available: raise ValueError('Future feature knowledge')
    values = frame.loc[friday].to_list() if friday in frame.index else [None, None]
    values = [None if pd.isna(v) else v for v in values]
    known = all(v is not None for v in values)
    if known: values = [decimal_index(v) for v in values]
    allow = known and Decimal(values[0]) <= Decimal(values[1])
    return dict(decision=decision, requested_vintage=vintage, observation_day=friday,
        observation_completion_bound=completion, available_at=available, local_receipt=receipt,
        raw_sha256=raw_sha, historical_evidence_type='provider_archived_vintage',
        vix_decimal=values[0], vix3m_decimal=values[1], known=known, allow=bool(allow),
        reason=('normal_term' if allow else 'inverted_term') if known else 'missing_friday')


def overlay(base, features):
    if list(base) != ASSETS or str(base.index.tz) != 'UTC' or not base.index.is_unique or not base.index.is_monotonic_increasing or not base.index.equals(base.index.normalize()):
        raise ValueError('Unreviewed base identities/daily calendar')
    out, rows = base*0., []
    for day in base.index[base.index.weekday == 6]:
        monday = day + pd.Timedelta(days=1)
        if monday not in features: continue  # Explicitly zero outside frozen source coverage.
        f = features[monday]
        if f['decision'] != monday or f['available_at'] > monday or type(f['known']) is not bool or type(f['allow']) is not bool or (f['allow'] and not f['known']):
            raise ValueError('Unknown or future decision-feature identity')
        if f['allow']: out.loc[day] = base.loc[day]
        rows.append(dict(**f, **{f'{kind}_{a}':float(weights.loc[day,a]) for kind,weights in [('baseline',base),('target',out)] for a in ASSETS}))
    return out, pd.DataFrame(rows)
