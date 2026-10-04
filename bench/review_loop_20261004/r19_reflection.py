"""Independent source-price/native-order and account-change reconciliation."""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from r9_selection import load_prices
from r10_gold import WINDOWS

HERE=Path(__file__).resolve().parent
ASSETS=['BTC','ETH']


def native_reconcile(curve, orders, minutes, cost):
    if not orders.time.is_monotonic_increasing: raise ValueError('Unordered native fills')
    cash,units=1.,np.zeros(2); max_gap=0.; prices=0
    for day,row in curve.iterrows():
        opened=np.array([minutes[a].loc[day,'open'] for a in ASSETS],dtype=float)
        before=cash+units@opened
        max_gap=max(max_gap,abs(before-row.pre_trade_open_equity))
        selected=orders.loc[(orders.time>=day)&(orders.time<day+pd.Timedelta(days=1))]
        fees=0.; turnover=np.zeros(2); terminal=False
        for fill in selected.itertuples():
            k=ASSETS.index(fill.asset); t=fill.time
            if fill.reason=='weekly':
                if day.weekday()!=0 or t!=day or terminal: raise ValueError('Unexpected weekly order time')
                expected=float(minutes[fill.asset].loc[t,'open'])
            elif fill.reason=='terminal_daily_close':
                if day!=curve.index[-1] or t!=day+pd.Timedelta(hours=23,minutes=59): raise ValueError('Unexpected terminal order time')
                expected=float(minutes[fill.asset].loc[t,'close']); terminal=True
            else: raise ValueError('Unfrozen order reason')
            if fill.price!=expected: raise ValueError('Native fill price does not match source execution observation')
            notional=fill.native_quantity*expected; fee=abs(notional)*cost
            if abs(notional-fill.dollar_amount)>1e-12 or abs(fee-fill.cost)>1e-12: raise ValueError('Native quantity/cost mismatch')
            cash-=notional+fee; units[k]+=fill.native_quantity; fees+=fee;turnover[k]+=abs(notional); prices+=1
            if cash < -1e-12 or units.min() < -1e-12: raise ValueError('Independent native ledger borrowed')
        closed=np.array([minutes[a].loc[day+pd.Timedelta(hours=23,minutes=59),'close'] for a in ASSETS],dtype=float)
        compare=np.r_[cash,units,cash+units@closed,fees,turnover]
        retained=np.r_[row.cash,*[row['units_'+a] for a in ASSETS],row.equity,row.cost,*[row['turnover_'+a] for a in ASSETS]]
        max_gap=max(max_gap,float(np.abs(compare-retained).max()))
    if max_gap>1e-12: raise ValueError('Independent native execution/account reconciliation failed')
    return dict(source_matched_native_fills=prices,max_abs_cash_units_equity_fees_turnover_gap=max_gap,
        final_native_cash=cash,terminal_native_units=units.tolist(),actual_exchange_execution_certified=False)


def daily_attribution(curve, px):
    pnl=pd.DataFrame(index=curve.index)
    for a in ASSETS:
        previous=curve['units_'+a].shift(1).fillna(0.)
        prior_close=px[a+'_close'].shift(1).reindex(curve.index)
        op,cl=px[a+'_open'].reindex(curve.index),px[a+'_close'].reindex(curve.index)
        pnl[a]=previous*(op-prior_close)+curve['pre_close_units_'+a]*(cl-op)
    pnl['fees']=-curve.cost
    change=curve.equity-curve.equity.shift(1).fillna(1.)
    gap=float(np.abs(pnl.sum(axis=1)-change).max())
    if gap>1e-12: raise ValueError('Independent daily price/fee attribution failed')
    return pnl,gap


def reflect():
    px,_,_=load_prices(); decisions=pd.read_csv(HERE/'r19_decision_audit.csv',parse_dates=['decision'])
    minutes={a:pd.read_parquet(HERE/f'r13_data/{a}USDT_1m.parquet') for a in ASSETS}
    results={}
    for label,start,end in WINDOWS:
        versions={}; selected=decisions.loc[decisions.window.eq(label)]
        coverage=dict(decision_weeks=len(selected),known_pairs=int(selected.known.sum()),
            missing_friday_weeks=int(selected.reason.eq('missing_friday').sum()),
            inverted_term_weeks=int(selected.reason.eq('inverted_term').sum()),
            normal_term_weeks=int(selected.reason.eq('normal_term').sum()))
        active=selected[[f'baseline_{a}' for a in ASSETS]].sum(axis=1).gt(1e-12)
        coverage['vetoed_original_active_weeks_by_reason']=selected.loc[active & ~selected.allow].reason.value_counts().to_dict()
        for cost in (.0012,.0024):
            for name in ('Cov10_baseline','term_confirmation'):
                key=f'{label}_{name}_cost{cost}'; curve=pd.read_csv(HERE/f'r19_{key}_daily.csv',index_col='date',parse_dates=True)
                pnl,gap=daily_attribution(curve,px)
                dd=curve.equity/curve.equity.cummax().clip(lower=1.)-1; trough=dd.idxmin(); past=curve.equity.loc[:trough]
                peak=past.idxmax() if past.max()>=1 else None
                episode=pnl.loc[:trough] if peak is None else pnl.loc[(pnl.index>peak)&(pnl.index<=trough)]
                v=dict(peak='initial cash' if peak is None else str(peak),trough=str(trough),
                    whole_window_asset_pnl_USDT_10000_start=(pnl.sum()*10000).to_dict(),
                    episode_asset_pnl_USDT_10000_start=(episode.sum()*10000).to_dict(),
                    all_days_attribution_max_abs_gap=gap,mean_held_exposure_pct=float(curve.pre_close_exposure.mean()*100),
                    wholly_cash_days=int(curve.pre_close_exposure.lt(1e-12).sum()),total_days=len(curve))
                if label=='recent_reused':
                    orders=pd.read_csv(HERE/f'r19_{key}_orders.csv',parse_dates=['time'])
                    v['independent_source_native_ledger']=native_reconcile(curve,orders,minutes,cost)
                versions[f'{name}_cost{cost}']=v
        delta={a:versions['term_confirmation_cost0.0012']['whole_window_asset_pnl_USDT_10000_start'][a]-versions['Cov10_baseline_cost0.0012']['whole_window_asset_pnl_USDT_10000_start'][a] for a in [*ASSETS,'fees']}
        results[label]=dict(versions=versions,decision_coverage=coverage,term_minus_control_PnL_USDT_10000_start=delta)
    return dict(results=results,scope='Independent source-price/native-order accounting and descriptive observed paths only. Holiday/availability vetoes distinct from term states. No causal skipped-trade alpha, sign/threshold/latency/sizing/universe sweep or adoption.')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--verify-only',action='store_true');args=p.parse_args()
    value=reflect(); path=HERE/'r19_reflection.json'; text=json.dumps(value,indent=2,sort_keys=True)+'\n'
    if args.verify_only:
        if path.read_text()!=text: raise ValueError('Frozen term reflection did not replay')
        print('Every daily price/fee change and recent source/native fill ledger replay PASS')
    else:
        path.write_text(text)
        for label,r in value['results'].items(): print(label,r['decision_coverage'],r['term_minus_control_PnL_USDT_10000_start'])
