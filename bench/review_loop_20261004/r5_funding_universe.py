"""Frozen broader-universe funding-only gate, with replayable public API pages."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from threading import Lock
import time

import requests

import funding_probe as funding

HERE = Path(__file__).resolve().parent
ASSETS = ("BTC", "ETH", "SOL", "XRP", "BNB", "ADA", "DOGE", "AVAX")
MANIFEST = HERE / "r5_sources.json"
BASES = {"bybit": "https://api.bybit.com", "okx": "https://www.okx.com"}


def main():
    records = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    lock = Lock()
    directory = HERE / "r5_raw"
    directory.mkdir(exist_ok=True)

    def fetch(venue, path, params):
        url = requests.Request("GET", BASES[venue] + path, params=params).prepare().url
        target = directory / (venue + "_" + hashlib.sha256(url.encode()).hexdigest()[:20] + ".json")
        if url in records:
            record = records[url]
            body = (HERE / record["path"]).read_bytes()
            if hashlib.sha256(body).hexdigest() != record["sha256"]:
                raise ValueError("Archived public page hash mismatch")
            return json.loads(body)
        if target.exists():
            raise ValueError("Raw page has no provenance record")
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        body = response.content
        parsed = response.json()
        target.write_bytes(body)
        record = {"retrieved_utc": datetime.now(timezone.utc).isoformat(), "url": url,
                  "path": str(target.relative_to(HERE)), "sha256": hashlib.sha256(body).hexdigest(),
                  "status": response.status_code}
        with lock:
            records[url] = record
            temporary = MANIFEST.with_suffix(".tmp")
            temporary.write_text(json.dumps(records, indent=2) + "\n")
            temporary.replace(MANIFEST)
        time.sleep(.22)
        return parsed

    def venue_data(venue):
        rates, errors = {}, {}
        for asset in ASSETS:
            try:
                series = funding.collect_history(venue, asset, int(funding.START.timestamp() * 1000),
                                                  int(funding.END.timestamp() * 1000), fetch)
                # An interval change is an input gap, not a zero-funding trade.
                series = funding.validate_history(series)
                series.to_csv(HERE / f"r5_{venue}_{asset}_rates.csv", header=["realized_rate"])
                rates[asset] = series
                print(venue, asset, "complete_events", len(series), flush=True)
            except (ValueError, requests.RequestException) as exc:
                errors[asset] = f"{type(exc).__name__}: {exc}"
                print(venue, asset, "unreviewed_input", type(exc).__name__, str(exc), flush=True)
        return rates, errors

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {venue: pool.submit(venue_data, venue) for venue in BASES}
        fetched = {venue: future.result() for venue, future in futures.items()}
    output = {"analyzed_utc": datetime.now(timezone.utc).isoformat(), "assets_fixed_before_fetch": list(ASSETS),
              "window": [str(funding.START), str(funding.END)], "cost_hurdle_bps": funding.COST * 10_000,
              "data_errors": {venue: result[1] for venue, result in fetched.items()}, "summary": {}, "weekly_rows": {},
              "limitation": "Necessary funding-only screen. No mark-weighted funding, basis PnL, executable fills or capital return. Not an OOS profit claim."}
    for asset in ASSETS:
        if asset not in fetched["bybit"][0] or asset not in fetched["okx"][0]:
            continue
        rows = funding.weekly_probe(fetched["bybit"][0][asset], fetched["okx"][0][asset])
        qualified = [row for row in rows if row["eligible"]]
        output["weekly_rows"][asset] = rows
        output["summary"][asset] = {"complete_weekly_windows": len(rows), "qualifying_entries": len(qualified),
                                    "max_forecast_bps": max((row["forecast_spread"] * 10_000 for row in rows), default=0.),
                                    "qualified_carry_less_cost_bps": [row["carry_less_cost"] * 10_000 for row in qualified],
                                    "enough_cycles_for_further_profit_test": len(qualified) >= 20}
        print(asset, output["summary"][asset], flush=True)
    output["qualifying_entries_total"] = sum(s["qualifying_entries"] for s in output["summary"].values())
    (HERE / "r5_results.json").write_text(json.dumps(output, indent=2) + "\n")


if __name__ == "__main__":
    main()
