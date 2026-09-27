"""Robustness evaluation of freqtrade backtest results.

Usage:
    python user_data/bench/evaluate.py <result.zip|results_dir> [<result.zip> ...]

For every strategy in every result file, prints:
  * headline stats (profit, trades, win rate, drawdown, sharpe)
  * yearly profit
  * monthly profit and the lottery test: profit with the best 1 / best 3
    months removed, share of positive months, monthly t-stat
All profits are in % of the starting balance (freqtrade's own convention).
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.data.btanalysis import (
    get_latest_backtest_filename,
    load_backtest_data,
    load_backtest_stats,
)


def resolve(arg: str) -> Path:
    p = Path(arg)
    if p.is_dir():
        return p / get_latest_backtest_filename(p)
    return p


def monthly_profit(trades: pd.DataFrame, start_balance: float) -> pd.Series:
    close_utc = trades["close_date"].dt.tz_convert("UTC").dt.tz_localize(None)
    by_month = trades.groupby(close_utc.dt.to_period("M"))["profit_abs"].sum()
    return by_month / start_balance * 100


def evaluate_strategy(name: str, s: dict, trades: pd.DataFrame) -> None:
    start = s["starting_balance"]
    total = s["profit_total"] * 100
    n = s["total_trades"]
    wins = s["wins"]
    print(f"\n=== {name}  [{s['backtest_start'][:10]} → {s['backtest_end'][:10]}]")
    print(
        f"profit {total:+.2f}%  trades {n}  win {wins / n * 100 if n else 0:.1f}%  "
        f"max DD {s.get('max_drawdown_account', 0) * 100:.1f}%  "
        f"sharpe {s.get('sharpe', float('nan')):.2f}  "
        f"market {s.get('market_change', 0) * 100:+.1f}%"
    )
    if n == 0:
        return

    # bootstrap the trade P&L: the interval says what size of difference this sample can resolve
    rng = np.random.default_rng(0)
    pnl = trades["profit_abs"].to_numpy()
    draws = rng.choice(pnl, size=(5000, len(pnl)), replace=True).sum(axis=1) / start * 100
    lo, hi = np.percentile(draws, [2.5, 97.5])
    print(f"bootstrap 95% CI of total profit: [{lo:+.1f}%, {hi:+.1f}%]  (half-width {(hi - lo) / 2:.1f}pp, {len(pnl)} trades)")

    m = monthly_profit(trades, start)
    yearly = m.groupby(m.index.year).sum()
    print("yearly:  " + "  ".join(f"{y}: {v:+.1f}%" for y, v in yearly.items()))

    top = m.sort_values(ascending=False)
    without1 = m.sum() - top.iloc[:1].sum()
    without3 = m.sum() - top.iloc[:3].sum()
    pos = (m > 0).mean() * 100
    t = m.mean() / m.std(ddof=1) * np.sqrt(len(m)) if len(m) > 1 and m.std(ddof=1) > 0 else float("nan")
    print(
        f"lottery: sum {m.sum():+.1f}%  -best1 {without1:+.1f}%  -best3 {without3:+.1f}%  "
        f"| months {len(m)}  positive {pos:.0f}%  t {t:.2f}"
    )
    print("best months: " + ", ".join(f"{p} {v:+.1f}%" for p, v in top.iloc[:3].items()))
    print("monthly: " + " ".join(f"{p.strftime('%y-%m')}:{v:+.1f}" for p, v in m.items()))


def main(args: list[str]) -> None:
    if not args:
        print(__doc__)
        sys.exit(1)
    for arg in args:
        path = resolve(arg)
        stats = load_backtest_stats(path)
        print(f"\n##### {path.name}")
        for name, s in stats["strategy"].items():
            trades = load_backtest_data(path, strategy=name)
            evaluate_strategy(name, s, trades)


if __name__ == "__main__":
    main(sys.argv[1:])
