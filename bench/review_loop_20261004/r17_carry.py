"""Fixed R17 exact-rate forecast and separately funded native-quantity carry ledger."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext

import numpy as np
import pandas as pd

from r16_knowledge import utc
from r6_relative import PerpLedger


ASSETS = ('BTCUSDT','ETHUSDT','SOLUSDT','XRPUSDT','BNBUSDT','ADAUSDT','DOGEUSDT','AVAXUSDT')
HURDLE = Decimal('0.0058')


def rate_decimal(value):
    if not isinstance(value,(str,Decimal)):
        raise ValueError('Exact source decimal funding rate required')
    try: rate=Decimal(value)
    except (InvalidOperation,ValueError) as exc: raise ValueError('Valid decimal rate required') from exc
    if not rate.is_finite(): raise ValueError('Finite decimal funding rate required')
    return rate


def sum_rates(values):
    rates=[rate_decimal(v) for v in values]
    least=min((r.as_tuple().exponent for r in rates),default=0)
    most=max((len(r.as_tuple().digits)+r.as_tuple().exponent for r in rates),default=1)
    with localcontext() as ctx:
        ctx.prec=max(ctx.prec,most-least+len(str(max(1,len(rates))))+2)
        return sum(rates,Decimal('0'))


@dataclass(frozen=True)
class FundingSeries:
    events: pd.DataFrame
    coverage_start: object
    coverage_end: object

    def __post_init__(self):
        start,end=utc(self.coverage_start),utc(self.coverage_end)
        frame=self.events.copy(deep=True)
        if not {'slot','rate_decimal','interval_hours'} <= set(frame.columns) or frame.empty:
            raise ValueError('Explicit nonempty funding calendar and interval schema required')
        if not isinstance(frame.index,pd.DatetimeIndex) or frame.index.tz is None:
            raise ValueError('Timezone-aware funding calendar required')
        frame.index=frame.index.tz_convert('UTC').as_unit('ns')
        slots=pd.DatetimeIndex(frame.slot)
        if slots.tz is None: raise ValueError('Timezone-aware scheduled funding slots required')
        slots=slots.tz_convert('UTC').as_unit('ns')
        offsets=frame.index-slots
        if not slots.equals(frame.index.floor('h')) or np.any(offsets.total_seconds()<0) or np.any(offsets.total_seconds()>1):
            raise ValueError('Only documented sub-second funding hour offsets may normalize')
        intervals=pd.to_numeric(frame.interval_hours,errors='raise').to_numpy(float)
        if not np.isfinite(intervals).all() or np.any(intervals<1) or np.any(intervals>24) or not np.equal(intervals,np.floor(intervals)).all():
            raise ValueError('Integral declared settlement interval required')
        if not slots.is_unique or not slots.is_monotonic_increasing or pd.isna(slots).any():
            raise ValueError('Repeated or reordered funding calendar')
        if len(slots)>1 and not np.array_equal(np.diff(slots.asi8),intervals[1:]*3600*1e9):
            raise ValueError('Funding calendar gap conflicts with declared settlement interval')
        if start>=end or slots[0]!=start or slots[-1]>=end or slots[-1]+pd.Timedelta(hours=intervals[-1])<end:
            raise ValueError('Funding calendar does not establish complete source coverage')
        frame['slot']=slots
        frame['interval_hours']=intervals.astype('int64')
        frame['rate_decimal']=[rate_decimal(v) for v in frame.rate_decimal]
        object.__setattr__(self,'events',frame)
        object.__setattr__(self,'coverage_start',start)
        object.__setattr__(self,'coverage_end',end)


def funding_forecast(series,decision):
    decision=utc(decision)
    if decision.weekday()!=0 or decision.hour!=1 or decision.minute or decision.second or decision.microsecond or decision.nanosecond:
        raise ValueError('Frozen decision must be Monday01 UTC')
    cutoff=decision-pd.Timedelta(hours=1)
    lower=cutoff-pd.Timedelta(days=7)
    if series.coverage_start>lower or series.coverage_end<=cutoff:
        raise ValueError('Funding forecast lacks full known source coverage')
    frame=series.events.loc[(series.events.slot>lower)&(series.events.slot<=cutoff)]
    if frame.empty or frame.slot.iloc[-1]+pd.Timedelta(hours=int(frame.interval_hours.iloc[-1]))<=cutoff:
        raise ValueError('Funding forecast calendar is incomplete at cutoff')
    return sum_rates(frame.rate_decimal)


def select_pairs(scores):
    exact={symbol:rate_decimal(value) for symbol,value in scores.items()}
    names=sorted(symbol for symbol,value in exact.items() if value>HURDLE)
    # Decimal comparison is exact; unary minus would round under caller context.
    return tuple(sorted(names,key=lambda symbol:exact[symbol],reverse=True)[:2])


class PairedLedger:
    def __init__(self,capital,n):
        if not np.isfinite(capital) or capital<=0 or type(n) is not int or n<1:
            raise ValueError('Positive funded capital and instrument dimension required')
        self.initial=float(capital);self.n=n
        self.spot_cash=float(capital)/2
        self.spot_quantity=np.zeros(n);self.spot_entry=np.zeros(n)
        self.future=PerpLedger(float(capital)/2,np.zeros(n),np.zeros(n))
        self.fees=self.price_pnl=self.funding=0.

    def vector(self,value,field,positive=False):
        v=np.asarray(value,float)
        if v.shape!=(self.n,) or not np.isfinite(v).all() or (positive and np.any(v<=0)):
            raise ValueError(f'Finite native {field} vector required')
        return v

    def equity(self,spot,mark):
        spot,mark=self.vector(spot,'spot price',True),self.vector(mark,'mark',True)
        return self.spot_cash+self.spot_quantity@spot+self.future.equity(mark)

    def repool(self):
        if self.spot_quantity.any() or self.future.quantity.any():
            raise ValueError('Cannot repool collateral while spot/perpetual inventory is held')
        capital=self.spot_cash+self.future.balance
        if capital<=0:raise ValueError('Insolvent realized cash')
        self.spot_cash=self.future.balance=capital/2

    def open(self,quantity,spot,future,mark,cost):
        q=self.vector(quantity,'quantity')
        s,f,m=(self.vector(v,name,True) for v,name in [(spot,'spot price'),(future,'future price'),(mark,'mark')])
        if self.spot_quantity.any() or self.future.quantity.any() or np.any(q<0):
            raise ValueError('Fresh nonnegative matched native quantity required')
        if not np.isfinite(cost) or cost<0:raise ValueError('Finite nonnegative cost required')
        bought=q@s;future_gross=q@f
        if bought*(1+cost)>self.spot_cash+1e-8 or future_gross*(1+cost)>self.future.balance+1e-8:
            raise ValueError('Separate actual cash sleeve budget exceeded')
        futures_marked=self.future.balance-future_gross*cost+q@(f-m)
        total_marked=self.spot_cash-bought*cost+futures_marked
        if futures_marked<=0 or q@m>.99*futures_marked+1e-8 or q@(s+m)>.99*total_marked+1e-8:
            raise ValueError('Post-fill marked futures or account gross exceeded')
        self.spot_cash-=bought*(1+cost)
        self.spot_quantity=q.copy();self.spot_entry=s.copy()
        fee,_,pnl=self.future.trade_to(-q,f,cost)
        self.fees+=bought*cost+fee;self.price_pnl+=pnl

    def close(self,spot,future,cost):
        s,f=self.vector(spot,'spot price',True),self.vector(future,'future price',True)
        if not np.isfinite(cost) or cost<0:raise ValueError('Finite nonnegative cost required')
        sold=self.spot_quantity@s
        self.price_pnl+=self.spot_quantity@(s-self.spot_entry)
        self.spot_cash+=sold*(1-cost)
        fee,_,pnl=self.future.trade_to(np.zeros(self.n),f,cost)
        self.fees+=sold*cost+fee;self.price_pnl+=pnl
        self.spot_quantity[:]=0;self.spot_entry[:]=0

    def fund(self,mark,rate,event_mask=None):
        m,r=np.asarray(mark,float),np.asarray(rate,float)
        mask=np.ones(self.n,dtype=bool) if event_mask is None else np.asarray(event_mask,bool)
        if m.shape!=(self.n,) or r.shape!=(self.n,) or mask.shape!=(self.n,):raise ValueError('Funding dimensions differ')
        held=mask & (self.future.quantity!=0)
        if not np.isfinite(r[held]).all():raise ValueError('Unknown held funding rate')
        if not np.isfinite(m[held]).all() or np.any(m[held]<=0):raise ValueError('Unknown held associated mark cannot be imputed')
        payment=float(-self.future.quantity[held]@(m[held]*r[held]))
        self.future.balance+=payment;self.funding+=payment
        return payment

    def margin_concern(self,mark):
        m=self.vector(mark,'mark',True)
        return bool(self.future.quantity.any() and self.future.equity(m)<=.005*(np.abs(self.future.quantity)@m))

    def reconciliation(self):
        if self.spot_quantity.any() or self.future.quantity.any():raise ValueError('Terminal reconciliation requires flat inventories')
        return self.spot_cash+self.future.balance-(self.initial+self.price_pnl+self.funding-self.fees)
