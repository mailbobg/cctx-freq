"""Matched frozen curves; scientific export, no strategy/model changes."""
from pathlib import Path
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd

HERE=Path(__file__).resolve().parent
fig,axes=plt.subplots(3,1,figsize=(11,8),sharex=True,gridspec_kw={'height_ratios':[1.7,1,1]},layout='constrained')
for name,label,color in [('Cov10_baseline','Unchanged Cov10 control','#234e70'),('ATR22x3','Position-anchored ATR22 x 3','#bb5a32')]:
    c=pd.read_csv(HERE/f'r13_recent_reused_{name}_cost0.0012_daily.csv',index_col='date',parse_dates=True)
    axes[0].plot(c.index,c.equity*10000,label=label,color=color,lw=1.6)
    daily=(c.equity/c.equity.cummax().clip(lower=1)-1)*100
    axes[1].plot(c.index,daily,color=color,lw=1.1)
    axes[2].plot(c.index,c.sampled_minute_running_dd_pct,color=color,lw=1.4)
axes[0].set_title('R13: ATR protection failed the frozen recent-window screen',loc='left',fontsize=14,pad=12)
axes[0].set_ylabel('Daily account equity\n(10,000 USDT start)')
axes[0].legend(loc='upper left',frameon=False)
axes[1].set_ylabel('Daily CLOSE\ndrawdown (%)')
axes[2].set_ylabel('Worst sampled-minute\ndrawdown so far (%)')
for ax in axes:
    ax.grid(axis='y',alpha=.2);ax.spines[['top','right']].set_visible(False)
for ax in axes[1:]:ax.axhline(-5,color='#737373',ls='--',lw=.8)
axes[2].xaxis.set_major_locator(mdates.MonthLocator(interval=4))
axes[2].xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
fig.supxlabel('2024-01-01 to 2026-08-31 | 0.12% all-in side costs, final CLOSE fees\nMinute samples include OPEN before/after orders and CLOSE; candle sampling is not tick/quote risk proof.',fontsize=9)
fig.savefig(HERE/'r13_matched_risk.png',dpi=180)
