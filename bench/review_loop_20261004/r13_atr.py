"""Preregistered ATR protection: completed-minute touches sell next OPEN."""
import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import talib

from r1_r2b_risk import ASSETS, RiskHoldR2b, risk_weights, signals, weekly_difference
from r8_crowding import summarize
from r9_selection import load_prices

HERE = Path(__file__).resolve().parent


def atr_values(px):
    if talib.get_unstable_period('ATR') != 0:
        raise ValueError('Unreviewed TA-Lib ATR unstable period')
    result = pd.DataFrame(np.nan,index=px.index,columns=ASSETS)
    for a in ASSETS:
        frame=px[[a+'_high',a+'_low',a+'_close']]
        known=np.isfinite(frame.to_numpy()).all(axis=1)
        if not known.any(): raise ValueError('No actual HLC source for ATR')
        begin=int(np.flatnonzero(known)[0])
        if not known[begin:].all(): raise ValueError('Interior ATR price gap')
        array=frame.iloc[begin:].to_numpy(float)
        result.iloc[begin:,result.columns.get_loc(a)] = talib.ATR(array[:,0],array[:,1],array[:,2],timeperiod=22)
    return result


def next_exit(opened,low,stop):
    """Return first executable index; len(opened) means next-day pending."""
    if not np.isfinite(stop) or stop < 0: raise ValueError('Invalid armed stop')
    gaps=np.flatnonzero(opened<=stop)
    touched=np.flatnonzero(low<=stop)
    choices=([int(gaps[0])] if len(gaps) else [])+([int(touched[0])+1] if len(touched) else [])
    return min(choices) if choices else None


@dataclass
class Account:
    cash: float = 1.
    units: np.ndarray = field(default_factory=lambda:np.zeros(2))
    average: np.ndarray = field(default_factory=lambda:np.zeros(2))
    realized: float = 0.
    total_cost: float = 0.
    events: list = field(default_factory=list)

    def order(self,k,amount,price,cost,time,reason):
        amount=max(amount,-self.units[k]*price)
        if amount>0: amount=min(amount,max(0.,self.cash)/(1+cost))
        quantity=amount/price
        if quantity>0: self.average[k]=(self.average[k]*self.units[k]+amount)/(self.units[k]+quantity)
        elif quantity<0: self.realized += -quantity*(price-self.average[k])
        self.units[k]+=quantity
        if abs(self.units[k])<1e-14:self.units[k],self.average[k]=0.,0.
        fee=abs(amount)*cost
        self.cash-=amount+fee
        if abs(amount)>1e-15:
            self.events.append(dict(time=time,asset=ASSETS[k],reason=reason,price=float(price),native_quantity=float(quantity),dollar_amount=float(amount),cost=float(fee)))
        if self.cash < -1e-10 or self.units.min() < -1e-10: raise AssertionError('Spot borrowing/negative units')
        return fee,abs(amount)


def simulate(px,weights,atr,minutes,start,end,cost,active=True,terminal=True):
    if not px.index.equals(weights.index) or not px.index.equals(atr.index): raise ValueError('Daily calendar mismatch')
    if list(weights)!=ASSETS or list(atr)!=ASSETS or set(minutes)!=set(ASSETS): raise ValueError('Unexpected assets')
    if not np.isfinite(cost) or cost<0: raise ValueError('Invalid side cost')
    begin,finish=pd.Timestamp(start,tz='UTC'),pd.Timestamp(end,tz='UTC')+pd.Timedelta(days=1)
    expected=pd.date_range(begin,finish,freq='min',inclusive='left').as_unit('ns')
    arrays={}
    for a in ASSETS:
        frame=minutes[a].loc[begin:finish-pd.Timedelta(minutes=1)]
        if not frame.index.as_unit('ns').equals(expected): raise ValueError('Unknown/repeated minute calendar')
        values=frame[['open','high','low','close']].to_numpy(float)
        if not np.isfinite(values).all() or (values<=0).any():raise ValueError('Unknown/nonpositive minute price')
        if (values[:,1]<values[:,[0,2,3]].max(axis=1)).any() or (values[:,2]>values[:,[0,1,3]].min(axis=1)).any():raise ValueError('Invalid minute OHLC')
        arrays[a]=values.reshape(-1,1440,4)
    opened=np.stack([arrays[a][:,:,0] for a in ASSETS],axis=2)
    lows=np.stack([arrays[a][:,:,2] for a in ASSETS],axis=2)
    closes=np.stack([arrays[a][:,:,3] for a in ASSETS],axis=2)
    account=Account();stops=np.zeros(2);entry=np.full(2,-1,dtype=int);pending=np.zeros(2,dtype=bool)
    rows=[];minute_peak=1.;minute_dd=0.
    day_positions=px.index.get_indexer(pd.date_range(begin,finish,freq='D',inclusive='left'))
    if (day_positions<200).any():raise ValueError('Incomplete daily warmup/calendar')
    highs=px[[a+'_high' for a in ASSETS]].to_numpy(float)
    for j,i in enumerate(day_positions):
        day=px.index[i];op,lo,cl=opened[j],lows[j],closes[j]
        before_open=account.cash+account.units@op[0]
        fees,turnover=0.,0.;asset_turnover=np.zeros(2);blocked=np.zeros(2,dtype=bool)
        if active:
            prior_atr=atr.iloc[i-1].to_numpy(float)
            if not np.isfinite(prior_atr).all() or (prior_atr<0).any():raise ValueError('Unknown prior ATR')
            for k in range(2):
                if account.units[k]>0:
                    if entry[k]<0:raise AssertionError('Missing held-position anchor')
                    previous_high=highs[max(entry[k],i-22):i,k]
                    if len(previous_high) and not np.isfinite(previous_high).all():raise ValueError('Unknown held-position HIGH')
                    if len(previous_high):stops[k]=max(stops[k],float(previous_high.max())-3*prior_atr[k],0.)
                    if pending[k] or op[0,k]<=stops[k]:
                        fee,sold=account.order(k,-account.units[k]*op[0,k],op[0,k],cost,day,'atr_pending' if pending[k] else 'atr_gap')
                        fees+=fee;turnover+=sold;asset_turnover[k]+=sold;blocked[k]=True
                        stops[k],entry[k],pending[k]=0.,-1,False
        if day.weekday()==0:
            target=weights.iloc[i-1].to_numpy(float)
            if not np.isfinite(target).all() or (target<0).any() or target.sum()>1+1e-12:raise ValueError('Invalid target')
            value=account.cash+account.units@op[0]
            diff=weekly_difference(account.units*op[0],target,value)
            diff[blocked]=0.
            for k in np.argsort(diff):
                was_flat=account.units[k]==0
                fee,dollars=account.order(k,diff[k],op[0,k],cost,day,'weekly')
                fees+=fee;turnover+=dollars;asset_turnover[k]+=dollars
                if active:
                    if account.units[k]==0:stops[k],entry[k],pending[k]=0.,-1,False
                    elif was_flat:stops[k],entry[k],pending[k]=max(0.,op[0,k]-3*prior_atr[k]),i,False
        # No intraday buys: each held asset may have one complete stop sale.
        path_units=np.broadcast_to(account.units,(1440,2)).copy()
        path_cash=np.full(1440,account.cash);minute_fees=np.zeros(1440);minute_fees[0]=fees
        executions=[]
        if active:
            for k in range(2):
                pending[k]=False
                if account.units[k]>0:
                    at=next_exit(op[:,k],lo[:,k],stops[k])
                    if at==1440:pending[k]=True
                    elif at is not None:executions.append((at,k))
            for at,k in sorted(executions):
                old_cash=account.cash
                fee,sold=account.order(k,-account.units[k]*op[at,k],op[at,k],cost,day+pd.Timedelta(minutes=at),'atr_minute')
                path_cash[at:]+=account.cash-old_cash;path_units[at:,k]=0.
                minute_fees[at]+=fee;fees+=fee;turnover+=sold;asset_turnover[k]+=sold
                stops[k],entry[k],pending[k]=0.,-1,False
        after_open=path_cash+(path_units*op).sum(axis=1)
        before_orders=after_open+minute_fees;before_orders[0]=before_open
        minute_close=path_cash+(path_units*cl).sum(axis=1)
        samples=np.column_stack([before_orders,after_open,minute_close]).ravel()
        pre_units,pre_cash=account.units.copy(),account.cash
        closed=cl[-1];pre_equity=account.cash+account.units@closed
        pre_exposure=(account.units@closed)/pre_equity
        if terminal and str(day.date())==end:
            for k in range(2):
                fee,sold=account.order(k,-account.units[k]*closed[k],closed[k],cost,day+pd.Timedelta(hours=23,minutes=59),'terminal_daily_close')
                fees+=fee;turnover+=sold;asset_turnover[k]+=sold
        account.total_cost+=fees
        equity=account.cash+account.units@closed
        unrealized=account.units@(closed-account.average)
        gap=equity-(1+account.realized+unrealized-account.total_cost)
        if abs(gap)>1e-10:raise AssertionError('Price P&L/cost accounting identity failed')
        samples=np.r_[samples,equity]
        running_peaks=np.maximum.accumulate(np.r_[minute_peak,samples])[1:]
        minute_dd=min(minute_dd,float((samples/running_peaks-1).min()*100));minute_peak=float(running_peaks[-1])
        row=dict(date=day,equity=equity,cash=account.cash,exposure=(account.units@closed)/equity,cost=fees,turnover=turnover,
            pre_close_equity=pre_equity,pre_close_cash=pre_cash,pre_close_exposure=pre_exposure,pre_trade_open_equity=before_open,
            realized_price_pnl=account.realized,unrealized_price_pnl=unrealized,cumulative_cost=account.total_cost,accounting_gap=gap,
            sampled_minute_peak_equity=minute_peak,sampled_minute_running_dd_pct=minute_dd,sampled_minute_day_min_equity=float(samples.min()),
            sampled_minute_day_max_equity=float(samples.max()))
        row.update({'units_'+a:account.units[k] for k,a in enumerate(ASSETS)})
        row.update({'pre_close_units_'+a:pre_units[k] for k,a in enumerate(ASSETS)})
        row.update({'turnover_'+a:asset_turnover[k] for k,a in enumerate(ASSETS)})
        row.update({'armed_stop_'+a:stops[k] for k,a in enumerate(ASSETS)})
        row.update({'pending_'+a:bool(pending[k]) for k,a in enumerate(ASSETS)})
        rows.append(row)
    curve=pd.DataFrame(rows).set_index('date')
    events=pd.DataFrame(account.events,columns=['time','asset','reason','price','native_quantity','dollar_amount','cost'])
    return curve,events


def prepare():
    px,spot,hashes=load_prices()
    for a in ASSETS:
        px[a+'_high'],px[a+'_low']=spot[a].high,spot[a].low
    weights=risk_weights(px,signals(px,RiskHoldR2b));atr=atr_values(px)
    manifest=json.loads((HERE/'r13_data/manifest.json').read_text());minutes={}
    for a in ASSETS:
        path=HERE/f'r13_data/{a}USDT_1m.parquet';sha=hashlib.sha256(path.read_bytes()).hexdigest()
        if sha!=manifest['output_sha256'][path.name]:raise ValueError('Frozen minute source changed')
        hashes[str(path.relative_to(HERE))]=sha;minutes[a]=pd.read_parquet(path)
    gaps={}
    for cost in (.0012,.0024):
        baseline,_=simulate(px,weights,atr,minutes,'2024-01-01','2026-08-31',cost,active=False)
        old=pd.read_csv(HERE/f'r10_recent_reused_Cov10_baseline_cost{cost}_daily.csv',index_col='date',parse_dates=True)
        cols=[x for x in baseline if not x.startswith(('sampled_minute_','armed_stop_','pending_'))]
        if not baseline.index.as_unit('ns').equals(old.index.as_unit('ns')):raise ValueError('Control calendar changed')
        gap=float(np.max(np.abs(baseline[cols].to_numpy()-old[cols].to_numpy())))
        if gap>1e-12:raise ValueError('Minute ledger changed the disabled daily control')
        gaps[str(cost)]=gap
    for cutoff in ('2024-05-31','2025-12-31'):
        pd.testing.assert_frame_equal(atr_values(px.loc[:cutoff]),atr.loc[:cutoff])
        pd.testing.assert_frame_equal(risk_weights(px.loc[:cutoff],signals(px.loc[:cutoff],RiskHoldR2b)),weights.loc[:cutoff])
    full,full_events=simulate(px,weights,atr,minutes,'2024-01-01','2024-07-05',.0012,terminal=False)
    cut=px.loc[:'2024-06-01']
    part,part_events=simulate(cut,weights.loc[cut.index],atr.loc[cut.index],{a:f.loc[:'2024-06-01'] for a,f in minutes.items()},'2024-01-01','2024-06-01',.0012,terminal=False)
    pd.testing.assert_frame_equal(part,full.loc[part.index])
    pd.testing.assert_frame_equal(part_events,full_events.loc[full_events.time<pd.Timestamp('2024-06-02',tz='UTC')].reset_index(drop=True))
    assert part.units_BTC.gt(0).any() and part.units_ETH.gt(0).any()
    names=['R13_PROTOCOL.md','r13_atr.py','test_r13_atr.py','r13_data.py','test_r13_data.py','r1_r2b_risk.py','r9_selection.py']
    verification=dict(source_inputs_sha256=hashes,source_code_sha256={x:hashlib.sha256((HERE/x).read_bytes()).hexdigest() for x in names},
        minute_source_manifest_sha256=hashlib.sha256((HERE/'r13_data/manifest.json').read_bytes()).hexdigest(),
        disabled_control_max_abs_gap=gaps,ATR_unstable_period=0,ATR_full_prefix_equal=True,real_held_execution_and_order_prefix_equal=True)
    path=HERE/'r13_input_manifest.json'
    if path.exists() and json.loads(path.read_text())!=verification:raise ValueError('Frozen ATR engine/input verification changed')
    path.write_text(json.dumps(verification,indent=2)+'\n')
    return px,weights,atr,minutes,verification


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--verify-only',action='store_true');args=parser.parse_args()
    px,weights,atr,minutes,verification=prepare()
    if args.verify_only:print('Matched daily controls, causal Wilder ATR, actual held execution/order prefixes PASS');return
    results={}
    for cost in (.0012,.0024):
        for name,active in [('Cov10_baseline',False),('ATR22x3',True)]:
            curve,events=simulate(px,weights,atr,minutes,'2024-01-01','2026-08-31',cost,active=active)
            result=summarize(curve)
            result.update(sampled_minute_account_DD_pct=float(curve.sampled_minute_running_dd_pct.iloc[-1]),
                max_accounting_gap=float(curve.accounting_gap.abs().max()),
                protective_full_exit_counts=events.loc[events.reason.str.startswith('atr')].groupby('asset').size().to_dict(),
                necessary_screen_gate=bool(result['necessary_screen_gate'] and curve.sampled_minute_running_dd_pct.iloc[-1]>=-5),
                risk_limitation='Synchronized minute candle OPEN before/after orders and CLOSE prices; not simultaneous executable quotes, tick prices, stop-price fills or exact intraminute risk. Touches trade following OPEN. No ATR/timing/entry/risk tuning.')
            key=f'recent_reused_{name}_cost{cost}';results[key]=result
            curve.to_csv(HERE/f'r13_{key}_daily.csv');events.to_csv(HERE/f'r13_{key}_orders.csv',index=False)
            print(key,{k:round(result[k],4) for k in ['CAGR_pct','daily_MTM_DD_pct','sampled_minute_account_DD_pct','fees_USDT_10000_start']},'gate',result['necessary_screen_gate'],flush=True)
    (HERE/'r13_results.json').write_text(json.dumps(dict(captured_utc=datetime.now(timezone.utc).isoformat(),input_manifest=verification,results=results,
        scope='Frozen first recent-window necessary screen; prior windows only if it passes all gates. No Freqtrade class, bot, alpha certification or activation from a source/test pass.'),indent=2)+'\n')


if __name__=='__main__':main()
