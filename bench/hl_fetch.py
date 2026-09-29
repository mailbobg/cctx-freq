"""Daily highs/lows for the L1 listing events (intraday liquidation check): monthly 1d klines from data.binance.vision
for each new listing's first three months → data_f1/daily_hl.parquet (symbol, date, high, low).

Usage: python user_data/bench/hl_fetch.py
"""

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from fetch_vision import months, read_csv  # noqa: E402
from f1_carry import load  # noqa: E402
from l1_listings import events  # noqa: E402
from p1_fetch import BASE, fetch  # noqa: E402

ROOT = Path("user_data/data_f1")
RAW = ROOT / "raw" / "1d"


def main():
    close, _, _, _ = load()
    evs = events(close)
    jobs = []
    for d0, s in evs:
        for m in months(d0.strftime("%Y-%m"), (d0 + pd.DateOffset(months=2)).strftime("%Y-%m")):
            if m <= "2026-08":
                jobs.append((f"{BASE}/monthly/klines/{s}/1d/{s}-1d-{m}.zip", RAW / s / f"{s}-1d-{m}.csv"))
    print(f"{len(evs)} events, {len(jobs)} archives", flush=True)
    with ThreadPoolExecutor(16) as ex:
        list(ex.map(lambda j: fetch(*j), jobs))
    frames = []
    for d in sorted(RAW.iterdir()):
        files = sorted(d.glob("*.csv"))
        if files:
            df = pd.concat([read_csv(f) for f in files])[["date", "high", "low", "close"]]
            df["symbol"] = d.name
            frames.append(df)
    out = pd.concat(frames).drop_duplicates(["symbol", "date"]).sort_values(["symbol", "date"])
    out.to_parquet(ROOT / "daily_hl.parquet")
    chk = out.merge(close.stack().rename("c0").reset_index().rename(columns={"level_1": "symbol"}), on=["date", "symbol"])
    print(f"daily_hl: {out.symbol.nunique()} symbols, {len(out)} rows; close matches daily.parquet: "
          f"{(abs(chk.close / chk.c0 - 1) < 1e-9).mean() * 100:.2f}%", flush=True)


if __name__ == "__main__":
    main()
