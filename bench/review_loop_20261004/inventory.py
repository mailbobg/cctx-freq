"""Freeze safe effective configuration and runtime evidence for the review loop."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
FIELDS = ("strategy", "dry_run", "bot_name", "db_url", "timeframe", "trading_mode",
          "margin_mode", "max_open_trades", "stake_amount", "dry_run_wallet",
          "tradable_balance_ratio", "use_exit_signal", "ignore_roi_if_entry_signal",
          "process_only_new_candles", "stoploss", "trailing_stop")


def effective(path):
    raw = json.loads(path.read_text())
    row = {"bot": path.stem, "config_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    if raw.get("runtime") == "cross_venue_paper":
        row.update({key: raw.get(key) for key in FIELDS})
        row["runtime"] = raw["runtime"]
        return row
    proc = subprocess.run(
        [str(Path(sys.executable).parent / "freqtrade"), "show-config", "-c", str(path)],
        cwd=ROOT, text=True, capture_output=True, timeout=30,
    )
    if proc.returncode:
        row["effective_config_error"] = proc.returncode
        return row
    cfg = json.loads(proc.stdout[proc.stdout.find("{"):])
    row.update({key: cfg.get(key) for key in FIELDS})
    row["pair_count"] = len(cfg.get("exchange", {}).get("pair_whitelist", []))
    row["exchange"] = cfg.get("exchange", {}).get("name")
    row["pairlists"] = cfg.get("pairlists")
    row["order_types"] = cfg.get("order_types")
    row["runtime"] = "freqtrade"
    return row


def main():
    paths = [p for p in sorted((ROOT / "user_data/bots").glob("*.json"))
             if "strategy" in json.loads(p.read_text())]
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(effective, paths))
    response = requests.get("http://127.0.0.1:8090/api/bots", timeout=15)
    response.raise_for_status()
    runtime = response.json()
    (HERE / "runtime_snapshot.json").write_text(json.dumps(runtime, indent=2, ensure_ascii=False) + "\n")
    result = {"captured_utc": datetime.now(timezone.utc).isoformat(), "configurations": rows,
              "review_scope": [r["bot"] for r in rows if r["bot"] not in ("e0v1en", "e0v1en-live")],
              "exclusions": {"e0v1en": "fresh E0V1ENWait3 cohort already started",
                             "e0v1en-live": "user-stopped live account; no activation"}}
    (HERE / "inventory.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print("other_bot_configs", len(result["review_scope"]), result["review_scope"])
    for row in rows:
        print({key: row.get(key) for key in ("bot", "strategy", "dry_run", "use_exit_signal",
                                            "trading_mode", "margin_mode", "pair_count",
                                            "effective_config_error")})


if __name__ == "__main__":
    main()
