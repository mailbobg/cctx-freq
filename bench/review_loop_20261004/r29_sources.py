"""Frozen direct overnight USD funding source probe; no policy or outcomes."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from threading import local
from urllib.parse import urlencode

import requests
from r20_audit import retained

HERE = Path(__file__).resolve().parent
DATA = HERE / 'r29_sources'
MONDAYS = ('2019-09-23', '2021-02-01', '2024-01-01', '2026-08-31')
CONTEXT = {
    'alfred_help': 'https://alfred.stlouisfed.org/help/downloaddata',
    'alfred_sofr_identity': 'https://alfred.stlouisfed.org/series?seid=SOFR',
    'alfred_effr_identity': 'https://alfred.stlouisfed.org/series?seid=EFFR',
    'publisher_sofr': 'https://www.newyorkfed.org/markets/reference-rates/sofr',
    'publisher_effr': 'https://www.newyorkfed.org/markets/reference-rates/effr',
    'publisher_methodology': 'https://www.newyorkfed.org/markets/reference-rates/additional-information-about-reference-rates',
    'publisher_indicative_warning': 'https://www.newyorkfed.org/markets/opolicy/operating_policy_241126',
    'publisher_repo_study': 'https://www.federalreserve.gov/econres/notes/feds-notes/what-happened-in-money-markets-in-september-2019-20200227.html',
}


def iso_date(text):
    day = date.fromisoformat(text)
    if day.isoformat() != text:
        raise ValueError('Noncanonical source date')
    return day


def probe_pairs():
    out = []
    for text in MONDAYS:
        monday = iso_date(text)
        if monday.weekday() != 0:
            raise ValueError('Decision is not Monday')
        out.append(dict(monday=text, vintage=(monday-timedelta(days=2)).isoformat()))
    return out


def archive_url(vintage):
    day = iso_date(vintage)
    if day.weekday() != 5:
        raise ValueError('Frozen as-of date must be Saturday')
    return 'https://alfred.stlouisfed.org/graph/alfredgraph.csv?' + urlencode(dict(
        id='SOFR,EFFR', vintage_date=vintage+','+vintage,
        cosd=(day-timedelta(days=13)).isoformat(), coed=vintage))


def probe_targets():
    targets = {'archive_'+pair['vintage']: archive_url(pair['vintage']) for pair in probe_pairs()}
    targets.update(CONTEXT)
    if len(targets) != 12:
        raise ValueError('Frozen direct funding source scope changed')
    return targets


def collect(targets, fetch=False):
    DATA.mkdir(exist_ok=True)
    (DATA/'raw').mkdir(exist_ok=True)
    path = DATA/'manifest.json'
    records = json.loads(path.read_text())['sources'] if path.exists() else {}
    pending = []
    for name, url in targets.items():
        if name in records:
            if records[name]['url'] != url:
                raise ValueError('Original request identity changed')
            retained(DATA, records[name])
        elif fetch:
            pending.append((name, url))
        else:
            raise ValueError('Explicit original fetch required '+name)
    clients = local()

    def get(item):
        name, url = item
        start = datetime.now(timezone.utc).isoformat()
        try:
            if not hasattr(clients, 'http'):
                clients.http = requests.Session()
            response = clients.http.get(url, timeout=(25, 25))
            body = response.content
            end = datetime.now(timezone.utc).isoformat()
            filename = hashlib.sha256((name+' '+url).encode()).hexdigest()
            raw = DATA/'raw'/filename
            if raw.exists():
                raise ValueError('Unregistered original raw body already exists')
            raw.write_bytes(body)
            return name, dict(url=url, final_url=response.url,
                status=response.status_code, path='raw/'+filename,
                requested_utc=start, retrieved_utc=end, bytes=len(body),
                sha256=hashlib.sha256(body).hexdigest(),
                headers={k: response.headers[k] for k in
                         ('Date', 'Content-Type', 'Last-Modified', 'ETag')
                         if k in response.headers})
        except requests.RequestException as exc:
            return name, dict(url=url, status=None, path=None,
                requested_utc=start, failed_utc=datetime.now(timezone.utc).isoformat(),
                source_error=type(exc).__name__+': '+str(exc))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(get, item) for item in pending]
        for future in as_completed(futures):
            name, record = future.result()
            records[name] = record
            temporary = path.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(dict(source_only=True,
                new_alpha_trial=False, new_performance=False, sources=records),
                indent=2, sort_keys=True)+'\n')
            temporary.replace(path)
            print(name, record['status'], record.get('bytes', 0), flush=True)
    return {name: (retained(DATA, records[name]), records[name]) for name in targets}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--fetch', action='store_true')
    args = parser.parse_args()
    collect(probe_targets(), args.fetch)
