"""Freeze cache identity/coverage for the long1m X8 comparison."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DATA = ROOT / "user_data/data/binance"


def inspect(path):
    if not path.exists():
        return {"path": str(path.relative_to(ROOT)), "missing": True}
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(4 * 1024 * 1024):
            digest.update(block)
    dates = pd.read_feather(path, columns=["date"])["date"]
    meta = path.stat()
    return {"path": str(path.relative_to(ROOT)), "sha256": digest.hexdigest(), "bytes": meta.st_size,
            "modified_utc": datetime.fromtimestamp(meta.st_mtime, timezone.utc).isoformat(),
            "rows": len(dates), "first": str(dates.min()), "last": str(dates.max()),
            "duplicate_dates": bool(dates.duplicated().any())}


def main():
    config = json.loads((HERE.parent / "review_20260927/x8.json").read_text())
    names = [pair.replace("/", "_") for pair in config["exchange"]["pair_whitelist"]]
    paths = sorted({DATA / f"{name}-{tf}.feather" for name in names for tf in ("1m", "5m", "15m", "1h", "4h", "1d")})
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(inspect, paths))
    output = {"captured_utc": datetime.now(timezone.utc).isoformat(),
              "capture_phase": "during R2 simulation; not a claim of a hash recorded before its start",
              "configuration_pairs": len(names), "files": rows,
              "missing_files": [row["path"] for row in rows if row.get("missing")],
              "limitation": "Identity and endpoints only; no proof of complete candles inside each file or point-in-time universe availability."}
    (HERE / "r2_data_manifest.json").write_text(json.dumps(output, indent=2) + "\n")
    print("pairs", len(names), "files", len(rows), "missing", output["missing_files"], flush=True)


if __name__ == "__main__":
    main()
