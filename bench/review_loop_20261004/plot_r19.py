"""Standalone comparison of retained fixed policies, no new rule experiment."""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
results=json.loads((HERE/'r19_results.json').read_text())['results']
reflection=json.loads((HERE/'r19_reflection.json').read_text())['results']
colors={'Cov10_baseline':'#b5762f','term_confirmation':'#19776e'}
labels={'Cov10_baseline':'Unchanged Cov10','term_confirmation':'Archived VIX term gate'}
plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
fig,axes=plt.subplots(2,2,figsize=(13,8.1),layout='constrained')
curves={}
for ax,window,title in zip(axes[0],('early_reused','recent_reused'),('2021 Feb - 2023 Dec','2024 Jan - 2026 Aug')):
    for name in colors:
        key=f'{window}_{name}_cost0.0012'
        curve=pd.read_csv(HERE/f'r19_{key}_daily.csv',index_col='date',parse_dates=True,float_precision='round_trip')
        curves[window,name]=curve;r=results[key]
        ax.plot(curve.index,curve.equity*10000,color=colors[name],label=f'{labels[name]}: CAGR {r["CAGR_pct"]:.2f}%')
    ax.set(title=title+' | net modeled account',ylabel='USDT, starting at 10,000')
    ax.axhline(10000,color='#999999',lw=.7);ax.grid(alpha=.15);ax.legend(fontsize=9,loc='upper left')
ax=axes[1,0]
for name in colors:
    curve=curves['recent_reused',name]
    daily=(curve.equity/curve.equity.cummax().clip(lower=1.)-1).cummin()*100
    ax.plot(curve.index,curve.sampled_minute_running_dd_pct,color=colors[name],label=labels[name]+' minute samples')
    ax.plot(curve.index,daily,color=colors[name],ls=':',alpha=.75,label=labels[name]+' daily CLOSE')
ax.axhline(-5,color='#a94242',ls='--',lw=1,label='5% mandate')
ax.set(title='Recent running worst account drawdown',ylabel='Percent from prior observed peak',ylim=(-10,.3))
ax.grid(alpha=.15);ax.legend(fontsize=8,loc='lower left',ncol=1)
ax=axes[1,1];categories=['BTC','ETH','fees'];x=np.arange(3);width=.35
for shift,window,color,label in [(-.5,'early_reused','#557c9c','Earlier'),(.5,'recent_reused','#744f74','Recent')]:
    values=[reflection[window]['term_minus_control_PnL_USDT_10000_start'][a] for a in categories]
    ax.bar(x+shift*width,values,width,color=color,label=label)
ax.set(xticks=x,xticklabels=categories,title='Term gate minus unchanged account P&L',ylabel='USDT per 10,000 starting account')
ax.axhline(0,color='#888888',lw=.8);ax.legend(fontsize=9);ax.grid(axis='y',alpha=.15)
for ax in (axes[0,0],axes[0,1],axes[1,0]):
    locator=mdates.MonthLocator(bymonth=(1,7))
    ax.xaxis.set_major_locator(locator);ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
fig.suptitle('R19 archived SPX volatility term gate: necessary screens rejected',fontsize=15,weight='bold')
fig.supxlabel('Fixed 0.12% side cost; reused history. Missing Friday pairs veto too. No actual-fill or independent-forward certification.',fontsize=9)
fig.savefig(HERE/'r19_term_screen.png',dpi=155)
