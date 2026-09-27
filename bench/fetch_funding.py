"""Download Binance USDT-perp funding-rate history into freqtrade's feather layout.

Binance's WAF returns 403 for /fapi/v1/fundingRate with limit=1000 (which ccxt uses),
so this pages through the endpoint with a smaller limit instead.

Usage: python user_data/bench/fetch_funding.py <pairs.json> [start=2023-10-01]
"""

import json
import sys
import time
from pathlib import Path

import pandas as pd
import requests

URL = "https://fapi.binance.com/fapi/v1/fundingRate"
OUT = Path("user_data/data/binance/futures")
PAGE = 500


def fetch(symbol: str, start_ms: int) -> pd.DataFrame:
    rows, page = [], PAGE
    while True:
        for attempt in range(6):
            try:
                r = requests.get(URL, params={"symbol": symbol, "startTime": start_ms, "limit": page}, timeout=20)
                break
            except requests.exceptions.RequestException:
                if attempt == 5:
                    raise
                time.sleep(2 * (attempt + 1))
        if r.status_code == 403 and page > 100:
            page = 100
            continue
        r.raise_for_status()
        data = r.json()
        rows.extend(data)
        if len(data) < page:
            break
        start_ms = data[-1]["fundingTime"] + 1
        time.sleep(0.25)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True).dt.floor("min")
    df["funding_rate"] = df["fundingRate"].astype(float)
    return df[["date", "funding_rate"]].drop_duplicates("date").sort_values("date").reset_index(drop=True)


def main(pairs_file: str, start: str = "2023-10-01") -> None:
    pairs = json.load(open(pairs_file))
    start_ms = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    for i, pair in enumerate(pairs, 1):
        symbol = pair.split(":")[0].replace("/", "")
        out = OUT / f"{pair.replace('/', '_').replace(':', '_')}-1h-funding_rate.feather"
        if out.exists():
            continue
        try:
            df = fetch(symbol, start_ms)
        except Exception as e:  # keep going, report at the end
            print(f"{i:3d} {pair}: FAILED {e}", flush=True)
            continue
        if df.empty:
            print(f"{i:3d} {pair}: no data", flush=True)
            continue
        df.to_feather(out)
        print(f"{i:3d} {pair}: {len(df)} rows {df['date'].iloc[0].date()} -> {df['date'].iloc[-1].date()}", flush=True)
        time.sleep(0.25)


if __name__ == "__main__":
    main(*sys.argv[1:])
