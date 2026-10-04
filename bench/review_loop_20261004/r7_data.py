"""Checksummed, individual dated contracts plus independently archived spot prices."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
from threading import Lock
import time
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
import pandas as pd
import requests

from r6_data import parse_klines

HERE = Path(__file__).resolve().parent
DATA = HERE / "r7_data"
ROOT = HERE.parents[2]
START = pd.Timestamp("2021-01-01", tz="UTC")
END = pd.Timestamp("2026-09-01", tz="UTC")
ASSETS = ("BTCUSDT", "ETHUSDT")
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision/"


def expiry(symbol):
    match = re.fullmatch(r"(?:BTC|ETH)USDT_(\d{6})", symbol)
    if not match:
        raise ValueError("Not a registered dated asset symbol")
    date = pd.to_datetime(match[1], format="%y%m%d", utc=True)
    last = date + pd.offsets.MonthEnd(0)
    friday = last - pd.Timedelta(days=(last.weekday() - 4) % 7)
    if date.month not in (3, 6, 9, 12) or date != friday:
        raise ValueError("Not a quarterly last-Friday expiry")
    return date + pd.Timedelta(hours=8)


def parse_archive(body, start, end, include_activity=False):
    frame = pd.read_csv(io.BytesIO(body), header=None)
    if str(frame.iloc[0, 0]) in ("open_time", "openTime"):
        frame = frame.iloc[1:]
    if frame.shape[1] != 12:
        raise ValueError("Wrong archive schema")
    opened = pd.to_numeric(frame[0], errors="raise")
    closed = pd.to_numeric(frame[6], errors="raise")
    micro = opened.ge(10**15)
    if micro.any():
        if not micro.all() or not opened.mod(1000).eq(0).all() or not closed.sub(opened).eq(86400000000 - 1).all():
            raise ValueError("Mixed/invalid microsecond candle dates")
        frame[0], frame[6] = opened // 1000, closed // 1000
        body = frame.to_csv(index=False, header=False).encode()
    begin, finish = pd.Timestamp(start), pd.Timestamp(end)
    begin = begin.tz_localize("UTC") if begin.tz is None else begin
    finish = finish.tz_localize("UTC") if finish.tz is None else finish
    result = parse_klines(body, "1d", begin, finish, allow_gaps=True)
    if include_activity:
        activity = frame[[5, 7, 8]].apply(pd.to_numeric, errors="raise")
        if not np.isfinite(activity.to_numpy()).all() or not activity.ge(0).all().all():
            raise ValueError("Invalid candle trading activity")
        for column, name in ((5, "volume"), (7, "quote_volume"), (8, "trades")):
            result[name] = activity[column].to_numpy()
    return result


def contract_calendar(frames, symbol, end):
    combined = pd.concat(frames).sort_index()
    if not combined.index.is_unique:
        raise ValueError("Repeated dated-contract row")
    expiration = expiry(symbol)
    after = combined.loc[combined.index > expiration.floor("D")]
    removed = 0
    if len(after):
        # Some official archives contain zero-trade, flat post-expiry padding.
        # Keep raw evidence; never allow those rows to become executable prices.
        activity = ["volume", "quote_volume", "trades"]
        before = combined.loc[combined.index <= expiration.floor("D")]
        if (not set(activity).issubset(combined.columns) or before.empty
            or not after[activity].eq(0).all().all()
            or not after[["open", "high", "low", "close"]].eq(before.close.iloc[-1]).all().all()):
            raise ValueError("Trade archive row after expiry")
        removed = len(after)
        combined = before
    final = min(expiration.floor("D"), end - pd.Timedelta(days=1))
    expected = pd.date_range(combined.index.min(), final, freq="D").as_unit("ns")
    if not combined.index.equals(expected):
        raise ValueError("Interior/active-tail dated-contract gap")
    combined.attrs["zero_trade_expired_padding_removed"] = removed
    return combined


def main():
    DATA.mkdir(exist_ok=True)
    raw = DATA / "raw"
    raw.mkdir(exist_ok=True)
    source_file = DATA / "sources.json"
    records = json.loads(source_file.read_text()) if source_file.exists() else {}
    lock = Lock()

    def get(url, session):
        if url in records:
            rec = records[url]
            body = (DATA / rec["path"]).read_bytes()
            if hashlib.sha256(body).hexdigest() != rec["sha256"]:
                raise ValueError("Raw archive hash changed")
            return body
        path = raw / hashlib.sha256(url.encode()).hexdigest()
        if path.exists():
            raise ValueError("Raw archive without provenance")
        response = session.get(url, timeout=(5, 15))
        response.raise_for_status()
        body = response.content
        path.write_bytes(body)
        with lock:
            records[url] = {"url": url, "path": str(path.relative_to(DATA)),
                            "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
                            "retrieved_utc": datetime.now(timezone.utc).isoformat()}
            temp = source_file.with_suffix(".tmp")
            temp.write_text(json.dumps(records, indent=2) + "\n")
            temp.replace(source_file)
        time.sleep(.06)
        return body

    def listing(prefix, session, delimiter=None):
        params = {"prefix": prefix}
        if delimiter:
            params["delimiter"] = delimiter
        url = requests.Request("GET", S3, params=params).prepare().url
        root = ET.fromstring(get(url, session))
        ns = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
        if root.findtext("s:IsTruncated", namespaces=ns) != "false":
            raise ValueError("Incomplete S3 archive listing")
        return [node.text for node in root.findall("s:CommonPrefixes/s:Prefix" if delimiter else "s:Contents/s:Key", ns)]

    def archive(key, session):
        url = "https://data.binance.vision/" + key
        body = get(url, session)
        check = get(url + ".CHECKSUM", session).decode().split()
        filename = key.rsplit("/", 1)[-1]
        if len(check) != 2 or check[1] != filename or check[0] != hashlib.sha256(body).hexdigest():
            raise ValueError("Official price checksum mismatch")
        with zipfile.ZipFile(io.BytesIO(body)) as zipped:
            expected = filename.replace(".zip", ".csv")
            if zipped.namelist() != [expected]:
                raise ValueError("Unexpected zipped price files")
            tag = filename[-11:-4]
            month = pd.Timestamp(tag + "-01", tz="UTC")
            return parse_archive(zipped.read(expected), month, month + pd.offsets.MonthBegin(1), include_activity=True)

    def collect(asset):
        with requests.Session() as session:
            spot_frames = []
            for month in pd.date_range(START, END, freq="MS", inclusive="left"):
                tag = str(month.date())[:7]
                spot_frames.append(archive(f"data/spot/monthly/klines/{asset}/1d/{asset}-1d-{tag}.zip", session))
            spot = pd.concat(spot_frames)
            expected = pd.date_range(START, END, freq="D", inclusive="left").as_unit("ns")
            if not spot.index.equals(expected):
                raise ValueError("Incomplete active spot calendar")
            # A separate official source must reproduce the frozen earlier spot execution inputs.
            frozen = pd.read_parquet(ROOT / "user_data/bench/events/c_daily.parquet").reindex(expected)
            short = asset.replace("USDT", "")
            if not np.allclose(spot[["open", "close"]].to_numpy(), frozen[[f"{short}_open", f"{short}_close"]].to_numpy(), atol=1e-8, rtol=0):
                raise ValueError("Official spot archives disagree with frozen spot inputs")
            spot.to_parquet(DATA / f"{asset}_spot.parquet")
            folders = listing(f"data/futures/um/monthly/klines/{asset}_", session, "/")
            contracts = {}
            for folder in folders:
                symbol = folder.rstrip("/").rsplit("/", 1)[-1]
                if not (pd.Timestamp("2021-03-26", tz="UTC") <= expiry(symbol) <= pd.Timestamp("2026-09-25 08:00", tz="UTC")):
                    continue
                keys = listing(folder + "1d/", session)
                selected = [key for key in keys if key.endswith(".zip") and key[-11:-4] < "2026-09"]
                if not selected:
                    raise ValueError("Registered contract has no price archive")
                frames = [archive(key, session) for key in selected]
                joined = contract_calendar(frames, symbol, END)
                joined.to_parquet(DATA / f"{symbol}.parquet")
                contracts[symbol] = {"expiry": expiry(symbol).isoformat(), "rows": len(joined),
                                     "first_archived_day": joined.index.min().isoformat(),
                                     "last_archived_day": joined.index.max().isoformat(),
                                     "zero_trade_expired_padding_removed": joined.attrs["zero_trade_expired_padding_removed"],
                                     "first_day_is_not_listing_metadata": True}
                print(symbol, "daily_rows", len(joined), flush=True)
            print(asset, "spot_days", len(spot), "quarterly_contracts", len(contracts), flush=True)
            return {"spot_rows": len(spot), "contracts": contracts}

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {asset: pool.submit(collect, asset) for asset in ASSETS}
        coverage = {asset: f.result() for asset, f in futures.items()}
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in DATA.glob("*.parquet")}
    (DATA / "manifest.json").write_text(json.dumps({"captured_utc": datetime.now(timezone.utc).isoformat(),
        "coverage": coverage, "output_sha256": hashes,
        "first_contract_listing": {"source": "https://www.binance.com/en/support/announcement/detail/ee59b5c8ddf84d6bbbde4ebff7bdfaa0", "BTCUSDT_210326": "2021-02-03T07:00:00Z", "ETHUSDT_210326": "2021-02-04T07:00:00Z"},
        "limitations": "Spot2025+ microsecond times normalized with strict precision/calendar checks; futures preserve individual expiry symbols. Trade prices are not mark prices. First archived date alone is not listing proof; missing decision data after initial confirmed listing requires repair/evidence."}, indent=2) + "\n")


if __name__ == "__main__":
    main()
