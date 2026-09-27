"""Build the 2021–2023 holdout data set from data.binance.vision (spot, delisted pairs included).

Spec: user_data/bench/specs/2026-09-26-holdout-2021-2023.md

Steps
  1. list every spot symbol on data.binance.vision, keep */USDT minus stablecoins and leveraged tokens
  2. rank by quote volume over Q4 of 2020 / 2021 / 2022 (1d archives) → top 120 per year
  3. download 5m archives for each year's universe from Oct of the previous year to Dec of the year
  4. write freqtrade feather (5m + resampled 15m/1h/4h/1d), applying the symbol-reuse gap rule

Usage: python user_data/bench/fetch_vision.py [rank|download|convert|all]
"""

import io
import json
import re
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests

BASE = "https://data.binance.vision/data/spot/monthly/klines"
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
ROOT = Path("user_data/data_holdout")
RAW = ROOT / "raw"
OUT = ROOT / "binance"
UNIV = Path("user_data/bench/holdout_universe.json")
YEARS = [2021, 2022, 2023]
TOP_N = 120
STABLE = {"USDC", "BUSD", "TUSD", "PAX", "USDP", "DAI", "FDUSD", "UST", "USTC", "SUSD", "EUR", "GBP", "AUD"}
LEVERAGED = re.compile(r".+(UP|DOWN|BULL|BEAR)$")
ALWAYS = ["BTCUSDT", "ETHUSDT"]
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume",
        "trades", "taker_base", "taker_quote", "ignore"]
UA = {"User-Agent": "ccxt-fr-research/0.1"}
session = requests.Session()
session.headers.update(UA)


def list_symbols() -> list[str]:
    symbols, marker = [], ""
    while True:
        r = session.get(S3, params={"delimiter": "/", "prefix": "data/spot/monthly/klines/", "marker": marker},
                        timeout=30)
        r.raise_for_status()
        prefixes = re.findall(r"<Prefix>data/spot/monthly/klines/([^/<]+)/</Prefix>", r.text)
        symbols += prefixes
        if "<IsTruncated>true</IsTruncated>" not in r.text:
            return symbols
        marker = re.search(r"<NextMarker>([^<]+)</NextMarker>", r.text).group(1)


def keep(symbol: str) -> bool:
    if not symbol.endswith("USDT"):
        return False
    base = symbol[:-4]
    return base not in STABLE and not LEVERAGED.match(base)


def fetch(symbol: str, tf: str, month: str) -> Path | None:
    dest = RAW / tf / symbol / f"{symbol}-{tf}-{month}.csv"
    if dest.exists():
        return dest
    miss = dest.with_suffix(".missing")
    if miss.exists():
        return None
    url = f"{BASE}/{symbol}/{tf}/{symbol}-{tf}-{month}.zip"
    for attempt in range(5):
        try:
            r = session.get(url, timeout=60)
            if r.status_code == 404:
                miss.parent.mkdir(parents=True, exist_ok=True)
                miss.touch()
                return None
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                data = z.read(z.namelist()[0])
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            return dest
        except Exception as e:  # noqa: BLE001
            if attempt == 4:
                print(f"FAIL {url}: {e}", flush=True)
                return None
            time.sleep(2 * (attempt + 1))
    return None


def read_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, header=None, names=COLS)
    if not str(df.iloc[0, 0]).isdigit():  # some archives carry a header row
        df = df.iloc[1:]
    df = df.astype({c: float for c in COLS[1:6] + ["quote_volume"]}).astype({"open_time": "int64"})
    unit = "us" if df["open_time"].iloc[0] > 10**14 else "ms"
    df["date"] = pd.to_datetime(df["open_time"], unit=unit, utc=True)
    return df


def months(start: str, end: str) -> list[str]:
    return [p.strftime("%Y-%m") for p in pd.period_range(start, end, freq="M")]


def rank() -> None:
    symbols = [s for s in list_symbols() if keep(s)]
    print(f"{len(symbols)} USDT symbols on data.binance.vision after filters", flush=True)
    jobs = [(s, m) for s in symbols for y in YEARS for m in months(f"{y - 1}-10", f"{y - 1}-12")]
    with ThreadPoolExecutor(16) as ex:
        list(ex.map(lambda a: fetch(a[0], "1d", a[1]), jobs))
    universe = {}
    for y in YEARS:
        qv, days = {}, {}
        for s in symbols:
            parts = [read_csv(p) for m in months(f"{y - 1}-10", f"{y - 1}-12")
                     if (p := RAW / "1d" / s / f"{s}-1d-{m}.csv").exists()]
            if parts:
                d = pd.concat(parts)
                qv[s], days[s] = d["quote_volume"].sum(), len(d)
        ranked = sorted((s for s in qv if days[s] >= 60), key=lambda s: -qv[s])
        universe[str(y)] = ranked[:TOP_N]
        print(f"{y}: {len(ranked)} eligible, top: {ranked[:8]}", flush=True)
    UNIV.write_text(json.dumps(universe, indent=1))


def download() -> None:
    universe = json.loads(UNIV.read_text())
    # one continuous month range per symbol, so a gap in the data is a real gap, not a year we skipped
    span = {}
    for y, syms in universe.items():
        y = int(y)
        for s in set(syms) | set(ALWAYS):
            lo, hi = span.get(s, (f"{y - 1}-10", f"{y}-12"))
            span[s] = (min(lo, f"{y - 1}-10"), max(hi, f"{y}-12"))
    jobs = sorted((s, m) for s, (lo, hi) in span.items() for m in months(lo, hi))
    print(f"{len(jobs)} monthly 5m archives", flush=True)
    done = 0
    with ThreadPoolExecutor(16) as ex:
        for _ in ex.map(lambda a: fetch(a[0], "5m", a[1]), jobs):
            done += 1
            if done % 500 == 0:
                print(f"  {done}/{len(jobs)}", flush=True)


def apply_gap_rule(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    gaps = df["date"].diff() > pd.Timedelta(days=3)
    for i in df.index[gaps]:
        pos = df.index.get_loc(i)
        before, after = df["close"].iloc[pos - 1], df["open"].iloc[pos]
        ratio = after / before
        if ratio > 2 or ratio < 0.5:
            print(f"  {symbol}: symbol reuse at {df['date'].iloc[pos]} (×{ratio:.3g}) → cut", flush=True)
            return df.iloc[:pos]
    return df


def convert() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for sdir in sorted((RAW / "5m").iterdir()):
        s = sdir.name
        files = sorted(sdir.glob("*.csv"))
        if not files:
            continue
        df = pd.concat([read_csv(p) for p in files])
        df = df.drop_duplicates("date").sort_values("date").reset_index(drop=True)
        df = apply_gap_rule(df, s)
        df = df[["date", "open", "high", "low", "close", "volume"]]
        name = f"{s[:-4]}_USDT"
        df.reset_index(drop=True).to_feather(OUT / f"{name}-5m.feather")
        ix = df.set_index("date")
        for tf, rule in {"15m": "15min", "1h": "1h", "4h": "4h", "1d": "1D"}.items():
            r = ix.resample(rule, label="left", closed="left").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
            r.reset_index().to_feather(OUT / f"{name}-{tf}.feather")
    print(f"converted {len(list(OUT.glob('*-5m.feather')))} pairs", flush=True)


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else "all"
    if step in ("rank", "all"):
        rank()
    if step in ("download", "all"):
        download()
    if step in ("convert", "all"):
        convert()
