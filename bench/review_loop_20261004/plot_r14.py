"""Scientific comparison of retained account paths; no strategy rerun."""
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd

HERE=Path(__file__).resolve().parent
fig,axes=plt.subplots(2,2,figsize=(12,7.5),sharex='col')
colors={'Cov10_baseline':'#315e9f','buy_confirmation':'#ba5d28'}
labels={'Cov10_baseline':'Unchanged Cov10','buy_confirmation':'Aggressive-buy confirmation'}
for col,(window,title) in enumerate([('early_reused','2021-02–2023-12 (reused history)'),('recent_reused','2024-01–2026-08 (reused history)')]):
    for name in colors:
        curve=pd.read_csv(HERE/f'r14_{window}_{name}_cost0.0012_daily.csv',index_col='date',parse_dates=True)
        t=curve.index.tz_localize(None);equity=curve.equity
        axes[0,col].plot(t,equity*10000,color=colors[name],label=labels[name],lw=1.5)
        dd=(equity/equity.cummax().clip(lower=1.)-1)*100
        axes[1,col].plot(t,dd.cummin(),color=colors[name],lw=1.5,label=labels[name]+' daily CLOSE')
        if 'sampled_minute_running_dd_pct' in curve:
            axes[1,col].plot(t,curve.sampled_minute_running_dd_pct,color=colors[name],lw=1.5,ls=':',label=labels[name]+' minute sample')
    axes[0,col].set_title(title);axes[0,col].axhline(10000,color='#999999',lw=.7)
    axes[1,col].axhline(-5,color='#a83232',ls='--',lw=1.2,label='5% risk ceiling')
    axes[0,col].legend(fontsize=8,loc='upper left')
    axes[1,col].legend(fontsize=7,loc='lower left')
    for row in (0,1):
        axes[row,col].grid(alpha=.18);axes[row,col].spines[['top','right']].set_visible(False)
        axes[row,col].tick_params(axis='x',rotation=20)
axes[0,0].set_ylabel('Account equity, USDT (10,000 start)')
axes[1,0].set_ylabel('Worst observed account drawdown so far, %')
fig.suptitle('R14 rejects: confirmation reduces participation without meeting return / risk gates',fontsize=12)
fig.text(.5,.018,'Fixed weekly OPEN targets, 0.12% per side incl. terminal costs. Candle-price sampling is not quote/fill or tick risk proof.',ha='center',fontsize=9)
fig.tight_layout(rect=(0,.045,1,.95))
fig.savefig(HERE/'r14_matched_risk.png',dpi=170)
print(HERE/'r14_matched_risk.png')
