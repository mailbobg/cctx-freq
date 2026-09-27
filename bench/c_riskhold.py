"""C: BTC + ETH risk-managed holding vs 50/50 weekly-rebalanced hold.

Spec and acceptance criteria: user_data/bench/specs/2026-09-27-c-btceth-risk-managed-hold.md

Each Monday: signal from the Sunday daily close, trade at the Monday open.
Per coin: on if 30-day return > 0; weight = 0.5 * min(1, 0.60 / sigma30) when on, else 0; rebalance a coin only when its
target differs from its current weight by > 5 percentage points. Benchmark: 50/50 rebalanced every Monday.

Usage: python user_data/bench/c_riskhold.py [fee] [lookback_days]
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests

DATA = Path("user_data/bench/events/c_daily.parquet")
FEE = float(sys.argv[1]) if len(sys.argv) > 1 else 0.001
LOOKBACK = int(sys.argv[2]) if len(sys.argv) > 2 else 30
VOL_TARGET, CAP, BAND = 0.60, 0.5, 0.05
WINDOWS = {"OOS 2017-2020": ("2017-08-17", "2020-12-31"), "IS 2021-2026": ("2021-01-01", "2026-09-21")}


def load() -> pd.DataFrame:
    if DATA.exists():
        return pd.read_parquet(DATA)
    s = requests.Session()
    out = {}
    for sym in ("BTCUSDT", "ETHUSDT"):
        rows, start = [], int(pd.Timestamp("2017-08-01", tz="UTC").timestamp() * 1000)
        while True:
            d = s.get("https://data-api.binance.vision/api/v3/klines",
                      params={"symbol": sym, "interval": "1d", "startTime": start, "limit": 1000}, timeout=30).json()
            if not d:
                break
            rows += d
            start = d[-1][0] + 86_400_000
            if len(d) < 1000:
                break
        df = pd.DataFrame(rows).iloc[:, :5]
        df.columns = ["t", "open", "high", "low", "close"]
        df["t"] = pd.to_datetime(df["t"], unit="ms", utc=True)
        df = df.set_index("t").astype(float)
        out[sym[:3]] = df
    wide = pd.concat({k: v[["open", "close"]] for k, v in out.items()}, axis=1).dropna()
    wide.columns = [f"{a}_{b}" for a, b in wide.columns]
    wide.to_parquet(DATA)
    return wide


def simulate(px: pd.DataFrame, start: str, end: str, mode: str) -> pd.Series:
    """Daily equity. mode: 'strategy', 'bench', 'trend_only', 'vol_only'."""
    coins = ["BTC", "ETH"]
    closes = px[[f"{c}_close" for c in coins]].set_axis(coins, axis=1)
    opens = px[[f"{c}_open" for c in coins]].set_axis(coins, axis=1)
    logr = np.log(closes).diff()
    mom = closes / closes.shift(LOOKBACK) - 1
    vol = logr.rolling(30).std() * np.sqrt(365)
    days = closes.loc[start:end].index
    # first Monday whose previous close has full history
    valid = mom.notna().all(axis=1) & vol.notna().all(axis=1)
    cash, units = 1.0, pd.Series(0.0, index=coins)
    started = False
    eq = []
    for i, day in enumerate(days):
        prev = closes.index[closes.index.get_loc(day) - 1]
        if day.weekday() == 0 and valid.loc[prev]:
            started = True
            if mode == "bench":
                target = pd.Series(0.5, index=coins)
            else:
                on = (mom.loc[prev] > 0) if mode in ("strategy", "trend_only") else pd.Series(True, index=coins)
                size = (CAP * np.minimum(1, VOL_TARGET / vol.loc[prev])) if mode in ("strategy", "vol_only") else pd.Series(CAP, index=coins)
                target = size.where(on, 0.0)
            o = opens.loc[day]
            value = cash + (units * o).sum()
            cur = units * o / value
            for c in coins:
                if mode == "bench" or abs(target[c] - cur[c]) > BAND:
                    trade = (target[c] - cur[c]) * value
                    units[c] += trade / o[c]
                    cash -= trade + abs(trade) * FEE
        if started:
            eq.append((day, cash + (units * closes.loc[day]).sum(), (units * closes.loc[day]).sum()))
    s = pd.DataFrame(eq, columns=["t", "eq", "inv"]).set_index("t")
    s["inv"] = s["inv"] / s["eq"]
    return s


def metrics(s: pd.DataFrame, turnover_proxy: bool = False) -> dict:
    eq = s["eq"] / s["eq"].iloc[0]
    yrs = (eq.index[-1] - eq.index[0]).days / 365
    r = eq.pct_change().dropna()
    dd = (eq / eq.cummax() - 1).min()
    y2 = eq.resample("YE").last()
    yr_ret = (y2 / pd.concat([pd.Series([eq.iloc[0]], index=[eq.index[0]]), y2.iloc[:-1]]).values - 1)
    return {"CAGR%": (eq.iloc[-1] ** (1 / yrs) - 1) * 100, "maxDD%": dd * 100,
            "Sharpe": r.mean() / r.std() * np.sqrt(365), "worst_year%": yr_ret.min() * 100,
            "years": {int(k.year): round(v * 100, 1) for k, v in zip(y2.index, yr_ret)},
            "invested%": s["inv"].mean() * 100, "start": str(eq.index[0].date()), "total%": (eq.iloc[-1] - 1) * 100}


def main() -> None:
    px = load()
    print(f"data {px.index[0].date()} -> {px.index[-1].date()}, fee {FEE}, lookback {LOOKBACK}")
    for name, (a, b) in WINDOWS.items():
        print(f"\n=== {name}")
        res = {m: metrics(simulate(px, a, b, m)) for m in ("strategy", "bench", "trend_only", "vol_only")}
        for m, v in res.items():
            print(f"{m:11s} start {v['start']}  total {v['total%']:+9.1f}%  CAGR {v['CAGR%']:+6.1f}%  maxDD {v['maxDD%']:6.1f}%  "
                  f"Sharpe {v['Sharpe']:.2f}  worst yr {v['worst_year%']:+6.1f}%  invested {v['invested%']:5.1f}%  years {v['years']}")
        s, bm = res["strategy"], res["bench"]
        worst_bm_year = min(bm["years"], key=bm["years"].get)
        checks = {
            "1 maxDD <= 50% of bench": abs(s["maxDD%"]) <= 0.5 * abs(bm["maxDD%"]),
            "2 CAGR >= 60% of bench and > 0": s["CAGR%"] > 0 and s["CAGR%"] >= 0.6 * bm["CAGR%"],
            "3 Sharpe > bench": s["Sharpe"] > bm["Sharpe"],
            f"4 loss in {worst_bm_year} <= half of bench": s["years"][worst_bm_year] >= 0.5 * bm["years"][worst_bm_year],
        }
        for k, v in checks.items():
            print(f"   {'PASS' if v else 'FAIL'}  {k}")


if __name__ == "__main__":
    main()
