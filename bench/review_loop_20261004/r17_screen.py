"""Aggregate the frozen R17 recent necessary screen; cannot adopt a strategy."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from r17_inputs import HERE,COSTS,POLICIES,digest


def metrics(daily,hourly,orders):
    account=np.r_[10000.,hourly[['before_funding_equity','after_funding_equity','after_orders_equity','pre_terminal_equity','equity']].to_numpy().ravel()]
    sampled_dd=float((account/np.maximum.accumulate(account)-1).min()*100)
    daily_path=np.r_[10000.,daily.equity.to_numpy()]
    daily_dd=float((daily_path/np.maximum.accumulate(daily_path)-1).min()*100)
    final=float(daily_path[-1]);cagr=((final/10000)**(365/len(daily))-1)*100 if final>0 else -100.
    yearly={};prior=10000.
    for year,frame in daily.groupby(daily.index.year):
        yearly[str(year)]=(float(frame.equity.iloc[-1])/prior-1)*100;prior=float(frame.equity.iloc[-1])
    margin=int(hourly.marked_margin_concern.sum());envelope=int(hourly.envelope_margin_concern.sum())
    reconcile=final-(10000+daily.realized_price_pnl.sum()+daily.funding.sum()-daily.cost.sum())
    if abs(reconcile)>1e-6:raise AssertionError('Terminal performance identity differs')
    active=orders.loc[orders.action.eq('open'),'date'].nunique()
    occupied=(hourly.gross_notional/hourly.after_orders_equity).to_numpy()
    return dict(calendar_days=len(daily),hour_count=len(hourly),final_equity_USDT=final,net_profit_USDT=final-10000,
                return_pct=(final/10000-1)*100,CAGR_pct=cagr,daily_close_DD_pct=-daily_dd,
                sampled_hour_phase_DD_pct=-sampled_dd,funding_USDT=float(daily.funding.sum()),
                realized_price_pnl_USDT=float(daily.realized_price_pnl.sum()),fees_USDT=float(daily.cost.sum()),
                marked_margin_concern_hours=margin,adverse_high_margin_concern_hours=envelope,
                min_sampled_futures_equity_USDT=float(hourly[['before_funding_futures_equity','after_funding_futures_equity','after_orders_futures_equity','closing_futures_equity']].to_numpy().min()),
                min_adverse_high_futures_equity_USDT=float(hourly.adverse_futures_equity.min()),
                max_marked_gross_pct=float(np.max(occupied)*100),calendar_average_marked_gross_pct=float(np.mean(occupied)*100),
                active_weeks=int(active),active_holding_hours=int(hourly.active.sum()),
                paired_asset_entries=int(orders.loc[orders.action.eq('open')&orders.leg.eq('spot')].shape[0]),
                individual_leg_fills=len(orders),ledger_reconciliation_USDT=float(reconcile),calendar_year_return_pct=yearly,
                recent_necessary_screen_gate=bool(cagr>12 and daily_dd>=-5 and sampled_dd>=-5 and not margin and not envelope))


def main():
    frozen=json.loads((HERE/'r17_input_validation.json').read_text())
    for mapping in ('source_sha256','input_manifests_sha256','execution_output_sha256'):
        for name,sha in frozen[mapping].items():
            if digest(HERE/name)!=sha:raise ValueError('Frozen pre-performance R17 artifact changed')
    results={}
    for policy in POLICIES:
        for cost in COSTS:
            key=f'recent_{policy}_cost{cost}'
            daily=pd.read_csv(HERE/f'r17_{key}_daily.csv',index_col='date',parse_dates=True)
            hourly=pd.read_parquet(HERE/f'r17_{key}_hourly.parquet')
            orders=pd.read_csv(HERE/f'r17_{key}_orders.csv',parse_dates=['date'])
            result=metrics(daily,hourly,orders);results[key]=result
            print(key,json.dumps(result),flush=True)
    cash=pd.read_csv(HERE/'r17_cash_control.csv',index_col='date',parse_dates=True)
    if not cash.equity.eq(10000).all():raise AssertionError('Independent cash control changed')
    base=results['recent_ranked_cost0.0012'];stress=results['recent_ranked_cost0.0024']
    rejected=not (base['recent_necessary_screen_gate'] and stress['net_profit_USDT']>0)
    output=dict(protocol='R17_PROTOCOL.md',input_freeze='r17_input_validation.json',window='2024-01-01 through2026-08-31 inclusive',
                reused_history=True,independent_OOS=False,necessary_recent_screen_only=True,results=results,
                doubled_cost_recent_candidate_net_profit_USDT=stress['net_profit_USDT'],
                rejected_at_first_necessary_screen=rejected,adopted=False,
                assumptions='Owned native spot and equal native quantity short USDT perpetual; separately paid spot cash and futures collateral; actual settlement associated marks/rates; explicit full weekly close/repool/reopen; hourly trade OPEN and terminal CLOSE with0.12%/0.24% per leg fill all-in cost. Quotes, lots, historical margin tiers, intrahour ticks and independent forward remain unverified. Flat weeks keep cash without invented price.',
                no_parameter_rescue=True,other_window_run=False,
                preregistered_engine_commit='096c0c2',
                screening_source_sha256={'r17_screen.py':digest(Path(__file__))})
    (HERE/'r17_results.json').write_text(json.dumps(output,indent=2)+'\n')
    print('Recent first screen rejected:',rejected,'adopted: False',flush=True)


if __name__=='__main__':main()
