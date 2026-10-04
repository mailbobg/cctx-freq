"""Small fixed public-source feasibility probe; no prices, orders or P&L."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

import requests
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / 'r19_sources'
VINTAGES = ('2021-01-30', '2024-01-06', '2026-08-29')


def archive_url(vintage, start):
    return 'https://alfred.stlouisfed.org/graph/alfredgraph.csv?' + urlencode(dict(
        id='VIXCLS,VXVCLS', vintage_date=f'{vintage},{vintage}', cosd=start, coed=vintage))


def probe_targets():
    return {
        'alfred_help': 'https://alfred.stlouisfed.org/help/downloaddata',
        'alfred_vix_identity': 'https://alfred.stlouisfed.org/series?seid=VIXCLS',
        'alfred_vix3m_identity': 'https://alfred.stlouisfed.org/series?seid=VXVCLS',
        'cboe_term_definition': 'https://www.cboe.com/tradable_products/vix/term_structure/',
        'cboe_current_vix': 'https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv',
        'cboe_current_vix3m_lead': 'https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX3M_History.csv',
        **{'archive_' + v: archive_url(v, start) for v, start in zip(VINTAGES, ('2021-01-17', '2023-12-24', '2026-08-16'))},
    }


def full_targets():
    from r10_gold import WINDOWS
    targets = probe_targets()
    for _, start, end in WINDOWS:
        for monday in pd.date_range(start, end, freq='W-MON', tz='UTC'):
            saturday = monday - pd.Timedelta(days=2)
            v = str(saturday.date())
            targets['archive_' + v] = archive_url(v, str((saturday-pd.Timedelta(days=13)).date()))
    return targets


def collect(targets, fetch=False):
    DATA.mkdir(exist_ok=True); (DATA / 'raw').mkdir(exist_ok=True)
    path = DATA / 'manifest.json'
    sources = json.loads(path.read_text())['sources'] if path.exists() else {}
    missing = []
    for key, url in targets.items():
        if key not in sources:
            if not fetch: raise ValueError('Explicit fetch required for absent source ' + key)
            missing.append((key, url)); continue
        r = sources[key]
        if r['url'] != url: raise ValueError('Source request identity changed ' + key)
        if r.get('path'):
            body = (DATA / r['path']).read_bytes()
            if len(body) != r['bytes'] or hashlib.sha256(body).hexdigest() != r['sha256']:
                raise ValueError('Retained body changed ' + key)

    def request(item):
        key, url = item
        began = datetime.now(timezone.utc).isoformat()
        try:
            r = requests.get(url, timeout=(5, 25)); body = r.content
            received = datetime.now(timezone.utc).isoformat()
            name = hashlib.sha256((key + ' ' + url).encode()).hexdigest()
            (DATA / 'raw' / name).write_bytes(body)
            return key, dict(url=url, final_url=r.url, requested_utc=began, retrieved_utc=received,
                status=r.status_code, bytes=len(body), sha256=hashlib.sha256(body).hexdigest(),
                path='raw/' + name, headers={k:r.headers[k] for k in ('Content-Type', 'Last-Modified', 'ETag') if k in r.headers})
        except requests.RequestException as e:
            return key, dict(url=url, requested_utc=began, failed_utc=datetime.now(timezone.utc).isoformat(),
                status=None, path=None, source_error=type(e).__name__ + ': ' + str(e))

    with ThreadPoolExecutor(max_workers=3) as pool:
        for future in as_completed([pool.submit(request, item) for item in missing]):
            key, record = future.result(); sources[key] = record
            path.write_text(json.dumps(dict(sources=sources, source_only=True, new_performance=False), indent=2, sort_keys=True) + '\n')
            print(key, record['status'], record.get('bytes', 0), flush=True)
    return {key:((DATA / sources[key]['path']).read_bytes() if sources[key].get('path') else None, sources[key]) for key in targets}


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--fetch', action='store_true'); p.add_argument('--full', action='store_true')
    args = p.parse_args(); records = collect(full_targets() if args.full else probe_targets(), args.fetch)
    for key, (body, record) in (records.items() if not args.full else []):
        if key.startswith('archive_') and body is not None:
            lines = body.decode('utf-8', errors='replace').splitlines()
            print(key, 'header', lines[:1], 'last rows', lines[-2:])
