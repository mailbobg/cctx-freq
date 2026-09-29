"""Funding history for every USDT-M perpetual (specs F1 and L1): monthly fundingRate archives from data.binance.vision,
2020-12 → 2026-08, for every eligible symbol in data_p1/daily.parquet → data_f1/funding.parquet (symbol, time, rate).

Usage: python user_data/bench/fund_fetch.py
"""

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from fetch_vision import months  # noqa: E402
from p1_fetch import BASE, eligible, fetch  # noqa: E402

ROOT = Path("user_data/data_f1")
RAW = ROOT / "raw" / "funding"
LAST = "2026-08"


def main():
    daily = pd.read_parquet("user_data/data_p1/daily.parquet")
    span = daily.groupby("symbol")["date"].agg(["min", "max"])
    jobs = []
    for s, (a, b) in span.iterrows():
        if not eligible(s):
            continue
        m0 = max(a.strftime("%Y-%m"), "2020-12")
        m1 = min(b.strftime("%Y-%m"), LAST)
        if m0 > m1:
            continue
        for m in months(m0, m1):
            jobs.append((f"{BASE}/monthly/fundingRate/{s}/{s}-fundingRate-{m}.zip", RAW / s / f"{s}-fundingRate-{m}.csv"))
    print(f"{span.shape[0]} symbols, {len(jobs)} archives", flush=True)
    with ThreadPoolExecutor(16) as ex:
        for i, _ in enumerate(ex.map(lambda j: fetch(*j), jobs), 1):
            if i % 2000 == 0:
                print(f"  {i}/{len(jobs)}", flush=True)
    fund = []
    for d in sorted(RAW.iterdir()):
        files = sorted(d.glob("*.csv"))
        if not files:
            continue
        f = pd.concat([pd.read_csv(x) for x in files])
        f = f[pd.to_numeric(f.iloc[:, 0], errors="coerce").notna()]
        t = pd.to_numeric(f.iloc[:, 0]).astype("int64")
        unit = "us" if t.iloc[0] > 10**14 else "ms"
        fund.append(pd.DataFrame({"symbol": d.name, "time": pd.to_datetime(t, unit=unit, utc=True),
                                  "rate": f.iloc[:, 2].astype(float).to_numpy()}))
    out = pd.concat(fund)
    out["time"] = out["time"].dt.floor("h")            # archive stamps carry ms offsets
    out = out.drop_duplicates(["symbol", "time"]).sort_values(["symbol", "time"]).reset_index(drop=True)
    out.to_parquet(ROOT / "funding.parquet")
    print(f"funding: {out.symbol.nunique()} symbols, {len(out)} rows, {out.time.min()} → {out.time.max()}", flush=True)


if __name__ == "__main__":
    main()
