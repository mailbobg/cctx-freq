"""Post-outcome native risk reconstruction; no policy, sizing or curve changes."""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from r19_reflection import read_frame
from r29_freeze import assert_frozen_inputs

HERE = Path(__file__).resolve().parent
ASSETS = ('BTC', 'ETH')


def mark_path(curve, orders, minutes, cost):
    if (str(curve.index.tz) != 'UTC' or not curve.index.is_unique or
            not curve.index.is_monotonic_increasing or not orders.time.is_monotonic_increasing):
        raise ValueError('Unreviewed native account or order calendar')
    expected_days = pd.date_range(curve.index[0], curve.index[-1], freq='D')
    if not curve.index.as_unit('ns').equals(expected_days.as_unit('ns')):
        raise ValueError('Missing native account day')
    begin, finish = curve.index[0], curve.index[-1]+pd.Timedelta(days=1)
    expected = pd.date_range(begin, finish, freq='min', inclusive='left')
    values = []
    for asset in ASSETS:
        frame = minutes[asset].loc[begin:finish-pd.Timedelta(minutes=1)]
        if not frame.index.as_unit('ns').equals(expected.as_unit('ns')):
            raise ValueError('Missing, repeated or unordered native minute')
        data = frame[['open', 'close']].to_numpy(float)
        if not np.isfinite(data).all() or (data <= 0).any():
            raise ValueError('Unknown native mark price')
        values.append(data.reshape(-1, 1440, 2))
    raw = np.stack(values, axis=-1)  # day, minute, OPEN/CLOSE, asset
    if len(orders) and not ((orders.time >= begin) & (orders.time < finish)).all():
        raise ValueError('Native order outside account calendar')
    cash, units = 1., np.zeros(2)
    peak, worst = 1., 0.
    peak_point = dict(time='initial_cash', stage='CASH', equity=1.)
    worst_peak, worst_trough, first_breach = peak_point, None, None
    label_gap, ledger_gap, fills = 0., 0., 0

    def point(day, index, path):
        if index == 0:
            time, stage = day, 'prefill_OPEN'
        elif index == len(path)-1:
            time, stage = day+pd.Timedelta(minutes=1439), 'terminal_CASH'
        else:
            minute, kind = divmod(index-1, 2)
            time = day+pd.Timedelta(minutes=minute)
            stage = 'OPEN' if kind == 0 else 'CLOSE'
        return dict(time=time.isoformat(), stage=stage, equity=float(path[index]))

    def apply(fill, expected_price):
        nonlocal cash, units, fills
        if fill.asset not in ASSETS or fill.price != expected_price:
            raise ValueError('Fill differs from actual native mark source')
        amount = fill.native_quantity*expected_price
        fee = abs(amount)*cost
        if abs(amount-fill.dollar_amount) > 1e-12 or abs(fee-fill.cost) > 1e-12:
            raise ValueError('Native fill units or paid fee differs')
        cash -= amount+fee
        units[ASSETS.index(fill.asset)] += fill.native_quantity
        if cash < -1e-12 or units.min() < -1e-12:
            raise ValueError('Native risk ledger borrowed or shorted')
        fills += 1

    for index, (day, row) in enumerate(curve.iterrows()):
        source = raw[index]
        before = cash+source[0, 0]@units
        selected = orders.loc[(orders.time >= day) & (orders.time < day+pd.Timedelta(days=1))]
        terminal = []
        for fill in selected.itertuples():
            if fill.reason == 'weekly':
                if day.weekday() != 0 or fill.time != day or terminal:
                    raise ValueError('Unfrozen native weekly order timing')
                apply(fill, source[0, 0, ASSETS.index(fill.asset)])
            elif fill.reason == 'terminal_daily_close':
                if day != curve.index[-1] or fill.time != day+pd.Timedelta(minutes=1439):
                    raise ValueError('Unfrozen terminal native order timing')
                terminal.append(fill)
            else:
                raise ValueError('Unfrozen native risk-path execution')
        # Independent cash + actual units valuation. Duplicate same-minute
        # pre/post marks without fees have no effect on extrema or drawdown.
        path = cash+(source@units).reshape(-1)
        for fill in terminal:
            apply(fill, source[-1, 1, ASSETS.index(fill.asset)])
        final = cash+source[-1, 1]@units
        path = np.r_[before, path, final]
        running = np.maximum.accumulate(np.r_[peak, path])[1:]
        drawdowns = 100*(path/running-1)
        local = int(np.argmin(drawdowns))
        if drawdowns[local] < worst:
            worst = float(drawdowns[local])
            local_peak = int(np.argmax(path[:local+1]))
            worst_peak = point(day, local_peak, path) if path[local_peak] > peak else peak_point
            worst_trough = point(day, local, path)
        if first_breach is None:
            breach = np.flatnonzero(drawdowns <= -5)
            if len(breach):
                first_breach = dict(**point(day, int(breach[0]), path), drawdown_pct=float(drawdowns[breach[0]]))
        local_max = int(np.argmax(path))
        if path[local_max] > peak:
            peak, peak_point = float(path[local_max]), point(day, local_max, path)
        computed = np.array([peak, worst, path.min(), path.max()])
        retained = row[['sampled_minute_peak_equity', 'sampled_minute_running_dd_pct',
                        'sampled_minute_day_min_equity', 'sampled_minute_day_max_equity']].to_numpy(float)
        label_gap = max(label_gap, float(np.max(np.abs(computed-retained))))
        ledger_gap = max(ledger_gap, abs(final-row.equity), abs(cash-row.cash),
                         float(np.max(np.abs(units-row[['units_BTC', 'units_ETH']].to_numpy(float)))))
    if label_gap > 1e-10 or ledger_gap > 1e-12:
        raise ValueError('Independent source-native minute risk or account labels differ')
    return dict(native_account_max_DD_pct=worst, worst_peak=worst_peak, worst_trough=worst_trough,
        first_5pct_breach=first_breach, native_fills_checked=fills,
        minute_open_close_marks_checked=2*len(expected), account_days_checked=len(curve),
        max_abs_daily_risk_label_gap=label_gap, max_abs_cash_units_equity_gap=ledger_gap,
        first_public_receipt_actual_exchange_fills_quote_depth_or_tick_risk_certified=False)


def audit():
    assert_frozen_inputs(HERE)
    minutes = {a:pd.read_parquet(HERE/f'r13_data/{a}USDT_1m.parquet') for a in ASSETS}
    results = {}
    for cost in (.0012, .0024):
        for name in ('Cov10_baseline', 'official_funding_spread'):
            key = f'recent_reused_{name}_cost{cost}'
            curve = read_frame(HERE/f'r29_{key}_daily.csv', index_col='date', parse_dates=True)
            orders = read_frame(HERE/f'r29_{key}_orders.csv', parse_dates=['time'])
            results[key] = mark_path(curve, orders, minutes, cost)
    return dict(post_outcome_checker=True, new_economic_variant=False, results=results,
        no_original_code_curve_order_or_rule_changed=True,
        scope='Independent actual native minute OPEN/CLOSE capital marks and native modeled fill ledger. Prefill and terminal fees included. No actual exchange-fill, continuous quote/tick risk or source/publication/forward certification.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    result = audit()
    path = HERE/'r29_mark_audit.json'
    text = json.dumps(result, indent=2, sort_keys=True)+'\n'
    if args.verify_only:
        if path.read_text() != text:
            raise ValueError('Independent native risk path did not replay')
        print('All four native minute capital paths replay PASS')
    else:
        path.write_text(text)
        for key, value in result['results'].items():
            print(key, value['native_account_max_DD_pct'], value['first_5pct_breach'], value['worst_trough'])
