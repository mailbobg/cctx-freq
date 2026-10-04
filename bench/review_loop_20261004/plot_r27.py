"""Standard shareable plots of frozen outcomes; no trading calculations."""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

HERE=Path(__file__).resolve().parent
RESULTS=json.loads((HERE/'r27_results.json').read_text())['results']
fig,axes=plt.subplots(2,2,figsize=(13,8),sharex='col',height_ratios=(1.6,1))
colors={'Cov10_baseline':'#818a96','activity_confirmation':'#087f72'}
for col,(window,title) in enumerate([('early_reused','2021 Feb – 2023 Dec'),('recent_reused','2024 Jan – 2026 Aug')]):
    for name,label in [('Cov10_baseline','Original Cov10'),('activity_confirmation','Quantity activity majority')]:
        for cost,style in [(.0012,'-'),(.0024,'--')]:
            key=f'{window}_{name}_cost{cost}'
            f=pd.read_csv(HERE/f'r27_{key}_daily.csv',float_precision='round_trip',index_col='date',parse_dates=True)
            eq=pd.concat([pd.Series([1.],index=[f.index[0]-pd.Timedelta(days=1)]),f.equity])
            dd=(eq/eq.cummax()-1)*100
            axes[0,col].plot(eq.index,eq*100,style,color=colors[name],linewidth=1.8 if name=='activity_confirmation' else 1.2,
                label=f'{label}, {cost*100:.2f}% / side')
            axes[1,col].plot(dd.index,dd,style,color=colors[name],linewidth=1.3)
    r=RESULTS[f'{window}_activity_confirmation_cost0.0012']
    note=f"Gate: CAGR {r['CAGR_pct']:.2f}% | daily DD {abs(r['daily_MTM_DD_pct']):.2f}%"
    if col==1:note+=f"\nMinute OPEN/CLOSE DD {abs(r['sampled_minute_account_DD_pct']):.2f}%"
    axes[0,col].set_title(title+'\n'+note,loc='left',fontsize=11)
    axes[0,col].set_ylabel('Account index (initial = 100)')
    axes[1,col].set_ylabel('Daily CLOSE drawdown (%)')
    axes[1,col].axhline(-5,color='#be4034',linewidth=1,linestyle=':',label='5% adoption limit')
    for ax in axes[:,col]:
        ax.grid(axis='y',alpha=.2);ax.spines[['top','right']].set_visible(False)
        ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=(1,7)))
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        ax.tick_params(axis='x',labelrotation=20)
    axes[0,col].legend(loc='upper left',fontsize=8,frameon=False)
axes[1,1].legend(loc='lower left',fontsize=8,frameon=False)
fig.suptitle('R27: fixed external native traded activity on unchanged portfolio risk',x=.06,ha='left',fontsize=15,fontweight='bold')
fig.text(.06,.025,'Reused history; fixed panel and costs; current original exchange archives. Daily CLOSE DD shown; recent minute risk annotated.\nHistorical publication/quote/lot and independent forward fidelity remain unverified. No adoption or bot activation.',fontsize=9,color='#505860')
fig.subplots_adjust(left=.07,right=.97,top=.87,bottom=.15,hspace=.2,wspace=.2)
fig.savefig(HERE/'r27_activity_screen.png',dpi=160,facecolor='white')
print(HERE/'r27_activity_screen.png')
