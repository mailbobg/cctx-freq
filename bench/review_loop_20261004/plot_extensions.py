"""Matched, reused-history research curves; never an actual execution/OOS claim."""
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd

HERE = Path(__file__).resolve().parent
SERIES = [('Cov10 control','r10','Cov10_baseline','#4169b1'),
          ('Selected major (R9)','r9','selected_major','#438870'),
          ('Fixed gold (R10)','r10','fixed_gold','#bd7734')]


def main():
    fig,axes = plt.subplots(2,2,figsize=(12,7),sharex='col',layout='constrained')
    for column,label in enumerate(('early_reused','recent_reused')):
        for name,prefix,version,color in SERIES:
            curve = pd.read_csv(HERE/f'{prefix}_{label}_{version}_cost0.0012_daily.csv',index_col='date',parse_dates=True)
            equity = curve.equity
            equity = pd.concat([pd.Series([1.],index=[equity.index[0]-pd.Timedelta(days=1)]),equity])
            drawdown = (equity/equity.cummax()-1)*100
            cagr = (equity.iloc[-1]**(365/len(curve))-1)*100
            axes[0,column].plot(equity.index,equity,label=f'{name}: CAGR {cagr:.2f}%',color=color,lw=1.4)
            axes[1,column].plot(drawdown.index,drawdown,label=f'{name}: DD {-drawdown.min():.2f}%',color=color,lw=1.3)
        axes[0,column].set_title('2021-02 to 2023-12' if column==0 else '2024-01 to 2026-08')
        axes[1,column].axhline(-5,color='#aa3e3e',ls='--',lw=.8)
        locator = mdates.MonthLocator(bymonth=(1,7))
        axes[1,column].xaxis.set_major_locator(locator)
        axes[1,column].xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
        for axis in axes[:,column]:
            axis.grid(alpha=.16)
            axis.legend(loc='best',fontsize=8,frameon=False)
            axis.spines[['top','right']].set_visible(False)
    axes[0,0].set_ylabel('Account equity / initial capital')
    axes[1,0].set_ylabel('Daily marked drawdown (%)')
    fig.suptitle('Matched research ledger: 0.12% per-side cost, final CLOSE fees\nReused history; no Freqtrade / quote-fill / forward certification',fontsize=12)
    fig.savefig(HERE/'r9_r10_matched_risk.png',dpi=160)
    plt.close(fig)


if __name__=='__main__':
    main()
