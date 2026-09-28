"""Data for X14 (spec: specs/2026-09-28-x14-hedged-flush-design.md).

  universe  every Monday 2021-01-04 → 2026-09-21: top-60 USDT-M perps by 30-day quote volume (BTC, stablecoins and
            non-crypto perps excluded), ≥ 30 days of history → data_x14/universe.json   (reuses P1's daily.parquet)
  fetch     5m klines for the union + BTC from data.binance.vision (monthly to 2026-08, daily for 2026-09-01..23),
            converted per symbol to data_x14/5m/<sym>.parquet (date, open, close as float32); CSVs deleted after use

Usage: python user_data/bench/x14_fetch.py universe|fetch|all
"""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from fetch_vision import months, read_csv  # noqa: E402
from p1_fetch import BASE, END, MONDAYS, RAW as P1_RAW, ROOT as P1_ROOT, fetch, non_crypto  # noqa: E402

ROOT = Path("user_data/data_x14")
RAW = ROOT / "raw"
TOP_N, MIN_HISTORY_DAYS, MIN_DAYS_IN_WINDOW = 60, 30, 25


def step_universe() -> None:
    daily = pd.read_parquet(P1_ROOT / "daily.parquet")
    drop = non_crypto() | {"BTCUSDT"}
    daily = daily[~daily.symbol.isin(drop)]
    first = daily.groupby("symbol")["date"].min()
    qv = daily.pivot(index="date", columns="symbol", values="quote_volume")
    universe = {}
    for t in MONDAYS:
        win = qv[(qv.index >= t - pd.Timedelta(days=30)) & (qv.index < t)]
        ok = (win.notna().sum() >= MIN_DAYS_IN_WINDOW) & (first.reindex(win.columns) <= t - pd.Timedelta(days=MIN_HISTORY_DAYS))
        ranked = win.sum().where(ok).dropna().sort_values(ascending=False)
        key = t.strftime("%Y-%m-%d")
        if len(ranked) < TOP_N and universe and win.notna().sum().max() < MIN_DAYS_IN_WINDOW:
            universe[key] = list(universe.values())[-1]        # September: daily archives end 2026-08-31
            continue
        universe[key] = list(ranked.index[:TOP_N])
    union = sorted({s for v in universe.values() for s in v})
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "universe.json").write_text(json.dumps({"mondays": universe, "union": union}, indent=1))
    sizes = pd.Series({k: len(v) for k, v in universe.items()})
    print(f"universe: {len(universe)} Mondays, {sizes.min()}–{sizes.max()} symbols, union {len(union)}", flush=True)


def convert(sym: str) -> None:
    files = sorted((RAW / sym).glob("*.csv"))
    if not files:
        print("no 5m data:", sym, flush=True)
        return
    df = pd.concat([read_csv(f) for f in files]).drop_duplicates("date").sort_values("date")
    df = df[df["date"] <= END][["date", "open", "close"]]
    df["open"] = df["open"].astype(np.float32)
    df["close"] = df["close"].astype(np.float32)
    df.reset_index(drop=True).to_parquet(ROOT / "5m" / f"{sym}.parquet")
    for f in files:
        f.unlink()


def step_fetch() -> None:
    uni = json.loads((ROOT / "universe.json").read_text())
    first_seen = {"BTCUSDT": "2020-11-01"}
    for monday, syms in uni["mondays"].items():
        for s in syms:
            first_seen.setdefault(s, monday)
    (ROOT / "5m").mkdir(parents=True, exist_ok=True)
    jobs = []
    for s, monday in first_seen.items():
        if (ROOT / "5m" / f"{s}.parquet").exists():
            continue
        start = (pd.Timestamp(monday) - pd.DateOffset(months=1)).strftime("%Y-%m")
        for m in months(max(start, "2020-10"), "2026-08"):
            jobs.append((f"{BASE}/monthly/klines/{s}/5m/{s}-5m-{m}.zip", RAW / s / f"{s}-5m-{m}.csv"))
        for d in pd.date_range("2026-09-01", "2026-09-23"):
            day = d.strftime("%Y-%m-%d")
            jobs.append((f"{BASE}/daily/klines/{s}/5m/{s}-5m-{day}.zip", RAW / s / f"{s}-5m-{day}.csv"))
    print(f"{len(first_seen)} symbols, {len(jobs)} archives to fetch", flush=True)
    with ThreadPoolExecutor(12) as ex:
        for i, _ in enumerate(ex.map(lambda j: fetch(*j), jobs), 1):
            if i % 500 == 0:
                print(f"  {i}/{len(jobs)}", flush=True)
    for s in first_seen:
        if not (ROOT / "5m" / f"{s}.parquet").exists():
            convert(s)
    print(f"5m parquet files: {len(list((ROOT / '5m').glob('*.parquet')))}", flush=True)


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else "all"
    if step in ("universe", "all"):
        step_universe()
    if step in ("fetch", "all"):
        step_fetch()
