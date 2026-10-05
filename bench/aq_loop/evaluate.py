"""Evaluate one loop item: read its backtest JSONs, apply gates, verdict.

Gates (vs the committed baseline in queue.json):
  1. every window total profit > 0
  2. worst drawdown across windows <= 25%
  3. beats baseline: total strictly greater in >= 3 of 4 windows,
     and no window worse than half the baseline total.
Verdict PASS/FAIL printed as JSON to stdout.
"""

import json
import sys
import zipfile
from glob import glob
from pathlib import Path


def load_total_file(zip_path: str, strategy: str) -> dict:
    z = zipfile.ZipFile(zip_path)
    js = [n for n in z.namelist()
          if n.endswith(".json") and "config" not in n and "market" not in n][0]
    data = json.loads(z.read(js))
    res = data["strategy"][strategy]
    for e in res.get("results_per_pair", []):
        if e.get("key") == "TOTAL":
            return {
                "total": float(e.get("profit_total_pct", 0)),
                "dd": abs(float(e.get("max_drawdown_account", 0))) * 100,
                "sharpe": float(e.get("sharpe", 0)),
                "trades": int(e.get("trades", 0)),
            }
    raise ValueError(f"no TOTAL in {path}")


def main() -> None:
    resdir, strategy, queue_path, item_id = sys.argv[1:5]
    q = json.loads(Path(queue_path).read_text())
    baseline = q["baseline"]
    windows = q["windows"]
    out, ok = {}, True
    reasons = []
    wins = 0
    zips = sorted(glob(f"{resdir}/*.zip"), key=lambda p: Path(p).stat().st_mtime)
    if len(zips) < len(windows):
        raise FileNotFoundError(f"only {len(zips)} zips in {resdir}, need {len(windows)}")
    for w, zp in zip(windows, zips[-len(windows):]):
        m = load_total_file(zp, strategy)
        out[w] = m
        if m["total"] <= 0:
            ok = False
            reasons.append(f"{w}: total {m['total']:.1f} <= 0")
        if m["dd"] > 25:
            ok = False
            reasons.append(f"{w}: dd {m['dd']:.1f} > 25")
        base = baseline[w]["total"]
        if m["total"] > base:
            wins += 1
        if m["total"] < 0.5 * base:
            ok = False
            reasons.append(f"{w}: total {m['total']:.1f} < half baseline {base:.1f}")
    if wins < 3:
        ok = False
        reasons.append(f"beats baseline in {wins}/4 windows (< 3)")
    print(json.dumps({"item": item_id, "verdict": "PASS" if ok else "FAIL",
                      "wins": wins, "reasons": reasons, "metrics": out}))
    if not ok:
        sys.exit(3)


if __name__ == "__main__":
    main()
