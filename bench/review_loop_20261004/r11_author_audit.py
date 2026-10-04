"""Availability/schema audit of the public author snapshot; no option P&L."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
PROBE = HERE / 'r11_probe'


def audit():
    source = json.loads((PROBE / 'author_dataset_download_source.json').read_text())
    path = PROBE / source['path']
    if source['status'] != 200 or path.stat().st_size != source['bytes']:
        raise ValueError('Incomplete public source response')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != source['sha256']:
        raise ValueError('Public author source bytes changed')
    frame = pd.read_parquet(path)
    required = {'snapshot_time', 'instrument_name', 'mark_price', 'bid_price', 'ask_price',
                'underlying_index', 'underlying_price', 'open_interest', 'volume',
                'estimated_delivery_price', 'expiry_date'}
    if set(frame.columns) != required:
        raise ValueError('Unknown author snapshot schema')
    if frame[['snapshot_time', 'expiry_date', 'instrument_name']].isna().any().any():
        raise ValueError('Missing identity or source timestamp')
    if not frame.instrument_name.str.fullmatch(r'BTC-\d{1,2}[A-Z]{3}\d{2}-\d+(?:\.\d+)?-[CP]').all():
        raise ValueError('Unreviewed non-BTC/non-option contract')
    catalog_path = PROBE / 'option_catalog.parquet'
    catalog_manifest = json.loads((PROBE / 'catalog_manifest.json').read_text())
    if hashlib.sha256(catalog_path.read_bytes()).hexdigest() != catalog_manifest['sha256']:
        raise ValueError('Historical identity catalog changed')
    catalog = pd.read_parquet(catalog_path).set_index('instrument_name')
    names = frame.instrument_name.unique()
    if not pd.Index(names).isin(catalog.index).all():
        raise ValueError('Source contract absent from the frozen official catalog')
    # The file timestamps have no zone. Compare labels only and disclose that
    # UTC/capture latency are unverified; do not turn them into fill timestamps.
    expected_expiry = pd.to_datetime(frame.instrument_name.map(catalog.expiration_timestamp), unit='ms', utc=True).dt.tz_localize(None)
    if not expected_expiry.eq(frame.expiry_date).all():
        raise ValueError('Author expiry labels disagree with official identities')
    times = frame.snapshot_time
    hours = times.dt.floor('h')
    unique_hours = pd.DatetimeIndex(hours.unique()).sort_values()
    full_hours = pd.date_range(unique_hours.min(), unique_hours.max(), freq='h')
    absent_hours = full_hours.difference(unique_hours)
    bidask = frame[['bid_price', 'ask_price']]
    finite_positive = np.isfinite(bidask.to_numpy()).all(axis=1) & bidask.gt(0).all(axis=1)
    crossed = finite_positive & frame.bid_price.ge(frame.ask_price)
    valid_sides = finite_positive & ~crossed
    counts = frame.groupby(hours).instrument_name.nunique()
    marks = frame.mark_price
    mark_known = np.isfinite(marks) & marks.gt(0)
    duplicate_keys = frame.duplicated(['instrument_name', 'snapshot_time'], keep=False)
    expired = times.ge(frame.expiry_date)
    return {
        'source_sha256': digest,
        'catalog_sha256': catalog_manifest['sha256'],
        'rows': len(frame), 'instrument_count': len(names),
        'first_source_snapshot': str(times.min()), 'last_source_snapshot': str(times.max()),
        'timestamp_zone': str(times.dt.tz),
        'hours_with_any_rows': len(unique_hours), 'hour_label_span': len(full_hours),
        'absent_whole_hour_labels': [str(x) for x in absent_hours],
        'contracts_per_present_hour_min_median_max': [int(counts.min()), float(counts.median()), int(counts.max())],
        'repeated_identity_snapshot_rows': int(duplicate_keys.sum()),
        'at_or_after_expiry_rows': int(expired.sum()),
        'missing_nonpositive_nonfinite_bidask_rows': int((~finite_positive).sum()),
        'locked_or_crossed_positive_bidask_rows': int(crossed.sum()),
        'positive_uncrossed_bidask_rows': int(valid_sides.sum()),
        'missing_nonpositive_nonfinite_mark_rows': int((~mark_known).sum()),
        'known_marks_outside_valid_bidask_rows': int((valid_sides & mark_known & (marks.lt(frame.bid_price) | marks.gt(frame.ask_price))).sum()),
        'official_catalog_expiry_labels_match': True,
        'missing_execution_fields': ['bid_amount', 'ask_amount', 'exchange_timestamp', 'local_arrival_timestamp'],
        'scope': 'Third-party author-captured BTC-only partial-2024 hourly snapshots linked by a Deribit education article. No execution latency/depth proof, no ETH or full historical windows, no forward-filled prices and no performance trial. Hour coverage is whole-source coverage, not coverage for every held contract.'
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    result = audit()
    path = PROBE / 'author_dataset_validation.json'
    if args.verify_only:
        if json.loads(path.read_text()) != result:
            raise ValueError('Public author source audit does not reproduce')
        print('Public author bytes, schema, identities and coverage replay PASS')
    else:
        path.write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
