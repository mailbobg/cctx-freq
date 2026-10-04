"""Independent price/fee account reconciliation and descriptive mechanism audit."""
import argparse
import json
from pathlib import Path
import pandas as pd
from r9_selection import load_prices
from r10_gold import WINDOWS
from r19_reflection import read_frame,daily_attribution,native_reconcile
from r33_quantity import ASSETS

HERE=Path(__file__).resolve().parent


def reflect():
    source=json.loads((HERE/'r33_source_audit.json').read_text())
    decisions=read_frame(HERE/'r33_decision_audit.csv')
    px,_,_=load_prices();minutes={a:pd.read_parquet(HERE/f'r13_data/{a}USDT_1m.parquet') for a in ASSETS}
    for _,row in decisions.iterrows():
        decision=str(pd.Timestamp(row.monday,tz='UTC'));f=source['source_feature_cohort'][decision]
        quantities=[]
        for kind in ('current','prior'):
            r=f[kind];literal=source['independent_native_cell_joins'][r['release_date']]['exact_native_average']
            if literal!=row[kind+'_week_average_million_usd'] or r['observation_date']!=row[kind+'_observation_date']:
                raise ValueError('Independent decision literal/date identity differs')
            quantities.append(literal)
        veto=quantities[0]<quantities[1]
        if veto!=bool(row.contraction):raise ValueError('Independent actual quantity sign differs')
        for asset in ASSETS:
            expected=0. if veto else row['baseline_'+asset]
            if abs(row['target_'+asset]-expected)>1e-12:raise ValueError('Independent joint target reconstruction differs')
    results={}
    for label,start,end in WINDOWS:
        selected=decisions.loc[decisions.window.eq(label)]
        active=selected[['baseline_'+a for a in ASSETS]].sum(axis=1).gt(1e-12)
        coverage=dict(decision_weeks=len(selected),active_original_weeks=int(active.sum()),
            contraction_weeks=int(selected.contraction.sum()),vetoed_original_active_weeks=int((active & selected.contraction).sum()),
            current_observation_lag_days=((pd.to_datetime(selected.monday)-pd.to_datetime(selected.current_observation_date)).dt.days.value_counts().to_dict()),
            issuing_report_weekday_counts=pd.to_datetime(selected.current_release_date).dt.day_name().value_counts().to_dict())
        versions={}
        for cost in (.0012,.0024):
            for name in ('Cov10_baseline','reserve_quantity'):
                key=f'{label}_{name}_cost{cost}';curve=read_frame(HERE/f'r33_{key}_daily.csv',index_col='date',parse_dates=True)
                pnl,gap=daily_attribution(curve,px)
                dd=curve.equity/curve.equity.cummax().clip(lower=1.)-1;trough=dd.idxmin();past=curve.equity.loc[:trough]
                peak=past.idxmax() if past.max()>=1 else None
                episode=pnl.loc[:trough] if peak is None else pnl.loc[(pnl.index>peak)&(pnl.index<=trough)]
                value=dict(peak='initial cash' if peak is None else str(peak),trough=str(trough),
                    whole_window_asset_pnl_USDT_10000_start=(pnl.sum()*10000).to_dict(),
                    episode_asset_pnl_USDT_10000_start=(episode.sum()*10000).to_dict(),all_days_attribution_max_abs_gap=gap,
                    mean_held_exposure_pct=float(curve.pre_close_exposure.mean()*100),
                    wholly_cash_days=int(curve.pre_close_exposure.lt(1e-12).sum()),total_days=len(curve))
                if label=='recent_reused':
                    orders=read_frame(HERE/f'r33_{key}_orders.csv',parse_dates=['time'])
                    value['independent_source_native_ledger']=native_reconcile(curve,orders,minutes,cost)
                versions[f'{name}_cost{cost}']=value
        delta={a:versions['reserve_quantity_cost0.0012']['whole_window_asset_pnl_USDT_10000_start'][a]-
                 versions['Cov10_baseline_cost0.0012']['whole_window_asset_pnl_USDT_10000_start'][a]
               for a in [*ASSETS,'fees']}
        results[label]=dict(versions=versions,decision_coverage=coverage,reserve_quantity_minus_control_PnL_USDT_10000_start=delta)
    return dict(results=results,all_584_independent_native_quantity_cells_and_joint_targets_reconciled=True,
        scope='Descriptive original-report quantities and independent native price/fee accounts. No causal skipped-trade attribution, clock/edition/fill authenticity, new OOS, rule tuning or adoption.')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--verify-only',action='store_true');args=p.parse_args()
    x=reflect();out=HERE/'r33_reflection.json';text=json.dumps(x,indent=2,sort_keys=True)+'\n'
    if args.verify_only:
        if out.read_text()!=text:raise ValueError('Independent reflection does not replay')
        print('Independent quantity targets and all daily/recent minute accounts replay PASS')
    else:
        out.write_text(text)
        for label,value in x['results'].items():print(label,value['decision_coverage'],value['reserve_quantity_minus_control_PnL_USDT_10000_start'])
