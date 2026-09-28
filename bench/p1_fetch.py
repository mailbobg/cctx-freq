"""Data for P1 pairs trading (spec: specs/2026-09-28-p1-crypto-pairs-design.md).

Steps (each resumable; raw archives cached under user_data/data_p1/raw/):
  daily     1d klines for every USDT-M perpetual on data.binance.vision → data_p1/daily.parquet
  universe  every Monday 2021-01-04 → 2026-09-21: top-20 by 30-day quote volume, ≥ 90 days of history,
            stablecoin/index products excluded → data_p1/universe.json (+ union of all selected symbols)
  hourly    1h klines + funding for the union, 2020-10 → 2026-09-23 → data_p1/hourly_{open,close}.parquet, funding.parquet
            (2026-09 klines from the daily archives; 2026-09 funding via the server's fapi access, since this Mac
            cannot reach fapi.binance.com)

Usage: python user_data/bench/p1_fetch.py daily|universe|hourly|all
"""

import io
import json
import re
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from fetch_vision import months, read_csv, session  # noqa: E402

S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
BASE = "https://data.binance.vision/data/futures/um"
ROOT = Path("user_data/data_p1")
RAW = ROOT / "raw"
STABLE = {"USDC", "BUSD", "TUSD", "PAX", "USDP", "DAI", "FDUSD", "USD1", "UST", "USTC", "SUSD", "EUR", "GBP", "AUD",
          "USDE", "FDUSDT", "USDT"}
INDEX = {"DEFI", "BTCDOM"}
MONDAYS = pd.date_range("2021-01-04", "2026-09-21", freq="W-MON", tz="UTC")
END = pd.Timestamp("2026-09-23", tz="UTC")
TOP_N, MIN_HISTORY_DAYS, MIN_DAYS_IN_WINDOW = 20, 90, 25
SERVER = "root@45.77.8.169"
SSH = ["ssh", "-i", str(Path("~/.ssh/ccxt_vps").expanduser()), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes"]


def s3_list(prefix: str, pattern: str) -> list[str]:
    out, marker = [], ""
    while True:
        for attempt in range(6):                     # this network drops TLS now and then
            try:
                r = session.get(S3, params={"delimiter": "/", "prefix": prefix, "marker": marker}, timeout=60)
                r.raise_for_status()
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 5:
                    print(f"LIST FAIL {prefix}: {e}", flush=True)
                    return out
                import time
                time.sleep(2 * (attempt + 1))
        out += re.findall(pattern, r.text)
        if "<IsTruncated>true</IsTruncated>" not in r.text:
            return out
        marker = re.search(r"<NextMarker>([^<]+)</NextMarker>", r.text).group(1)


def fetch(url: str, dest: Path) -> Path | None:
    if dest.exists():
        return dest
    if dest.with_suffix(".missing").exists():
        return None
    for attempt in range(5):
        try:
            r = session.get(url, timeout=120)
            if r.status_code == 404:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.with_suffix(".missing").touch()
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
    return None


def symbols() -> list[str]:
    syms = s3_list("data/futures/um/monthly/klines/", r"<Prefix>data/futures/um/monthly/klines/([^/<]+)/</Prefix>")
    return sorted(s for s in syms if s.endswith("USDT") and "_" not in s)


def eligible(sym: str) -> bool:
    base = sym[:-4]
    return base not in STABLE and base not in INDEX


def step_daily() -> None:
    syms = [s for s in symbols() if eligible(s)]
    print(f"{len(syms)} USDT-M perpetual symbols", flush=True)

    def sym_months(s):
        return s, s3_list(f"data/futures/um/monthly/klines/{s}/1d/", rf"<Key>[^<]*-1d-(\d{{4}}-\d{{2}})\.zip</Key>")

    with ThreadPoolExecutor(16) as ex:
        avail = dict(ex.map(sym_months, syms))
    jobs = [(f"{BASE}/monthly/klines/{s}/1d/{s}-1d-{m}.zip", RAW / "1d" / s / f"{s}-1d-{m}.csv")
            for s, ms in avail.items() for m in ms]
    print(f"{len(jobs)} monthly 1d archives", flush=True)
    with ThreadPoolExecutor(16) as ex:
        for i, _ in enumerate(ex.map(lambda j: fetch(*j), jobs), 1):
            if i % 2000 == 0:
                print(f"  {i}/{len(jobs)}", flush=True)
    frames = []
    for s in syms:
        files = sorted((RAW / "1d" / s).glob("*.csv"))
        if not files:
            continue
        df = pd.concat([read_csv(f) for f in files])[["date", "close", "quote_volume"]]
        df["symbol"] = s
        frames.append(df)
    daily = pd.concat(frames).drop_duplicates(["symbol", "date"]).sort_values(["symbol", "date"])
    daily.to_parquet(ROOT / "daily.parquet")
    print(f"daily.parquet: {daily.symbol.nunique()} symbols, {len(daily)} rows", flush=True)


def non_crypto() -> set[str]:
    """Perpetuals on tokenized stocks/commodities/FX/indexes, by Binance's underlyingType (exchangeInfo snapshot)."""
    f = ROOT / "exchangeInfo.json"
    if not f.exists():
        return set()
    return {s["symbol"] for s in json.loads(f.read_text())["symbols"] if s.get("underlyingType", "COIN") != "COIN"}


def step_universe() -> None:
    daily = pd.read_parquet(ROOT / "daily.parquet")
    drop = non_crypto()
    daily = daily[~daily.symbol.isin(drop)]
    print(f"excluded {len(drop)} non-crypto perpetuals (underlyingType != COIN)", flush=True)
    first = daily.groupby("symbol")["date"].min()
    qv = daily.pivot(index="date", columns="symbol", values="quote_volume")
    universe = {}
    for t in MONDAYS:
        win = qv[(qv.index >= t - pd.Timedelta(days=30)) & (qv.index < t)]
        ok = (win.notna().sum() >= MIN_DAYS_IN_WINDOW) & (first.reindex(win.columns) <= t - pd.Timedelta(days=MIN_HISTORY_DAYS))
        ranked = win.sum().where(ok).dropna().sort_values(ascending=False)
        key = t.strftime("%Y-%m-%d")
        if len(ranked) < TOP_N and universe and win.notna().sum().max() < MIN_DAYS_IN_WINDOW:
            # the monthly 1d archives end before this Monday (last weeks of the window): carry last week's list
            universe[key] = list(universe.values())[-1]
            print(f"  {key}: daily archives incomplete, carrying forward the previous universe", flush=True)
            continue
        universe[key] = list(ranked.index[:TOP_N])
    union = sorted({s for v in universe.values() for s in v})
    (ROOT / "universe.json").write_text(json.dumps({"mondays": universe, "union": union}, indent=1))
    sizes = pd.Series({k: len(v) for k, v in universe.items()})
    print(f"universe: {len(universe)} Mondays, {sizes.min()}–{sizes.max()} symbols each, union {len(union)}", flush=True)
    for k in list(universe)[::52]:
        print(" ", k, universe[k][:8], "…")


def funding_via_server(symbols: list[str], start: pd.Timestamp, end: pd.Timestamp) -> dict[str, pd.DataFrame]:
    """Funding rates from fapi through the server, all symbols in ONE ssh session (many quick ssh connections get
    throttled by sshd). Returns {symbol: DataFrame(time, rate)}; symbols with no data are simply absent."""
    a, b = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    script = ("for s in " + " ".join(symbols) + "; do printf '%s\\t' \"$s\"; "
              f"curl -s -m 30 -A 'Mozilla/5.0' 'https://fapi.binance.com/fapi/v1/fundingRate?symbol='\"$s\"'&startTime={a}&endTime={b}&limit=100'; "
              "echo; sleep 0.2; done")
    r = subprocess.run(SSH + [SERVER, script], capture_output=True, text=True, timeout=600)
    out = {}
    for line in r.stdout.splitlines():
        sym, _, body = line.partition("\t")
        try:
            rows = json.loads(body)
        except ValueError:
            continue
        if isinstance(rows, list) and rows:
            out[sym] = pd.DataFrame({"time": pd.to_datetime([x["fundingTime"] for x in rows], unit="ms", utc=True),
                                     "rate": [float(x["fundingRate"]) for x in rows]})
    return out


def step_hourly() -> None:
    uni = json.loads((ROOT / "universe.json").read_text())
    first_seen = {}
    for monday, syms in uni["mondays"].items():
        for s in syms:
            first_seen.setdefault(s, monday)
    jobs = []
    for s, monday in first_seen.items():
        start = (pd.Timestamp(monday) - pd.DateOffset(months=4)).strftime("%Y-%m")
        for m in months(max(start, "2020-10"), "2026-08"):
            jobs.append((f"{BASE}/monthly/klines/{s}/1h/{s}-1h-{m}.zip", RAW / "1h" / s / f"{s}-1h-{m}.csv"))
            jobs.append((f"{BASE}/monthly/fundingRate/{s}/{s}-fundingRate-{m}.zip", RAW / "funding" / s / f"{s}-fundingRate-{m}.csv"))
        for d in pd.date_range("2026-09-01", "2026-09-23"):
            day = d.strftime("%Y-%m-%d")
            jobs.append((f"{BASE}/daily/klines/{s}/1h/{s}-1h-{day}.zip", RAW / "1h" / s / f"{s}-1h-{day}.csv"))
    print(f"{len(first_seen)} symbols, {len(jobs)} archives", flush=True)
    with ThreadPoolExecutor(16) as ex:
        for i, _ in enumerate(ex.map(lambda j: fetch(*j), jobs), 1):
            if i % 1000 == 0:
                print(f"  {i}/{len(jobs)}", flush=True)

    opens, closes, qvs, fund = {}, {}, {}, []
    for s in first_seen:
        files = sorted((RAW / "1h" / s).glob("*.csv"))
        if not files:
            print("no 1h data:", s, flush=True)
            continue
        df = pd.concat([read_csv(f) for f in files]).drop_duplicates("date").sort_values("date").set_index("date")
        df = df[df.index <= END]
        opens[s], closes[s], qvs[s] = df["open"], df["close"], df["quote_volume"]
        ffiles = sorted((RAW / "funding" / s).glob("*.csv"))
        if ffiles:
            f = pd.concat([pd.read_csv(x) for x in ffiles])
            f = f[pd.to_numeric(f.iloc[:, 0], errors="coerce").notna()]
            t = pd.to_numeric(f.iloc[:, 0]).astype("int64")
            unit = "us" if t.iloc[0] > 10**14 else "ms"
            fund.append(pd.DataFrame({"symbol": s, "time": pd.to_datetime(t, unit=unit, utc=True),
                                      "rate": f.iloc[:, 2].astype(float).to_numpy()}))
    try:                                       # September 2026: no monthly funding archive yet
        sept = funding_via_server(list(opens), pd.Timestamp("2026-09-01", tz="UTC"), END)
        for s, df in sept.items():
            df["symbol"] = s
            fund.append(df)
        print(f"september funding via server: {len(sept)} of {len(opens)} symbols", flush=True)
    except Exception as e:  # noqa: BLE001
        print("september funding failed:", e, flush=True)
    pd.DataFrame(opens).sort_index().to_parquet(ROOT / "hourly_open.parquet")
    pd.DataFrame(closes).sort_index().to_parquet(ROOT / "hourly_close.parquet")
    pd.DataFrame(qvs).sort_index().to_parquet(ROOT / "hourly_qv.parquet")
    funding = pd.concat(fund).drop_duplicates(["symbol", "time"]).sort_values(["symbol", "time"])
    funding.to_parquet(ROOT / "funding.parquet")
    print(f"hourly: {len(closes)} symbols, {len(pd.DataFrame(closes))} hours; funding rows {len(funding)}", flush=True)


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else "all"
    if step in ("daily", "all"):
        step_daily()
    if step in ("universe", "all"):
        step_universe()
    if step in ("hourly", "all"):
        step_hourly()
