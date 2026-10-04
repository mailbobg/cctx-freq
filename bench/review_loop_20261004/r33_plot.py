"""Display one frozen rule and original controls; never fit or select a variant."""
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd

HERE = Path(__file__).resolve().parent
plt.rcParams.update({'font.size':10, 'axes.spines.top':False, 'axes.spines.right':False,
                     'axes.grid':True, 'grid.alpha':.16})
fig, axes = plt.subplots(2, 2, figsize=(13, 8.2), layout='constrained')
for col, (window, label) in enumerate([
        ('early_reused', '2021-02 to 2023-12'), ('recent_reused', '2024-01 to 2026-08')]):
    for name, title, color in [('Cov10_baseline', 'Original Cov10', '#496c91'),
                               ('reserve_quantity', 'Reserve quantity contraction gate', '#b65c39')]:
        for cost, style, suffix in [(.0012, '-', 'base cost'), (.0024, '--', 'doubled cost')]:
            curve = pd.read_csv(HERE/f'r33_{window}_{name}_cost{cost}_daily.csv', index_col='date', parse_dates=True)
            axes[0, col].plot(curve.index, (curve.equity-1)*100, color=color, ls=style,
                              lw=1.5, label=title+': '+suffix)
            if window == 'recent_reused':
                worst = curve.sampled_minute_running_dd_pct
            else:
                worst = ((curve.equity/curve.equity.cummax().clip(lower=1.)-1)*100).cummin()
            axes[1, col].plot(curve.index, worst, color=color, ls=style, lw=1.5)
    axes[0, col].set(title=label+' (reused history)', ylabel='Net total return (%)')
    axes[0, col].axhline(0, color='#777', lw=.7)
    axes[0, col].legend(fontsize=8, loc='best')
    axes[1, col].set(ylabel='Worst account drawdown seen (%)', xlabel='UTC date',
                     title='Daily CLOSE marking' if col == 0 else 'Synchronized minute OPEN/CLOSE marking')
    axes[1, col].axhline(-5, color='#555', ls=':', lw=1, label='5% drawdown limit')
    axes[1, col].legend(fontsize=8)
    for row in range(2):
        axes[row, col].tick_params(axis='x', labelrotation=22)
fig.suptitle('R33 original-report reserve contraction gate after unchanged BTC / ETH joint sizing\nActual report dates; modeled conservative arrival buffer; no first-receipt, actual-fill or forward certification', fontsize=13)
fig.savefig(HERE/'r33_reserve_quantity_screen.png', dpi=150)
plt.close(fig)
