"""Bounded original reserve-quantity/history probe; no economic rule or outcome."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from threading import local
from urllib.parse import urlencode

import requests

HERE = Path(__file__).resolve().parent
DATA = HERE/'r31_sources'
PROBES = (
    ('2019-09-23','2019-09-19','2019-08-22'),
    ('2021-02-01','2021-01-28','2020-12-31'),
    ('2024-01-01','2023-12-28','2023-11-30'),
    ('2026-08-31','2026-08-27','2026-07-30'),
    ('2024-12-02','2024-11-29','2024-10-31'),
)
CONTEXT = dict(
    publisher_calendar='https://www.federalreserve.gov/releases/h41/default.htm',
    publisher_explanation='https://www.federalreserve.gov/RELEASES/h41/about.htm',
    publisher_processing='https://www.federalreserve.gov/releases/h41/perfeval2012.htm',
    fred_identity='https://fred.stlouisfed.org/series/WRESBAL',
    alfred_help='https://alfred.stlouisfed.org/help/downloaddata',
)


def targets():
    result = {}
    for monday_text, current, prior in PROBES:
        monday = date.fromisoformat(monday_text)
        if monday.weekday() != 0:
            raise ValueError('Forensic anchor is not Monday')
        for text in (current,prior):
            day = date.fromisoformat(text)
            result['publisher_'+text] = 'https://www.federalreserve.gov/releases/h41/'+day.strftime('%Y%m%d')+'/'
        vintage = monday-timedelta(days=2)
        result['archive_'+vintage.isoformat()] = 'https://alfred.stlouisfed.org/graph/alfredgraph.csv?'+urlencode(dict(
            id='WRESBAL', vintage_date=vintage.isoformat(),cosd=(vintage-timedelta(days=41)).isoformat(),coed=vintage.isoformat()))
    result.update(CONTEXT)
    if len(result) != 20:
        raise ValueError('Preregistered original source role count differs')
    return result


def retained(record):
    if not record.get('path'):
        return None
    raw = DATA/record['path']
    if raw.parent != DATA/'raw':
        raise ValueError('Native body escapes original raw scope')
    body = raw.read_bytes()
    if hashlib.sha256(body).hexdigest() != record['sha256'] or len(body) != record['bytes']:
        raise ValueError('Original native source body changed')
    return body


def collect(fetch=False):
    DATA.mkdir(exist_ok=True)
    (DATA/'raw').mkdir(exist_ok=True)
    path = DATA/'manifest.json'
    records = json.loads(path.read_text())['sources'] if path.exists() else {}
    scope = targets()
    if set(records)-set(scope):
        raise ValueError('Unregistered extra source role')
    pending = []
    for role,url in scope.items():
        if role in records:
            if records[role]['url'] != url:
                raise ValueError('Original native request identity changed')
            retained(records[role])
        elif fetch:
            pending.append((role,url))
        else:
            raise ValueError('Explicit original fetch required '+role)
    clients = local()

    def get(item):
        role,url = item
        start = datetime.now(timezone.utc).isoformat()
        try:
            if not hasattr(clients,'http'):
                clients.http = requests.Session()
            response = clients.http.get(url,timeout=(25,25))
            body = response.content
            raw = DATA/'raw'/hashlib.sha256((role+' '+url).encode()).hexdigest()
            if raw.exists():
                raise ValueError('Unregistered original native body exists')
            raw.write_bytes(body)
            return role,dict(url=url,final_url=response.url,status=response.status_code,
                requested_utc=start,retrieved_utc=datetime.now(timezone.utc).isoformat(),
                path='raw/'+raw.name,bytes=len(body),sha256=hashlib.sha256(body).hexdigest(),
                headers={k:response.headers[k] for k in ('Date','Content-Type','Last-Modified','ETag') if k in response.headers})
        except requests.RequestException as exc:
            return role,dict(url=url,status=None,path=None,requested_utc=start,
                failed_utc=datetime.now(timezone.utc).isoformat(),source_error=type(exc).__name__+': '+str(exc))

    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in as_completed([pool.submit(get,item) for item in pending]):
            role,record = future.result()
            records[role] = record
            tmp = path.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(dict(source_plan_commit='f93dd94',source_only=True,
                no_alpha_rule_cohort_weights_or_performance=True,sources=records),indent=2,sort_keys=True)+'\n')
            tmp.replace(path)
            print(role,record['status'],record.get('bytes',0),flush=True)
    return records


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--fetch',action='store_true')
    args = parser.parse_args()
    collect(args.fetch)
