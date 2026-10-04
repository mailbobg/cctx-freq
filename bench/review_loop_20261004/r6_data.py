"""Validated, replayable two-instrument data for the preregistered R6 trial."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
from threading import Lock
import time
import zipfile

import numpy as np
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DATA = HERE / "r6_data"
START = pd.Timestamp("2020-12-01", tz="UTC")
TRADE_START = pd.Timestamp("2020-01-01", tz="UTC")
END = pd.Timestamp("2026-09-01", tz="UTC")
ASSETS = ("BTCUSDT", "ETHUSDT")


def parse_klines(body, timeframe, start, end, allow_gaps=False):
    frame = pd.read_csv(io.BytesIO(body), header=None)
    if len(frame) and str(frame.iloc[0, 0]) in ("open_time", "openTime"):
        frame = frame.iloc[1:]
    if frame.shape[1] != 12:
        raise ValueError("Unexpected futures candle schema")
    selected = frame[[0, 1, 2, 3, 4, 6]].apply(pd.to_numeric, errors="raise")
    if not np.isfinite(selected.to_numpy()).all():
        raise ValueError("Nonfinite candle")
    step = pd.Timedelta(timeframe.replace("d", "D"))
    dates = pd.to_datetime(selected[0], unit="ms", utc=True)
    index = pd.DatetimeIndex(dates).as_unit("ns")
    expected = pd.date_range(start, end, freq=step, inclusive="left").as_unit("ns")
    if not index.is_unique or not index.is_monotonic_increasing or len(index.difference(expected)) or (not allow_gaps and not index.equals(expected)):
        raise ValueError("Missing, repeated, reordered or unexpected candle dates")
    if not selected[6].sub(selected[0]).eq(step.total_seconds() * 1000 - 1).all():
        raise ValueError("Wrong candle close time")
    values = selected[[1, 2, 3, 4]].set_axis(["open", "high", "low", "close"], axis=1)
    if not values.gt(0).all().all() or not values.high.ge(values[["open", "close", "low"]].max(axis=1)).all() or not values.low.le(values[["open", "close", "high"]].min(axis=1)).all():
        raise ValueError("Invalid OHLC")
    values.index = index
    values.index.name = "date"
    return values


def complete_candles(frames, timeframe, start, end):
    combined = pd.concat(frames).sort_index()
    combined.index = combined.index.as_unit("ns")
    repeated = combined[combined.index.duplicated(keep=False)]
    for _, group in repeated.groupby(level=0):
        if not np.allclose(group.to_numpy(), group.iloc[0].to_numpy(), atol=1e-8, rtol=0):
            raise ValueError("Conflicting archived candle sources")
    combined = combined[~combined.index.duplicated()]
    expected = pd.date_range(start, end, freq=pd.Timedelta(timeframe.replace("d", "D")), inclusive="left").as_unit("ns")
    if not combined.index.equals(expected):
        raise ValueError("Candle gap remains after archived supplements")
    return combined


def parse_funding(rows, symbol, start, end):
    if not rows or any(r.get("symbol") != symbol for r in rows):
        raise ValueError("Empty or wrong funding instrument")
    times = pd.to_datetime([int(r["fundingTime"]) for r in rows], unit="ms", utc=True)
    rates = np.asarray([float(r["fundingRate"]) for r in rows])
    marks = np.asarray([float(r["markPrice"]) if r.get("markPrice") not in (None, "") else np.nan for r in rows])
    slots = times.floor("8h")
    expected = pd.date_range(start, end, freq="8h", inclusive="left")
    if not slots.equals(expected) or np.any((times - slots).total_seconds() > 1):
        raise ValueError("Missing, repeated, reordered or unexpected funding event")
    if not np.isfinite(rates).all() or np.any(np.isinf(marks)) or np.any(marks[np.isfinite(marks)] <= 0):
        raise ValueError("Invalid funding rate/associated mark")
    return pd.DataFrame({"slot": slots, "rate": rates, "mark": marks}, index=times).rename_axis("time")


def main():
    DATA.mkdir(exist_ok=True)
    raw = DATA / "raw"
    raw.mkdir(exist_ok=True)
    manifest_path = DATA / "sources.json"
    records = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    lock = Lock()

    def download(url, session):
        target = raw / hashlib.sha256(url.encode()).hexdigest()
        if url in records:
            rec = records[url]
            body = (DATA / rec["path"]).read_bytes()
            if hashlib.sha256(body).hexdigest() != rec["sha256"]:
                raise ValueError("Archived source hash mismatch")
            return body
        if target.exists():
            raise ValueError("Raw source without provenance record")
        response = session.get(url, timeout=(5, 15))
        response.raise_for_status()
        body = response.content
        target.write_bytes(body)
        rec = {"url": url, "path": str(target.relative_to(DATA)),
               "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
               "retrieved_utc": datetime.now(timezone.utc).isoformat()}
        with lock:
            records[url] = rec
            temp = manifest_path.with_suffix(".tmp")
            temp.write_text(json.dumps(records, indent=2) + "\n")
            temp.replace(manifest_path)
        time.sleep(.08)
        return body

    def asset_data(symbol):
        trades, marks, local_records, repairs = [], [], [], []
        for month in pd.date_range(TRADE_START, START, freq="MS", inclusive="left"):
            following = month + pd.offsets.MonthBegin(1)
            tag = str(month.date())[:7]
            local = ROOT / f"user_data/data_f1/raw/1d_all/{symbol}/{symbol}-1d-{tag}.csv"
            raw_trade = local.read_bytes()
            trades.append(parse_klines(raw_trade, "1d", month, following))
            local_records.append({"path": str(local.relative_to(ROOT)), "sha256": hashlib.sha256(raw_trade).hexdigest(),
                                  "origin": "Existing archived CSV; standard Binance monthly futures klines URL is inferred, not fetched now",
                                  "inferred_url": f"https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/1d/{symbol}-1d-{tag}.zip"})
        with requests.Session() as session:
            for i, month in enumerate(pd.date_range(START, END, freq="MS", inclusive="left")):
                following = month + pd.offsets.MonthBegin(1)
                tag = str(month.date())[:7]
                file_name = f"{symbol}-1h-{tag}.zip"
                url = f"https://data.binance.vision/data/futures/um/monthly/markPriceKlines/{symbol}/1h/{file_name}"
                body = download(url, session)
                checksum = download(url + ".CHECKSUM", session).decode().split()
                if len(checksum) != 2 or checksum[1] != file_name or checksum[0] != hashlib.sha256(body).hexdigest():
                    raise ValueError("Official archive checksum mismatch")
                with zipfile.ZipFile(io.BytesIO(body)) as zipped:
                    expected_csv = file_name.replace(".zip", ".csv")
                    if zipped.namelist() != [expected_csv]:
                        raise ValueError("Unexpected archive contents")
                    partial = parse_klines(zipped.read(expected_csv), "1h", month, following, allow_gaps=True)
                expected_dates = pd.date_range(month, following, freq="1h", inclusive="left")
                missing = expected_dates.difference(partial.index)
                supplements = [partial]
                if len(missing):
                    days = missing.floor("D").unique()
                    print(symbol, tag, "missing_monthly_mark_hours", len(missing), "supplement_days", len(days), flush=True)
                    for day in days:
                        daily_name = f"{symbol}-1h-{day.date()}.zip"
                        daily_url = f"https://data.binance.vision/data/futures/um/daily/markPriceKlines/{symbol}/1h/{daily_name}"
                        daily_body = download(daily_url, session)
                        daily_checksum = download(daily_url + ".CHECKSUM", session).decode().split()
                        if len(daily_checksum) != 2 or daily_checksum[1] != daily_name or daily_checksum[0] != hashlib.sha256(daily_body).hexdigest():
                            raise ValueError("Official daily checksum mismatch")
                        with zipfile.ZipFile(io.BytesIO(daily_body)) as zipped:
                            csv_name = daily_name.replace(".zip", ".csv")
                            if zipped.namelist() != [csv_name]:
                                raise ValueError("Unexpected daily archive contents")
                            supplements.append(parse_klines(zipped.read(csv_name), "1h", day, day + pd.Timedelta(days=1)))
                    repairs.append({"month": tag, "missing_monthly_hours": len(missing), "daily_supplement_dates": [str(d.date()) for d in days]})
                marks.append(complete_candles(supplements, "1h", month, following))
                local = ROOT / f"user_data/data_f1/raw/1d_all/{symbol}/{symbol}-1d-{tag}.csv"
                raw_trade = local.read_bytes()
                trades.append(parse_klines(raw_trade, "1d", month, following))
                local_records.append({"path": str(local.relative_to(ROOT)), "sha256": hashlib.sha256(raw_trade).hexdigest(),
                                      "origin": "Existing archived CSV; standard Binance monthly futures klines URL is inferred, not fetched now",
                                      "inferred_url": f"https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/1d/{symbol}-1d-{tag}.zip"})
                if (i + 1) % 10 == 0:
                    print(symbol, "validated_mark_months", i + 1, flush=True)
            rows, cursor = [], int(START.timestamp() * 1000)
            end_ms = int(END.timestamp() * 1000) - 1
            while cursor <= end_ms:
                url = requests.Request("GET", "https://fapi.binance.com/fapi/v1/fundingRate", params={"symbol": symbol, "startTime": cursor, "endTime": end_ms, "limit": 1000}).prepare().url
                page = json.loads(download(url, session))
                if not isinstance(page, list):
                    raise ValueError("Funding API did not return events")
                if not page:
                    break
                last = int(page[-1]["fundingTime"])
                if last < cursor:
                    raise ValueError("Repeated funding page")
                rows.extend(page)
                cursor = last + 1
                if len(page) < 1000:
                    break
        trade = complete_candles(trades, "1d", TRADE_START, END)
        mark = pd.concat(marks)
        funding = parse_funding(rows, symbol, START, END)
        old = pd.read_parquet(ROOT / "user_data/data_f1/funding.parquet")
        old = old[old.symbol.eq(symbol)].set_index("time").rate.reindex(funding.slot)
        if not np.allclose(old.to_numpy(), funding.rate.to_numpy(), atol=1e-12, rtol=0):
            raise ValueError("API funding disagrees with the frozen earlier input")
        trade.to_parquet(DATA / f"{symbol}_trade.parquet")
        mark.to_parquet(DATA / f"{symbol}_mark.parquet")
        funding.to_parquet(DATA / f"{symbol}_funding.parquet")
        (DATA / f"{symbol}_local_trade_sources.json").write_text(json.dumps(local_records, indent=2) + "\n")
        (DATA / f"{symbol}_mark_supplements.json").write_text(json.dumps(repairs, indent=2) + "\n")
        print(symbol, "days", len(trade), "mark_hours", len(mark), "funding", len(funding),
              "missing_associated_mark", int(funding.mark.isna().sum()), flush=True)
        return {"daily_rows": len(trade), "mark_rows": len(mark), "funding_rows": len(funding),
                "missing_associated_mark": int(funding.mark.isna().sum())}

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {s: pool.submit(asset_data, s) for s in ASSETS}
        coverage = {s: f.result() for s, f in futures.items()}
    outputs = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in DATA.glob("*.parquet")}
    (DATA / "manifest.json").write_text(json.dumps({"captured_utc": datetime.now(timezone.utc).isoformat(),
        "coverage": coverage, "output_sha256": outputs,
        "limitation": "Missing API-associated marks remain missing. Hourly mark opens can be a documented settlement-price approximation; hourly extremes provide per-payment sensitivity inputs. Neither is labeled an exact associated mark."}, indent=2) + "\n")


if __name__ == "__main__":
    main()
