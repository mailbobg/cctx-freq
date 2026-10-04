"""Offline reuse of checksummed R7 klines, preserving original trade-side decimals."""
import argparse
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import io
import json
from pathlib import Path
import zipfile

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA, OLD = HERE/'r14_data', HERE/'r7_data'
START, END = pd.Timestamp('2021-01-01', tz='UTC'), pd.Timestamp('2026-09-01', tz='UTC')
ASSETS = ('BTC', 'ETH')


def decimal_value(value):
    if not isinstance(value, str):
        raise ValueError('Original decimal string required')
    try:
        result = Decimal(value)
    except InvalidOperation as e:
        raise ValueError('Unknown decimal field') from e
    if not result.is_finite():
        raise ValueError('Unknown decimal field')
    return result


def exact_precision(values):
    nonzero = [v for v in values if v]
    return max(60, max(v.adjusted() for v in nonzero)-min(v.as_tuple().exponent for v in nonzero)
               +len(str(len(values)))+10) if nonzero else 60


def checked_zip(body, check, filename):
    fields = check.decode().split()
    if len(fields) != 2 or fields[1] != filename or fields[0] != hashlib.sha256(body).hexdigest():
        raise ValueError('Official checksum/filename mismatch')
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        member = filename.removesuffix('.zip')+'.csv'
        if z.namelist() != [member]:
            raise ValueError('Wrong archive member identity')
        return z.read(member)


def parse_flow(body, start, end):
    raw = pd.read_csv(io.BytesIO(body), header=None, dtype=str, keep_default_na=False)
    if raw.shape[1] != 12 or raw.empty:
        raise ValueError('Wrong/empty flow schema')
    rows = [[decimal_value(x) for x in row] for row in raw.to_numpy()]
    opened, closed = [r[0] for r in rows], [r[6] for r in rows]
    if any(x != x.to_integral_value() for x in opened+closed):
        raise ValueError('Fractional source timestamp')
    micro = [x >= 10**15 for x in opened]
    if any(micro) and not all(micro):
        raise ValueError('Mixed archive epoch units')
    scale, unit = (1000000, 'us') if all(micro) else (1000, 'ms')
    if any(c-o != 86400*scale-1 for o, c in zip(opened, closed)):
        raise ValueError('Wrong completed daily close span')
    index = pd.DatetimeIndex(pd.to_datetime([int(x) for x in opened], unit=unit, utc=True)).as_unit('ns')
    expected = pd.date_range(start, end, freq='D', inclusive='left').as_unit('ns')
    if not index.equals(expected):
        raise ValueError('Missing, repeated, reordered or unexpected daily bar')
    for r in rows:
        op, hi, lo, cl, base, quote, trades, buy_base, buy_quote = [r[k] for k in (1,2,3,4,5,7,8,9,10)]
        if min(op, hi, lo, cl, base, quote, trades) <= 0 or trades != trades.to_integral_value():
            raise ValueError('Nonpositive price/activity or fractional trade count')
        if hi < max(op, lo, cl) or lo > min(op, hi, cl):
            raise ValueError('Invalid OHLC')
        if not (0 <= buy_base <= base and 0 <= buy_quote <= quote):
            raise ValueError('Buy volume outside total volume bounds')
        with localcontext() as ctx:
            # Exact products/subtractions, not rounded ratios or a fitted tolerance.
            ctx.prec = 2*exact_precision(r)
            pairs = [(base, quote), (buy_base, buy_quote), (base-buy_base, quote-buy_quote)]
            for native, notional in pairs:
                if (native == 0) != (notional == 0) or not (lo*native <= notional <= hi*native):
                    raise ValueError('Physical native/quote volume units disagree')
    result = raw[[1,2,3,4]].astype(float).set_axis(['open','high','low','close'], axis=1)
    if not np.isfinite(result.to_numpy()).all():
        raise ValueError('Unrepresentable OHLC')
    for k, name in ((5,'volume'), (7,'quote_volume'), (9,'buy_volume'), (10,'buy_quote_volume')):
        result[name] = raw[k]
    result['trades'] = [int(r[8]) for r in rows]
    result.index = index
    result['available_at'] = index+pd.Timedelta(days=1)
    return result.rename_axis('date')


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--verify-cache-only', action='store_true')
    args = parser.parse_args()
    records_path = OLD/'sources.json'; records = json.loads(records_path.read_text())
    prior_outputs = json.loads((OLD/'manifest.json').read_text())['output_sha256']
    used, outputs, validation = {}, {}, {}

    def get(url):
        record = records[url]
        body = (OLD/record['path']).read_bytes()
        if hashlib.sha256(body).hexdigest() != record['sha256'] or len(body) != record['bytes']:
            raise ValueError('Original retained R7 bytes changed')
        if record.get('url') != url:
            raise ValueError('Retained source URL identity changed')
        used[url] = record
        return body

    for asset in ASSETS:
        frames = []
        for month in pd.date_range(START, END, freq='MS', inclusive='left'):
            tag = str(month.date())[:7]; name = f'{asset}USDT-1d-{tag}.zip'
            url = f'https://data.binance.vision/data/spot/monthly/klines/{asset}USDT/1d/{name}'
            frames.append(parse_flow(checked_zip(get(url), get(url+'.CHECKSUM'), name), month, month+pd.offsets.MonthBegin(1)))
        joined = pd.concat(frames)
        expected = pd.date_range(START, END, freq='D', inclusive='left').as_unit('ns')
        if not joined.index.equals(expected): raise ValueError('Full flow calendar disagrees')
        old_path = OLD/f'{asset}USDT_spot.parquet'
        old_sha = hashlib.sha256(old_path.read_bytes()).hexdigest()
        if old_sha != prior_outputs[old_path.name]: raise ValueError('Frozen daily output changed')
        old = pd.read_parquet(old_path).reindex(expected)
        columns = ['open','high','low','close','volume','quote_volume','trades']
        gap = float(np.abs(joined[columns].astype(float).to_numpy()-old[columns].to_numpy()).max())
        if gap != 0: raise ValueError('Original decimal parsing disagrees with frozen daily prices/activity')
        target = DATA/f'{asset}USDT_flow.parquet'
        if args.verify_cache_only:
            pd.testing.assert_frame_equal(pd.read_parquet(target), joined)
        else:
            DATA.mkdir(exist_ok=True); joined.to_parquet(target)
        outputs[target.name] = hashlib.sha256(target.read_bytes()).hexdigest()
        validation[asset] = dict(rows=len(joined), first=str(joined.index[0]),
            last=str(joined.index[-1]), frozen_daily_sha256=old_sha, daily_OHLC_activity_max_gap=gap,
            exact_decimal_volume_preserved=True, complete_calendar=True, native_quote_physical_bounds=True)
        print(asset, len(joined), 'source calendar/decimal units/reference PASS', flush=True)
    manifest = dict(reused_source_count=len(used), original_source_manifest_sha256=hashlib.sha256(records_path.read_bytes()).hexdigest(),
        reused_sources=used, output_sha256=outputs, validation=validation,
        provenance='Only original R7 retained bodies and official checksums, zero HTTP calls. Original records did not retain status; no new status or retrieval date fabricated.')
    path = DATA/'manifest.json'
    if args.verify_cache_only:
        if json.loads(path.read_text()) != manifest: raise ValueError('Frozen flow manifest did not replay exactly')
        print('Offline raw archive, checksum, physical units and exact parsed output replay PASS')
    else:
        path.write_text(json.dumps(manifest, indent=2)+'\n')
        print('Reused source responses', len(used))


if __name__ == '__main__': main()
