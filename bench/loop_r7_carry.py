"""Loop round 7: funding-rate cash-and-carry (long spot + short perp), per the r/algotradingcrypto
physics-PhD post (survived OOS) vs Borri–Liu–Tsyvinski–Wu (carry Sharpe negative in 2025).

Weekly (Monday 00:00 UTC): hold the hedge on every coin whose trailing 7-day mean funding > 0, equal
weight across held coins. Income = funding received by the short perp. Costs on every entry/exit of a
coin: spot 0.1% + perp 0.05% per side (a coin entering and later leaving pays 2×(0.1%+0.05%)).
Price legs cancel (basis ignored; it converges at exit on average). Output: weekly portfolio return,
annualised by year. Variant: BTC and ETH only.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

FUT = Path("user_data/data/binance/futures")
PAIRS = json.load(open("user_data/pairs-x9.json"))
COST_SIDE = 0.001 + 0.0005


def funding(pair):
    f = FUT / f"{pair.replace('/', '_').replace(':', '_')}-1h-funding_rate.feather"
    s = pd.read_feather(f).set_index("date")["funding_rate"]
    return s[~s.index.duplicated()]


def run(pairs):
    F = {p: funding(p) for p in pairs}
    weeks = pd.date_range("2024-01-01", "2026-09-21", freq="W-MON", tz="UTC")
    held, rows = set(), []
    for w0, w1 in zip(weeks[:-1], weeks[1:]):
        sig = {p: s[(s.index > w0 - pd.Timedelta(days=7)) & (s.index <= w0)].mean() for p, s in F.items()}
        new = {p for p, v in sig.items() if pd.notna(v) and v > 0}
        turnover = len(new ^ held)
        income = [F[p][(F[p].index > w0) & (F[p].index <= w1)].sum() for p in new]
        gross = float(np.mean(income)) if new else 0.0
        cost = (turnover * COST_SIDE) / max(len(new | held), 1)
        rows.append({"week": w0, "n": len(new), "gross": gross, "cost": cost, "net": gross - cost})
        held = new
    return pd.DataFrame(rows).set_index("week")


for label, pairs in (("63 perps", PAIRS), ("BTC+ETH only", ["BTC/USDT:USDT", "ETH/USDT:USDT"])):
    R = run(pairs)
    y = R.groupby(R.index.year).agg(weeks=("net", "size"), gross=("gross", "sum"), cost=("cost", "sum"), net=("net", "sum"), neg_weeks=("net", lambda x: (x < 0).mean()))
    y["net_annualised_%"] = y["net"] / y["weeks"] * 52 * 100
    print(f"\n=== {label}: avg coins held {R.n.mean():.1f}, weekly net mean {R.net.mean()*100:.3f}% (t {R.net.mean()/R.net.std()*np.sqrt(len(R)):.1f}), worst week {R.net.min()*100:.2f}%")
    print((y.assign(gross=lambda d: (d.gross * 100).round(2), cost=lambda d: (d.cost * 100).round(2), net=lambda d: (d.net * 100).round(2), neg_weeks=lambda d: (d.neg_weeks * 100).round(0))
           .rename(columns={"gross": "gross_%", "cost": "cost_%", "net": "net_%", "neg_weeks": "neg_weeks_%"})).round(2).to_string())
