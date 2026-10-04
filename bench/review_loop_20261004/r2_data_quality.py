"""Read-only interior candle coverage audit for the frozen 2024 R2 inputs."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
START = pd.Timestamp("2024-01-01", tz="UTC")
END = pd.Timestamp("2025-01-01", tz="UTC")
STEPS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


def inspect(row):
    path = ROOT / row["path"]
    timeframe = path.stem.rsplit("-", 1)[1]
    step = pd.Timedelta(seconds=STEPS[timeframe])
    dates = pd.read_feather(path, columns=["date"])["date"]
    active = dates[(dates >= START) & (dates < END)]
    result = {"path": row["path"], "timeframe": timeframe,
              "all_dates_monotonic": bool(dates.is_monotonic_increasing),
              "duplicate_dates": int(dates.duplicated().sum()),
              "active_rows": len(active)}
    if active.empty:
        return {**result, "no_candles_in_window": True}
    diffs = active.diff().dropna()
    gaps = diffs[diffs > step]
    result.update(first=str(active.iloc[0]), last=str(active.iloc[-1]),
                  late_start=bool(active.iloc[0] > START),
                  early_end=bool(active.iloc[-1] < END - step),
                  interior_missing_candles=int(((gaps / step) - 1).sum()),
                  gap_intervals=len(gaps),
                  maximum_gap_seconds=float(gaps.max().total_seconds()) if len(gaps) else 0.0,
                  misaligned_intervals=int(((diffs / step) % 1 != 0).sum()))
    return result


def main():
    manifest = json.loads((HERE / "r2_data_manifest.json").read_text())
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(inspect, manifest["files"]))
    output = {"captured_utc": datetime.now(timezone.utc).isoformat(),
              "window": [str(START), str(END)], "files": rows,
              "limitation": "A late start/end can be a listing, delisting or missing data; this audit does not infer which. Internal missing timestamps remain missing observations, even if the backtester fills flat candles. This does not prove point-in-time universe/source availability."}
    (HERE / "r2_data_quality.json").write_text(json.dumps(output, indent=2) + "\n")
    for tf in STEPS:
        group = [r for r in rows if r["timeframe"] == tf]
        print(tf, "files", len(group), "with_internal_gaps",
              sum(r.get("gap_intervals", 0) > 0 for r in group), "missing_internal_candles",
              sum(r.get("interior_missing_candles", 0) for r in group), "late_starts",
              sum(r.get("late_start", False) for r in group), "early_ends",
              sum(r.get("early_end", False) for r in group), flush=True)
    daily_bad = [r for r in rows if r["timeframe"] == "1d" and
                 (r.get("gap_intervals", 0) or r.get("early_end") or r.get("no_candles_in_window"))]
    print("daily_prices_requiring_trade-date_check", [r["path"] for r in daily_bad], flush=True)


if __name__ == "__main__":
    main()
