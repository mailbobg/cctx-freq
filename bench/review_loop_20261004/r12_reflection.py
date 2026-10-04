"""Reconcile the retained daily-off loss paths; no new counterfactual rule."""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from r9_selection import load_prices

HERE = Path(__file__).resolve().parent


def reflect():
    px, _, _ = load_prices()
    result = {}
    for label in ('early_reused', 'recent_reused'):
        versions = {}
        for name in ('Cov10_baseline', 'daily_off'):
            curve = pd.read_csv(HERE/f'r12_{label}_{name}_cost0.0012_daily.csv', index_col='date', parse_dates=True)
            pnl = pd.DataFrame(index=curve.index)
            for asset in ('BTC', 'ETH'):
                previous_units = curve['units_'+asset].shift(1).fillna(0.)
                previous_close = px[asset+'_close'].shift(1).reindex(curve.index)
                opened, closed = px[asset+'_open'].reindex(curve.index), px[asset+'_close'].reindex(curve.index)
                pnl[asset] = previous_units*(opened-previous_close)+curve['pre_close_units_'+asset]*(closed-opened)
            pnl['fees'] = -curve.cost
            actual = curve.equity-curve.equity.shift(1).fillna(1.)
            gap = float(np.max(np.abs(pnl.sum(axis=1)-actual)))
            if gap > 1e-12:
                raise AssertionError('Asset price and fees do not reconcile to daily equity changes')
            drawdown = curve.equity/curve.equity.cummax().clip(lower=1.)-1
            trough = drawdown.idxmin()
            past = curve.equity.loc[:trough]
            peak = past.idxmax() if past.max() >= 1 else None
            episode = pnl.loc[:trough] if peak is None else pnl.loc[(pnl.index>peak)&(pnl.index<=trough)]
            peak_equity = 1 if peak is None else curve.equity.loc[peak]
            change = float((curve.equity.loc[trough]-peak_equity)*10000)
            if abs(float(episode.sum().sum()*10000)-change) > 1e-8:
                raise AssertionError('Loss-episode asset attribution disagrees with account change')
            versions[name] = {
                'peak': 'initial cash' if peak is None else str(peak), 'trough': str(trough),
                'daily_MTM_DD_pct': float(drawdown.loc[trough]*100),
                'episode_asset_pnl_USDT_at_10000_start': (episode.sum()*10000).to_dict(),
                'episode_net_equity_change_USDT': change,
                'whole_window_asset_pnl_USDT_at_10000_start': (pnl.sum()*10000).to_dict(),
                'all_days_attribution_max_abs_gap': gap,
            }
        delta = {a:versions['daily_off']['whole_window_asset_pnl_USDT_at_10000_start'][a]-versions['Cov10_baseline']['whole_window_asset_pnl_USDT_at_10000_start'][a] for a in ('BTC', 'ETH', 'fees')}
        result[label] = {'versions':versions, 'daily_off_minus_control_attribution_USDT':delta}
    return dict(results=result, scope='Attribution of two already-frozen policies with their actual modeled unit/cash paths. Price-PnL difference is not a zero-fee counterfactual or the isolated causal effect of one exit; no new policy, threshold or profit selection.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    output = reflect()
    path = HERE/'r12_reflection.json'
    if args.verify_only:
        if json.loads(path.read_text()) != output:
            raise ValueError('Frozen daily-off reflection does not reproduce')
        print('Every daily account change and retained loss-path attribution replay PASS')
    else:
        path.write_text(json.dumps(output, indent=2)+'\n')
        print(json.dumps(output, indent=2))


if __name__ == '__main__':
    main()
