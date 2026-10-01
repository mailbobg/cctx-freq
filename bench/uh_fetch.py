"""Daily high/low/close for every USDT-M perpetual (spec U1: liquidation of a 1x short on the intraday high):
monthly 1d klines from data.binance.vision → data_f1/daily_ohlc.parquet (symbol, date, high, low, close).

Usage: python user_data/bench/uh_fetch.py
"""

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from fetch_vision import months, read_csv  # noqa: E402
from p1_fetch import BASE, eligible, fetch  # noqa: E402

ROOT = Path("user_data/data_f1")
RAW = ROOT / "raw" / "1d_all"
LAST = "2026-08"


def main():
    daily = pd.read_parquet("user_data/data_p1/daily.parquet")
    span = daily.groupby("symbol")["date"].agg(["min", "max"])
    jobs = []
    for s, (a, b) in span.iterrows():
        if not eligible(s):
            continue
        m0, m1 = a.strftime("%Y-%m"), min(b.strftime("%Y-%m"), LAST)
        if m0 > m1:
            continue
        for m in months(m0, m1):
            jobs.append((f"{BASE}/monthly/klines/{s}/1d/{s}-1d-{m}.zip", RAW / s / f"{s}-1d-{m}.csv"))
    print(f"{len(span)} symbols, {len(jobs)} archives", flush=True)
    with ThreadPoolExecutor(16) as ex:
        for i, _ in enumerate(ex.map(lambda j: fetch(*j), jobs), 1):
            if i % 3000 == 0:
                print(f"  {i}/{len(jobs)}", flush=True)
    frames = []
    for d in sorted(RAW.iterdir()):
        files = sorted(d.glob("*.csv"))
        if files:
            df = pd.concat([read_csv(f) for f in files])[["date", "high", "low", "close"]]
            df["symbol"] = d.name
            frames.append(df)
    out = pd.concat(frames).drop_duplicates(["symbol", "date"]).sort_values(["symbol", "date"]).reset_index(drop=True)
    out.to_parquet(ROOT / "daily_ohlc.parquet")
    chk = out.merge(daily[["symbol", "date", "close"]].rename(columns={"close": "c0"}), on=["symbol", "date"])
    print(f"daily_ohlc: {out.symbol.nunique()} symbols, {len(out)} rows; close matches daily.parquet: "
          f"{(abs(chk.close / chk.c0 - 1) < 1e-9).mean() * 100:.2f}% of {len(chk)}", flush=True)


if __name__ == "__main__":
    main()
