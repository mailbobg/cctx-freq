"""Frozen native source cohort; no strategy optimization or synthetic minutes."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
from threading import Lock, local
import time
import zipfile

import numpy as np
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
DATA = HERE / 'r35_data'
PLAN = HERE / 'r35_source_plan.json'
PLAN_COMMIT = '7085c1a'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')


def targets():
    out = []
    periods = [('monthly', t, t + pd.offsets.MonthBegin(1), t.strftime('%Y-%m'))
               for t in pd.date_range('2018-04-01', '2026-08-01', freq='MS', tz='UTC')]
    periods += [('daily', t, t + pd.Timedelta(days=1), t.strftime('%Y-%m-%d'))
                for t in pd.date_range('2026-09-01', '2026-09-21', freq='D', tz='UTC')]
    for asset in ('BTC', 'ETH'):
        for kind, start, end, period in periods:
            filename = f'{asset}USDT-1m-{period}.zip'
            url = f'https://data.binance.vision/data/spot/{kind}/klines/{asset}USDT/1m/{filename}'
            out.append(dict(asset=asset, kind=kind, start=str(start), end=str(end),
                            filename=filename, url=url, checksum_url=url + '.CHECKSUM'))
    assert len(out) == 244
    return out


def freeze_plan():
    if PLAN.exists():
        raise ValueError('Source plan already exists; do not overwrite')
    previous = json.loads((HERE / 'r13_data/sources.json').read_text())
    jobs = targets()
    urls = [u for j in jobs for u in (j['url'], j['checksum_url'])]
    inherited = {}
    for url in urls:
        if url in previous:
            r = previous[url]
            path = HERE / 'r13_data' / r['path']
            if digest(path) != r['sha256'] or path.stat().st_size != r['bytes'] or r['status'] != 200:
                raise ValueError('Inherited native response changed')
            inherited[url] = dict(original_record=r, path=str(path.relative_to(HERE)),
                                  original_manifest_sha256=digest(HERE / 'r13_data/sources.json'))
    if len(inherited) != 128 or len(set(urls)) != 488:
        raise ValueError('Fixed source census changed')
    old = json.loads((HERE / 'experiment_versions.json').read_text())
    root = HERE.parents[2]
    for key, parent in (('source_sha256', HERE), ('output_sha256', HERE), ('root_source_sha256', root)):
        for name, sha in old[key].items():
            if digest(parent / name) != sha:
                raise ValueError('Previous registered file changed: ' + name)
    r1 = json.loads((HERE / 'r1_freq_results.json').read_text())
    inputs = {'r1_freq_results.json': digest(HERE / 'r1_freq_results.json'),
              'R35_RISK_AUDIT_PLAN.md': digest(HERE / 'R35_RISK_AUDIT_PLAN.md'),
              'PROTOCOL.md': digest(HERE / 'PROTOCOL.md'),
              'selection_board.json': digest(HERE / 'selection_board.json'),
              'experiment_versions.json': digest(HERE / 'experiment_versions.json')}
    for asset in ('BTC', 'ETH'):
        name = f'r1_data/{asset}_USDT-1d.feather'
        inputs[name] = digest(HERE / name)
    for key, r in r1.items():
        p = Path(r['source'])
        if digest(p) != r['source_sha256']:
            raise ValueError('Original Freqtrade archive changed')
        inputs[str(p)] = r['source_sha256']
        name = f'r1_freq_{key}.csv'
        inputs[name] = digest(HERE / name)
    write_json(PLAN, dict(protocol_commit=PLAN_COMMIT, jobs=jobs, url_roles=len(urls),
                         inherited_roles=len(inherited), new_request_roles=len(urls) - len(inherited),
                         inherited=inherited, original_input_sha256=inputs,
                         previous_registered_counts={k: len(old[k]) for k in
                                                     ('source_sha256','output_sha256','root_source_sha256')},
                         no_new_intraday_risk_outcomes=True))
    return len(urls), len(inherited)


def assert_inputs():
    plan = json.loads(PLAN.read_text())
    if plan['protocol_commit'] != PLAN_COMMIT or plan['jobs'] != targets():
        raise ValueError('Frozen source calendar changed')
    # Shared registries may be appended only AFTER final prior-integrity validation.
    for name, sha in plan['original_input_sha256'].items():
        if digest(HERE / name) != sha:
            raise ValueError('Frozen input changed: ' + name)
    return plan


def checked_csv(body, check, filename):
    fields = check.decode('utf-8').split()
    if fields != [hashlib.sha256(body).hexdigest(), filename]:
        raise ValueError('Native SHA256 or literal archive filename mismatch')
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        member = filename[:-4] + '.csv'
        if z.namelist() != [member]:
            raise ValueError('Unexpected native archive member')
        return z.read(member)


def parse_minutes(body, start, end):
    raw = pd.read_csv(io.BytesIO(body), header=None)
    if raw.empty or raw.shape[1] != 12:
        raise ValueError('Native minute schema must have 12 columns')
    x = raw.apply(pd.to_numeric, errors='raise')
    if not np.isfinite(x.to_numpy()).all():
        raise ValueError('Nonfinite native minute value')
    opened, closed = x[0], x[6]
    if not opened.mod(1).eq(0).all() or not closed.mod(1).eq(0).all():
        raise ValueError('Fractional native timestamp')
    micro = opened.ge(10**15)
    if micro.any() and not micro.all():
        raise ValueError('Mixed native epoch units')
    unit = 'us' if micro.all() else 'ms'
    scale = 1000000 if unit == 'us' else 1000
    if not closed.sub(opened).eq(60 * scale - 1).all():
        raise ValueError('Native close span is not one minute')
    index = pd.DatetimeIndex(pd.to_datetime(opened.astype('int64'), unit=unit, utc=True)).as_unit('ns')
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if not index.is_monotonic_increasing or index.has_duplicates or not (index >= start).all() or not (index < end).all() or not (index == index.floor('min')).all():
        raise ValueError('Duplicate, reordered, unaligned or out-of-window native minute')
    prices = x[[1, 2, 3, 4]]
    if not prices.gt(0).all().all() or not x[[5, 7, 8, 9, 10]].ge(0).all().all() or not x[8].mod(1).eq(0).all():
        raise ValueError('Nonpositive price, negative activity or fractional trades')
    if not x[2].ge(prices.max(axis=1)).all() or not x[3].le(prices.min(axis=1)).all():
        raise ValueError('Native OHLC bounds disagree')
    if not x[9].le(x[5] + 1e-8).all() or not x[10].le(x[7] + 1e-8).all():
        raise ValueError('Taker volume exceeds native total volume')
    if not (x[8].eq(0) == x[5].eq(0)).all() or not (x[8].eq(0) == x[7].eq(0)).all():
        raise ValueError('Native zero-trade/activity identity disagrees')
    frame = x[[1,2,3,4,5,7,8]].set_axis(['open','high','low','close','volume','quote_volume','trades'], axis=1)
    frame.index = index
    expected = pd.date_range(start, end, freq='min', inclusive='left').as_unit('ns')
    missing = expected.difference(index)
    return frame.rename_axis('date'), dict(rows=len(frame), missing_minutes=len(missing),
        missing_utc=[str(t) for t in missing], zero_activity_minutes=int(frame.trades.eq(0).sum()))


def collect(offline=False):
    plan = assert_inputs()
    DATA.mkdir(exist_ok=True); (DATA / 'raw').mkdir(exist_ok=True)
    path = DATA / 'sources.json'
    records = json.loads(path.read_text()) if path.exists() else {}
    for url, item in plan['inherited'].items():
        r = item['original_record']
        native = dict(r, path=item['path'], inherited=True,
                      original_manifest_sha256=item['original_manifest_sha256'],
                      original_request_utc_unavailable='requested_utc' not in r)
        if url in records and records[url] != native:
            raise ValueError('Inherited original receipt metadata changed')
        records[url] = native
    lock = Lock(); pacing = Lock(); state = local(); next_dispatch = [0.0]
    def get(url):
        if url in records:
            r = records[url]
            if r.get('path') is not None:
                p = HERE / r['path']
                if digest(p) != r['sha256'] or p.stat().st_size != r['bytes']:
                    raise ValueError('Retained source body changed')
            return r
        if offline:
            raise ValueError('Offline replay lacks originally attempted URL')
        if not hasattr(state, 'session'):
            state.session = requests.Session()
        with pacing:
            pause = next_dispatch[0] - time.monotonic()
            if pause > 0: time.sleep(pause)
            next_dispatch[0] = time.monotonic() + .6
            requested = datetime.now(timezone.utc).isoformat()
        try:
            response = state.session.get(url, timeout=(10, 60))
            body = response.content
            p = DATA / 'raw' / hashlib.sha256(url.encode()).hexdigest()
            p.write_bytes(body)
            rec = dict(url=url, path=str(p.relative_to(HERE)), status=response.status_code,
                bytes=len(body), sha256=hashlib.sha256(body).hexdigest(), inherited=False,
                requested_utc=requested, retrieved_utc=datetime.now(timezone.utc).isoformat(),
                error=None, final_url=response.url)
        except requests.RequestException as error:
            rec = dict(url=url, path=None, status=None, bytes=0, sha256=None, inherited=False,
                requested_utc=requested, retrieved_utc=datetime.now(timezone.utc).isoformat(),
                error=type(error).__name__ + ': ' + str(error), final_url=None)
        with lock:
            records[url] = rec
            tmp = path.with_suffix('.tmp'); write_json(tmp, records); tmp.replace(path)
            new_count = sum(not r['inherited'] for r in records.values())
            if new_count % 20 == 0: print(f'Native source capture {new_count}/360 new roles', flush=True)
        return rec
    urls = [u for j in plan['jobs'] for u in (j['url'], j['checksum_url'])]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(get, urls))
    if set(records) != set(urls): raise ValueError('Frozen source role set disagrees')
    if not path.exists(): write_json(path, records)
    return records


def derive(offline=False):
    records = collect(offline)
    plan = json.loads(PLAN.read_text())
    source_sha = {r['path']: r['sha256'] for r in records.values() if r['path']}
    out = dict(protocol_commit=PLAN_COMMIT, url_roles=len(records),
        inherited_roles=sum(r['inherited'] for r in records.values()),
        new_roles=sum(not r['inherited'] for r in records.values()),
        status_counts={str(s): sum(r['status'] == s for r in records.values())
                       for s in sorted(set(r['status'] for r in records.values()),key=str)},
        source_manifest_sha256=digest(DATA / 'sources.json'), native_input_sha256=source_sha,
        archives=[], validation={}, output_sha256={}, source_contract_complete=False,
        historical_receipt_or_execution_quote_certified=False)
    parts = {'BTC': [], 'ETH': []}
    for job in plan['jobs']:
        rs = [records[job[k]] for k in ('url','checksum_url')]
        item = dict(asset=job['asset'],filename=job['filename'], valid=False)
        try:
            if any(r['status'] != 200 or not r['path'] for r in rs):
                raise ValueError('Original native HTTP/transport failure')
            bodies = [(HERE / r['path']).read_bytes() for r in rs]
            f, info = parse_minutes(checked_csv(*bodies, job['filename']), job['start'], job['end'])
            item.update(info, valid=True); parts[job['asset']].append(f)
        except (ValueError,zipfile.BadZipFile,UnicodeDecodeError) as error:
            item['failure'] = type(error).__name__ + ': ' + str(error)
        out['archives'].append(item)
    for asset, frames in parts.items():
        if len(frames) != 122:
            out['validation'][asset] = dict(complete_archive_set=False,valid_archives=len(frames))
            continue
        joined = pd.concat(frames)
        if joined.index.has_duplicates or not joined.index.is_monotonic_increasing:
            raise ValueError('Archive join overlaps or reorders original minutes')
        original = pd.read_feather(HERE / f'r1_data/{asset}_USDT-1d.feather').set_index('date')
        daily = joined.resample('D').agg({'open':'first','high':'max','low':'min','close':'last'})
        reference = original.reindex(daily.index)
        gap = (daily - reference[['open','high','low','close']]).abs()
        mismatches = gap.max(axis=1).gt(1e-8) | gap.isna().any(axis=1)
        counts = joined.resample('D').size()
        target = DATA / f'{asset}USDT_1m.parquet'
        if offline:
            pd.testing.assert_frame_equal(pd.read_parquet(target), joined)
        else: joined.to_parquet(target)
        out['output_sha256'][str(target.relative_to(HERE))] = digest(target)
        out['validation'][asset] = dict(complete_archive_set=True, rows=len(joined),
            first=str(joined.index[0]),last=str(joined.index[-1]),
            missing_minutes=sum(x['missing_minutes'] for x in out['archives'] if x['asset']==asset and x['valid']),
            zero_activity_minutes=int(joined.trades.eq(0).sum()),
            incomplete_days=int(counts.ne(1440).sum()), daily_OHLC_max_gap=float(gap.max().max()),
            daily_mismatches=[dict(date=str(t),native_daily_OHLC=daily.loc[t].to_dict(),
                                   original_daily_OHLC=reference.loc[t,['open','high','low','close']].to_dict(),
                                   native_minutes=int(counts.loc[t])) for t in daily.index[mismatches]],
            original_daily_sha256=digest(HERE / f'r1_data/{asset}_USDT-1d.feather'))
    out['source_contract_complete'] = all(v.get('complete_archive_set') and v['missing_minutes']==0 and
        not v['daily_mismatches'] for v in out['validation'].values()) and len(out['validation']) == 2
    dest = HERE / 'r35_source_audit.json'
    if offline:
        if dest.read_text() != json.dumps(out,indent=2,sort_keys=True,allow_nan=False)+'\n':
            raise ValueError('Original native source audit does not replay byte exactly')
    else: write_json(dest,out)
    print({k:out[k] for k in ('url_roles','inherited_roles','new_roles','status_counts','source_contract_complete')},flush=True)
    print({a:{k:v.get(k) for k in ('rows','missing_minutes','zero_activity_minutes','daily_OHLC_max_gap')} for a,v in out['validation'].items()},flush=True)
    return out


if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--freeze-plan',action='store_true');p.add_argument('--offline',action='store_true')
    args=p.parse_args()
    if args.freeze_plan: print(freeze_plan())
    else: derive(args.offline)
