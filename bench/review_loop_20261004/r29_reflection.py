"""Read exact original rate cells independently; reconcile native price accounts."""
import argparse
import csv
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import re

import pandas as pd

from r9_selection import load_prices
from r10_gold import WINDOWS
from r19_reflection import read_frame, daily_attribution, native_reconcile
from r29_funding import ASSETS
from r29_cohort_sources import DATA, decision_pairs, targets
from r20_audit import retained
from r19_run import frozen_json

HERE = Path(__file__).resolve().parent


def check_native_feature(monday, value, body, record):
    """Independent CSV join: no strategy parser, selector or feature constructor."""
    vintage = str((monday-pd.Timedelta(days=2)).date())
    if (pd.Timestamp(value['decision']) != monday or value['vintage'] != vintage or
            pd.Timestamp(value['available_at_assumed']) != monday or
            value['historical_first_public_receipt_certified'] is not False or
            value['known'] is not True or
            value['source_sha256'] != record['sha256'] or
            hashlib.sha256(body).hexdigest() != record['sha256'] or
            value['current_capture_utc'] != record['retrieved_utc']):
        raise ValueError('Independent native source identity, clock or known state differs')
    rows = csv.reader(io.StringIO(body.decode('utf-8')))
    if next(rows) != ['observation_date', 'SOFR_'+vintage.replace('-', ''), 'EFFR_'+vintage.replace('-', '')]:
        raise ValueError('Independent native historical series/version differs')
    first, last = (str((monday-pd.Timedelta(days=n)).date()) for n in (7, 4))
    eligible, chosen, previous, count = [], None, None, 0
    for index, row in enumerate(rows):
        if len(row) != 3:
            raise ValueError('Independent native source width differs')
        day = pd.Timestamp(row[0], tz='UTC')
        if (str(day.date()) != row[0] or day.weekday() > 4 or row[0] > vintage or
                previous is not None and row[0] <= previous):
            raise ValueError('Independent native source date order/cutoff differs')
        if any(v not in ('', '.') and not re.fullmatch(r'-?\d+(?:\.\d+)?', v) for v in row[1:]):
            raise ValueError('Independent native source value format differs')
        if first <= row[0] <= last:
            cell = dict(original_row_index=index, observation_date=row[0],
                        native_values={'SOFR':row[1], 'EFFR':row[2]})
            eligible.append(cell)
            if all(v not in ('', '.') for v in row[1:]):
                chosen = cell
        previous = row[0]
        count += 1
    if chosen is None or eligible != value['native_eligible_observations']:
        raise ValueError('Independent complete eligible native dates/values/unknowns differ')
    rates = chosen['native_values']
    allow = Decimal(rates['SOFR']) <= Decimal(rates['EFFR'])
    if (str(pd.Timestamp(value['observation_day']).date()) != chosen['observation_date'] or
            value['selected_original_row_index'] != chosen['original_row_index'] or
            value['sofr_native'] != rates['SOFR'] or value['effr_native'] != rates['EFFR'] or
            type(value['allow']) is not bool or value['allow'] != allow or
            pd.Timestamp(value['eligible_start']) != monday-pd.Timedelta(days=7) or
            pd.Timestamp(value['eligible_end']) != monday-pd.Timedelta(days=4) or
            value['reason'] != ('nonpositive_official_funding_spread' if allow else 'positive_official_funding_spread')):
        raise ValueError('Independent most recent common-known native sign differs')
    return dict(original_rows=count, eligible_rows=len(eligible),
                selected_native_rate_cells=2, eligible_native_rate_cells=2*len(eligible),
                eligible_unknown_cells=sum(v in ('', '.') for r in eligible for v in r['native_values'].values()))


def validate_source_cohort(save=False):
    audit = json.loads((HERE/'r29_source_audit.json').read_text())
    records = json.loads((DATA/'manifest.json').read_text())['sources']
    coverage = {v['vintage']:v for v in audit['native_archive_coverage']}
    cohort = audit['source_feature_cohort']
    expected = decision_pairs()
    if len(cohort) != 292 or len(coverage) != 292:
        raise ValueError('Independent native decision/version count differs')
    totals = dict(original_rows=0, eligible_rows=0, selected_native_rate_cells=0,
                  eligible_native_rate_cells=0, eligible_unknown_cells=0)
    for pair in expected:
        monday = pd.Timestamp(pair['monday'], tz='UTC')
        value = cohort[str(monday)]
        c = coverage[pair['vintage']]
        record = records[c['selected_record']]
        if (record['url'] != targets()['archive_'+pair['vintage']] or record['status'] != 200 or
                record['sha256'] != c['source_sha256'] or value['source_record'] != c['selected_record'] or
                value['window'] != pair['window']):
            raise ValueError('Independent declared original counterpart differs')
        actual = check_native_feature(monday, value, retained(DATA, record), record)
        if actual['original_rows'] != c['original_native_rows']:
            raise ValueError('Independent full native source count differs')
        for key in totals:
            totals[key] += actual[key]
    result = dict(source_stage_only=True, new_performance=False, **totals,
        decision_mondays=292, exact_original_rate_text_row_version_and_unknown_states=True,
        independent_most_recent_common_known_mon_thu_and_strict_native_decimal_sign=True,
        historical_first_public_receipt_and_actual_execution_certified=False)
    if save:
        frozen_json(HERE/'r29_native_feature_validation.json', result)
    return result


def reflect():
    source_validation = validate_source_cohort()
    px, _, _ = load_prices()
    decisions = read_frame(HERE/'r29_decision_audit.csv', parse_dates=['decision', 'observation_day'])
    minutes = {a:pd.read_parquet(HERE/f'r13_data/{a}USDT_1m.parquet') for a in ASSETS}
    results = {}
    for label, start, end in WINDOWS:
        versions = {}
        selected = decisions.loc[decisions.window.eq(label)]
        active = selected[[f'baseline_{a}' for a in ASSETS]].sum(axis=1).gt(1e-12)
        coverage = dict(decision_weeks=len(selected), known_weeks=int(selected.known.sum()),
            source_reason_counts=selected.reason.value_counts().to_dict(),
            selected_observation_weekday_counts=selected.observation_day.dt.day_name().value_counts().to_dict(),
            active_original_weeks=int(active.sum()),
            vetoed_original_active_weeks_by_reason=selected.loc[active & ~selected.allow].reason.value_counts().to_dict(),
            vetoed_original_active_coin_weeks={a:int((selected['baseline_'+a].gt(1e-12) & ~selected.allow).sum()) for a in ASSETS})
        for cost in (.0012, .0024):
            for name in ('Cov10_baseline', 'official_funding_spread'):
                key = f'{label}_{name}_cost{cost}'
                curve = read_frame(HERE/f'r29_{key}_daily.csv', index_col='date', parse_dates=True)
                pnl, gap = daily_attribution(curve, px)
                dd = curve.equity/curve.equity.cummax().clip(lower=1.)-1
                trough = dd.idxmin()
                past = curve.equity.loc[:trough]
                peak = past.idxmax() if past.max() >= 1 else None
                episode = pnl.loc[:trough] if peak is None else pnl.loc[(pnl.index > peak) & (pnl.index <= trough)]
                value = dict(peak='initial cash' if peak is None else str(peak), trough=str(trough),
                    whole_window_asset_pnl_USDT_10000_start=(pnl.sum()*10000).to_dict(),
                    episode_asset_pnl_USDT_10000_start=(episode.sum()*10000).to_dict(),
                    all_days_attribution_max_abs_gap=gap,
                    mean_held_exposure_pct=float(curve.pre_close_exposure.mean()*100),
                    wholly_cash_days=int(curve.pre_close_exposure.lt(1e-12).sum()), total_days=len(curve))
                if label == 'recent_reused':
                    orders = read_frame(HERE/f'r29_{key}_orders.csv', parse_dates=['time'])
                    value['independent_source_native_ledger'] = native_reconcile(curve, orders, minutes, cost)
                versions[f'{name}_cost{cost}'] = value
        delta = {a:versions['official_funding_spread_cost0.0012']['whole_window_asset_pnl_USDT_10000_start'][a]-
                   versions['Cov10_baseline_cost0.0012']['whole_window_asset_pnl_USDT_10000_start'][a]
                 for a in [*ASSETS, 'fees']}
        results[label] = dict(versions=versions, decision_coverage=coverage,
                             official_funding_spread_minus_control_PnL_USDT_10000_start=delta)
    return dict(results=results, independent_native_source_feature_validation=source_validation,
        scope='Independent native rates, price/fee accounting and descriptive decisions. No causal skipped-trade claim, sign/threshold/date/latency/risk/fee tuning, authenticated fills or adoption.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    value = reflect()
    path = HERE/'r29_reflection.json'
    text = json.dumps(value, indent=2, sort_keys=True)+'\n'
    if args.verify_only:
        if path.read_text() != text:
            raise ValueError('Frozen independent native rate reflection did not replay')
        print('Native same-date rate joins and all daily/recent source account ledgers replay PASS')
    else:
        path.write_text(text)
        for label, result in value['results'].items():
            print(label, result['decision_coverage'], result['official_funding_spread_minus_control_PnL_USDT_10000_start'])
