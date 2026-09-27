"""Build event datasets for the listing and unlock studies.

  listings : every Binance USDT perpetual with its onboard date (incl. delisted ones), plus daily
             futures klines from data.binance.vision for the first 180 days after listing.
  unlocks  : every cliff unlock event in DefiLlama's public emissions datasets, with its size relative
             to the tokens already unlocked at that moment, mapped to a Binance perp symbol when one exists.

Outputs (user_data/bench/events/):
  listings.parquet        symbol, onboard, status
  listing_klines/<SYM>.parquet   daily OHLCV (futures), first 180 days
  unlocks.parquet         protocol, name, symbol_guess, ts, tokens, category, unlocked_before, pct_of_unlocked
  unlock_klines/<SYM>.parquet    daily OHLCV (futures) ±60 days around each mapped unlock

Usage: python user_data/bench/fetch_events.py listings|unlocks
"""

import io
import json
import sys
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

OUT = Path("user_data/bench/events")
VISION = "https://data.binance.vision/data/futures/um/monthly/klines/{s}/1d/{s}-1d-{y}-{m:02d}.zip"
LLAMA_LIST = "https://defillama-datasets.llama.fi/emissionsProtocolsList"
LLAMA_ONE = "https://defillama-datasets.llama.fi/emissions/{p}"
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trades",
        "taker_base", "taker_quote", "ignore"]


def get(url: str, retries: int = 5, **kw):
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=30, **kw)
            if r.status_code in (200, 404):
                return r
        except requests.exceptions.RequestException:
            pass
        time.sleep(2 * (attempt + 1))
    return None


def monthly_klines(symbol: str, start: date, days: int) -> pd.DataFrame:
    frames = []
    cur = date(start.year, start.month, 1)
    end = start + timedelta(days=days)
    while cur <= end:
        r = get(VISION.format(s=symbol, y=cur.year, m=cur.month))
        if r is not None and r.status_code == 200:
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                raw = z.read(z.namelist()[0]).decode()
            df = pd.read_csv(io.StringIO(raw), header=None)
            if not str(df.iloc[0, 0]).isdigit():  # some archives carry a header row
                df = df.iloc[1:]
            df.columns = COLS
            frames.append(df)
        cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames).astype({"open_time": "int64"})
    df["date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[c] = df[c].astype(float)
    df = df[["date", "open", "high", "low", "close", "volume", "quote_volume"]].drop_duplicates("date")
    return df[(df["date"].dt.date >= start) & (df["date"].dt.date <= end)].reset_index(drop=True)


def listings() -> None:
    info = get("https://fapi.binance.com/fapi/v1/exchangeInfo").json()
    rows = [
        {"symbol": s["symbol"], "onboard": datetime.fromtimestamp(s["onboardDate"] / 1000, timezone.utc).date(),
         "status": s["status"]}
        for s in info["symbols"] if s["contractType"] == "PERPETUAL" and s["quoteAsset"] == "USDT"
    ]
    df = pd.DataFrame(rows).sort_values("onboard")
    (OUT / "listing_klines").mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT / "listings.parquet")
    todo = df[(df["onboard"] >= date(2023, 1, 1))]
    print(f"{len(df)} perps, fetching klines for {len(todo)} listed since 2023", flush=True)
    for i, row in enumerate(todo.itertuples(), 1):
        f = OUT / "listing_klines" / f"{row.symbol}.parquet"
        if f.exists():
            continue
        k = monthly_klines(row.symbol, row.onboard, 180)
        if not k.empty:
            k.to_parquet(f)
        print(f"{i:4d}/{len(todo)} {row.symbol} {row.onboard} {len(k)} days", flush=True)


def unlocks() -> None:
    protocols = get(LLAMA_LIST).json()
    perps = pd.read_parquet(OUT / "listings.parquet") if (OUT / "listings.parquet").exists() else None
    perp_bases = {}
    if perps is not None:
        for s in perps["symbol"]:
            base = s[:-4]
            perp_bases[base.lstrip("1000").lstrip("1M")] = s
            perp_bases[base] = s
    events = []
    for i, p in enumerate(protocols, 1):
        r = get(LLAMA_ONE.format(p=p))
        if r is None or r.status_code != 200:
            continue
        try:
            d = r.json()
        except ValueError:
            continue
        # cumulative unlocked (all allocations) by day, to size each event relative to what is already out
        series = {}
        for alloc in d.get("documentedData", {}).get("data", []):
            for pt in alloc.get("data", []):
                t = int(float(pt["timestamp"]))
                series[t] = series.get(t, 0) + float(pt.get("unlocked") or 0)
        cum = pd.Series(series).sort_index()
        sym = None
        token = (d.get("metadata") or {}).get("token") or ""
        name = d.get("name") or p
        for ev in (d.get("metadata") or {}).get("events", []):
            if ev.get("unlockType") != "cliff":
                continue
            try:
                ts = int(float(ev.get("timestamp")))
            except (TypeError, ValueError):
                continue
            n = sum(float(x) for x in (ev.get("noOfTokens") or []) if isinstance(x, (int, float, str)) and str(x).replace(".", "", 1).isdigit())
            if ts <= 0 or n <= 0:
                continue
            before = cum[cum.index < ts - 86400]
            unlocked_before = float(before.iloc[-1]) if len(before) else 0.0
            events.append({
                "protocol": p, "name": name, "token": token, "gecko_id": d.get("gecko_id"),
                "ts": datetime.fromtimestamp(ts, timezone.utc), "tokens": n,
                "category": ev.get("category"), "unlocked_before": unlocked_before,
                "pct_of_unlocked": n / unlocked_before if unlocked_before > 0 else None,
            })
        print(f"{i:4d}/{len(protocols)} {p}: {sum(1 for e in events if e['protocol'] == p)} cliff events", flush=True)
        time.sleep(0.1)
    df = pd.DataFrame(events)
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT / "unlocks.parquet")
    print(f"saved {len(df)} cliff events from {df['protocol'].nunique()} protocols", flush=True)


if __name__ == "__main__":
    {"listings": listings, "unlocks": unlocks}[sys.argv[1]]()
