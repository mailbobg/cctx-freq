"""Order and settlement attribution of already frozen own-trend outcomes."""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
ASSETS=['BTCUSDT','ETHUSDT']
WINDOWS=[('early_reused','2021-02-01','2023-12-31'),('recent_reused','2024-01-01','2026-08-31')]


def reflect():
    mark={a:pd.read_parquet(HERE/f'r6_data/{a}_mark.parquet') for a in ASSETS}
    funding={a:pd.read_parquet(HERE/f'r6_data/{a}_funding.parquet') for a in ASSETS}
    results={}
    for label,start,end in WINDOWS:
        versions={}
        for name in ('cov10_long','symmetric_own_trend'):
            prefix=f'r15_{label}_{name}_cost0.0012_central'
            daily=pd.read_csv(HERE/f'{prefix}_daily.csv',index_col='date',parse_dates=True)
            hours=pd.read_parquet(HERE/f'{prefix}_hourly.parquet')
            orders=pd.read_csv(HERE/f'{prefix}_orders.csv')
            orders['time']=pd.to_datetime(orders.time,format='mixed',utc=True).dt.as_unit('ns')
            direction={s:{k:0. for k in ['price_pnl','funding','costs']} for s in ['long','short']}
            for row in orders.itertuples(index=False):
                old,new=float(row.old_quantity),float(row.new_quantity)
                if old:direction['long' if old>0 else 'short']['price_pnl']+=row.realized_price_pnl
                if old*new<0:
                    for quantity in (old,new):direction['long' if quantity>0 else 'short']['costs']-=abs(quantity)*row.price*.0012
                else:
                    quantity=new if new else old
                    direction['long' if quantity>0 else 'short']['costs']-=row.cost
            pnl=pd.DataFrame(index=daily.index);asset_funding=pd.DataFrame(index=daily.index)
            for a in ASSETS:
                native=daily[a.removesuffix('USDT')+'_quantity'].shift(1).fillna(0.)
                closes=mark[a].close.resample('D').last()
                closed,prior=closes.reindex(daily.index),closes.shift(1).reindex(daily.index)
                fills=orders.loc[orders.asset.eq(a)].copy();fill_days=fills.time.dt.floor('D')
                adjustment=pd.Series(fills.quantity_delta.to_numpy()*(closed.reindex(fill_days).to_numpy()-fills.price.to_numpy()),index=fill_days).groupby(level=0).sum().reindex(daily.index,fill_value=0.)
                pnl[a]=native*(closed-prior)+adjustment
                events=funding[a].loc[funding[a].slot.ge(pd.Timestamp(start,tz='UTC')) & funding[a].slot.lt(pd.Timestamp(end,tz='UTC')+pd.Timedelta(days=1))].sort_values('slot').copy()
                events['slot']=events.slot.dt.as_unit('ns')
                # At an exact Monday00 timestamp, old quantities settle first.
                history=fills[['time','new_quantity']].sort_values('time')
                joined=pd.merge_asof(events,history,left_on='slot',right_on='time',direction='backward',allow_exact_matches=False)
                quantity=joined.new_quantity.fillna(0.).to_numpy(float)  # known initial cash, no earlier fills
                associated=joined.mark.to_numpy(float)
                settlement=np.where(np.isnan(associated),mark[a].open.reindex(pd.DatetimeIndex(joined.slot)).to_numpy(float),associated)
                payments=-quantity*settlement*joined.rate.to_numpy(float)
                by_day=pd.Series(payments,index=pd.DatetimeIndex(joined.slot).floor('D')).groupby(level=0).sum().reindex(daily.index,fill_value=0.)
                asset_funding[a]=by_day
                for s,mask in [('long',quantity>0),('short',quantity<0)]:direction[s]['funding']+=float(payments[mask].sum())
                if abs(float(pnl[a].sum()-fills.realized_price_pnl.sum()))>1e-7:raise AssertionError('Marked daily price P&L/closed realization disagree')
            funding_gap=float((asset_funding.sum(axis=1)-daily.funding).abs().max())
            if funding_gap>1e-8:raise AssertionError('Reconstructed actual pre-fill funding does not reconcile')
            pnl['funding']=asset_funding.sum(axis=1);pnl['costs']=-daily.cost
            gap=float((pnl.sum(axis=1)-(daily.equity-daily.equity.shift(1).fillna(10000.))).abs().max())
            if gap>1e-7:raise AssertionError('Mark/fill/funding/cost attribution identity failed')
            for value in direction.values():value['net_pnl']=sum(value.values())
            net=sum(v['net_pnl'] for v in direction.values())
            if abs(net-float(daily.equity.iloc[-1]-10000))>1e-7:raise AssertionError('Directional net P&L does not reconcile')
            dd=daily.equity/daily.equity.cummax().clip(lower=10000)-1
            trough=dd.idxmin();past=daily.equity.loc[:trough];peak=past.idxmax() if past.max()>=10000 else None
            episode=pnl.loc[:trough] if peak is None else pnl.loc[(pnl.index>peak)&(pnl.index<=trough)]
            loss=float(daily.equity.loc[trough]-(10000 if peak is None else daily.equity.loc[peak]))
            if abs(float(episode.sum().sum())-loss)>1e-7:raise AssertionError('Loss-period attribution failed')
            exposure={}
            for a in ASSETS:
                quantity=daily[a.removesuffix('USDT')+'_quantity'].reindex(episode.index)
                signed=quantity*mark[a].close.resample('D').last().reindex(episode.index)/daily.equity.reindex(episode.index)
                exposure[a]=dict(days=len(quantity),long_days=int(quantity.gt(0).sum()),short_days=int(quantity.lt(0).sum()),
                    min_signed_close_exposure_pct=float(signed.min()*100),max_signed_close_exposure_pct=float(signed.max()*100))
            versions[name]=dict(daily_peak='initial cash' if peak is None else str(peak),daily_trough=str(trough),
                daily_MTM_DD_pct=float(dd.loc[trough]*100),sampled_hour_account_DD_pct=float(hours.sampled_hour_running_dd_pct.iloc[-1]),
                episode_net_equity_change_USDT_10000_start=loss,episode_asset_PnL_USDT_10000_start=episode.sum().to_dict(),
                whole_window_asset_PnL_USDT_10000_start=pnl.sum().to_dict(),directional_PnL_USDT_10000_start=direction,
                daily_attribution_max_gap_USDT=gap,actual_pre_fill_funding_max_gap_USDT=funding_gap,
                loss_episode_observed_close_exposure=exposure,
                weekly_flip_order_counts=orders.loc[orders.reason.eq('weekly') & (orders.old_quantity*orders.new_quantity<0)].groupby('asset').size().to_dict(),
                max_gross_pct=float(hours.gross.max()*100),observed_margin_breach_hours=int(hours.marked_margin_breach.sum()),envelope_margin_concern_hours=int(hours.envelope_margin_breach.sum()))
        delta={k:versions['symmetric_own_trend']['whole_window_asset_PnL_USDT_10000_start'][k]-versions['cov10_long']['whole_window_asset_PnL_USDT_10000_start'][k] for k in [*ASSETS,'funding','costs']}
        results[label]=dict(versions=versions,symmetric_minus_control_PnL_USDT_10000_start=delta)
    return dict(results=results,scope='Accounting attribution of existing frozen policies only. Directional costs split actual flips across closed/opened quantities. Actual rates and explicit central associated-mark fallback retained; no sign/horizon/risk rescue, cost-free counterfactual or pure-short incremental-alpha claim. Joint sizing changes long positions too.')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--verify-only',action='store_true');args=parser.parse_args()
    result=reflect();path=HERE/'r15_reflection.json'
    if args.verify_only:
        if json.loads(path.read_text())!=result:raise ValueError('Frozen signed-trend reflection did not replay')
        print('Marked daily/fill P&L, pre-Monday funding, directional costs and loss-period account attribution replay PASS')
    else:path.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':main()
