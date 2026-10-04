"""Replay held-position causality and executable-day activity audits."""
import hashlib
import json
from pathlib import Path

import pandas as pd

from r7_dated import simulate, ASSETS


def main():
    here = Path(__file__).resolve().parent
    spot = {a: pd.read_parquet(here / "r7_data" / f"{a}_spot.parquet") for a in ASSETS}
    dated = {f.stem: pd.read_parquet(f) for f in (here / "r7_data").glob("*USDT_*.parquet")}
    full, _ = simulate(spot, dated, "2024-01-01", "2024-07-05", .0012, terminal=False)
    part, _ = simulate(spot, dated, "2024-01-01", "2024-06-01", .0012, terminal=False)
    pd.testing.assert_frame_equal(part, full.loc[part.index])
    assert part.gross.gt(0).any()
    results = json.loads((here / "r7_results.json").read_text())
    violations = []
    for key, events in results["decisions"].items():
        window_end = pd.Timestamp("2023-12-31" if key.startswith("early") else "2026-08-31", tz="UTC")
        for event in events:
            entry = pd.Timestamp(event["date"])
            close = min(pd.Timestamp(event["expiry"]).floor("D") - pd.Timedelta(days=1), window_end)
            suffix = pd.Timestamp(event["expiry"]).strftime("_%y%m%d")
            for k, decision in enumerate(event["entered"]):
                if decision != "enter":
                    continue
                future, cash = dated[ASSETS[k] + suffix], spot[ASSETS[k]]
                if min(future.loc[entry, "trades"], future.loc[close, "trades"],
                       cash.loc[entry, "trades"], cash.loc[close, "trades"]) <= 0:
                    violations.append({"run": key, "asset": ASSETS[k], "entry": str(entry), "close": str(close)})
    assert not violations
    fields = ["CAGR_pct", "daily_trade_price_DD_pct", "calendar_year_return_pct", "entered_asset_cycles",
              "ledger_reconciliation_USDT", "unresolved_margin_days"]
    proof = {"real_input_held_prefix_equal": True, "cutoff": "2024-06-01",
             "entry_close_nonzero_trade_activity_violations": violations,
             "candidate_results": {key: {field: result[field] for field in fields}
                                   for key, result in results["results"].items() if "equal_pair" in key},
             "source_sha256": {name: hashlib.sha256((here / name).read_bytes()).hexdigest()
                               for name in ("r7_dated.py", "r7_validate.py")},
             "limitations": "Nonzero daily OPEN/CLOSE activity is not proof of atomic fills or full mark-based margin solvency."}
    (here / "r7_validation.json").write_text(json.dumps(proof, indent=2) + "\n")
    print("Held-position prefix and all actual entry/closing-day activity checks PASS")


if __name__ == "__main__":
    main()
