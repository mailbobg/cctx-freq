"""Read completed spot backtests and check daily MTM accounting before reporting."""
import json
import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'user_data/bench'))
from freqtrade.data.btanalysis import load_backtest_data, load_backtest_stats, get_latest_backtest_filename
from mtm import equity_curve
from block_bootstrap import block_interval


def summarize(directory):
    directory = Path(directory)
    path = directory if directory.is_file() else directory / get_latest_backtest_filename(directory)
    stats = load_backtest_stats(path)
    out = {}
    for name, s in stats['strategy'].items():
        trades = load_backtest_data(path, strategy=name)
        if trades.empty:
            out[name] = {'trades': 0}
            continue
        if trades['is_short'].any() or (trades['leverage'] != 1).any():
            raise ValueError('MTM reconstruction supports long, unleveraged spot only')
        days = pd.date_range(pd.to_datetime(s['backtest_start'], utc=True).floor('D'),
                             pd.to_datetime(s['backtest_end'], utc=True).floor('D'), freq='D')
        for pair in trades['pair'].unique():
            if not (ROOT / 'user_data/data/binance' / (pair.replace('/', '_')+'-1d.feather')).exists():
                raise ValueError(f'Missing daily prices: {pair}')
        equity, cash, holdings = equity_curve(trades, s['starting_balance'], days)
        final_gap = float(equity.iloc[-1] - s['starting_balance'] - trades['profit_abs'].sum())
        if abs(final_gap) > max(1., s['starting_balance'] * .001):
            raise ValueError(f'Unreconciled MTM final balance: {name} gap={final_gap}')
        pnl = equity.diff()
        pnl.iloc[0] = equity.iloc[0] - s['starting_balance']
        peak = equity.cummax().clip(lower=s['starting_balance'])
        out[name] = {'trades': len(trades), 'profit_pct': s['profit_total']*100,
                     'MTM_DD_pct': float((equity/peak-1).min()*100),
                     'MTM_block7_pct': block_interval(pnl.to_numpy()/s['starting_balance']*100, 7),
                     'MTM_block30_pct': block_interval(pnl.to_numpy()/s['starting_balance']*100, 30),
                     'accounting_gap_USDT': final_gap,
                     'max_daily_exposure_pct': float((holdings.sum(axis=1)/equity).max()*100),
                     'by_entry_tag': trades.groupby('enter_tag', dropna=False)['profit_abs'].agg(['count','sum']).reset_index().to_dict('records'),
                     'source': str(path)}
        equity.to_csv(Path(__file__).parent / (directory.name+'_'+name+'_equity.csv'), header=['equity'])
        print(name, {k:v for k,v in out[name].items() if k not in ('by_entry_tag','source')}, flush=True)
    (Path(__file__).parent / (directory.name+'_summary.json')).write_text(json.dumps(out,indent=2)+'\n')


if __name__ == '__main__':
    for arg in sys.argv[1:]:
        summarize(arg)
