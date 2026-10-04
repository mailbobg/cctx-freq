"""Funding-only necessary-condition screen; no executable or basis-inclusive PnL."""
import numpy as np
import pandas as pd
import hashlib
import json
from pathlib import Path
import requests

START = pd.Timestamp('2026-07-05T00:00:00Z')
END = pd.Timestamp('2026-10-04T00:00:00Z')
COST = .0038  # four 5bps fees, four 2bps adverse fills, 10bps basis allowance


def archived_fetch(records):
    pages = {r['url']: r for r in records}
    bases = {'bybit': 'https://api.bybit.com', 'okx': 'https://www.okx.com'}
    def fetch(venue, path, params):
        url = requests.Request('GET', bases[venue] + path, params=params).prepare().url
        if url not in pages: raise ValueError('requested archive page absent')
        record = pages[url]; body = Path(record['path']).read_bytes()
        if hashlib.sha256(body).hexdigest() != record['sha256']:
            raise ValueError('funding archive hash mismatch')
        return json.loads(body)
    return fetch


def validate_history(series, start=START, end=END):
    if series.index.has_duplicates or not np.isfinite(series).all():
        raise ValueError('duplicate/nonfinite funding events')
    data = series.sort_index()
    # BTC/ETH audit accepts only a complete observed 8h UTC grid. An interval change
    # is an unreviewed input, never an invitation to invent or discard a settlement.
    expected = pd.date_range(start, end, freq='8h', inclusive='left', tz='UTC')
    actual = data.index[(data.index >= start) & (data.index < end)]
    if not actual.equals(expected):
        raise ValueError('funding clock changed or history incomplete')
    return data.loc[expected]


def period_sum(series, start, end):
    return float(series.loc[(series.index > start) & (series.index <= end)].sum())


def weekly_probe(bybit, okx):
    a = validate_history(bybit); b = validate_history(okx)
    rows = []
    for date in pd.date_range(START.ceil('D'), END, freq='W-MON', tz='UTC'):
        entry = date + pd.Timedelta(hours=1); exit_time = entry + pd.Timedelta(days=7)
        available = entry - pd.Timedelta(hours=1)
        lag_start = available - pd.Timedelta(days=7)
        if lag_start < START or exit_time >= END:
            continue
        forecast = period_sum(a, lag_start, available) - period_sum(b, lag_start, available)
        direction = 1 if forecast >= 0 else -1
        actual = direction * (period_sum(a, entry, exit_time) - period_sum(b, entry, exit_time))
        rows.append({'decision': entry.isoformat(), 'exit': exit_time.isoformat(),
                     'short_venue': 'bybit' if direction == 1 else 'okx',
                     'forecast_spread': abs(forecast), 'realized_spread': actual,
                     'cost_hurdle': COST, 'eligible': abs(forecast) > COST,
                     'carry_less_cost': actual - COST})
    return rows


def collect_history(venue, symbol, start_ms, end_ms, fetch):
    cursor = end_ms - 1; observations = {}
    for _ in range(12):
        if venue == 'bybit':
            path = '/v5/market/funding/history'
            params = {'category': 'linear', 'symbol': symbol + 'USDT', 'endTime': cursor, 'limit': 200}
        elif venue == 'okx':
            path = '/api/v5/public/funding-rate-history'
            params = {'instId': symbol + '-USDT-SWAP', 'after': str(cursor + 1), 'limit': '100'}
        else:
            raise ValueError('unsupported venue')
        raw = fetch(venue, path, params)
        if venue == 'bybit':
            if raw.get('retCode') != 0: raise ValueError('Bybit history request failed')
            rows = raw['result']['list']
        else:
            if raw.get('code') != '0': raise ValueError('OKX history request failed')
            rows = raw['data']
        if not rows: raise ValueError('funding history ended before requested coverage')
        times = []
        for row in rows:
            if row.get('symbol' if venue == 'bybit' else 'instId') != (
                    symbol + 'USDT' if venue == 'bybit' else symbol + '-USDT-SWAP'):
                raise ValueError('funding instrument mismatch')
            ts = int(row['fundingRateTimestamp' if venue == 'bybit' else 'fundingTime'])
            if ts > cursor or ts in observations: raise ValueError('pagination repeated/future settlement')
            rate = row['fundingRate' if venue == 'bybit' else 'realizedRate']
            if rate is None or rate == '': raise ValueError('realized funding missing')
            observations[ts] = float(rate); times.append(ts)
        oldest = min(times)
        if oldest <= start_ms:
            result = pd.Series(observations, dtype=float).sort_index()
            result.index = pd.to_datetime(result.index, unit='ms', utc=True)
            return result.loc[(result.index >= pd.to_datetime(start_ms, unit='ms', utc=True)) &
                              (result.index < pd.to_datetime(end_ms, unit='ms', utc=True))]
        cursor = oldest - 1
    raise ValueError('bounded history pagination incomplete')
