"""R17 hourly paired execution ledger; no return/risk aggregation or tuning."""
import numpy as np
import pandas as pd

from r16_knowledge import utc
from r17_carry import ASSETS,FundingSeries,PairedLedger
from r6_relative import bounded_target


def simulate(spot,trade,mark,fund,decisions,start,end,cost,policy='ranked',terminal=True):
    if policy not in ('ranked','equal_pairs'):raise ValueError('Unregistered R17 policy')
    if not np.isfinite(cost) or cost<0:raise ValueError('Finite nonnegative cost required')
    begin,finish=utc(start),utc(end)
    if begin>=finish or begin!=begin.floor('D') or finish!=finish.floor('D'):
        raise ValueError('Whole-day exclusive simulation calendar required')
    hours=pd.date_range(begin,finish,freq='h',inclusive='left').as_unit('ns')
    required_decisions=hours[(hours.weekday==0)&(hours.hour==1)]
    decision_times=pd.DatetimeIndex([utc(d['decision']) for d in decisions]).as_unit('ns')
    if not decision_times.equals(required_decisions):raise ValueError('Complete unique ordered Monday01 decision calendar required')
    choices={}
    for record,t in zip(decisions,decision_times):
        selected=tuple(record['selected'])
        if len(selected)>2 or len(set(selected))!=len(selected) or any(s not in ASSETS for s in selected):
            raise ValueError('Unregistered decision inventory')
        if 'slot_count' in record and record['slot_count']!=len(selected):raise ValueError('Declared decision slot count differs')
        choices[t]=selected

    n=len(ASSETS);required=np.zeros(len(hours),bool)
    for t,selected in choices.items():
        if selected:required |= (hours>=t)&(hours<=t+pd.Timedelta(days=7))
    prices={}
    for label,source in [('spot',spot),('trade',trade),('mark',mark)]:
        rows=[]
        if set(source)!=set(ASSETS):raise ValueError('Registered price instruments differ')
        for symbol in ASSETS:
            frame=source[symbol]
            if not isinstance(frame.index,pd.DatetimeIndex) or frame.index.tz is None or not frame.index.is_unique or not frame.index.is_monotonic_increasing:
                raise ValueError('Unique ordered timezone-aware price calendar required')
            values=frame.reindex(hours)[['open','high','low','close']].to_numpy(float)
            used=values[required]
            if not np.isfinite(used).all() or (used<=0).any() or (used[:,1]<used[:,[0,2,3]].max(axis=1)).any() or (used[:,2]>used[:,[0,1,3]].min(axis=1)).any():
                raise ValueError('Unknown or invalid required holding/order price')
            rows.append(values)
        prices[label]=np.stack(rows,axis=2)
    rates=np.full((len(hours),n),np.nan);associated=np.full_like(rates,np.nan);event_mask=np.zeros_like(rates,bool)
    for j,symbol in enumerate(ASSETS):
        series=fund[symbol]
        if not isinstance(series,FundingSeries) or series.coverage_start>begin or series.coverage_end<finish:
            raise ValueError('Validated full funding calendar required')
        # Original actual event timestamps remain in source; only <=1s
        # documented hour offsets normalize to the prescribed slot ordering.
        events=series.events.set_index('slot').reindex(hours)
        mask=events.interval_hours.notna().to_numpy()
        rates[mask,j]=[float(r) for r in events.loc[mask,'rate_decimal']]
        associated[mask,j]=pd.to_numeric(events.loc[mask,'mark'],errors='raise').to_numpy(float)
        event_mask[:,j]=mask

    book=PairedLedger(10000.,n);hour_rows=[];orders=[]
    def inventory_orders(t,action,q,s,f):
        for j,symbol in enumerate(ASSETS):
            if not q[j]:continue
            spot_side='buy' if action=='open' else 'sell'
            future_side='sell' if action=='open' else 'buy'
            for leg,side,price in [('spot',spot_side,s[j]),('future',future_side,f[j])]:
                orders.append(dict(date=t,symbol=symbol,action=action,leg=leg,side=side,quantity=q[j],
                                   price=price,notional=q[j]*price,cost=q[j]*price*cost))

    def concern(m):return book.margin_concern(m)

    for i,t in enumerate(hours):
        held=book.spot_quantity.any()
        if held!=bool(book.future.quantity.any()) or not np.array_equal(book.spot_quantity,-book.future.quantity):
            raise AssertionError('Native paired inventories differ')
        selected=choices.get(t,())
        priced=bool(held or selected)
        cash=book.spot_cash+book.future.balance
        fees0,pnl0=book.fees,book.price_pnl
        if priced:
            so,sh,sl,sc=prices['spot'][i];fo,fh,fl,fc=prices['trade'][i];mo,mh,ml,mc=prices['mark'][i]
            before=book.equity(so,mo);before_futures=book.future.equity(mo);before_concern=concern(mo)
        else:
            before=cash;before_futures=book.future.balance;before_concern=False
        payment=book.fund(associated[i],rates[i],event_mask[i])
        after_fund=book.equity(so,mo) if priced else book.spot_cash+book.future.balance
        after_fund_futures=book.future.equity(mo) if priced else book.future.balance
        after_fund_concern=concern(mo) if priced else False
        if t in choices:
            if held:
                old=book.spot_quantity.copy();inventory_orders(t,'close',old,so,fo)
                book.close(so,fo,cost)
            book.repool()
            if selected:
                k=len(selected)
                fractions=np.array([.5 if s in selected else 0. for s in ASSETS]) if policy=='ranked' else np.full(n,k/(2*n))
                quantity=.99*np.minimum(book.spot_cash*fractions/(so*(1+cost)),book.future.balance*fractions/(fo*(1+cost)))
                quantity=-bounded_target(book.future,-quantity,fo,mo,cost)
                book.open(quantity,so,fo,mo,cost)
                inventory_orders(t,'open',quantity,so,fo)
        after_orders=book.equity(so,mo) if priced else book.spot_cash+book.future.balance
        after_orders_futures=book.future.equity(mo) if priced else book.future.balance
        after_order_concern=concern(mo) if priced else False
        gross=book.spot_quantity@(so+mo) if priced else 0.
        futures_gross=np.abs(book.future.quantity)@mo if priced else 0.
        before_terminal=book.equity(sc,mc) if priced else book.spot_cash+book.future.balance
        closing_futures=book.future.equity(mc) if priced else book.future.balance
        close_concern=concern(mc) if priced else False
        adverse_futures=book.future.equity(mh) if priced else book.future.balance
        envelope_concern=concern(mh) if priced else False
        if terminal and i==len(hours)-1 and book.spot_quantity.any():
            old=book.spot_quantity.copy();inventory_orders(t,'terminal',old,sc,fc)
            book.close(sc,fc,cost)
        equity=book.equity(sc,mc) if priced else book.spot_cash+book.future.balance
        row=dict(date=t,before_funding_equity=before,after_funding_equity=after_fund,
                 after_orders_equity=after_orders,pre_terminal_equity=before_terminal,equity=equity,
                 before_funding_futures_equity=before_futures,after_funding_futures_equity=after_fund_futures,
                 after_orders_futures_equity=after_orders_futures,closing_futures_equity=closing_futures,
                 adverse_futures_equity=adverse_futures,spot_cash=book.spot_cash,futures_balance=book.future.balance,
                 funding=payment,cost=book.fees-fees0,realized_price_pnl=book.price_pnl-pnl0,
                 gross_notional=gross,futures_gross=futures_gross,active=bool(gross),
                 marked_margin_concern=bool(before_concern or after_fund_concern or after_order_concern or close_concern),
                 envelope_margin_concern=envelope_concern)
        for j,s in enumerate(ASSETS):
            row[s+'_spot_quantity']=book.spot_quantity[j];row[s+'_future_quantity']=book.future.quantity[j]
        hour_rows.append(row)
    hourly=pd.DataFrame(hour_rows).set_index('date')
    daily=hourly[['equity','pre_terminal_equity','spot_cash','futures_balance']].resample('D').last()
    for col in ('funding','cost','realized_price_pnl'):daily[col]=hourly[col].resample('D').sum()
    order_frame=pd.DataFrame(orders,columns=['date','symbol','action','leg','side','quantity','price','notional','cost'])
    if terminal and (abs(book.reconciliation())>1e-6 or book.spot_quantity.any() or book.future.quantity.any()):
        raise AssertionError('Final flat native ledger does not reconcile')
    if abs(order_frame.cost.sum()-book.fees)>1e-7:raise AssertionError('Four-fill order costs differ from ledger')
    return daily,hourly,order_frame
