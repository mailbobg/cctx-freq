"""Fetch public daily candles into an isolated snapshot, never shared bot data."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
END = pd.Timestamp("2026-09-22", tz="UTC")
BASE = "https://data-api.binance.vision/api/v3/klines"


def main():
    directory = HERE / "r1_data"
    directory.mkdir(exist_ok=True)
    cached = pd.read_parquet(HERE.parents[0] / "events/c_daily.parquet").loc[:"2026-09-21"]
    manifest = {"captured_utc": datetime.now(timezone.utc).isoformat(), "endpoint": BASE, "files": {}}
    for asset in ("BTC", "ETH"):
        start = int(pd.Timestamp("2017-08-17", tz="UTC").timestamp() * 1000)
        end = int(END.timestamp() * 1000) - 1
        raw = directory / f"{asset}_USDT-1d.raw.json"
        if raw.exists():
            rows = json.loads(raw.read_text())
        else:
            rows = []
            while start <= end:
                response = requests.get(BASE, params={"symbol": f"{asset}USDT", "interval": "1d",
                                                     "startTime": start, "endTime": end, "limit": 1000}, timeout=15)
                response.raise_for_status()
                part = response.json()
                if not isinstance(part, list):
                    raise ValueError(f"Unexpected public data response for {asset}")
                if not part:
                    break
                rows.extend(part)
                start = part[-1][0] + 86_400_000
            raw.write_text(json.dumps(rows, separators=(",", ":")) + "\n")
        frame = pd.DataFrame(rows).iloc[:, :6]
        frame.columns = ["date", "open", "high", "low", "close", "volume"]
        frame["date"] = pd.to_datetime(frame.date, unit="ms", utc=True)
        for column in frame.columns[1:]:
            frame[column] = frame[column].astype(float)
        for column in ("open", "close"):
            expected = cached[f"{asset}_{column}"]
            actual = frame.set_index("date")[column].reindex(expected.index).astype(float)
            pd.testing.assert_series_equal(actual, expected, check_names=False, rtol=0., atol=1e-8)
        assert frame.date.is_unique and (frame.date.diff().dropna() == pd.Timedelta(days=1)).all()
        path = directory / f"{asset}_USDT-1d.feather"
        frame.to_feather(path)
        manifest["files"][asset] = {"raw_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
                                    "feather_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                    "rows": len(frame), "start": str(frame.date.min()), "end": str(frame.date.max()),
                                    "cached_open_close_match": True}
        print(asset, manifest["files"][asset], flush=True)
    (HERE / "r1_data_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    config = json.loads((HERE.parents[0] / "review_20260927/x8.json").read_text())
    config.pop("grinding_enable", None)
    config.update(timeframe="1d", max_open_trades=2, bot_name="research-r1-validation")
    config["exchange"]["pair_whitelist"] = ["BTC/USDT", "ETH/USDT"]
    (HERE / "r1_freqtrade.json").write_text(json.dumps(config, indent=2) + "\n")


if __name__ == "__main__":
    main()
