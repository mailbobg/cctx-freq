"""Independent retained-fill/funding and hourly P&L reflection, no new trades."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from r17_carry import ASSETS,rate_decimal,sum_rates
from r17_inputs import HERE,COSTS,POLICIES,digest,load


def audit(key,inputs):
    spot,trade,mark,fund,decisions,begin,end=inputs
    hourly=pd.read_parquet(HERE/f'r17_{key}_hourly.parquet')
    orders=pd.read_csv(HERE/f'r17_{key}_orders.csv',parse_dates=['date'],float_precision='round_trip')
    scores=pd.read_csv(HERE/'r17_decision_audit.csv',dtype={'forecast_decimal':str},parse_dates=['decision'])
    hours=hourly.index;n=len(ASSETS)
    q_end=hourly[[s+'_spot_quantity' for s in ASSETS]].to_numpy()
    q_pre=np.vstack([np.zeros(n),q_end[:-1]])
    delta_spot=np.zeros_like(q_end);delta_future=np.zeros_like(q_end)
    open_cost=np.zeros(len(hours));terminal_cost=np.zeros(len(hours));terminal_change=np.zeros(len(hours))
    sopen=np.column_stack([spot[s].open.reindex(hours).to_numpy() for s in ASSETS])
    sclose=np.column_stack([spot[s].close.reindex(hours).to_numpy() for s in ASSETS])
    fopen=np.column_stack([trade[s].open.reindex(hours).to_numpy() for s in ASSETS])
    fclose=np.column_stack([trade[s].close.reindex(hours).to_numpy() for s in ASSETS])
    mopen=np.column_stack([mark[s].open.reindex(hours).to_numpy() for s in ASSETS])
    mclose=np.column_stack([mark[s].close.reindex(hours).to_numpy() for s in ASSETS])
    for row in orders.itertuples():
        i=hours.get_loc(row.date);j=ASSETS.index(row.symbol)
        source=spot if row.leg=='spot' else trade
        field='close' if row.action=='terminal' else 'open'
        if row.price!=source[row.symbol].loc[row.date,field]:raise AssertionError('Retained fill price/source changed')
        if row.action=='terminal':
            terminal_cost[i]+=row.cost
            if row.leg=='future':terminal_change[i]+=row.quantity*(mclose[i,j]-row.price)
        else:
            open_cost[i]+=row.cost
            delta=(1 if row.side=='buy' else -1)*row.quantity
            (delta_spot if row.leg=='spot' else delta_future)[i,j]+=delta
    q_after=q_pre+delta_spot
    terminal_spot=orders.loc[orders.action.eq('terminal')&orders.leg.eq('spot')]
    expected_end=q_after.copy()
    for r in terminal_spot.itertuples():expected_end[hours.get_loc(r.date),ASSETS.index(r.symbol)]-=r.quantity
    if not np.allclose(expected_end,q_end,rtol=0,atol=1e-10):raise AssertionError('Retained fills differ from native inventory')
    if not np.allclose(delta_spot,-delta_future,rtol=0,atol=1e-10):raise AssertionError('Native hedge fill quantities differ')
    fund_hour=np.zeros(len(hours));fund_total=0.;cycles=[]
    for j,symbol in enumerate(ASSETS):
        log=orders.loc[orders.symbol.eq(symbol)&orders.leg.eq('spot')].copy()
        update=log[['date','action','quantity']].copy()
        update['held_quantity']=np.where(update.action.eq('open'),update.quantity,0.)
        update=update.drop_duplicates('date',keep='last').sort_values('date')
        events=fund[symbol].events.loc[(fund[symbol].events.slot>=begin)&(fund[symbol].events.slot<end)].copy()
        calendar=events[['slot','mark','rate_decimal']].sort_values('slot').rename(columns={'slot':'date'})
        calendar['date']=pd.DatetimeIndex(calendar.date).as_unit('ns')
        if update.empty:calendar['held_quantity']=0.
        else:
            update['date']=pd.DatetimeIndex(update.date).as_unit('ns')
            calendar=pd.merge_asof(calendar,update[['date','held_quantity']],on='date',direction='backward',allow_exact_matches=False)
            # Before the first actual fill the initial inventory is known flat.
            calendar['held_quantity']=calendar.held_quantity.fillna(0.)
        calendar['payment']=calendar.held_quantity*calendar['mark']*calendar.rate_decimal.map(float)
        payments=calendar.groupby('date').payment.sum().reindex(hours,fill_value=0.).to_numpy()
        fund_hour+=payments;fund_total+=float(calendar.payment.sum())
        held=None
        for row in log.itertuples():
            pair=orders.loc[orders.date.eq(row.date)&orders.symbol.eq(symbol)&orders.action.eq(row.action)]
            if len(pair)!=2 or pair.leg.nunique()!=2 or not pair.quantity.eq(row.quantity).all():raise AssertionError('Paired native fills differ')
            future_price=float(pair.loc[pair.leg.eq('future'),'price'].iloc[0])
            if row.action=='open':
                if held is not None:raise AssertionError('Open before complete paired close')
                held=(row.date,row.quantity,row.price,future_price,float(pair.cost.sum()))
            else:
                if held is None:raise AssertionError('Close without owned spot')
                opened,q,entry_spot,entry_future,entry_cost=held
                if q!=row.quantity:raise AssertionError('Close native units differ')
                within=calendar.loc[(calendar.date>opened)&(calendar.date<=row.date)]
                spot_pnl=q*(row.price-entry_spot);future_pnl=q*(entry_future-future_price)
                funding=float(within.payment.sum());cost=entry_cost+float(pair.cost.sum())
                matched=scores.loc[scores.decision.eq(opened)&scores.symbol.eq(symbol),'forecast_decimal']
                forecast=str(rate_decimal(matched.iloc[0]));realized=str(sum_rates(within.rate_decimal))
                cycles.append(dict(symbol=symbol,opened=opened.isoformat(),closed=row.date.isoformat(),native_quantity=q,
                                   forecast_rate_decimal=forecast,next_hold_actual_rate_decimal=realized,
                                   actual_settlement_count=len(within),spot_price_pnl_USDT=spot_pnl,
                                   short_price_pnl_USDT=future_pnl,paired_basis_price_pnl_USDT=spot_pnl+future_pnl,
                                   funding_USDT=funding,fees_USDT=cost,net_USDT=spot_pnl+future_pnl+funding-cost))
                held=None
        if held is not None:raise AssertionError('Final retained-order paired cycle is not flat')
    if not np.allclose(fund_hour,hourly.funding.to_numpy(),rtol=0,atol=1e-8):raise AssertionError('Actual associated-mark funding differs from retained native fills')
    overnight=np.zeros(len(hours));within_hour=np.zeros(len(hours));fill_change=np.zeros(len(hours))
    held_pre=q_pre.any(axis=1);held_after=q_after.any(axis=1);fill_hours=delta_future.any(axis=1)
    prev_sclose=np.vstack([np.full(n,np.nan),sclose[:-1]])
    prev_mclose=np.vstack([np.full(n,np.nan),mclose[:-1]])
    overnight[held_pre]=(q_pre[held_pre]*(sopen[held_pre]-prev_sclose[held_pre])-
                         q_pre[held_pre]*(mopen[held_pre]-prev_mclose[held_pre])).sum(axis=1)
    within_hour[held_after]=(q_after[held_after]*(sclose[held_after]-sopen[held_after])-
                            q_after[held_after]*(mclose[held_after]-mopen[held_after])).sum(axis=1)
    fill_change[fill_hours]=(delta_future[fill_hours]*(mopen[fill_hours]-fopen[fill_hours])).sum(axis=1)
    prior=np.r_[10000.,hourly.equity.to_numpy()[:-1]]
    errors=dict(overnight_open=float(np.max(np.abs(hourly.before_funding_equity.to_numpy()-prior-overnight))),
                actual_funding=float(np.max(np.abs(hourly.after_funding_equity-hourly.before_funding_equity-fund_hour))),
                open_fill_basis_and_cost=float(np.max(np.abs(hourly.after_orders_equity-hourly.after_funding_equity-fill_change+open_cost))),
                native_intrahour_mark=float(np.max(np.abs(hourly.pre_terminal_equity-hourly.after_orders_equity-within_hour))),
                terminal_basis_and_cost=float(np.max(np.abs(hourly.equity-hourly.pre_terminal_equity-terminal_change+terminal_cost))))
    if max(errors.values())>1e-7:raise AssertionError('Independent hourly native P&L does not reconcile')
    price=sum(r['paired_basis_price_pnl_USDT'] for r in cycles);fees=sum(r['fees_USDT'] for r in cycles)
    final=float(hourly.equity.iloc[-1]);gap=final-(10000+price+fund_total-fees)
    if abs(gap)>1e-7:raise AssertionError('Independent completed-cycle net profit differs')
    margin_ratio=np.divide(hourly.futures_gross,hourly.after_orders_futures_equity)
    opened=hours.isin(orders.loc[orders.action.eq('open'),'date'])
    result=dict(cycles=cycles,independent_realized_price_pnl_USDT=price,independent_actual_funding_USDT=fund_total,
                independent_four_fill_costs_USDT=fees,completed_cycle_net_USDT=sum(r['net_USDT'] for r in cycles),
                terminal_cycle_reconciliation_USDT=gap,max_hourly_identity_error_USDT=errors,
                profitable_paired_cycles=sum(r['net_USDT']>0 for r in cycles),paired_cycle_count=len(cycles),
                next_rate_sum_below_forecast_cycles=sum(rate_decimal(r['next_hold_actual_rate_decimal'])<rate_decimal(r['forecast_rate_decimal']) for r in cycles),
                costs_as_fraction_actual_funding=fees/fund_total if fund_total>0 else None,
                max_entry_postfill_account_gross_pct=float((hourly.loc[opened,'gross_notional']/hourly.loc[opened,'after_orders_equity']).max()*100),
                max_between_week_futures_gross_to_marked_equity=float(margin_ratio.max()))
    print(key,'independent native cycles and five hourly identities reconcile',flush=True)
    return result


def main():
    inputs=load();screen=json.loads((HERE/'r17_results.json').read_text());results={}
    for policy in POLICIES:
        for cost in COSTS:
            key=f'recent_{policy}_cost{cost}';results[key]=audit(key,inputs)
    candidate=results['recent_ranked_cost0.0012'];control=results['recent_equal_pairs_cost0.0012']
    comparison=dict(candidate_minus_equal_control_net_USDT=candidate['completed_cycle_net_USDT']-control['completed_cycle_net_USDT'],
                    funding_difference_USDT=candidate['independent_actual_funding_USDT']-control['independent_actual_funding_USDT'],
                    paired_basis_price_difference_USDT=candidate['independent_realized_price_pnl_USDT']-control['independent_realized_price_pnl_USDT'],
                    extra_cost_USDT=candidate['independent_four_fill_costs_USDT']-control['independent_four_fill_costs_USDT'])
    out=dict(results=results,base_candidate_vs_matched_control=comparison,screen_rejected=screen['rejected_at_first_necessary_screen'],
             reflection='Same native pair cancels most direction P&L; observed eligibility/occupation is sparse and full-calendar return is small. Funding receipts are positive but doubled four-fill costs exceed funding plus paired basis profit. Rate rank adds positive descriptive net versus equal control without establishing OOS selection alpha. Post-fill no-leverage caps do not prevent between-week short gross/collateral drift. No parameter rescue/earlier favorable sample, funding mark imputation, lot shortcuts, hidden financing or adoption.',
             screening_only=True,no_new_orders_or_parameters=True,
             reflection_source_sha256=digest(Path(__file__)))
    (HERE/'r17_reflection.json').write_text(json.dumps(out,indent=2)+'\n')


if __name__=='__main__':main()
