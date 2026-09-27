"""Mark-to-market equity curve for a freqtrade backtest result.

freqtrade's reported drawdown only counts closed trades. A strategy that keeps
losing positions open for months (DCA, "grinding") hides most of its risk that
way. This script rebuilds the daily equity curve from the exported orders,
valuing open positions at each day's close, and reports the real drawdown.

Usage: python user_data/bench/mtm.py <result.zip> [<result.zip> ...]
"""

import os
import sys
from pathlib import Path

import pandas as pd

from freqtrade.data.btanalysis import load_backtest_data, load_backtest_stats

DATADIR = Path(os.environ.get("MTM_DATADIR", "user_data/data/binance"))


def daily_closes(pairs, days: pd.DatetimeIndex) -> pd.DataFrame:
    closes = {}
    for p in pairs:
        spot = DATADIR / f"{p.replace('/', '_')}-1d.feather"
        futures = DATADIR / "futures" / f"{p.replace('/', '_').replace(':', '_')}-1d-futures.feather"
        f = futures if ":" in p else spot
        if f.exists():
            df = pd.read_feather(f)
            closes[p] = df.set_index("date")["close"]
    out = pd.DataFrame(closes)
    return out.reindex(days, method="ffill")


def equity_curve(trades: pd.DataFrame, start_balance: float, days: pd.DatetimeIndex):
    closes = daily_closes(trades["pair"].unique(), days)
    cash = pd.Series(0.0, index=days)
    pos = pd.DataFrame(0.0, index=days, columns=closes.columns)
    for _, t in trades.iterrows():
        fee = t["fee_open"]
        for o in t["orders"]:
            day = pd.Timestamp(o["order_filled_timestamp"], unit="ms", tz="UTC").floor("D")
            if day < days[0]:
                day = days[0]
            if o["ft_order_side"] == "buy":
                cash[day] -= o["cost"] * (1 + fee)
                pos.loc[day, t["pair"]] += o["amount"]
            else:
                cash[day] += o["cost"] * (1 - fee)
                pos.loc[day, t["pair"]] -= o["amount"]
    # funding fees (futures) are booked by freqtrade into profit_abs; add them as cash at trade close
    if "funding_fees" in trades.columns:
        for day, fee in zip(trades["close_date"].dt.floor("D"), trades["funding_fees"].fillna(0.0)):
            if day in cash.index:
                cash[day] += fee
    cash = start_balance + cash.cumsum()
    pos = pos.cumsum()
    holdings = (pos * closes).fillna(0.0)
    equity = cash + holdings.sum(axis=1)
    return equity, cash, holdings


def report(name: str, s: dict, trades: pd.DataFrame) -> None:
    start = s["starting_balance"]
    days = pd.date_range(
        pd.Timestamp(s["backtest_start"]).floor("D"),
        pd.Timestamp(s["backtest_end"]).floor("D"),
        freq="D",
        tz="UTC",
    )
    equity, cash, holdings = equity_curve(trades, start, days)
    peak = equity.cummax()
    dd = equity / peak - 1
    worst = dd.idxmin()
    exposure = holdings.sum(axis=1) / equity
    print(f"\n=== {name}  [{days[0].date()} → {days[-1].date()}]")
    print(
        f"final equity {equity.iloc[-1] / start * 100 - 100:+.1f}%  (freqtrade closed-trade profit "
        f"{s['profit_total'] * 100:+.1f}%)"
    )
    print(
        f"max drawdown mark-to-market {dd.min() * 100:.1f}% on {worst.date()}  "
        f"(freqtrade closed-trade drawdown {s.get('max_drawdown_account', 0) * 100:.1f}%)"
    )
    print(
        f"equity at worst point {equity[worst] / start * 100 - 100:+.1f}%  "
        f"cash {cash[worst] / start * 100:.0f}%  in positions {holdings.loc[worst].sum() / start * 100:.0f}%"
    )
    print(f"max exposure {exposure.max() * 100:.0f}% of equity, days fully invested (>90%): {(exposure > 0.9).sum()}")
    bags = holdings.loc[worst].sort_values(ascending=False).head(4)
    print("biggest positions at worst point: " + ", ".join(f"{p} {v / start * 100:.0f}%" for p, v in bags.items() if v > 0))
    monthly = equity.resample("ME").last()
    monthly_ret = monthly.pct_change() * 100
    monthly_ret.iloc[0] = (monthly.iloc[0] / start - 1) * 100
    print("monthly MTM: " + " ".join(f"{d.strftime('%y-%m')}:{v:+.1f}" for d, v in monthly_ret.items()))


def main(args):
    for arg in args:
        stats = load_backtest_stats(arg)
        print(f"\n##### {Path(arg).name}")
        for name, s in stats["strategy"].items():
            report(name, s, load_backtest_data(arg, strategy=name))


if __name__ == "__main__":
    main(sys.argv[1:])
