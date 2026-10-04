"""Display frozen R17 outcomes, not a model fitting/selection script."""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np


HERE=Path(__file__).resolve().parent
plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'axes.grid':True,'grid.alpha':.16})
fig,axes=plt.subplots(2,2,figsize=(13,8.5),constrained_layout=True)
labels=[('ranked',.0012,'Ranked: base','#087e8b','-'),('ranked',.0024,'Ranked: doubled cost','#be3949','-'),
        ('equal_pairs',.0012,'Equal control: base','#7b8490','--'),('equal_pairs',.0024,'Equal control: doubled cost','#b09891','--')]
for policy,cost,label,color,style in labels:
    daily=pd.read_csv(HERE/f'r17_recent_{policy}_cost{cost}_daily.csv',index_col='date',parse_dates=True)
    axes[0,0].plot(daily.index,(daily.equity/10000-1)*100,label=label,color=color,linestyle=style,lw=1.7)
axes[0,0].axhline(0,color='#777',lw=.6);axes[0,0].set(title='Full-calendar net return: 974 days',ylabel='Return (%)')
axes[0,0].legend(fontsize=8,loc='lower right')
daily=pd.read_csv(HERE/'r17_recent_ranked_cost0.0012_daily.csv',index_col='date',parse_dates=True)
for field,label,color,sign in [('funding','Actual funding +226.32','#087e8b',1),('realized_price_pnl','Paired basis price +14.11','#ba8830',1),('cost','Four-fill costs -157.63','#be3949',-1)]:
    axes[0,1].plot(daily.index,sign*daily[field].cumsum(),label=label,color=color)
axes[0,1].set(title='Base candidate cash attribution',ylabel='Cumulative USDT per 10,000 start');axes[0,1].legend(fontsize=8)
reflection=json.loads((HERE/'r17_reflection.json').read_text())['results']['recent_ranked_cost0.0012']
cycles=reflection['cycles'];x=np.array([float(c['forecast_rate_decimal'])*10000 for c in cycles]);y=np.array([float(c['next_hold_actual_rate_decimal'])*10000 for c in cycles])
for positive,color,label in [(True,'#087e8b','Positive net paired cycle (7)'),(False,'#be3949','Negative net paired cycle (6)')]:
    mask=np.array([c['net_USDT']>0 for c in cycles])==positive
    axes[1,0].scatter(x[mask],y[mask],c=color,label=label,s=42,edgecolors='white',lw=.7)
limit=max(x.max(),y.max())*1.08;axes[1,0].plot([0,limit],[0,limit],color='#777',linestyle='--',lw=1)
axes[1,0].set(xlim=(0,limit),ylim=(0,limit),title='Selected coin-weeks: next sum below forecast in 12/13',xlabel='Prior 7-day actual funding sum (bp)',ylabel='Next held actual funding sum (bp)')
axes[1,0].legend(fontsize=8)
hourly=pd.read_parquet(HERE/'r17_recent_ranked_cost0.0012_hourly.parquet')
ratio=(hourly.futures_gross/hourly.after_orders_futures_equity).resample('D').max()
gross=(hourly.gross_notional/hourly.after_orders_equity).resample('D').max()
axes[1,1].plot(ratio.index,ratio,color='#087e8b',label='Short gross / futures marked equity')
axes[1,1].plot(gross.index,gross,color='#ba8830',label='Paired gross / total marked equity')
axes[1,1].axhline(.99,color='#777',lw=.9,linestyle='--',label='Entry cap only: 0.99')
axes[1,1].set(title='Between-week collateral drift: short ratio reaches 2.46',ylabel='Notional / marked equity',ylim=(0,2.65));axes[1,1].legend(fontsize=8)
for ax in (axes[0,0],axes[0,1],axes[1,1]):
    ax.tick_params(axis='x',labelrotation=25)
fig.suptitle('R17 same-native spot/perpetual carry: first necessary screen rejected\nReused history; hourly candles; exact funding payments; no execution/forward certification',fontsize=14)
fig.savefig(HERE/'r17_carry_screen.png',dpi=150)
plt.close(fig)
