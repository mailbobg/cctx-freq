"""X13 tick-level fill check (spec: specs/2026-09-27-x13-resting-tp-design.md, validation step 3).

Samples 100 take-profit ("roi") exits per year from the fee-0.1% backtests (seed 0) and checks Binance aggTrades
from entry + 60 s to the end of the backtest's fill minute:
  filled     = some trade printed strictly above the target (our resting ask would have been taken first)
  uncertain  = highest print equals the target (queue-dependent) -> counted as not filled
  not filled = highest print below the target
aggTrades day files are streamed from data.binance.vision and not kept.

Usage: python user_data/bench/x13_tickcheck.py [label]   (label default fee10)
"""

import io
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from freqtrade.data.btanalysis import get_latest_backtest_filename, load_backtest_data

LABEL = sys.argv[1] if len(sys.argv) > 1 else "fee10"
STRAT = sys.argv[2] if len(sys.argv) > 2 else "E0V1ENX13"
OUT = Path(f"user_data/bench/events/x13_tickcheck_{LABEL}.csv")
URL = "https://data.binance.vision/data/spot/daily/aggTrades/{s}/{s}-aggTrades-{d}.zip"
COLS = ["agg_id", "price", "qty", "first_id", "last_id", "time", "buyer_maker", "best_match"]
session = requests.Session()
session.headers["User-Agent"] = "ccxt-fr-research/0.1"


def sample() -> pd.DataFrame:
    parts = []
    for y in (2024, 2025, 2026):
        d = Path(f"user_data/backtest_results/x13/{LABEL}-{y}")
        t = load_backtest_data(d / get_latest_backtest_filename(d), STRAT)
        tp = t[t["exit_reason"] == "roi"]
        parts.append(tp.sample(n=min(100, len(tp)), random_state=0).assign(year=y, tp_total=len(tp), trades_total=len(t)))
    return pd.concat(parts, ignore_index=True)


def day_trades(symbol: str, day: str) -> pd.DataFrame | None:
    for attempt in range(4):
        try:
            r = session.get(URL.format(s=symbol, d=day), timeout=180)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                df = pd.read_csv(z.open(z.namelist()[0]), header=None, names=COLS, usecols=[1, 2, 5])
            if not str(df.iloc[0, 0]).replace(".", "").isdigit():
                df = df.iloc[1:]
            df = df.astype({"price": float, "qty": float, "time": "int64"})
            unit = "us" if df["time"].iloc[0] > 10**14 else "ms"
            df["time"] = pd.to_datetime(df["time"], unit=unit, utc=True)
            return df
        except Exception:  # noqa: BLE001
            if attempt == 3:
                raise
    return None


def check(row) -> dict:
    sym = row.pair.replace("/", "")
    start = row.open_date + pd.Timedelta(seconds=60)
    end = row.close_date + pd.Timedelta(seconds=60)
    days = sorted({start.strftime("%Y-%m-%d"), (end - pd.Timedelta(microseconds=1)).strftime("%Y-%m-%d")})
    frames = [f for d in days if (f := day_trades(sym, d)) is not None]
    out = {"pair": row.pair, "year": row.year, "open_date": row.open_date, "close_date": row.close_date,
           "target": row.close_rate, "amount": row.amount}
    if not frames:
        return out | {"result": "no_data"}
    w = pd.concat(frames)
    w = w[(w["time"] >= start) & (w["time"] < end)]
    if w.empty:
        return out | {"result": "not_filled", "max_price": np.nan}
    mx = w["price"].max()
    above = w[w["price"] > row.close_rate]
    res = "filled" if len(above) else ("uncertain" if mx == row.close_rate else "not_filled")
    return out | {"result": res, "max_price": mx, "qty_above": above["qty"].sum(),
                  "first_above_s": (above["time"].min() - row.open_date).total_seconds() if len(above) else np.nan}


def main() -> None:
    s = sample()
    print(f"sample {len(s)} take-profit exits; per year:",
          s.groupby("year")[["tp_total", "trades_total"]].first().to_dict("index"), flush=True)
    rows = []
    with ThreadPoolExecutor(6) as ex:
        for i, r in enumerate(ex.map(check, s.itertuples()), 1):
            rows.append(r)
            if i % 50 == 0:
                print(f"  {i}/{len(s)}", flush=True)
    res = pd.DataFrame(rows)
    res.to_csv(OUT, index=False)
    valid = res[res["result"] != "no_data"]
    print("\nresult counts:", res["result"].value_counts().to_dict())
    for y, g in valid.groupby("year"):
        print(f"{y}: fill rate {(g['result'] == 'filled').mean() * 100:.1f}%  (n={len(g)}, uncertain {(g['result'] == 'uncertain').sum()})")
    print(f"all: fill rate {(valid['result'] == 'filled').mean() * 100:.1f}%  (n={len(valid)})")
    f = valid[valid["result"] == "filled"]
    if len(f):
        print(f"median qty traded above target / our amount: {(f['qty_above'] / f['amount']).median():.1f}x; "
              f"median time entry -> first print above: {f['first_above_s'].median() / 60:.0f} min")
    print(f"saved {OUT}")


if __name__ == "__main__":
    main()
