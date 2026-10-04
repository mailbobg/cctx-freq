"""Attribution of already frozen flow-confirmation policies, without new trades."""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from r9_selection import load_prices
from r13_reflection import cycles

HERE = Path(__file__).resolve().parent
ASSETS = ['BTC','ETH']
WINDOWS = [('early_reused','2021-02-01','2023-12-31'), ('recent_reused','2024-01-01','2026-08-31')]


def reflect():
    px,_,_=load_prices()
    decisions = pd.read_csv(HERE/'r14_decision_audit.csv', parse_dates=['decision','feature_day','available_at'])
    results = {}
    for label,start,end in WINDOWS:
        versions = {}
        for name in ['Cov10_baseline','buy_confirmation']:
            curve=pd.read_csv(HERE/f'r14_{label}_{name}_cost0.0012_daily.csv',index_col='date',parse_dates=True)
            pnl=pd.DataFrame(index=curve.index)
            for a in ASSETS:
                previous_units=curve['units_'+a].shift(1).fillna(0.)
                previous_close=px[a+'_close'].shift(1).reindex(curve.index)
                opened,closed=px[a+'_open'].reindex(curve.index),px[a+'_close'].reindex(curve.index)
                pnl[a]=previous_units*(opened-previous_close)+curve['pre_close_units_'+a]*(closed-opened)
            pnl['costs']=-curve.cost
            gap=float((pnl.sum(axis=1)-(curve.equity-curve.equity.shift(1).fillna(1.))).abs().max())
            if gap>1e-12:raise AssertionError('Weekly OPEN/terminal CLOSE accounting does not reconcile')
            dd=curve.equity/curve.equity.cummax().clip(lower=1.)-1
            trough=dd.idxmin();past=curve.equity.loc[:trough];peak=past.idxmax() if past.max()>=1 else None
            episode=pnl.loc[:trough] if peak is None else pnl.loc[(pnl.index>peak)&(pnl.index<=trough)]
            peak_equity=1. if peak is None else float(curve.equity.loc[peak])
            change=float((curve.equity.loc[trough]-peak_equity)*10000)
            if abs(float(episode.sum().sum()*10000)-change)>1e-8:raise AssertionError('Loss-episode attribution failed')
            versions[name]=dict(peak='initial cash' if peak is None else str(peak),trough=str(trough),
                daily_MTM_DD_pct=float(dd.loc[trough]*100),episode_net_equity_change_USDT_10000_start=change,
                episode_asset_pnl_USDT_10000_start=(episode.sum()*10000).to_dict(),
                whole_window_asset_pnl_USDT_10000_start=(pnl.sum()*10000).to_dict(),all_days_attribution_max_abs_gap=gap,
                mean_held_exposure_pct=float(curve.pre_close_exposure.mean()*100),
                wholly_cash_days=int(curve.pre_close_exposure.lt(1e-12).sum()),total_days=len(curve))
            if label=='recent_reused':
                orders=pd.read_csv(HERE/f'r14_{label}_{name}_cost0.0012_orders.csv',parse_dates=['time'])
                rows=cycles(orders)
                net=sum(r['net_pnl_USDT_10000_start'] for r in rows)
                if abs(net-float((curve.equity.iloc[-1]-1)*10000))>1e-8:raise AssertionError('Completed cycles do not reconcile')
                versions[name].update(completed_positions=len(rows),net_losing_positions=sum(r['net_pnl_USDT_10000_start']<0 for r in rows),
                    completed_cycle_net_pnl_USDT_10000_start=net,cycles=rows)
        coverage={}
        selected=decisions.loc[decisions.decision.ge(pd.Timestamp(start,tz='UTC')) & decisions.decision.le(pd.Timestamp(end,tz='UTC'))]
        for a in ASSETS:
            group=selected.loc[selected.asset.eq(a)];on=group.baseline_weight.gt(1e-12)
            coverage[a]=dict(decision_weeks=len(group),known_30_day_features=int(group.known.sum()),
                positive_features=int(group.positive.sum()),original_on_weeks=int(on.sum()),
                retained_original_on_weeks=int((on & group.target_weight.gt(0)).sum()),
                vetoed_original_on_weeks=int((on & group.target_weight.eq(0)).sum()),
                unknown_original_on_weeks=int((on & ~group.known).sum()))
        delta={a:versions['buy_confirmation']['whole_window_asset_pnl_USDT_10000_start'][a]-versions['Cov10_baseline']['whole_window_asset_pnl_USDT_10000_start'][a] for a in [*ASSETS,'costs']}
        results[label]=dict(versions=versions,decision_coverage=coverage,buy_confirmation_minus_control_asset_PnL_USDT_10000_start=delta)
    return dict(results=results,scope='Accounting and descriptive coverage of original fixed policies. No sign reversal, new threshold/lookback, opportunity-ranking trial, causal proof of individual skipped trades, or cost-free counterfactual.')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--verify-only',action='store_true');args=parser.parse_args()
    output=reflect();path=HERE/'r14_reflection.json'
    if args.verify_only:
        if json.loads(path.read_text())!=output:raise ValueError('Frozen flow reflection did not replay')
        print('All account changes, loss attribution, completed recent cycles and fixed decision coverage replay PASS')
    else:
        path.write_text(json.dumps(output,indent=2)+'\n')
        for label,r in output['results'].items():
            compact={name:{k:v for k,v in value.items() if k!='cycles'} for name,value in r['versions'].items()}
            print(label,json.dumps(dict(versions=compact,coverage=r['decision_coverage'],difference=r['buy_confirmation_minus_control_asset_PnL_USDT_10000_start']),indent=2))


if __name__=='__main__':main()
