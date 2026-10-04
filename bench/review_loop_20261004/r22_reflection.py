"""Independent native accounting and descriptive fixed-panel decisions."""
import argparse
import json
from pathlib import Path

import pandas as pd

from r9_selection import load_prices
from r10_gold import WINDOWS
from r19_reflection import read_frame,daily_attribution,native_reconcile
from r22_participation import ASSETS,PANEL

HERE=Path(__file__).resolve().parent


def reflect():
    px,_,_=load_prices();decisions=read_frame(HERE/'r22_decision_audit.csv',parse_dates=['decision'])
    minutes={a:pd.read_parquet(HERE/f'r13_data/{a}USDT_1m.parquet') for a in ASSETS};results={}
    for label,start,end in WINDOWS:
        versions={};selected=decisions.loc[decisions.window.eq(label)]
        coverage=dict(decision_weeks=len(selected),known_weeks=int(selected.known.sum()),
            source_reason_counts=selected.reason.value_counts().to_dict(),
            positive_member_count_histogram={int(k):int(v) for k,v in selected.positive_count.value_counts().sort_index().items()},
            per_member_positive_weeks={s:int(selected[s+'_positive'].sum()) for s in PANEL})
        active=selected[[f'baseline_{a}' for a in ASSETS]].sum(axis=1).gt(1e-12)
        coverage['vetoed_original_active_weeks_by_reason']=selected.loc[active & ~selected.allow].reason.value_counts().to_dict()
        coverage['active_original_weeks']=int(active.sum())
        coverage['vetoed_original_active_coin_weeks']={a:int((selected['baseline_'+a].gt(1e-12)&~selected.allow).sum()) for a in ASSETS}
        for cost in (.0012,.0024):
            for name in ('Cov10_baseline','participation_confirmation'):
                key=f'{label}_{name}_cost{cost}';curve=read_frame(HERE/f'r22_{key}_daily.csv',index_col='date',parse_dates=True)
                pnl,gap=daily_attribution(curve,px)
                dd=curve.equity/curve.equity.cummax().clip(lower=1.)-1;trough=dd.idxmin();past=curve.equity.loc[:trough]
                peak=past.idxmax() if past.max()>=1 else None
                episode=pnl.loc[:trough] if peak is None else pnl.loc[(pnl.index>peak)&(pnl.index<=trough)]
                v=dict(peak='initial cash' if peak is None else str(peak),trough=str(trough),
                    whole_window_asset_pnl_USDT_10000_start=(pnl.sum()*10000).to_dict(),
                    episode_asset_pnl_USDT_10000_start=(episode.sum()*10000).to_dict(),
                    all_days_attribution_max_abs_gap=gap,mean_held_exposure_pct=float(curve.pre_close_exposure.mean()*100),
                    wholly_cash_days=int(curve.pre_close_exposure.lt(1e-12).sum()),total_days=len(curve))
                if label=='recent_reused':
                    orders=read_frame(HERE/f'r22_{key}_orders.csv',parse_dates=['time'])
                    v['independent_source_native_ledger']=native_reconcile(curve,orders,minutes,cost)
                versions[f'{name}_cost{cost}']=v
        delta={a:versions['participation_confirmation_cost0.0012']['whole_window_asset_pnl_USDT_10000_start'][a]-versions['Cov10_baseline_cost0.0012']['whole_window_asset_pnl_USDT_10000_start'][a] for a in [*ASSETS,'fees']}
        results[label]=dict(versions=versions,decision_coverage=coverage,participation_minus_control_PnL_USDT_10000_start=delta)
    return dict(results=results,scope='Independent retained native price/fill accounting and descriptive panel decisions. No causal skipped-trade alpha or sign/horizon/member/majority/day/sizing/fee sweep; no adoption.')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--verify-only',action='store_true');args=p.parse_args()
    value=reflect();path=HERE/'r22_reflection.json';text=json.dumps(value,indent=2,sort_keys=True)+'\n'
    if args.verify_only:
        if path.read_text()!=text:raise ValueError('Frozen native participation reflection did not replay')
        print('Every daily price/fee change and recent source/native fill ledger replay PASS')
    else:
        path.write_text(text)
        for label,r in value['results'].items():print(label,r['decision_coverage'],r['participation_minus_control_PnL_USDT_10000_start'])
