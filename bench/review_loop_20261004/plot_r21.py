"""Shareable display of frozen outputs; no numerical trading changes."""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

HERE=Path(__file__).resolve().parent
RESULTS=json.loads((HERE/'r21_results.json').read_text())['results']
fig,axes=plt.subplots(2,2,figsize=(13,8),sharex='col',height_ratios=(1.6,1))
colors={'Cov10_baseline':'#818a96','basis_confirmation':'#087f72'}
for col,(window,title) in enumerate([('early_reused','2021 Feb – 2023 Dec'),('recent_reused','2024 Jan – 2026 Aug')]):
    for name,label in [('Cov10_baseline','Original Cov10'),('basis_confirmation','Quarterly gate')]:
        for cost,style in [(.0012,'-'),(.0024,'--')]:
            key=f'{window}_{name}_cost{cost}'
            f=pd.read_csv(HERE/f'r21_{key}_daily.csv',float_precision='round_trip',index_col='date',parse_dates=True)
            eq=pd.concat([pd.Series([1.],index=[f.index[0]-pd.Timedelta(days=1)]),f.equity])
            dd=(eq/eq.cummax()-1)*100
            line_label=f'{label}, {cost*100:.2f}% / side'
            axes[0,col].plot(eq.index,eq*100,style,color=colors[name],linewidth=1.8 if name=='basis_confirmation' else 1.2,label=line_label)
            axes[1,col].plot(dd.index,dd,style,color=colors[name],linewidth=1.3)
    r=RESULTS[f'{window}_basis_confirmation_cost0.0012']
    note=f"Gate: CAGR {r['CAGR_pct']:.2f}% | daily DD {abs(r['daily_MTM_DD_pct']):.2f}%"
    if col==1:note+=f"\nMinute OPEN/CLOSE DD {abs(r['sampled_minute_account_DD_pct']):.2f}%"
    axes[0,col].set_title(title+'\n'+note,loc='left',fontsize=11)
    axes[0,col].set_ylabel('Account index (initial = 100)')
    axes[1,col].set_ylabel('Daily CLOSE drawdown (%)')
    axes[1,col].axhline(-5,color='#be4034',linewidth=1,linestyle=':',label='5% adoption limit')
    axes[1,col].set_ylim(-9,0.3)
    for ax in axes[:,col]:
        ax.grid(axis='y',alpha=.2);ax.spines[['top','right']].set_visible(False)
        locator=mdates.AutoDateLocator(minticks=4,maxticks=7)
        ax.xaxis.set_major_locator(locator);ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    axes[0,col].legend(loc='upper left',fontsize=8,frameon=False)
axes[0,1].text(.97,.12,'All 280 recent asset decisions allow.\nCandidate and control are identical.',transform=axes[0,1].transAxes,
               ha='right',fontsize=10,color='#087f72')
axes[1,1].legend(loc='lower left',fontsize=8,frameon=False)
fig.suptitle('R21: quarterly discount confirmation fails the frozen selection gates',x=.06,ha='left',fontsize=15,fontweight='bold')
fig.text(.06,.025,'Reused history; fixed costs; current original exchange archives. Daily CLOSE DD shown; recent minute risk annotated.\nHistorical publication/quote/lot and independent forward fidelity remain unverified. No adoption or bot activation.',fontsize=9,color='#505860')
fig.subplots_adjust(left=.07,right=.97,top=.87,bottom=.12,hspace=.2,wspace=.2)
fig.savefig(HERE/'r21_basis_screen.png',dpi=160,facecolor='white')
print(HERE/'r21_basis_screen.png')
