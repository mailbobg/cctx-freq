"""Preregistered symmetric own-trend short branch and matched perpetual cash ledger."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from r1_r2b_risk import RiskHoldR2b, signals, risk_weights
from r6_relative import ASSETS, WINDOWS, PerpLedger, bounded_target, funding_marks, metrics

HERE=Path(__file__).resolve().parent


def raw_signal(close,shorts=True):
    close=close.sort_index().asfreq('D')
    if list(close)!=ASSETS or not close.index.is_unique:raise ValueError('Unexpected price identities/calendar')
    px=close.rename(columns={'BTCUSDT':'BTC_close','ETHUSDT':'ETH_close'})
    raw=signals(px,RiskHoldR2b).set_axis(ASSETS,axis=1)
    if shorts:
        momentum=close/close.shift(30)-1
        vol=np.log(close).diff().rolling(30).std()*np.sqrt(365)
        sma=close.rolling(200).mean()
        scale=pd.DataFrame(np.where(close<sma,1.,.5),index=close.index,columns=ASSETS)
        negative=(momentum<0)&np.isfinite(vol)&np.isfinite(sma)&np.isfinite(close)&(vol>=0)
        amplitude=.5*np.minimum(1.,.60/vol)*scale
        raw=raw.mask(negative,-amplitude)
    return raw


def weights(close,shorts=True):
    close=close.sort_index().asfreq('D')
    raw=raw_signal(close,shorts)
    px=close.rename(columns={'BTCUSDT':'BTC_close','ETHUSDT':'ETH_close'})
    return risk_weights(px,raw.set_axis(['BTC','ETH'],axis=1)).set_axis(ASSETS,axis=1)


def banded_request(quantity,request,target_weights,equity,price,signed=True):
    quantity,request,target_weights,price=map(lambda x:np.asarray(x,float),(quantity,request,target_weights,price))
    active=(np.abs(quantity)>1e-12)&(quantity*target_weights>0) if signed else (quantity>1e-12)&(target_weights>0)
    keep=active&(np.abs((request-quantity)*price)<=.05*.99*equity)
    result=request.copy();result[keep]=quantity[keep]
    return result


def trade_record(book,target,price,cost,time,reason,events):
    previous,entry=book.quantity.copy(),book.entry.copy()
    fee,turnover,realized=book.trade_to(target,price,cost)
    for k,a in enumerate(ASSETS):
        delta=float(book.quantity[k]-previous[k])
        if abs(delta)<=1e-15:continue
        closed=min(abs(delta),abs(previous[k])) if previous[k] and (book.quantity[k]*previous[k]<=0 or abs(book.quantity[k])<abs(previous[k])) else 0.
        events.append(dict(time=time,asset=a,reason=reason,old_quantity=float(previous[k]),new_quantity=float(book.quantity[k]),
            quantity_delta=delta,price=float(price[k]),old_entry=float(entry[k]),new_entry=float(book.entry[k]),
            realized_price_pnl=float(np.sign(previous[k])*closed*(price[k]-entry[k])),cost=float(abs(delta)*price[k]*cost)))
    return fee,turnover,realized


def simulate(trade,mark,fund,target_weights,start,end,cost,signed_band=True,sensitivity='central',terminal=True):
    if not np.isfinite(cost) or cost<0:raise ValueError('Invalid side cost')
    begin=pd.Timestamp(start,tz='UTC');finish=pd.Timestamp(end,tz='UTC')+pd.Timedelta(days=1)
    hours=pd.date_range(begin,finish,freq='1h',inclusive='left').as_unit('ns')
    days=pd.date_range(begin,finish,freq='D',inclusive='left').as_unit('ns')
    if set(trade)!=set(ASSETS) or set(mark)!=set(ASSETS) or set(fund)!=set(ASSETS) or list(target_weights)!=ASSETS:
        raise ValueError('Unexpected contract identity')
    marks=np.stack([mark[a].reindex(hours)[['open','high','low','close']].to_numpy(float) for a in ASSETS],axis=2)
    traded=np.stack([trade[a].reindex(days)[['open','close']].to_numpy(float) for a in ASSETS],axis=2)
    if not np.isfinite(marks).all() or not np.isfinite(traded).all() or min(marks.min(),traded.min())<=0:
        raise ValueError('Unreviewed price input')
    rates,associated=[],[];required=hours[hours.hour%8==0]
    for a in ASSETS:
        events=fund[a].set_index('slot')
        if not events.index.is_unique or not required.isin(events.index).all():raise ValueError('Missing/repeated funding slot')
        if not np.isfinite(events.reindex(required).rate.to_numpy()).all():raise ValueError('Unknown actual funding rate')
        rates.append(events.rate.reindex(hours).to_numpy());associated.append(events.mark.reindex(hours).to_numpy())
    rates,associated=np.stack(rates,axis=1),np.stack(associated,axis=1)
    desired=target_weights.reindex(days-pd.Timedelta(days=1)).to_numpy(float)
    if not np.isfinite(desired).all() or (np.abs(desired).sum(axis=1)>1+1e-12).any():raise ValueError('Unknown/excessive signed target')
    book=PerpLedger(10000.);hour_rows=[];day_rows=[];orders=[]
    day_funding=day_fees=day_turnover=day_realized=0.;sample_peak=10000.;sample_dd=0.
    for i,timestamp in enumerate(hours):
        d=i//24;opened,high,low,closed=marks[i]
        payment=fees=turnover=realized=0.
        before_funding=book.equity(opened)
        if timestamp.hour%8==0:
            settlement=funding_marks(associated[i],book.quantity,rates[i],opened,high,low,sensitivity)
            payment=book.fund(settlement,rates[i])
        opening_equity=book.equity(opened);opening_gross=np.abs(book.quantity)@opened
        opening_breach=opening_equity<=.005*opening_gross
        if timestamp.hour==0 and timestamp.weekday()==0:
            requested=desired[d]*.99*opening_equity/traded[d,0]
            requested=banded_request(book.quantity,requested,desired[d],opening_equity,traded[d,0],signed=signed_band)
            target=bounded_target(book,requested,traded[d,0],opened,cost)
            fees,turnover,realized=trade_record(book,target,traded[d,0],cost,timestamp,'weekly',orders)
            if np.abs(book.quantity)@opened>.99*book.equity(opened)+1e-7:raise AssertionError('Post-fill gross/collateral cap failed')
        after_orders=book.equity(opened)
        adverse_price=np.where(book.quantity>=0,low,high)
        adverse_equity=book.equity(adverse_price);adverse_gross=np.abs(book.quantity)@adverse_price
        equity=book.equity(closed);gross_notional=np.abs(book.quantity)@closed
        envelope_breach=adverse_equity<=.005*adverse_gross;close_breach=equity<=.005*gross_notional
        day_funding+=payment;day_fees+=fees;day_turnover+=turnover;day_realized+=realized
        pre_terminal=equity
        samples=[before_funding,opening_equity,after_orders,equity]
        if terminal and i==len(hours)-1:
            fee,total,pnl=trade_record(book,[0.,0.],traded[d,1],cost,timestamp+pd.Timedelta(hours=1)-pd.Timedelta(milliseconds=1),'terminal_daily_close',orders)
            fees+=fee;turnover+=total;realized+=pnl;day_fees+=fee;day_turnover+=total;day_realized+=pnl
            equity=book.equity(closed);samples.append(equity)
        peaks=np.maximum.accumulate(np.r_[sample_peak,samples])[1:]
        sample_dd=min(sample_dd,float((np.asarray(samples)/peaks-1).min()*100));sample_peak=float(peaks[-1])
        hour_rows.append((timestamp,equity,book.balance,payment,fees,turnover,
            gross_notional/pre_terminal if pre_terminal>0 else np.inf,adverse_equity,adverse_gross,
            bool(opening_breach or close_breach),bool(envelope_breach),before_funding,opening_equity,after_orders,sample_peak,sample_dd))
        if timestamp.hour==23:
            day_rows.append((timestamp.floor('D'),equity,pre_terminal,book.balance,day_funding,day_fees,day_turnover,day_realized,book.quantity[0],book.quantity[1]))
            day_funding=day_fees=day_turnover=day_realized=0.
    daily=pd.DataFrame(day_rows,columns=['date','equity','pre_terminal_equity','balance','funding','cost','turnover','realized_pnl','BTC_quantity','ETH_quantity']).set_index('date')
    hourly=pd.DataFrame(hour_rows,columns=['date','equity','balance','funding','cost','turnover','gross','adverse_equity','adverse_gross',
        'marked_margin_breach','envelope_margin_breach','before_funding_open_equity','before_orders_open_equity','after_orders_open_equity',
        'sampled_hour_peak_equity','sampled_hour_running_dd_pct']).set_index('date')
    order_frame=pd.DataFrame(orders,columns=['time','asset','reason','old_quantity','new_quantity','quantity_delta','price','old_entry','new_entry','realized_price_pnl','cost'])
    if terminal:
        gap=book.balance-(10000+daily.realized_pnl.sum()+daily.funding.sum()-daily.cost.sum())
        if abs(gap)>1e-6 or np.abs(book.quantity).sum():raise AssertionError('Final signed price/funding/fee ledger failed')
    if abs(float(order_frame.cost.sum()-daily.cost.sum()))>1e-8 or abs(float(order_frame.realized_price_pnl.sum()-daily.realized_pnl.sum()))>1e-8:
        raise AssertionError('Order/day realization or fee accounting failed')
    return daily,hourly,order_frame


def prepare():
    path=HERE/'r6_data/manifest.json';manifest=json.loads(path.read_text());inputs={}
    for name,expected in manifest['output_sha256'].items():
        p=HERE/'r6_data'/name;actual=hashlib.sha256(p.read_bytes()).hexdigest()
        if actual!=expected:raise ValueError('Frozen R6 original input changed')
        inputs[str(p.relative_to(HERE))]=actual
    trade={a:pd.read_parquet(HERE/f'r6_data/{a}_trade.parquet') for a in ASSETS}
    mark={a:pd.read_parquet(HERE/f'r6_data/{a}_mark.parquet') for a in ASSETS}
    fund={a:pd.read_parquet(HERE/f'r6_data/{a}_funding.parquet') for a in ASSETS}
    close=pd.DataFrame({a:trade[a].close for a in ASSETS})
    base,candidate=weights(close,shorts=False),weights(close);gaps={}
    for cutoff in ('2021-12-31','2024-12-31'):
        pd.testing.assert_frame_equal(raw_signal(close.loc[:cutoff]),raw_signal(close).loc[:cutoff])
        pd.testing.assert_frame_equal(weights(close.loc[:cutoff]),candidate.loc[:cutoff])
    for label,start,end in WINDOWS:
        for cost in (.0012,.0024):
            daily,hourly,_=simulate(trade,mark,fund,base,start,end,cost,signed_band=False)
            prefix=f'r6_{label}_cov10_long_cost{cost}_central'
            old_daily=pd.read_csv(HERE/f'{prefix}_daily.csv',index_col='date',parse_dates=True)
            old_hour=pd.read_parquet(HERE/f'{prefix}_hourly.parquet')
            for mode,actual,old in [('daily',daily,old_daily),('hourly',hourly,old_hour)]:
                if not actual.index.as_unit('ns').equals(old.index.as_unit('ns')):raise ValueError('Control calendar changed')
                gap=float(np.abs(actual[old.columns].to_numpy(float)-old.to_numpy(float)).max())
                if gap>1e-8:raise ValueError('Frozen R6 control does not match new signed-band ledger')
                gaps[f'{label}_{cost}_{mode}']=gap
    full=simulate(trade,mark,fund,candidate,'2024-01-01','2024-07-05',.0012,terminal=False)
    part=simulate(trade,mark,fund,candidate.loc[:'2024-06-01'],'2024-01-01','2024-06-01',.0012,terminal=False)
    pd.testing.assert_frame_equal(part[0],full[0].loc[part[0].index])
    pd.testing.assert_frame_equal(part[1],full[1].loc[part[1].index])
    pd.testing.assert_frame_equal(part[2],full[2].loc[full[2].time<pd.Timestamp('2024-06-02',tz='UTC')].reset_index(drop=True))
    if not part[0][['BTC_quantity','ETH_quantity']].lt(0).any().any() or not part[0][['BTC_quantity','ETH_quantity']].gt(0).any().any():
        raise AssertionError('Actual prefix must exercise held long and short positions')
    names=['R15_PROTOCOL.md','r15_trend.py','test_r15_trend.py','r6_relative.py','r1_r2b_risk.py','../../strategies/RiskHoldR2b.py','../../strategies/portfolio_risk.py']
    verification=dict(source_inputs_sha256=inputs,source_code_sha256={n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in names},
        source_manifest_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),control_max_abs_gap_USDT=gaps,
        original_positive_raw_and_disabled_weights_equal=True,raw_and_weight_prefix_equal=True,actual_long_short_execution_and_order_prefix_equal=True,
        new_same_sign_short_band=True,sign_flips_and_full_zero_bypass_band=True)
    target=HERE/'r15_input_manifest.json'
    if target.exists() and json.loads(target.read_text())!=verification:raise ValueError('Frozen R15 verification changed')
    target.write_text(json.dumps(verification,indent=2)+'\n')
    return trade,mark,fund,base,candidate,verification


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--verify-only',action='store_true');args=parser.parse_args()
    trade,mark,fund,base,candidate,verification=prepare()
    if args.verify_only:print('All R6 perpetual controls, causal own-trend weights and actual long/short execution/order prefixes PASS');return
    results={};candidate.to_csv(HERE/'r15_weight_audit.csv')
    for label,start,end in WINDOWS:
        for cost in (.0012,.0024):
            for name,target,signed in [('cov10_long',base,False),('symmetric_own_trend',candidate,True)]:
                for sensitivity in (('central','adverse','favorable') if label=='early_reused' else ('central',)):
                    daily,hourly,orders=simulate(trade,mark,fund,target,start,end,cost,signed_band=signed,sensitivity=sensitivity)
                    result=metrics(daily,hourly);result['sampled_hour_account_DD_pct']=float(hourly.sampled_hour_running_dd_pct.iloc[-1])
                    result['screen_gate'] &= result['sampled_hour_account_DD_pct']>=-5
                    key=f'{label}_{name}_cost{cost}_{sensitivity}';results[key]=result
                    daily.to_csv(HERE/f'r15_{key}_daily.csv');hourly.to_parquet(HERE/f'r15_{key}_hourly.parquet');orders.to_csv(HERE/f'r15_{key}_orders.csv',index=False)
                    print(key,{k:round(result[k],4) for k in ['CAGR_pct','daily_MTM_DD_pct','sampled_hour_account_DD_pct','funding_USDT','fees_USDT']},'gate',result['screen_gate'],flush=True)
    (HERE/'r15_results.json').write_text(json.dumps(dict(captured_utc=datetime.now(timezone.utc).isoformat(),input_manifest=verification,results=results,
        limitations='Preregistered symmetric own-trend negative branch, unchanged positive signal and horizons. Actual rates, missing associated settlement marks explicit sensitivities, hourly observed marks and adverse envelope, not exact tiers/ticks/fills. Reused history, no parameter rescue, Freqtrade class, cohort or activation.'),indent=2)+'\n')


if __name__=='__main__':main()
