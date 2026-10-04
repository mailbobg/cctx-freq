"""Full frozen overnight funding versions with original capture inheritance."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from threading import local

import pandas as pd
import requests
from r10_gold import WINDOWS
from r20_audit import retained
import r29_sources as probe

HERE = probe.HERE
DATA = HERE/'r29_cohort_sources'


def decision_pairs():
    out = []
    for window, start, end in WINDOWS:
        for monday in pd.date_range(start, end, freq='W-MON', tz='UTC'):
            out.append(dict(window=window, monday=str(monday.date()),
                vintage=str((monday-pd.Timedelta(days=2)).date())))
    return out


def targets():
    out = {}
    for pair in decision_pairs():
        vintage = pair['vintage']
        out['archive_'+vintage] = probe.archive_url(vintage)
    if len(out) != 292 or len(decision_pairs()) != 292:
        raise ValueError('Frozen historical version/calendar scope changed')
    return out


def collect(fetch=False, retry_transport=False):
    DATA.mkdir(exist_ok=True)
    (DATA/'raw').mkdir(exist_ok=True)
    path = DATA/'manifest.json'
    records = json.loads(path.read_text())['sources'] if path.exists() else {}
    fixed = targets()
    probe_path = probe.DATA/'manifest.json'
    original_manifest = json.loads(probe_path.read_text())['sources']
    probe_sha = hashlib.sha256(probe_path.read_bytes()).hexdigest()

    def save():
        tmp = path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(dict(protocol_commit='58d4344',
            original_probe_manifest_sha256=probe_sha, sources=records), indent=2, sort_keys=True)+'\n')
        tmp.replace(path)

    for key, url in fixed.items():
        if key in records:
            if records[key]['url'] != url:
                raise ValueError('Original cohort source request changed')
            retained(DATA, records[key])
        elif any(name.split('_retry_')[0] == key and r['status'] == 200 for name,r in original_manifest.items()):
            choices=sorted((name,r) for name,r in original_manifest.items()
                           if name.split('_retry_')[0] == key and r['status'] == 200)
            original_key, old = choices[0]
            if old['url'] != url:
                raise ValueError('Original probe counterpart request altered')
            body = retained(probe.DATA, old)
            filename = hashlib.sha256((key+' '+url).encode()).hexdigest()
            raw = DATA/'raw'/filename
            if raw.exists():
                raise ValueError('Unregistered inherited body exists')
            raw.write_bytes(body)
            records[key] = dict(old, path='raw/'+filename, inherited=True,
                original_manifest='r29_sources/manifest.json', original_manifest_sha256=probe_sha,
                original_record=original_key)
            save()
        elif not fetch:
            raise ValueError('Explicit original cohort fetch required '+key)
    pending = [(k, u) for k, u in fixed.items() if k not in records] if fetch else []
    if retry_transport:
        if fetch:
            raise ValueError('Original fetch and explicit retry are separate operations')
        for key, url in fixed.items():
            attempts = [(k, r) for k, r in records.items() if k.split('_retry_')[0] == key]
            if records[key]['status'] is None and not any(r['status'] == 200 for _, r in attempts):
                if any(r['status'] is not None for _, r in attempts):
                    continue  # Do not retry access denial/malformed HTTP as transport.
                n = len(attempts)
                pending.append((key+'_retry_'+str(n), url))
    clients = local()

    def get(item):
        key, url = item
        start = datetime.now(timezone.utc).isoformat()
        try:
            if not hasattr(clients, 'http'):
                clients.http = requests.Session()
            response = clients.http.get(url, timeout=(25, 25))
            body = response.content
            end = datetime.now(timezone.utc).isoformat()
            filename = hashlib.sha256((key+' '+url).encode()).hexdigest()
            raw = DATA/'raw'/filename
            if raw.exists():
                raise ValueError('Unregistered raw cohort response exists')
            raw.write_bytes(body)
            return key, dict(url=url, final_url=response.url, status=response.status_code,
                path='raw/'+filename, requested_utc=start, retrieved_utc=end,
                bytes=len(body), sha256=hashlib.sha256(body).hexdigest(),
                headers={k: response.headers[k] for k in
                         ('Date', 'Content-Type', 'Last-Modified', 'ETag') if k in response.headers},
                inherited=False)
        except requests.RequestException as exc:
            return key, dict(url=url, status=None, path=None, requested_utc=start,
                failed_utc=datetime.now(timezone.utc).isoformat(), inherited=False,
                source_error=type(exc).__name__+': '+str(exc))

    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in as_completed([pool.submit(get, item) for item in pending]):
            key, record = future.result()
            records[key] = record
            save()
            print(key, record['status'], record.get('bytes', 0), flush=True)
    for key, record in records.items():
        root = key.split('_retry_')[0]
        if root not in fixed or record['url'] != fixed[root]:
            raise ValueError('Unreviewed cohort original/retry URL')
        retained(DATA, record)
        if record.get('inherited'):
            original = original_manifest[record['original_record']]
            if (record['original_manifest_sha256'] != probe_sha or record['sha256'] != original['sha256'] or
                record['requested_utc'] != original['requested_utc'] or
                record['retrieved_utc'] != original['retrieved_utc']):
                raise ValueError('Inherited original version/capture provenance differs')
    return records


def selected():
    records = collect()
    out = {}
    for key in targets():
        attempts = sorted((name, r) for name, r in records.items() if name.split('_retry_')[0] == key)
        successes = [(name, r) for name, r in attempts if r['status'] == 200]
        if not successes:
            raise ValueError('Required historical version unavailable '+key)
        name, record = successes[0]
        out[key] = (retained(DATA, record), record, name)
    return out


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--fetch', action='store_true')
    parser.add_argument('--retry-transport', action='store_true')
    args = parser.parse_args()
    collect(args.fetch, args.retry_transport)
