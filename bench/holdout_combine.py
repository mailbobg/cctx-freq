"""Combine the per-year holdout backtests into the spec's six criteria (specs/2026-09-26-holdout-2021-2023.md).

Usage: python user_data/bench/holdout_combine.py <label> <strategy>   (reads backtest_results/holdout/<label>-<year>/)
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from freqtrade.data.btanalysis import get_latest_backtest_filename, load_backtest_data, load_backtest_stats

label, strat = sys.argv[1], sys.argv[2]
yearly, pnl, monthly = {}, [], []
for y in (2021, 2022, 2023):
    d = Path(f"user_data/backtest_results/holdout/{label}-{y}")
    f = d / get_latest_backtest_filename(d)
    s = load_backtest_stats(f)["strategy"][strat]
    t = load_backtest_data(f, strat)
    start = s["starting_balance"]
    yearly[y] = s["profit_total"]
    pnl.append(t["profit_abs"].to_numpy() / start)          # each trade as a fraction of that year's start
    m = t.groupby(t["close_date"].dt.tz_convert("UTC").dt.tz_localize(None).dt.to_period("M"))["profit_abs"].sum() / start
    monthly.append(m)
    print(f"{y}: {s['profit_total'] * 100:+8.1f}%  trades {s['total_trades']:5d}  "
          f"avg/trade {t['profit_ratio'].mean() * 100:+.2f}%  market {s.get('market_change', 0) * 100:+.1f}%  file {f.name}")
comp = np.prod([1 + v for v in yearly.values()]) - 1
p = np.concatenate(pnl)
rng = np.random.default_rng(0)
draws = rng.choice(p, size=(5000, len(p)), replace=True).sum(axis=1) * 100
lo, hi = np.percentile(draws, [2.5, 97.5])
m = pd.concat(monthly)
print(f"compounded {comp * 100:+.1f}%  | sum of yearly {sum(yearly.values()) * 100:+.1f}%  "
      f"| CI (pooled, % of yearly start) [{lo:+.1f}, {hi:+.1f}]  | -best3 months {(m.sum() - m.nlargest(3).sum()) * 100:+.1f}%  "
      f"| months positive {(m > 0).mean() * 100:.0f}%")
