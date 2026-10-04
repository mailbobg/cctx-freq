"""Retained perpetual equity and observed-hour risk, no new strategy run."""
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd

HERE=Path(__file__).resolve().parent
fig,axes=plt.subplots(2,2,figsize=(12,7.5),sharex='col')
colors={'cov10_long':'#315e9f','symmetric_own_trend':'#ba5d28'}
labels={'cov10_long':'Cov10 long perpetual control','symmetric_own_trend':'Symmetric own-trend long / short'}
for col,(window,title) in enumerate([('early_reused','2021-02–2023-12'),('recent_reused','2024-01–2026-08')]):
    for name in colors:
        prefix=f'r15_{window}_{name}_cost0.0012_central'
        curve=pd.read_csv(HERE/f'{prefix}_daily.csv',index_col='date',parse_dates=True)
        hours=pd.read_parquet(HERE/f'{prefix}_hourly.parquet')
        t=curve.index.tz_localize(None)
        axes[0,col].plot(t,curve.equity,color=colors[name],label=labels[name],lw=1.5)
        dd=(curve.equity/curve.equity.cummax().clip(lower=10000)-1)*100
        axes[1,col].plot(t,dd.cummin(),color=colors[name],lw=1.5,label=labels[name]+' daily')
        sampled=hours.sampled_hour_running_dd_pct.resample('D').last().reindex(curve.index)
        axes[1,col].plot(t,sampled,color=colors[name],ls=':',lw=1.5,label=labels[name]+' hour sample')
    axes[0,col].set_title(title+' (reused history)');axes[0,col].axhline(10000,color='#999999',lw=.7)
    axes[1,col].axhline(-5,color='#a83232',ls='--',lw=1.2,label='5% risk ceiling')
    axes[0,col].legend(fontsize=8,loc='upper left');axes[1,col].legend(fontsize=7,loc='lower left')
    for row in (0,1):
        axes[row,col].grid(alpha=.18);axes[row,col].spines[['top','right']].set_visible(False);axes[row,col].tick_params(axis='x',rotation=20)
axes[0,0].set_ylabel('Account equity, USDT (10,000 start)')
axes[1,0].set_ylabel('Worst observed account drawdown so far, %')
fig.suptitle('R15 rejects: early profit improves; recent short losses and drawdown worsen',fontsize=12)
fig.text(.5,.018,'0.12% per side; actual funding rates with disclosed missing-mark approximation. Hourly samples are not executable quotes or tick risk.',ha='center',fontsize=9)
fig.tight_layout(rect=(0,.045,1,.95));fig.savefig(HERE/'r15_matched_risk.png',dpi=170)
print(HERE/'r15_matched_risk.png')
