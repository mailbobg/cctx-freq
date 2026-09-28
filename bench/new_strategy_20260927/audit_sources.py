"""Reproduce the source-availability probe. This does not backtest a strategy."""

import csv
import hashlib
import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests


ROOT = "https://data.binance.vision/data/"
OUT = Path(__file__).resolve().parent


def probe(item):
    label, path = item
    url = ROOT + path
    result = {"label": label, "url": url}
    try:
        response = requests.get(url, timeout=30)
        result["http_status"] = response.status_code
        if response.status_code != 200:
            return result
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            member = archive.namelist()[0]
            rows = list(csv.reader(io.StringIO(archive.read(member).decode())))
        has_header = not rows[0][0].replace(".", "", 1).isdigit()
        data = rows[1:] if has_header else rows
        result.update(
            archive_sha256=hashlib.sha256(response.content).hexdigest(),
            member=member,
            header=rows[0] if has_header else None,
            observations=len(data),
            first=data[0],
            last=data[-1],
            unique_timestamps=len({row[0] for row in data}),
        )
    except (requests.RequestException, zipfile.BadZipFile, IndexError) as error:
        result["error"] = str(error)
    return result


def main():
    jobs = []
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        for day in ("2022-11-09", "2023-08-17", "2024-08-05", "2025-10-10"):
            jobs.append((f"oi:{symbol}:{day}",
                         f"futures/um/daily/metrics/{symbol}/{symbol}-metrics-{day}.zip"))
    for symbol in ("BTCUSDT_240927", "ETHUSDT_240927"):
        jobs.append((f"dated:{symbol}:2024-08-05",
                     f"futures/um/daily/klines/{symbol}/1h/{symbol}-1h-2024-08-05.zip"))
    for market in ("spot", "futures/um"):
        jobs.append((f"flow:{market}:BTCUSDT:2024-08-05",
                     f"{market}/daily/klines/BTCUSDT/5m/BTCUSDT-5m-2024-08-05.zip"))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(probe, jobs))
    payload = {
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Selected-date availability and schema audit, not continuous coverage or alpha evidence",
        "limitations": [
            "Archive retrieval does not establish original publication/receipt timestamps.",
            "Selected crash dates are illustrative, not a representative event-study sample.",
            "Full historical coverage, revisions and live feature latency remain unverified.",
            "OI measures outstanding contracts, not identified liquidations or the side closing.",
        ],
        "results": results,
    }
    (OUT / "source_audit.json").write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({"output": str(OUT / "source_audit.json"),
                      "successful": sum(r.get("http_status") == 200 and "error" not in r for r in results),
                      "requested": len(results)}, indent=2))


if __name__ == "__main__":
    main()
