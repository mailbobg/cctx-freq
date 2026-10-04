"""Minute account risk from frozen actual Freqtrade fills, not target weights."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

import numpy as np
import pandas as pd

from r35_native import DATA, HERE, assert_inputs, digest, write_json

ASSETS=('BTC','ETH')


def exported_orders(stat, expected_fee):
    orders=[]; fingerprints=set()
    if float(stat['starting_balance']) != 10000:
        raise ValueError('Original initial account balance differs')
    for ti,t in enumerate(stat['trades']):
        if t['is_short'] or float(t['leverage'])!=1 or float(t['funding_fees'])!=0 or t['is_open']:
            raise ValueError('Requires closed unleveraged long spot trades without funding')
        if t['pair'] not in ('BTC/USDT','ETH/USDT'):
            raise ValueError('Unknown original portfolio asset')
        if abs(float(t['fee_open'])-expected_fee)>1e-15 or abs(float(t['fee_close'])-expected_fee)>1e-15:
            raise ValueError('Original side fee differs')
        if not t['orders']:raise ValueError('Trade without exported fills')
        for oi,o in enumerate(t['orders']):
            at=pd.Timestamp(o['order_filled_timestamp'],unit='ms',tz='UTC')
            side=o['ft_order_side'];q=float(o['amount']);price=float(o['safe_price'])
            if side not in ('buy','sell') or not np.isfinite([q,price]).all() or min(q,price)<=0 or at!=at.floor('D'):
                raise ValueError('Unusable native fill side/quantity/price/timestamp')
            if bool(o['ft_is_entry']) != (side=='buy'):
                raise ValueError('Original entry/side identity differs')
            fingerprint=(at,t['pair'],side,q,price,str(o.get('ft_order_tag')))
            if fingerprint in fingerprints:raise ValueError('Duplicate exported fill fingerprint')
            fingerprints.add(fingerprint)
            fee=float(t['fee_open'] if side=='buy' else t['fee_close'])
            notional=q*price
            orders.append(dict(timestamp=at,asset=t['pair'].split('/')[0],side=side,
                quantity=q,price=price,fee_rate=fee,fee_USDT=notional*fee,
                cash_delta=(-notional*(1+fee) if side=='buy' else notional*(1-fee)),
                quantity_delta=(q if side=='buy' else -q),
                original_trade=ti,original_order=oi,exported_cost_ignored=float(o['cost']),
                tag=o.get('ft_order_tag'),exit_reason=t['exit_reason']))
    frame=pd.DataFrame(orders)
    if frame.empty:raise ValueError('No exported native fills')
    frame['side_rank']=frame.side.map({'sell':0,'buy':1})
    return frame.sort_values(['timestamp','side_rank','original_trade','original_order'],kind='stable').reset_index(drop=True)


def event_description(path_pos, minute_positions, kinds, events, index, equity):
    minute=int(minute_positions[path_pos]);kind=int(kinds[path_pos])
    if kind>=2:
        item=events[kind-2]
        phase=item['phase'];identity=item.get('order_identity')
    else:phase=('post_fill_OPEN','CLOSE')[kind];identity=None
    return dict(minute_utc=str(index[minute]),phase=phase,equity_USDT=float(equity[path_pos]),
                order_identity=identity)


def risk_pair(upper, lower, initial):
    """Includes own-minute upper for a bound, never calls it a measured path."""
    peak=np.maximum(initial,np.maximum.accumulate(upper))
    dd=lower/peak-1
    trough=int(np.argmin(dd))
    eligible=np.flatnonzero(upper[:trough+1]==peak[trough])
    top=int(eligible[0]) if len(eligible) else None
    return dd,top,trough


def marked_account(index, prices, orders, starting_balance=10000):
    """Missing held prices remain NaN; only observed events receive risk values."""
    if index.has_duplicates or not index.is_monotonic_increasing or index.tz is None:
        raise ValueError('Account calendar must be ordered unique UTC minutes')
    n=len(index)
    if n==0:raise ValueError('Empty account calendar')
    aligned={a:prices[a].reindex(index) for a in ASSETS}
    px={field:np.column_stack([aligned[a][field].to_numpy() for a in ASSETS])
        for field in ('open','close','high','low')}
    dcash=np.zeros(n);dunits=np.zeros((n,2))
    grouped={};fill_failures=[]
    for oi,o in orders.iterrows():
        if o['asset'] not in ASSETS or o['side'] not in ('buy','sell'):
            raise ValueError('Unsupported native order')
        p=index.get_indexer([o['timestamp']])[0]
        if p<0:raise ValueError('Fill outside frozen full account calendar')
        a=ASSETS.index(o['asset'])
        native_open=px['open'][p,a]
        if not np.isfinite(native_open) or abs(float(native_open)-o['price'])>1e-8:
            fill_failures.append(dict(order_index=int(oi),date=str(o['timestamp']),asset=o['asset'],
                modeled_fill=float(o['price']),native_open=float(native_open) if np.isfinite(native_open) else None))
        dcash[p]+=o['cash_delta'];dunits[p,a]+=o['quantity_delta']
        grouped.setdefault(p,[]).append(oi)
    cash=starting_balance+np.cumsum(dcash);units=np.cumsum(dunits,axis=0)
    if cash.min()<-1e-6 or units.min()<-1e-10:raise ValueError('Borrowed cash or negative spot inventory')
    units[np.abs(units)<1e-10]=0
    active=units>0
    def value(c,q,p):
        return c+(np.where(q>0,q*p,0)).sum(axis=-1)
    mark={f:value(cash,units,p) for f,p in px.items()}
    held_missing=(active & (~np.isfinite(px['open']) | ~np.isfinite(px['close']) | ~np.isfinite(px['high']) | ~np.isfinite(px['low'])))
    held_zero=np.column_stack([active[:,ai] & aligned[a].trades.eq(0).to_numpy()
                              for ai,a in enumerate(ASSETS)])
    extra_pos=[];extra_eq=[];events=[];before_missing=0;min_inter_cash=float(cash.min())
    for p,ois in sorted(grouped.items()):
        c=cash[p]-dcash[p];q=units[p]-dunits[p]
        q[np.abs(q)<1e-10]=0
        eq=value(c,q,px['open'][p])
        before_missing+=int(not np.isfinite(eq))
        extra_pos.append(2*p);extra_eq.append(eq)
        events.append(dict(phase='pre_fill_OPEN',order_identity=None))
        for oi in ois:
            o=orders.loc[oi];a=ASSETS.index(o['asset'])
            c+=o['cash_delta'];q[a]+=o['quantity_delta']
            q[np.abs(q)<1e-10]=0
            min_inter_cash=min(min_inter_cash,float(c))
            if c<-1e-6 or q.min()<-1e-10:raise ValueError('Intermediate fill borrows cash or inventory')
            eq=value(c,q,px['open'][p]);before_missing+=int(not np.isfinite(eq))
            extra_pos.append(2*p);extra_eq.append(eq)
            events.append(dict(phase='after_individual_fill',order_identity=int(oi)))
    # np.insert preserves repeated insertion positions' given event order.
    base=np.column_stack([mark['open'],mark['close']]).ravel()
    sampled=np.insert(base,extra_pos,extra_eq)
    minutes=np.insert(np.repeat(np.arange(n),2),extra_pos,[p//2 for p in extra_pos])
    kinds=np.insert(np.tile([0,1],n),extra_pos,np.arange(2,len(events)+2))
    known=np.isfinite(sampled);observed=sampled[known]
    observed_minutes=minutes[known];observed_kinds=kinds[known]
    if not len(observed) or (observed<=0).any():raise ValueError('No positive known account marks')
    draw,top,trough=risk_pair(observed,observed,starting_balance)
    def describe(pos):return event_description(pos,observed_minutes,observed_kinds,events,index,observed)
    peak_desc=describe(top) if top is not None else dict(minute_utc=str(index[0]),phase='initial_capital',equity_USDT=starting_balance,order_identity=None)
    breaches={}
    for threshold in (.05,.15):
        hit=np.flatnonzero(draw < -threshold)
        breaches[str(threshold)]=dict(**describe(int(hit[0])),drawdown_pct=float(draw[hit[0]]*100)) if len(hit) else None
    high=mark['high'].copy();low=mark['low'].copy()
    for pos,eq in zip(extra_pos,extra_eq):
        p=pos//2
        if np.isfinite(eq) and np.isfinite(high[p]) and np.isfinite(low[p]):
            high[p]=max(high[p],eq);low[p]=min(low[p],eq)
    valid_envelope=np.isfinite(high)&np.isfinite(low)
    env_idx=np.flatnonzero(valid_envelope)
    env_draw,env_top,env_bottom=risk_pair(high[valid_envelope],low[valid_envelope],starting_balance)
    env_b=int(env_idx[env_bottom]);env_t=int(env_idx[env_top]) if env_top is not None else None
    result=dict(sampled_OPEN_CLOSE_and_fill_DD_pct=float(draw[trough]*100),
        sampled_peak=peak_desc,sampled_trough=describe(trough),first_sampled_breaches=breaches,
        conservative_OHLC_envelope_DD_pct=float(env_draw[env_bottom]*100),
        envelope_peak=dict(minute_utc=str(index[env_t]) if env_t is not None else str(index[0]),
                           equity_upper_bound_USDT=float(high[env_t]) if env_t is not None else starting_balance),
        envelope_trough=dict(minute_utc=str(index[env_b]),equity_lower_bound_USDT=float(low[env_b])),
        envelope_is_bound_not_synchronous_price_or_high_before_low_observation=True,
        required_account_minutes=n,known_sampled_marks=len(observed),missing_sampled_marks=int((~known).sum()),
        extra_fill_boundary_marks=len(events),held_missing_asset_minutes={a:int(held_missing[:,i].sum()) for i,a in enumerate(ASSETS)},
        held_zero_activity_asset_minutes={a:int(held_zero[:,i].sum()) for i,a in enumerate(ASSETS)},
        missing_fill_boundary_marks=before_missing,original_fill_OPEN_parity_failures=fill_failures,
        minimum_cash_USDT=min_inter_cash,minimum_asset_units={a:float(units[:,i].min()) for i,a in enumerate(ASSETS)},
        terminal_units={a:float(units[-1,i]) for i,a in enumerate(ASSETS)},terminal_cash_USDT=float(cash[-1]),
        total_explicit_fees_USDT=float(orders.fee_USDT.sum()),
        complete_held_intraday_price_and_fill_coverage=bool(not held_missing.any() and not before_missing and not fill_failures),
        continuous_liquidity_or_tick_risk_or_future_profit_certified=False)
    daily=pd.DataFrame(dict(cash=cash,equity=mark['close'],BTC_units=units[:,0],ETH_units=units[:,1]),index=index).resample('D').last()
    # .last() alone could conceal a missing final held mark; select actual 23:59.
    exact_close=index[(index.hour==23)&(index.minute==59)]
    if len(exact_close):daily.loc[exact_close.floor('D'),'equity']=mark['close'][index.get_indexer(exact_close)]
    return result,daily


def run(verify=False):
    plan=assert_inputs();source=json.loads((HERE/'r35_source_audit.json').read_text())
    for name,sha in source['native_input_sha256'].items():
        if digest(HERE/name)!=sha:raise ValueError('Native input changed')
    for name,sha in source['output_sha256'].items():
        if digest(HERE/name)!=sha:raise ValueError('Native derived minutes changed')
    if len(source['output_sha256'])!=2:raise ValueError('Incomplete native archive set; no full account audit')
    prices={a:pd.read_parquet(DATA/f'{a}USDT_1m.parquet') for a in ASSETS}
    r1=json.loads((HERE/'r1_freq_results.json').read_text());results={};outputs={}
    for label,cost in (('early',.0012),('recent',.0012),('early_cost2',.0024),('recent_cost2',.0024)):
        p=Path(r1[label+'_RiskHoldR2bCov10']['source'])
        with zipfile.ZipFile(p) as z:
            names=[n for n in z.namelist() if n.endswith('.json') and not n.endswith('_config.json')]
            if len(names)!=1:raise ValueError('Ambiguous native backtest statistics member')
            stats=json.loads(z.read(names[0]))['strategy']
        if set(stats)!= {'RiskHoldR2b','RiskHoldR2bCov10'}:raise ValueError('Original strategy cohort differs')
        for name in ('RiskHoldR2b','RiskHoldR2bCov10'):
            s=stats[name];key=label+'_'+name
            start=pd.Timestamp(s['backtest_start'],tz='UTC');end=pd.Timestamp(s['backtest_end'],tz='UTC')
            index=pd.date_range(start,end+pd.Timedelta(days=1),freq='min',inclusive='left').as_unit('ns')
            window={a:f.loc[start:end+pd.Timedelta(days=1)-pd.Timedelta(minutes=1)] for a,f in prices.items()}
            orders=exported_orders(s,cost)
            risk,daily=marked_account(index,window,orders,float(s['starting_balance']))
            original=pd.read_csv(HERE/f'r1_freq_{key}.csv',index_col=0)
            original.index=pd.to_datetime(original.index,utc=True).as_unit('ns')
            pd.testing.assert_index_equal(original.index,daily.index,check_names=False)
            gap=(daily[['cash','equity']]-original[['cash','equity']]).abs()
            if gap.isna().any().any():daily_parity=False;max_gap=None
            else:max_gap=float(gap.max().max());daily_parity=max_gap<=.01
            final_gap=float(risk['terminal_cash_USDT']-float(s['final_balance']))
            profit_gap=float(risk['terminal_cash_USDT']-float(s['starting_balance'])-sum(t['profit_abs'] for t in s['trades']))
            if abs(final_gap)>.01 or abs(profit_gap)>.01 or any(abs(x)>1e-10 for x in risk['terminal_units'].values()):
                raise ValueError('Terminal native account does not reconcile')
            risk.update(original_CAGR_pct=r1[key]['CAGR_pct'],original_return_pct=r1[key]['return_pct'],
                original_daily_MTM_DD_pct=r1[key]['maxDD_pct'],original_years_pct=r1[key]['years_pct'],
                start=str(start),end=str(end),cost_per_side=cost,orders=len(orders),trades=len(s['trades']),
                daily_cash_equity_parity=daily_parity,max_daily_cash_equity_gap_USDT=max_gap,
                final_balance_gap_USDT=final_gap,sum_profit_gap_USDT=profit_gap,
                frozen_5pct_default_pass=False,adopted=False,unchanged_strategy=True)
            risk['complete_held_intraday_price_and_fill_coverage'] &= daily_parity
            results[key]=risk
            csv=HERE/f'r35_{key}_daily.csv';ocsv=HERE/f'r35_{key}_orders.csv'
            text=daily.to_csv();otext=orders.drop(columns='side_rank').to_csv(index=False)
            if verify:
                if csv.read_text()!=text or ocsv.read_text()!=otext:raise ValueError('Native account output bytes changed')
            else:csv.write_text(text);ocsv.write_text(otext)
            outputs[csv.name]=digest(csv);outputs[ocsv.name]=digest(ocsv)
            print(key,{k:risk[k] for k in ('sampled_OPEN_CLOSE_and_fill_DD_pct','conservative_OHLC_envelope_DD_pct','complete_held_intraday_price_and_fill_coverage','max_daily_cash_equity_gap_USDT')},flush=True)
    out=dict(protocol_commit=plan['protocol_commit'],source_plan_sha256=digest(HERE/'r35_source_plan.json'),
        native_source_audit_sha256=digest(HERE/'r35_source_audit.json'),results=results,
        original_defaults_not_user_confirmed=True,previous_gates_and_strategy_rules_unchanged=True,
        no_new_predictor_sweep_or_independent_forward_cohort=True,adopted=False,output_sha256=outputs)
    dest=HERE/'r35_account_risk.json';text=json.dumps(out,indent=2,sort_keys=True,allow_nan=False)+'\n'
    if verify:
        if dest.read_text()!=text:raise ValueError('Account risk audit is not byte-identical')
    else:dest.write_text(text)
    return out


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--verify',action='store_true');args=p.parse_args();run(args.verify)
