"""5m perpetual data for X14-V (spec: specs/2026-09-28-x14v-venue-replication.md) from Bybit or Bitget public APIs.

Output has the same layout as data_x14/ so bench/x14_flush.py runs unchanged (X14_DATA=user_data/data_x14_<venue>):
  daily.parquet          symbol, date, close, quote_volume (all USDT perps, for the point-in-time universe)
  universe.json          Mondays 2022-01-03 → 2026-09-21: top-60 by 30-day quote volume, ≥ 30 days history,
                         BTC / stablecoins / non-crypto bases excluded
  5m/<SYMBOL>.parquet    date, open, close (float32), 2021-12 → 2026-09-23, union of the universe + BTCUSDT
  funding.parquet        symbol, time, rate

Resumable: every API page is cached under raw/ as JSON. Usage:
  python user_data/bench/venue_fetch.py bybit|bitget daily|universe|fivemin|funding|convert|all
"""

import json
import os
import sys
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests

VENUE = sys.argv[1] if len(sys.argv) > 1 else "bybit"
ROOT = Path(f"user_data/data_{'x14_' + VENUE}")
RAW = ROOT / "raw"
MONDAYS = pd.date_range("2022-01-03", "2026-09-21", freq="W-MON", tz="UTC")
START = pd.Timestamp("2021-12-01", tz="UTC")
END = pd.Timestamp("2026-09-23", tz="UTC")
TOP_N, MIN_HISTORY_DAYS, MIN_DAYS_IN_WINDOW = 60, 30, 25
STABLE = {"USDC", "BUSD", "TUSD", "USDP", "DAI", "FDUSD", "USD1", "USDE", "UST", "USTC", "EUR", "GBP", "AUD"}
# tokenized stocks/commodities/FX seen on Binance (and any base ending in these venue tags)
NON_CRYPTO_HINT = {"XAU", "XAG", "CL", "BZ", "QQQ", "SPY", "MSTR", "TSLA", "NVDA", "AAPL", "COIN", "HOOD", "SNDK",
                   "SKHYNIX", "SOXL", "SPCX", "DRAM", "MU", "EWY", "GOOGL", "META", "AMZN", "MSFT", "CRCL", "NFLX"}
session = requests.Session()
session.headers["User-Agent"] = "ccxt-fr-research/0.1"
MS5 = 5 * 60 * 1000


def get(url: str, params: dict, tries: int = 6):
    for i in range(tries):
        try:
            r = session.get(url, params=params, timeout=30)
            if r.status_code == 429 or r.status_code >= 500:
                raise RuntimeError(f"http {r.status_code}")
            r.raise_for_status()
            d = r.json()
            # venue-level error codes (rate limits, invalid symbol) come back as HTTP 200
            code = d.get("retCode", d.get("code", 0))
            if str(code) not in ("0", "00000"):
                msg = d.get("retMsg") or d.get("msg") or ""
                if "rate" in msg.lower() or str(code) in ("10006", "10016", "429", "30001", "30002"):
                    raise RuntimeError(f"rate limited: {code} {msg}")
                return {"result": {"list": []}, "data": [], "_error": f"{code} {msg}"}
            return d
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def cached(key: Path, fn):
    if key.exists():
        return json.loads(key.read_text())
    out = fn()
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text(json.dumps(out))
    return out


# ------------------------------------------------------------------ venue adapters

class Bybit:
    base = "https://api.bybit.com"
    workers = 8

    def symbols(self) -> list[str]:
        d = get(f"{self.base}/v5/market/instruments-info", {"category": "linear", "limit": 1000})
        return sorted(x["symbol"] for x in d["result"]["list"]
                      if x["quoteCoin"] == "USDT" and x["contractType"] == "LinearPerpetual")

    def klines(self, sym: str, interval: str, start_ms: int, end_ms: int) -> list[list]:
        """Ascending [t, o, h, l, c, v, qv] over [start, end]. Bybit returns the NEWEST `limit` rows inside the
        window, so page backwards with `end` until the window is covered."""
        out, end = [], end_ms
        while end > start_ms:
            d = get(f"{self.base}/v5/market/kline", {"category": "linear", "symbol": sym, "interval": interval,
                                                      "start": start_ms, "end": end, "limit": 1000})
            rows = d["result"].get("list") or []
            if not rows:
                break
            out += [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), float(r[6])] for r in rows]
            oldest = min(int(r[0]) for r in rows)
            if oldest <= start_ms or len(rows) < 1000:
                break
            end = oldest - 1
        return sorted({r[0]: r for r in out}.values(), key=lambda r: r[0])

    def funding(self, sym: str, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
        out, end = [], end_ms
        while True:
            d = get(f"{self.base}/v5/market/funding/history", {"category": "linear", "symbol": sym,
                                                                "startTime": start_ms, "endTime": end, "limit": 200})
            rows = d["result"]["list"]
            if not rows:
                break
            out += [(int(r["fundingRateTimestamp"]), float(r["fundingRate"])) for r in rows]
            oldest = min(int(r["fundingRateTimestamp"]) for r in rows)
            if len(rows) < 200 or oldest <= start_ms:
                break
            end = oldest - 1
        return sorted(set(out))


class Bitget:
    base = "https://api.bitget.com"
    workers = 6
    workers_5m = 20                 # public limit 20 req/s per IP; each 5m page is 200 candles

    def symbols(self) -> list[str]:
        d = get(f"{self.base}/api/v2/mix/market/contracts", {"productType": "usdt-futures"})
        return sorted(x["symbol"] for x in d["data"] if x["symbol"].endswith("USDT"))

    def klines(self, sym: str, interval: str, start_ms: int, end_ms: int) -> list[list]:
        """Ascending rows by paging BACKWARDS with endTime (history-candles returns ≤ 200 rows ending at endTime)."""
        gran = {"5": "5m", "D": "1D"}[interval]
        out, end = [], end_ms
        while end > start_ms:
            d = get(f"{self.base}/api/v2/mix/market/history-candles",
                    {"symbol": sym, "productType": "usdt-futures", "granularity": gran, "endTime": end, "limit": 200})
            rows = d.get("data") or []
            if not rows:
                break
            out += [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), float(r[6])] for r in rows]
            oldest = min(int(r[0]) for r in rows)
            if oldest <= start_ms or len(rows) < 2:
                break
            end = oldest - 1
        out = [r for r in out if start_ms <= r[0] <= end_ms]
        return sorted({r[0]: r for r in out}.values(), key=lambda r: r[0])

    def funding(self, sym: str, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
        out, page = [], 1
        while True:
            d = get(f"{self.base}/api/v2/mix/market/history-fund-rate",
                    {"symbol": sym, "productType": "usdt-futures", "pageSize": 100, "pageNo": page})
            rows = d.get("data") or []
            if not rows:
                break
            out += [(int(r["fundingTime"]), float(r["fundingRate"])) for r in rows]
            if min(int(r["fundingTime"]) for r in rows) < start_ms or len(rows) < 100:
                break
            page += 1
        return sorted({t: (t, x) for t, x in out if start_ms <= t <= end_ms}.values())


ADAPTERS = {"bybit": Bybit, "bitget": Bitget}


# ------------------------------------------------------------------ steps

def eligible(sym: str) -> bool:
    base = sym[:-4]
    for pre in ("1000000", "100000", "10000", "1000", "1M", "1MILLION"):
        if base.startswith(pre) and base[len(pre):].isalpha():
            base = base[len(pre):]
            break
    return base not in STABLE and base not in NON_CRYPTO_HINT and "PERP" not in base


def step_daily(v) -> None:
    syms = [s for s in v.symbols() if eligible(s)]
    print(f"{VENUE}: {len(syms)} USDT perps", flush=True)
    s0, e0 = int(pd.Timestamp("2021-10-01", tz="UTC").timestamp() * 1000), int(END.timestamp() * 1000)

    def one(sym):
        rows = cached(RAW / "daily" / f"{sym}.json", lambda: v.klines(sym, "D", s0, e0))
        return [(sym, r[0], r[4], r[6]) for r in rows]

    recs = []
    with ThreadPoolExecutor(v.workers) as ex:
        for i, rows in enumerate(ex.map(one, syms), 1):
            recs += rows
            if i % 100 == 0:
                print(f"  daily {i}/{len(syms)}", flush=True)
    daily = pd.DataFrame(recs, columns=["symbol", "t", "close", "quote_volume"])
    daily["date"] = pd.to_datetime(daily["t"], unit="ms", utc=True).dt.floor("D")
    daily = daily.drop(columns="t").drop_duplicates(["symbol", "date"]).sort_values(["symbol", "date"])
    daily.to_parquet(ROOT / "daily.parquet")
    print(f"daily.parquet: {daily.symbol.nunique()} symbols, {len(daily)} rows", flush=True)


def step_universe() -> None:
    daily = pd.read_parquet(ROOT / "daily.parquet")
    daily = daily[daily.symbol != "BTCUSDT"]
    first = daily.groupby("symbol")["date"].min()
    qv = daily.pivot(index="date", columns="symbol", values="quote_volume")
    universe = {}
    for t in MONDAYS:
        win = qv[(qv.index >= t - pd.Timedelta(days=30)) & (qv.index < t)]
        ok = (win.notna().sum() >= MIN_DAYS_IN_WINDOW) & (first.reindex(win.columns) <= t - pd.Timedelta(days=MIN_HISTORY_DAYS))
        ranked = win.sum().where(ok).dropna().sort_values(ascending=False)
        universe[t.strftime("%Y-%m-%d")] = list(ranked.index[:TOP_N])
    union = sorted({s for v in universe.values() for s in v})
    (ROOT / "universe.json").write_text(json.dumps({"mondays": universe, "union": union}, indent=1))
    sizes = pd.Series({k: len(v) for k, v in universe.items()})
    print(f"universe: {len(universe)} Mondays, {sizes.min()}–{sizes.max()} symbols, union {len(union)}", flush=True)


def _quarters(s0: int, e0: int):
    q = pd.Timestamp(s0, unit="ms", tz="UTC").tz_localize(None).to_period("Q")
    while q.start_time.tz_localize("UTC") <= END:
        a = max(s0, int(q.start_time.tz_localize("UTC").timestamp() * 1000))
        b = min(e0, int(q.end_time.tz_localize("UTC").timestamp() * 1000))
        if a <= b:
            yield q, a, b
        q += 1


def _convert(sym: str, rows: list) -> int:
    if not rows:
        print("no 5m data:", sym, flush=True)
        return 0
    df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume", "quote_volume"])
    df["date"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    df = df[(df["t"] % MS5 == 0)].drop_duplicates("date").sort_values("date")
    out = df[["date", "open", "close"]].copy()
    out["open"] = out["open"].astype(np.float32)
    out["close"] = out["close"].astype(np.float32)
    out.reset_index(drop=True).to_parquet(ROOT / "5m" / f"{sym}.parquet")
    return len(out)


def step_fivemin(v, newest_first: bool = True) -> None:
    """Fetch (symbol, quarter) jobs in parallel — newest quarters first, so a partial run already covers 2025–26 —
    then convert each symbol from its cached quarters."""
    uni = json.loads((ROOT / "universe.json").read_text())
    first_seen = {"BTCUSDT": "2021-12-01"}
    for monday, syms in uni["mondays"].items():
        for s in syms:
            first_seen.setdefault(s, monday)
    (ROOT / "5m").mkdir(parents=True, exist_ok=True)
    e0 = int(END.timestamp() * 1000)
    jobs = []
    for sym, monday in first_seen.items():
        if (ROOT / "5m" / f"{sym}.parquet").exists():
            continue
        s0 = int(max(START, pd.Timestamp(monday, tz="UTC") - pd.DateOffset(months=1)).timestamp() * 1000)
        for q, a, b in _quarters(s0, e0):
            key = RAW / "5m" / sym / f"{q}.json"
            if not key.exists():
                jobs.append((str(q), sym, a, b, key))
    shard = os.environ.get("VENUE_SHARD")            # "k/n": this process takes symbols with crc32(sym) % n == k
    if shard:
        k, n = map(int, shard.split("/"))
        jobs = [j for j in jobs if zlib.crc32(j[1].encode()) % n == k]
    jobs.sort(key=lambda j: j[0], reverse=newest_first)
    print(f"{VENUE}: {len(first_seen)} symbols, {len(jobs)} quarter jobs to fetch{' (shard ' + shard + ')' if shard else ''}", flush=True)

    def one(job):
        _, sym, a, b, key = job
        cached(key, lambda: v.klines(sym, "5", a, b))
        return sym

    with ThreadPoolExecutor(getattr(v, "workers_5m", v.workers)) as ex:
        for i, sym in enumerate(ex.map(one, jobs), 1):
            if i % 200 == 0:
                print(f"  5m quarters {i}/{len(jobs)} ({sym})", flush=True)
    if not shard:
        step_convert(first_seen)


def step_convert(first_seen: dict | None = None) -> None:
    """Build 5m/<sym>.parquet from whatever quarters are cached (also usable for a partial data set)."""
    if first_seen is None:
        uni = json.loads((ROOT / "universe.json").read_text())
        first_seen = {"BTCUSDT": "2021-12-01"}
        for monday, syms in uni["mondays"].items():
            for s in syms:
                first_seen.setdefault(s, monday)
    (ROOT / "5m").mkdir(parents=True, exist_ok=True)
    n = 0
    for sym in first_seen:
        files = sorted((RAW / "5m" / sym).glob("*.json"))
        rows = []
        for f in files:
            rows += json.loads(f.read_text())
        if _convert(sym, rows):
            n += 1
    print(f"5m parquet files: {n}", flush=True)


def step_funding(v) -> None:
    syms = [p.stem for p in (ROOT / "5m").glob("*.parquet")]
    s0, e0 = int(START.timestamp() * 1000), int(END.timestamp() * 1000)

    def one(sym):
        return sym, cached(RAW / "funding" / f"{sym}.json", lambda: v.funding(sym, s0, e0))

    recs = []
    with ThreadPoolExecutor(v.workers) as ex:
        for sym, rows in ex.map(one, syms):
            recs += [(sym, t, r) for t, r in rows]
    f = pd.DataFrame(recs, columns=["symbol", "t", "rate"])
    f["time"] = pd.to_datetime(f["t"], unit="ms", utc=True)
    f = f.drop(columns="t").sort_values(["symbol", "time"])
    f.to_parquet(ROOT / "funding.parquet")
    print(f"funding: {f.symbol.nunique()} symbols, {len(f)} rows, {f.time.min()} -> {f.time.max()}", flush=True)


if __name__ == "__main__":
    v = ADAPTERS[VENUE]()
    step = sys.argv[2] if len(sys.argv) > 2 else "all"
    ROOT.mkdir(parents=True, exist_ok=True)
    if step in ("daily", "all"):
        step_daily(v)
    if step in ("universe", "all"):
        step_universe()
    if step in ("fivemin", "all"):
        step_fivemin(v)
    if step == "convert":
        step_convert()
    if step in ("funding", "all"):
        step_funding(v)
