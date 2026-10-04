"""Order-aware accounting and loss-cycle audit of already frozen R13 paths."""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from r9_selection import load_prices

HERE=Path(__file__).resolve().parent


def cycles(orders):
    quantity={a:0. for a in ['BTC','ETH']};average=quantity.copy();profit=quantity.copy();fees=quantity.copy();opened={}
    rows=[]
    for r in orders.itertuples(index=False):
        a=r.asset;q=float(r.native_quantity);price=float(r.price)
        if abs(q*price-r.dollar_amount)>1e-12:raise AssertionError('Order quantity/price/notional units disagree')
        if q>0:
            if quantity[a]==0:opened[a]=r.time
            average[a]=(quantity[a]*average[a]+q*price)/(quantity[a]+q)
        elif q<0:profit[a]+=-q*(price-average[a])
        quantity[a]+=q;fees[a]+=r.cost
        if abs(quantity[a])<1e-14:
            if a in opened:
                rows.append(dict(asset=a,opened=str(opened.pop(a)),closed=str(r.time),exit_reason=r.reason,
                    price_pnl_USDT_10000_start=profit[a]*10000,cost_USDT_10000_start=fees[a]*10000,
                    net_pnl_USDT_10000_start=(profit[a]-fees[a])*10000))
            quantity[a],average[a],profit[a],fees[a]=0.,0.,0.,0.
    if any(quantity.values()):raise AssertionError('Terminal position remains open in the order audit')
    return rows


def audit():
    px,_,_=load_prices();results={}
    for name in ['Cov10_baseline','ATR22x3']:
        curve=pd.read_csv(HERE/f'r13_recent_reused_{name}_cost0.0012_daily.csv',index_col='date',parse_dates=True)
        orders=pd.read_csv(HERE/f'r13_recent_reused_{name}_cost0.0012_orders.csv',parse_dates=['time'])
        pnl=pd.DataFrame(index=curve.index)
        day_orders=orders.time.dt.floor('D')
        recorded_cost=orders.groupby(day_orders).cost.sum().reindex(curve.index,fill_value=0)
        if np.max(np.abs(recorded_cost.to_numpy()-curve.cost.to_numpy()))>1e-12:raise AssertionError('Daily/order costs disagree')
        for a in ['BTC','ETH']:
            closed=px[a+'_close'].reindex(curve.index)
            prior_close=px[a+'_close'].shift(1).reindex(curve.index)
            prior_units=curve['units_'+a].shift(1).fillna(0.)
            fills=orders.loc[orders.asset.eq(a)].copy();fill_days=fills.time.dt.floor('D')
            adjustment=pd.Series(fills.native_quantity.to_numpy()*(closed.reindex(fill_days).to_numpy()-fills.price.to_numpy()),index=fill_days).groupby(level=0).sum().reindex(curve.index,fill_value=0.)
            pnl[a]=prior_units*(closed-prior_close)+adjustment
        pnl['costs']=-curve.cost
        delta=curve.equity-curve.equity.shift(1).fillna(1.)
        gap=float(np.max(np.abs(pnl.sum(axis=1)-delta)))
        if gap>1e-12:raise AssertionError('Intraday fill-aware daily price P&L does not reconcile')
        drawdown=curve.equity/curve.equity.cummax().clip(lower=1)-1
        trough=drawdown.idxmin();past=curve.equity.loc[:trough];peak=past.idxmax() if past.max()>=1 else None
        episode=pnl.loc[:trough] if peak is None else pnl.loc[(pnl.index>peak)&(pnl.index<=trough)]
        cycle_rows=cycles(orders)
        net=sum(r['net_pnl_USDT_10000_start'] for r in cycle_rows)
        if abs(net-(curve.equity.iloc[-1]-1)*10000)>1e-8:raise AssertionError('All completed positions do not reconcile to terminal account profit')
        streak=longest=0
        for r in sorted(cycle_rows,key=lambda x:(x['closed'],x['asset'])):
            streak=streak+1 if r['net_pnl_USDT_10000_start']<0 else 0;longest=max(longest,streak)
        results[name]=dict(daily_peak='initial cash' if peak is None else str(peak),daily_trough=str(trough),
            daily_DD_pct=float(drawdown.loc[trough]*100),sampled_minute_DD_pct=float(curve.sampled_minute_running_dd_pct.iloc[-1]),
            loss_episode_asset_PnL_USDT_10000_start=(episode.sum()*10000).to_dict(),
            whole_window_asset_PnL_USDT_10000_start=(pnl.sum()*10000).to_dict(),all_days_attribution_max_abs_gap=gap,
            completed_position_count=len(cycle_rows),net_losing_position_count=sum(r['net_pnl_USDT_10000_start']<0 for r in cycle_rows),
            longest_chronological_losing_close_sequence=longest,net_closed_position_profit_USDT_10000_start=net,cycles=cycle_rows)
    change={a:results['ATR22x3']['whole_window_asset_PnL_USDT_10000_start'][a]-results['Cov10_baseline']['whole_window_asset_PnL_USDT_10000_start'][a] for a in ['BTC','ETH','costs']}
    return dict(results=results,ATR_minus_control_asset_PnL_USDT_10000_start=change,
        scope='Order-aware attribution of existing frozen paths only. No theoretical-stop fill alternative, no no-cost or identical-capital counterfactual, and no new strategy parameter. Chronological losing-close sequence is a descriptive count across two assets, not proof that those closures alone caused account drawdown.')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--verify-only',action='store_true');args=parser.parse_args()
    output=audit();path=HERE/'r13_reflection.json'
    if args.verify_only:
        if json.loads(path.read_text())!=output:raise ValueError('Frozen ATR reflection does not replay')
        print('All intraday fill/day-cost attribution and completed-cycle account identities replay PASS')
    else:
        path.write_text(json.dumps(output,indent=2)+'\n')
        for name,r in output['results'].items():print(name,{k:v for k,v in r.items() if k!='cycles'})
        print('ATR minus control',output['ATR_minus_control_asset_PnL_USDT_10000_start'])


if __name__=='__main__':main()
