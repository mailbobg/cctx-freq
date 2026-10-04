"""Reconcile every daily equity move and attribute the frozen gold loss path."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from r9_selection import load_prices

HERE = Path(__file__).resolve().parent


def main():
    px, _, _ = load_prices()
    gold = pd.read_parquet(HERE/'r10_data/PAXGUSDT_spot.parquet')
    px['PAXG_open'],px['PAXG_close'] = gold.open,gold.close
    result = {}
    for label in ('early_reused','recent_reused'):
        curve = pd.read_csv(HERE/f'r10_{label}_fixed_gold_cost0.0012_daily.csv',index_col='date',parse_dates=True)
        pnl = pd.DataFrame(index=curve.index)
        for asset in ('BTC','ETH','PAXG'):
            previous_units = curve['units_'+asset].shift(1).fillna(0.)
            previous_close = px[asset+'_close'].shift(1).reindex(curve.index)
            opened = px[asset+'_open'].reindex(curve.index)
            closed = px[asset+'_close'].reindex(curve.index)
            pnl[asset] = previous_units*(opened-previous_close)+curve['pre_close_units_'+asset]*(closed-opened)
        pnl['fees'] = -curve.cost
        actual = curve.equity-curve.equity.shift(1).fillna(1.)
        gap = float(np.max(np.abs(pnl.sum(axis=1)-actual)))
        if gap > 1e-12:
            raise AssertionError('Asset price and fee changes do not reconcile to daily account equity')
        drawdown = curve.equity/curve.equity.cummax().clip(lower=1.)-1
        trough = drawdown.idxmin()
        peak = curve.equity.loc[:trough].idxmax()
        episode = pnl.loc[(pnl.index>peak)&(pnl.index<=trough)]
        result[label] = {'peak':str(peak),'trough':str(trough),'daily_MTM_DD_pct':float(drawdown.loc[trough]*100),
                         'episode_asset_pnl_USDT_at_10000_start':(episode.sum()*10000).to_dict(),
                         'episode_net_equity_change_USDT':float((curve.equity.loc[trough]-curve.equity.loc[peak])*10000),
                         'all_days_attribution_max_abs_gap':gap,
                         'annual_asset_pnl_USDT_at_10000_start':{str(y):(part.sum()*10000).to_dict() for y,part in pnl.groupby(pnl.index.year)},
                         'note':'Existing fixed-rule holdings attribution only; neither a no-fee counterfactual nor an alternative hedge backtest.'}
    (HERE/'r10_attribution.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    main()
