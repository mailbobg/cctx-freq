"""Matched weekly baseline/candidate comparison; cached history only, no downloads."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'user_data/strategies'))
sys.path.insert(0, str(ROOT / 'user_data/bench'))
from portfolio_risk import portfolio_weights
from block_bootstrap import block_interval


def simulate(px, weights, start, end, fee):
    cash, units = 1., np.zeros(2)
    rows = []
    closes = px[['BTC_close', 'ETH_close']].to_numpy()
    opens = px[['BTC_open', 'ETH_open']].to_numpy()
    for i, day in enumerate(px.index):
        if not (start <= str(day.date()) <= end) or i < 31:
            continue
        if day.weekday() == 0:
            value = cash + units @ opens[i]
            targets = weights.iloc[i-1].to_numpy() * .99  # common reserve
            diff = targets * value - units * opens[i]
            diff[np.abs(diff) <= .05 * value] = 0.
            # Sells fund buys, and no negative cash / hidden leverage.
            for k in np.argsort(diff):
                amount = max(diff[k], -units[k] * opens[i, k])
                if amount > 0:
                    amount = min(amount, max(0., cash) / (1 + fee))
                units[k] += amount / opens[i, k]
                cash -= amount + abs(amount) * fee
        rows.append((day, cash + units @ closes[i]))
    return pd.Series(dict(rows))


def metrics(eq):
    pnl = eq.diff()
    pnl.iloc[0] = eq.iloc[0] - 1.
    returns = eq.pct_change()
    returns.iloc[0] = eq.iloc[0] - 1.
    peak = eq.cummax().clip(lower=1.)
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    ends = eq.groupby(eq.index.year).last()
    prev = ends.shift(1).fillna(1.)
    return {'return_pct': (eq.iloc[-1]-1)*100, 'CAGR_pct': (eq.iloc[-1]**(1/years)-1)*100,
            'maxDD_pct': (eq / peak - 1).min()*100,
            'sharpe': returns.mean()/returns.std()*np.sqrt(365),
            'years_pct': ((ends/prev-1)*100).to_dict(),
            'MTM_block7_interval_pct': block_interval(pnl.to_numpy()*100, 7),
            'MTM_block30_interval_pct': block_interval(pnl.to_numpy()*100, 30)}


def main():
    px = pd.read_parquet(ROOT / 'user_data/bench/events/c_daily.parquet')
    closes = px[['BTC_close', 'ETH_close']].set_axis(['BTC', 'ETH'], axis=1)
    vol = np.log(closes).diff().rolling(30).std()*np.sqrt(365)
    baseline = (.5*np.minimum(1., .60/vol)).where(closes/closes.shift(30)>1., 0.)
    candidate = portfolio_weights(closes)
    result = {}
    for window, start, end in [('2017-2020_reused', '2017-09-18', '2020-12-31'),
                                ('2021-2026_in_sample', '2021-01-01', '2026-09-21')]:
        for fee in (.001, .002):
            for name, w in [('baseline', baseline), ('portfolio_cap', candidate)]:
                eq = simulate(px, w, start, end, fee)
                key = f'{window}_fee{fee}_{name}'
                result[key] = metrics(eq)
                eq.to_csv(Path(__file__).parent / (key+'.csv'), header=['equity'])
                print(key, {k:round(v, 2) for k,v in result[key].items() if isinstance(v, float)}, flush=True)
    (Path(__file__).parent / 'c_results.json').write_text(json.dumps(result, indent=2)+'\n')


if __name__ == '__main__':
    main()
